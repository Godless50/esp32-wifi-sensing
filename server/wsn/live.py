"""Live view: CSI amplitudes, per-device stats, WebSocket fan-out hub."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import logging

import numpy as np

from .breath import SCORE_OK, SPREAD_OK, amplitude_series, score_series
from .protocol import CSI_TYPES, TYPE_DIAG, TYPE_LOG, TYPE_PRED, TYPE_TELEM, Packet, Prediction, Telemetry

_LTF_BYTES = 128  # 64 subcarriers x (imag, real) int8
# ESP-IDF buffer order inside one LTF: subcarriers 0..31, then -32..-1. Reorder to -32..31.
_REORDER = np.r_[32:64, 0:32]
_HZ_WINDOW_S = 2.0
_QUEUE_MAX = 200
log = logging.getLogger(__name__)
LOG_KEEP = 30


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


MOTION_WINDOW_S = 1.0
MOTION_PERIOD_S = 0.25  # 4 Гц
MOTION_BASELINE_S = 60.0
_NULL_AMP = 1.0  # поднесущие без сигнала (защитные, DC) не учитываются


def motion_energy(amps: np.ndarray, normalize: bool = False) -> float:
    """Mean over live subcarriers of the temporal std of amplitude. amps: [T, 64].

    normalize=True divides every frame by its mean amplitude first (removes AGC / transmit-power jumps,
    which are large for IoT transmitters), so only the shape of the channel counts.
    """
    a = np.asarray(amps, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 2:
        return 0.0
    live = a.mean(axis=0) > _NULL_AMP
    if not live.any():
        return 0.0
    a = a[:, live]
    if normalize:
        a = a / (a.mean(axis=1, keepdims=True) + 1e-3)
    return float(a.std(axis=0).mean())


BREATH_WINDOW_S, BREATH_MIN_S, BREATH_PERIOD_S, BREATH_HZ = 120.0, 90.0, 15.0, 20.0
LEGACY_AFTER_S = 10.0  # линия без HT-кадров дольше этого — считаем её по L-LTF (устройства 11g)


BOARD_ZONES = ("зал", "кухня", "коридор", "санузел")          # порядок выходов модели платы (ml/train_final.py)
BOARD_ACTS = ("нет движения", "машет", "ходит", "мелкие")


def board_prediction(device_id: str, t: float, r: Prediction) -> dict:
    """The on-board model's answer in the same shape as the server learner's /api/predict message."""
    zi = max(range(4), key=lambda i: r.zone[i])
    ai = max(range(4), key=lambda i: r.act[i])
    return {"type": "pred", "source": "board", "device_id": device_id, "t": t,
            "zone": BOARD_ZONES[zi], "zone_p": round(r.zone[zi], 3), "activity": BOARD_ACTS[ai],
            "activity_p": round(r.act[ai], 3), "moving_p": round(r.move, 3),
            "probs": {z: round(p, 3) for z, p in zip(BOARD_ZONES, r.zone)},
            "model": f"плата v{r.model_ver}", "infer_ms": r.infer_ms, "links_live": r.links_live}


class _Motion:
    def __init__(self):
        self.window: deque = deque()  # (t, amp[64])
        self.baseline: deque = deque()  # (t, raw)
        self.last_emit = -1e9
        self.kind = "ht"  # какое поле обучения в окне: HT-LTF или L-LTF (не смешиваем)
        self.last_ht = -1e9
        self.long: deque = deque()  # (t кадра, amp) за BREATH_WINDOW_S, не чаще BREATH_HZ — для дыхания
        self.last_breath = -1e9


@dataclass
class DeviceStats:
    device_id: str
    csi_hz: float = 0.0
    last_seen: float = 0.0
    rssi: int | None = None
    telemetry: Telemetry | None = None
    age_s: float = 0.0
    counters: dict[str, int] = field(default_factory=lambda: {"frames": 0, "packets": 0})
    links: dict[str, dict] = field(default_factory=dict)  # src MAC -> {"hz", "rssi"}
    boot_id: int | None = None
    diag: dict | None = None  # Diag.summary(): прошлые перезапуски платы и последнее падение
    log: list[str] = field(default_factory=list)  # последние строки TYPE_LOG с временем сервера


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
        self._motion: dict[tuple[str, str], _Motion] = {}
        self._link_times: dict[str, dict[str, deque]] = {}
        self._link_rssi: dict[str, dict[str, int]] = {}
        self.listeners: list = []  # вызываются с каждым сообщением хаба (трекер человека)

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
            links = {src: {"hz": sum(1 for t in q if t > now - _HZ_WINDOW_S) / _HZ_WINDOW_S,
                           "rssi": self._link_rssi[dev_id].get(src)}
                     for src, q in self._link_times.get(dev_id, {}).items()}
            out.append(dataclasses.replace(st, csi_hz=recent / _HZ_WINDOW_S, links=links,
                                           age_s=max(0.0, now - st.last_seen), counters=dict(st.counters)))
        return out

    def on_packet(self, p: Packet, recv_t: float) -> None:
        st = self._stats.get(p.device_id)
        if st is None:
            st = self._stats[p.device_id] = DeviceStats(p.device_id)
            self._times[p.device_id] = deque()
        st.last_seen = recv_t
        st.counters["packets"] += 1
        if p.type == TYPE_LOG and p.log is not None:
            st.log = (st.log + [f"{time.strftime('%H:%M:%S')} {p.log}"])[-LOG_KEEP:]
            log.warning("board %s: %s", p.device_id, p.log)
            return
        if p.type == TYPE_DIAG and p.diag is not None:
            new_boot = st.boot_id != p.boot_id or st.diag is None
            st.boot_id, st.diag = p.boot_id, p.diag.summary()
            if new_boot:
                log.warning("board %s boot %08x diag: %s", p.device_id, p.boot_id, st.diag)
            return
        if p.type == TYPE_TELEM and p.telemetry is not None:
            st.telemetry = p.telemetry
            self._broadcast({"type": "telem", "device_id": p.device_id, "t": recv_t,
                             **dataclasses.asdict(p.telemetry)})
            return
        if p.type == TYPE_PRED and p.prediction is not None:
            self._broadcast(board_prediction(p.device_id, recv_t, p.prediction))
            return
        if p.type not in CSI_TYPES or not p.frames:
            return
        times = self._times[p.device_id]
        for _ in p.frames:
            times.append(recv_t)
        while times and times[0] <= recv_t - _HZ_WINDOW_S:
            times.popleft()
        st.csi_hz = len(times) / _HZ_WINDOW_S
        st.counters["frames"] += len(p.frames)
        lt = self._link_times.setdefault(p.device_id, {})
        lr = self._link_rssi.setdefault(p.device_id, {})
        for f in p.frames:
            q = lt.setdefault(f.src, deque())
            q.append(recv_t)
            while q and q[0] <= recv_t - _HZ_WINDOW_S:
                q.popleft()
            lr[f.src] = f.rssi
        primary = max(lt, key=lambda s: len(lt[s]))  # основной канал — самый частый передатчик (точка доступа)
        self._motion_update(p, recv_t, primary)
        mine = [f for f in p.frames if f.src == primary]
        if not mine:
            return
        last = mine[-1]
        st.rssi = last.rssi
        if recv_t - self._last_emit.get(p.device_id, -1e9) < self.min_interval - 1e-9:
            return
        amp = ltf_amplitude(last.data, "ht" if len(last.data) >= 256 else "lltf")
        if amp is None:
            return
        self._last_emit[p.device_id] = recv_t
        self._broadcast({"type": "amp", "device_id": p.device_id, "src": last.src, "t": recv_t, "rssi": last.rssi,
                         "sig_mode": last.sig_mode, "amp": [round(float(x), 1) for x in amp]})

    def _motion_update(self, p: Packet, recv_t: float, primary: str) -> None:
        for src in {f.src for f in p.frames}:
            self._motion_link(p, recv_t, src, src == primary)

    def _motion_link(self, p: Packet, recv_t: float, src: str, primary: bool) -> None:
        m = self._motion.get((p.device_id, src))
        if m is None:
            m = self._motion[(p.device_id, src)] = _Motion()
        last_us = p.frames[-1].t_us
        for f in p.frames:
            if f.src != src:
                continue
            if len(f.data) >= 256:
                kind = "ht"
                m.last_ht = recv_t
            elif recv_t - m.last_ht > LEGACY_AFTER_S:
                kind = "lltf"  # передатчик шлёт только кадры 11a/g: берём L-LTF
            else:
                continue  # у линии есть HT-кадры: редкие L-LTF не подмешиваем
            if kind != m.kind:
                m.kind = kind
                m.window.clear()
                m.baseline.clear()
                m.long.clear()
            amp = ltf_amplitude(f.data, kind)
            if amp is not None:
                m.window.append((recv_t, amp))
                lag = (last_us - f.t_us) / 1e6  # время самого кадра по часам платы (внутри пакета)
                ft = recv_t - lag if 0.0 <= lag < 2.0 else recv_t
                if not m.long or ft - m.long[-1][0] >= 1 / BREATH_HZ:
                    m.long.append((ft, amp))
        while m.window and m.window[0][0] <= recv_t - MOTION_WINDOW_S:
            m.window.popleft()
        while m.long and m.long[0][0] <= recv_t - BREATH_WINDOW_S:
            m.long.popleft()
        if recv_t - m.last_breath >= BREATH_PERIOD_S and m.long and m.long[-1][0] - m.long[0][0] >= BREATH_MIN_S:
            m.last_breath = recv_t
            self._breath(p.device_id, src, primary, recv_t, m)
        if recv_t - m.last_emit < MOTION_PERIOD_S - 1e-9 or len(m.window) < 2:
            return
        m.last_emit = recv_t
        raw = motion_energy(np.stack([a for _, a in m.window]), normalize=True)
        m.baseline.append((recv_t, raw))
        while m.baseline and m.baseline[0][0] <= recv_t - MOTION_BASELINE_S:
            m.baseline.popleft()
        med = float(np.median([r for _, r in m.baseline]))
        energy = raw / med if med > 1e-6 else 0.0
        self._broadcast({"type": "motion", "device_id": p.device_id, "src": src, "primary": primary, "t": recv_t,
                         "energy": round(energy, 3), "raw": round(raw, 3)})

    def _breath(self, device_id: str, src: str, primary: bool, now: float, m: _Motion) -> None:
        t = np.array([x for x, _ in m.long])
        r = score_series(t, amplitude_series(np.stack([a for _, a in m.long])), t[0], t[-1] + 1e-6)
        if r is None:
            return
        score, bpm, spread = r
        self._broadcast({"type": "breath", "device_id": device_id, "src": src, "primary": primary, "t": now,
                         "bpm": round(bpm, 1), "score": round(score, 1), "spread": round(spread, 1),
                         "ok": bool(score > SCORE_OK and spread < SPREAD_OK)})

    def publish(self, msg: dict) -> None:
        """Send an arbitrary message (e.g. live phone position) to all live subscribers."""
        self._broadcast(msg)

    def _broadcast(self, msg: dict) -> None:
        self._fanout(msg)
        for fn in list(self.listeners):  # после рассылки: ответ слушателя идёт клиентам следом за исходным
            try:
                fn(msg)
            except Exception:  # слушатель не должен ронять приём пакетов
                log.exception("live listener failed")

    def _fanout(self, msg: dict) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for sub in list(self._subs):
            if sub.loop is None or sub.loop is current:
                sub._put(msg)
            else:
                sub.loop.call_soon_threadsafe(sub._put, msg)
