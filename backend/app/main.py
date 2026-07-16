import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import config
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

    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _broadcast_loop() -> None:
    while True:
        await asyncio.sleep(config.BROADCAST_INTERVAL)
        if manager.active:
            await manager.broadcast(monitor.snapshot())


def _sd_notify(state: str) -> None:
    """Compatible con systemd Type=notify y Docker. No-op si NOTIFY_SOCKET no está definido."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(addr)
        sock.sendall(state.encode())
    except OSError:
        logger.warning("sd_notify failed", exc_info=True)
    finally:
        sock.close()


async def _watchdog_loop() -> None:
    """Compatible con systemd WatchdogSec y Docker. No-op si WATCHDOG_USEC no está definido."""
    usec = int(os.environ.get("WATCHDOG_USEC", "0"))
    if usec <= 0:
        return
    interval = usec / 1_000_000 / 2
    while True:
        _sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    monitor.start()
    broadcast_task = asyncio.create_task(_broadcast_loop())
    watchdog_task = asyncio.create_task(_watchdog_loop())
    _sd_notify("READY=1")
    yield
    watchdog_task.cancel()
    broadcast_task.cancel()
    await monitor.stop()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Lab Dashboard", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
@limiter.limit("30/second")
async def get_status(request):
    return monitor.snapshot()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json(monitor.snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
