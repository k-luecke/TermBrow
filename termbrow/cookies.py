"""Session cookies — let TermBrow read pages you're logged into.

TermBrow fetches with plain HTTP (no browser engine), so to reach a page behind
a login it needs the session cookies your real browser already holds. Two ways
in, tried in this order of convenience:

  1. Automatic: read the cookie straight from a local browser via
     `browser_cookie3`. Nice when it works — but modern Chrome/Edge on Windows
     encrypt their cookie store ("app-bound encryption"), which blocks this.
  2. cookies.txt: you export the site's cookies once (a one-click browser
     extension, Netscape format) and point TermBrow at the file. Because the
     browser exports them already-decrypted, this works everywhere.

Imported cookies are stored locally in ~/.termbrow/cookies.json — the same
private, inspectable place as the rest of your data. Nothing is uploaded.
"""
from __future__ import annotations

import http.cookiejar
import time
from urllib.parse import urlparse

from .store import CONFIG_DIR, _read_json, _write_json

COOKIES_FILE = CONFIG_DIR / "cookies.json"

# Browsers we try for automatic import, best-effort and in this order.
_BROWSERS = ("chrome", "edge", "firefox", "brave", "chromium", "opera")


def _load() -> dict:
    # { "domain": [ {name, value, domain, path, secure, expires}, ... ] }
    return _read_json(COOKIES_FILE, {})


def _save(data: dict) -> None:
    _write_json(COOKIES_FILE, data)


def _registrable(host: str) -> str:
    """A crude eTLD+1 so cookies imported for www.site.com also cover site.com."""
    host = (host or "").lstrip(".").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def save_domain_cookies(domain: str, cookie_dicts: list[dict]) -> int:
    """Merge a batch of cookies under their registrable domain. Returns count."""
    if not cookie_dicts:
        return 0
    key = _registrable(domain)
    data = _load()
    existing = {(c["name"], c.get("path", "/")): c for c in data.get(key, [])}
    for c in cookie_dicts:
        existing[(c["name"], c.get("path", "/"))] = c
    data[key] = list(existing.values())
    _save(data)
    return len(cookie_dicts)


def clear(domain: str | None = None) -> None:
    if domain is None:
        _save({})
        return
    data = _load()
    data.pop(_registrable(domain), None)
    _save(data)


def domains() -> list[tuple[str, int]]:
    return [(d, len(c)) for d, c in _load().items()]


def cookies_for_url(url: str) -> dict[str, str]:
    """The name→value cookies to send with a request to `url` (unexpired)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return {}
    now = time.time()
    out: dict[str, str] = {}
    for domain, cks in _load().items():
        d = domain.lstrip(".")
        if host == d or host.endswith("." + d):
            for c in cks:
                exp = c.get("expires") or 0
                if exp and 0 < exp < now:
                    continue  # expired
                out[c["name"]] = c["value"]
    return out


def _jar_to_dicts(jar, only_host: str | None = None) -> list[dict]:
    out = []
    for c in jar:
        if only_host:
            cd = (c.domain or "").lstrip(".").lower()
            if not (only_host == cd or only_host.endswith("." + cd) or cd.endswith(only_host)):
                continue
        out.append({
            "name": c.name,
            "value": c.value or "",
            "domain": c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "expires": c.expires or 0,
        })
    return out


def import_from_browser(domain: str) -> tuple[int, str | None, str | None]:
    """Try to read `domain`'s cookies from a local browser.

    Returns (count_saved, browser_used, error_message). On the common Windows
    case where Chrome/Edge encryption blocks us, count is 0 and error explains.
    """
    try:
        import browser_cookie3 as bc
    except ImportError:
        return 0, None, "browser_cookie3 is not installed."

    host = domain.lstrip(".").lower()
    hit_encryption = False
    for name in _BROWSERS:
        fn = getattr(bc, name, None)
        if not fn:
            continue
        try:
            jar = fn(domain_name=host)
            dicts = _jar_to_dicts(jar, only_host=host)
            if dicts:
                save_domain_cookies(host, dicts)
                return len(dicts), name, None
        except Exception as exc:  # locked/encrypted store, missing profile, …
            msg = str(exc).lower()
            if "admin" in msg or "decrypt" in msg or "key" in msg:
                hit_encryption = True
            continue
    if hit_encryption:
        return 0, None, (
            "your browser encrypts its cookie store (modern Chrome/Edge on "
            "Windows), so TermBrow can't read it directly. Use a cookies.txt "
            "export instead — see :help login."
        )
    return 0, None, "no cookies found in any local browser."


def import_cookies_txt(path: str, only_domain: str | None = None) -> tuple[int, str | None]:
    """Import a Netscape-format cookies.txt file. Returns (count, error)."""
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(path, ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        return 0, f"File not found: {path}"
    except Exception as exc:
        return 0, f"Couldn't read cookies.txt ({exc}). Is it Netscape format?"

    host = only_domain.lstrip(".").lower() if only_domain else None
    dicts = _jar_to_dicts(jar, only_host=host)
    if not dicts:
        where = f" for {only_domain}" if only_domain else ""
        return 0, f"No cookies{where} found in that file."

    # Group by registrable domain so each site's cookies are stored together.
    by_domain: dict[str, list[dict]] = {}
    for c in dicts:
        by_domain.setdefault(_registrable(c["domain"]), []).append(c)
    total = 0
    for dom, cks in by_domain.items():
        total += save_domain_cookies(dom, cks)
    return total, None
