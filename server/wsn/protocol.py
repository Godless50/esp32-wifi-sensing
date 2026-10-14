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
TYPE_MODEL_CHUNK = 6  # сервер -> плата: кусок новой модели
TYPE_MODEL_ACK = 7    # плата -> сервер: сколько модели записано
TYPE_DIAG = 8         # плата -> сервер: чем кончились прошлые загрузки
DIAG_HIST = 16
DIAG_MSG = 47
DIAG_BT = 8
TYPE_LOG = 9          # плата -> сервер: строка текста (отладка плат вдали от USB)
LOG_MAX = 200
MODEL_CHUNK_MAX = 1024
CSI_TYPES = (TYPE_CSI, TYPE_CSI_SRC)
HEADER_LEN = 21
HMAC_LEN = 16
MAX_DATAGRAM = 1400

_HEADER = struct.Struct("<4sBB6sIIB")
_FRAME = struct.Struct("<QbbBBH")
_TELEM = struct.Struct("<ffbIIIIIB")
_PRED = struct.Struct("<IHBB4B4BB")
_CHUNK = struct.Struct("<IIIH")
_ACK = struct.Struct("<IIBBB")
_DIAG = struct.Struct("<IIIBBB")
_DIAG_TAIL = struct.Struct("<BBBBI")
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
class ModelAck:
    """OTA progress of a board: blob version, contiguous bytes stored, state (0 idle, 1 receiving, 2 activating,
    3 error), slot being written (0 model_a, 1 model_b), last error code."""
    version: int
    received: int
    state: int
    slot: int
    error: int


# esp_reset_reason_t и panic_exception_t (ESP-IDF 5.x)
RESET_REASONS = {0: "-", 1: "POWERON", 2: "EXT", 3: "SW", 4: "PANIC", 5: "INT_WDT", 6: "TASK_WDT", 7: "WDT",
                 8: "DEEPSLEEP", 9: "BROWNOUT", 10: "SDIO", 11: "USB", 12: "JTAG", 13: "EFUSE", 14: "PWR_GLITCH",
                 15: "CPU_LOCKUP"}
PANIC_KINDS = {0: "", 1: "DEBUG", 2: "IWDT", 3: "TWDT", 4: "ABORT", 5: "FAULT"}
# soc_reset_reason_t ESP32-S3: какой именно сторож/сброс (ESP_RST_WDT объединяет несколько)
ROM_RESETS = {1: "POWERON/BROWNOUT/SUPER_WDT_CHIP", 3: "SW_SYS", 5: "DEEPSLEEP", 7: "MWDT0_SYS", 8: "MWDT1_SYS",
              9: "RTC_WDT_SYS", 11: "MWDT0_CPU", 12: "SW_CPU", 13: "RTC_WDT_CPU", 15: "BROWNOUT_SYS",
              16: "RTC_WDT_RTC", 17: "MWDT1_CPU", 18: "SUPER_WDT", 19: "CLK_GLITCH", 20: "EFUSE_CRC", 21: "USB_UART",
              22: "USB_JTAG", 23: "PWR_GLITCH"}
EARLY_KINDS = {1: "interrupt", 2: "exception"}
PANIC_RSN = {0: "none", 1: "debug exception", 2: "double exception", 3: "kernel exception", 4: "coprocessor",
             5: "IWDT CPU0", 6: "IWDT CPU1", 7: "cache error"}
EXCCAUSE = {0: "IllegalInstruction", 2: "InstructionFetchError", 3: "LoadStoreError", 6: "IntegerDivideByZero",
            9: "LoadStoreAlignment", 20: "InstFetchProhibited", 28: "LoadProhibited", 29: "StoreProhibited"}


@dataclass(frozen=True)
class Diag:
    """Reboot history of a board. history: (reset reason, boot stage) newest first; stage bit 0x80 = USB host.
    panic_*: the last panic of the previous boot (panic_exc 0 = it did not panic). prev_uptime_s 0 = unknown."""
    boots: int
    prev_uptime_s: int
    panic_pc: int
    panic_exc: int
    panic_core: int
    history: tuple[tuple[int, int], ...]
    msg: str = ""
    rom_rst: tuple[int, int] = (0, 0)  # 0 = прошивка без этого поля
    early_kind: int = 0
    early_cause: int = 0
    early_pc: int = 0
    backtrace: tuple[int, ...] = ()

    def summary(self) -> dict:
        ec = (PANIC_RSN if self.early_kind == 1 else EXCCAUSE).get(self.early_cause, str(self.early_cause))
        return {
            "boots": self.boots,
            "prev_uptime_s": self.prev_uptime_s,
            "rom_reset": [ROM_RESETS.get(r, str(r)) for r in self.rom_rst] if any(self.rom_rst) else None,
            "early": None if not self.early_kind else {
                "kind": EARLY_KINDS.get(self.early_kind, str(self.early_kind)), "cause": ec,
                "pc": f"0x{self.early_pc:08x}"},
            "backtrace": [f"0x{a:08x}" for a in self.backtrace],
            "last": [f"{RESET_REASONS.get(r, str(r))}/{s & 0x7F}{'u' if s & 0x80 else ''}" for r, s in self.history],
            "panic": None if not self.panic_exc else {
                "kind": PANIC_KINDS.get(self.panic_exc, str(self.panic_exc)),
                "pc": f"0x{self.panic_pc:08x}", "core": self.panic_core, "msg": self.msg},
        }


@dataclass(frozen=True)
class Packet:
    type: int
    device_id: str
    boot_id: int
    seq: int
    frames: list[CsiFrame] = field(default_factory=list)
    telemetry: Telemetry | None = None
    prediction: Prediction | None = None
    model_ack: ModelAck | None = None
    diag: Diag | None = None
    log: str | None = None


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


