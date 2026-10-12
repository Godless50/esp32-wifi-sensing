"""UDP ingest: signature check, replay protection (spec 4.5), counters."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .keys import KeyStore
from .protocol import Packet, ProtocolError, parse_packet, peek_device_id

log = logging.getLogger(__name__)


@dataclass
class _DevState:
    current_boot: int
    last_seq: int
    retired: deque = field(default_factory=lambda: deque(maxlen=64))


class IngestService(asyncio.DatagramProtocol):
    def __init__(self, keys: KeyStore, on_packet: Callable[[Packet, float], None]):
        self.keys = keys
        self.on_packet = on_packet
        self.counters: dict[str, int] = {
            "accepted": 0,
            "rejected_unknown": 0,
            "rejected_hmac": 0,
            "rejected_malformed": 0,
            "rejected_replay": 0,
            "callback_errors": 0,
        }
        self.unknown_devices: set[str] = set()
        self._state: dict[str, _DevState] = {}

    def handle(self, buf: bytes, recv_t: float) -> bool:
        try:
            dev = peek_device_id(buf)
        except ProtocolError:
            self.counters["rejected_malformed"] += 1
            return False
        key = self.keys.get(dev)
        if key is None:
            self.counters["rejected_unknown"] += 1
            self.unknown_devices.add(dev)
            return False
        try:
            pkt = parse_packet(buf, key)
        except ProtocolError as e:
            self.counters["rejected_hmac" if "hmac" in str(e) else "rejected_malformed"] += 1
            return False
        if not self._fresh(pkt):
            self.counters["rejected_replay"] += 1
            return False
        self.counters["accepted"] += 1
        try:
            self.on_packet(pkt, recv_t)
        except Exception:  # потребитель не должен ронять приём
            self.counters["callback_errors"] += 1
            log.exception("on_packet failed")
        return True

    def _fresh(self, pkt: Packet) -> bool:
        st = self._state.get(pkt.device_id)
        if st is None:
            self._state[pkt.device_id] = _DevState(pkt.boot_id, pkt.seq)
            return True
        if pkt.boot_id == st.current_boot:
            if pkt.seq <= st.last_seq:
                return False
            st.last_seq = pkt.seq
            return True
        if pkt.boot_id in st.retired:
            return False
        st.retired.append(st.current_boot)
        st.current_boot, st.last_seq = pkt.boot_id, pkt.seq
        return True

    # asyncio.DatagramProtocol
    def datagram_received(self, data: bytes, addr) -> None:
        self.handle(data, time.monotonic())
