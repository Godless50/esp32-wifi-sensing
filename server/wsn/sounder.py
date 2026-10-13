"""Server-driven sounding of the links "access point -> board".

Every board gets ~100 tiny UDP datagrams per second (b"WSNS" + counter) at the address its packets come from.
The board's access point (its mesh node) transmits each of them to the board, so each one is a CSI sample of
that link; the board drops it after counting. Before, the boards pinged the gateway themselves: 3 boards x 100 Hz
of ICMP ran into the router's limits (the replies of one board vanished), and through a mesh node a ping that
waits for its reply gave only ~25 Hz. UDP forwarding is the normal fast path of the router and of the mesh.

No sounding while a model / firmware / settings push to that board runs: the chunks must not queue behind it.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time

log = logging.getLogger(__name__)

MAGIC = b"WSNS"
STALE_S = 10.0  # плата молчит дольше — не шлём (выключена или перезагружается)


class Sounder:
    def __init__(self, ingest, pusher=None, hz: float = 100.0, last_seen=None):
        if not 0 < hz <= 200:
            raise ValueError("sounding rate must be within (0, 200] Hz")
        self.ingest = ingest
        self.pusher = pusher
        self.hz = hz
        self.transport = None
        self.last_seen = last_seen  # callable dev -> monotonic time of the last packet, or None
        self.sent = 0
        self._seq = 0

    def _busy(self, dev: str) -> bool:
        st = self.pusher.pushes.get(dev) if self.pusher is not None else None
        return st is not None and st.state in ("starting", "sending", "activating")

    def tick(self, now: float | None = None) -> int:
        """Send one datagram to every live board; returns how many were sent."""
        tr = self.transport if self.transport is not None else getattr(self.pusher, "transport", None)
        if tr is None:
            return 0
        now = time.monotonic() if now is None else now
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        msg = MAGIC + struct.pack("<I", self._seq)
        n = 0
        for dev, addr in list(self.ingest.addrs.items()):
            if self._busy(dev):
                continue
            if self.last_seen is not None:
                t = self.last_seen(dev)
                if t is None or now - t > STALE_S:
                    continue
            tr.sendto(msg, addr)
            n += 1
        self.sent += n
        return n

    async def run(self) -> None:
        period = 1.0 / self.hz
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        log.info("sounder: %.0f datagrams/s to every board", self.hz)
        while True:
            try:
                self.tick()
            except Exception:  # отправка не должна ронять сервер
                log.exception("sounder tick failed")
            nxt += period
            delay = nxt - loop.time()
            if delay < -1.0:  # цикл событий стоял: не догоняем пачкой
                nxt = loop.time()
                delay = 0.0
            await asyncio.sleep(max(0.0, delay))
