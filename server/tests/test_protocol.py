import hashlib
import hmac

import pytest

from wsn.protocol import (
    HEADER_LEN,
    HMAC_LEN,
    TYPE_CSI,
    TYPE_TELEM,
    CsiFrame,
    ProtocolError,
    Telemetry,
    encode_packet,
    parse_packet,
    peek_device_id,
)

KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"  # STA MAC платы (тестовый)
TELEM = Telemetry(1.0, 0.0, -1, 1, 1, 1, 1, 0, 2)


def _frames(n=5, length=256):
    return [
        CsiFrame(t_us=1_000_000 + i, rssi=-45, noise=-92, sig_mode=1, cwb=0, data=bytes(range(length)))
        for i in range(n)
    ]


def _resign(body: bytes) -> bytes:
    return body + hmac.new(KEY, body, hashlib.sha256).digest()[:16]


def test_csi_roundtrip():
    frames = _frames()
    buf = encode_packet(TYPE_CSI, MAC, boot_id=0xDEADBEEF, seq=7, key=KEY, frames=frames)
    assert len(buf) == 21 + 5 * (14 + 256) + 16
    assert HEADER_LEN == 21 and HMAC_LEN == 16
    p = parse_packet(buf, KEY)
    assert (p.type, p.device_id, p.boot_id, p.seq) == (TYPE_CSI, MAC, 0xDEADBEEF, 7)
    assert p.frames == frames and p.telemetry is None


def test_telemetry_roundtrip():
    t = Telemetry(98.5, 0.02, -44, 120_000, 8_000_000, 3600, 1, 0, 2)
    p = parse_packet(encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=t), KEY)
    assert p.telemetry.csi_hz == pytest.approx(98.5) and p.telemetry.drop_ratio == pytest.approx(0.02)
    assert (p.telemetry.rssi_avg, p.telemetry.heap_free, p.telemetry.uptime_s, p.telemetry.wifi_state) == (
        -44,
        120_000,
        3600,
        2,
    )
    assert p.frames == []


def test_bad_hmac_rejected():
    buf = bytearray(encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=TELEM))
    buf[-1] ^= 1
    with pytest.raises(ProtocolError, match="hmac"):
        parse_packet(bytes(buf), KEY)


def test_wrong_key_rejected():
    buf = encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=TELEM)
    with pytest.raises(ProtocolError, match="hmac"):
        parse_packet(buf, bytes(32))


@pytest.mark.parametrize("mutate", ["magic", "ver", "truncated", "type"])
def test_malformed_rejected(mutate):
    buf = encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=TELEM)
    body = bytearray(buf[:-16])
    if mutate == "magic":
        body[0:4] = b"XXXX"
        bad = _resign(bytes(body))
    elif mutate == "ver":
        body[4] = 2
        bad = _resign(bytes(body))
    elif mutate == "type":
        body[5] = 9
        bad = _resign(bytes(body))
    else:
        bad = buf[:10]
    with pytest.raises(ProtocolError):
        parse_packet(bad, KEY)


def test_frame_len_overflow_rejected():
    buf = encode_packet(TYPE_CSI, MAC, 1, 0, KEY, frames=_frames(1, 16))
    body = bytearray(buf[:-16])
    body[21 + 12 : 21 + 14] = (200).to_bytes(2, "little")  # поле len первого кадра
    with pytest.raises(ProtocolError, match="length"):
        parse_packet(_resign(bytes(body)), KEY)


def test_trailing_bytes_rejected():
    buf = encode_packet(TYPE_CSI, MAC, 1, 0, KEY, frames=_frames(1, 16))
    with pytest.raises(ProtocolError, match="length"):
        parse_packet(_resign(buf[:-16] + b"\x00"), KEY)


def test_too_large_rejected_on_encode():
    with pytest.raises(ProtocolError, match="too large"):
        encode_packet(TYPE_CSI, MAC, 1, 0, KEY, frames=_frames(6, 256))


def test_peek_device_id():
    assert peek_device_id(encode_packet(TYPE_CSI, MAC, 1, 0, KEY, frames=[])) == MAC


def test_peek_device_id_short_buffer():
    with pytest.raises(ProtocolError):
        peek_device_id(b"WSN1")


def test_diag_roundtrip_and_bounds():
    from wsn.protocol import DIAG_HIST, TYPE_DIAG, Diag
    d = Diag(7, 0, 0, 0, 0, ((1, 0x81),), "")
    p = parse_packet(encode_packet(TYPE_DIAG, MAC, 3, 1, KEY, diag=d), KEY)
    assert p.diag == d and p.diag.summary()["panic"] is None and p.diag.summary()["last"] == ["POWERON/1u"]
    with pytest.raises(ProtocolError):
        encode_packet(TYPE_DIAG, MAC, 3, 1, KEY, diag=Diag(1, 0, 0, 0, 0, ((1, 1),) * (DIAG_HIST + 1)))
    body = bytearray(encode_packet(TYPE_DIAG, MAC, 3, 1, KEY, diag=d)[:-HMAC_LEN])
    body[HEADER_LEN + 14] = DIAG_HIST + 1  # длина истории больше допустимой
    with pytest.raises(ProtocolError):
        parse_packet(_resign(bytes(body)), KEY)
    with pytest.raises(ProtocolError):
        parse_packet(_resign(bytes(body[:-1])), KEY)
    # первая версия пакета (без хвоста с причинами сброса ROM) тоже читается
    old = bytearray(encode_packet(TYPE_DIAG, MAC, 3, 1, KEY, diag=d)[:-HMAC_LEN - 9])
    assert parse_packet(_resign(bytes(old)), KEY).diag == d
    v2 = bytearray(encode_packet(TYPE_DIAG, MAC, 3, 1, KEY, diag=d)[:-HMAC_LEN - 1])  # хвост без трассы
    assert parse_packet(_resign(bytes(v2)), KEY).diag == d
