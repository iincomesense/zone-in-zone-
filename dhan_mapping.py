"""
dhan_mapping.py - loads data/dhan_security_ids.json (built by
build_dhan_mapping.py) at runtime. If the file doesn't exist yet (fresh
clone, mapping script not run), every lookup simply returns None instead
of raising - callers (scheduler._fetch_candles, price_provider bulk
calls) already treat "no Dhan security_id" as "skip Dhan, use next
source", so this degrades cleanly.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("dhan_mapping")

_MAPPING_PATH = Path(__file__).parent / "data" / "dhan_security_ids.json"
_lock = threading.Lock()
_cache: Optional[Dict[str, Dict[str, str]]] = None


def _load() -> Dict[str, Dict[str, str]]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if not _MAPPING_PATH.exists():
            logger.warning(
                "data/dhan_security_ids.json missing - run `python build_dhan_mapping.py` "
                "once Dhan keys are set. Dhan will be skipped for candles until then."
            )
            _cache = {}
            return _cache
        try:
            _cache = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to parse %s: %s", _MAPPING_PATH, exc)
            _cache = {}
        return _cache


def get_security_id(symbol: str) -> Optional[str]:
    entry = _load().get(symbol.upper())
    return entry.get("security_id") if entry else None


def get_exchange_segment(symbol: str, default: str = "NSE_EQ") -> str:
    entry = _load().get(symbol.upper())
    return entry.get("exchange_segment", default) if entry else default


def reload() -> None:
    """Call after re-running build_dhan_mapping.py in a long-lived process."""
    global _cache
    with _lock:
        _cache = None
    _load()


def is_mapped(symbol: str) -> bool:
    return get_security_id(symbol) is not None


def mapping_size() -> int:
    return len(_load())
