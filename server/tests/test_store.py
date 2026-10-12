import json

import pytest

from wsn.store import SessionBusy, Store, read_csi_bin

DEV = "7c:4f:ad:bc:9b:54"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "wsn.db", tmp_path / "sessions")


def test_start_stop_list(store):
    s = store.start_session("walk", DEV, now=100.0)
    assert store.active().id == s.id and s.kind == "walk" and s.stopped is None
    store.stop_session(s.id, now=160.0)
    assert store.active() is None
    (info,) = store.list_sessions()
    assert info.id == s.id and info.started == 100.0 and info.stopped == 160.0
    assert info.started_wall > 1_600_000_000


def test_second_start_busy(store):
    store.start_session("walk", DEV, now=1.0)
    with pytest.raises(SessionBusy):
        store.start_session("empty", DEV, now=2.0)


def test_kind_validated(store):
    with pytest.raises(ValueError):
        store.start_session("party", DEV, now=1.0)


def test_csi_bin_roundtrip(store):
    s = store.start_session("walk", DEV, now=1.0)
    for i, buf in enumerate([b"a" * 10, b"b" * 1400, b"c"]):
        store.append_csi(10.0 + i * 0.01, buf)
    store.stop_session(s.id, now=11.0)
    recs = read_csi_bin(store.session_dir(s.id) / "csi.bin")
    assert [b for _, b in recs] == [b"a" * 10, b"b" * 1400, b"c"]
    assert [round(t, 6) for t, _ in recs] == [10.0, 10.01, 10.02]
    assert store.list_sessions()[0].csi_packets == 3


def test_append_without_session_ignored(store):
    store.append_csi(1.0, b"x")
    store.append_pose({"t": 1.0})
    assert store.list_sessions() == []


def test_poses_and_events_jsonl(store):
    s = store.start_session("walk", DEV, now=1.0)
    store.append_pose({"seq": 1, "t": 1.5, "p": [0, 1.4, 0]})
    store.append_event({"seq": 1, "t": 1.6, "kind": "origin"})
    store.stop_session(s.id, now=2.0)
    d = store.session_dir(s.id)
    assert json.loads((d / "pose.jsonl").read_text().splitlines()[0])["seq"] == 1
    assert json.loads((d / "events.jsonl").read_text().splitlines()[0])["kind"] == "origin"
    info = store.list_sessions()[0]
    assert (info.poses, info.events) == (1, 1)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["id"] == s.id and meta["device_id"] == DEV


def test_delete(store):
    s = store.start_session("walk", DEV, now=1.0)
    with pytest.raises(SessionBusy):
        store.delete_session(s.id)
    store.stop_session(s.id, now=2.0)
    store.delete_session(s.id)
    assert store.list_sessions() == [] and not store.session_dir(s.id).exists()


def test_active_session_survives_restart_as_stopped(tmp_path):
    st = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    s = st.start_session("walk", DEV, now=1.0)
    st.append_csi(1.5, b"x")
    st2 = Store(tmp_path / "wsn.db", tmp_path / "sessions")  # процесс api перезапущен
    assert st2.active() is None
    (info,) = st2.list_sessions()
    assert info.id == s.id and info.stopped is not None
