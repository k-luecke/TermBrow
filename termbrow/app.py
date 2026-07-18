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

import asyncio
import textwrap
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, HorizontalScroll, VerticalScroll
from textual.widgets import (
    Button, Footer, Header, Input, Markdown, Static, TabbedContent, TabPane,
)

from . import cookies, curate, home, store, tor
from .fetch import Page, fetch_page

ONION_HELP = """\
# Reading Tor onion (.onion) services

TermBrow can open `.onion` links and fold onion results into search — but it
needs a **Tor proxy** to route through. It never runs Tor itself.

## Connect Tor (pick one)

- **Tor Browser** — just launch it; TermBrow auto-detects it on port 9150.
- **A `tor` service** — auto-detected on port 9050.
- **Whonix Gateway** — run TermBrow in the Workstation, or point at the gateway:
  `:tor 10.152.152.10:9050` (use your gateway's address). This gives Whonix's
  full stream-isolation and leak protection, with TermBrow as a read-only client.

Check status with `:tor`. Force a proxy with `:tor host:port`, disable with
`:tor off`, re-enable auto-detect with `:tor auto`.

## Staying safe

- TermBrow is **read-only** — no forms, no logins, no crypto — so you can't be
  phished or pay a scammer *through it*.
- Onion **search uses Ahmia**, which filters abuse material, but results are
  still **unverified**. Treat every onion address as untrusted until you confirm
  it against the service's official clearnet site.
- Most "marketplaces" are scams. Use Tor for **reading** — research, journalism,
  and reporting that's censored or otherwise unavailable. That's what this is for.

Onion pages load slower (they route through several Tor relays) — that's normal.\
"""

LOGIN_HELP = """\
# Logging in to a site

TermBrow reads pages with plain HTTP (no browser engine), so to open something
behind a login it needs the **session cookies** your real browser already holds.

## Easiest — works with any browser (`cookies.txt`)

1. Install a cookies-export extension, e.g. **Get cookies.txt LOCALLY**
   (Chrome/Edge) or **cookies.txt** (Firefox).
2. Log in to the site in your browser, then click the extension to export a
   `cookies.txt` for the current site.
3. In TermBrow, open that site, then run:  `:login C:\\path\\to\\cookies.txt`
4. TermBrow reloads the page using your session — ad-free.

## Automatic — Firefox or unlocked browsers

Just run `:login` while on the site. Modern Chrome/Edge on Windows encrypt their
cookie store, which blocks automatic reading; use the `cookies.txt` method above.

## Managing sessions

- `:logout` — forget the current site's cookies
- `:cookies` — list sites you have a saved session for

Cookies live locally in `~/.termbrow/cookies.json` and are never uploaded.\
"""

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
        discover = article.kind == "discover"
        marker = "◇ " if discover else ""
        classes = "feed-item discover" if discover else "feed-item"
        # Wrap the title to the box width (max two lines) so headlines never
        # spill outside the chip; the full title lives in the tooltip.
        lines = textwrap.wrap(marker + article.title, width=30) or [marker or "…"]
        shown = lines[:2]
        if len(lines) > 2:
            shown[1] = shown[1][:27].rstrip() + "…"
        label = "\n".join(shown) + f"\n[{source[:26]}]"
        super().__init__(label, classes=classes)
        self.url = article.url
        kind = "Discovery — new but related" if discover else "From your interests"
        self.tooltip = f"{article.title}\n\n{kind} · {source}"


