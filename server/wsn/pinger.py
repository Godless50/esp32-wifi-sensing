"""Pinger: makes fixed Wi-Fi devices transmit, so the board can capture their CSI (extra radio links).

Every ping reply (and its forwarding by a mesh node) is a frame on the 2.4 GHz channel; the board, in
promiscuous mode with the device MAC in its link list, records CSI of the device -> board path.
Config: data/links.json  {"ping_hz": 20, "targets": [{"ip": "192.168.4.2", "mac": "...", "name": "..."}]}
Uses unprivileged ICMP datagram sockets (no raw socket / root needed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def echo_request(ident: int, seq: int, payload: bytes = b"wsn-link") -> bytes:
    hdr = struct.pack("!BBHHH", 8, 0, 0, ident & 0xFFFF, seq & 0xFFFF)
    return struct.pack("!BBHHH", 8, 0, checksum(hdr + payload), ident & 0xFFFF, seq & 0xFFFF) + payload


@dataclass
class Target:
    ip: str
    mac: str = ""
    name: str = ""
    sent: int = 0
    replies: int = 0


@dataclass
class PingerConfig:
    ping_hz: float = 20.0
    targets: list[Target] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "PingerConfig":
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        hz = float(d.get("ping_hz", 20.0))
        if not 0.5 <= hz <= 100:
            raise ValueError("ping_hz must be within 0.5..100")
        return cls(hz, [Target(t["ip"], t.get("mac", ""), t.get("name", "")) for t in d.get("targets", [])])


class Pinger:
    def __init__(self, cfg: PingerConfig, sock_factory=None):
        self.cfg = cfg
        self._factory = sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP))
        self._sock = None
        self._seq = 0

    def stats(self) -> list[dict]:
        return [{"ip": t.ip, "mac": t.mac, "name": t.name, "sent": t.sent, "replies": t.replies}
                for t in self.cfg.targets]

    def tick(self) -> None:
        """Send one echo to every target and drain replies (non-blocking)."""
        if self._sock is None:
            self._sock = self._factory()
            self._sock.setblocking(False)
        self._seq += 1
        for t in self.cfg.targets:
            try:
                self._sock.sendto(echo_request(0, self._seq), (t.ip, 0))
                t.sent += 1
            except OSError as e:  # устройство пропало из сети — не мешает остальным
                log.debug("ping %s: %s", t.ip, e)
        by_ip = {t.ip: t for t in self.cfg.targets}
        while True:
            try:
                _, (ip, _) = self._sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if ip in by_ip:
                by_ip[ip].replies += 1

    async def run(self) -> None:
        if not self.cfg.targets:
            return
        period = 1.0 / self.cfg.ping_hz
        log.info("pinger: %d targets at %.0f Hz", len(self.cfg.targets), self.cfg.ping_hz)
        while True:
            try:
                self.tick()
            except OSError as e:
                log.warning("pinger socket: %s", e)
                self._sock = None
                await asyncio.sleep(5)
            await asyncio.sleep(period)
