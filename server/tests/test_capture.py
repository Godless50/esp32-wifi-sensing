import json

import pytest
from starlette.testclient import TestClient

from wsn.app import create_app
from wsn.capture import CaptureProtocol
from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub
from wsn.recorder import Recorder
from wsn.store import Store

DEV = "7c:4f:ad:bc:9b:54"


class Clock:
    def __init__(self, t=500.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def proto(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    clock = Clock()
    p = CaptureProtocol(store, Recorder(store), default_device=lambda: DEV, clock=clock)
    p.clock_obj = clock
    return p


def synced(p, cid):
    """hello + one perfect sync sample: phone ms 0 == server 500.0 s."""
    p.on_message(cid, {"type": "hello", "client_id": cid})
    out = p.on_message(cid, {"type": "ping", "c0": 0})
    pong = next(m for m in out if m["type"] == "pong")
    assert pong["c0"] == 0 and pong["s"] == 500.0
    p.on_message(cid, {"type": "sync", "c0": 0, "s": 500.0, "c1": 0})


def ev(seq, kind, t=100, **kw):
    return {"type": "event", "seq": seq, "t": t, "kind": kind, **kw}


def errors(out):
    return [m["code"] for m in out if m["type"] == "error"]


def test_session_start_and_poses_dedup(proto):
    synced(proto, "A")
    proto.on_message("A", ev(1, "session_start", session_kind="walk"))
    sid = proto.store.active().id
    items = [{"seq": i, "t": 1000 * i, "p": [i, 1.4, 0], "q": [0, 0, 0, 1]} for i in range(1, 6)]
    out = proto.on_message("A", {"type": "poses", "items": items})
    assert {"type": "ack", "pose_seq": 5, "event_seq": 1} in out
    items2 = [{"seq": i, "t": 1000 * i, "p": [i, 1.4, 0], "q": [0, 0, 0, 1]} for i in range(3, 8)]
    out = proto.on_message("A", {"type": "poses", "items": items2})
    assert {"type": "ack", "pose_seq": 7, "event_seq": 1} in out
    proto.on_message("A", ev(2, "session_stop"))
    lines = (proto.store.session_dir(sid) / "pose.jsonl").read_text().splitlines()
    assert [json.loads(x)["seq"] for x in lines] == [1, 2, 3, 4, 5, 6, 7]
    assert json.loads(lines[0])["t"] == pytest.approx(501.0)  # 1000 мс телефона -> сервер 501 с
    assert proto.store.active() is None


def test_second_client_busy(proto):
    synced(proto, "A")
    synced(proto, "B")
    proto.on_message("A", ev(1, "session_start", session_kind="walk"))
    out = proto.on_message("B", ev(1, "session_start", session_kind="walk"))
    assert errors(out) == ["session_busy"]
    out = proto.on_message("B", {"type": "poses", "items": [{"seq": 1, "t": 1, "p": [0, 0, 0], "q": [0, 0, 0, 1]}]})
    assert errors(out) == ["not_owner"]
    assert proto.store.active().poses == 0


def test_requires_clock_sync(proto):
    proto.on_message("A", {"type": "hello", "client_id": "A"})
    out = proto.on_message("A", ev(1, "session_start", session_kind="walk"))
    assert errors(out) == ["no_clock_sync"] and proto.store.active() is None


def test_event_dedup_and_storage(proto):
    synced(proto, "A")
    proto.on_message("A", ev(1, "session_start", session_kind="walk"))
    sid = proto.store.active().id
    proto.on_message("A", ev(2, "origin", t=2000, p=[0, 1.3, 0]))
    proto.on_message("A", ev(2, "origin", t=2000, p=[0, 1.3, 0]))  # повтор после переподключения
    proto.on_message("A", ev(3, "label", t=2500, pose="sit"))
    proto.on_message("A", ev(4, "session_stop"))
    kinds = [json.loads(x)["kind"] for x in (proto.store.session_dir(sid) / "events.jsonl").read_text().splitlines()]
    assert kinds == ["session_start", "origin", "label", "session_stop"]


def test_unknown_event_kind_rejected(proto):
    synced(proto, "A")
    out = proto.on_message("A", ev(1, "explode"))
    assert errors(out) == ["bad_event"]


def test_poses_without_session_are_acked_and_dropped(proto):
    synced(proto, "A")
    out = proto.on_message("A", {"type": "poses", "items": [{"seq": 1, "t": 1, "p": [0, 0, 0], "q": [0, 0, 0, 1]}]})
    assert {"type": "ack", "pose_seq": 1, "event_seq": 0} in out
    assert errors(out) == ["no_session"]


def test_empty_session_with_duration(proto):
    synced(proto, "A")
    proto.on_message("A", ev(1, "session_start", session_kind="empty", duration_s=300))
    s = proto.store.active()
    assert s.kind == "empty" and s.duration_s == 300


def test_ws_endpoint(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    ks = KeyStore(tmp_path / "k.json")
    hub = LiveHub()
    ui = tmp_path / "ui"
    (ui / "device").mkdir(parents=True)
    app = create_app(hub, IngestService(ks, hub.on_packet), ui, store=store, recorder=Recorder(store),
                     default_device=lambda: DEV)
    with TestClient(app) as c, c.websocket_connect("/ws/capture") as ws:
        ws.send_json({"type": "hello", "client_id": "X"})
        first, second = ws.receive_json(), ws.receive_json()
        assert first["type"] == "state" and first["session"] is None
        assert second == {"type": "ack", "pose_seq": 0, "event_seq": 0}  # телефон узнаёт, что досылать
        ws.send_json({"type": "ping", "c0": 5})
        pong = ws.receive_json()
        assert pong["type"] == "pong" and pong["c0"] == 5
