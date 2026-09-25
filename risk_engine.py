"""
risk_engine.py - position sizing and trade eligibility, independent of
the zone-detection logic in zone_core.py.

Spec requirements this implements:
    - quantity = f(capital, risk_pct)
    - minimum acceptable reward:risk is 1:3 (spec: "target price कम से कम RR 1:3")
      even if a zone's own computed RR (zone_core's targetRR, default 5.0)
      is higher - MIN_RR is a hard floor, not a target.
    - a trade must never be marked eligible with a computed quantity of 0
      or a non-finite risk-per-share (division-by-zero guard)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


MIN_ACCEPTABLE_RR = 3.0  # hard floor per spec: "कम से कम RR 1:3"


@dataclass
class RiskConfig:
    capital: float = 25000.0
    risk_pct: float = 0.5          # percent of capital risked per trade
    lot_size: int = 1              # >1 for F&O contracts; 1 for cash equity
    min_rr: float = MIN_ACCEPTABLE_RR
    max_qty: Optional[int] = None  # optional hard cap (e.g. exchange/broker limit)

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.risk_pct <= 0:
            raise ValueError("risk_pct must be positive")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")


@dataclass
class PositionResult:
    entry: float
    stop_loss: float
    target: float
    quantity: int
    risk_per_share: float
    risk_amount: float
    reward_per_share: float
    reward_amount: float
    actual_rr: float
    is_eligible: bool
    rejection_reason: str  # "" when is_eligible is True


class RiskEngine:
    def __init__(self, config: RiskConfig):
        self.config = config

    def calculate_position(
        self,
        entry: float,
        sl: float,
        tp: float,
        rr: Optional[float] = None,
    ) -> PositionResult:
        """rr is informational (the RR the zone was built for); actual
        eligibility is always checked against config.min_rr as the hard
        floor, per spec, regardless of what rr the caller passes in."""
        risk_per_share = abs(entry - sl)
        reward_per_share = abs(tp - entry)

        if risk_per_share <= 0 or not math.isfinite(risk_per_share):
            return PositionResult(
                entry=entry, stop_loss=sl, target=tp, quantity=0,
                risk_per_share=risk_per_share, risk_amount=0.0,
                reward_per_share=reward_per_share, reward_amount=0.0,
                actual_rr=0.0, is_eligible=False,
                rejection_reason="risk_per_share शून्य या अमान्य है (entry == stop loss)",
            )

        actual_rr = reward_per_share / risk_per_share

        risk_amount_budget = self.config.capital * (self.config.risk_pct / 100.0)
        raw_qty = risk_amount_budget / risk_per_share

        # Round down to a whole number of lots (lot_size=1 for cash equity).
        lots = math.floor(raw_qty / self.config.lot_size)
        quantity = lots * self.config.lot_size

        if self.config.max_qty is not None:
            quantity = min(quantity, self.config.max_qty)

        risk_amount = quantity * risk_per_share
        reward_amount = quantity * reward_per_share

        rejection_reason = ""
        is_eligible = True

        if quantity < self.config.lot_size:
            is_eligible = False
            rejection_reason = (
                f"तय किए गए risk_pct ({self.config.risk_pct}%) और capital "
                f"(₹{self.config.capital:,.0f}) से 1 भी {('lot' if self.config.lot_size>1 else 'share')} "
                f"नहीं बन रहा - इस stop-loss distance के लिए capital अपर्याप्त है"
            )
        elif actual_rr < self.config.min_rr:
            is_eligible = False
            rejection_reason = (
                f"RR {actual_rr:.2f} न्यूनतम स्वीकार्य RR {self.config.min_rr:.1f} से कम है"
            )

        return PositionResult(
            entry=entry, stop_loss=sl, target=tp, quantity=quantity,
            risk_per_share=round(risk_per_share, 4),
            risk_amount=round(risk_amount, 2),
            reward_per_share=round(reward_per_share, 4),
            reward_amount=round(reward_amount, 2),
            actual_rr=round(actual_rr, 2),
            is_eligible=is_eligible,
            rejection_reason=rejection_reason,
        )


def get_risk_engine(config: Optional[RiskConfig] = None) -> RiskEngine:
    return RiskEngine(config or RiskConfig())
