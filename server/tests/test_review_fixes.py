"""Regression tests for findings of the phase-1 whole-branch review."""

import pytest

from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub
from wsn.protocol import TYPE_CSI, TYPE_TELEM, CsiFrame, Packet, Telemetry, encode_packet

KEY = bytes(range(32))
MAC = "7c:4f:ad:bc:9b:54"
T = Telemetry(1.0, 0.0, -1, 1, 1, 1, 1, 0, 2)


def pkt(boot, seq, key=KEY, mac=MAC):
    return encode_packet(TYPE_TELEM, mac, boot, seq, key, telemetry=T)


def make(tmp_path, key=KEY):
    ks = KeyStore(tmp_path / "k.json")
    ks.set(MAC, key)
    return ks, IngestService(ks, lambda p, t: None, state_path=tmp_path / "boot_state.json")


# C1: replayed packet of an older boot must not displace the live boot
def test_old_boot_replay_does_not_block_live_stream(tmp_path):
    _, svc = make(tmp_path)
    assert svc.handle(pkt(222, 0), 1.0)
    assert svc.handle(pkt(111, 0), 1.1) is False
    assert svc.handle(pkt(222, 1), 1.2)
    assert svc.handle(pkt(222, 2), 1.3)


def test_old_boot_rejected_after_service_restart(tmp_path):
    ks, svc = make(tmp_path)
    assert svc.handle(pkt(5, 0), 1.0)
    svc2 = IngestService(ks, lambda p, t: None, state_path=tmp_path / "boot_state.json")
    assert svc2.handle(pkt(4, 99), 2.0) is False
    assert svc2.handle(pkt(6, 0), 2.1)


def test_boot_state_resets_when_key_rotates(tmp_path):
    ks, svc = make(tmp_path)
    assert svc.handle(pkt(50, 0), 1.0)
    new_key = bytes(32 * [7])
    ks.set(MAC, new_key)  # re-provisioning after flash erase: boot counter restarts at 1
    assert svc.handle(pkt(1, 0, key=new_key), 2.0)


# I1: unknown MACs are bounded
def test_unknown_devices_capped(tmp_path):
    _, svc = make(tmp_path)
    for i in range(100):
        svc.handle(pkt(1, 0, mac=f"02:00:00:00:00:{i:02x}"), float(i))
    assert len(svc.unknown_devices) == 32
    assert "02:00:00:00:00:63" in svc.unknown_devices
    assert "02:00:00:00:00:00" not in svc.unknown_devices


# I2: silent device must not look healthy
def test_devices_rate_decays_when_silent():
    hub = LiveHub()
    for i in range(200):
        hub.on_packet(Packet(TYPE_CSI, MAC, 1, i, [CsiFrame(0, -45, -92, 1, 0, bytes(256))], None), i * 0.01)
    (d,) = hub.devices(now=1.99)
    assert d.csi_hz == pytest.approx(100, rel=0.05) and d.age_s == pytest.approx(0.0)
    (d,) = hub.devices(now=10.0)
    assert d.csi_hz == 0.0 and d.age_s == pytest.approx(8.01)


# I5: broken key file keeps last good keys and is counted
def test_corrupt_key_file_keeps_last_good(tmp_path):
    path = tmp_path / "k.json"
    ks = KeyStore(path)
    ks.set(MAC, KEY)
    path.write_text("{not json")
    assert ks.get(MAC) == KEY
    assert ks.errors == 1
    assert ks.get(MAC) == KEY
    assert ks.errors == 1  # не перечитывает тот же битый файл на каждом пакете
