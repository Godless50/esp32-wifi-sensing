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
HEADER_LEN = 21
HMAC_LEN = 16
MAX_DATAGRAM = 1400

_HEADER = struct.Struct("<4sBB6sIIB")
_FRAME = struct.Struct("<QbbBBH")
_TELEM = struct.Struct("<ffbIIIIIB")
assert _HEADER.size == HEADER_LEN and _FRAME.size == 14 and _TELEM.size == 30


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
class Packet:
    type: int
    device_id: str
    boot_id: int
    seq: int
    frames: list[CsiFrame] = field(default_factory=list)
    telemetry: Telemetry | None = None


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
    if ptype == TYPE_CSI:
        for _ in range(n):
            if off + _FRAME.size > len(body):
                raise ProtocolError("length: frame header")
            t_us, rssi, noise, sig_mode, cwb, dlen = _FRAME.unpack_from(body, off)
            off += _FRAME.size
            if off + dlen > len(body):
                raise ProtocolError("length: frame data")
            frames.append(CsiFrame(t_us, rssi, noise, sig_mode, cwb, bytes(body[off : off + dlen])))
            off += dlen
    elif ptype == TYPE_TELEM:
        if n != 1 or off + _TELEM.size > len(body):
            raise ProtocolError("length: telemetry")
        telemetry = Telemetry(*_TELEM.unpack_from(body, off))
        off += _TELEM.size
    else:
        raise ProtocolError(f"unsupported type {ptype}")
    if off != len(body):
        raise ProtocolError("length: trailing bytes")
    return Packet(ptype, _mac_str(dev), boot_id, seq, frames, telemetry)


def encode_packet(
    type: int,
    device_id: str,
    boot_id: int,
    seq: int,
    key: bytes,
    frames: list[CsiFrame] | None = None,
    telemetry: Telemetry | None = None,
) -> bytes:
    parts: list[bytes] = []
    if type == TYPE_CSI:
        frames = frames or []
        n = len(frames)
        for f in frames:
            parts.append(_FRAME.pack(f.t_us, f.rssi, f.noise, f.sig_mode, f.cwb, len(f.data)))
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
    else:
        raise ProtocolError(f"unsupported type {type}")
    body = _HEADER.pack(MAGIC, VERSION, type, _mac_bytes(device_id), boot_id, seq, n) + b"".join(parts)
    if len(body) + HMAC_LEN > MAX_DATAGRAM:
        raise ProtocolError("too large")
    return body + _sign(key, body)
