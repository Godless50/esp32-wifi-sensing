"""Push a model blob to a board over UDP (the board side is firmware/main/model_ota.c).

Chunks go to the address the board sends its CSI from, signed with the board key and bound to its current boot
id. The board stores them in order and reports how many bytes it has (WSN_TYPE_MODEL_ACK); the pusher always
resumes from that point, so lost datagrams are simply sent again. Done when the board reports "activating"
(it then restarts) or its telemetry already shows the new model version.

The same channel carries a firmware image (ESP app .bin): its version has bit 31 set and equals the board's
firmware id (bit 31 + the first 3 bytes of the image's ELF SHA256, telemetry field fw_ver). The board writes it into
the other OTA app partition, checks it, boots it and confirms it only after reaching Wi-Fi (else it rolls back).
And a settings blob ("WSNC" | u8 n | n x MAC: the extra transmitters whose CSI the board forwards): version with
bits 31 and 30 set; the board stores it in NVS and restarts (done when it reports "activating").
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field

from .protocol import MODEL_CHUNK_MAX, TYPE_MODEL_ACK, TYPE_TELEM, ModelAck, Packet, encode_model_chunk

log = logging.getLogger(__name__)
WINDOW = 8          # кусков подряд, потом ждём подтверждения
ACK_WAIT_S = 0.6
TIMEOUT_S = 180.0
FW_TIMEOUT_S = 900.0
FW_FLAG = 0x80000000
CFG_MASK = 0xC0000000


def links_blob(macs: list[str], promisc: bool | None = None, beacon_hz: int = 50, bssid: str | None = None) -> bytes:
    """Settings blob for the board: extra transmitters (at most 14) and, optionally, whether to sniff the air for
    them (promiscuous mode crashes the Wi-Fi library) and the rate of its own ESP-NOW broadcasts."""
    if len(macs) > 14:
        raise ValueError("at most 14 extra transmitters")
    out = bytearray(b"WSNC") + bytes([len(macs)])
    for m in macs:
        b = bytes.fromhex(m.replace(":", ""))
        if len(b) != 6:
            raise ValueError(f"bad MAC {m}")
        out += b
    if promisc is not None or bssid is not None:
        out += bytes([1 if promisc else 0, max(0, min(100, int(beacon_hz)))])
    if bssid is not None:  # к какому узлу mesh подключаться (пароль сети остаётся на плате)
        b = bytes.fromhex(bssid.replace(":", ""))
        if len(b) != 6:
            raise ValueError(f"bad BSSID {bssid}")
        out += b
    return bytes(out)
APP_DESC_MAGIC = b"\x32\x54\xcd\xab"  # esp_app_desc_t, сразу после заголовков образа (смещение 0x20)


def firmware_id(blob: bytes) -> int | None:
    """Firmware id of an ESP app image (same as wsn_fw_id() on the board), None when blob is not an app image."""
    if len(blob) < 0x100 or blob[0] != 0xE9 or blob[0x20:0x24] != APP_DESC_MAGIC:
        return None
    sha = blob[0x20 + 144: 0x20 + 147]
    return FW_FLAG | (sha[0] << 16) | (sha[1] << 8) | sha[2]


@dataclass
class PushState:
    device_id: str
    version: int
    total: int
    sent: int = 0
    acked: int = 0
    kind: str = "model"  # model | firmware
    state: str = "starting"  # starting | sending | activating | done | failed
    message: str = ""
    started: float = field(default_factory=time.time)


class ModelPusher:
    def __init__(self, keys, ingest):
        self.keys = keys
        self.ingest = ingest
        self.transport = None
        self.acks: dict[str, tuple[ModelAck, float]] = {}
        self.model_ver: dict[str, int] = {}
        self.fw_ver: dict[str, int] = {}
        self.pushes: dict[str, PushState] = {}
        self._ack_event: dict[str, asyncio.Event] = {}
        self._seq = int(time.time() * 1000) & 0x7FFFFFFF

    def on_packet(self, p: Packet, recv_t: float) -> None:
        if p.type == TYPE_MODEL_ACK and p.model_ack is not None:
            self.acks[p.device_id] = (p.model_ack, recv_t)
            ev = self._ack_event.get(p.device_id)
            if ev is not None:
                ev.set()
        elif p.type == TYPE_TELEM and p.telemetry is not None:
            self.model_ver[p.device_id] = p.telemetry.model_ver
            self.fw_ver[p.device_id] = p.telemetry.fw_ver

    def status(self) -> list[dict]:
        return [vars(s) for s in self.pushes.values()]

    def start(self, device_id: str, blob: bytes) -> PushState:
        fw = firmware_id(blob)
        cfg = blob[:4] == b"WSNC"
        if cfg and (len(blob) < 5 or len(blob) not in (5 + 6 * blob[4], 7 + 6 * blob[4], 13 + 6 * blob[4])):
            raise ValueError("bad settings blob")
        if fw is None and not cfg and (blob[:4] != b"WSNM" or len(blob) < 128):
            raise ValueError("neither a model blob, a firmware image nor a settings blob")
        cur = self.pushes.get(device_id)
        if cur and cur.state in ("starting", "sending", "activating"):
            raise RuntimeError("push already running")
        loop = asyncio.get_running_loop()  # вызывать из цикла событий сервера (иначе RuntimeError до записи состояния)
        if cfg:
            ver, kind = CFG_MASK | (int(time.time()) & 0x3FFFFFFF), "settings"
        elif fw is not None:
            ver, kind = fw, "firmware"
        else:
            ver, kind = struct.unpack_from("<I", blob, 4)[0], "model"
            if ver & FW_FLAG:
                raise ValueError("model version must not have bit 31 set")
        st = PushState(device_id, ver, len(blob), kind=kind)
        self.pushes[device_id] = st
        loop.create_task(self._run(st, blob))
        return st

    async def _run(self, st: PushState, blob: bytes) -> None:
        dev = st.device_id
        ev = self._ack_event.setdefault(dev, asyncio.Event())
        end = time.monotonic() + (FW_TIMEOUT_S if st.kind == "firmware" else TIMEOUT_S)
        last_start = -1
        try:
            while time.monotonic() < end:
                if st.kind == "model" and self.model_ver.get(dev) == st.version:
                    st.state, st.message = "done", "board runs the new model"
                    return
                if st.kind == "firmware" and self.fw_ver.get(dev) == st.version:
                    st.state, st.message = "done", "board runs the new firmware"
                    return
                ack = self.acks.get(dev, (None, 0))[0]
                if ack is not None and ack.version == st.version:
                    st.acked = ack.received
                    if ack.state == 2 and st.kind == "settings":
                        st.state, st.message = "done", "board stored the settings and restarts"
                        return
                    if ack.state == 2:
                        st.state, st.message = "activating", f"board verified the {st.kind} and restarts"
                    elif ack.state == 3:
                        st.state, st.message = "failed", f"board error {ack.error}"
                        return
                start = st.acked if ack is not None and ack.version == st.version else 0
                if st.state == "activating":
                    await asyncio.sleep(1.0)  # ждём телеметрию после перезагрузки
                    continue
                addr, key = self.ingest.addrs.get(dev), self.keys.get(dev)
                boot = self.ingest.boot_of(dev)
                if self.transport is None or addr is None or key is None or boot is None:
                    st.message = "board not seen yet"
                    await asyncio.sleep(1.0)
                    continue
                st.state = "sending"
                hi = start + WINDOW * MODEL_CHUNK_MAX
                if start == last_start:
                    # Нет продвижения: подтверждение платы могло потеряться, а плата уже стоит дальше (до st.sent).
                    # Старые прошивки на повтор записанного куска не отвечают — шлём подряд до st.sent включительно,
                    # тогда кусок, которого плата ждёт, точно в окне.
                    # Не больше двух окон: иначе окно растёт на кусок за круг и забивает эфир (так было 13.10).
                    hi = min(max(hi, st.sent + MODEL_CHUNK_MAX), start + 2 * WINDOW * MODEL_CHUNK_MAX)
                last_start = start
                for off in range(start, min(st.total, hi), MODEL_CHUNK_MAX):
                    self._seq += 1
                    data = blob[off:off + MODEL_CHUNK_MAX]
                    self.transport.sendto(encode_model_chunk(dev, boot, self._seq, key, st.version, st.total, off, data), addr)
                    st.sent = max(st.sent, off + len(data))
                    await asyncio.sleep(0.005)
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), ACK_WAIT_S)
                except asyncio.TimeoutError:
                    pass
            st.state, st.message = "failed", "timeout"
        except Exception as e:  # отправка не должна ронять сервер
            log.exception("model push failed")
            st.state, st.message = "failed", str(e)