def parse_packet(buf: bytes, key: bytes | None, verify: bool = True) -> Packet:
    """verify=False: no signature check — only for recordings of datagrams that were checked when they arrived
    (the board's key changes when it is provisioned again, old recordings stay readable)."""
    if len(buf) < HEADER_LEN + HMAC_LEN:
        raise ProtocolError("length: too short")
    body, tag = buf[:-HMAC_LEN], buf[-HMAC_LEN:]
    if verify and (key is None or not hmac.compare_digest(_sign(key, body), tag)):
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
    model_ack: ModelAck | None = None
    diag: Diag | None = None
    text: str | None = None
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
    elif ptype == TYPE_MODEL_ACK:
        if n != 1 or off + _ACK.size > len(body):
            raise ProtocolError("length: model ack")
        model_ack = ModelAck(*_ACK.unpack_from(body, off))
        off += _ACK.size
    elif ptype == TYPE_DIAG:
        if n != 1 or off + _DIAG.size > len(body):
            raise ProtocolError("length: diag")
        boots, up, pc, exc, core, hn = _DIAG.unpack_from(body, off)
        off += _DIAG.size
        if hn > DIAG_HIST or off + 2 * hn + 1 > len(body):
            raise ProtocolError("length: diag history")
        hist = tuple((body[off + 2 * i], body[off + 2 * i + 1]) for i in range(hn))
        off += 2 * hn
        ml = body[off]
        off += 1
        if ml > DIAG_MSG or off + ml > len(body):
            raise ProtocolError("length: diag msg")
        msg = bytes(body[off : off + ml]).decode("utf-8", "replace")
        off += ml
        extra: tuple = ()
        rest = len(body) - off  # старые прошивки шлют пакет короче: без хвоста или без трассы
        if rest >= _DIAG_TAIL.size:
            r0, r1, ek, ecause, epc = _DIAG_TAIL.unpack_from(body, off)
            off += _DIAG_TAIL.size
            bt: tuple[int, ...] = ()
            if rest > _DIAG_TAIL.size:
                bn = body[off]
                if bn > DIAG_BT or off + 1 + 4 * bn > len(body):
                    raise ProtocolError("length: diag backtrace")
                bt = struct.unpack_from(f"<{bn}I", body, off + 1)
                off += 1 + 4 * bn
            extra = ((r0, r1), ek, ecause, epc, bt)
        diag = Diag(boots, up, pc, exc, core, hist, msg, *extra)
    elif ptype == TYPE_LOG:
        if n != 1 or off + 1 > len(body) or body[off] > LOG_MAX or off + 1 + body[off] > len(body):
            raise ProtocolError("length: log")
        text = bytes(body[off + 1 : off + 1 + body[off]]).decode("utf-8", "replace")
        off += 1 + body[off]
    else:
        raise ProtocolError(f"unsupported type {ptype}")
    if off != len(body):
        raise ProtocolError("length: trailing bytes")
    return Packet(ptype, _mac_str(dev), boot_id, seq, frames, telemetry, prediction, model_ack, diag, text)


def encode_packet(
    type: int,
    device_id: str,
    boot_id: int,
    seq: int,
    key: bytes,
    frames: list[CsiFrame] | None = None,
    telemetry: Telemetry | None = None,
    prediction: Prediction | None = None,
    diag: Diag | None = None,
    log: str | None = None,
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
    elif type == TYPE_DIAG:
        if diag is None:
            raise ProtocolError("diag required")
        n = 1
        msg = diag.msg.encode()[:DIAG_MSG]
        if len(diag.history) > DIAG_HIST:
            raise ProtocolError("diag history too long")
        parts.append(_DIAG.pack(diag.boots, diag.prev_uptime_s, diag.panic_pc, diag.panic_exc, diag.panic_core,
                                len(diag.history)))
        parts.append(bytes(x for rs in diag.history for x in rs))
        parts.append(bytes([len(msg)]) + msg)
        if len(diag.backtrace) > DIAG_BT:
            raise ProtocolError("diag backtrace too long")
        parts.append(_DIAG_TAIL.pack(*diag.rom_rst, diag.early_kind, diag.early_cause, diag.early_pc))
        parts.append(struct.pack(f"<B{len(diag.backtrace)}I", len(diag.backtrace), *diag.backtrace))
    elif type == TYPE_LOG:
        if log is None:
            raise ProtocolError("log required")
        n = 1
        t = log.encode()[:LOG_MAX]
        parts.append(bytes([len(t)]) + t)
    else:
        raise ProtocolError(f"unsupported type {type}")
    body = _HEADER.pack(MAGIC, VERSION, type, _mac_bytes(device_id), boot_id, seq, n) + b"".join(parts)
    if len(body) + HMAC_LEN > MAX_DATAGRAM:
        raise ProtocolError("too large")
    return body + _sign(key, body)


def encode_model_chunk(device_id: str, board_boot_id: int, seq: int, key: bytes, version: int, total: int,
                       offset: int, data: bytes) -> bytes:
    """Server -> board packet with one piece of a model blob. Header boot_id = the board's CURRENT boot id
    (the board drops chunks of other boots), seq strictly increasing per push."""
    if len(data) > MODEL_CHUNK_MAX or offset + len(data) > total:
        raise ProtocolError("chunk bounds")
    body = (_HEADER.pack(MAGIC, VERSION, TYPE_MODEL_CHUNK, _mac_bytes(device_id), board_boot_id, seq, 1)
            + _CHUNK.pack(version, total, offset, len(data)) + data)
    return body + _sign(key, body)
