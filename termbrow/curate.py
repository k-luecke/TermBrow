"""Content curation: search and a personalized "For You" feed.

Design constraint: every recommended item must resolve to a *real publisher
URL* that the reader can actually open ad-free. That rules out Google News RSS
(its links are opaque JS redirects). We use keyless sources that return direct
article URLs:

  * Hacker News (Algolia API) — full-text keyword search + a front-page feed,
    every hit carries a direct `url`.
  * Wikipedia search API — encyclopedic depth for research topics.
  * A handful of reputable RSS feeds — broad "trending" when history is thin.

Each backend is best-effort: if one is unreachable we simply drop it, so the
feed degrades gracefully rather than erroring.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, unquote, urlparse

import feedparser
import httpx

from . import tor
from .fetch import HEADERS

# GDELT: a keyless global news index that returns DIRECT article URLs from a
# huge diversity of outlets (the antidote to "just a Guardian article"). It
# throttles to one request per ~5s, so we space our calls and fail soft.
_GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_MIN_INTERVAL = 5.0
_gdelt_last_call = -1e9

# Ahmia's onion search service (abuse-filtered). Reached through Tor; the
# clearnet site is JavaScript-gated, so we query the onion when Tor is up.
_AHMIA_ONION = "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"

# Wikimedia's API enforces a stricter User-Agent policy than its page endpoint:
# it wants an app name + contact. Bare "Mozilla" strings get 403'd here.
WIKI_HEADERS = {
    "User-Agent": "TermBrow/1.0 (https://github.com/termbrow; reader@termbrow.local) httpx",
    "Accept": "application/json",
}

# Direct-link RSS feeds for the default/trending feed (no keyword history yet).
TRENDING_FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.theguardian.com/science/rss",
    "https://rss.sciam.com/ScientificAmerican-Global",
]


@dataclass
class Article:
    title: str
    url: str
    source: str
    # "foryou"  = anchored in the reader's known interests (exploit)
    # "discover" = anchored-but-novel, surfaced by explore mode
    kind: str = "foryou"
    onion: bool = False               # a Tor .onion result (needs verification)
    date: str = ""                    # e.g. "posted 2024-03-15" / "edited 2024-…"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").replace("www.", "")


def _dedupe(articles: list[Article], limit: int) -> list[Article]:
    seen: set[str] = set()
    out: list[Article] = []
    for a in articles:
        key = a.url.split("?")[0].rstrip("/")
        if not a.url.startswith("http") or key in seen or not a.title.strip():
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def _gdelt_search(query: str, limit: int, sort: str) -> list[Article]:
    """Diverse, direct-URL world news via GDELT. `sort`: 'newest' | 'relevance'.

    English-filtered; fails soft (returns []) on GDELT's rate-limit or any error
    so a throttled news index never breaks search."""
    global _gdelt_last_call
    now = time.monotonic()
    if now - _gdelt_last_call < _GDELT_MIN_INTERVAL:
        return []  # respect GDELT's 1-per-5s limit rather than get blocked
    _gdelt_last_call = now
    gsort = "datedesc" if sort == "newest" else "hybridrel"
    url = (
        f"{_GDELT_URL}?query={quote(query + ' sourcelang:english')}"
        f"&mode=artlist&maxrecords={limit}&format=json&sort={gsort}"
    )
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20)
        articles = r.json().get("articles", [])  # non-JSON (rate limit) -> raises
    except Exception:
        return []
    out = []
    for a in articles:
        link, title = a.get("url"), a.get("title")
        if not link or not title:
            continue
        sd = a.get("seendate", "")
        date = f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        out.append(Article(
            title=title, url=link, source=a.get("domain") or _host(link),
            date=f"news {date}" if date else "news",
        ))
    return out


def _hn_search(query: str, limit: int, page: int = 0, sort: str = "relevance") -> list[Article]:
    # HN Algolia has two endpoints: relevance-ranked and strictly by date.
    endpoint = "search_by_date" if sort == "newest" else "search"
    url = (
        f"https://hn.algolia.com/api/v1/{endpoint}"
        f"?query={quote_plus(query)}&tags=story&hitsPerPage={limit}&page={page}"
    )
    r = httpx.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    out = []
    for hit in r.json().get("hits", []):
        link = hit.get("url")
        title = hit.get("title")
        if link and title:
            posted = (hit.get("created_at") or "")[:10]
            out.append(Article(
                title=title, url=link, source=_host(link),
                date=f"posted {posted}" if posted else "",
            ))
    return out


def _hn_front(limit: int) -> list[Article]:
    url = f"https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={limit}"
    r = httpx.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    out = []
    for hit in r.json().get("hits", []):
        link = hit.get("url")
        title = hit.get("title")
        if link and title:
            out.append(Article(title=title, url=link, source=_host(link)))
    return out


def _wiki_article(title: str, kind: str = "foryou") -> Article:
    slug = title.replace(" ", "_")
    return Article(
        title=f"{title} — Wikipedia",
        url=f"https://en.wikipedia.org/wiki/{quote_plus(slug)}",
        source="wikipedia.org",
        kind=kind,
    )


def _wiki_search(query: str, limit: int, offset: int = 0) -> list[Article]:
    r = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": limit, "sroffset": offset,
            "srprop": "timestamp",
        },
        headers=WIKI_HEADERS, timeout=15,
    )
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    out = []
    for h in hits:
        if not h.get("title"):
            continue
        art = _wiki_article(h["title"])
        ts = (h.get("timestamp") or "")[:10]  # last revision date
        art.date = f"edited {ts}" if ts else ""
        out.append(art)
    return out


def _wiki_top_title(keyword: str) -> str | None:
    r = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": keyword,
                "format": "json", "srlimit": 1},
        headers=WIKI_HEADERS, timeout=15,
    )
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def _wiki_neighbors(keyword: str, k: int) -> list[Article]:
    """Adjacent topics: the outgoing links of a keyword's Wikipedia page.

    This is the heart of 'serendipity within reach' — every result is one hop
    from something the reader already cares about, so it's novel without being
    random. List/index pages are filtered out as low-value.
    """
    title = _wiki_top_title(keyword)
    if not title:
        return []
    r = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "titles": title, "prop": "links",
                "plnamespace": 0, "pllimit": 60, "format": "json"},
        headers=WIKI_HEADERS, timeout=15,
    )
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    links = [l["title"] for p in pages.values() for l in p.get("links", [])]
    links = [t for t in links if not t.startswith(("List of", "Index of"))]
    random.shuffle(links)
    return [_wiki_article(t, kind="discover") for t in links[:k]]


def _cross_pollinate(keywords: list[str], k: int) -> list[Article]:
    """Novelty at the *intersection* of two of the reader's interests."""
    if len(keywords) < 2:
        return []
    a, b = random.sample(keywords[: min(5, len(keywords))], 2)
    out = _hn_search(f"{a} {b}", k)
    for art in out:
        art.kind = "discover"
    return out


