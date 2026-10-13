"""Feeds accepted datagrams into the active session (its board -> csi.bin, every board -> csi_all.bin); auto-stops idle sessions."""

from __future__ import annotations

import logging

from .store import Store

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, store: Store, idle_timeout_s: float = 120.0):
        self.store = store
        self.idle_timeout_s = idle_timeout_s

    def on_datagram(self, buf: bytes, device_id: str, recv_t: float) -> None:
        s = self.store.active()
        if s is None:
            return
        self.store.append_csi_all(recv_t, buf)  # все платы: отметки сессии описывают всю квартиру
        if s.device_id == device_id.lower():
            self.store.append_csi(recv_t, buf)

    def note_activity(self, now: float) -> None:
        """Phone data (poses/events) keeps the session alive; CSI alone does not (board streams forever)."""
        self.store.last_activity = max(self.store.last_activity, now)

    def tick(self, now: float) -> None:
        s = self.store.active()
        if s is None:
            return
        if s.duration_s is not None:  # "empty": человек ушёл вместе с телефоном — только по длительности
            if now >= s.started + s.duration_s:
                log.info("session %s reached planned duration, stopping", s.id)
                self.store.stop_session(s.id, now)
            return
        if now - self.store.last_activity > self.idle_timeout_s:
            log.warning("session %s idle for %.0fs, stopping", s.id, now - self.store.last_activity)
            self.store.stop_session(s.id, now)
