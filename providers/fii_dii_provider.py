"""
fii_dii_provider.py - FII/DII cash-market activity, last 3 trading days.

Per the spec, StockEdge is used as the reference/inbuilt-link source for
this on the dashboard (it already aggregates NSE's own FII/DII bulletin
nicely for display). This module:
    1. Pulls the raw NSE FII/DII figures (free, official) for the actual
       numbers used in the AI hypothesis.
    2. Also returns the StockEdge URL so the frontend can embed/link it
       as the visual reference the user asked for.
"""
from __future__ import annotations

from typing import Any, Dict, List

from config import CONFIG
from providers._cache import FII_DII_CACHE
from providers._http import safe_get, new_nse_session

STOCKEDGE_FII_DII_URL = "https://web.stockedge.com/fii-dii-activity"
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"


def get_fii_dii_last_3_days(use_cache: bool = True) -> Dict[str, Any]:
    cache_key = "fii_dii:last3"
    if use_cache:
        cached = FII_DII_CACHE.get(cache_key)
        if cached is not None:
            return cached

    session = new_nse_session()
    resp = safe_get(NSE_FII_DII_URL, session=session)

    result: Dict[str, Any]
    if resp.get("ok") and isinstance(resp.get("data"), list):
        rows: List[Dict[str, Any]] = []
        for row in resp["data"][:3]:
            try:
                rows.append({
                    "date": row.get("date"),
                    "category": row.get("category"),
                    "buy_value_cr": float(row.get("buyValue", 0)),
                    "sell_value_cr": float(row.get("sellValue", 0)),
                    "net_value_cr": float(row.get("netValue", 0)),
                })
            except (TypeError, ValueError):
                continue
        result = {
            "ok": bool(rows), "rows": rows, "source": "NSE India",
            "stockedge_url": STOCKEDGE_FII_DII_URL,
        }
    else:
        result = {
            "ok": False, "rows": [], "status": "unavailable",
            "reason": resp.get("error", "NSE FII/DII endpoint unreachable"),
            "stockedge_url": STOCKEDGE_FII_DII_URL,
        }

    FII_DII_CACHE.set(cache_key, result, CONFIG.cache_ttl_fii_dii_sec)
    return result
