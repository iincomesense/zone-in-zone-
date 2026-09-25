"""
oi_provider.py - current-month Open Interest by strike, used to confirm
a demand zone (needs high PE OI near the zone price) or a supply zone
(needs high CE OI near the zone price). If nothing verifiable is found,
callers must show blank - never fabricate a number.

Sources (free, in fallback order):
    1. NSE India option-chain-indices / option-chain-equities API
    2. Moneycontrol option chain page (HTML parse) as backup when NSE
       blocks/rate-limits (common on shared hosting IPs like Render)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from config import CONFIG
from providers._cache import OI_CACHE
from providers._http import safe_get, new_nse_session


def _nearest_strike_row(rows: List[Dict[str, Any]], spot_price: float) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r.get("strikePrice", float("inf")) - spot_price))


def _from_nse(symbol: str, is_index: bool, spot_price: float) -> Optional[Dict[str, Any]]:
    session = new_nse_session()
    url = (
        f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        if is_index else
        f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
    )
    resp = safe_get(url, session=session)
    if not resp.get("ok") or not resp.get("data"):
        return None
    try:
        records = resp["data"]["records"]["data"]
        rows = []
        for r in records:
            strike = r.get("strikePrice")
            ce_oi = r.get("CE", {}).get("openInterest")
            pe_oi = r.get("PE", {}).get("openInterest")
            if strike is None:
                continue
            rows.append({"strikePrice": strike, "CE_OI": ce_oi or 0, "PE_OI": pe_oi or 0})
        nearest = _nearest_strike_row(rows, spot_price)
        if not nearest:
            return None
        max_ce = max(rows, key=lambda r: r["CE_OI"])
        max_pe = max(rows, key=lambda r: r["PE_OI"])
        return {
            "ok": True, "symbol": symbol,
            "nearest_strike": nearest["strikePrice"],
            "nearest_ce_oi": nearest["CE_OI"], "nearest_pe_oi": nearest["PE_OI"],
            "max_ce_oi_strike": max_ce["strikePrice"], "max_ce_oi": max_ce["CE_OI"],
            "max_pe_oi_strike": max_pe["strikePrice"], "max_pe_oi": max_pe["PE_OI"],
            "source": "NSE Option Chain",
        }
    except (KeyError, TypeError, ValueError):
        return None


def _from_moneycontrol(mc_option_chain_url: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort HTML fallback. Moneycontrol's markup changes often, so
    this is intentionally defensive - any parse miss returns None and the
    caller shows blank rather than crashing or guessing."""
    if not mc_option_chain_url:
        return None
    resp = safe_get(mc_option_chain_url)
    if not resp.get("ok") or not resp.get("text"):
        return None
    try:
        soup = BeautifulSoup(resp["text"], "lxml")
        table = soup.find("table", {"id": re.compile("optionChain", re.I)}) or soup.find("table")
        if table is None:
            return None
        # Parsing left deliberately shallow - real cell structure should be
        # confirmed against the live page and refined; returning None here
        # is safe/expected until that pass is done.
        return None
    except Exception:
        return None


def get_oi_snapshot(
    symbol: str,
    spot_price: float,
    is_index: bool = False,
    mc_fallback_url: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Returns OI context for a zone check, or a clean 'unavailable' dict
    (never fabricated numbers) so the dashboard can render it blank."""
    cache_key = f"oi:{symbol}"
    if use_cache:
        cached = OI_CACHE.get(cache_key)
        if cached is not None:
            return cached

    result = _from_nse(symbol, is_index, spot_price) or _from_moneycontrol(mc_fallback_url)
    if result is None:
        result = {"ok": False, "symbol": symbol, "status": "unavailable", "reason": "no OI source responded"}

    OI_CACHE.set(cache_key, result, CONFIG.cache_ttl_oi_sec)
    return result


def check_oi_confirms_zone(oi_snapshot: Dict[str, Any], is_demand_zone: bool) -> Dict[str, Any]:
    """Zone confirmation rule from the spec:
        demand zone  -> nearest-strike PE OI should dominate CE OI
        supply zone  -> nearest-strike CE OI should dominate PE OI
    Returns blank/unconfirmed if data is unavailable - never guesses."""
    if not oi_snapshot.get("ok"):
        return {"confirmed": None, "display": "", "reason": "oi_unavailable"}

    pe_oi = oi_snapshot.get("nearest_pe_oi", 0)
    ce_oi = oi_snapshot.get("nearest_ce_oi", 0)
    if pe_oi == 0 and ce_oi == 0:
        return {"confirmed": None, "display": "", "reason": "zero_oi"}

    if is_demand_zone:
        confirmed = pe_oi > ce_oi
        display = f"PE OI {pe_oi:,} vs CE OI {ce_oi:,}" if confirmed else ""
    else:
        confirmed = ce_oi > pe_oi
        display = f"CE OI {ce_oi:,} vs PE OI {pe_oi:,}" if confirmed else ""

    return {"confirmed": confirmed, "display": display, "pe_oi": pe_oi, "ce_oi": ce_oi}
