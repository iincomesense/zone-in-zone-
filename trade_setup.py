"""
trade_setup.py - turns a zone_core.Zone into the concrete, displayable
trade row the dashboard needs: entry, stop-loss (with buffer, already
baked into zone.slVal by zone_core), quantity (from risk_engine), and
target (minimum RR 1:3 enforced by risk_engine.MIN_ACCEPTABLE_RR).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from zone_core import Zone
from risk_engine import RiskConfig, get_risk_engine


def get_base_candle_close(df: pd.DataFrame, zone: Zone) -> float:
    """Close of the last base candle before leg-out - an alternative,
    more conservative entry reference than the raw proximal line."""
    idx = zone.startBarIndex + zone.baseCount - 1
    if 0 <= idx < len(df):
        return float(df.iloc[idx]["close"])
    return float(zone.proxVal)


def get_trade_setup(
    df: pd.DataFrame,
    zone: Zone,
    capital: float = 25000.0,
    risk_pct: float = 0.5,
    rr: float = 5.0,
    use_base_close_logic: bool = False,
    lot_size: int = 1,
) -> Dict[str, Any]:
    """Returns a fully-formed trade-row dict for one validated zone."""
    config = RiskConfig(capital=capital, risk_pct=risk_pct, lot_size=lot_size)
    engine = get_risk_engine(config)

    entry = zone.proxVal if not use_base_close_logic else get_base_candle_close(df, zone)
    sl = zone.slVal
    risk = abs(entry - sl)
    # Target is rebuilt around the (possibly base-close-adjusted) entry,
    # not just copied from zone.tpVal, so RR stays consistent with the
    # actual entry reference chosen.
    tp = entry + risk * rr if zone.isDemand else entry - risk * rr

    position = engine.calculate_position(entry, sl, tp, rr=rr)

    return {
        "isDemand": zone.isDemand,
        "patternType": zone.patternType,
        "zoneCategory": zone.zoneCategory,
        "state": zone.state,
        "entry": position.entry,
        "stopLoss": position.stop_loss,
        "target": position.target,
        "quantity": position.quantity,
        "risk_per_share": position.risk_per_share,
        "risk_amount": position.risk_amount,
        "reward_per_share": position.reward_per_share,
        "reward_amount": position.reward_amount,
        "rr": position.actual_rr,
        "capital": capital,
        "risk_pct": risk_pct,
        "proxVal": round(zone.proxVal, 2),
        "distVal": round(zone.distVal, 2),
        "base_close": round(get_base_candle_close(df, zone), 2),
        "timestamp": str(zone.timestamp) if zone.timestamp else "",
        "is_eligible": position.is_eligible,
        "rejection_reason": position.rejection_reason,
    }


def get_all_trade_setups(
    df: pd.DataFrame,
    zones: List[Zone],
    capital: float = 25000.0,
    risk_pct: float = 0.5,
    rr: float = 5.0,
    use_base_close_logic: bool = False,
    lot_size: int = 1,
    eligible_only: bool = False,
) -> List[Dict[str, Any]]:
    setups = [
        get_trade_setup(
            df, z, capital=capital, risk_pct=risk_pct, rr=rr,
            use_base_close_logic=use_base_close_logic, lot_size=lot_size,
        )
        for z in zones
    ]
    if eligible_only:
        setups = [s for s in setups if s["is_eligible"]]
    return setups
