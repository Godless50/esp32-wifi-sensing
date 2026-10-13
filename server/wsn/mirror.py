"""Copy every authenticated board datagram to a local Unix datagram socket (live learner on the host GPU).

The socket file lives in the data volume (data/run/csi.sock), so a process on the host can read what the API
container receives. Nobody listening -> datagrams are dropped silently; the API never blocks on the mirror.
"""

from __future__ import annotations

import logging
import socket
import time
from pathlib import Path

log = logging.getLogger(__name__)


class DatagramMirror:
    def __init__(self, path: Path):
        self.path = str(path)
        self.sent = 0
        self.dropped = 0
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._last_log = 0.0

    def send(self, buf: bytes) -> None:
        try:
            self._sock.sendto(buf, self.path)
            self.sent += 1
        except (FileNotFoundError, ConnectionRefusedError, BlockingIOError, OSError) as e:
            self.dropped += 1
            now = time.monotonic()
            if now - self._last_log > 300 and not isinstance(e, (FileNotFoundError, ConnectionRefusedError)):
                self._last_log = now
                log.warning("mirror %s: %s (dropped %d)", self.path, e, self.dropped)

    def close(self) -> None:
        self._sock.close()
