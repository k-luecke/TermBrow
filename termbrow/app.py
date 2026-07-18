"""TermBrow TUI — a clickable, ad-free terminal browser for reading and research.

Design intent (per the cognitive-load rationale behind this tool): keep the
frame *stable*. One accent color, one fixed-width reading column, the same
chrome in the same place on every page. Minimizing contextual shifts between
pages is what lets the reader keep attention on the material instead of
re-orienting to a new layout each time.

Tabs preserve that stability across parallel reads: opening a link in a new tab
keeps the page you were on intact, so a tangent never costs you your place.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, HorizontalScroll, VerticalScroll
from textual.widgets import (
    Button, Footer, Header, Input, Markdown, Static, TabbedContent, TabPane,
)

from . import curate, store
from .fetch import Page, fetch_page

WELCOME = """\
# TermBrow

A clickable, ad-free terminal browser tuned for **reading** and **research**.

- Type a **URL** or a **search query** in the bar above and press Enter.
- Every link below is **clickable** — click to follow it, ad-free.
- The **For You** strip up top learns from what you read (`Ctrl+E` tunes it).
- Save what you enjoy with `Ctrl+S`; open your **Library** with `Ctrl+Y`.
- Open links in a **new tab** (`Ctrl+N` toggles) so you never lose your place.

Try clicking one of these to start:

