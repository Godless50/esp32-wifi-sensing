"""Cross-check: packets encoded by firmware/components/wsn_proto (C) parse identically in Python."""

from pathlib import Path

import pytest

from wsn.protocol import TYPE_CSI, TYPE_TELEM, CsiFrame, Telemetry, encode_packet, parse_packet

GOLDEN = Path(__file__).parent / "golden" / "c_packets.hex"
KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"


def _lines():
    if not GOLDEN.exists():
        pytest.fail(f"{GOLDEN} missing: run make -C firmware/components/wsn_proto/test golden")
    return [bytes.fromhex(x) for x in GOLDEN.read_text().split()]


def test_c_csi_packet():
    buf = _lines()[0]
    p = parse_packet(buf, KEY)
    data = bytes(j & 0xFF for j in range(256))
    expected = [CsiFrame(1_000_000 + i, -45, -92, 1, 0, data) for i in range(5)]
    assert (p.type, p.device_id, p.boot_id, p.seq) == (TYPE_CSI, MAC, 0xDEADBEEF, 7)
    assert p.frames == expected
    assert buf == encode_packet(TYPE_CSI, MAC, 0xDEADBEEF, 7, KEY, frames=expected)


def test_c_telem_packet():
    buf = _lines()[1]
    p = parse_packet(buf, KEY)
    assert (p.type, p.seq) == (TYPE_TELEM, 8)
    t = p.telemetry
    assert t.csi_hz == pytest.approx(98.5) and t.drop_ratio == pytest.approx(0.02)
    assert (t.rssi_avg, t.heap_free, t.psram_free, t.uptime_s, t.fw_ver, t.model_ver, t.wifi_state) == (
        -44, 120_000, 8_000_000, 3600, 1, 0, 2)
    assert buf == encode_packet(TYPE_TELEM, MAC, 0xDEADBEEF, 8, KEY, telemetry=Telemetry(
        t.csi_hz, t.drop_ratio, -44, 120_000, 8_000_000, 3600, 1, 0, 2))
