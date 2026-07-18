# TermBrow

A **clickable, ad-free terminal browser** built for reading and research.

TermBrow fetches any page, strips it down to the article — no ads, no nav, no
popups — and renders it as a clean, fixed-width reading column with **clickable
links**. A *For You* strip at the top curates what to read next from your own
reading history, the way a news carousel does, but from ad-free, direct
publisher sources.

It opens on a **constructive home page** — today's featured knowledge, topics
adjacent to what you study, civic facts about your own area, and your saved
library. Deliberately *not* a breaking-news or outrage stream: the goal is a
starting point that informs and grounds you rather than mining your attention.
Every traditional browser affordance (history, back, close a tab) is reachable
by both a keyboard shortcut and a toolbar click — the keyboard just means you
rarely need the mouse.

## Why it looks the way it does

The layout is deliberately monotonous: one accent color, one column width, the
same chrome in the same place on every page. Holding the visual context stable
from page to page reduces the contextual shifts that tax working memory — so
attention stays on the material, not on re-orienting to each new layout.

## Install & run

**Recommended — [pipx](https://pipx.pypa.io) installs TermBrow into its own
isolated environment and gives you a real `termbrow` command.**

If you don't already have pipx, bootstrap it first (one time):

```bash
python -m pip install --user pipx
python -m pipx ensurepath
```

> **Then reopen your terminal.** `ensurepath` adds pipx's app folder to your
> PATH, but an already-open shell won't see the change until you start a new one
> — otherwise the next step's `termbrow`/`pipx` command reports "not recognized".

Now install and run:

```bash
pipx install git+https://github.com/k-luecke/TermBrow
termbrow
```

Reopen your terminal once more after installing so the new `termbrow` command is
on PATH. To update later: `pipx upgrade termbrow`. To remove:
`pipx uninstall termbrow`.

<details>
<summary>Windows / PowerShell notes</summary>

Use `python` (as above); the commands are identical. If `termbrow` still isn't
found in a fresh terminal, the app was installed to `%USERPROFILE%\.local\bin` —
you can run it directly with:

```powershell
& "$env:USERPROFILE\.local\bin\termbrow.exe"
```
</details>

<details>
<summary>Alternatives (plain pip, or run from a clone)</summary>

```bash
# Plain pip — into the current environment (a venv is recommended)
pip install git+https://github.com/k-luecke/TermBrow
termbrow

# From a clone, for development
git clone https://github.com/k-luecke/TermBrow
cd TermBrow
pip install -e .        # editable install; then run `termbrow`
```

On Windows without pipx, the bundled `run.ps1` sets up a local `.venv` and
launches the app in one step: `./run.ps1`
</details>

Requires Python 3.9+.

## Using it

- **Home page** — opens on launch and in every new tab. Set your locale with
  `:area <your city>` to get civic facts and local topics; nothing here is an
  ad or an outrage feed.
- **Commands** — type these in the address bar: `:home`, `:history`,
  `:library`, `:area <place>`, `:login`, `:logout`, `:cookies`, `:tor`,
  `:sort <newest|relevance>`, `:help login`, `:help onion`.
- **Read sites you're logged into** — TermBrow can use your browser's session
  (see [Logging in](#logging-in) below).
- **Read anything** — type a URL in the top bar, Enter. The page loads ad-free.
- **Search / research** — type words instead of a URL to get a clickable results
  list drawn from **diverse world news** ([GDELT](https://gdeltproject.org),
  direct publisher links), Hacker News full-text, and Wikipedia — plus Tor onion
  results when connected. No single outlet can dominate: results are **capped at
  two per domain**, so "recent news on X" gives a spread, not five of the same
  paper. Each result shows its **date**; **`Ctrl+K`** toggles sort between *most
  relevant* and *newest first*; **← Previous / Next →** page through more.
  Opened articles show their **publication date** under the headline.
- **Click links** — every link in the reading pane and every *For You* chip is
  clickable and loads in place (or in a new tab — see below).
- **Save what you like** — press `Ctrl+S` while reading to add an article to your
  **Library**; open the library any time with `Ctrl+Y`. `Ctrl+S` again removes it.
- **Tabs** — `Ctrl+T` opens a new tab, `Ctrl+W` closes one, `Ctrl+PgUp`/`PgDn`
  switch. Toggle `Ctrl+N` to make links open in a **new background tab** so a
  tangent never costs you the page you were on. Each tab keeps its own history.
- **For You** — the top strip learns from what you read. The more you read, the
  more it follows your current topics; with no history it shows trending items.
- **Explore mode** — press `Ctrl+E` to cycle **Focus → Balanced → Discover**.
  This is the dial between more-of-the-same and novel discovery (see below).

### Explore mode — discovery without pigeonholing

The problem with attention-metric feeds is they collapse onto whatever you
clicked first. TermBrow's answer is **serendipity within reach**: every feed
reserves a budget for *discovery*, but discoveries are **anchored one hop from
what you already read**, so they're new without being random.

Discoveries (marked `◇`, in a quieter tint) come from three anchored sources:

- **Neighbors** — adjacent topics drawn from the Wikipedia page of one of your
  interests (genuinely related, but a step outward).
- **Cross-pollination** — the *intersection* of two of your interests, surfacing
  things that sit between them.
- **A small wildcard** — one or two purely trending items for outside air.

The `Ctrl+E` dial sets how much of the feed is discovery — **Focus** ~15%,
**Balanced** ~40%, **Discover** ~65%. Discovery is strictly capped at that
budget, so a low setting genuinely means little novelty — the dial is honest.

It learns from *engagement, not just clicks*: reading a discovery graduates its
topic into your interests; ignoring it costs nothing. Interest weights also
**decay** over time, so the feed follows recent attention instead of ossifying —
and a source-diversity cap keeps any single site from dominating the strip.

### Keys

| Key | Action |
| --- | --- |
| `Ctrl+L` | Jump to the address/search bar |
| `Ctrl+B` | Back (within the current tab) |
| `Ctrl+H` | Open history |
| `Ctrl+K` | Toggle search sort (relevant / newest) |
| `Ctrl+S` | Save / unsave the current article to the Library |
| `Ctrl+Y` | Open the Library |
| `Ctrl+T` | New tab |
| `Ctrl+W` | Close tab |
| `Ctrl+PgDn` / `Ctrl+PgUp` | Next / previous tab |
| `Ctrl+N` | Toggle: links open in a new background tab |
| `Ctrl+E` | Cycle explore mode (Focus / Balanced / Discover) |
| `Ctrl+R` | Refresh the *For You* feed |
| `Ctrl+G` | Show/hide the feed strip |
| `Ctrl+D` | Toggle light/dark |
| `Esc`    | Return focus to the reader |
| `Ctrl+Q` | Quit |

## Logging in

TermBrow fetches with plain HTTP (no browser engine), so to open a page behind a
login it borrows the **session cookies** your real browser already holds. Your
data stays local — cookies are saved only in `~/.termbrow/cookies.json`, never
uploaded.

**Easiest — `cookies.txt` (any browser):**

1. Install a cookie-export extension — *Get cookies.txt LOCALLY* (Chrome/Edge)
   or *cookies.txt* (Firefox).
2. Log in to the site in your browser, then export a `cookies.txt` for it.
3. In TermBrow, open that site and run `:login C:\path\to\cookies.txt`. The page
   reloads using your session — ad-free.

**Automatic (`:login` with no argument):** works for Firefox and unlocked cookie
stores. Modern **Chrome/Edge on Windows encrypt their cookies** ("app-bound
encryption"), which blocks automatic reading — use the `cookies.txt` method
there. Run `:help login` in the app for the same guide.

Manage sessions with `:cookies` (list) and `:logout` (forget the current site).
For sites that need JavaScript or interactive login, `Ctrl+O` still opens the
page in your real browser.

## Tor / onion services

TermBrow can open `.onion` links and fold onion results into search, for reading
research and reporting that's censored or otherwise unavailable. It does **not**
run Tor or a VM itself — it routes through an existing **Tor SOCKS proxy**:

- **Tor Browser** — just launch it; auto-detected on port 9150.
- **A `tor` service** — auto-detected on port 9050.
- **Whonix Gateway** — point TermBrow at it: `:tor 10.152.152.10:9050` (your
  gateway's address). You get Whonix's full stream-isolation and leak
  protection, with TermBrow as a thin read-only client — the right way to get
  "Whonix integration" without bundling a VM.

Commands: `:tor` (status) · `:tor host:port` (set) · `:tor off` / `:tor auto` ·
`:help onion` (setup + safety). Onion requests use `socks5h` so Tor — not your
machine — resolves the address, and onion links never leak to the clearnet.

**Avoiding scams:** TermBrow is **read-only** (no forms, logins, or crypto), so
you can't be phished or pay a scammer *through it*. Onion search uses
[Ahmia](https://ahmia.fi), which filters abuse material — but results are still
**unverified and tagged 🧅**; confirm any onion address against the service's
official clearnet site before trusting it. Use Tor for reading, not marketplaces.

## How the ad-free / curation parts work

- **Ad & clutter removal** — [trafilatura](https://trafilatura.readthedocs.io)
  extracts just the article body (dropping nav, sidebars, promos, comments), and
  a second pass defuses any surviving links that point at known ad/tracker hosts
  so a stray click can never land on an ad.
- **Curation** uses only keyless sources that return *real publisher URLs* you
  can actually open ad-free: Hacker News (Algolia) full-text search + front
  page, the Wikipedia search API, and a few reputable RSS feeds for trending.
  Google News is intentionally avoided — its RSS links are opaque redirects that
  can't be opened directly.
- **Loading "unreadable" sites** — if a direct fetch comes back blocked or
  empty (bot walls, JavaScript-only pages, dead links), TermBrow escalates
  automatically: first it retries with a full desktop-browser fingerprint, then
  it falls back to the **Wayback Machine's** latest static snapshot (which is
  often readable even when the live site isn't). The status bar shows
  `(via wayback)` when a page came from the archive. The Wayback fallback is the
  only time a URL you visit is sent to a third party (archive.org), and only
  after a direct read has failed.
- **Constructive by design** — the home page draws only from knowledge, learning,
  and civic sources (Wikipedia's featured feed, interest-adjacent topics, civic
  facts for your area). It never pulls a breaking-news / outrage stream, because
  the point is to inform without preying on negativity bias.
- **Your data stays local** — reading history, topic weights, your saved
  **Library**, and preferences live in `~/.termbrow/` as plain, inspectable JSON
  (`history.json`, `keywords.json`, `library.json`, `prefs.json`). Delete any of
  them to reset.

## Layout

```
termbrow/
  app.py      # Textual TUI: tabs, toolbar, navigation, commands, wiring
  home.py     # constructive home page (Wikipedia featured, learn, civic, library)
  fetch.py    # fetch chain (direct → browser headers → Wayback) + ad-strip
  curate.py   # search (GDELT news + HN + Wikipedia, sort + per-domain diversity)
              # and the For You feed with explore/exploit
  cookies.py  # session import (browser_cookie3 + cookies.txt) for logged-in reads
  tor.py      # Tor SOCKS proxy detection/config for .onion access (+ Whonix)
  store.py    # local history, topic weights, saved library, preferences
```

## License

[Mozilla Public License 2.0](LICENSE).
