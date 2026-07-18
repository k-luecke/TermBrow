"""Tor integration — read .onion services through a SOCKS proxy.

TermBrow does not (and should not) run a VM or a Tor daemon itself. Instead it
speaks to an existing Tor SOCKS proxy, which can be:

  * Tor Browser's built-in Tor (127.0.0.1:9150),
  * a standalone `tor` service (127.0.0.1:9050), or
  * a **Whonix Gateway** — point TermBrow at it with `:tor <gateway-ip:9050>`
    and you get Whonix's full stream-isolation and leak protection, with
    TermBrow as a thin read-only client.

Onion addresses can't be resolved by normal DNS, so requests use `socks5h`
(the proxy resolves the host). TermBrow stays a *reader*: no forms, no login
posting, no crypto — which structurally removes most darkweb scam vectors,
since you can't transact through it.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from . import store

# Where Tor SOCKS proxies usually listen: Tor Browser first, then a tor service.
DEFAULT_PORTS = (9150, 9050)
PREF_KEY = "tor_proxy"  # stored value: "host:port", or "off", or unset (auto)


class TorNotConnected(Exception):
    """Raised when an .onion is requested but no Tor proxy is reachable."""


def is_onion(url: str) -> bool:
    ref = url if "://" in url else "http://" + url
    host = (urlparse(ref).hostname or "").lower()
    return host.endswith(".onion")


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def detect_proxy() -> str | None:
    """Return 'host:port' of a reachable Tor SOCKS proxy, honoring preferences.

    - pref "off": Tor disabled → None.
    - pref "host:port": use it if reachable, else None (configured but down).
    - unset: probe the default local ports.
    """
    pref = store.get_pref(PREF_KEY)
    if pref == "off":
        return None
    if pref:
        host, _, port = pref.partition(":")
        try:
            port_num = int(port or 9050)
        except ValueError:
            return None
        return pref if _port_open(host or "127.0.0.1", port_num) else None
    for port in DEFAULT_PORTS:
        if _port_open("127.0.0.1", port):
            return f"127.0.0.1:{port}"
    return None


def proxy_socks_url() -> str | None:
    """A `socks5h://` URL for httpx (remote DNS, required for .onion), or None."""
    proxy = detect_proxy()
    return f"socks5h://{proxy}" if proxy else None


def set_proxy(value: str | None) -> None:
    """`None` disables Tor ("off"); "auto" clears the pref; else store host:port."""
    if value is None:
        store.set_pref(PREF_KEY, "off")
    elif value == "auto":
        store.set_pref(PREF_KEY, "")
    else:
        store.set_pref(PREF_KEY, value)


def status_line() -> str:
    pref = store.get_pref(PREF_KEY)
    if pref == "off":
        return "Tor: off (onion links disabled)"
    proxy = detect_proxy()
    if proxy:
        how = "configured" if pref else "auto-detected"
        return f"Tor: connected via {proxy} ({how})"
    if pref:
        return f"Tor: NOT reachable at {pref} — is the proxy/gateway running?"
    return "Tor: not detected — start Tor Browser or a Whonix Gateway (:help onion)"
