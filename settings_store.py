"""
settings_store.py - persists dashboard settings (trading mode, capital,
risk%, etc.) to data/settings.json so they survive a server restart -
the earlier in-memory-only APP_SETTINGS dict in main.py reset on every
deploy/restart, which is a real problem for something like trading_mode
staying "algo" only in memory.

Kept intentionally simple (a single JSON file + a lock) rather than a
database - this is one small settings object with low write frequency,
not a workload that needs SQLite/Postgres.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict

SETTINGS_PATH = Path(__file__).parent / "data" / "settings.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "trading_mode": "paper",          # "paper" | "manual" | "algo"
    "capital": 25000.0,
    "risk_pct": 0.5,
    "target_rr": 5.0,
    "scan_range_pct": 10.0,           # EOD high+X% / low-X% per spec, adjustable
    "proximal_alert_pct": 0.5,
}

_lock = threading.Lock()


def load() -> Dict[str, Any]:
    with _lock:
        if not SETTINGS_PATH.exists():
            return dict(DEFAULT_SETTINGS)
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            # Merge onto defaults so a newly-added setting key doesn't
            # break loading an older settings.json.
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_SETTINGS)


def save(settings: Dict[str, Any]) -> None:
    with _lock:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = SETTINGS_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(SETTINGS_PATH)  # atomic on POSIX - avoids a half-written file on crash
