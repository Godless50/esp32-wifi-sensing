"""FastAPI app: REST, live WebSocket, static UI."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from typing import Callable

from .capture import CaptureProtocol
from .ingest import IngestService
from .live import LiveHub
from .geometry import GeometryError, frame_from_points
from .plan import RoomTracker, build_plan
from .recorder import Recorder
from .store import SessionBusy, SessionNotFound, Store

log = logging.getLogger(__name__)
TICK_S = 5.0


class ZoneLabel(BaseModel):
    count: int = Field(ge=0, le=20)
    activity: str = Field(default="", max_length=40)


class OccupancyLabel(BaseModel):
    zones: dict[str, ZoneLabel]
    note: str = Field(default="", max_length=200)


class StartSession(BaseModel):
    kind: str
    device_id: str
    duration_s: float | None = None


def create_app(
    hub: LiveHub,
    ingest: IngestService,
    ui_dir: Path,
    udp_port: int | None = None,
    store: Store | None = None,
    recorder: Recorder | None = None,
    default_device: Callable[[], str | None] | None = None,
) -> FastAPI:
    ui_dir = Path(ui_dir)
    tracker = RoomTracker()
    phone_last = {"t": -1e9}

    def publish_phone(msg: dict) -> None:
        """Живая позиция телефона в системе плана (если в сессии уже отмечены плата и роутер), ≤4 Гц."""
        items = [it for it in (msg.get("items") or []) if isinstance(it, dict) and it.get("p")]
        now = time.monotonic()
        if not items or tracker.origin is None or tracker.router is None or now - phone_last["t"] < 0.25:
            return
        try:
            frame = frame_from_points(tracker.origin, tracker.router)
            x, y = frame.apply(items[-1]["p"])
        except (GeometryError, TypeError, ValueError, IndexError):
            return
        phone_last["t"] = now
        hub.publish({"type": "phone", "t": now, "xy": [round(x, 3), round(y, 3)]})

    capture = (
        CaptureProtocol(store, recorder, default_device or (lambda: None), validate_event=tracker.check)
        if store is not None and recorder is not None
        else None
    )

    async def ticker() -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                recorder.tick(time.monotonic())
            except Exception:
                log.exception("recorder tick failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        transport = None
        tasks = []
        if udp_port is not None:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(lambda: ingest, local_addr=("0.0.0.0", udp_port))
            log.info("UDP ingest on :%d", udp_port)
        if recorder is not None:
            tasks.append(asyncio.create_task(ticker()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
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

    def _store() -> Store:
        if store is None:
            raise HTTPException(503, "sessions disabled")
        return store

    @app.post("/api/sessions")
    def start_session(req: StartSession) -> dict:
        try:
            info = _store().start_session(req.kind, req.device_id, time.monotonic(), req.duration_s)
        except SessionBusy as e:
            raise HTTPException(409, f"session {e} is active") from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return dataclasses.asdict(info)

    @app.post("/api/labels")
    def add_label(req: OccupancyLabel) -> dict:
        """Разметка «кто где»: пишется событием occupancy в активную запись с серверным временем."""
        st = _store()
        s = st.active()
        if s is None:
            raise HTTPException(409, "no active session")
        st.append_event({"seq": None, "t": time.monotonic(), "kind": "occupancy",
                         "zones": {k: v.model_dump() for k, v in req.zones.items()}, "note": req.note})
        return {"session": s.id}

    @app.post("/api/sessions/{sid}/stop")
    def stop_session(sid: str) -> dict:
        try:
            return dataclasses.asdict(_store().stop_session(sid, time.monotonic()))
        except SessionNotFound as e:
            raise HTTPException(404, "no such session") from e

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return [dataclasses.asdict(s) for s in _store().list_sessions()]

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str) -> dict:
        try:
            _store().delete_session(sid)
        except SessionBusy as e:
            raise HTTPException(409, "stop the session first") from e
        except SessionNotFound as e:
            raise HTTPException(404, "no such session") from e
        return {"deleted": sid}

    @app.get("/api/plan")
    def api_plan() -> dict:
        return build_plan(_store())

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

    @app.websocket("/ws/capture")
    async def ws_capture(ws: WebSocket) -> None:
        await ws.accept()
        if capture is None:
            await ws.close(code=1013)
            return
        cid = f"anon-{id(ws)}"
        last_state = time.monotonic()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                except asyncio.TimeoutError:
                    msg = None
                if msg is not None:
                    if msg.get("type") == "hello" and msg.get("client_id"):
                        cid = str(msg["client_id"])[:64]
                    replies = capture.on_message(cid, msg)
                    for reply in replies:
                        await ws.send_json(reply)
                    if msg.get("type") == "poses" and not any(r.get("type") == "error" for r in replies):
                        publish_phone(msg)
                if time.monotonic() - last_state >= 1.0:
                    last_state = time.monotonic()
                    st = capture.state(cid)
                    devs = {d.device_id: d for d in hub.devices()}
                    d = devs.get(st["device_id"] or "")
                    st["csi_hz"] = d.csi_hz if d else 0.0
                    await ws.send_json(st)
        except (WebSocketDisconnect, RuntimeError):
            pass

    def page(rel: str):
        def handler() -> FileResponse:
            return FileResponse(ui_dir / rel, media_type="text/html")
        return handler

    app.get("/")(page("scene/index.html"))
    app.get("/device")(page("device/index.html"))
    app.get("/capture")(page("capture/index.html"))
    app.get("/capture/probe")(page("capture/probe.html"))
    app.get("/data")(page("data/index.html"))

    app.mount("/static", StaticFiles(directory=ui_dir), name="static")
    return app
