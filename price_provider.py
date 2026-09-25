"""
price_provider.py - live price + change% with automatic source fallback.

Fallback order (as requested: Dhan when connected, free sources always
available as backup so the app never goes blank):
    1. Dhan (only if configured) - fastest/most accurate for held positions
    2. NSE India official quote endpoint (free, no key, but fragile/rate-limited)
    3. Yahoo Finance via yfinance (free, no key, most reliable uptime)

Every function returns a uniform dict:
    {"ok": True, "symbol":..., "price":..., "change":..., "change_pct":...,
     "source": "Dhan"/"NSE"/"Yahoo Finance", "status": "success"}
  or
    {"ok": False, "symbol":..., "status": "failed", "error": "..."}
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config import CONFIG
from providers._cache import PRICE_CACHE
from providers._http import safe_get, new_nse_session
from providers import dhan_client


def _from_dhan(symbol: str, dhan_security_id: Optional[int], dhan_segment: str) -> Optional[Dict[str, Any]]:
    if not dhan_security_id or not dhan_client.is_available():
        return None
    result = dhan_client.get_quote({dhan_segment: [dhan_security_id]})
    if not result.get("ok"):
        return None
    try:
        segment_data = result["data"]["data"][dhan_segment][str(dhan_security_id)]
        ltp = float(segment_data["last_price"])
        close = float(segment_data.get("close_price") or segment_data.get("ohlc", {}).get("close") or ltp)
        change = ltp - close
        change_pct = (change / close * 100.0) if close else 0.0
        return {
            "ok": True, "symbol": symbol, "price": round(ltp, 2),
            "change": round(change, 2), "change_pct": round(change_pct, 2),
            "source": "Dhan", "status": "success",
        }
    except (KeyError, TypeError, ValueError):
        return None


def _from_nse(symbol: str, nse_symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    if not nse_symbol:
        return None
    session = new_nse_session()
    is_index = nse_symbol.upper() in {"NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE"}
    url = (
        "https://www.nseindia.com/api/allIndices"
        if is_index else
        f"https://www.nseindia.com/api/quote-equity?symbol={nse_symbol}"
    )
    resp = safe_get(url, session=session)
    if not resp.get("ok") or not resp.get("data"):
        return None
    try:
        if is_index:
            rows = resp["data"]["data"]
            row = next((r for r in rows if r.get("index") == nse_symbol), None)
            if not row:
                return None
            ltp = float(row["last"])
            change = float(row["change"])
            change_pct = float(row["percentChange"])
        else:
            data = resp["data"]
            price_info = data["priceInfo"]
            ltp = float(price_info["lastPrice"])
            change = float(price_info["change"])
            change_pct = float(price_info["pChange"])
        return {
            "ok": True, "symbol": symbol, "price": round(ltp, 2),
            "change": round(change, 2), "change_pct": round(change_pct, 2),
            "source": "NSE India", "status": "success",
        }
    except (KeyError, TypeError, ValueError, StopIteration):
        return None


def _from_yahoo(symbol: str, yahoo_symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    if not yahoo_symbol:
        return None
    try:
        import yfinance as yf
        ticker = yf.Ticker(yahoo_symbol)
        hist = ticker.history(period="2d")
        if hist.empty:
            return None
        curr = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else curr
        change = curr - prev
        change_pct = (change / prev * 100.0) if prev else 0.0
        return {
            "ok": True, "symbol": symbol, "price": round(curr, 2),
            "change": round(change, 2), "change_pct": round(change_pct, 2),
            "source": "Yahoo Finance", "status": "success",
        }
    except Exception:
        return None


def get_live_price(
    symbol: str,
    yahoo_symbol: Optional[str] = None,
    nse_symbol: Optional[str] = None,
    dhan_security_id: Optional[int] = None,
    dhan_segment: str = "NSE_EQ",
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Try Dhan -> NSE -> Yahoo in order, cache the first success."""
    cache_key = f"price:{symbol}"
    if use_cache:
        cached = PRICE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    for fetcher, args in (
        (_from_dhan, (symbol, dhan_security_id, dhan_segment)),
        (_from_nse, (symbol, nse_symbol)),
        (_from_yahoo, (symbol, yahoo_symbol)),
    ):
        result = fetcher(*args)
        if result is not None:
            PRICE_CACHE.set(cache_key, result, CONFIG.cache_ttl_price_sec)
            return result

    failed = {
        "ok": False, "symbol": symbol, "price": None, "change": None,
        "change_pct": None, "status": "failed",
        "error": "all sources (Dhan/NSE/Yahoo) failed or unconfigured",
    }
    # Cache failures too, but briefly, so a dead symbol doesn't hammer
    # every source on every dashboard refresh.
    PRICE_CACHE.set(cache_key, failed, min(CONFIG.cache_ttl_price_sec, 5))
    return failed


def get_live_prices_bulk(instruments: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """instruments: {symbol: {"yahoo": ..., "nse": ..., "dhan_id": ..., "dhan_segment": ...}}
    Returns {symbol: price_result}. Sequential for now - safe default;
    swap to asyncio/threaded batch once source rate-limits are confirmed
    in production (NSE in particular bans aggressive parallel hitting)."""
    out: Dict[str, Dict[str, Any]] = {}
    for symbol, meta in instruments.items():
        out[symbol] = get_live_price(
            symbol,
            yahoo_symbol=meta.get("yahoo"),
            nse_symbol=meta.get("nse"),
            dhan_security_id=meta.get("dhan_id"),
            dhan_segment=meta.get("dhan_segment", "NSE_EQ"),
        )
    return out
