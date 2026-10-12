import asyncio
import time

import numpy as np
import pytest

from wsn.live import LiveHub, ltf_amplitude
from wsn.protocol import TYPE_CSI, TYPE_TELEM, CsiFrame, Packet, Telemetry

MAC = "02:00:00:00:00:01"


def frame(length=256, data=None, rssi=-45):
    return CsiFrame(0, rssi, -92, 1, 0, data if data is not None else bytes(length))


def csi_packet(seq, frames):
    return Packet(TYPE_CSI, MAC, 1, seq, frames, None)


@pytest.mark.parametrize("length,part", [(128, "lltf"), (256, "lltf"), (256, "ht"), (384, "lltf"), (384, "ht")])
def test_amplitude_lengths(length, part):
    a = ltf_amplitude(bytes(length), part)
    assert a.shape == (64,) and a.dtype == np.float32


def test_amplitude_rejects_bad_lengths():
    assert ltf_amplitude(bytes(128), "ht") is None
    assert ltf_amplitude(bytes(200), "lltf") is None


def test_amplitude_order():
    buf = bytearray(128)
    buf[2], buf[3] = 3, 4  # поднесущая +1: imag=3, real=4
    a = ltf_amplitude(bytes(buf), "lltf")
    assert a[33] == pytest.approx(5.0)
    assert np.count_nonzero(a) == 1


def test_amplitude_negative_values():
    buf = bytearray(128)
    buf[64], buf[65] = (-3) & 0xFF, (-4) & 0xFF  # поднесущая −32 (первая отрицательная)
    a = ltf_amplitude(bytes(buf), "lltf")
    assert a[0] == pytest.approx(5.0)


def test_amplitude_ht_uses_second_ltf():
    buf = bytearray(256)
    buf[128 + 2], buf[128 + 3] = 6, 8
    assert ltf_amplitude(bytes(buf), "ht")[33] == pytest.approx(10.0)
    assert ltf_amplitude(bytes(buf), "lltf")[33] == 0


def test_csi_hz():
    hub = LiveHub()
    for i in range(200):
        hub.on_packet(csi_packet(i, [frame()]), i * 0.01)
    (d,) = hub.devices(now=1.99)
    assert d.device_id == MAC
    assert d.csi_hz == pytest.approx(100, rel=0.05)
    assert d.rssi == -45 and d.last_seen == pytest.approx(1.99)


def test_unknown_length_frame_skipped_for_amp_but_counted():
    hub = LiveHub()
    hub.on_packet(csi_packet(0, [frame(200)]), 0.0)
    (d,) = hub.devices()
    assert d.counters["frames"] == 1


def test_telemetry_stored():
    hub = LiveHub()
    t = Telemetry(90.0, 0.1, -50, 1, 2, 3, 1, 0, 2)
    hub.on_packet(Packet(TYPE_TELEM, MAC, 1, 0, [], t), 5.0)
    (d,) = hub.devices()
    assert d.telemetry == t


async def test_decimation():
    hub = LiveHub(decimate_hz=20.0)
    sub = hub.subscribe()
    for i in range(100):
        hub.on_packet(csi_packet(i, [frame()]), i * 0.01)
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())
    amps = [m for m in msgs if m["type"] == "amp"]
    assert 19 <= len(amps) <= 21
    assert len(amps[0]["amp"]) == 64 and amps[0]["device_id"] == MAC


async def test_slow_client_dropped():
    hub = LiveHub(decimate_hz=1000.0)
    slow = hub.subscribe()
    worst = 0.0
    for i in range(300):
        t0 = time.perf_counter()
        hub.on_packet(csi_packet(i, [frame()]), i * 0.01)
        worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.005
    assert slow.closed
    assert hub.subscriber_count() == 0
    # итератор медленного клиента завершается, а не висит
    items = [m async for m in slow]
    assert all(isinstance(m, dict) for m in items)


async def test_subscription_iterates_and_closes():
    hub = LiveHub(decimate_hz=1000.0)
    sub = hub.subscribe()
    hub.on_packet(csi_packet(0, [frame()]), 0.0)
    msg = await asyncio.wait_for(sub.__anext__(), 1)
    assert msg["type"] == "amp"
    sub.close()
    assert hub.subscriber_count() == 0
