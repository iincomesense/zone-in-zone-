from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Structural check only: no density/HQ scoring.
BASE_BORING_MAX_BODY_PCT = 0.55

PINE_DEFAULTS: Dict[str, Any] = {
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
    "legOutToLegInBodyMult": 1.0,
    "legInMinBodyPct": 0.55,
    "useImbalance": True,
    "maxImbalanceMult": 1.0,
    "relaxGapCapOvernight": True,
    "rejectOppositeCoverPct": 0.50,
    "testedLegOutRetracePct": 1.00,
    "maxTestedCount": 1,
}

DEFAULT_PARAMS = dict(PINE_DEFAULTS)


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


def settings(accountCapital: Optional[float] = None, **overrides: Any) -> Dict[str, Any]:
    config = dict(PINE_DEFAULTS)
    if accountCapital is not None:
        overrides["accountCapital"] = accountCapital
    config.update({k: v for k, v in overrides.items() if k in config})

    integer_keys = {
        "atrPeriod", "volSmaPeriod", "minBaseCountInput",
        "maxBaseCountInput", "maxTestedCount"
    }
    boolean_keys = {"useImbalance", "relaxGapCapOvernight"}

    for key, value in list(config.items()):
        if key in boolean_keys:
            config[key] = bool(value)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be numeric") from exc
        if not np.isfinite(number):
            raise ValueError(f"{key} must be finite")

        if key in integer_keys:
            config[key] = int(round(number))
        else:
            config[key] = number

    if config["accountCapital"] <= 0:
        raise ValueError("accountCapital must be positive")
    if config["targetRR"] <= 0:
        raise ValueError("targetRR must be positive")
    config["minBaseCountInput"] = max(1, min(int(config["minBaseCountInput"]), 3))
    config["maxBaseCountInput"] = max(config["minBaseCountInput"], min(int(config["maxBaseCountInput"]), 3))

    return config


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Soft normalization for compatibility with Yahoo Finance and resampled frames."""
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(c).strip().lower() for c in frame.columns]

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"Missing required OHLC columns: {missing}")

    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["volume"] = frame["volume"].fillna(0.0)

    # Clean non-finite values safely
    for col in required + ["volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)

    return frame


class ZoneEngine:
    def __init__(self, df: pd.DataFrame, **kwargs: Any):
        self.df = normalize_ohlcv(df)
        self.config = settings(**kwargs)
        for key, value in self.config.items():
            setattr(self, key, value)

        self.minBaseCount = self.minBaseCountInput
        self.maxBaseCount = self.maxBaseCountInput
        self.open = self.df["open"].to_numpy(dtype=float)
        self.high = self.df["high"].to_numpy(dtype=float)
        self.low = self.df["low"].to_numpy(dtype=float)
        self.close = self.df["close"].to_numpy(dtype=float)
        self.volume = self.df["volume"].to_numpy(dtype=float)
        self.n = len(self.df)

        self.active_zones: List[Zone] = []
        self.live_zones: List[Zone] = []
        self._prepare_indicators()

    @staticmethod
    def _rma(series: np.ndarray, length: int) -> np.ndarray:
        result = np.full(len(series), np.nan)
        if len(series) >= length:
            result[length - 1] = np.mean(series[:length])
            for i in range(length, len(series)):
                result[i] = result[i - 1] + (series[i] - result[i - 1]) / length
        return result

    def _prepare_indicators(self) -> None:
        if self.n > 0:
            previous = np.r_[self.close[0], self.close[:-1]]
            self.current_tr = np.maximum(
                self.high - self.low,
                np.maximum(abs(self.high - previous), abs(self.low - previous)),
            )
            self.current_tr[0] = self.high[0] - self.low[0]
        else:
            self.current_tr = np.empty(0)
        self.atr_val = self._rma(self.current_tr, self.atrPeriod)
        vol_s = pd.Series(self.volume)
        self.vol_sma = vol_s.rolling(self.volSmaPeriod, min_periods=1).mean().to_numpy()

    def _body_pct(self, pos: int) -> float:
        size = self.high[pos] - self.low[pos]
        return abs(self.close[pos] - self.open[pos]) / size if size > 0 else 0.0

    def _vol_x(self, pos: int) -> float:
        avg = self.vol_sma[pos]
        return float(self.volume[pos] / avg) if np.isfinite(avg) and avg > 0 else float("nan")

    def _is_overnight_gap(self, i: int) -> bool:
        if i <= 0:
            return False
        try:
            return self.df.index[i].date() != self.df.index[i - 1].date()
        except AttributeError:
            return False

    def _scan_bar(self, i: int) -> None:
        for count in range(self.minBaseCount, self.maxBaseCount + 1):
            lin, prev = i - count - 1, i - count - 2
            if prev < 0 or np.isnan(self.atr_val[lin]):
                continue

            lin_tr = self.current_tr[lin]
            lin_range = self.high[lin] - self.low[lin]
            lin_bull = self.close[lin] > self.open[lin]
            lin_bear = self.close[lin] < self.open[lin]

            if lin_range == 0 or self._body_pct(lin) < self.legInMinBodyPct:
                continue

            opposite = (
                (lin_bull and self.close[prev] < self.open[prev])
                or (lin_bear and self.close[prev] > self.open[prev])
            )
            if opposite:
                prev_hi = max(self.open[prev], self.close[prev])
                prev_lo = min(self.open[prev], self.close[prev])
                overlap = max(0.0, min(prev_hi, self.high[lin]) - max(prev_lo, self.low[lin]))
                if overlap / lin_range >= self.rejectOppositeCoverPct:
                    continue

            base = slice(i - count, i)
            base_tr = self.current_tr[base]
            base_body = abs(self.close[base] - self.open[base])
            base_atr = self.atr_val[base]

            if np.isnan(base_atr).any() or (base_tr > self.maxBaseAtrMult * base_atr).any():
                continue

            max_tr, max_body = float(max(base_tr)), float(max(base_body))
            body_ratios = np.divide(base_body, base_tr, out=np.zeros_like(base_body), where=base_tr > 0)
            if max_tr == 0 or (body_ratios > BASE_BORING_MAX_BODY_PCT).any():
                continue

            base_hi, base_lo = float(max(self.high[base])), float(min(self.low[base]))
            size_mult = 1.5 if count == 1 else self.legInToBaseSizeMult
            if lin_tr < size_mult * max_tr or lin_tr < self.legInMinAtrMult * self.atr_val[lin]:
                continue

            out_tr = self.current_tr[i]
            out_hi, out_lo = self.high[i], self.low[i]
            out_open, out_close = self.open[i], self.close[i]
            demand, supply = out_close > out_open, out_close < out_open
            if not (demand or supply):
                continue

            if out_hi >= base_hi and out_lo <= base_lo:
                continue

            lin_body = abs(self.close[lin] - self.open[lin])
            out_body = abs(out_close - out_open)
            wick_pct = (
                ((out_hi - max(out_open, out_close)) + (min(out_open, out_close) - out_lo)) / (out_hi - out_lo)
                if (out_hi - out_lo) > 0 else 0.0
            )

            if not (
                out_tr > self.legOutTrMult * self.atr_val[i]
                and wick_pct <= self.maxWickPct
                and out_tr >= self.legOutMinTrRatio * lin_tr
                and max_tr < lin_tr < out_tr
                and max_body < lin_body
                and out_body > self.legOutToLegInBodyMult * lin_body
                and (out_close > base_hi if demand else out_close < base_lo)
                and (self.volume[i] <= 0 or self.volume[i] > self.volume[lin])
            ):
                continue

            genuine_gap, imbalance, gap_size = False, True, 0.0
            if self.useImbalance:
                genuine_gap = out_lo > base_hi if demand else out_hi < base_lo
                imbalance = genuine_gap or (out_close > self.high[lin] if demand else out_close < self.low[lin])
                gap_size = max(0.0, out_lo - base_hi if demand else base_lo - out_hi)

            if min(out_open, out_close) <= base_lo and max(out_open, out_close) >= base_hi and not genuine_gap:
                continue

            bull_clv = (self.close[lin] - self.low[lin]) / lin_range
            bear_clv = (self.high[lin] - self.close[lin]) / lin_range
            valid_lin = (lin_bull and bull_clv >= self.minClvPct) or (lin_bear and bear_clv >= self.minClvPct)
            if not imbalance or not valid_lin:
                continue

            pattern = ("RBR" if lin_bull else "DBR") if demand else ("RBD" if lin_bull else "DBD")
            prox, dist = (base_hi, base_lo) if demand else (base_lo, base_hi)
            sl = dist - self.slBufferAtr * self.atr_val[i] if demand else dist + self.slBufferAtr * self.atr_val[i]
            risk = abs(prox - sl)
            tp = prox + risk * self.targetRR if demand else prox - risk * self.targetRR

            if any(
                z.isDemand == demand and abs(z.proxVal - prox) < self.atr_val[i] * 0.25
                for z in self.live_zones[-11:]
            ):
                break

            color = "green" if demand else "red"
            ts = self.df.index[i] if hasattr(self.df.index, "__getitem__") else None

            zone = Zone(
                proxVal=prox,
                distVal=dist,
                slVal=sl,
                tpVal=tp,
                isDemand=bool(demand),
                densityScore=0,
                isHQ=False,
                patternType=pattern,
                zoneCategory="Continuation" if pattern in ("RBR", "DBD") else "Reversal",
                state="Fresh",
                touchCount=0,
                startBarIndex=i - count,
                createdBarIndex=i,
                baseCount=count,
                legOutHigh=out_hi,
                legOutLow=out_lo,
                legOutMidLevel=(
                    out_hi - self.testedLegOutRetracePct * (out_hi - out_lo)
                    if demand
                    else out_lo + self.testedLegOutRetracePct * (out_hi - out_lo)
                ),
                isOvernight=self._is_overnight_gap(i),
                legInTR=lin_tr,
                legOutTR=out_tr,
                zoneBox=Box(i - count - 1, max(prox, dist), i + 15, min(prox, dist), color, (color, 0.15)),
                timestamp=ts,
                riskPct=risk / prox * 100 if prox else float("nan"),
                score10=0.0,
                baseColourOK=False,
                legInVolX=self._vol_x(lin),
                legOutVolX=self._vol_x(i),
                retestVolX=float("nan"),
                entryStatus="",
                entryPrice=0.0,
                gapToLegIn=gap_size,
            )
            self.active_zones.append(zone)
            self.live_zones.append(zone)
            break

    def _update_zone_states(self, i: int) -> None:
        for zone in self.live_zones[:]:
            if i <= zone.createdBarIndex:
                zone.zoneBox.set_right(i + 15)
                continue
            broken = self.low[i] <= zone.distVal if zone.isDemand else self.high[i] >= zone.distVal
            touched = self.low[i] <= zone.proxVal if zone.isDemand else self.high[i] >= zone.proxVal
            if broken:
                zone.state = "Broken"
            elif touched:
                zone.state = "Tested"
                zone.touchCount += 1
                if zone.touchCount > self.maxTestedCount:
                    zone.state = "Broken"

            if zone.state == "Broken":
                zone.zoneBox.set_bgcolor(("gray", 0.05))
                zone.zoneBox.set_border_color(("gray", 0.20))
                self.live_zones.remove(zone)
            else:
                zone.zoneBox.set_right(i + 15)

    def run(self) -> List[Zone]:
        self.active_zones, self.live_zones = [], []
        min_bars = max(self.atrPeriod, self.maxBaseCount + 3, 11)
        for i in range(min_bars, self.n):
            if i < self.n - 1 and not np.isnan(self.atr_val[i]):
                self._scan_bar(i)
            self._update_zone_states(i)
        return self.active_zones


def scan_zones(
    df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    accountCapital: Optional[float] = None,
) -> List[Zone]:
    incoming = dict(params or {})
    if accountCapital is not None:
        incoming["accountCapital"] = accountCapital
    return ZoneEngine(df, **settings(**incoming)).run()


def latest_active_zones(zones: List[Zone]) -> List[Zone]:
    return [z for z in zones if z.state in ("Fresh", "Tested")]


def get_zone_alerts(zones: List[Zone], price: float) -> List[Zone]:
    return [
        z for z in latest_active_zones(zones)
        if min(z.proxVal, z.distVal) <= price <= max(z.proxVal, z.distVal)
    ]


def recommended_trade_setup(accountCapital: Optional[float] = None) -> Dict[str, Any]:
    config = settings(accountCapital=accountCapital)
    return {
        "patterns": ["RBR", "DBR", "DBD", "RBD"],
        "targetRR": config["targetRR"],
        "risk_pct": config["riskPct"],
        "capital": config["accountCapital"],
        "slBufferAtr": config["slBufferAtr"],
        "entry_mode": "prox",
    }


def backtest_summary(zones: List[Zone], df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "n_zones": len(zones),
        "n_active": len(latest_active_zones(zones)),
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
    return {
        "implemented": False,
        "n_trades": None,
        "win_pct": None,
        "net_roi_pct": None,
        "sample_zones": sum(not patterns or z.patternType in patterns for z in zones),
        "risk_pct": risk_pct,
        "capital": capital,
        "targetRR": rr,
        "message": "Trade backtest is not implemented.",
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
        **dict.fromkeys("ABCDEF"),
    }


def resample_nse_session(
    df: pd.DataFrame,
    n_hours: int,
    session_start: str = "09:15",
    session_end: str = "15:30",
) -> pd.DataFrame:
    """Safe NSE session resampling compatible with legacy calls."""
    frame = normalize_ohlcv(df)
    if not isinstance(frame.index, pd.DatetimeIndex):
        return frame

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    pieces = []
    for _, day_df in frame.groupby(frame.index.date):
        day_df = day_df.between_time(session_start, session_end)
        if day_df.empty:
            continue
        resampled = day_df.resample(f"{int(n_hours)}h", origin="start", label="left", closed="left").agg(agg)
        pieces.append(resampled.dropna(subset=["open", "high", "low", "close"]))
    return pd.concat(pieces).sort_index() if pieces else frame.iloc[:0]
