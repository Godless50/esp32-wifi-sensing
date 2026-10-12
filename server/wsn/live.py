"""Live view: CSI amplitudes, per-device stats, WebSocket fan-out hub."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .protocol import TYPE_CSI, TYPE_TELEM, Packet, Telemetry

_LTF_BYTES = 128  # 64 subcarriers x (imag, real) int8
# ESP-IDF buffer order inside one LTF: subcarriers 0..31, then -32..-1. Reorder to -32..31.
_REORDER = np.r_[32:64, 0:32]
_HZ_WINDOW_S = 2.0
_QUEUE_MAX = 200


def ltf_amplitude(data: bytes, part: Literal["lltf", "ht"]) -> np.ndarray | None:
    n = len(data)
    if n not in (128, 256, 384):
        return None
    if part == "lltf":
        off = 0
    elif part == "ht":
        if n < 256:
            return None
        off = _LTF_BYTES
    else:
        raise ValueError(part)
    iq = np.frombuffer(data, dtype=np.int8, count=_LTF_BYTES, offset=off).astype(np.float32).reshape(64, 2)
    amp = np.sqrt(iq[:, 0] ** 2 + iq[:, 1] ** 2)
    return amp[_REORDER]


@dataclass
class DeviceStats:
    device_id: str
    csi_hz: float = 0.0
    last_seen: float = 0.0
    rssi: int | None = None
    telemetry: Telemetry | None = None
    age_s: float = 0.0
    counters: dict[str, int] = field(default_factory=lambda: {"frames": 0, "packets": 0})


class Subscription:
    """Bounded per-client queue. Iterating yields hub messages until closed."""

    _CLOSED = object()

    def __init__(self, hub: "LiveHub"):
        self._hub = hub
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        try:
            self.loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.closed = False

    def _put(self, msg: dict) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._drop()

    def _drop(self) -> None:
        self.closed = True
        self._hub._subs.discard(self)
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(self._CLOSED)

    def close(self) -> None:
        if not self.closed:
            self._drop()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        msg = await self.queue.get()
        if msg is self._CLOSED:
            raise StopAsyncIteration
        return msg


class LiveHub:
    def __init__(self, decimate_hz: float = 20.0):
        self.min_interval = 1.0 / decimate_hz
        self._stats: dict[str, DeviceStats] = {}
        self._times: dict[str, deque] = {}
        self._last_emit: dict[str, float] = {}
        self._subs: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        sub = Subscription(self)
        self._subs.add(sub)
        return sub

    def subscriber_count(self) -> int:
        return len(self._subs)

    def devices(self, now: float | None = None) -> list[DeviceStats]:
        """Snapshot with rate and age evaluated at `now` (monotonic), so a silent device decays to 0 Hz."""
        now = time.monotonic() if now is None else now
        out = []
        for dev_id, st in self._stats.items():
            recent = sum(1 for t in self._times[dev_id] if t > now - _HZ_WINDOW_S)
            out.append(dataclasses.replace(st, csi_hz=recent / _HZ_WINDOW_S,
                                           age_s=max(0.0, now - st.last_seen), counters=dict(st.counters)))
        return out

    def on_packet(self, p: Packet, recv_t: float) -> None:
        st = self._stats.get(p.device_id)
        if st is None:
            st = self._stats[p.device_id] = DeviceStats(p.device_id)
            self._times[p.device_id] = deque()
        st.last_seen = recv_t
        st.counters["packets"] += 1
        if p.type == TYPE_TELEM and p.telemetry is not None:
            st.telemetry = p.telemetry
            self._broadcast({"type": "telem", "device_id": p.device_id, "t": recv_t,
                             **dataclasses.asdict(p.telemetry)})
            return
        if p.type != TYPE_CSI or not p.frames:
            return
        times = self._times[p.device_id]
        for _ in p.frames:
            times.append(recv_t)
        while times and times[0] <= recv_t - _HZ_WINDOW_S:
            times.popleft()
        st.csi_hz = len(times) / _HZ_WINDOW_S
        st.counters["frames"] += len(p.frames)
        last = p.frames[-1]
        st.rssi = last.rssi
        if recv_t - self._last_emit.get(p.device_id, -1e9) < self.min_interval - 1e-9:
            return
        amp = ltf_amplitude(last.data, "ht" if len(last.data) >= 256 else "lltf")
        if amp is None:
            return
        self._last_emit[p.device_id] = recv_t
        self._broadcast({"type": "amp", "device_id": p.device_id, "t": recv_t, "rssi": last.rssi,
                         "sig_mode": last.sig_mode, "amp": [round(float(x), 1) for x in amp]})

    def _broadcast(self, msg: dict) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for sub in list(self._subs):
            if sub.loop is None or sub.loop is current:
                sub._put(msg)
            else:
                sub.loop.call_soon_threadsafe(sub._put, msg)
