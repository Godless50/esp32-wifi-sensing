"""UDP ingest: signature check, replay protection (spec 4.5), counters.

boot_id is a monotonic per-device boot counter (firmware keeps it in NVS). A packet is fresh when
boot_id > current, or boot_id == current and seq > last seq. The current boot per device is persisted
so an api restart does not reopen old boots. Rotating a device key (re-provisioning) resets its state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .keys import KeyStore
from .protocol import Packet, ProtocolError, parse_packet, peek_device_id

log = logging.getLogger(__name__)

UNKNOWN_MAX = 32


@dataclass
class _DevState:
    boot: int
    last_seq: int
    key_fp: str


def _fp(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


class IngestService(asyncio.DatagramProtocol):
    def __init__(
        self,
        keys: KeyStore,
        on_packet: Callable[[Packet, float], None],
        state_path: Path | None = None,
        on_raw: Callable[[bytes, str, float], None] | None = None,
    ):
        self.keys = keys
        self.on_packet = on_packet
        self.on_raw = on_raw
        self.state_path = Path(state_path) if state_path else None
        self.counters: dict[str, int] = {
            "accepted": 0,
            "rejected_unknown": 0,
            "rejected_hmac": 0,
            "rejected_malformed": 0,
            "rejected_replay": 0,
            "callback_errors": 0,
        }
        self._unknown: OrderedDict[str, float] = OrderedDict()
        self._state: dict[str, _DevState] = self._load_state()

    @property
    def unknown_devices(self) -> list[str]:
        return list(self._unknown)

    def _load_state(self) -> dict[str, _DevState]:
        if not self.state_path or not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text())
            # seq is not persisted: after a restart any seq of the current boot is accepted once
            return {k: _DevState(int(v["boot"]), -1, v["key_fp"]) for k, v in raw.items()}
        except (ValueError, KeyError, TypeError):
            log.exception("boot state unreadable, starting empty")
            return {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        data = {k: {"boot": v.boot, "key_fp": v.key_fp} for k, v in self._state.items()}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_path.parent, prefix=".boot-")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.state_path)

    def handle(self, buf: bytes, recv_t: float) -> bool:
        try:
            dev = peek_device_id(buf)
        except ProtocolError:
            self.counters["rejected_malformed"] += 1
            return False
        key = self.keys.get(dev)
        if key is None:
            self.counters["rejected_unknown"] += 1
            self._unknown[dev] = recv_t
            self._unknown.move_to_end(dev)
            while len(self._unknown) > UNKNOWN_MAX:
                self._unknown.popitem(last=False)
            return False
        try:
            pkt = parse_packet(buf, key)
        except ProtocolError as e:
            self.counters["rejected_hmac" if "hmac" in str(e) else "rejected_malformed"] += 1
            return False
        if not self._fresh(pkt, _fp(key)):
            self.counters["rejected_replay"] += 1
            return False
        self.counters["accepted"] += 1
        for cb, args in ((self.on_raw, (buf, pkt.device_id, recv_t)), (self.on_packet, (pkt, recv_t))):
            if cb is None:
                continue
            try:
                cb(*args)
            except Exception:  # потребитель не должен ронять приём
                self.counters["callback_errors"] += 1
                log.exception("ingest consumer failed")
        return True

    def _fresh(self, pkt: Packet, key_fp: str) -> bool:
        st = self._state.get(pkt.device_id)
        if st is None or st.key_fp != key_fp or pkt.boot_id > st.boot:
            self._state[pkt.device_id] = _DevState(pkt.boot_id, pkt.seq, key_fp)
            self._save_state()
            return True
        if pkt.boot_id < st.boot or pkt.seq <= st.last_seq:
            return False
        st.last_seq = pkt.seq
        return True

    # asyncio.DatagramProtocol
    def datagram_received(self, data: bytes, addr) -> None:
        self.handle(data, time.monotonic())
