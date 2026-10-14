"""FastAPI app: REST, live WebSocket, static UI."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from typing import Callable

from .capture import CaptureProtocol
from .ingest import IngestService
from .live import LiveHub
from .geometry import GeometryError, frame_from_points
from .person import PersonTracker
from .plan import RoomTracker, build_plan
from .recorder import Recorder
from .store import SessionBusy, SessionNotFound, Store

log = logging.getLogger(__name__)
TICK_S = 5.0


POSTURES = ("стоит", "сидит", "лежит", "разные")
ARM_STATES = ("опущены", "согнуты", "подняты", "разные")


class ZoneLabel(BaseModel):
    count: int = Field(ge=0, le=20)
    activity: str = Field(default="", max_length=40)
    moving: int | None = Field(default=None, ge=0, le=20)  # сколько из них сейчас двигается (страница /people)
    posture: str | None = Field(default=None, max_length=10)
    arms: str | None = Field(default=None, max_length=10)


SUBJECT_ACTS = ("покой", "стоит", "машет", "ходит", "мелкие", "переход")


class SubjectLabel(BaseModel):
    """Кого ведёт сценарий: где он и что делает (точная метка для обучения)."""
    zone: str = Field(max_length=20)
    act: str = Field(max_length=12)
    place: str = Field(default="", max_length=40)


class OccupancyLabel(BaseModel):
    zones: dict[str, ZoneLabel]
    note: str = Field(default="", max_length=200)
    script: bool = False  # метку поставил сценарий по таймеру, а не человек по памяти
    subject: SubjectLabel | None = None
    delay_s: float = Field(default=0.0, ge=0, le=900)  # метка вступает в силу позже (все ушли из квартиры)


class Prediction(BaseModel):
    """Live model output posted by ml/learner.py; forwarded to /ws/live as {"type": "pred", ...}."""
    device_id: str = Field(max_length=32)
    zone: str = Field(max_length=20)
    zone_p: float = Field(ge=0, le=1)
    activity: str = Field(max_length=20)
    activity_p: float = Field(ge=0, le=1)
    moving_p: float = Field(ge=0, le=1)
    probs: dict[str, float] = Field(default_factory=dict)
    model: str = Field(default="", max_length=40)
    links_live: int | None = Field(default=None, ge=0, le=64)  # живые линии модели в окне (трекер: < 4 — шум)
    # многоголовая модель (ml/train_multi.py): сколько людей двигается, по зонам, поза одного человека
    people_probs: list[float] | None = Field(default=None, max_length=8)  # 0..5 человек
    counts_probs: dict[str, list[float]] | None = None
    pose_probs: dict[str, float] | None = None
    arms_probs: dict[str, float] | None = None
    legs_probs: dict[str, float] | None = None
    angles: list[float] | None = Field(default=None, max_length=4)


class WifiSecret(BaseModel):
    password: str = Field(min_length=8, max_length=63)


class ProvisionResult(BaseModel):
    ok: bool
    message: str = Field(default="", max_length=300)


class ModelPushReq(BaseModel):
    device_id: str | None = Field(default=None, max_length=17)
    file: str = Field(default="board_model.bin", max_length=80)  # в data/ml


class StartSession(BaseModel):
    kind: str
    device_id: str
    duration_s: float | None = None

PLAN_REFRESH_S = 60.0

def create_app(
    hub: LiveHub,
    ingest: IngestService,
    ui_dir: Path,
    udp_port: int | None = None,
    store: Store | None = None,
    recorder: Recorder | None = None,
    default_device: Callable[[], str | None] | None = None,
    pinger=None,
    pusher=None,
    sounder=None,
    infer=None,
) -> FastAPI:
    ui_dir = Path(ui_dir)
    tracker = RoomTracker()
    person = PersonTracker(  # человек на плане: слушает pred / motion / breath хаба; состояние — между перезапусками
        publish=hub.publish, state_path=(store.sessions_dir.parent / "run" / "person_state.json") if store else None)
    hub.listeners.append(person.on_message)
    if infer is not None:  # модель квартиры (wsn.zonemodel): каждый кадр CSI -> её окна
        hub.frame_listeners.append(infer.on_frame)

    def board_list() -> list[dict]:
        """Boards on the air (provisioning order) and the access point each one is connected to."""
        known = ingest.keys.ids() if hasattr(ingest.keys, "ids") else []
        live = {d.device_id: d for d in hub.devices() if d.age_s <= 30}
        order = [d for d in known if d in live] + sorted(d for d in live if d not in known)
        out, aps = [], {}
        for i, dev in enumerate(order, 1):
            links = live[dev].links
            ap = max((s for s in links if s not in live and s not in known), key=lambda s: links[s]["hz"], default=None)
            out.append({"mac": dev, "role": "board", "name": f"Плата {i} ({dev[-5:]})", "ap": ap,
                        "links": len(links), "csi_hz": round(live[dev].csi_hz, 1)})
            if ap:
                aps.setdefault(ap, []).append(dev)
        for j, (ap, boards) in enumerate(aps.items(), 1):
            out.append({"mac": ap, "role": "ap", "name": f"Точка доступа {j} ({ap[-5:]})", "boards": boards})
        return out
    # Пароль Wi-Fi для новой платы: только в памяти процесса, 10 минут, отдаётся один раз скрипту на хосте
    # (tools/provision_web.sh) по токену из data/run/provision.token. На диск не пишется.
    prov = {"pw": None, "until": 0.0, "result": None}
    prov_token = secrets.token_hex(16)
    if store is not None:
        run_dir = store.sessions_dir.parent / "run"
        run_dir.mkdir(exist_ok=True)
        tok = run_dir / "provision.token"
        tok.write_text(prov_token)
        os.chmod(tok, 0o600)
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
        CaptureProtocol(store, recorder, default_device or (lambda: None), validate_event=tracker.check,
                        devices=board_list)
        if store is not None and recorder is not None
        else None
    )

    plan_cache: dict = {"plan": None, "dirty": False}

    async def plan_refresher() -> None:
        while True:
            try:
                plan_cache["dirty"] = False
                plan = await asyncio.to_thread(build_plan, store)
                plan_cache["plan"] = plan
                if person.set_plan(plan):
                    log.info("person tracker: plan updated, %s", person.snapshot()["grid"])
            except Exception:
                log.exception("plan refresh failed")
            for _ in range(int(PLAN_REFRESH_S)):  # запись обхода сохранена — план сразу, не через минуту
                if plan_cache["dirty"]:
                    break
                await asyncio.sleep(1.0)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                recorder.tick(time.monotonic())
            except Exception:
                log.exception("recorder tick failed")

    async def infer_loop() -> None:
        from .zonemodel import STEP_S
        while True:
            await asyncio.sleep(STEP_S)
            try:
                infer.tick(time.monotonic())
            except Exception:
                log.exception("room model tick failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        transport = None
        tasks = []
        if udp_port is not None:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(lambda: ingest, local_addr=("0.0.0.0", udp_port))
            log.info("UDP ingest on :%d", udp_port)
            if pusher is not None:
                pusher.transport = transport
            if sounder is not None:
                sounder.transport = transport
                tasks.append(asyncio.create_task(sounder.run()))
        if recorder is not None:
            tasks.append(asyncio.create_task(ticker()))
        if pinger is not None:
            tasks.append(asyncio.create_task(pinger.run()))
        if store is not None:
            tasks.append(asyncio.create_task(plan_refresher()))
        if infer is not None:
            tasks.append(asyncio.create_task(infer_loop()))
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
        if req.subject is not None and req.subject.act not in SUBJECT_ACTS:
            raise HTTPException(422, f"subject.act must be one of {SUBJECT_ACTS}")
        for name, z in req.zones.items():
            if z.moving is not None and z.moving > z.count:
                raise HTTPException(422, f"{name}: moving > count")
            if z.posture is not None and z.posture not in POSTURES:
                raise HTTPException(422, f"posture must be one of {POSTURES}")
            if z.arms is not None and z.arms not in ARM_STATES:
                raise HTTPException(422, f"arms must be one of {ARM_STATES}")
        st = _store()
        s = st.active()
        if s is None:
            raise HTTPException(409, "no active session")
        ev = {"seq": None, "t": time.monotonic() + req.delay_s, "kind": "occupancy",
              "zones": {k: v.model_dump(exclude_none=True) for k, v in req.zones.items()}, "note": req.note}
        if req.delay_s:
            ev["delay_s"] = req.delay_s
        if req.script:
            ev["script"] = True
        if req.subject is not None:
            ev["subject"] = req.subject.model_dump()
        st.append_event(ev)
        return {"session": s.id}

    @app.post("/api/provision/wifi")
    def provision_wifi(req: WifiSecret) -> dict:
        prov.update(pw=req.password, until=time.monotonic() + 600, result=None)
        return {"ok": True, "expires_s": 600}

    @app.get("/api/provision/status")
    def provision_status() -> dict:
        waiting = prov["pw"] is not None and time.monotonic() < prov["until"]
        return {"waiting": waiting, "result": prov["result"]}

    def _check_token(token: str | None) -> None:
        if not token or not secrets.compare_digest(token, prov_token):
            raise HTTPException(403, "token")

    @app.post("/api/provision/take")
    def provision_take(x_provision_token: str | None = Header(default=None)) -> dict:
        _check_token(x_provision_token)
        if prov["pw"] is None or time.monotonic() >= prov["until"]:
            prov["pw"] = None
            raise HTTPException(404, "nothing to take")
        pw, prov["pw"] = prov["pw"], None
        return {"password": pw}

    @app.post("/api/provision/result")
    def provision_result(req: ProvisionResult, x_provision_token: str | None = Header(default=None)) -> dict:
        _check_token(x_provision_token)
        prov["result"] = req.model_dump()
        return {"ok": True}

    @app.post("/api/model/push")
    async def model_push(req: ModelPushReq) -> dict:  # async: отправка идёт задачей в цикле событий сервера
        """Send data/ml/<file> (board model blob) to one board, or to every board seen in the last 10 s."""
        if pusher is None or store is None:
            raise HTTPException(503, "model push disabled")
        ml = (store.sessions_dir.parent / "ml").resolve()
        path = (ml / req.file).resolve()
        if path.parent != ml or not path.is_file():
            raise HTTPException(404, "no such model file in data/ml")
        blob = path.read_bytes()
        devs = [req.device_id.lower()] if req.device_id else [d.device_id for d in hub.devices() if d.age_s < 10]
        if not devs:
            raise HTTPException(409, "no board online")
        out = []
        for dev in devs:
            try:
                out.append(vars(pusher.start(dev, blob)))
            except (ValueError, RuntimeError) as e:
                raise HTTPException(409, f"{dev}: {e}") from e
        return {"pushes": out}

    @app.get("/api/model/push")
    def model_push_status() -> dict:
        return {"pushes": pusher.status() if pusher is not None else []}

    @app.post("/api/predict")
    def add_prediction(req: Prediction) -> dict:
        hub.publish({"type": "pred", "source": "server", "t": time.monotonic(), **req.model_dump()})
        return {"ok": True}

    @app.post("/api/sessions/{sid}/stop")
    def stop_session(sid: str) -> dict:
        try:
            return dataclasses.asdict(_store().stop_session(sid, time.monotonic()))
        except SessionNotFound as e:
            raise HTTPException(404, "no such session") from e

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return [dataclasses.asdict(s) for s in _store().list_sessions()]

    @app.get("/api/sessions/{sid}/labels")
    def session_labels(sid: str) -> list[dict]:
        """Разметка записи «кто где» по времени от начала записи, с."""
        st = _store()
        info = next((s for s in st.list_sessions() if s.id == sid), None)
        if info is None:
            raise HTTPException(404, "no such session")
        path = st.session_dir(sid) / "events.jsonl"
        out = []
        for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "occupancy" and isinstance(e.get("t"), (int, float)):
                out.append({"t": round(e["t"] - info.started, 1), "note": e.get("note", ""), "script": bool(e.get("script")),
                            "subject": e.get("subject"), "zones": e.get("zones", {})})
        return out

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str) -> dict:
        try:
            _store().delete_session(sid)
        except SessionBusy as e:
            raise HTTPException(409, "stop the session first") from e
        except SessionNotFound as e:
            raise HTTPException(404, "no such session") from e
        return {"deleted": sid}

    @app.get("/api/links")
    def api_links() -> dict:
        """Extra radio links: pinged devices and per-transmitter CSI rate at each board."""
        return {"targets": pinger.stats() if pinger is not None else [],
                "devices": {d.device_id: d.links for d in hub.devices()}}

    @app.get("/api/plan")
    def api_plan() -> dict:
        plan = build_plan(_store())
        person.set_plan(plan)
        return plan

    @app.delete("/api/person")
    def api_person_reset() -> dict:
        """Forget the tracked position (the tracker then waits for movement again)."""
        person.reset()
        return {"ok": True}

    @app.get("/api/person")
    def api_person() -> dict:
        """Last tracked position of the person, grid size and per-link noise floors (for debugging)."""
        return person.snapshot()

    def train_status() -> dict | None:
        if store is None:
            return None
        p = store.sessions_dir.parent / "run" / "train_status.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def model_status() -> dict | None:
        """Model state; links_now only of our boards: receiver and transmitter are known boards, or the transmitter
        is the receiver's access point (a stranger's ESP-NOW beacon on the channel is not a link of this flat)."""
        if infer is None:
            return None
        st = infer.status()
        boards = {b["mac"]: b for b in board_list() if b["role"] == "board"}
        known = set(ingest.keys.ids() if hasattr(ingest.keys, "ids") else []) | set(boards)

        def ours(lk: str) -> bool:
            rx, _, tx = lk.partition("|")
            return rx in known and (tx in known or tx == (boards.get(rx) or {}).get("ap"))
        st["links_now"] = [lk for lk in st["links_now"] if ours(lk)]
        return st

    # async: читают состояние приёма (буферы, статистику плат) в том же цикле событий, где оно меняется
    @app.get("/api/model")
    async def api_model() -> dict:
        """The flat's room model (data/model) and the state of the last training (train.sh)."""
        return {"model": model_status(), "training": train_status()}

    @app.get("/api/status")
    async def api_status() -> dict:
        """Everything the phone app shows on its home screen: boards, plan, recordings, model."""
        devs = board_list()
        plan = plan_cache["plan"] or (build_plan(store) if store is not None else None)
        sessions = _store().list_sessions() if store is not None else []
        walks = [s for s in sessions if s.kind == "walk" and s.stopped is not None]
        active = store.active() if store is not None else None
        return {
            "boards": [d for d in devs if d["role"] == "board"],
            "aps": [d for d in devs if d["role"] == "ap"],
            "plan": None if plan is None else {
                "rooms": [r["name"] for r in plan["rooms"]],
                "boards": [b["device_id"] for b in plan["boards"]],
                "skipped": plan["skipped"], "warnings": plan["warnings"]},
            "walks": [{"id": s.id, "started_wall": s.started_wall, "duration_s": round(s.stopped - s.started, 1),
                       "poses": s.poses, "events": s.events, "plan_ok": s.plan_ok} for s in walks[-5:]],
            "recording": dataclasses.asdict(active) if active else None,
            "model": model_status(),
            "training": train_status(),
            "person": person.last,
        }

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        sub = hub.subscribe()  # до accept: клиент не пропустит первые сообщения
        # ?types=person,rooms — только эти сообщения (телефону не нужен поток амплитуд по 20 Гц на плату)
        types = {t for t in (ws.query_params.get("types") or "").split(",") if t}
        try:
            await ws.accept()
            async for msg in sub:
                if not types or msg.get("type") in types:
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
                    if msg.get("type") == "event" and msg.get("kind") in ("session_stop", "device", "room_close"):
                        plan_cache["dirty"] = True
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

    app.get("/")(page("home/index.html"))
    app.get("/scene3d")(page("scene/index.html"))
    app.get("/device")(page("device/index.html"))
    app.get("/capture")(page("capture/index.html"))
    app.get("/capture/probe")(page("capture/probe.html"))
    app.get("/data")(page("data/index.html"))
    app.get("/scenario")(page("scenario/index.html"))
    app.get("/people")(page("people/index.html"))
    app.get("/provision")(page("provision/index.html"))

    @app.middleware("http")
    async def no_stale_ui(request, call_next):
        """Pages and scripts are revalidated on every load (ETag): after an update the browser never keeps old JS."""
        resp = await call_next(request)
        p = request.url.path
        if p.startswith("/static/") or not p.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    app.mount("/static", StaticFiles(directory=ui_dir), name="static")
    return app
