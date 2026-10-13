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


def test_motion_energy_constant_is_zero():
    from wsn.live import motion_energy
    assert motion_energy(np.full((100, 64), 20.0, dtype=np.float32)) == pytest.approx(0.0, abs=1e-6)


def test_motion_energy_unit_noise_is_one():
    from wsn.live import motion_energy
    rng = np.random.default_rng(1)
    assert motion_energy(20.0 + rng.normal(0, 1, (100, 64))) == pytest.approx(1.0, rel=0.1)


def test_motion_energy_ignores_null_subcarriers_and_short_windows():
    from wsn.live import motion_energy
    rng = np.random.default_rng(2)
    a = 20.0 + rng.normal(0, 1, (100, 64))
    a[:, :6] = 0.0  # защитные/DC поднесущие без сигнала
    assert motion_energy(a) == pytest.approx(1.0, rel=0.1)
    assert motion_energy(a[:1]) == 0.0


def noisy_frame(rng, sigma):
    iq = np.clip(np.round(rng.normal(20, sigma, 256)), -127, 127).astype(np.int8)
    return frame(data=iq.tobytes())


async def test_motion_rate_and_normalisation():
    hub = LiveHub(decimate_hz=1.0)  # мало amp-сообщений, чтобы очередь клиента не переполнилась
    sub = hub.subscribe()
    rng = np.random.default_rng(3)
    for i in range(1000):  # 10 с покоя при 100 Гц
        hub.on_packet(csi_packet(i, [noisy_frame(rng, 1.0)]), i * 0.01)
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())
    motion = [m for m in msgs if m["type"] == "motion"]
    assert 38 <= len(motion) <= 41  # 4 Гц
    gaps = np.diff([m["t"] for m in motion])
    assert gaps.min() >= 0.25 - 1e-9
    assert motion[-1]["device_id"] == MAC
    assert motion[-1]["energy"] == pytest.approx(1.0, rel=0.25)  # в покое ≈ 1


async def test_multilink_motion_and_amp_per_transmitter():
    from wsn.protocol import TYPE_CSI_SRC
    hub = LiveHub(decimate_hz=1.0)
    sub = hub.subscribe()
    rng = np.random.default_rng(4)
    AP, LINK = "02:00:00:00:a1:18", "98:b3:ef:02:da:28"

    def f(src, sigma):
        iq = np.clip(np.round(rng.normal(20, sigma, 256)), -127, 127).astype(np.int8)
        return CsiFrame(0, -45, -92, 1, 0, iq.tobytes(), src)

    for i in range(500):  # 5 с: точка доступа 100 Гц, дополнительный передатчик 20 Гц
        frames = [f(AP, 1.0)] + ([f(LINK, 1.0)] if i % 5 == 0 else [])
        hub.on_packet(Packet(TYPE_CSI_SRC, MAC, 1, i, frames, None), i * 0.01)
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())
    motion_src = {m["src"] for m in msgs if m["type"] == "motion"}
    assert motion_src == {AP, LINK}
    assert {m["primary"] for m in msgs if m["type"] == "motion" and m["src"] == AP} == {True}
    assert {m["primary"] for m in msgs if m["type"] == "motion" and m["src"] == LINK} == {False}
    assert {m["src"] for m in msgs if m["type"] == "amp"} == {AP}  # водопад — только основной канал
    (dev,) = hub.devices(now=5.0)
    assert dev.links[LINK]["hz"] == pytest.approx(20, rel=0.2) and dev.links[AP]["hz"] == pytest.approx(100, rel=0.1)



async def test_legacy_only_link_uses_lltf_and_ht_link_ignores_stray_legacy():
    from wsn.protocol import TYPE_CSI_SRC
    hub = LiveHub(decimate_hz=1.0)
    sub = hub.subscribe()
    rng = np.random.default_rng(5)
    AP, VAC = "02:00:00:00:a1:18", "02:00:00:00:b8:14"

    def f(src, n):
        iq = np.clip(np.round(rng.normal(20, 1.0, n)), -127, 127).astype(np.int8)
        return CsiFrame(0, -56, -92, 0 if n == 128 else 1, 0, iq.tobytes(), src)

    for i in range(500):  # 5 с: AP 100 Гц HT (+ редкие кадры 11g), пылесос 20 Гц только 11g
        frames = [f(AP, 256)] + ([f(VAC, 128)] if i % 5 == 0 else []) + ([f(AP, 128)] if i % 50 == 25 else [])
        hub.on_packet(Packet(TYPE_CSI_SRC, MAC, 1, i, frames, None), i * 0.01)
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())
    vac = [m for m in msgs if m["type"] == "motion" and m["src"] == VAC]
    ap = [m for m in msgs if m["type"] == "motion" and m["src"] == AP]
    assert len(vac) >= 15 and all(m["raw"] > 0 for m in vac)
    assert len(ap) >= 18  # редкие L-LTF у HT-линии не сбрасывают её окно


