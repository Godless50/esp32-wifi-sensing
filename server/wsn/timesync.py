"""Phone ↔ server clock offset from ping/pong samples (spec 6.3): minimum-RTT sample of the last 20 wins."""

from __future__ import annotations

from collections import deque


class ClockSync:
    def __init__(self, keep: int = 20):
        self._samples: deque[tuple[float, float]] = deque(maxlen=keep)  # (rtt_s, offset_s)

    def add(self, c0: float, s: float, c1: float) -> None:
        """c0/c1: phone send/receive time of the ping (ms, performance.now); s: server time of the pong (s)."""
        rtt = (c1 - c0) / 1000.0
        if rtt < 0:
            return
        mid_phone = (c0 + c1) / 2000.0
        self._samples.append((rtt, s - mid_phone))

    @property
    def offset(self) -> float | None:
        if not self._samples:
            return None
        return min(self._samples)[1]

    @property
    def rtt(self) -> float | None:
        return min(self._samples)[0] if self._samples else None

    def to_server(self, t_phone_ms: float) -> float:
        off = self.offset
        if off is None:
            raise ValueError("no clock sync samples")
        return t_phone_ms / 1000.0 + off
