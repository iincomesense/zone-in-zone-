"""
scheduler.py - runs the zone scan for every instrument x every timeframe,
but ONLY actually re-scans a (symbol, timeframe) pair when that
timeframe's candle has just closed - per the spec: "running candle
complete होने के बाद ही scan चले... मेमोरी में रखे जिससे बार-बार लोड से बचे".

This module owns the in-memory `LATEST_RESULTS` store the dashboard API
reads from. It does not fetch candle data itself (that's a broker/vendor
concern - see `_fetch_candles` below, which is the one function you must
wire to a real OHLCV source before this runs for real).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

import instruments
import dhan_mapping
import settings_store
from config import CONFIG
from data_pipeline import should_rescan, get_market_context, TF_TO_PANDAS_FREQ
from zone_validation import scan_and_validate_final
from trade_setup import get_all_trade_setups
from hypothesis_engine import get_zone_hypothesis
from nifty_context import get_nifty_validated_zones, check_nifty_conflict
from providers import price_provider, dhan_client

logger = logging.getLogger("scheduler")

TIMEFRAMES: List[str] = [
    "3M", "5M", "10M", "15M", "30M", "75M", "1H", "2H", "4H", "6H",
    "Daily", "Weekly", "Monthly",
]

# In-memory store the API layer reads from. Structure:
#   LATEST_RESULTS[symbol][timeframe] = {"zones": [...trade-setup dicts...], "scanned_at": iso-str}
LATEST_RESULTS: Dict[str, Dict[str, Any]] = {}
_NIFTY_ZONES_CACHE: Dict[str, Any] = {"zones": None, "price": None, "scanned_at": None}


async def _fetch_candles(symbol: str, timeframe: str, lookback_bars: int = 400) -> Optional[pd.DataFrame]:
    """Real implementation: pulls OHLCV from Dhan's historical/intraday
    endpoints via dhan_client, using the symbol->security_id mapping
    built by build_dhan_mapping.py (see dhan_mapping.py).

    Returns None (never raises) when:
        - Dhan isn't configured (keys missing)
        - this symbol has no security_id mapping yet
        - Dhan's API call itself fails
        - the response can't be parsed into a usable OHLCV frame
    In every None case the caller (scan_one) just skips this symbol/
    timeframe for this cycle - the scheduler keeps going.
    """
    if not dhan_client.is_available():
        return None

    security_id = dhan_mapping.get_security_id(symbol)
    if security_id is None:
        return None
    segment = dhan_mapping.get_exchange_segment(symbol)

    intraday_minutes = {
        "3M": 3, "5M": 5, "10M": 10, "15M": 15, "30M": 30,
        "75M": 75, "1H": 60, "2H": 120, "4H": 240, "6H": 360,
    }

    from_date, to_date = _date_range_for(timeframe)

    if timeframe in intraday_minutes:
        result = dhan_client.get_historical_candles(
            int(security_id), segment, "EQUITY",
            from_date=from_date, to_date=to_date,
            interval_minutes=intraday_minutes[timeframe],
        )
        df = _parse_dhan_candles(result)
        return df

    # Daily/Weekly/Monthly: Dhan gives daily candles; Weekly/Monthly are
    # built here by resampling, since Dhan's historical endpoint is daily-only.
    result = dhan_client.get_historical_candles(
        int(security_id), segment, "EQUITY", from_date=from_date, to_date=to_date,
    )
    daily_df = _parse_dhan_candles(result)
    if daily_df is None or daily_df.empty:
        return None

    if timeframe == "Daily":
        return daily_df
    if timeframe == "Weekly":
        return _resample_ohlcv(daily_df, "W-FRI")
    if timeframe == "Monthly":
        return _resample_ohlcv(daily_df, "ME")
    return None


def _date_range_for(timeframe: str) -> tuple[str, str]:
    """Conservative lookback windows - intraday history APIs are usually
    capped (~90 days is common across vendors including Dhan), longer
    timeframes get a multi-year window so ATR/base-count lookbacks in
    zone_core have enough bars."""
    from datetime import date, timedelta
    today = date.today()
    intraday_tfs = {"3M", "5M", "10M", "15M", "30M", "75M", "1H", "2H", "4H", "6H"}
    if timeframe in intraday_tfs:
        start = today - timedelta(days=85)
    elif timeframe == "Daily":
        start = today - timedelta(days=730)
    else:  # Weekly/Monthly need a much longer daily history to resample from
        start = today - timedelta(days=365 * 5)
    return start.isoformat(), today.isoformat()


def _parse_dhan_candles(result: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """Defensive parser - Dhan's documented shape is a dict of parallel
    arrays: {"open":[...], "high":[...], "low":[...], "close":[...],
    "volume":[...], "timestamp":[...] (epoch seconds)}. Falls back to a
    list-of-dicts shape too, in case of an API version difference.
    Returns None on any shape it doesn't recognise, rather than guessing."""
    if not result.get("ok"):
        return None
    data = result.get("data")
    if not data:
        return None

    try:
        if isinstance(data, dict) and "open" in data:
            n = len(data["open"])
            ts = data.get("timestamp") or data.get("start_Time") or list(range(n))
            df = pd.DataFrame({
                "open": data["open"], "high": data["high"],
                "low": data["low"], "close": data["close"],
                "volume": data.get("volume", [0] * n),
            }, index=pd.to_datetime(ts, unit="s", errors="coerce"))
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            df = pd.DataFrame(data)
            time_col = next((c for c in ("timestamp", "start_Time", "time") if c in df.columns), None)
            if time_col is None:
                return None
            df.index = pd.to_datetime(df[time_col], unit="s", errors="coerce")
            df = df.rename(columns={c: c.lower() for c in df.columns})
        else:
            return None

        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df = df[~df.index.isna()].sort_index()
        return df if not df.empty else None
    except (KeyError, TypeError, ValueError):
        return None


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
    return out


