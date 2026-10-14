"""Product path: boards marked by the phone, plan from those marks, map driven by the flat's model, status API."""

from starlette.testclient import TestClient

from wsn.app import create_app
from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub
from wsn.person import PersonTracker
from wsn.plan import RoomTracker, build_plan, frame_marks
from wsn.recorder import Recorder
from wsn.store import Store

B1, B2, AP = "02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:aa"


def ar(x, y):
    return [float(x), 1.2, -float(y)]


def walk(store: Store, marks, rooms, t0=100.0) -> str:
    info = store.start_session("walk", B1, t0)
    t = t0
    for mac, role, xy in marks:
        t += 1
        store.append_event({"seq": None, "t": t, "kind": "device", "mac": mac, "role": role, "p": ar(*xy)})
    for name, poly in rooms:
        t += 1
        store.append_event({"seq": None, "t": t, "kind": "room_new", "name": name})
        for x, y in poly:
            t += 1
            store.append_event({"seq": None, "t": t, "kind": "corner", "p": ar(x, y)})
        store.append_event({"seq": None, "t": t, "kind": "room_close"})
    for i in range(50):
        store.append_pose({"seq": i + 1, "t": t + i * 0.2, "p": ar(1 + i * 0.02, 1), "q": [0, 0, 0, 1]})
    store.stop_session(info.id, t + 20)
    return info.id


ROOM_A = [[0, 0], [3, 0], [3, 3], [0, 3]]
ROOM_B = [[3, 0], [6, 0], [6, 3], [3, 3]]


