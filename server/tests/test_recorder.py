import httpx
import pytest

from wsn.app import create_app
from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub
from wsn.protocol import TYPE_CSI, CsiFrame, encode_packet
from wsn.recorder import Recorder
from wsn.store import Store, read_csi_bin

KEY = bytes(range(32))
DEV = "7c:4f:ad:bc:9b:54"
OTHER = "02:00:00:00:00:01"


def pkt(seq, mac=DEV):
    return encode_packet(TYPE_CSI, mac, 1, seq, KEY, frames=[CsiFrame(0, -40, -92, 1, 0, bytes(256))])


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    rec = Recorder(store, idle_timeout_s=120.0)
    ks = KeyStore(tmp_path / "k.json")
    ks.set(DEV, KEY)
    ks.set(OTHER, KEY)
    hub = LiveHub()
    ingest = IngestService(ks, hub.on_packet, on_raw=rec.on_datagram)
    ui = tmp_path / "ui"
    (ui / "device").mkdir(parents=True)
    return store, rec, ingest, create_app(hub, ingest, ui, store=store, recorder=rec)


def test_records_only_active_device_and_session(rig):
    store, rec, ingest, _ = rig
    ingest.handle(pkt(0), 1.0)  # вне сессии
    s = store.start_session("walk", DEV, now=2.0)
    ingest.handle(pkt(1), 2.1)
    ingest.handle(pkt(0, mac=OTHER), 2.2)  # чужое устройство
    ingest.handle(pkt(1), 2.3)  # повтор — отвергнут ingest'ом, не пишется
    store.stop_session(s.id, now=3.0)
    recs = read_csi_bin(store.session_dir(s.id) / "csi.bin")
    assert len(recs) == 1 and recs[0][1] == pkt(1)


def test_idle_timeout(rig):
    store, rec, _, _ = rig
    s = store.start_session("walk", DEV, now=100.0)
    rec.tick(now=160.0)
    assert store.active() is not None
    rec.note_activity(150.0)
    rec.tick(now=269.0)
    assert store.active() is not None
    rec.tick(now=271.0)
    assert store.active() is None
    assert store.list_sessions()[0].id == s.id


def test_empty_session_runs_for_its_duration_without_phone(rig):
    store, rec, _, _ = rig
    store.start_session("empty", DEV, now=0.0, duration_s=600.0)
    rec.tick(now=300.0)  # телефон ушёл вместе с человеком — бездействие не останавливает
    assert store.active() is not None
    rec.tick(now=601.0)
    assert store.active() is None


async def test_rest_sessions(rig):
    store, _, _, app = rig
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/sessions", json={"kind": "walk", "device_id": DEV})
        assert r.status_code == 200
        sid = r.json()["id"]
        r2 = await c.post("/api/sessions", json={"kind": "empty", "device_id": DEV})
        assert r2.status_code == 409
        assert (await c.delete(f"/api/sessions/{sid}")).status_code == 409
        assert (await c.post(f"/api/sessions/{sid}/stop")).status_code == 200
        lst = (await c.get("/api/sessions")).json()
        assert [x["id"] for x in lst] == [sid] and lst[0]["stopped"] is not None
        assert (await c.delete(f"/api/sessions/{sid}")).status_code == 200
        assert (await c.get("/api/sessions")).json() == []
        assert (await c.post("/api/sessions/nope/stop")).status_code == 404


def test_static_session_runs_for_its_duration(rig):
    store, rec, _, _ = rig
    s = store.start_session("static", DEV, now=0.0)
    assert s.duration_s == 1200.0  # по умолчанию 20 мин
    rec.tick(now=600.0)  # люди неподвижны, телефона нет — запись продолжается
    assert store.active() is not None
    rec.tick(now=1201.0)
    assert store.active() is None


async def test_occupancy_labels(rig):
    import json
    store, _, _, app = rig
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {"zones": {"спальня": {"count": 2, "activity": "сон"}, "зал": {"count": 1, "activity": "сидит"}},
                "note": "начало"}
        assert (await c.post("/api/labels", json=body)).status_code == 409  # нет записи — некуда писать
        sid = (await c.post("/api/sessions", json={"kind": "static", "device_id": DEV, "duration_s": 60})).json()["id"]
        r = await c.post("/api/labels", json=body)
        assert r.status_code == 200 and r.json()["session"] == sid
        bad = await c.post("/api/labels", json={"zones": {"зал": {"count": -1}}})
        assert bad.status_code == 422
        await c.post(f"/api/sessions/{sid}/stop")
    evs = [json.loads(x) for x in (store.session_dir(sid) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    (lab,) = [e for e in evs if e["kind"] == "occupancy"]
    assert lab["zones"]["спальня"] == {"count": 2, "activity": "сон"} and lab["note"] == "начало"
    assert isinstance(lab["t"], float)
    assert "script" not in lab and "subject" not in lab


async def test_scenario_labels(rig, tmp_path):
    import json
    store, _, _, app = rig
    (tmp_path / "ui" / "scenario").mkdir()
    (tmp_path / "ui" / "scenario" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        sid = (await c.post("/api/sessions", json={"kind": "static", "device_id": DEV, "duration_s": 60})).json()["id"]
        body = {"zones": {"кухня": {"count": 1, "activity": "машет у плиты"}}, "note": "сценарий: кухня машет",
                "script": True, "subject": {"zone": "кухня", "act": "машет", "place": "у плиты"}}
        assert (await c.post("/api/labels", json=body)).status_code == 200
        bad = dict(body, subject={"zone": "кухня", "act": "танцует"})
        assert (await c.post("/api/labels", json=bad)).status_code == 422
        assert (await c.get("/scenario")).status_code == 200
        await c.post(f"/api/sessions/{sid}/stop")
    evs = [json.loads(x) for x in (store.session_dir(sid) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    (lab,) = [e for e in evs if e["kind"] == "occupancy"]
    assert lab["script"] is True and lab["subject"] == {"zone": "кухня", "act": "машет", "place": "у плиты"}
