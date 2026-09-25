"""
nifty_context.py - implements the spec's index-conflict rule:
    "किसी instrument में demand zone ले रहे हैं तो Nifty 50 supply zone
     नहीं हो, और supply zone ले रहे हैं तो उसके आसपास Nifty 50 demand
     zone नहीं हो"

This runs the same zone_core/zone_validation engine on Nifty 50 candles
and checks whether an opposite-type Fresh/Tested zone sits within
`proximity_pct` of Nifty's current price - i.e. an active, nearby
conflicting zone, not just any zone anywhere on the chart.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from zone_core import Zone
from zone_validation import scan_and_validate_final


def get_nifty_validated_zones(nifty_df: pd.DataFrame, timeframe: str = "Daily") -> List[Zone]:
    """Thin wrapper so callers don't need to know the validation module's
    parent_tf plumbing - kept separate from the per-instrument scan so
    Nifty is scanned once and reused across every instrument's context
    check in the same cycle (see scheduler.py)."""
    return scan_and_validate_final(nifty_df, parent_tf=timeframe)


def check_nifty_conflict(
    nifty_zones: List[Zone],
    nifty_current_price: float,
    instrument_is_demand: bool,
    proximity_pct: float = 1.0,
) -> Dict[str, Any]:
    """instrument_is_demand: True if the INSTRUMENT's zone (not Nifty's)
    is a demand zone. Per spec, that instrument zone is flagged when
    Nifty itself has an active SUPPLY zone nearby (opposite type), and
    vice-versa.

    proximity_pct: how close (as % of current Nifty price) a Nifty zone's
    proximal line must be to count as "nearby" / active right now.
    """
    if nifty_current_price is None or nifty_current_price <= 0:
        return {"checked": False, "conflict": None, "reason": "nifty_current_price unavailable"}

    # We're checking for a Nifty zone of the OPPOSITE type to the
    # instrument's zone, per the spec.
    opposite_is_demand = not instrument_is_demand

    active_opposite_zones = [
        z for z in nifty_zones
        if z.isDemand == opposite_is_demand and z.state in ("Fresh", "Tested")
    ]

    conflicts = []
    for z in active_opposite_zones:
        distance_pct = abs(z.proxVal - nifty_current_price) / nifty_current_price * 100.0
        if distance_pct <= proximity_pct:
            conflicts.append({
                "proxVal": round(z.proxVal, 2),
                "distVal": round(z.distVal, 2),
                "state": z.state,
                "patternType": z.patternType,
                "distance_pct": round(distance_pct, 3),
                "timestamp": str(z.timestamp) if z.timestamp else "",
            })

    return {
        "checked": True,
        "conflict": bool(conflicts),
        "nifty_current_price": nifty_current_price,
        "proximity_pct": proximity_pct,
        "conflicting_nifty_zone_type": "Supply" if opposite_is_demand is False else "Demand",
        "conflicting_zones": conflicts,
    }


def get_nifty_conflict_for_instrument(
    nifty_df: pd.DataFrame,
    nifty_current_price: float,
    instrument_is_demand: bool,
    timeframe: str = "Daily",
    proximity_pct: float = 1.0,
    precomputed_nifty_zones: Optional[List[Zone]] = None,
) -> Dict[str, Any]:
    """Convenience one-shot call for ad-hoc use. In the scheduler, prefer
    calling get_nifty_validated_zones() once per cycle and passing the
    result via precomputed_nifty_zones to every instrument's check,
    rather than re-scanning Nifty per instrument."""
    zones = precomputed_nifty_zones
    if zones is None:
        zones = get_nifty_validated_zones(nifty_df, timeframe=timeframe)
    return check_nifty_conflict(zones, nifty_current_price, instrument_is_demand, proximity_pct)