async def scan_one(symbol: str, timeframe: str, meta: Dict[str, Any]) -> None:
    if not should_rescan(symbol, timeframe):
        return  # candle for this TF hasn't closed since we last scanned - serve cache

    df = await _fetch_candles(symbol, timeframe)
    if df is None or df.empty:
        return

    try:
        valid_zones = scan_and_validate_final(df, parent_tf=timeframe)
    except Exception:
        logger.exception("zone scan failed for %s/%s", symbol, timeframe)
        return

    user_settings = settings_store.load()
    setups = get_all_trade_setups(
        df, valid_zones,
        capital=user_settings["capital"],
        risk_pct=user_settings["risk_pct"],
        rr=user_settings["target_rr"],
    )

    nifty_price = _NIFTY_ZONES_CACHE.get("price")
    nifty_zones = _NIFTY_ZONES_CACHE.get("zones") or []

    price = price_provider.get_live_price(
        symbol, yahoo_symbol=meta.get("yahoo"), nse_symbol=meta.get("nse"),
    )
    fetch_news = bool(setups)  # only bother with news when there's actually something to show
    market_context = get_market_context(
        symbol, yahoo_symbol=meta.get("yahoo"), nse_symbol=meta.get("nse"),
        fetch_news=fetch_news,
    ) if fetch_news else {"price": price, "oi_snapshot": None, "fii_dii": None, "news": None}

    enriched = []
    for setup in setups:
        conflict = check_nifty_conflict(
            nifty_zones, nifty_price, instrument_is_demand=setup["isDemand"],
        ) if nifty_price else {"checked": False, "conflict": None}

        hyp = get_zone_hypothesis(
            symbol, is_demand_zone=setup["isDemand"], market_context=market_context,
            nifty_zone_conflict=conflict.get("conflict"),
        )
        enriched.append({
            **setup,
            "symbol": symbol,
            "timeframe": timeframe,
            "nifty_conflict": conflict,
            "hypothesis": hyp["hypothesis"],
            "hypothesis_source": hyp["source"],
            "tradingview_link": instruments.tradingview_link(symbol),
        })

    LATEST_RESULTS.setdefault(symbol, {})[timeframe] = {
        "setups": enriched,
        "price": price,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


async def refresh_nifty_zones(timeframe: str = "Daily") -> None:
    """Scans Nifty once per cycle and caches it for every instrument's
    conflict-check that same cycle, instead of re-scanning Nifty once
    per instrument (which the spec explicitly warns against - avoid
    repeated load)."""
    meta = instruments.GLOBAL_INSTRUMENTS["NIFTY1!"]
    df = await _fetch_candles("NIFTY1!", timeframe)
    price = price_provider.get_live_price("NIFTY1!", yahoo_symbol=meta.get("yahoo"))
    _NIFTY_ZONES_CACHE["price"] = price["price"] if price.get("ok") else None
    if df is not None and not df.empty:
        try:
            _NIFTY_ZONES_CACHE["zones"] = get_nifty_validated_zones(df, timeframe=timeframe)
        except Exception:
            logger.exception("nifty zone scan failed")
            _NIFTY_ZONES_CACHE["zones"] = []
    _NIFTY_ZONES_CACHE["scanned_at"] = datetime.now(timezone.utc).isoformat()


async def run_full_cycle() -> None:
    """One pass: refresh Nifty context, then scan every NSE instrument
    across every timeframe. should_rescan() inside scan_one() makes this
    cheap to call often - most (symbol, timeframe) pairs will just be a
    cache-marker check and an early return."""
    await refresh_nifty_zones()

    all_symbols = list(instruments.NSE_INSTRUMENTS.keys())
    for symbol in all_symbols:
        meta = {"nse": symbol, "yahoo": f"{symbol.replace('&', '_')}.NS"}
        for tf in TIMEFRAMES:
            await scan_one(symbol, tf, meta)


async def run_forever(poll_interval_sec: int = 30) -> None:
    """Entry point for a background task in main.py. Deliberately polls
    frequently (should_rescan filters out no-op work) rather than trying
    to precisely time each timeframe's close - simpler and self-healing
    if the process restarts mid-candle."""
    while True:
        try:
            await run_full_cycle()
        except Exception:
            logger.exception("scan cycle failed - will retry next interval")
        await asyncio.sleep(poll_interval_sec)


def get_proximal_alerts(proximity_pct: float = 0.5) -> List[Dict[str, Any]]:
    """Spec: notify when a zone's proximal line comes within 0.5% of
    current price. Reads purely from LATEST_RESULTS memory - cheap, no
    network calls, safe to poll from the API layer often."""
    alerts = []
    for symbol, by_tf in LATEST_RESULTS.items():
        for tf, payload in by_tf.items():
            price = payload.get("price", {})
            if not price.get("ok"):
                continue
            current = price["price"]
            for setup in payload.get("setups", []):
                distance_pct = abs(setup["proxVal"] - current) / current * 100.0 if current else None
                if distance_pct is not None and distance_pct <= proximity_pct:
                    alerts.append({
                        "symbol": symbol, "timeframe": tf,
                        "zone_type": "Demand" if setup["isDemand"] else "Supply",
                        "proxVal": setup["proxVal"], "current_price": current,
                        "distance_pct": round(distance_pct, 3),
                    })
    return alerts