def test_plan_from_device_marks(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    sid = walk(store, [(B1, "board", (0, 0)), (AP, "ap", (5, 0)), (B2, "board", (5.5, 2.5))],
               [("A", ROOM_A), ("B", ROOM_B)])
    plan = build_plan(store)
    assert {r["name"] for r in plan["rooms"]} == {"A", "B"}
    assert [b["device_id"] for b in plan["boards"]] == [B1, B2]
    b2 = plan["boards"][1]["xy"]
    assert abs(b2[0] - 5.5) < 1e-6 and abs(b2[1] - 2.5) < 1e-6  # ось x — на точку доступа
    ap = [o for o in plan["objects"] if o.get("mac") == AP]
    assert len(ap) == 1 and abs(ap[0]["xy"][0] - 5) < 1e-6
    assert plan["router"] == [5.0, 0.0]
    assert len(plan["trajectories"][sid]) >= 40


def test_axis_falls_back_when_board_sits_at_the_router(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    walk(store, [(B1, "board", (0, 0)), (AP, "ap", (0.2, 0)), (B2, "board", (4, 0))], [("A", ROOM_A)])
    plan = build_plan(store)
    assert plan["skipped"] == {}
    assert abs(plan["boards"][1]["xy"][0] - 4) < 1e-6 and abs(plan["boards"][1]["xy"][1]) < 1e-6
    assert plan["router"] is None  # ось построена на вторую плату: места роутера на плане нет


def test_frame_marks_prefers_explicit_marks():
    ev = [{"kind": "device", "mac": B1, "role": "board", "p": ar(1, 1)},
          {"kind": "origin", "p": ar(0, 0)}, {"kind": "router", "p": ar(3, 0)}]
    o, r = frame_marks(ev)
    assert o["kind"] == "origin" and r["kind"] == "router"


def test_room_tracker_validates_device_marks():
    tr = RoomTracker()
    assert tr.check("device", {"p": ar(0, 0), "mac": B1}) is not None  # без роли
    assert tr.check("device", {"p": ar(0, 0), "mac": B1, "role": "board"}) is None
    assert tr.check("device", {"p": ar(3, 0), "mac": AP, "role": "ap"}) is None
    assert tr.origin == ar(0, 0) and tr.router == ar(3, 0)


def plan2():
    return {"rooms": [{"name": "A", "polygon": ROOM_A}, {"name": "B", "polygon": ROOM_B}], "zones": [],
            "objects": [], "board": [0, 0], "boards": [{"device_id": B1, "xy": [0.5, 0.5]},
                                                       {"device_id": B2, "xy": [5.5, 2.5]}], "router": None}


def pred(t, probs, moving=0.9, empty=0.0):
    return {"type": "pred", "source": "model", "device_id": "", "t": t, "zone": max(probs, key=probs.get),
            "zone_p": max(probs.values()), "probs": probs, "moving_p": moving, "empty_p": empty,
            "activity": "ходит", "activity_p": moving, "links_live": 4, "links_total": 4}


def test_rooms_follow_the_flat_model():
    out = []
    pt = PersonTracker(publish=out.append)
    pt.set_plan(plan2())
    for i in range(10):
        pt.on_message(pred(100.0 + i, {"A": 0.9, "B": 0.1}))
    rooms = [m for m in out if m["type"] == "rooms"][-1]
    st = {r["name"]: r for r in rooms["rooms"]}
    assert rooms["source"] == "model"
    assert st["A"]["state"] == "движение" and st["B"]["state"] == "тихо"
    for i in range(10):
        pt.on_message(pred(110.0 + i, {"A": 0.1, "B": 0.9}))
    st = {r["name"]: r for r in [m for m in out if m["type"] == "rooms"][-1]["rooms"]}
    assert st["B"]["state"] == "движение" and st["A"]["state"] == "тихо"


def test_nobody_home_from_the_model():
    out = []
    pt = PersonTracker(publish=out.append)
    pt.set_plan(plan2())
    for i in range(30):
        pt.on_message(pred(200.0 + i, {"A": 0.5, "B": 0.5}, moving=0.05, empty=0.95))
    rooms = [m for m in out if m["type"] == "rooms"][-1]
    assert rooms["nobody_home"] is True
    assert all(r["state"] == "тихо" for r in rooms["rooms"])


def test_model_needs_most_of_its_links():
    out = []
    pt = PersonTracker(publish=out.append)
    pt.set_plan(plan2())
    m = pred(300.0, {"A": 0.9, "B": 0.1})
    m["links_live"], m["links_total"] = 1, 4  # 3 из 4 линий молчат: ответ модели — не свидетельство
    pt.on_message(m)
    assert not pt.model_fresh(300.0)
    m = pred(301.0, {"A": 0.9, "B": 0.1})
    m["links_live"], m["links_total"] = 1, 1  # одна плата — одна линия: годится
    pt.on_message(m)
    assert pt.model_fresh(301.0)


def test_status_and_capture_devices(tmp_path):
    ks = KeyStore(tmp_path / "keys.json")
    ks.set(B1, bytes(32))
    hub = LiveHub()
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    app = create_app(hub, IngestService(ks, hub.on_packet), tmp_path, store=store, recorder=Recorder(store))
    with TestClient(app) as c:
        st = c.get("/api/status").json()
        assert st["boards"] == [] and st["walks"] == [] and st["model"] is None
        assert c.get("/api/model").json() == {"model": None, "training": None}
        with c.websocket_connect("/ws/capture") as ws:
            ws.send_json({"type": "hello", "client_id": "t"})
            state = ws.receive_json()
            assert state["type"] == "state" and state["devices"] == []


def test_dead_link_is_zero_not_an_extreme_input():
    import numpy as np
    from wsn.zonemodel import NF, standardisation, standardize
    rng = np.random.default_rng(0)
    X = np.zeros((50, 2, NF))
    X[:, :, :-1] = rng.normal(-3, 0.01, (50, 2, NF - 1))
    X[:, :, -1] = 1.0  # обе линии живы во всех окнах: у alive разброс 0
    mu, sd = standardisation(X)
    dead = X[:3].copy()
    dead[:, 1] = 0.0  # вторая линия умерла
    Z = standardize(dead, mu, sd).reshape(3, 2, NF)
    assert np.all(Z[:, 1] == 0.0)
    assert np.abs(Z).max() < 50


def test_interrupted_walk_keeps_its_length(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    info = store.start_session("walk", B1, 100.0)
    for i in range(10):
        store.append_pose({"seq": i + 1, "t": 100.0 + i * 30, "p": ar(1, 1), "q": [0, 0, 0, 1]})
    store.append_event({"seq": None, "t": 395.0, "kind": "step", "name": "x"})
    del store  # сервер перезапустили посреди записи
    st2 = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    s = next(x for x in st2.list_sessions() if x.id == info.id)
    assert s.stopped == 395.0 and s.plan_reason == "interrupted"


def test_phone_ping_keeps_the_walk_alive_without_poses(tmp_path):
    from wsn.capture import CaptureProtocol
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    rec = Recorder(store)
    now = [1000.0]
    cap = CaptureProtocol(store, rec, lambda: B1, clock=lambda: now[0])
    cap.on_message("ph", {"type": "hello", "client_id": "ph"})
    cap.on_message("ph", {"type": "sync", "c0": 0.0, "s": 1000.0, "c1": 0.0})
    cap.on_message("ph", {"type": "event", "seq": 1, "t": 0.0, "kind": "session_start", "session_kind": "walk"})
    for _ in range(40):  # 20 минут: телефон в кармане, поз нет, только ping каждые 30 с
        now[0] += 30
        cap.on_message("ph", {"type": "ping", "c0": 0.0})
        rec.tick(now[0])
    assert store.active() is not None
    now[0] += rec.idle_timeout_s + 10  # телефон пропал совсем
    rec.tick(now[0])
    assert store.active() is None
