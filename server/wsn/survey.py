"""Survey of the home network by a board (firmware/main/survey.c): start it, keep its report, set its links.

The board finds which hosts answer an echo request, pings them one at a time and watches which transmitter starts
speaking. Devices it hears well become its links: it keeps pinging them itself and forwards their CSI (with the
sniffer on) like the other links. Commands are signed with the board key and bound to its current boot.
"""

from __future__ import annotations

import time

from .protocol import CMD_LINKS, CMD_SURVEY, TYPE_SURVEY, Packet, encode_cmd, links_args, survey_args

COPIES = 3  # одна и та же команда (тот же seq) трижды: UDP теряется, плата примет первую дошедшую


class SurveyControl:
    def __init__(self, keys, ingest, pusher):
        self.keys, self.ingest, self.pusher = keys, ingest, pusher
        self.reports: dict[str, dict] = {}
        self._seq = int(time.time() * 1000) & 0x7FFFFFFF

    def on_packet(self, p: Packet, recv_t: float) -> None:
        if p.type == TYPE_SURVEY and p.survey is not None:
            self.reports[p.device_id] = {"t": round(time.time(), 1), "boot_id": p.boot_id, **p.survey.summary()}

    def _send(self, device_id: str, cmd: int, args: bytes) -> None:
        dev = device_id.lower()
        transport = getattr(self.pusher, "transport", None)
        addr, key, boot = self.ingest.addrs.get(dev), self.keys.get(dev), self.ingest.boot_of(dev)
        if transport is None or addr is None or key is None or boot is None:
            raise RuntimeError(f"{dev}: board not seen yet")
        self._seq += 1
        pkt = encode_cmd(dev, boot, self._seq, key, cmd, args)
        for _ in range(COPIES):
            transport.sendto(pkt, addr)

    def start(self, device_id: str, seconds: int = 60, max_links: int = 6,
              exclude: list[str] | tuple[str, ...] = ()) -> dict:
        args = survey_args(seconds, max_links, list(exclude))
        self._send(device_id, CMD_SURVEY, args)
        self.reports[device_id.lower()] = {"t": round(time.time(), 1), "state": "requested", "progress": 0,
                                           "responders": 0, "entries": []}
        return self.reports[device_id.lower()]

    def set_links(self, device_id: str, links: list[tuple[str, str]]) -> None:
        self._send(device_id, CMD_LINKS, links_args(links))

    def status(self) -> dict:
        return {"surveys": self.reports}
