"""WSN1 UDP packet format (spec 4.3). Little-endian, HMAC-SHA256 truncated to 16 bytes."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field

MAGIC = b"WSN1"
VERSION = 1
TYPE_CSI = 1
TYPE_PRED = 2
TYPE_TELEM = 3
TYPE_CSI_SRC = 4  # CSI-кадры с MAC передатчика (несколько линий связи)
CSI_TYPES = (TYPE_CSI, TYPE_CSI_SRC)
HEADER_LEN = 21
HMAC_LEN = 16
MAX_DATAGRAM = 1400

_HEADER = struct.Struct("<4sBB6sIIB")
_FRAME = struct.Struct("<QbbBBH")
_TELEM = struct.Struct("<ffbIIIIIB")
_PRED = struct.Struct("<IHBB4B4BB")
assert _HEADER.size == HEADER_LEN and _FRAME.size == 14 and _TELEM.size == 30 and _PRED.size == 17


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class CsiFrame:
    t_us: int
    rssi: int
    noise: int
    sig_mode: int
    cwb: int
    data: bytes
    src: str = ""  # MAC передатчика (только TYPE_CSI_SRC; иначе — точка доступа платы)


@dataclass(frozen=True)
class Telemetry:
    csi_hz: float
    drop_ratio: float
    rssi_avg: int
    heap_free: int
    psram_free: int
    uptime_s: int
    fw_ver: int
    model_ver: int
    wifi_state: int


@dataclass(frozen=True)
class Prediction:
    """Answer of the on-board model; probabilities 0..1 (sent as u8 = p*255)."""
    model_ver: int
    infer_ms: int
    links_live: int
    act: tuple[float, float, float, float]   # нет движения, машет, ходит, мелкие
    zone: tuple[float, float, float, float]  # зал, кухня, коридор, санузел
    move: float


@dataclass(frozen=True)
class Packet:
    type: int
    device_id: str
    boot_id: int
    seq: int
    frames: list[CsiFrame] = field(default_factory=list)
    telemetry: Telemetry | None = None
    prediction: Prediction | None = None


def _mac_str(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _mac_bytes(mac: str) -> bytes:
    parts = mac.split(":")
    if len(parts) != 6:
        raise ProtocolError(f"bad mac {mac!r}")
    return bytes(int(p, 16) for p in parts)


def _sign(key: bytes, body: bytes) -> bytes:
    return hmac.new(key, body, hashlib.sha256).digest()[:HMAC_LEN]


def peek_device_id(buf: bytes) -> str:
    """Return device MAC from the header without verifying the signature."""
    if len(buf) < HEADER_LEN + HMAC_LEN:
        raise ProtocolError("length: too short")
    if buf[:4] != MAGIC:
        raise ProtocolError("magic")
    return _mac_str(buf[6:12])


def parse_packet(buf: bytes, key: bytes) -> Packet:
    if len(buf) < HEADER_LEN + HMAC_LEN:
        raise ProtocolError("length: too short")
    body, tag = buf[:-HMAC_LEN], buf[-HMAC_LEN:]
    if not hmac.compare_digest(_sign(key, body), tag):
        raise ProtocolError("hmac mismatch")
    magic, ver, ptype, dev, boot_id, seq, n = _HEADER.unpack_from(body, 0)
    if magic != MAGIC:
        raise ProtocolError("magic")
    if ver != VERSION:
        raise ProtocolError(f"version {ver}")
    off = HEADER_LEN
    frames: list[CsiFrame] = []
    telemetry: Telemetry | None = None
    prediction: Prediction | None = None
    if ptype in CSI_TYPES:
        for _ in range(n):
            if off + _FRAME.size > len(body):
                raise ProtocolError("length: frame header")
            t_us, rssi, noise, sig_mode, cwb, dlen = _FRAME.unpack_from(body, off)
            off += _FRAME.size
            src = ""
            if ptype == TYPE_CSI_SRC:
                if off + 6 > len(body):
                    raise ProtocolError("length: frame src")
                src = _mac_str(body[off : off + 6])
                off += 6
            if off + dlen > len(body):
                raise ProtocolError("length: frame data")
            frames.append(CsiFrame(t_us, rssi, noise, sig_mode, cwb, bytes(body[off : off + dlen]), src))
            off += dlen
    elif ptype == TYPE_TELEM:
        if n != 1 or off + _TELEM.size > len(body):
            raise ProtocolError("length: telemetry")
        telemetry = Telemetry(*_TELEM.unpack_from(body, off))
        off += _TELEM.size
    elif ptype == TYPE_PRED:
        if n != 1 or off + _PRED.size > len(body):
            raise ProtocolError("length: prediction")
        v = _PRED.unpack_from(body, off)
        prediction = Prediction(v[0], v[1], v[2], tuple(x / 255 for x in v[4:8]), tuple(x / 255 for x in v[8:12]),
                                v[12] / 255)
        off += _PRED.size
    else:
        raise ProtocolError(f"unsupported type {ptype}")
    if off != len(body):
        raise ProtocolError("length: trailing bytes")
    return Packet(ptype, _mac_str(dev), boot_id, seq, frames, telemetry, prediction)


def encode_packet(
    type: int,
    device_id: str,
    boot_id: int,
    seq: int,
    key: bytes,
    frames: list[CsiFrame] | None = None,
    telemetry: Telemetry | None = None,
    prediction: Prediction | None = None,
) -> bytes:
    parts: list[bytes] = []
    if type in CSI_TYPES:
        frames = frames or []
        n = len(frames)
        for f in frames:
            parts.append(_FRAME.pack(f.t_us, f.rssi, f.noise, f.sig_mode, f.cwb, len(f.data)))
            if type == TYPE_CSI_SRC:
                parts.append(_mac_bytes(f.src))
            parts.append(f.data)
    elif type == TYPE_TELEM:
        if telemetry is None:
            raise ProtocolError("telemetry required")
        n = 1
        t = telemetry
        parts.append(
            _TELEM.pack(t.csi_hz, t.drop_ratio, t.rssi_avg, t.heap_free, t.psram_free,
                        t.uptime_s, t.fw_ver, t.model_ver, t.wifi_state)
        )
    elif type == TYPE_PRED:
        if prediction is None:
            raise ProtocolError("prediction required")
        n = 1
        r = prediction
        q = lambda x: max(0, min(255, round(x * 255)))
        parts.append(_PRED.pack(r.model_ver, r.infer_ms, r.links_live, 0, *map(q, r.act), *map(q, r.zone), q(r.move)))
    else:
        raise ProtocolError(f"unsupported type {type}")
    body = _HEADER.pack(MAGIC, VERSION, type, _mac_bytes(device_id), boot_id, seq, n) + b"".join(parts)
    if len(body) + HMAC_LEN > MAX_DATAGRAM:
        raise ProtocolError("too large")
    return body + _sign(key, body)
