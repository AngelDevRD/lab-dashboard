import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import config
from .alerts.center import alert_center
from .alerts.service import notification_service
from .alerts.thresholds import threshold_manager
from .collectors import claude_usage, framework_telemetry
from .collectors.claude_usage import ClaudeUsagePush
from .collectors.framework_telemetry import FrameworkTelemetryReport
from .models import HealthResponse, StatusResponse
from .monitor import monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dashboard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def _send_one(self, ws: WebSocket, payload: dict) -> WebSocket | None:
        # A half-open socket (wifi drop, device sleep, NAT rebind with no
        # keepalive/ping-pong anywhere in the stack) can hang send_json() for
        # a long time at the OS level. Bound every send so one bad client
        # can never stall the broadcast to everyone else.
        try:
            await asyncio.wait_for(
                ws.send_json(payload), timeout=config.WS_SEND_TIMEOUT
            )
            return None
        except (
            WebSocketDisconnect,
            RuntimeError,
            ConnectionError,
            asyncio.TimeoutError,
        ) as exc:
            logger.warning("WS send failed, dropping client: %s", exc)
            return ws

    async def broadcast(self, payload: dict) -> None:
        results = await asyncio.gather(
            *(self._send_one(ws, payload) for ws in self.active), return_exceptions=True
        )
        for ws in results:
            if isinstance(ws, WebSocket):
                self.disconnect(ws)


manager = ConnectionManager()


async def _broadcast_loop() -> None:
    while True:
        await asyncio.sleep(config.BROADCAST_INTERVAL)
        if manager.active:
            started = time.monotonic()
            try:
                await manager.broadcast(monitor.snapshot())
            except Exception:
                logger.exception("broadcast_loop iteration crashed, continuing")
            else:
                duration_ms = (time.monotonic() - started) * 1000
                if duration_ms > config.WS_SEND_TIMEOUT * 1000:
                    logger.warning(
                        "broadcast to %d clients took %.0fms",
                        len(manager.active),
                        duration_ms,
                    )


async def _prewarm_claude_usage() -> None:
    # The first ccusage call per period is a slow full log scan (seconds to
    # tens of seconds). Kick it off at startup instead of waiting for the
    # first user to open the Métricas view and eat that latency live.
    for period in ("daily", "session"):
        try:
            await claude_usage.get_report(period)
        except Exception:
            logger.warning("claude_usage prewarm failed for %s", period)


@asynccontextmanager
async def lifespan(app: FastAPI):
    monitor.start()
    broadcast_task = asyncio.create_task(_broadcast_loop())
    prewarm_task = asyncio.create_task(_prewarm_claude_usage())
    yield
    broadcast_task.cancel()
    prewarm_task.cancel()
    await monitor.stop()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Lab Dashboard", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_stale_cache(request: Request, call_next):
    # Tablets running old/quirky browsers (Android WebViews, frozen Chrome
    # builds) don't reliably follow HTTP caching heuristics, so we spell out
    # the policy explicitly instead of leaving it up to their guesswork.
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/status", response_model=StatusResponse)
@limiter.limit("30/second")
async def get_status(request: Request):
    return StatusResponse(**monitor.snapshot())


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.get("/api/alerts")
async def get_alerts(server: str = "", severity: str = "", status: str = ""):
    alerts = alert_center.get_all()
    if server:
        alerts = [a for a in alerts if a.server_host == server or a.server == server]
    if severity:
        alerts = [a for a in alerts if a.severity.value.upper() == severity.upper()]
    if status:
        alerts = [a for a in alerts if a.status.value == status.lower()]
    return {
        "alerts": [a.model_dump() for a in alerts[-200:]],
        "count": alert_center.count(),
    }


@app.put("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    alert = notification_service.manually_resolve(alert_id)
    if not alert:
        alert = alert_center.get(alert_id)
        if alert:
            alert.status = "resolved"
            alert_center.update(alert)
    if alert:
        return {"ok": True, "alert": alert.model_dump()}
    return JSONResponse(status_code=404, content={"detail": "Alert not found"})


@app.get("/api/alerts/settings")
async def get_alert_settings():
    return threshold_manager.settings.model_dump()


@app.put("/api/alerts/settings")
async def update_alert_settings(settings: dict):
    try:
        updated = threshold_manager.update(settings)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"detail": exc.errors()})
    return updated.model_dump()


@app.get("/api/alerts/count")
async def get_alert_count():
    return alert_center.count()


@app.get("/api/claude-usage")
async def get_claude_usage(period: str = "daily", days: str = "30"):
    since = None
    try:
        if days != "all":
            since = (date.today() - timedelta(days=max(int(days), 1) - 1)).strftime(
                "%Y%m%d"
            )
    except ValueError:
        return JSONResponse(
            status_code=422, content={"detail": f"invalid days: {days!r}"}
        )
    try:
        return await claude_usage.get_report(period, since=since)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except Exception as exc:
        logger.warning("claude_usage fetch failed: %s", exc)
        return JSONResponse(status_code=502, content={"detail": "ccusage unavailable"})


@app.get("/api/claude-usage/status")
async def get_claude_usage_status():
    status = claude_usage.push_status()
    if status is None:
        return JSONResponse(status_code=404, content={"detail": "no push received yet"})
    return status


@app.post("/api/claude-usage/report")
@limiter.limit("30/second")
async def report_claude_usage(
    request: Request, payload: ClaudeUsagePush, x_claude_usage_token: str = Header(default="")
):
    if (
        not config.CLAUDE_USAGE_REPORT_TOKEN
        or x_claude_usage_token != config.CLAUDE_USAGE_REPORT_TOKEN
    ):
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    claude_usage.save_push(payload)
    return Response(status_code=204)


@app.post("/api/network/report")
@limiter.limit("30/second")
async def report_network(
    request: Request, payload: dict, x_guardian_token: str = Header(default="")
):
    if (
        not config.NETWORK_REPORT_TOKEN
        or x_guardian_token != config.NETWORK_REPORT_TOKEN
    ):
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    device_id = payload.get("device_id")
    if not device_id:
        return JSONResponse(status_code=422, content={"detail": "device_id required"})
    monitor.report_device(
        device_id, {k: v for k, v in payload.items() if k != "device_id"}
    )
    return Response(status_code=204)


@app.post("/api/framework-telemetry/report")
@limiter.limit("30/second")
async def report_framework_telemetry(
    request: Request,
    payload: FrameworkTelemetryReport,
    x_framework_token: str = Header(default=""),
):
    if (
        not config.FRAMEWORK_TELEMETRY_TOKEN
        or x_framework_token != config.FRAMEWORK_TELEMETRY_TOKEN
    ):
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    framework_telemetry.record(payload)
    return Response(status_code=204)


@app.get("/api/framework-telemetry")
async def get_framework_telemetry():
    return framework_telemetry.summary()


@app.get("/api/autonomy/metrics")
async def get_autonomy_metrics():
    """Diagnostico interno del algoritmo hibrido de autonomia (ver
    backend/app/collectors/autonomy.py) — no forma parte del dashboard
    principal, es para verificar en vivo que el disparador de calibracion
    esta funcionando como fue diseñado."""
    return monitor.autonomy_metrics()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    client = (
        f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
    )
    logger.info("WS connected: %s (active=%d)", client, len(manager.active))
    try:
        await websocket.send_json(monitor.snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS receive loop crashed for %s", client)
    finally:
        manager.disconnect(websocket)
        logger.info("WS disconnected: %s (active=%d)", client, len(manager.active))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