def _rss(url: str, limit: int) -> list[Article]:
    feed = feedparser.parse(url)
    src = _host(url) or "rss"
    out = []
    for e in feed.entries[:limit]:
        link = getattr(e, "link", None)
        title = getattr(e, "title", None)
        if link and title:
            out.append(Article(title=title, url=link, source=src))
    return out


def _gather(backends) -> list[Article]:
    """Run best-effort backends; swallow individual failures."""
    results: list[Article] = []
    for fn in backends:
        try:
            results.extend(fn())
        except Exception:
            continue
    return results


def _onion_search(query: str, limit: int) -> list[Article]:
    """Best-effort onion results via Ahmia, only when Tor is connected.

    Ahmia filters abuse material, which reduces (not eliminates) the worst of
    the darkweb. Results are still *unverified* — the UI flags them as such.
    """
    proxy = tor.proxy_socks_url()
    if not proxy:
        return []
    url = f"http://{_AHMIA_ONION}/search/?q={quote_plus(query)}"
    try:
        r = httpx.get(url, headers=HEADERS, timeout=45, proxy=proxy, follow_redirects=True)
        html = r.text
    except Exception:
        return []
    out: list[Article] = []
    seen: set[str] = set()
    # Ahmia links results through /search/redirect?...&redirect_url=<onion>
    for m in re.finditer(
        r'redirect_url=([^"&]+)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{3,140})', html
    ):
        link = unquote(m.group(1)).strip()
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        host = urlparse(link).hostname or ""
        if not host.endswith(".onion") or link in seen:
            continue
        seen.add(link)
        out.append(Article(title=title or host, url=link, source="onion", onion=True))
        if len(out) >= limit:
            break
    return out


def _diverse(articles: list[Article], limit: int, max_per_host: int = 2) -> list[Article]:
    """Dedupe by URL and cap results per domain, so no single outlet dominates
    (why a leaks search shouldn't return five Guardian pieces)."""
    seen: set[str] = set()
    hosts: dict[str, int] = {}
    out: list[Article] = []
    for a in articles:
        key = a.url.split("?")[0].rstrip("/")
        host = _host(a.url)
        if not a.url.startswith("http") or key in seen or not a.title.strip():
            continue
        if hosts.get(host, 0) >= max_per_host:
            continue
        seen.add(key)
        hosts[host] = hosts.get(host, 0) + 1
        out.append(a)
        if len(out) >= limit:
            break
    return out


