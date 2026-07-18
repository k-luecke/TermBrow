"""The home page — a calm, constructive landing surface.

Philosophy: most feeds monetize attention by amplifying outrage and novelty.
TermBrow's home page does the opposite. It draws only from sources oriented
toward *knowledge, learning, and civic life* — Wikipedia's featured material,
adjacent topics from what you already study, and civic facts about your own
area — and it never pulls a breaking-news / outrage stream. The goal is a page
that leaves you a little more informed and a little more grounded, not agitated.

Everything here is keyless and clickable. Internal `termbrow:` links drive
in-app actions (set your area, open history) so the page is fully usable by
mouse or keyboard alike.
"""
from __future__ import annotations

import asyncio
import datetime

import httpx

from . import store
from .curate import WIKI_HEADERS, Article, _wiki_neighbors, _wiki_search

_FEATURED = "https://en.wikipedia.org/api/rest_v1/feed/featured/{date}"


async def _get_featured(client: httpx.AsyncClient) -> dict:
    """Wikipedia's featured feed for today (falling back a day for TZ lag)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for delta in (0, 1):
        date = (now - datetime.timedelta(days=delta)).strftime("%Y/%m/%d")
        try:
            r = await client.get(_FEATURED.format(date=date))
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return {}


def _link(title: str, url: str) -> str:
    title = title.replace("[", "(").replace("]", ")")
    return f"[{title}]({url})" if url else title


def _tfa_block(feat: dict) -> list[str]:
    tfa = feat.get("tfa") or {}
    if not tfa:
        return []
    title = (tfa.get("titles") or {}).get("normalized") or tfa.get("title", "")
    url = ((tfa.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    extract = tfa.get("extract", "").strip()
    if not title:
        return []
    return ["## Today in knowledge", "", f"**{_link(title, url)}** — {extract}", ""]


def _onthisday_block(feat: dict, n: int = 4) -> list[str]:
    events = feat.get("onthisday") or []
    if not events:
        return []
    out = ["## On this day", ""]
    for e in events[:n]:
        year = e.get("year", "")
        text = e.get("text", "").strip()
        page = (e.get("pages") or [{}])[0]
        url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")
        out.append(f"- **{year}** — {_link(text, url)}" if url else f"- **{year}** — {text}")
    out.append("")
    return out


def _learn_block(interests: list[str]) -> list[str]:
    """Adjacent learning: topics one hop from what the reader already studies."""
    if not interests:
        return [
            "## Learn something",
            "",
            "As you read, this space fills with topics adjacent to your "
            "interests — a gentle way outward, not a rabbit hole. Start with "
            "anything above, or search a subject (`Ctrl+L`).",
            "",
        ]
    seed = interests[0]
    neighbors: list[Article] = _wiki_neighbors(seed, 4)
    if not neighbors:
        return []
    out = [f"## Learn something — near *{seed}*", ""]
    for a in neighbors:
        out.append(f"- {_link(a.title.replace(' — Wikipedia', ''), a.url)}")
    out.append("")
    return out


def _civic_block(area: str | None) -> list[str]:
    if not area:
        return [
            "## Civic & community",
            "",
            "Set your area to see civic facts and local topics here — no ads, "
            "no outrage cycle, just what's useful where you live.",
            "",
            "- [Set your area](termbrow:area)  ·  or type `:area Your City` above",
            "- [How local government works](https://en.wikipedia.org/wiki/Local_government)",
            "- [What a city council does](https://en.wikipedia.org/wiki/City_council)",
            "",
        ]
    facts: list[Article] = _wiki_search(f"{area} government", 3)
    out = [f"## Civic & community — {area}", ""]
    if facts:
        for a in facts:
            out.append(f"- {_link(a.title.replace(' — Wikipedia', ''), a.url)}")
    out.append(
        f"- [Search local topics for {area}](termbrow:search?q={area} city council school board)"
    )
    out.append(f"- [Change your area](termbrow:area)")
    out.append("")
    return out


def _library_block(library: list[dict], n: int = 5) -> list[str]:
    if not library:
        return [
            "## Your library",
            "",
            "Nothing saved yet. Press `Ctrl+S` while reading to keep an article "
            "here. [Open your library](termbrow:library) any time.",
            "",
        ]
    out = ["## Your library", ""]
    for item in library[:n]:
        out.append(f"- {_link(item.get('title', 'Untitled'), item.get('url', ''))}")
    if len(library) > n:
        out.append(f"- [See all {len(library)} saved →](termbrow:library)")
    out.append("")
    return out


_FOOTER = (
    "---\n\n"
    "**Getting around** · `Ctrl+L` search or type a URL · `Ctrl+H` history · "
    "`Ctrl+Y` library · `Ctrl+T` new tab · `Ctrl+E` tune your feed · "
    "`Ctrl+B` back · the toolbar buttons do the same by mouse.\n\n"
    "*No ads. No outrage feed. Just things worth your attention.*"
)


def _offline_home() -> str:
    """Shown when the constructive sources can't be reached."""
    return (
        "# TermBrow\n\n"
        "*A calmer way to read — no ads, no outrage feed.*\n\n"
        "Couldn't reach the knowledge sources just now (offline?). You can still "
        "type a URL or search with `Ctrl+L`, open your library with `Ctrl+Y`, or "
        "view history with `Ctrl+H`.\n\n" + _FOOTER
    )


async def build_homepage(area: str | None, interests: list[str], library: list[dict]) -> str:
    """Assemble the home page Markdown from constructive, keyless sources."""
    try:
        async with httpx.AsyncClient(
            headers=WIKI_HEADERS, timeout=20.0, follow_redirects=True
        ) as client:
            feat = await _get_featured(client)
    except Exception:
        feat = {}

    # The interest-adjacent and civic blocks hit sync HTTP helpers — run them off
    # the event loop so the UI never stalls while the page assembles.
    learn = await asyncio.to_thread(_learn_block, interests)
    civic = await asyncio.to_thread(_civic_block, area)

    parts: list[str] = [
        "# TermBrow",
        "",
        "*A calmer way to read. No ads, no outrage feed — just things worth "
        "your attention.*",
        "",
    ]
    parts += _tfa_block(feat)
    parts += _onthisday_block(feat)
    parts += learn
    parts += civic
    parts += _library_block(library)
    parts.append(_FOOTER)

    md = "\n".join(parts)
    # If literally nothing loaded (no featured, no civic/library), fall back.
    if not feat and not area and not library and not interests:
        return _offline_home()
    return md
