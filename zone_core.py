from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# The complete density/HQ score calculation has intentionally been removed.
# All non-scoring zone-detection, state, risk and trade-level rules are kept.
# A base is deliberately required to be a boring/ranging candle. This is a
# hard structural check, not a score bonus and not an exposed setting.
BASE_BORING_MAX_BODY_PCT = 0.55


PINE_DEFAULTS: Dict[str, Any] = {
    # Explicitly changeable through settings(...), scan_zones(...), or
    # scan_zones(..., accountCapital=...).
    "accountCapital": 25000.0,
    "riskPct": 0.5,
    "targetRR": 5.0,
    "slBufferAtr": 0.1,
    "atrPeriod": 14,
    "volSmaPeriod": 20,
    "legOutTrMult": 1.2,
    "legOutMinTrRatio": 1.0,
    "maxBaseAtrMult": 1.0,
    "maxWickPct": 0.30,
    "minBaseCountInput": 1,
    "maxBaseCountInput": 3,
    "legInMinAtrMult": 1.0,
    "minClvPct": 0.60,
    "legInToBaseSizeMult": 2.0,
    # Wickless-body hierarchy: leg-out body must be strictly larger than
    # leg-in body by this multiplier. 1.0 means simply greater-than.
    "legOutToLegInBodyMult": 1.0,
    "legInMinBodyPct": 0.55,
    "useImbalance": True,
    "maxImbalanceMult": 1.0,
    "relaxGapCapOvernight": True,
    "rejectOppositeCoverPct": 0.50,
    "testedLegOutRetracePct": 1.00,
    # Keep a zone after only its first retest. The second proximal touch
    # makes it Broken/removed.
    "maxTestedCount": 1,
}


# -----------------------------------------------------------------------------
# Data objects
# -----------------------------------------------------------------------------
@dataclass
class Box:
    left: int
    top: float
    right: int
    bottom: float
    border_color: object
    bgcolor: object

    def set_right(self, right: int) -> None:
        self.right = right

    def set_bgcolor(self, color: object) -> None:
        self.bgcolor = color

    def set_border_color(self, color: object) -> None:
        self.border_color = color


@dataclass
class Zone:
    proxVal: float
    distVal: float
    slVal: float
    tpVal: float
    isDemand: bool
    # Compatibility fields for the existing Streamlit caller. They are numeric
    # neutral values only; no score is calculated and no HQ threshold is applied.
    densityScore: int
    isHQ: bool
    patternType: str
    zoneCategory: str
    state: str
    touchCount: int
    startBarIndex: int
    createdBarIndex: int
    baseCount: int
    legOutHigh: float
    legOutLow: float
    legOutMidLevel: float
    isOvernight: bool
    legInTR: float
    legOutTR: float
    zoneBox: Box
    timestamp: object = None
    riskPct: float = float("nan")
    score10: float = 0.0
    baseColourOK: bool = False
    legInVolX: float = float("nan")
    legOutVolX: float = float("nan")
    retestVolX: float = float("nan")
    entryStatus: str = ""
    entryPrice: float = 0.0
    gapToLegIn: float = 0.0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _positive_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc

    if not np.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


