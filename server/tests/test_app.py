import httpx
import pytest
from starlette.testclient import TestClient

from wsn.app import create_app
from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub
from wsn.protocol import TYPE_CSI, CsiFrame, encode_packet

KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"


@pytest.fixture
def stack(tmp_path):
    ui = tmp_path / "ui"
    (ui / "device").mkdir(parents=True)
    (ui / "device" / "index.html").write_text("<!doctype html><title>device</title>")
    (ui / "capture").mkdir()
    (ui / "capture" / "probe.html").write_text("<!doctype html><title>probe</title>")
    (ui / "capture" / "index.html").write_text("<!doctype html><title>capture</title>")
    (ui / "scene").mkdir()
    (ui / "scene" / "index.html").write_text("<!doctype html><title>scene</title>")
    (ui / "home").mkdir()
    (ui / "home" / "index.html").write_text("<!doctype html><title>home</title>")
    (ui / "data").mkdir()
    (ui / "data" / "index.html").write_text("<!doctype html><title>data</title>")
    ks = KeyStore(tmp_path / "k.json")
    ks.set(MAC, KEY)
    hub = LiveHub(decimate_hz=1000.0)
    ingest = IngestService(ks, hub.on_packet)
    return hub, ingest, create_app(hub, ingest, ui)


def _pkt(seq):
    return encode_packet(TYPE_CSI, MAC, 1, seq, KEY, frames=[CsiFrame(0, -40, -92, 1, 0, bytes(256))])


async def test_api_devices(stack):
    hub, ingest, app = stack
    ingest.handle(_pkt(0), 1.0)
    ingest.handle(encode_packet(TYPE_CSI, "02:00:00:00:00:99", 1, 0, KEY, frames=[]), 1.0)  # неизвестный MAC
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/devices")
    body = r.json()
    assert r.status_code == 200
    assert [d["device_id"] for d in body["devices"]] == [MAC]
    assert body["devices"][0]["rssi"] == -40
    assert body["counters"]["accepted"] == 1
    assert body["unknown_devices"] == ["02:00:00:00:00:99"]


async def test_device_page(stack):
    _, _, app = stack
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/device")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("url,marker", [("/capture", "capture"), ("/", "home"), ("/scene3d", "scene"), ("/data", "data")])
async def test_pages(stack, url, marker):
    _, _, app = stack
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(url)
    assert r.status_code == 200 and marker in r.text


async def test_capture_probe_page(stack):
    _, _, app = stack
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/capture/probe")
    assert r.status_code == 200 and "probe" in r.text


def test_ws_live_receives_amp(stack):
    hub, ingest, app = stack
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        ingest.handle(_pkt(0), 1.0)
        msg = ws.receive_json()
        assert msg["type"] == "amp" and msg["device_id"] == MAC
