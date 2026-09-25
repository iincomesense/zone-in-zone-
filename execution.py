"""
execution.py - what happens when a user (or, in algo mode, the system
itself) decides to act on a zone's trade setup. Three modes, three very
different risk profiles:

    paper  - no money moves. Just appended to data/trade_log.jsonl so the
             user can review hit-rate/behaviour before trusting the system.
    manual - also no money moves automatically. Records that the user
             says they took this trade manually (e.g. via their own
             broker app) - useful for journaling, not order routing.
    algo   - the only mode that can send a real order, and only when
             BOTH (a) dashboard trading_mode is "algo" AND (b) the caller
             passes confirm=True on this specific call. This double-gate
             (mode + per-call confirm) is deliberate: a bug that flips
             trading_mode to "algo" should still not be able to fire an
             order on its own.

Every recorded action - paper, manual, or algo - goes into the same
append-only log so nothing gets lost and the dashboard can show a single
unified trade history regardless of mode.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from providers import dhan_client
import dhan_mapping

TRADE_LOG_PATH = Path(__file__).parent / "data" / "trade_log.jsonl"
_log_lock = threading.Lock()


@dataclass
class ExecutionResult:
    ok: bool
    mode: str
    trade_id: str
    status: str          # "logged_paper" | "logged_manual" | "order_placed" | "rejected"
    message: str
    broker_response: Optional[Dict[str, Any]] = None


def _append_log(entry: Dict[str, Any]) -> None:
    with _log_lock:
        TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRADE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _new_trade_id() -> str:
    return uuid.uuid4().hex[:12]


def execute_paper(setup: Dict[str, Any]) -> ExecutionResult:
    trade_id = _new_trade_id()
    entry = {
        "trade_id": trade_id, "mode": "paper", "status": "logged_paper",
        "setup": setup, "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_log(entry)
    return ExecutionResult(
        ok=True, mode="paper", trade_id=trade_id, status="logged_paper",
        message="Paper trade log हो गई - कोई असली ऑर्डर नहीं गया।",
    )


def execute_manual(setup: Dict[str, Any], note: str = "") -> ExecutionResult:
    trade_id = _new_trade_id()
    entry = {
        "trade_id": trade_id, "mode": "manual", "status": "logged_manual",
        "setup": setup, "note": note, "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_log(entry)
    return ExecutionResult(
        ok=True, mode="manual", trade_id=trade_id, status="logged_manual",
        message="Manual ट्रेड journal में दर्ज हो गई। ऑर्डर आपने खुद अपने broker app से लिया माना गया है।",
    )


def execute_algo(
    setup: Dict[str, Any],
    dashboard_trading_mode: str,
    confirm: bool,
    product_type: str = "INTRADAY",
    order_type: str = "LIMIT",
) -> ExecutionResult:
    """The only path that can place a real Dhan order.

    Gate 1: dashboard_trading_mode must already be "algo" (set via the
            Settings page / API - a considered, sticky choice).
    Gate 2: confirm=True must be passed on THIS call - never defaulted,
            never inferred, so nothing can fire an order as a side effect.
    """
    trade_id = _new_trade_id()

    if dashboard_trading_mode != "algo":
        return ExecutionResult(
            ok=False, mode="algo", trade_id=trade_id, status="rejected",
            message="Dashboard अभी Algo mode में नहीं है - पहले Settings में mode बदलें।",
        )
    if not confirm:
        return ExecutionResult(
            ok=False, mode="algo", trade_id=trade_id, status="rejected",
            message="confirm=True explicitly नहीं भेजा गया - सुरक्षा के लिए ऑर्डर नहीं गया।",
        )
    if not dhan_client.is_available():
        return ExecutionResult(
            ok=False, mode="algo", trade_id=trade_id, status="rejected",
            message="Dhan configured नहीं है (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN missing)।",
        )
    if not setup.get("is_eligible", False):
        return ExecutionResult(
            ok=False, mode="algo", trade_id=trade_id, status="rejected",
            message=f"यह setup eligible नहीं है: {setup.get('rejection_reason', 'unknown reason')}",
        )

    symbol = setup.get("symbol")
    security_id = dhan_mapping.get_security_id(symbol) if symbol else None
    if security_id is None:
        return ExecutionResult(
            ok=False, mode="algo", trade_id=trade_id, status="rejected",
            message=f"{symbol} के लिए Dhan security_id मैप नहीं मिला - build_dhan_mapping.py चलाएं।",
        )

    order_payload = {
        "transactionType": "BUY" if setup.get("isDemand") else "SELL",
        "exchangeSegment": dhan_mapping.get_exchange_segment(symbol),
        "productType": product_type,
        "orderType": order_type,
        "securityId": security_id,
        "quantity": setup.get("quantity"),
        "price": setup.get("entry"),
    }

    result = dhan_client.place_order(order_payload, enable_live_orders=True)

    entry = {
        "trade_id": trade_id, "mode": "algo", "status": "order_placed" if result.get("ok") else "rejected",
        "setup": setup, "order_payload": order_payload, "broker_response": result,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_log(entry)

    if result.get("ok"):
        return ExecutionResult(
            ok=True, mode="algo", trade_id=trade_id, status="order_placed",
            message="Dhan को ऑर्डर भेज दिया गया।", broker_response=result,
        )
    return ExecutionResult(
        ok=False, mode="algo", trade_id=trade_id, status="rejected",
        message=f"Dhan ने ऑर्डर reject/fail किया: {result.get('error') or result.get('reason')}",
        broker_response=result,
    )


def get_trade_history(limit: int = 100) -> list[Dict[str, Any]]:
    if not TRADE_LOG_PATH.exists():
        return []
    lines = TRADE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))  # newest first
