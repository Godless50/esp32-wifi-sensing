"""Push a model blob to a board over UDP (the board side is firmware/main/model_ota.c).

Chunks go to the address the board sends its CSI from, signed with the board key and bound to its current boot
id. The board stores them in order and reports how many bytes it has (WSN_TYPE_MODEL_ACK); the pusher always
resumes from that point, so lost datagrams are simply sent again. Done when the board reports "activating"
(it then restarts) or its telemetry already shows the new model version.
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


@dataclass
class PushState:
    device_id: str
    version: int
    total: int
    sent: int = 0
    acked: int = 0
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

    def status(self) -> list[dict]:
        return [vars(s) for s in self.pushes.values()]

    def start(self, device_id: str, blob: bytes) -> PushState:
        if blob[:4] != b"WSNM" or len(blob) < 128:
            raise ValueError("not a model blob")
        cur = self.pushes.get(device_id)
        if cur and cur.state in ("starting", "sending", "activating"):
            raise RuntimeError("push already running")
        loop = asyncio.get_running_loop()  # вызывать из цикла событий сервера (иначе RuntimeError до записи состояния)
        st = PushState(device_id, struct.unpack_from("<I", blob, 4)[0], len(blob))
        self.pushes[device_id] = st
        loop.create_task(self._run(st, blob))
        return st

    async def _run(self, st: PushState, blob: bytes) -> None:
        dev = st.device_id
        ev = self._ack_event.setdefault(dev, asyncio.Event())
        end = time.monotonic() + TIMEOUT_S
        try:
            while time.monotonic() < end:
                if self.model_ver.get(dev) == st.version:
                    st.state, st.message = "done", "board runs the new model"
                    return
                ack = self.acks.get(dev, (None, 0))[0]
                if ack is not None and ack.version == st.version:
                    st.acked = ack.received
                    if ack.state == 2:
                        st.state, st.message = "activating", "board verified the model and restarts"
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
                for off in range(start, min(st.total, start + WINDOW * MODEL_CHUNK_MAX), MODEL_CHUNK_MAX):
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
