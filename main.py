"""
main.py - FastAPI backend for the zone trading terminal.

Endpoints map directly to the spec's 3-page dashboard:
    /api/zones                 -> Page 1: scanned/validated zones table
    /api/instruments/live      -> Page 2: all instruments, live change%, TradingView link
    /api/settings, /api/news   -> Page 3: settings + global/instrument news+events
    /api/alerts/proximal       -> notification bar (zones within 0.5% of price)
    /api/health                -> which data sources are actually configured

Run locally:
    uvicorn main:app --reload
Render:
    see render.yaml / Procfile in this same folder.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import instruments
import scheduler
import execution
import settings_store
from config import CONFIG, status_report
from providers import price_provider, fii_dii_provider, news_provider
from providers import dhan_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# App-wide mutable settings (paper/manual/algo mode, scan range %, capital).
# Persisted to data/settings.json via settings_store, so a Render restart
# or redeploy no longer silently resets trading_mode back to "paper" -
# a real problem with the earlier pure in-memory version.
# ---------------------------------------------------------------------------
APP_SETTINGS: Dict[str, Any] = settings_store.load()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scan_task = asyncio.create_task(scheduler.run_forever(poll_interval_sec=30))
    broadcast_task = asyncio.create_task(alert_broadcaster(interval_sec=10))
    logger.info("Background scan cycle + alert broadcaster started")
    yield
    for task in (scan_task, broadcast_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Zone Trading Terminal API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your actual frontend origin before going live
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Page 1: Zones table
# ---------------------------------------------------------------------------
@app.get("/api/zones")
def get_zones(
    symbol: Optional[str] = Query(None, description="Filter to one NSE symbol"),
    timeframe: Optional[str] = Query(None, description="Filter to one timeframe e.g. '15M'"),
    eligible_only: bool = Query(True, description="Only RR>=3 and quantity>=1 setups"),
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    symbols = [symbol] if symbol else list(scheduler.LATEST_RESULTS.keys())
    for sym in symbols:
        by_tf = scheduler.LATEST_RESULTS.get(sym, {})
        tfs = [timeframe] if timeframe else list(by_tf.keys())
        for tf in tfs:
            payload = by_tf.get(tf)
            if not payload:
                continue
            for setup in payload["setups"]:
                if eligible_only and not setup.get("is_eligible"):
                    continue
                rows.append(setup)
    return {"count": len(rows), "zones": rows}


# ---------------------------------------------------------------------------
# Page 2: Instruments live, with TradingView inbuilt link
# ---------------------------------------------------------------------------
@app.get("/api/instruments/live")
def get_instruments_live(kind: str = Query("all", pattern="^(all|global|nse)$")) -> Dict[str, Any]:
    out = []
    if kind in ("all", "global"):
        for sym, meta in instruments.GLOBAL_INSTRUMENTS.items():
            price = price_provider.get_live_price(sym, yahoo_symbol=meta.get("yahoo"))
            out.append({
                "symbol": sym, "name": meta.get("name"), "kind": "global",
                "price": price, "tradingview_link": instruments.tradingview_link(sym),
            })
    if kind in ("all", "nse"):
        for sym in instruments.NSE_INSTRUMENTS.keys():
            yahoo_sym = f"{sym.replace('&', '_')}.NS"
            price = price_provider.get_live_price(sym, yahoo_symbol=yahoo_sym, nse_symbol=sym)
            out.append({
                "symbol": sym, "name": sym, "kind": "nse",
                "price": price, "tradingview_link": instruments.tradingview_link(sym),
            })
    return {"count": len(out), "instruments": out}


@app.get("/api/instruments/header")
def get_header_strip() -> Dict[str, Any]:
    """The top header bar: NIFTY 1!, GIFTNIFTY, DXY, USDINR, US10Y, XAUUSD, SPOTCRUDE, US500."""
    header_symbols = ["NIFTY1!", "GIFTNIFTY", "DXY", "USDINR", "US10Y", "XAUUSD", "SPOTCRUDE", "US500"]
    out = []
    for sym in header_symbols:
        meta = instruments.GLOBAL_INSTRUMENTS.get(sym, {})
        price = price_provider.get_live_price(sym, yahoo_symbol=meta.get("yahoo"))
        out.append({"symbol": sym, "price": price})
    return {"header": out}


# ---------------------------------------------------------------------------
# Page 3: Settings + global/instrument news & events, FII/DII
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    trading_mode: Optional[str] = None
    capital: Optional[float] = None
    risk_pct: Optional[float] = None
    target_rr: Optional[float] = None
    scan_range_pct: Optional[float] = None
    proximal_alert_pct: Optional[float] = None


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    return {**APP_SETTINGS, "sources": status_report()}


@app.post("/api/settings")
def update_settings(update: SettingsUpdate) -> Dict[str, Any]:
    if update.trading_mode is not None:
        if update.trading_mode not in ("paper", "manual", "algo"):
            raise HTTPException(400, "trading_mode must be paper, manual, or algo")
        if update.trading_mode == "algo" and not dhan_client.is_available():
            raise HTTPException(400, "Algo mode के लिए पहले DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN configure करें")
        APP_SETTINGS["trading_mode"] = update.trading_mode
    for field in ("capital", "risk_pct", "target_rr", "scan_range_pct", "proximal_alert_pct"):
        val = getattr(update, field)
        if val is not None:
            APP_SETTINGS[field] = val
    settings_store.save(APP_SETTINGS)  # persist so a restart doesn't silently revert mode/capital
    return {**APP_SETTINGS, "sources": status_report()}


@app.get("/api/news")
def get_news(symbol: Optional[str] = None) -> Dict[str, Any]:
    return news_provider.get_verified_news_and_events(instrument_query=symbol)


@app.get("/api/fii-dii")
def get_fii_dii() -> Dict[str, Any]:
    return fii_dii_provider.get_fii_dii_last_3_days()


# ---------------------------------------------------------------------------
# Notification bar: zones within proximal_alert_pct of current price
# ---------------------------------------------------------------------------
@app.get("/api/alerts/proximal")
def get_proximal_alerts() -> Dict[str, Any]:
    alerts = scheduler.get_proximal_alerts(APP_SETTINGS["proximal_alert_pct"])
    return {"count": len(alerts), "alerts": alerts}


# ---------------------------------------------------------------------------
# Trade execution: paper / manual / algo
# ---------------------------------------------------------------------------
class ExecuteRequest(BaseModel):
    setup: Dict[str, Any]
    note: Optional[str] = None       # used by manual mode
    confirm: bool = False            # required True for algo mode - never defaulted true


@app.post("/api/execute")
def execute_trade(req: ExecuteRequest) -> Dict[str, Any]:
    mode = APP_SETTINGS["trading_mode"]
    if mode == "paper":
        result = execution.execute_paper(req.setup)
    elif mode == "manual":
        result = execution.execute_manual(req.setup, note=req.note or "")
    elif mode == "algo":
        result = execution.execute_algo(
            req.setup, dashboard_trading_mode=mode, confirm=req.confirm,
        )
    else:
        raise HTTPException(400, f"Unknown trading_mode: {mode}")

    if not result.ok and result.status == "rejected":
        # Still 200 with ok:false rather than an HTTP error - a rejection
        # (e.g. "confirm not set") is an expected, informative outcome for
        # the frontend to show, not a server fault.
        return {"ok": False, "trade_id": result.trade_id, "status": result.status, "message": result.message}
    return {
        "ok": result.ok, "trade_id": result.trade_id, "status": result.status,
        "message": result.message, "broker_response": result.broker_response,
    }


@app.get("/api/trades")
def get_trades(limit: int = Query(100, le=500)) -> Dict[str, Any]:
    history = execution.get_trade_history(limit=limit)
    return {"count": len(history), "trades": history}


# ---------------------------------------------------------------------------
# WebSocket: push proximal alerts + zone updates instead of 15s polling
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self.active: List[Any] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active:
                self.active.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        dead = []
        async with self._lock:
            connections = list(self.active)
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


manager = ConnectionManager()


@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We don't need anything FROM the client - just keep the
            # socket open and let the broadcaster push to it. A short
            # receive with a generous timeout lets us notice a client
            # disconnect promptly without a tight busy-loop.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


async def alert_broadcaster(interval_sec: int = 10) -> None:
    """Background task: periodically pushes proximal alerts to every
    connected client. Falls back gracefully to nothing if no one is
    connected - broadcast() over an empty list is a cheap no-op."""
    while True:
        try:
            alerts = scheduler.get_proximal_alerts(APP_SETTINGS["proximal_alert_pct"])
            if alerts:
                await manager.broadcast({"type": "proximal_alerts", "alerts": alerts})
        except Exception:
            logger.exception("alert_broadcaster cycle failed")
        await asyncio.sleep(interval_sec)


# ---------------------------------------------------------------------------
# Health / diagnostics
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "sources": status_report(),
        "trading_mode": APP_SETTINGS["trading_mode"],
        "symbols_scanned": len(scheduler.LATEST_RESULTS),
        "nifty_context": {
            "price": scheduler._NIFTY_ZONES_CACHE.get("price"),
            "zones_cached": len(scheduler._NIFTY_ZONES_CACHE.get("zones") or []),
            "scanned_at": scheduler._NIFTY_ZONES_CACHE.get("scanned_at"),
        },
    }


# Serve the dashboard's static frontend (see frontend/ folder) at the root.
try:
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
except RuntimeError:
    logger.warning("frontend/ folder not found - API-only mode")