async def test_breathing_message_for_still_breathing_link_only():
    from wsn.protocol import TYPE_CSI_SRC
    hub = LiveHub(decimate_hz=1.0)
    sub = hub.subscribe()
    rng = np.random.default_rng(6)
    BR, QUIET = "02:00:00:00:e5:57", "98:b3:ef:02:da:28"
    theta = rng.uniform(0, 2 * np.pi, 64)

    def f(src, t, depth):
        a = 30 * (1 + depth * np.sin(2 * np.pi * 0.25 * t) * (np.arange(64) % 2))  # 15 вдохов/мин на половине поднесущих
        iq = np.zeros((128, 2))
        iq[64:, 0], iq[64:, 1] = a * np.sin(theta), a * np.cos(theta)  # HT-LTF: (imag, real)
        iq += rng.normal(0, 0.4, iq.shape)
        return CsiFrame(int(t * 1e6), -60, -92, 1, 0, np.clip(np.round(iq), -127, 127).astype(np.int8).tobytes(), src)

    msgs = []
    for i in range(2000):  # 100 с по 20 Гц
        t = i * 0.05
        hub.on_packet(Packet(TYPE_CSI_SRC, MAC, 1, i, [f(BR, t, 0.06), f(QUIET, t, 0.0)], None), t)
        while not sub.queue.empty():  # читаем по ходу, чтобы очередь подписчика не переполнилась
            msgs.append(sub.queue.get_nowait())
    br = [m for m in msgs if m["type"] == "breath" and m["src"] == BR]
    quiet = [m for m in msgs if m["type"] == "breath" and m["src"] == QUIET]
    assert br and br[-1]["ok"] and abs(br[-1]["bpm"] - 15) <= 1.0, br
    assert quiet and not any(m["ok"] for m in quiet), quiet


def test_moving_mean_matches_convolution():
    from wsn.breath import _moving_mean
    G = np.random.default_rng(1).normal(size=(300, 3))
    w = 61
    ref = np.stack([np.convolve(np.pad(G[:, k], w // 2, mode="edge"), np.ones(w) / w, "valid") for k in range(3)], 1)
    assert np.allclose(_moving_mean(G, w), ref)

def test_motion_energy_normalize_removes_gain_jumps():
    from wsn.live import motion_energy
    base = np.linspace(10, 40, 64)
    gains = np.repeat([1.0, 1.6, 0.7, 1.3], 25)[:, None]  # скачки АРУ/мощности без изменения канала
    a = base[None] * gains
    assert motion_energy(a) > 1.0
    assert motion_energy(a, normalize=True) < 1e-3



def test_mirror_delivers_and_never_raises(tmp_path):
    import socket
    from wsn.mirror import DatagramMirror
    m = DatagramMirror(tmp_path / "csi.sock")
    m.send(b"nobody listens")  # нет слушателя — тихо отбрасываем
    assert m.dropped == 1
    rx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    rx.bind(str(tmp_path / "csi.sock"))
    m.send(b"pkt")
    assert rx.recv(64) == b"pkt" and m.sent == 1
    rx.close(); m.close()



async def test_board_prediction_is_broadcast():
    from wsn.protocol import TYPE_PRED, Prediction
    hub = LiveHub(decimate_hz=1.0)
    sub = hub.subscribe()
    r = Prediction(7, 80, 8, (0.1, 0.7, 0.15, 0.05), (0.6, 0.2, 0.1, 0.1), 0.9)
    hub.on_packet(Packet(TYPE_PRED, MAC, 1, 3, [], None, r), 5.0)
    m = sub.queue.get_nowait()
    assert m["type"] == "pred" and m["source"] == "board" and m["zone"] == "зал" and m["activity"] == "машет"
    assert m["model"] == "плата v7" and m["moving_p"] == 0.9


def test_diag_is_exposed_per_device():
    from wsn.protocol import TYPE_DIAG, Diag
    hub = LiveHub()
    d = Diag(5, 120, 0x40370000, 5, 1, ((4, 6), (1, 1)), "LoadProhibited")
    hub.on_packet(Packet(TYPE_DIAG, MAC, 9, 0, diag=d), 1.0)
    (st,) = hub.devices(now=1.0)
    assert st.boot_id == 9 and st.diag["last"] == ["PANIC/6", "POWERON/1"]
    assert st.diag["panic"]["msg"] == "LoadProhibited" and st.diag["prev_uptime_s"] == 120


def test_log_lines_are_kept_per_device():
    from wsn.live import LOG_KEEP
    from wsn.protocol import TYPE_LOG
    hub = LiveHub()
    for i in range(LOG_KEEP + 5):
        hub.on_packet(Packet(TYPE_LOG, MAC, 9, i, log=f"line {i}"), 1.0)
    (st,) = hub.devices(now=1.0)
    assert len(st.log) == LOG_KEEP and st.log[-1].endswith(f"line {LOG_KEEP + 4}")
