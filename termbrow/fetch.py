"""Fetch a URL and reduce it to clean, ad-free reading Markdown.

Ad/clutter removal happens in two layers:
  1. trafilatura strips boilerplate (nav, sidebars, promo blocks, comments) and
     returns just the article body.
  2. We post-filter the surviving links, defusing any that point at known
     ad/tracker hosts so a stray click can never navigate into an ad.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import trafilatura

# A descriptive UA + Accept headers. Several large sites (Wikipedia among them)
# 403 a bare "Mozilla" string; this identifies the client honestly and works.
HEADERS = {
    "User-Agent": "TermBrow/1.0 (terminal reading browser) Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# A full desktop-Chrome fingerprint used as a second attempt: some sites that
# 403 the honest UA above will serve a "real browser". Includes a Referer so we
# look like an organic click-through rather than a bare bot request.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
}

# Below this many characters we treat extraction as a failure and escalate to
# the next strategy (alternate headers, then the Wayback Machine).
_MIN_READABLE = 280

# Link destinations we never want a reader to click into. Substring match on the
# host is intentional — it catches subdomains (e.g. pagead2.googlesyndication).
AD_HOSTS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "google-analytics.com", "googletagmanager.com", "adservice.google",
    "taboola.com", "outbrain.com", "criteo.com", "adnxs.com", "rubiconproject.com",
    "pubmatic.com", "amazon-adsystem.com", "scorecardresearch.com", "quantserve.com",
    "moatads.com", "adsafeprotected.com", "zedo.com", "sharethrough.com",
    "bidswitch.net", "smartadserver.com", "openx.net", "3lift.com", "casalemedia.com",
    "yieldmo.com", "teads.tv", "revcontent.com", "mgid.com", "ad.doubleclick.net",
)

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")

# Small English stopword set — enough to keep keyword extraction focused on
# topic words without pulling in a heavyweight NLP dependency.
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "her",
    "was", "one", "our", "out", "day", "get", "has", "him", "his", "how", "man",
    "new", "now", "old", "see", "two", "way", "who", "boy", "did", "its", "let",
    "put", "say", "she", "too", "use", "that", "with", "have", "this", "will",
    "your", "from", "they", "know", "want", "been", "good", "much", "some", "time",
    "very", "when", "come", "here", "just", "like", "long", "make", "many", "more",
    "only", "over", "such", "take", "than", "them", "well", "were", "what", "which",
    "would", "there", "their", "about", "could", "other", "these", "those", "into",
    "also", "after", "first", "where", "being", "while", "should", "because",
    "through", "between", "however", "said", "says", "each", "most", "may", "used",
}


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    keywords: list[str]
    via: str = "direct"  # "direct" | "browser-headers" | "wayback"


def _defuse_ad_links(markdown: str) -> str:
    """Turn `[text](ad-url)` into plain `text` so ad links can't be followed."""

    def repl(m: re.Match) -> str:
        text, url = m.group(1), m.group(2)
        host = (urlparse(url).hostname or "").lower()
        if any(bad in host for bad in AD_HOSTS):
            return text
        return m.group(0)

    return _LINK_RE.sub(repl, markdown)


def extract_keywords(title: str, body: str, n: int = 8) -> list[str]:
    """Frequency-based topic words from the article, title words weighted 3x."""
    counts: dict[str, int] = {}
    for source, weight in ((title, 3), (body, 1)):
        for w in _WORD_RE.findall(source.lower()):
            if w in _STOPWORDS or len(w) < 4:
                continue
            counts[w] = counts.get(w, 0) + weight
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [w for w, _ in ranked[:n]]


def _extract(html: str, url: str) -> Page:
    title = ""
    meta = trafilatura.extract_metadata(html)
    if meta and meta.title:
        title = meta.title
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    if not title:
        # Fall back to the first Markdown heading, else the hostname.
        m = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
        title = m.group(1).strip() if m else (urlparse(url).hostname or url)
    markdown = _defuse_ad_links(markdown)
    if not markdown.strip():
        markdown = (
            "*TermBrow could not extract readable article text from this page.*\n\n"
            "It may be a login wall, a JavaScript-only app, or a non-article page."
        )
    keywords = extract_keywords(title, markdown)
    return Page(url=url, title=title, markdown=markdown, keywords=keywords)


async def _try_direct(url: str, headers: dict, via: str) -> tuple[Page | None, Exception | None]:
    """One direct fetch+extract attempt. Returns (page_or_None, error_or_None)."""
    try:
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=25.0
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            final_url, html = str(resp.url), resp.text
        page = await asyncio.to_thread(_extract, html, final_url)
        page.via = via
        return page, None
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller's chain
        return None, exc


async def _try_wayback(url: str) -> Page | None:
    """Last resort: read the newest Wayback Machine snapshot of the page.

    Snapshots are static HTML captured after the original page rendered, so this
    recovers many sites that block us, sit behind soft paywalls, render only via
    JavaScript, or have since gone offline.
    """
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30.0
        ) as client:
            avail = await client.get(
                "https://archive.org/wayback/available", params={"url": url}
            )
            if avail.status_code != 200:
                return None  # e.g. 429 rate-limit — just skip the fallback
            snap = (avail.json().get("archived_snapshots") or {}).get("closest") or {}
            snap_url = snap.get("url")
            if not snap.get("available") or not snap_url:
                return None
            resp = await client.get(snap_url)
            resp.raise_for_status()
            html = resp.text
        # Extract against the *original* URL so links/keywords stay canonical.
        page = await asyncio.to_thread(_extract, html, url)
        page.via = "wayback"
        return page
    except Exception:  # noqa: BLE001 — fallback is best-effort by design
        return None


async def fetch_page(url: str) -> Page:
    """Fetch `url` as clean reading Markdown, escalating through fallbacks.

    Strategy: honest direct fetch → full-browser headers → Wayback snapshot.
    Raises only if every strategy fails to produce any content at all.
    """
    if not urlparse(url).scheme:
        url = "https://" + url

    best: Page | None = None
    first_error: Exception | None = None

    for headers, via in ((HEADERS, "direct"), (BROWSER_HEADERS, "browser-headers")):
        page, err = await _try_direct(url, headers, via)
        if err is not None and first_error is None:
            first_error = err
        if page is not None:
            if len(page.markdown) >= _MIN_READABLE:
                return page
            best = best or page  # keep a thin result as a possible fallback

    wb = await _try_wayback(url)
    if wb is not None and len(wb.markdown) >= _MIN_READABLE:
        return wb
    best = best or wb

    if best is not None:
        return best
    raise first_error or RuntimeError(f"Could not load {url}")