1. [Cognitive load (Wikipedia)](https://en.wikipedia.org/wiki/Cognitive_load)
2. [The value of deep reading (Ars Technica)](https://arstechnica.com/science/)
3. [Hacker News front page items appear in your feed above]\
"""


class FeedButton(Button):
    """A carousel chip that remembers which article it points at.

    Discoveries get a ◇ marker and a distinct border so exploration is visible
    and honest — the reader can always tell 'more of what I read' from 'something
    new you might like'.
    """

    def __init__(self, article: curate.Article) -> None:
        source = article.source or "web"
        title = article.title if len(article.title) <= 46 else article.title[:45] + "…"
        discover = article.kind == "discover"
        marker = "◇ " if discover else ""
        classes = "feed-item discover" if discover else "feed-item"
        super().__init__(f"{marker}{title}\n[{source}]", classes=classes)
        self.url = article.url
        self.tooltip = "Discovery — new but related" if discover else "From your interests"


class ReaderTab(TabPane):
    """One reading surface: its own scrollable article and its own back-stack."""

    def __init__(self, tab_id: str, title: str = "New Tab") -> None:
        super().__init__(title, id=tab_id)
        self.nav: list[str] = []          # per-tab navigation back-stack (URLs)
        self.page_url: str = ""           # canonical URL of the current article
        self.page_title: str = title
        self.view: str = "welcome"        # welcome | article | search | library

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="reader"):
            with Center():
                yield Markdown(WELCOME, classes="article")

    @property
    def article(self) -> Markdown:
        return self.query_one(Markdown)

    @property
    def scroller(self) -> VerticalScroll:
        return self.query_one(VerticalScroll)


class TermBrow(App):
    TITLE = "TermBrow"
    SUB_TITLE = "reading browser"

    CSS = """
    Screen { background: $background; }

    #addrbar {
        margin: 0 1;
        border: round $accent 40%;
        background: $surface;
    }
    #addrbar:focus { border: round $accent; }

    /* For You carousel — a single quiet strip, same place every page. */
    #feed-wrap { height: auto; }
    #feed-label { color: $text-muted; padding: 0 2; text-style: bold; }
    #feed { height: 5; padding: 0 1; scrollbar-size-horizontal: 1; }
    .feed-item {
        width: 30; height: 3; margin: 0 1 0 0;
        border: round $primary 40%; background: $surface;
        color: $text; text-align: left;
    }
    .feed-item:hover { border: round $accent; }
    /* Discoveries: a quiet secondary tint — visible, still within one system. */
    .feed-item.discover { border: round $secondary 50%; color: $text-muted; }
    .feed-item.discover:hover { border: round $secondary; }

    /* Reading column: fixed width, centered, so line length never shifts. */
    #tabs { height: 1fr; }
    .reader { padding: 1 0 2 0; }
    .article { width: 84; max-width: 100%; }

    #status {
        dock: bottom; height: 1; padding: 0 2;
        color: $text-muted; background: $panel;
    }
    """

    BINDINGS = [
        ("ctrl+l", "focus_address", "Address"),
        ("ctrl+b", "back", "Back"),
        ("ctrl+t", "new_tab", "New tab"),
        ("ctrl+w", "close_tab", "Close tab"),
        ("ctrl+pagedown", "next_tab", "Next tab"),
        ("ctrl+pageup", "prev_tab", "Prev tab"),
        ("ctrl+n", "toggle_new_tab_links", "Link→new tab"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+y", "show_library", "Library"),
        ("ctrl+e", "cycle_explore", "Explore mode"),
        ("ctrl+r", "refresh_feed", "Refresh feed"),
        ("ctrl+g", "toggle_feed", "Toggle feed"),
        ("ctrl+d", "toggle_dark", "Light/Dark"),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "focus_reader", "Reader"),
    ]

    # Explore/exploit dial: name → fraction of the feed given to discovery.
    EXPLORE_MODES = [("Focus", 0.15), ("Balanced", 0.4), ("Discover", 0.65)]

    def __init__(self) -> None:
        super().__init__()
        self._explore_idx = 1        # default: Balanced
        self._tab_seq = 1            # last-assigned tab number (tab-1 exists)
        self._new_tab_links = False  # when True, clicks open a background tab

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Input(placeholder="Enter a URL or a search query…  (Ctrl+L)", id="addrbar")
        with Horizontal(id="feed-wrap"):
            yield Static("For You", id="feed-label")
        yield HorizontalScroll(id="feed")
        with TabbedContent(id="tabs"):
            yield ReaderTab("tab-1")
        yield Static("Ready.", id="status")
        yield Footer()

    # ------------------------------------------------------------------ mount
    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.refresh_feed()

    # --------------------------------------------------------------- helpers
    def _status(self, msg: str) -> None:
        # Guarded: a worker may finish just as the app is tearing down, after
        # the status widget has left the DOM — that's harmless, not an error.
        try:
            self.query_one("#status", Static).update(msg)
        except Exception:
            pass

    def _tabs(self) -> TabbedContent:
        return self.query_one("#tabs", TabbedContent)

    def active_tab(self) -> ReaderTab:
        tc = self._tabs()
        return tc.get_pane(tc.active)  # type: ignore[return-value]

    def _reader_tabs(self) -> list[ReaderTab]:
        return list(self._tabs().query(ReaderTab))

    def _set_tab_label(self, tab: ReaderTab, title: str) -> None:
        label = title if len(title) <= 22 else title[:21] + "…"
        try:
            self._tabs().get_tab(tab.id).label = label or "Untitled"
        except Exception:
            pass

    def _sync_chrome(self, tab: ReaderTab) -> None:
        """Point the shared chrome (address bar, title) at `tab`'s state."""
        self.query_one("#addrbar", Input).value = tab.page_url
        self.sub_title = tab.page_title[:60] if tab.page_title else "reading browser"

    def _looks_like_url(self, text: str) -> bool:
        text = text.strip()
        if "://" in text:
            return True
        return " " not in text and "." in text and not text.endswith(".")

    async def _new_tab(self, title: str = "New Tab", *, activate: bool = True) -> ReaderTab:
        self._tab_seq += 1
        tab_id = f"tab-{self._tab_seq}"
        pane = ReaderTab(tab_id, title)
        await self._tabs().add_pane(pane)
        if activate:
            self._tabs().active = tab_id
        return pane

    # ------------------------------------------------------------ input flow
    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        if self._looks_like_url(value):
            self.load_url(value)
        else:
            self.run_search(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button, FeedButton):
            self.load_url(event.button.url)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        # Every clickable link (article, search results, library) loads here.
        event.stop()
        self.load_url(event.href)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = self.active_tab()
        if isinstance(tab, ReaderTab):
            self._sync_chrome(tab)

    # ------------------------------------------------------------ navigation
    def load_url(self, url: str, *, new_tab: bool | None = None, push: bool = True) -> None:
        """Public entry point. Dispatches the actual fetch onto a worker so the
        UI stays responsive; `new_tab` defaults to the Ctrl+N toggle."""
        if new_tab is None:
            new_tab = self._new_tab_links
        self.run_worker(
            self._load(url, new_tab=new_tab, push=push),
            group="load", exclusive=False,
        )

    async def _load(self, url: str, *, new_tab: bool, push: bool) -> None:
        if not urlparse(url).scheme and "." in url:
            url = "https://" + url

        # A new-tab open stays in the background so the current page isn't lost.
        if new_tab:
            tab = await self._new_tab("Loading…", activate=False)
        else:
            tab = self.active_tab()

        self._status(f"Loading  {url}")
        try:
            page: Page = await fetch_page(url)
        except httpx.HTTPStatusError as exc:
            self._show_error(tab, url, f"HTTP {exc.response.status_code}")
            return
        except Exception as exc:  # transport, timeout, malformed URL, …
            self._show_error(tab, url, str(exc) or exc.__class__.__name__)
            return

        if push and (not tab.nav or tab.nav[-1] != page.url):
            tab.nav.append(page.url)
        tab.page_url = page.url
        tab.page_title = page.title
        tab.view = "article"

        await tab.article.update(page.markdown)
        tab.scroller.scroll_home(animate=False)
        self._set_tab_label(tab, page.title)

        saved = "★ " if store.is_saved(page.url) else ""
        via = "" if page.via == "direct" else f"  (via {page.via})"
        if tab is self.active_tab():
            self._sync_chrome(tab)
            self._status(f"{saved}Read: {page.title[:66]}{via}")
        else:
            self._status(f"Opened in new tab: {page.title[:50]}{via}  (Ctrl+PgDn)")

        # Remember what was read, then let the feed follow that attention.
        store.record_visit(page.url, page.title)
        store.bump_keywords(page.keywords)
        self.refresh_feed()

    def _show_error(self, tab: ReaderTab, url: str, detail: str) -> None:
        md = (
            f"# Could not load page\n\n"
            f"**{url}**\n\n"
            f"> {detail}\n\n"
            "TermBrow tried a direct fetch, a full browser fingerprint, and the "
            "Wayback Machine. The site may require login, be JavaScript-only with "
            "no archived snapshot, or be offline. Try another link or a search."
        )
        tab.view = "article"
        tab.page_url = ""
        self.run_worker(tab.article.update(md), group="render", exclusive=False)
        self._status(f"Error: {detail[:70]}")

    # ---------------------------------------------------------------- search
    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        tab = self.active_tab()
        self._status(f"Searching  “{query}”")
        try:
            results = await curate.search(query)
        except Exception as exc:
            self._show_error(tab, f"search: {query}", str(exc))
            return
        if not results:
            await tab.article.update(
                f"# No results for “{query}”\n\nTry different or broader terms."
            )
            self._status("No results.")
            return
        lines = [f"# Search — “{query}”", ""]
        for i, a in enumerate(results, 1):
            lines.append(f"{i}. [{a.title}]({a.url})  ·  *{a.source}*")
        tab.view = "search"
        tab.page_url = ""
        tab.page_title = f"Search: {query}"
        await tab.article.update("\n".join(lines))
        tab.scroller.scroll_home(animate=False)
        self._set_tab_label(tab, f"⌕ {query}")
        self._sync_chrome(tab)
        self._status(f"{len(results)} results for “{query}”.")

    # ------------------------------------------------------------------ feed
    @work(exclusive=True, group="feed")
    async def refresh_feed(self) -> None:
        keywords = store.top_keywords()
        mode_name, ratio = self.EXPLORE_MODES[self._explore_idx]
        try:
            articles = await curate.feed(
                keywords, read_urls=store.read_urls(), explore_ratio=ratio
            )
        except Exception:
            articles = []
        try:
            feed = self.query_one("#feed", HorizontalScroll)
            await feed.remove_children()
            if not articles:
                await feed.mount(Static("  (feed unavailable — check your connection)"))
                return
            await feed.mount_all([FeedButton(a) for a in articles])
            base = "For You" if keywords else "Trending"
            n_disc = sum(1 for a in articles if a.kind == "discover")
            self.query_one("#feed-label", Static).update(
                f"{base}  ·  {mode_name}  ·  {n_disc} ◇ new"
            )
        except Exception:
            pass  # app tearing down mid-refresh — nothing to render into

    # --------------------------------------------------------------- library
    @work(exclusive=True, group="search")
    async def show_library(self) -> None:
        tab = self.active_tab()
        lib = store.load_library()
        if not lib:
            md = (
                "# Library\n\nYour library is empty.\n\n"
                "While reading an article, press **Ctrl+S** to save it here."
            )
        else:
            lines = [f"# Library — {len(lib)} saved", ""]
            for i, item in enumerate(lib, 1):
                host = (urlparse(item["url"]).hostname or "").replace("www.", "")
                lines.append(f"{i}. [{item['title']}]({item['url']})  ·  *{host}*")
            lines.append("\n*Open one to read it. Ctrl+S again while reading removes it.*")
            md = "\n".join(lines)
        tab.view = "library"
        tab.page_url = ""
        tab.page_title = "Library"
        await tab.article.update(md)
        tab.scroller.scroll_home(animate=False)
        self._set_tab_label(tab, "★ Library")
        self._sync_chrome(tab)
        self._status(f"Library: {len(lib)} saved article(s).")

    # --------------------------------------------------------------- actions
    def action_focus_address(self) -> None:
        addr = self.query_one("#addrbar", Input)
        addr.focus()
        addr.action_end()

    def action_focus_reader(self) -> None:
        self.active_tab().scroller.focus()

    def action_back(self) -> None:
        tab = self.active_tab()
        if len(tab.nav) < 2:
            self._status("No page to go back to in this tab.")
            return
        tab.nav.pop()               # drop current
        target = tab.nav[-1]
        self.load_url(target, new_tab=False, push=False)

    def action_new_tab(self) -> None:
        self.run_worker(self._open_blank_tab(), group="tabs", exclusive=False)

    async def _open_blank_tab(self) -> None:
        tab = await self._new_tab("New Tab", activate=True)
        self._sync_chrome(tab)
        self.action_focus_address()
        self._status("New tab. Type a URL or search.")

    def action_close_tab(self) -> None:
        tc = self._tabs()
        if tc.tab_count <= 1:
            self._status("Can't close the last tab.")
            return
        self.run_worker(tc.remove_pane(tc.active), group="tabs", exclusive=False)
        self._status("Tab closed.")

    def _switch_tab(self, delta: int) -> None:
        tabs = self._reader_tabs()
        if len(tabs) < 2:
            return
        ids = [t.id for t in tabs]
        cur = self._tabs().active
        idx = (ids.index(cur) + delta) % len(ids) if cur in ids else 0
        self._tabs().active = ids[idx]

    def action_next_tab(self) -> None:
        self._switch_tab(1)

    def action_prev_tab(self) -> None:
        self._switch_tab(-1)

    def action_toggle_new_tab_links(self) -> None:
        self._new_tab_links = not self._new_tab_links
        where = "a new background tab" if self._new_tab_links else "this tab"
        self._status(f"Links now open in {where}.")

    def action_save(self) -> None:
        tab = self.active_tab()
        if tab.view != "article" or not tab.page_url:
            self._status("Nothing to save — open an article first.")
            return
        if store.is_saved(tab.page_url):
            store.remove_article(tab.page_url)
            self._status(f"Removed from library: {tab.page_title[:60]}")
        else:
            store.save_article(tab.page_url, tab.page_title)
            self._status(f"★ Saved to library: {tab.page_title[:60]}")

    def action_show_library(self) -> None:
        self.show_library()

    def action_refresh_feed(self) -> None:
        self._status("Refreshing feed…")
        self.refresh_feed()

    def action_cycle_explore(self) -> None:
        self._explore_idx = (self._explore_idx + 1) % len(self.EXPLORE_MODES)
        name, ratio = self.EXPLORE_MODES[self._explore_idx]
        self._status(f"Explore mode: {name} — {int(ratio * 100)}% discovery")
        self.refresh_feed()

    def action_toggle_feed(self) -> None:
        for wid in ("#feed", "#feed-wrap"):
            w = self.query_one(wid)
            w.display = not w.display

    def action_toggle_dark(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"


def main() -> None:
    TermBrow().run()


if __name__ == "__main__":
    main()
