"""FastAPI app: REST, live WebSocket, static UI."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ingest import IngestService
from .live import LiveHub

log = logging.getLogger(__name__)


def create_app(hub: LiveHub, ingest: IngestService, ui_dir: Path, udp_port: int | None = None) -> FastAPI:
    ui_dir = Path(ui_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        transport = None
        if udp_port is not None:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(lambda: ingest, local_addr=("0.0.0.0", udp_port))
            log.info("UDP ingest on :%d", udp_port)
        try:
            yield
        finally:
            if transport is not None:
                transport.close()

    app = FastAPI(title="wsn", lifespan=lifespan)

    @app.get("/api/devices")
    def api_devices() -> dict:
        return {
            "devices": [dataclasses.asdict(d) for d in hub.devices()],
            "unknown_devices": sorted(ingest.unknown_devices),
            "counters": dict(ingest.counters),
        }

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        sub = hub.subscribe()  # до accept: клиент не пропустит первые сообщения
        try:
            await ws.accept()
            async for msg in sub:
                await ws.send_json(msg)
        except WebSocketDisconnect:
            pass
        finally:
            sub.close()

    @app.get("/device")
    def device_page() -> FileResponse:
        return FileResponse(ui_dir / "device" / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=ui_dir), name="static")
    return app
