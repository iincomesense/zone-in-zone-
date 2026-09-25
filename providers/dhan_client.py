"""
dhan_client.py - thin wrapper around the Dhan HQ v2 REST API.

Rules this file follows:
1. Import must never fail and no function here may raise on a missing
   client_id/access_token - every public function checks
   `CONFIG.dhan.is_configured` first and returns a clean
   {"ok": False, "reason": "dhan_not_configured"} instead.
2. This is a data/broker READ+ORDER wrapper, not an execution engine -
   order placement is here as a placeholder that is explicitly disabled
   until `enable_live_orders=True` is passed, so the terminal defaults
   to Paper mode everywhere else in the app.

Docs referenced (Dhan HQ v2): LTP/quote via /v2/marketfeed/ltp and
/v2/marketfeed/quote, historical candles via /v2/charts/historical,
orders via /v2/orders. Exact schema can shift - keep this module as the
single place to patch when Dhan changes a field name.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import CONFIG
from providers._http import safe_get
import requests


def _headers() -> Dict[str, str]:
    return {
        "access-token": CONFIG.dhan.access_token or "",
        "client-id": CONFIG.dhan.client_id or "",
        "Content-Type": "application/json",
    }


def is_available() -> bool:
    return CONFIG.dhan.is_configured


def _not_configured(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"ok": False, "reason": "dhan_not_configured", "source": "Dhan"}
    if extra:
        out.update(extra)
    return out


def get_ltp(security_ids_by_segment: Dict[str, List[int]]) -> Dict[str, Any]:
    """Last traded price for a batch of instruments.

    security_ids_by_segment example:
        {"NSE_EQ": [11536, 1333], "IDX_I": [13]}
    (Dhan requires numeric security IDs, not the plain trading symbol -
    map your instrument -> Dhan security_id in a lookup table before
    calling this. That mapping is out of scope for this file.)
    """
    if not is_available():
        return _not_configured()

    url = f"{CONFIG.dhan.base_url}/marketfeed/ltp"
    try:
        resp = requests.post(
            url, json=security_ids_by_segment, headers=_headers(),
            timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "source": "Dhan"}
        return {"ok": True, "data": resp.json(), "source": "Dhan"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "source": "Dhan"}


def get_quote(security_ids_by_segment: Dict[str, List[int]]) -> Dict[str, Any]:
    """Fuller quote (OHLC, volume, OI where applicable) than get_ltp."""
    if not is_available():
        return _not_configured()

    url = f"{CONFIG.dhan.base_url}/marketfeed/quote"
    try:
        resp = requests.post(
            url, json=security_ids_by_segment, headers=_headers(),
            timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "source": "Dhan"}
        return {"ok": True, "data": resp.json(), "source": "Dhan"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "source": "Dhan"}


def get_historical_candles(
    security_id: int,
    exchange_segment: str,
    instrument_type: str,
    from_date: str,
    to_date: str,
    interval_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Historical OHLCV. interval_minutes=None -> daily endpoint,
    otherwise -> intraday endpoint. Dates as 'YYYY-MM-DD'."""
    if not is_available():
        return _not_configured()

    if interval_minutes:
        url = f"{CONFIG.dhan.base_url}/charts/intraday"
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "interval": str(interval_minutes),
            "fromDate": from_date,
            "toDate": to_date,
        }
    else:
        url = f"{CONFIG.dhan.base_url}/charts/historical"
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "fromDate": from_date,
            "toDate": to_date,
        }

    try:
        resp = requests.post(
            url, json=payload, headers=_headers(), timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "source": "Dhan"}
        return {"ok": True, "data": resp.json(), "source": "Dhan"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "source": "Dhan"}


def place_order(order_payload: Dict[str, Any], enable_live_orders: bool = False) -> Dict[str, Any]:
    """Order placement - LOCKED by default.

    This function will refuse to send a real order unless the caller
    explicitly passes enable_live_orders=True. This keeps the default
    app behaviour (Paper/Manual mode from the dashboard spec) safe even
    if Dhan keys are already configured for data purposes.
    """
    if not is_available():
        return _not_configured()

    if not enable_live_orders:
        return {
            "ok": False,
            "reason": "live_orders_disabled",
            "message": "Order placement is disabled. Switch dashboard to Algo mode "
                       "and pass enable_live_orders=True explicitly to arm this.",
            "source": "Dhan",
        }

    url = f"{CONFIG.dhan.base_url}/orders"
    try:
        resp = requests.post(
            url, json=order_payload, headers=_headers(), timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "detail": resp.text, "source": "Dhan"}
        return {"ok": True, "data": resp.json(), "source": "Dhan"}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "source": "Dhan"}
