"""TermBrow TUI — a clickable, ad-free terminal browser for reading and research.

Design intent (per the cognitive-load rationale behind this tool): keep the
frame *stable*. One accent color, one fixed-width reading column, the same
chrome in the same place on every page. Minimizing contextual shifts between
pages is what lets the reader keep attention on the material instead of
re-orienting to a new layout each time.

Tabs preserve that stability across parallel reads; a constructive home page
sets the tone on open; and every traditional affordance (history, back, close a
tab) is reachable by both a keyboard shortcut and a toolbar click — the keyboard
just means you rarely need the mouse.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, HorizontalScroll, VerticalScroll
from textual.widgets import (
    Button, Footer, Header, Input, Markdown, Static, TabbedContent, TabPane,
)

from . import curate, home, store
from .fetch import Page, fetch_page

# Brief placeholder shown for the instant before the home page finishes loading.
PLACEHOLDER = "# TermBrow\n\n*Loading your home page…*"


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
        self.view: str = "home"           # home | article | search | library | history

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="reader"):
            with Center():
                yield Markdown(PLACEHOLDER, classes="article")

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

    /* Toolbar: mouse parity for the traditional actions, beside the address. */
    #toolbar { height: 3; margin: 0 1; }
    .navbtn {
        width: 5; min-width: 5; height: 3; margin: 0 1 0 0;
        border: round $accent 30%; background: $surface; color: $text;
    }
    .navbtn:hover { border: round $accent; color: $accent; }
    #addrbar {
        width: 1fr; height: 3;
        border: round $accent 40%; background: $surface;
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
        ("ctrl+h", "show_history", "History"),
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
        with Horizontal(id="toolbar"):
            yield Button("‹", id="tb-back", classes="navbtn")
            yield Button("⌂", id="tb-home", classes="navbtn")
            yield Input(placeholder="URL, search, or :command …  (Ctrl+L)", id="addrbar")
            yield Button("+", id="tb-new", classes="navbtn")
            yield Button("✕", id="tb-close", classes="navbtn")
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
        # Tooltips make the icon toolbar self-explanatory.
        self.query_one("#tb-back", Button).tooltip = "Back (Ctrl+B)"
        self.query_one("#tb-home", Button).tooltip = "Home (:home)"
        self.query_one("#tb-new", Button).tooltip = "New tab (Ctrl+T)"
        self.query_one("#tb-close", Button).tooltip = "Close tab (Ctrl+W)"
        self.go_home()
        self.refresh_feed()

    # --------------------------------------------------------------- helpers
    def _status(self, msg: str) -> None:
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
        if value.startswith(":"):
            self._run_command(value[1:].strip())
        elif self._looks_like_url(value):
            self.load_url(value)
        else:
            self.run_search(value)

    def _run_command(self, cmd: str) -> None:
        name, _, rest = cmd.partition(" ")
        name, rest = name.lower(), rest.strip()
        if name in ("home", "h"):
            self.go_home()
        elif name == "history":
            self.show_history()
        elif name in ("library", "lib"):
            self.show_library()
        elif name == "area":
            if rest:
                store.set_pref("area", rest)
                self._status(f"Area set to “{rest}”. Refreshing home…")
                self.go_home()
            else:
                self._status("Usage: :area <your city or region>")
        else:
            self._status(f"Unknown command “:{cmd}”. Try :home, :history, :area <place>.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button
        if isinstance(button, FeedButton):
            self.load_url(button.url)
            return
        action = {
            "tb-back": self.action_back,
            "tb-home": self.action_home,
            "tb-new": self.action_new_tab,
            "tb-close": self.action_close_tab,
        }.get(button.id or "")
        if action:
            action()

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        href = event.href
        if href.startswith("termbrow:"):
            self._handle_internal(href)
        else:
            self.load_url(href)

    def _handle_internal(self, href: str) -> None:
        """In-app action links from the home page (keyboard- or mouse-driven)."""
        rest = href[len("termbrow:"):]
        name, _, query = rest.partition("?")
        if name == "home":
            self.go_home()
        elif name == "history":
            self.show_history()
        elif name == "library":
            self.show_library()
        elif name == "area":
            addr = self.query_one("#addrbar", Input)
            addr.focus()
            addr.value = ":area "
            addr.action_end()
            self._status("Type your city or region, then Enter.")
        elif name == "search":
            params = parse_qs(query)
            q = unquote((params.get("q") or [""])[0])
            if q:
                self.run_search(q)
        else:
            self._status(f"Unknown action: {href}")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = self.active_tab()
        if isinstance(tab, ReaderTab):
            self._sync_chrome(tab)

    # -------------------------------------------------------------- home page
    def go_home(self, tab: ReaderTab | None = None) -> None:
        self.run_worker(self._go_home(tab), group="load", exclusive=False)

    async def _go_home(self, tab: ReaderTab | None) -> None:
        tab = tab or self.active_tab()
        self._status("Loading home…")
        try:
            md = await home.build_homepage(
                store.get_pref("area"), store.top_keywords(), store.load_library()
            )
        except Exception as exc:
            md = f"# TermBrow\n\nCouldn't build the home page: {exc}"
        tab.view = "home"
        tab.page_url = ""
        tab.page_title = "Home"
        await tab.article.update(md)
        tab.scroller.scroll_home(animate=False)
        self._set_tab_label(tab, "⌂ Home")
        if tab is self.active_tab():
            self._sync_chrome(tab)
            self._status("Home — a calmer place to start.")

    # ------------------------------------------------------------ navigation
    def load_url(self, url: str, *, new_tab: bool | None = None, push: bool = True) -> None:
        if new_tab is None:
            new_tab = self._new_tab_links
        self.run_worker(
            self._load(url, new_tab=new_tab, push=push),
            group="load", exclusive=False,
        )

    async def _load(self, url: str, *, new_tab: bool, push: bool) -> None:
        if not urlparse(url).scheme and "." in url:
            url = "https://" + url

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
        except Exception as exc:
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

    # -------------------------------------------------------- library/history
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
        await self._render_view(tab, "library", "Library", "★ Library", md)
        self._status(f"Library: {len(lib)} saved article(s).")

    @work(exclusive=True, group="search")
    async def show_history(self) -> None:
        tab = self.active_tab()
        history = store.load_history()
        seen: set[str] = set()
        recent = []
        for visit in reversed(history):        # newest first, de-duplicated
            if visit.url in seen:
                continue
            seen.add(visit.url)
            recent.append(visit)
        if not recent:
            md = "# History\n\nNothing here yet — pages you read will be listed here."
        else:
            lines = [f"# History — {len(recent)} recent", ""]
            for i, visit in enumerate(recent[:150], 1):
                host = (urlparse(visit.url).hostname or "").replace("www.", "")
                lines.append(f"{i}. [{visit.title}]({visit.url})  ·  *{host}*")
            md = "\n".join(lines)
        await self._render_view(tab, "history", "History", "⏱ History", md)
        self._status(f"History: {len(recent)} page(s).")

    async def _render_view(
        self, tab: ReaderTab, view: str, title: str, label: str, md: str
    ) -> None:
        tab.view = view
        tab.page_url = ""
        tab.page_title = title
        await tab.article.update(md)
        tab.scroller.scroll_home(animate=False)
        self._set_tab_label(tab, label)
        self._sync_chrome(tab)

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

    # --------------------------------------------------------------- actions
    def action_focus_address(self) -> None:
        addr = self.query_one("#addrbar", Input)
        addr.focus()
        addr.action_end()

    def action_focus_reader(self) -> None:
        self.active_tab().scroller.focus()

    def action_home(self) -> None:
        self.go_home()

    def action_back(self) -> None:
        tab = self.active_tab()
        if len(tab.nav) < 2:
            self._status("No page to go back to in this tab.")
            return
        tab.nav.pop()
        target = tab.nav[-1]
        self.load_url(target, new_tab=False, push=False)

    def action_new_tab(self) -> None:
        self.run_worker(self._open_new_tab(), group="tabs", exclusive=False)

    async def _open_new_tab(self) -> None:
        tab = await self._new_tab("New Tab", activate=True)
        await self._go_home(tab)          # new tabs open the home page
        self.action_focus_address()

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

    def action_show_history(self) -> None:
        self.show_history()

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
