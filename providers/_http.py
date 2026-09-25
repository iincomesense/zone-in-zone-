"""
_http.py - shared request helper. Every provider uses this instead of
calling `requests` directly, so timeout/retry/error behaviour is
consistent and a single flaky source can never crash the pipeline -
it always degrades to a returned error dict instead of raising.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from config import CONFIG


def safe_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """GET request that never raises. Returns:
        {"ok": True, "data": <parsed json or None>, "text": <raw text>, "status": <int>}
        {"ok": False, "error": <str>, "status": <int or None>}
    """
    hdrs = {"User-Agent": CONFIG.user_agent}
    if headers:
        hdrs.update(headers)

    req_timeout = timeout if timeout is not None else CONFIG.http_timeout_sec
    max_tries = (retries if retries is not None else CONFIG.http_retries) + 1
    getter = session.get if session is not None else requests.get

    last_err = "unknown error"
    for attempt in range(max_tries):
        try:
            resp = getter(url, params=params, headers=hdrs, timeout=req_timeout)
            status = resp.status_code
            if status >= 400:
                last_err = f"HTTP {status}"
                # 429/5xx are worth a short backoff + retry, 4xx client
                # errors (bad symbol etc.) are not.
                if status in (429, 500, 502, 503, 504) and attempt < max_tries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"ok": False, "error": last_err, "status": status}
            try:
                data = resp.json()
            except ValueError:
                data = None
            return {"ok": True, "data": data, "text": resp.text, "status": status}
        except requests.RequestException as exc:
            last_err = str(exc)
            if attempt < max_tries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
    return {"ok": False, "error": last_err, "status": None}


def new_nse_session() -> requests.Session:
    """NSE India's site requires an initial homepage hit to pick up
    cookies before its data endpoints will respond - without this every
    NSE call returns 401/403."""
    s = requests.Session()
    s.headers.update({"User-Agent": CONFIG.user_agent, "Accept": "*/*"})
    try:
        s.get("https://www.nseindia.com", timeout=CONFIG.http_timeout_sec)
    except requests.RequestException:
        pass  # session still usable for a retry by the caller; not fatal
    return s