class ReaderTab(TabPane):
    """One reading surface: its own scrollable article and its own back-stack."""

    def __init__(self, tab_id: str, title: str = "New Tab") -> None:
        super().__init__(title, id=tab_id)
        self.nav: list[str] = []          # per-tab navigation back-stack (URLs)
        self.page_url: str = ""           # canonical URL of the current article
        self.last_url: str = ""           # last URL *attempted* (even if it failed)
        self.page_title: str = title
        self.view: str = "home"           # home | article | search | library | history

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="reader"):
            with Center():
                # open_links=False is essential: otherwise Textual's Markdown
                # auto-opens every link in the system browser (a stray Chrome
                # window) and races our own in-app navigation. We handle all
                # link clicks ourselves via on_markdown_link_clicked.
                yield Markdown(PLACEHOLDER, classes="article", open_links=False)

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
    .navbtn:hover { border: round $accent; color: $accent; background: $boost; }
    .navbtn:focus { border: round $accent; }
    #addrbar {
        width: 1fr; height: 3;
        border: round $accent 40%; background: $surface;
    }
    #addrbar:focus { border: round $accent; }

    /* For You carousel — a single quiet strip, same place every page. */
    #feed-wrap { height: auto; }
    #feed-label { color: $text-muted; padding: 0 2; text-style: bold; }
    #feed { height: 6; padding: 0 1; scrollbar-size-horizontal: 1; }
    .feed-item {
        width: 34; height: 5; margin: 0 1 0 0;
        border: round $primary 40%; background: $surface;
        color: $text; text-align: left;
    }
    .feed-item:hover { border: round $accent; background: $boost; }
    .feed-item.discover { border: round $secondary 50%; color: $text-muted; }
    .feed-item.discover:hover { border: round $secondary; background: $boost; }

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
        ("ctrl+o", "open_external", "Open in browser"),
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

    def _set_loading(self, tab: ReaderTab, on: bool) -> None:
        # Textual's built-in loading overlay: an animated spinner over the
        # reading pane. Immediate, tactile acknowledgement that a click landed.
        try:
            tab.scroller.loading = on
        except Exception:
            pass

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
        elif name == "login":
            self.do_login(rest)
        elif name == "logout":
            self.do_logout()
        elif name == "cookies":
            self.show_cookies()
        elif name == "tor":
            self._cmd_tor(rest)
        elif name in ("onion",) or (name == "help" and rest.lower() == "onion"):
            self.run_worker(
                self._render_view(self.active_tab(), "help", "Onion / Tor help",
                                  "🧅 Onion", ONION_HELP),
                group="render", exclusive=False,
            )
        elif name == "help" and rest.lower() == "login":
            self.run_worker(
                self._render_view(self.active_tab(), "help", "Login help",
                                  "? Login", LOGIN_HELP),
                group="render", exclusive=False,
            )
        else:
            self._status(
                f"Unknown command “:{cmd}”. Try :home, :history, :login, :tor, :area <place>."
            )

    def _cmd_tor(self, rest: str) -> None:
        rest = rest.strip()
        if rest.lower() == "off":
            tor.set_proxy(None)
        elif rest.lower() in ("auto", "on", ""):
            if rest.lower() in ("auto", "on"):
                tor.set_proxy("auto")
        elif ":" in rest:
            tor.set_proxy(rest)
        else:
            self._status("Usage: :tor  ·  :tor host:port  ·  :tor off  ·  :tor auto")
            return
        self._status(tor.status_line())

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
        elif name == "back":
            self.action_back()
        elif name == "reload":
            target = self.active_tab().last_url
            if target:
                self.load_url(target, new_tab=False)
        elif name == "external":
            self.action_open_external()
        elif name == "onionhelp":
            self.run_worker(
                self._render_view(self.active_tab(), "help", "Onion / Tor help",
                                  "🧅 Onion", ONION_HELP),
                group="render", exclusive=False,
            )
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
        self._set_loading(tab, True)
        self._status("Loading home…")
        try:
            md = await home.build_homepage(
                store.get_pref("area"), store.top_keywords(), store.load_library()
            )
        except Exception as exc:
            md = f"# TermBrow\n\nCouldn't build the home page: {exc}"
        finally:
            self._set_loading(tab, False)
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
            tab = await self._new_tab("⟳ Loading…", activate=False)
        else:
            tab = self.active_tab()
            self._set_tab_label(tab, "⟳ Loading…")

        tab.last_url = url                 # remembered so Ctrl+O / retry can act
        if tor.is_onion(url):
            self._status(f"Loading onion over Tor (slower)…  {url[:50]}")
        else:
            self._status(f"Loading  {url}")
        self._set_loading(tab, True)
        try:
            page: Page = await fetch_page(url)
        except tor.TorNotConnected:
            self._set_loading(tab, False)
            self._set_tab_label(tab, tab.page_title or "Untitled")
            self._show_onion_setup(tab, url)
            return
        except httpx.HTTPStatusError as exc:
            self._set_loading(tab, False)
            self._set_tab_label(tab, tab.page_title or "Untitled")
            self._show_error(tab, url, f"HTTP {exc.response.status_code}")
            return
        except Exception as exc:
            self._set_loading(tab, False)
            self._set_tab_label(tab, tab.page_title or "Untitled")
            self._show_error(tab, url, str(exc) or exc.__class__.__name__)
            return
        finally:
            self._set_loading(tab, False)

        if push and (not tab.nav or tab.nav[-1] != page.url):
            tab.nav.append(page.url)
        tab.page_url = page.url
        tab.page_title = page.title
        tab.view = "article"

        body = page.markdown
        if page.via == "tor":
            body = (
                "> 🧅 **Onion service, loaded over Tor.** Read-only view — no "
                "forms, logins, or crypto pass through TermBrow. Verify this "
                "address via the service's official site before trusting it.\n\n"
                "---\n\n" + body
            )
        await tab.article.update(body)
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

    def _show_onion_setup(self, tab: ReaderTab, url: str) -> None:
        md = (
            f"# Tor isn't connected\n\n"
            f"**{url}** is a Tor onion address, but TermBrow can't reach a Tor "
            f"proxy right now.\n\n"
            f"> {tor.status_line()}\n\n"
            "Start **Tor Browser** or a **Whonix Gateway**, then reload — or point "
            "TermBrow at a proxy with `:tor host:port`.\n\n"
            "- [↻ Retry](termbrow:reload)\n"
            "- [How onion access works](termbrow:onionhelp)\n"
            "- [Back](termbrow:back)  ·  [Home](termbrow:home)\n"
        )
        tab.view = "error"
        tab.page_url = ""
        self.run_worker(tab.article.update(md), group="render", exclusive=False)
        self._status("Tor not connected — see :help onion")

    def _show_error(self, tab: ReaderTab, url: str, detail: str) -> None:
        hint = ""
        if "403" in detail:
            hint = (
                "\n**403** means the site blocked an automated reader (bot "
                "protection). It will usually still open in your normal browser.\n"
            )
        elif "404" in detail:
            hint = "\n**404** means the page no longer exists at that address.\n"
        md = (
            f"# Couldn't load this page\n\n"
            f"**{url}**\n\n"
            f"> {detail}\n{hint}\n"
            "TermBrow tried a direct fetch, a full browser fingerprint, and the "
            "Wayback Machine. What next:\n\n"
            "- [↻ Retry](termbrow:reload)\n"
            "- [Open in your web browser](termbrow:external)  — loads with cookies "
            "and JavaScript, outside TermBrow\n"
            "- [Back](termbrow:back)  ·  [Home](termbrow:home)\n"
        )
        tab.view = "error"
        tab.page_url = ""
        self.run_worker(tab.article.update(md), group="render", exclusive=False)
        self._status(f"Error: {detail[:66]} — Ctrl+O opens it in your browser")

    # ---------------------------------------------------------------- search
    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        tab = self.active_tab()
        self._set_loading(tab, True)
        self._status(f"Searching  “{query}”")
        try:
            results = await curate.search(query)
        except Exception as exc:
            self._set_loading(tab, False)
            self._show_error(tab, f"search: {query}", str(exc))
            return
        finally:
            self._set_loading(tab, False)
        if not results:
            await tab.article.update(
                f"# No results for “{query}”\n\nTry different or broader terms."
            )
            self._status("No results.")
            return
        lines = [f"# Search — “{query}”", ""]
        if any(a.onion for a in results):
            lines.append(
                "> 🧅 = Tor onion result (via Ahmia, abuse-filtered but "
                "**unverified**). TermBrow is read-only, so you can't be phished "
                "here — but verify any onion address before trusting it, and "
                "never send credentials or crypto.\n"
            )
        for i, a in enumerate(results, 1):
            if a.onion:
                lines.append(f"{i}. 🧅 [{a.title}]({a.url})  ·  *onion — unverified*")
            else:
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

    # ------------------------------------------------------------- sessions
    @work(exclusive=True, group="cookies")
    async def do_login(self, arg: str) -> None:
        tab = self.active_tab()
        host = (urlparse(tab.page_url or tab.last_url).hostname or "").lower()
        if arg:
            path = arg.strip().strip('"').strip("'")
            self._status(f"Importing cookies from {path}…")
            count, err = await asyncio.to_thread(
                cookies.import_cookies_txt, path, host or None
            )
        else:
            if not host:
                self._status("Open the site first, then :login  (or :login <cookies.txt>)")
                return
            self._status(f"Reading {host} session from your browser…")
            count, _browser, err = await asyncio.to_thread(
                cookies.import_from_browser, host
            )
        if count:
            self._status(f"✓ Imported {count} cookies. Reloading with your session…")
            target = tab.page_url or tab.last_url
            if target:
                self.load_url(target, new_tab=False)
        else:
            self._status(f"Login import failed — {err}")
            # Surface the how-to so the fix is one screen away.
            await self._render_view(tab, "help", "Login help", "? Login", LOGIN_HELP)

    def do_logout(self) -> None:
        tab = self.active_tab()
        host = (urlparse(tab.page_url or tab.last_url).hostname or "").lower()
        if not host:
            self._status("Open the site first, then :logout")
            return
        cookies.clear(host)
        self._status(f"Forgot the saved session for {host}.")

    @work(exclusive=True, group="search")
    async def show_cookies(self) -> None:
        tab = self.active_tab()
        saved = cookies.domains()
        if not saved:
            md = (
                "# Saved sessions\n\nNone yet. Run `:login` while on a site "
                "(or `:login <cookies.txt>`) to read pages you're logged into. "
                "See `:help login`."
            )
        else:
            lines = ["# Saved sessions", ""]
            for dom, n in sorted(saved):
                lines.append(f"- **{dom}** — {n} cookie(s)")
            lines.append("\n*`:logout` on a site forgets its cookies.*")
            md = "\n".join(lines)
        await self._render_view(tab, "cookies", "Saved sessions", "🔑 Sessions", md)

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

    def action_open_external(self) -> None:
        """Deliberately open the current/last URL in the system web browser.

        This is the on-purpose version of what we removed by disabling Markdown's
        auto-open: sites that block in-app reading (403) or need JavaScript can be
        handed to the real browser, but only when the reader asks for it."""
        tab = self.active_tab()
        target = tab.page_url or tab.last_url
        if not target:
            self._status("No page URL to open in your browser.")
            return
        self.open_url(target)
        self._status(f"Opened in your web browser: {target[:70]}")

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
