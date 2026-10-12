import os

import pytest

from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.protocol import TYPE_TELEM, Telemetry, encode_packet

KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"
T = Telemetry(1.0, 0.0, -1, 1, 1, 1, 1, 0, 2)


def pkt(seq, boot=1, key=KEY, mac=MAC):
    return encode_packet(TYPE_TELEM, mac, boot, seq, key, telemetry=T)


@pytest.fixture
def svc(tmp_path):
    ks = KeyStore(tmp_path / "k.json")
    ks.set(MAC, KEY)
    got = []
    s = IngestService(ks, lambda p, t: got.append((p, t)))
    s.got = got
    return s


def test_accepts_valid(svc):
    assert svc.handle(pkt(0), 1.0) is True
    assert len(svc.got) == 1 and svc.got[0][1] == 1.0
    assert svc.counters["accepted"] == 1


def test_unknown_device(svc):
    assert svc.handle(pkt(0, mac="02:00:00:00:00:99"), 1.0) is False
    assert svc.counters["rejected_unknown"] == 1
    assert "02:00:00:00:00:99" in svc.unknown_devices
    assert svc.got == []


def test_hmac_and_malformed(svc):
    assert svc.handle(pkt(0, key=bytes(32)), 1.0) is False
    assert svc.counters["rejected_hmac"] == 1
    assert svc.handle(os.urandom(5), 1.0) is False
    assert svc.handle(b"XXXX" + os.urandom(60), 1.0) is False
    assert svc.counters["rejected_malformed"] == 2


def test_replay_same_boot(svc):
    assert svc.handle(pkt(5), 1.0)
    assert svc.handle(pkt(5), 1.1) is False
    assert svc.handle(pkt(4), 1.2) is False
    assert svc.counters["rejected_replay"] == 2


def test_reboot_new_boot_id(svc):
    assert svc.handle(pkt(100, boot=0xA), 1.0)
    assert svc.handle(pkt(0, boot=0xB), 2.0)
    assert svc.handle(pkt(101, boot=0xA), 3.0) is False
    assert svc.counters["rejected_replay"] == 1


def test_gap_allowed(svc):
    assert svc.handle(pkt(1), 1.0)
    assert svc.handle(pkt(50), 1.1)


def test_datagram_received_does_not_raise_on_callback_error(tmp_path):
    ks = KeyStore(tmp_path / "k.json")
    ks.set(MAC, KEY)

    def boom(p, t):
        raise RuntimeError("consumer bug")

    s = IngestService(ks, boom)
    s.datagram_received(pkt(0), ("127.0.0.1", 1))  # не должно уронить цикл приёма
    assert s.counters["callback_errors"] == 1
