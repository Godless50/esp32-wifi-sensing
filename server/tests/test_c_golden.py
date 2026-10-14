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


def test_c_csi_src_packet():
    from wsn.protocol import TYPE_CSI_SRC
    buf = _lines()[2]
    p = parse_packet(buf, KEY)
    data = bytes(j & 0xFF for j in range(256))
    expected = [CsiFrame(1_000_000, -45, -92, 0, 0, data[:128], src="98:b3:ef:02:da:28"),
                CsiFrame(1_000_001, -45, -92, 1, 0, data, src="98:b3:ef:02:da:29")]
    assert (p.type, p.seq) == (TYPE_CSI_SRC, 9)
    assert p.frames == expected
    assert buf == encode_packet(TYPE_CSI_SRC, MAC, 0xDEADBEEF, 9, KEY, frames=expected)


def test_c_pred_packet():
    from wsn.protocol import TYPE_PRED, Prediction
    buf = _lines()[3]
    p = parse_packet(buf, KEY)
    assert (p.type, p.seq) == (TYPE_PRED, 10)
    r = p.prediction
    assert (r.model_ver, r.infer_ms, r.links_live) == (1791870150, 87, 8)
    assert [round(x * 255) for x in r.act] == [10, 200, 40, 5] and [round(x * 255) for x in r.zone] == [180, 50, 20, 5]
    assert round(r.move * 255) == 230
    assert buf == encode_packet(TYPE_PRED, MAC, 0xDEADBEEF, 10, KEY, prediction=r)


def test_c_model_ack_packet():
    from wsn.protocol import TYPE_MODEL_ACK, ModelAck
    p = parse_packet(_lines()[4], KEY)
    assert (p.type, p.seq) == (TYPE_MODEL_ACK, 11)
    assert p.model_ack == ModelAck(1791871129, 2048, 1, 1, 0)


def test_py_model_chunk_fixture_is_current():
    """The C parser test reads this fixture; it must stay what encode_model_chunk produces."""
    from wsn.protocol import encode_model_chunk
    fx = (Path(__file__).parent / "golden" / "py_model_chunk.hex").read_text().split()[0]
    assert encode_model_chunk(MAC, 0xDEADBEEF, 5, KEY, 1791871129, 3000, 1024, bytes(range(100))).hex() == fx


def test_c_diag_packet():
    from wsn.protocol import TYPE_DIAG, Diag
    buf = _lines()[5]
    p = parse_packet(buf, KEY)
    assert (p.type, p.seq) == (TYPE_DIAG, 12)
    d = p.diag
    assert d == Diag(42, 196, 0x4037A1B2, 5, 0, ((4, 6), (3, 0x86), (1, 1)), "Cache error", (0x10, 0x0D), 1, 7,
                     0x40380A0C, (0x4200C54C, 0x4200F6E3))
    assert buf == encode_packet(TYPE_DIAG, MAC, 0xDEADBEEF, 12, KEY, diag=d)
    s = d.summary()
    assert s["last"] == ["PANIC/6", "SW/6u", "POWERON/1"]
    assert s["panic"] == {"kind": "FAULT", "pc": "0x4037a1b2", "core": 0, "msg": "Cache error"}
    assert s["rom_reset"] == ["RTC_WDT_RTC", "RTC_WDT_CPU"]
    assert s["early"] == {"kind": "interrupt", "cause": "cache error", "pc": "0x40380a0c"}
    assert s["backtrace"] == ["0x4200c54c", "0x4200f6e3"]


def test_c_log_packet():
    from wsn.protocol import TYPE_LOG
    buf = _lines()[6]
    p = parse_packet(buf, KEY)
    assert (p.type, p.seq, p.log) == (TYPE_LOG, 13, "romif +0x128 3fc9a000->00000000")
    assert buf == encode_packet(TYPE_LOG, MAC, 0xDEADBEEF, 13, KEY, log=p.log)


def test_c_survey_packet():
    from wsn.protocol import SURVEY_F_CHOSEN, SURVEY_F_UNHEARD, TYPE_SURVEY, Survey, SurveyEntry
    buf = _lines()[7]
    p = parse_packet(buf, KEY)
    assert (p.type, p.seq) == (TYPE_SURVEY, 14)
    assert p.survey == Survey(3, 100, 3, (SurveyEntry("192.168.4.2", "d8:d6:68:b3:4c:23", 0.95, -56, 100,
                                                       SURVEY_F_CHOSEN),
                                          SurveyEntry("192.168.4.28", "", 0.0, 0, 0, SURVEY_F_UNHEARD)))
    assert p.survey.entries[0].chosen and p.survey.entries[1].unheard and p.survey.state_name == "done"
    assert buf == encode_packet(TYPE_SURVEY, MAC, 0xDEADBEEF, 14, KEY, survey=p.survey)


def hub_cmd_fixture() -> list[str]:
    from wsn.protocol import CMD_LINKS, CMD_SURVEY, encode_cmd, encode_hub, links_args, survey_args
    return [encode_hub(MAC, 1_792_000_000, KEY, "192.168.4.23", 5005).hex(),
            encode_cmd(MAC, 0xDEADBEEF, 9, KEY, CMD_SURVEY, survey_args(60, 6, ["192.168.4.23", "192.168.4.99"])).hex(),
            encode_cmd(MAC, 0xDEADBEEF, 10, KEY, CMD_LINKS, links_args([("d8:d6:68:b3:4c:23", "192.168.4.2")])).hex()]


def test_py_hub_cmd_fixture_is_current():
    """firmware/components/wsn_proto/test/hub_cmd_test.c parses this fixture with the board's parsers."""
    fx = (Path(__file__).parent / "golden" / "py_hub_cmd.hex").read_text().split()
    assert fx == hub_cmd_fixture()


def test_hub_cmd_bounds():
    from wsn.protocol import ProtocolError, encode_hub, links_args, survey_args
    with pytest.raises(ProtocolError):
        survey_args(60, 6, [f"10.0.0.{i}" for i in range(17)])
    with pytest.raises(ProtocolError):
        survey_args(5)
    with pytest.raises(ProtocolError):
        links_args([("d8:d6:68:b3:4c:23", "192.168.4.2")] * 10)
    with pytest.raises(ProtocolError):
        encode_hub(MAC, 1, KEY, "192.168.4.300", 5005)
