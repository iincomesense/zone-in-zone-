"""
_cache.py - tiny in-memory TTL cache.

Why this exists: the user's spec explicitly asks that small timeframes
(3M/5M/10M) not re-trigger network/API load on every check - only after
the running candle actually closes, and results should sit in memory
between closes. This cache is the mechanism for that: each data type
(price/oi/fii_dii/news) gets its own TTL, and zone_core scan results are
keyed by (symbol, timeframe, last_closed_candle_timestamp) so a repeat
call within the same still-open candle is served from memory.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_sec: float) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl_sec, value)

    def get_or_set(self, key: str, ttl_sec: float, factory: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl_sec)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Process-wide caches, one per data domain, so a price refresh doesn't
# evict OI/news entries and vice versa.
PRICE_CACHE = TTLCache()
OI_CACHE = TTLCache()
FII_DII_CACHE = TTLCache()
NEWS_CACHE = TTLCache()
ZONE_SCAN_CACHE = TTLCache()  # keyed by symbol|timeframe|last_closed_bar_ts
HYPOTHESIS_CACHE = TTLCache()  # keyed by symbol|zone_type|last_closed_bar_ts