def _search_sync(query: str, limit: int, page: int = 0, sort: str = "relevance") -> list[Article]:
    per = max(4, limit)
    # GDELT (diverse world news) and Wikipedia enrich page 0; deeper pages page
    # through Hacker News, which has clean offset paging.
    gdelt = _gdelt_search(query, 20, sort) if page == 0 else []
    hn = _hn_search(query, per, page=page, sort=sort)
    wiki = _wiki_search(query, 3, offset=page * 3) if page == 0 else []
    # News first (freshest/most on-point for "recent news …"), then community,
    # then reference — all passed through the per-domain diversity cap.
    articles = _diverse(gdelt + hn + wiki, limit)
    onions = _onion_search(query, 5) if page == 0 else []
    if not onions:
        return articles
    keep = max(0, limit - len(onions))
    return articles[:keep] + onions


def _take(candidates, n, seen, host_counts, max_per_host=2):
    """Greedily pick up to n items, skipping already-seen URLs and capping how
    many can share a host — the anti-pigeonhole diversity guarantee."""
    picked = []
    for a in candidates:
        if len(picked) >= n:
            break
        key = a.url.split("?")[0].rstrip("/")
        host = _host(a.url)
        if not a.url.startswith("http") or key in seen or not a.title.strip():
            continue
        if host_counts.get(host, 0) >= max_per_host:
            continue
        seen.add(key)
        host_counts[host] = host_counts.get(host, 0) + 1
        picked.append(a)
    return picked


def _interleave(exploit, discover, limit):
    """Spread discover items evenly through the feed instead of clumping them,
    so exploration feels woven in rather than bolted on the end."""
    n_disc = len(discover)
    result, di, ei = [], 0, 0
    for i in range(limit):
        want = round((i + 1) * n_disc / max(1, limit))
        if want > di and di < len(discover):
            result.append(discover[di]); di += 1
        elif ei < len(exploit):
            result.append(exploit[ei]); ei += 1
        elif di < len(discover):
            result.append(discover[di]); di += 1
        else:
            break
    return result


def _feed_sync(keywords, read_urls, explore_ratio, limit) -> list[Article]:
    seen = set(read_urls or ())
    hosts: dict[str, int] = {}

    if not keywords:
        # Cold start: no interests yet — lean on trending, lightly tagged so a
        # few show as discoveries. Nothing to pigeonhole into anyway.
        exploit = _gather([lambda: _hn_front(10)])
        discover = _gather([(lambda u=u: _rss(u, 3)) for u in TRENDING_FEEDS])
        for a in discover:
            a.kind = "discover"
    else:
        anchors = keywords[:4]
        # Exploit: solidly within known interests. Query the combined anchors
        # *and* the single strongest interest so Focus mode has enough on-topic
        # material to stay focused rather than falling back to discovery.
        exploit = _gather([
            lambda: _hn_search(" ".join(anchors), limit),
            lambda: _hn_search(anchors[0], 8),
            lambda: _wiki_search(" ".join(anchors[:2]), 4),
        ])
        # Discover: anchored novelty — neighbors of an interest, the
        # intersection of two, and one small wildcard for true outside air.
        seed = random.choice(anchors)
        discover = _gather([
            lambda: _wiki_neighbors(seed, 6),
            lambda: _cross_pollinate(keywords, 4),
            lambda: [Article(a.title, a.url, a.source, kind="discover")
                     for a in _hn_front(3)],
        ])

    n_disc = max(1, round(limit * explore_ratio)) if keywords else max(3, limit // 2)
    # Discovery is strictly capped at its budget — never backfilled past it, so
    # a low explore ratio genuinely means little novelty (the dial is honest).
    disc_picks = _take(discover, n_disc, seen, hosts, max_per_host=3)
    exp_picks = _take(exploit, limit - len(disc_picks), seen, hosts, max_per_host=3)
    # If exploit is thin, top up from leftover discovery so the strip stays full.
    if len(exp_picks) + len(disc_picks) < limit:
        disc_picks += _take(discover, limit - len(exp_picks) - len(disc_picks),
                            seen, hosts, max_per_host=3)
    return _interleave(exp_picks, disc_picks, limit)


async def search(query: str, limit: int = 15, page: int = 0,
                 sort: str = "relevance") -> list[Article]:
    return await asyncio.to_thread(_search_sync, query, limit, page, sort)


async def feed(keywords, read_urls=None, explore_ratio: float = 0.4,
               limit: int = 12) -> list[Article]:
    """The carousel: exploit known interests, explore anchored novelty.

    `explore_ratio` (0..1) is the fraction of slots given to discovery — the
    dial the UI cycles through Focus / Balanced / Discover.
    """
    return await asyncio.to_thread(_feed_sync, keywords, read_urls, explore_ratio, limit)
