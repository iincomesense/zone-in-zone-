"""
data_pipeline.py - single entry point the zone-scanner / dashboard layer
calls. Ties together price_provider, oi_provider, fii_dii_provider,
news_provider and dhan_client, and implements the "only refresh after
the running candle closes" memory rule from the spec.

This file does NOT run zone_core/zone_validation itself (those already
exist and are timeframe-agnostic pattern logic) - it prepares the market
context each zone gets displayed with: live price, OI confirmation,
FII/DII backdrop, and verified news/events.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config import CONFIG, status_report
from providers import _cache as cache_module
from providers._cache import ZONE_SCAN_CACHE
from providers import price_provider, oi_provider, fii_dii_provider, news_provider

# Timeframe -> pandas offset alias, used to compute each TF's last CLOSED
# candle boundary so we never re-scan mid-candle.
TF_TO_PANDAS_FREQ = {
    "3M": "3min", "5M": "5min", "10M": "10min", "15M": "15min", "30M": "30min",
    "75M": "75min", "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h",
    "Daily": "1D", "Weekly": "1W", "Monthly": "1ME",
}


def last_closed_candle_ts(timeframe: str, now: Optional[datetime] = None) -> pd.Timestamp:
    """Floor 'now' to the timeframe grid, then step back one bar - that is
    the timestamp of the most recently CLOSED candle for this timeframe.
    Used as the cache/scan key so repeated dashboard polls within the
    same still-forming candle serve cached results instead of re-scanning.

    Note: intraday/daily frequencies (3min..1D) are FIXED-length pandas
    offsets and can use Timestamp.floor() directly. Weekly and Monthly
    are calendar-based (variable length - a month isn't a fixed number
    of nanoseconds), so pandas .floor() raises ValueError on them - those
    two are floored manually below instead.
    """
    if timeframe not in TF_TO_PANDAS_FREQ:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    now = now or datetime.now(timezone.utc)
    ts = pd.Timestamp(now)

    if timeframe == "Monthly":
        current_month_start = ts.normalize().replace(day=1)
        last_month_end = current_month_start - pd.Timedelta(days=1)
        return last_month_end.replace(day=1)  # start of the last CLOSED month

    if timeframe == "Weekly":
        current_week_start = ts.normalize() - pd.Timedelta(days=ts.dayofweek)
        return current_week_start - pd.Timedelta(weeks=1)  # start of last CLOSED week

    freq = TF_TO_PANDAS_FREQ[timeframe]
    floored = ts.floor(freq)
    # floored is the START of the currently-forming candle -> the previous
    # bar boundary is the last CLOSED candle.
    return floored - pd.tseries.frequencies.to_offset(freq)


def should_rescan(symbol: str, timeframe: str, now: Optional[datetime] = None) -> bool:
    """True only when we've moved past the cached last-closed-candle key,
    i.e. a new candle has actually closed since the last scan."""
    key = f"scan_marker:{symbol}:{timeframe}"
    current_marker = str(last_closed_candle_ts(timeframe, now))
    cached_marker = ZONE_SCAN_CACHE.get(key)
    if cached_marker == current_marker:
        return False
    ZONE_SCAN_CACHE.set(key, current_marker, ttl_sec=60 * 60 * 24)  # long TTL; overwritten each new close
    return True


def get_market_context(
    symbol: str,
    yahoo_symbol: Optional[str] = None,
    nse_symbol: Optional[str] = None,
    dhan_security_id: Optional[int] = None,
    dhan_segment: str = "NSE_EQ",
    is_index: bool = False,
    fetch_news: bool = True,
) -> Dict[str, Any]:
    """Everything the dashboard needs to render alongside a scanned zone
    for one instrument: live price, OI snapshot, FII/DII backdrop, and
    verified news/events. Every sub-call already fails soft, so this
    function itself cannot raise from a source outage."""
    price = price_provider.get_live_price(
        symbol, yahoo_symbol=yahoo_symbol, nse_symbol=nse_symbol,
        dhan_security_id=dhan_security_id, dhan_segment=dhan_segment,
    )

    oi_snapshot = None
    if price.get("ok"):
        oi_snapshot = oi_provider.get_oi_snapshot(symbol, spot_price=price["price"], is_index=is_index)

    fii_dii = fii_dii_provider.get_fii_dii_last_3_days()

    news = None
    if fetch_news:
        news = news_provider.get_verified_news_and_events(instrument_query=symbol)

    return {
        "symbol": symbol,
        "price": price,
        "oi_snapshot": oi_snapshot,
        "fii_dii": fii_dii,
        "news": news,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_market_context_bulk(instruments: List[Dict[str, Any]], fetch_news: bool = False) -> Dict[str, Dict[str, Any]]:
    """instruments: list of dicts each shaped like the kwargs of
    get_market_context. News is off by default in bulk mode - it's the
    slowest/most rate-limited call; fetch it per-symbol on demand
    (e.g. when a user opens that instrument's detail view) instead."""
    out = {}
    for inst in instruments:
        symbol = inst["symbol"]
        out[symbol] = get_market_context(fetch_news=fetch_news, **inst)
    return out


def health_check() -> Dict[str, Any]:
    """For a /health endpoint or the Settings page - shows which sources
    are configured without ever leaking key values."""
    return {
        "config": status_report(),
        "cache_sizes": {
            "price": len(cache_module.PRICE_CACHE._store),
            "oi": len(cache_module.OI_CACHE._store),
            "fii_dii": len(cache_module.FII_DII_CACHE._store),
            "news": len(cache_module.NEWS_CACHE._store),
        },
        "timeframes_supported": list(TF_TO_PANDAS_FREQ.keys()),
    }
