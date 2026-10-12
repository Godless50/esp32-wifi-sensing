import math

import pytest

from wsn.plan import RoomTracker, build_plan
from wsn.store import Store

DEV = "7c:4f:ad:bc:9b:54"
ROOM = [(1, 1), (3, 1), (3, -1), (1, -1)]  # «мировые» координаты: плата (0,0), роутер (4,0)


def to_xr(w, theta, tx, ty, h=1.4):
    """World floor point -> phone XR coords of a session whose frame is rotated by theta and shifted."""
    x = w[0] * math.cos(theta) - w[1] * math.sin(theta) + tx
    y = w[0] * math.sin(theta) + w[1] * math.cos(theta) + ty
    return [x, h, -y]  # floor (x, y) == XR (x, -z)


def record(store, theta, tx, ty, t0, with_router=True, room_name="Кухня"):
    s = store.start_session("walk", DEV, now=t0)
    seq = iter(range(1, 100))
    ev = lambda kind, **kw: store.append_event({"seq": next(seq), "t": t0 + 1, "kind": kind, **kw})  # noqa: E731
    ev("session_start", session_kind="walk")
    ev("origin", p=to_xr((0, 0), theta, tx, ty))
    if with_router:
        ev("router", p=to_xr((4, 0), theta, tx, ty))
    ev("room_new", name=room_name)
    for c in ROOM:
        ev("corner", p=to_xr(c, theta, tx, ty, h=0.0), source="hit")
    ev("room_close")
    ev("object", p=to_xr((2, 0), theta, tx, ty, h=0.0), type="стол", size=[1.2, 0.8, 0.75])
    for i in range(50):  # 10 Гц, 5 с: идём вдоль оси
        store.append_pose({"seq": i + 1, "t": t0 + 2 + i * 0.1, "p": to_xr((i * 0.05, 0), theta, tx, ty), "q": [0, 0, 0, 1]})
    ev("session_stop")
    store.stop_session(s.id, now=t0 + 10)
    return s.id


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "wsn.db", tmp_path / "sessions")


def test_two_sessions_same_world(store):
    record(store, theta=0.7, tx=2.0, ty=-1.0, t0=100)
    record(store, theta=-2.1, tx=-0.5, ty=3.0, t0=200, room_name="Кухня")
    plan = build_plan(store)
    (room,) = plan["rooms"]  # то же имя — последнее определение
    assert room["name"] == "Кухня"
    for got, want in zip(room["polygon"], ROOM):
        assert got == pytest.approx(want, abs=0.01)
    assert plan["router"] == pytest.approx([4.0, 0.0], abs=0.01)
    assert plan["board"] == [0.0, 0.0]
    assert plan["objects"][-1]["xy"] == pytest.approx([2.0, 0.0], abs=0.01)
    assert plan["skipped"] == {}


def test_trajectory_decimated_to_5hz(store):
    sid = record(store, theta=0.3, tx=0, ty=0, t0=100)
    traj = build_plan(store)["trajectories"][sid]
    assert 24 <= len(traj) <= 26
    assert traj[-1][1] == pytest.approx(2.45, abs=0.06)


def test_session_without_router_skipped(store):
    sid = record(store, theta=0, tx=0, ty=0, t0=100, with_router=False)
    plan = build_plan(store)
    assert plan["rooms"] == [] and "router" in plan["skipped"][sid]
    assert store.get(sid).plan_ok is False and "router" in store.get(sid).plan_reason


def test_room_tracker_validates_live():
    rt = RoomTracker()
    assert rt.check("origin", {"p": [0, 1.4, 0]}) is None
    assert rt.check("router", {"p": [0.1, 1.4, -0.1]})[0] == "bad_router"
    assert rt.check("router", {"p": [0, 1.4, -3]}) is None
    rt.check("room_new", {"name": "A"})
    rt.check("corner", {"p": [0, 0, 0]})
    rt.check("corner", {"p": [1, 0, 0]})
    code, msg = rt.check("room_close", {})
    assert code == "bad_polygon" and "too few" in msg
    rt.check("corner", {"p": [1, 0, -1]})
    assert rt.check("room_close", {}) is None
    assert rt.check("corner", {"p": [0, 0, 0]})[0] == "no_room"  # угол без «новой комнаты»