# -----------------------------------------------------------------------------
# Zone engine
# -----------------------------------------------------------------------------
class ZoneEngine:
    def __init__(self, df: pd.DataFrame, **kwargs: Any):
        self.df = df.copy()

        # Volume is optional. The original permissive behaviour is preserved.
        if "volume" not in self.df.columns:
            self.df["volume"] = 0.0
        self.df["volume"] = self.df["volume"].fillna(0.0)

        for key, default in PINE_DEFAULTS.items():
            setattr(self, key, kwargs.get(key, default))

        self.accountCapital = _positive_float(
            self.accountCapital, "accountCapital"
        )
        self.riskPct = float(self.riskPct)
        if not np.isfinite(self.riskPct) or self.riskPct < 0:
            raise ValueError("riskPct must be a finite non-negative number")

        hard_max_base_count = 3
        self.minBaseCount = max(
            1, min(self.minBaseCountInput, self.maxBaseCountInput)
        )
        self.maxBaseCount = min(self.maxBaseCountInput, hard_max_base_count)

        self.open = self.df["open"].to_numpy(dtype=float)
        self.high = self.df["high"].to_numpy(dtype=float)
        self.low = self.df["low"].to_numpy(dtype=float)
        self.close = self.df["close"].to_numpy(dtype=float)
        self.volume = self.df["volume"].to_numpy(dtype=float)
        self.n = len(self.df)
        self.dayofweek = self.df.index.dayofweek.to_numpy()
        self.time_ms = self.df.index.astype(np.int64) // 10**6

        self.active_zones: List[Zone] = []
        self.live_zones: List[Zone] = []
        self._prepare_indicators()

    def _rma(self, series: np.ndarray, length: int) -> np.ndarray:
        n = len(series)
        result = np.full(n, np.nan)
        if n < length:
            return result

        result[length - 1] = np.mean(series[:length])
        for i in range(length, n):
            result[i] = (series[i] - result[i - 1]) / length + result[i - 1]
        return result

    def _prepare_indicators(self) -> None:
        if self.n > 0:
            previous_close = np.empty(self.n)
            previous_close[0] = self.close[0]
            previous_close[1:] = self.close[:-1]

            tr = np.maximum(
                self.high - self.low,
                np.maximum(
                    np.abs(self.high - previous_close),
                    np.abs(self.low - previous_close),
                ),
            )
            tr[0] = self.high[0] - self.low[0]
            self.current_tr = tr
        else:
            self.current_tr = np.empty(0)

        self.atr_val = self._rma(self.current_tr, self.atrPeriod)
        self.vol_sma = (
            self.df["volume"].rolling(self.volSmaPeriod).mean().to_numpy()
        )

    def _tr(self, i: int, idx: int) -> float:
        pos = i - idx
        return (
            float(self.current_tr[pos])
            if 0 <= pos < self.n
            else float("nan")
        )

    def _is_bull(self, i: int, idx: int) -> bool:
        pos = i - idx
        return bool(self.close[pos] > self.open[pos])

    def _is_bear(self, i: int, idx: int) -> bool:
        pos = i - idx
        return bool(self.open[pos] > self.close[pos])

    def _body_high_low(self, i: int, idx: int) -> tuple[float, float]:
        pos = i - idx
        return max(self.open[pos], self.close[pos]), min(
            self.open[pos], self.close[pos]
        )

    def _wick_pct(self, i: int, idx: int) -> float:
        pos = i - idx
        candle_range = self.high[pos] - self.low[pos]
        if candle_range == 0:
            return 0.0

        return (
            (self.high[pos] - max(self.open[pos], self.close[pos]))
            + (min(self.open[pos], self.close[pos]) - self.low[pos])
        ) / candle_range

    def _body_pct(self, i: int, idx: int) -> float:
        pos = i - idx
        candle_range = self.high[pos] - self.low[pos]
        if candle_range == 0:
            return 0.0
        return abs(self.close[pos] - self.open[pos]) / candle_range

    def _is_overnight_gap(self, i: int) -> bool:
        if i == 0:
            return False
        return bool(
            self.dayofweek[i] != self.dayofweek[i - 1]
            or (self.time_ms[i] - self.time_ms[i - 1]) > 86400000
        )

    def _scan_bar(self, i: int) -> None:
        zone_found_on_this_bar = False

        for b_count in range(self.minBaseCount, self.maxBaseCount + 1):
            if zone_found_on_this_bar:
                break

            leg_out_idx = 0
            leg_in_idx = b_count + 1
            prev_idx = b_count + 2

            pos_leg_in = i - leg_in_idx
            if pos_leg_in < 0 or np.isnan(self.atr_val[pos_leg_in]):
                continue

            leg_in_tr = self._tr(i, leg_in_idx)
            leg_in_low = self.low[pos_leg_in]
            leg_in_high = self.high[pos_leg_in]
            leg_in_close = self.close[pos_leg_in]
            leg_in_vol = self.volume[pos_leg_in]
            leg_in_range = leg_in_high - leg_in_low
            leg_in_is_bull = self._is_bull(i, leg_in_idx)
            leg_in_is_bear = self._is_bear(i, leg_in_idx)

            if (
                leg_in_range == 0
                or self._body_pct(i, leg_in_idx) < self.legInMinBodyPct
            ):
                continue

            pos_prev = i - prev_idx
            if pos_prev < 0:
                continue

            if (
                leg_in_is_bull and self._is_bear(i, prev_idx)
            ) or (
                leg_in_is_bear and self._is_bull(i, prev_idx)
            ):
                prev_body_high, prev_body_low = self._body_high_low(i, prev_idx)
                overlap = max(
                    0.0,
                    min(prev_body_high, leg_in_high)
                    - max(prev_body_low, leg_in_low),
                )
                if overlap / leg_in_range >= self.rejectOppositeCoverPct:
                    continue

            bull_clv = (leg_in_close - leg_in_low) / leg_in_range
            bear_clv = (leg_in_high - leg_in_close) / leg_in_range

            all_base_valid = True
            all_base_boring = True
            max_base_tr = 0.0
            max_base_body = 0.0
            max_base_high = -1.0
            min_base_low = 1_000_000_000.0

            for b in range(1, b_count + 1):
                pos_b = i - b
                if pos_b < 0 or np.isnan(self.atr_val[pos_b]):
                    all_base_valid = False
                    break

                base_tr = self._tr(i, b)
                if base_tr > self.maxBaseAtrMult * self.atr_val[pos_b]:
                    all_base_valid = False
                    break

                base_body_pct = (
                    abs(self.close[pos_b] - self.open[pos_b]) / base_tr
                    if base_tr > 0
                    else 0.0
                )
                if base_body_pct > BASE_BORING_MAX_BODY_PCT:
                    all_base_boring = False

                max_base_tr = max(max_base_tr, base_tr)
                max_base_body = max(
                    max_base_body,
                    abs(self.close[pos_b] - self.open[pos_b]),
                )
                max_base_high = max(max_base_high, self.high[pos_b])
                min_base_low = min(min_base_low, self.low[pos_b])

            if not all_base_valid or max_base_tr == 0:
                continue

            effective_base_size_mult = (
                1.5 if b_count == 1 else self.legInToBaseSizeMult
            )
            if (
                leg_in_tr < effective_base_size_mult * max_base_tr
                or leg_in_tr < self.legInMinAtrMult * self.atr_val[pos_leg_in]
            ):
                continue

            pos_leg_out = i - leg_out_idx
            leg_out_tr = self._tr(i, leg_out_idx)
            leg_out_high = self.high[pos_leg_out]
            leg_out_low = self.low[pos_leg_out]
            leg_out_close = self.close[pos_leg_out]
            leg_out_open = self.open[pos_leg_out]
            leg_out_vol = self.volume[pos_leg_out]
            leg_in_body_size = abs(leg_in_close - self.open[pos_leg_in])
            leg_out_body_size = abs(leg_out_close - leg_out_open)
            is_demand_leg_out = self._is_bull(i, leg_out_idx)
            is_supply_leg_out = self._is_bear(i, leg_out_idx)

            if not (is_demand_leg_out or is_supply_leg_out):
                continue

            # Keep the supplied full-engulf rejection rule unchanged.
            leg_out_fully_engulfs_base = (
                leg_out_high >= max_base_high
                and leg_out_low <= min_base_low
            )
            if leg_out_fully_engulfs_base:
                continue

            # Strict candle-size hierarchy: a valid setup must contain a
            # real base, then a larger leg-in, then a still larger leg-out.
            # This is an additional hard rejection only; all other rules stay
            # unchanged.
            is_leg_out_explosive = (
                leg_out_tr > self.legOutTrMult * self.atr_val[pos_leg_out]
            )
            is_leg_out_wick_valid = (
                self._wick_pct(i, leg_out_idx) <= self.maxWickPct
            )
            passes_tr_hierarchy = (
                leg_out_tr >= self.legOutMinTrRatio * leg_in_tr
                and leg_in_tr > max_base_tr
            )
            passes_strict_candle_hierarchy = (
                max_base_tr < leg_in_tr < leg_out_tr
            )
            # Also require the visible candle bodies to follow the same
            # hierarchy. This prevents a large-wick candle from being
            # incorrectly treated as a larger leg-out only because its TR is
            # large.
            passes_strict_body_hierarchy = (
                max_base_body < leg_in_body_size
                and leg_out_body_size
                > self.legOutToLegInBodyMult * leg_in_body_size
            )

            # A leg-out is complete only when its close has broken the base
            # edge. A close merely beyond leg-in is not enough to confirm the
            # base-to-leg-out transition.
            passes_leg_out_close_confirmation = (
                leg_out_close > max_base_high
                if is_demand_leg_out
                else leg_out_close < min_base_low
            )

            leg_out_volume_missing = (
                not np.isfinite(leg_out_vol) or leg_out_vol <= 0
            )
            passes_volume = leg_out_volume_missing or leg_out_vol > leg_in_vol

            is_overnight = self._is_overnight_gap(i)

            has_imbalance = True
            has_genuine_gap = False
            gap_size = 0.0
            if self.useImbalance:
                if is_demand_leg_out:
                    has_genuine_gap = leg_out_low > max_base_high
                    has_imbalance = has_genuine_gap or (
                        leg_out_close > leg_in_high
                    )
                    gap_size = max(0.0, leg_out_low - max_base_high)
                elif is_supply_leg_out:
                    has_genuine_gap = leg_out_high < min_base_low
                    has_imbalance = has_genuine_gap or (
                        leg_out_close < leg_in_low
                    )
                    gap_size = max(0.0, min_base_low - leg_out_high)

            leg_out_body_high = max(leg_out_open, leg_out_close)
            leg_out_body_low = min(leg_out_open, leg_out_close)
            if (
                leg_out_body_low <= min_base_low
                and leg_out_body_high >= max_base_high
                and not has_genuine_gap
            ):
                continue

            is_rbr = (
                leg_in_is_bull
                and bull_clv >= self.minClvPct
                and is_demand_leg_out
            )
            is_dbr = (
                leg_in_is_bear
                and bear_clv >= self.minClvPct
                and is_demand_leg_out
            )
            is_dbd = (
                leg_in_is_bear
                and bear_clv >= self.minClvPct
                and is_supply_leg_out
            )
            is_rbd = (
                leg_in_is_bull
                and bull_clv >= self.minClvPct
                and is_supply_leg_out
            )

            # This is now the final validity gate. No score or HQ score
            # threshold is applied after this point.
            if not (
                (is_rbr or is_dbr or is_dbd or is_rbd)
                and is_leg_out_explosive
                and is_leg_out_wick_valid
                and passes_tr_hierarchy
                and all_base_boring
                and passes_strict_candle_hierarchy
                and passes_strict_body_hierarchy
                and passes_leg_out_close_confirmation
                and passes_volume
                and has_imbalance
            ):
                continue

            prox_val = max_base_high if is_demand_leg_out else min_base_low
            dist_val = min_base_low if is_demand_leg_out else max_base_high
            sl_val = (
                dist_val - self.slBufferAtr * self.atr_val[i]
                if is_demand_leg_out
                else dist_val + self.slBufferAtr * self.atr_val[i]
            )
            risk_per_share = abs(prox_val - sl_val)
            tp_val = (
                prox_val + risk_per_share * self.targetRR
                if is_demand_leg_out
                else prox_val - risk_per_share * self.targetRR
            )
            leg_out_mid_level = (
                leg_out_high
                - self.testedLegOutRetracePct * (leg_out_high - leg_out_low)
                if is_demand_leg_out
                else leg_out_low
                + self.testedLegOutRetracePct * (leg_out_high - leg_out_low)
            )

            # Preserve the original one-zone-per-bar behaviour. In the old
            # version this flag was set immediately before duplicate checking.
            zone_found_on_this_bar = True

            # Duplicate check remains unchanged.
            is_duplicate = False
            checked = 0
            for check_zone in reversed(self.live_zones):
                if (
                    check_zone.isDemand == is_demand_leg_out
                    and abs(check_zone.proxVal - prox_val)
                    < self.atr_val[i] * 0.25
                ):
                    is_duplicate = True
                    break
                checked += 1
                if checked >= 11:
                    break
            if is_duplicate:
                continue

            box_border_color, box_fill_color = (
                ("green", ("green", 0.15))
                if is_demand_leg_out
                else ("red", ("red", 0.15))
            )

            risk_pct_of_price = (
                risk_per_share / prox_val * 100.0 if prox_val else float("nan")
            )
            new_zone = Zone(
                proxVal=prox_val,
                distVal=dist_val,
                slVal=sl_val,
                tpVal=tp_val,
                isDemand=is_demand_leg_out,
                # Scoring was removed; numeric neutral values prevent legacy
                # sort/render code from applying unary minus to None.
                densityScore=0,
                isHQ=False,
                patternType=(
                    "RBR"
                    if is_rbr
                    else "DBR"
                    if is_dbr
                    else "DBD"
                    if is_dbd
                    else "RBD"
                ),
                zoneCategory=(
                    "Continuation" if (is_rbr or is_dbd) else "Reversal"
                ),
                state="Fresh",
                touchCount=0,
                startBarIndex=i - b_count,
                createdBarIndex=i,
                baseCount=b_count,
                legOutHigh=leg_out_high,
                legOutLow=leg_out_low,
                legOutMidLevel=leg_out_mid_level,
                isOvernight=is_overnight,
                legInTR=leg_in_tr,
                legOutTR=leg_out_tr,
                zoneBox=Box(
                    left=i - b_count - 1,
                    top=prox_val,
                    right=i + 15,
                    bottom=dist_val,
                    border_color=box_border_color,
                    bgcolor=box_fill_color,
                ),
                timestamp=self.df.index[i],
                riskPct=risk_pct_of_price,
                score10=0.0,
                baseColourOK=False,
                legInVolX=(
                    leg_in_vol / self.vol_sma[pos_leg_in]
                    if self.vol_sma[pos_leg_in]
                    and not np.isnan(self.vol_sma[pos_leg_in])
                    else float("nan")
                ),
                legOutVolX=(
                    leg_out_vol / self.vol_sma[pos_leg_out]
                    if self.vol_sma[pos_leg_out]
                    and not np.isnan(self.vol_sma[pos_leg_out])
                    else float("nan")
                ),
                gapToLegIn=gap_size,
            )

            self.active_zones.append(new_zone)
            self.live_zones.append(new_zone)

    def _update_zone_states(self, i: int) -> None:
        if not self.live_zones:
            return

        lo_t, hi_t = self.low[i], self.high[i]

        for k in range(len(self.live_zones) - 1, -1, -1):
            zone = self.live_zones[k]

            # A zone cannot be Tested by the same candle that created it.
            # The leg-out candle is the formation candle, not a retest.
            if i <= zone.createdBarIndex:
                zone.zoneBox.set_right(i + 15)
                continue

            if zone.state == "Fresh":
                if zone.isDemand:
                    if lo_t <= zone.distVal:
                        zone.state = "Broken"
                    elif lo_t <= zone.proxVal:
                        zone.state = "Tested"
                        zone.touchCount += 1
                else:
                    if hi_t >= zone.distVal:
                        zone.state = "Broken"
                    elif hi_t >= zone.proxVal:
                        zone.state = "Tested"
                        zone.touchCount += 1

            elif zone.state == "Tested":
                if zone.isDemand:
                    if lo_t <= zone.distVal:
                        zone.state = "Broken"
                    elif lo_t <= zone.proxVal:
                        zone.touchCount += 1
                else:
                    if hi_t >= zone.distVal:
                        zone.state = "Broken"
                    elif hi_t >= zone.proxVal:
                        zone.touchCount += 1

            if (
                zone.state == "Tested"
                and zone.touchCount > self.maxTestedCount
            ):
                zone.state = "Broken"

            if zone.state == "Broken":
                zone.zoneBox.set_bgcolor(("gray", 0.05))
                zone.zoneBox.set_border_color(("gray", 0.20))
                self.live_zones.pop(k)
            else:
                zone.zoneBox.set_right(i + 15)

    def run(self) -> List[Zone]:
        min_bar = max(self.atrPeriod, self.maxBaseCount + 3, 11)
        for i in range(min_bar, self.n):
            # Do not create a zone on the last available candle. In live data
            # that candle may still be forming, so its leg-in/base/leg-out
            # structure is not confirmed yet. Existing zones are still
            # allowed to receive a retest/break update on that candle.
            if i < self.n - 1 and not np.isnan(self.atr_val[i]):
                self._scan_bar(i)
            self._update_zone_states(i)
        return self.active_zones


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def settings(
    accountCapital: Optional[float] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Return configuration with an explicitly changeable account capital.

    Examples:
        settings(accountCapital=100000)
        settings(accountCapital=100000, targetRR=3.0)
    """
    result = dict(PINE_DEFAULTS)

    if accountCapital is not None:
        overrides["accountCapital"] = accountCapital

    # Unknown legacy keys are ignored, as in the original public API.
    for key, value in overrides.items():
        if key in PINE_DEFAULTS:
            result[key] = value

    result["accountCapital"] = _positive_float(
        result["accountCapital"], "accountCapital"
    )
    return result


def scan_zones(
    df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    accountCapital: Optional[float] = None,
) -> List[Zone]:
    """Scan zones.

    accountCapital can be changed either by params or by the explicit keyword:

        scan_zones(df, accountCapital=100000)
        scan_zones(df, {"accountCapital": 100000})
    """
    incoming = dict(params or {})
    if accountCapital is not None:
        incoming["accountCapital"] = accountCapital

    config = settings(**incoming)
    engine_config = {
        key: value for key, value in config.items() if key in PINE_DEFAULTS
    }
    return ZoneEngine(df, **engine_config).run()


def recommended_trade_setup(
    accountCapital: Optional[float] = None,
) -> Dict[str, Any]:
    config = settings(accountCapital=accountCapital)
    return {
        "patterns": ["RBR", "DBR", "DBD", "RBD"],
        "targetRR": config["targetRR"],
        "risk_pct": config["riskPct"],
        "capital": config["accountCapital"],
        "slBufferAtr": config["slBufferAtr"],
        "entry_mode": "prox",
    }


def backtest_summary(
    zones: List[Zone],
    df: pd.DataFrame,
) -> Dict[str, Any]:
    active = [z for z in zones if z.state in ("Fresh", "Tested")]
    return {
        "n_zones": len(zones),
        "n_active": len(active),
        "n_broken": sum(z.state == "Broken" for z in zones),
    }


def realistic_roi(
    zones: List[Zone],
    df: pd.DataFrame,
    rr: float = 5.0,
    risk_pct: float = 0.5,
    capital: float = 25000.0,
    patterns: Optional[List[str]] = None,
    buffer: float = 0.1,
    entry_mode: str = "prox",
    max_hold: int = 40,
) -> Dict[str, Any]:
    selected = [
        z for z in zones if not patterns or z.patternType in patterns
    ]
    return {
        "n_trades": 0,
        "win_pct": 0.0,
        "net_roi_pct": 0.0,
        "sample_zones": len(selected),
        "risk_pct": risk_pct,
        "capital": capital,
        "targetRR": rr,
    }


def target_context(
    zone: Zone,
    df: Optional[pd.DataFrame] = None,
    htf_df: Optional[pd.DataFrame] = None,
    market_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
    spx_ret20: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "score": None,
        "max": 6,
        "label": "—",
        "why": [],
        "A": None,
        "B": None,
        "C": None,
        "D": None,
        "E": None,
        "F": None,
    }


def latest_active_zones(zones: List[Zone]) -> List[Zone]:
    return [z for z in zones if z.state in ("Fresh", "Tested")]


def get_zone_alerts(zones: List[Zone], price: float) -> List[Zone]:
    return [
        z
        for z in latest_active_zones(zones)
        if min(z.proxVal, z.distVal)
        <= price
        <= max(z.proxVal, z.distVal)
    ]


def resample_nse_session(
    df: pd.DataFrame,
    n_hours: int,
    session_start: str = "09:15",
    session_end: str = "15:30",
) -> pd.DataFrame:
    df = df.sort_index().copy()
    out_frames = []

    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg_dict["volume"] = "sum"

    for _, day_df in df.groupby(df.index.date):
        day_df = day_df.between_time(session_start, session_end)
        if day_df.empty:
            continue

        agg = (
            day_df.resample(
                f"{n_hours}h",
                origin="start",
                label="left",
                closed="left",
            )
            .agg(agg_dict)
            .dropna(subset=["open"])
        )
        out_frames.append(agg)

    if out_frames:
        return pd.concat(out_frames).sort_index()
    return pd.DataFrame(columns=list(agg_dict.keys()))
