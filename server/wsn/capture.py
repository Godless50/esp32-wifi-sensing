"""Phone capture protocol (spec 6.3/6.4), transport-independent: on_message(client_id, msg) -> replies.

Client → server: hello, ping{c0}, sync{c0,s,c1}, poses{items:[{seq,t,p,q}]}, event{seq,t,kind,...}
Server → client: pong{c0,s}, ack{pose_seq,event_seq}, state{...}, error{code,message}
Times from the phone are performance.now() milliseconds; they are stored converted to server monotonic seconds.
Sequence numbers are per client_id and survive reconnects, so resent items are dropped, not duplicated.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable

from .recorder import Recorder
from .store import SessionBusy, Store
from .timesync import ClockSync

EVENT_KINDS = {
    "session_start", "session_stop", "origin", "router", "room_new", "corner", "room_close", "object", "label",
    "device",  # телефон у платы или точки доступа: {mac, role: board|ap, p}
    "step",    # шаг сценария записи в приложении: {name, room?, activity, duration_s}
}


@dataclass
class _Client:
    clock: ClockSync = field(default_factory=ClockSync)
    pose_seq: int = 0
    event_seq: int = 0


def _err(code: str, message: str = "") -> dict:
    return {"type": "error", "code": code, "message": message or code}


class CaptureProtocol:
    def __init__(
        self,
        store: Store,
        recorder: Recorder,
        default_device: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        validate_event: Callable[[str, dict], tuple[str, str] | None] | None = None,
        devices: Callable[[], list[dict]] | None = None,
    ):
        self.store = store
        self.recorder = recorder
        self.default_device = default_device
        self.devices = devices  # платы и их точки доступа в эфире: что телефону отмечать на плане
        self.now = clock
        self.validate_event = validate_event  # returns (code, message) to reject the event, else None
        self.clients: dict[str, _Client] = {}
        self.owner: str | None = None

    def client(self, cid: str) -> _Client:
        return self.clients.setdefault(cid, _Client())

    def state(self, cid: str) -> dict:
        s = self.store.active()
        return {
            "type": "state",
            "session": dataclasses.asdict(s) if s else None,
            "owner": s is not None and self.owner == cid,
            "device_id": self.default_device(),
            "clock_synced": self.client(cid).clock.offset is not None,
            "devices": self.devices() if self.devices is not None else [],
        }

    def _ack(self, c: _Client) -> dict:
        return {"type": "ack", "pose_seq": c.pose_seq, "event_seq": c.event_seq}

    def _owns(self, cid: str) -> bool:
        s = self.store.active()
        return s is not None and self.owner == cid

    def on_message(self, cid: str, msg: dict) -> list[dict]:
        kind = msg.get("type")
        c = self.client(cid)
        if kind == "hello":
            return [self.state(cid), self._ack(c)]
        if kind == "ping":
            return [{"type": "pong", "c0": msg.get("c0"), "s": self.now()}]
        if kind == "sync":
            try:
                c.clock.add(float(msg["c0"]), float(msg["s"]), float(msg["c1"]))
            except (KeyError, TypeError, ValueError):
                return [_err("bad_sync")]
            return []
        if kind == "poses":
            return self._poses(cid, c, msg.get("items") or [])
        if kind == "event":
            return self._event(cid, c, msg)
        return [_err("bad_type", f"unknown message type {kind!r}")]

    def _poses(self, cid: str, c: _Client, items: list) -> list[dict]:
        out: list[dict] = []
        fresh = [it for it in items if isinstance(it, dict) and int(it.get("seq", 0)) > c.pose_seq]
        if not fresh:
            return [self._ack(c)]
        if self.store.active() is None:
            c.pose_seq = max(int(it["seq"]) for it in fresh)  # нечего писать: подтвердить, чтобы телефон не копил
            return [_err("no_session"), self._ack(c)]
        if not self._owns(cid):
            return [_err("not_owner", "another phone is recording")]
        if c.clock.offset is None:
            return [_err("no_clock_sync")]
        for it in sorted(fresh, key=lambda x: int(x["seq"])):
            seq = int(it["seq"])
            if seq <= c.pose_seq:
                continue
            self.store.append_pose({"seq": seq, "t": c.clock.to_server(float(it["t"])), "t_phone": it["t"],
                                    "p": it.get("p"), "q": it.get("q")})
            c.pose_seq = seq
        self.recorder.note_activity(self.now())
        out.append(self._ack(c))
        return out

    def _event(self, cid: str, c: _Client, msg: dict) -> list[dict]:
        seq = int(msg.get("seq", 0))
        ev_kind = msg.get("kind")
        if ev_kind not in EVENT_KINDS:
            return [_err("bad_event", f"unknown event {ev_kind!r}")]
        if seq <= c.event_seq:
            return [self._ack(c)]  # повтор после переподключения
        if c.clock.offset is None:
            return [_err("no_clock_sync")]
        t = c.clock.to_server(float(msg.get("t", 0)))
        rec = {k: v for k, v in msg.items() if k not in ("type",)}
        rec.update({"t": t, "t_phone": msg.get("t"), "seq": seq})

        if ev_kind == "session_start":
            if self.store.active() is not None and not self._owns(cid):
                return [_err("session_busy", "another session is recording")]
            if self.store.active() is None:
                dev = self.default_device()
                if not dev:
                    return [_err("no_device", "no board is streaming CSI")]
                try:
                    self.store.start_session(msg.get("session_kind", "walk"), dev, self.now(), msg.get("duration_s"))
                except (SessionBusy, ValueError) as e:
                    return [_err("bad_session", str(e))]
                self.owner = cid
                if self.validate_event is not None:
                    self.validate_event(ev_kind, rec)  # сброс состояния проверки на новую сессию
            self.store.append_event(rec)
        elif not self._owns(cid):
            return [_err("no_session" if self.store.active() is None else "not_owner")]
        else:
            if self.validate_event is not None:
                problem = self.validate_event(ev_kind, rec)
                if problem:
                    c.event_seq = seq  # отклонённое событие не надо пересылать
                    # для разбора: сохраняем, но под другим kind, чтобы план его не учитывал
                    self.store.append_event({**rec, "kind": "rejected", "orig_kind": ev_kind,
                                             "code": problem[0], "message": problem[1]})
                    return [_err(*problem), self._ack(c)]
            self.store.append_event(rec)
            if ev_kind == "session_stop":
                s = self.store.active()
                self.store.stop_session(s.id, self.now())
                self.owner = None
        c.event_seq = seq
        self.recorder.note_activity(self.now())
        return [self._ack(c), self.state(cid)]
