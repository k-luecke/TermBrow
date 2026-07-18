"""Persistent state: reading history and accumulated topic keywords.

Kept deliberately small and human-readable (JSON under ~/.termbrow) so the
"curation" is inspectable — the user can see exactly what shapes their feed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = Path.home() / ".termbrow"
HISTORY_FILE = CONFIG_DIR / "history.json"
KEYWORDS_FILE = CONFIG_DIR / "keywords.json"
LIBRARY_FILE = CONFIG_DIR / "library.json"

# How much a single visit boosts a keyword, and how fast interest decays each
# time we re-score. Decay keeps the feed following *recent* attention rather
# than ossifying around whatever was read first.
_VISIT_WEIGHT = 1.0
_DECAY = 0.98


@dataclass
class Visit:
    url: str
    title: str
    ts: float = field(default_factory=time.time)


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return default


def _write_json(path: Path, data) -> None:
    _ensure_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_history() -> list[Visit]:
    return [Visit(**v) for v in _read_json(HISTORY_FILE, [])]


def read_urls() -> set[str]:
    """URLs already read — used to keep the feed from re-serving old pages."""
    return {v.get("url", "") for v in _read_json(HISTORY_FILE, [])}


def record_visit(url: str, title: str) -> None:
    history = _read_json(HISTORY_FILE, [])
    # De-dupe consecutive reloads of the same page.
    if history and history[-1].get("url") == url:
        return
    history.append(asdict(Visit(url=url, title=title)))
    _write_json(HISTORY_FILE, history[-500:])


def load_keywords() -> dict[str, float]:
    return _read_json(KEYWORDS_FILE, {})


def bump_keywords(keywords: list[str]) -> None:
    """Decay all interests slightly, then reward the freshly-read topics."""
    weights = load_keywords()
    for k in list(weights):
        weights[k] = round(weights[k] * _DECAY, 4)
        if weights[k] < 0.05:
            del weights[k]
    for k in keywords:
        weights[k] = round(weights.get(k, 0.0) + _VISIT_WEIGHT, 4)
    _write_json(KEYWORDS_FILE, weights)


def top_keywords(n: int = 6) -> list[str]:
    weights = load_keywords()
    return [k for k, _ in sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:n]]


# --------------------------------------------------------------- library
# The library is the reader's own keep-pile: articles they chose to save,
# independent of the automatic history/interest tracking.

def load_library() -> list[dict]:
    """Saved articles, most-recently-saved first."""
    return _read_json(LIBRARY_FILE, [])


def is_saved(url: str) -> bool:
    return any(item.get("url") == url for item in load_library())


def save_article(url: str, title: str) -> bool:
    """Add to the library. Returns False if it was already saved."""
    lib = load_library()
    if any(item.get("url") == url for item in lib):
        return False
    lib.insert(0, {"url": url, "title": title, "ts": time.time()})
    _write_json(LIBRARY_FILE, lib)
    return True


def remove_article(url: str) -> bool:
    """Remove from the library. Returns False if it wasn't there."""
    lib = load_library()
    kept = [item for item in lib if item.get("url") != url]
    if len(kept) == len(lib):
        return False
    _write_json(LIBRARY_FILE, kept)
    return True


def clear_all() -> None:
    for p in (HISTORY_FILE, KEYWORDS_FILE, LIBRARY_FILE):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
