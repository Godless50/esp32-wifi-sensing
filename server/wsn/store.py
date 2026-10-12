"""Recording sessions: SQLite metadata + per-session files (spec 6.2).

data/sessions/<id>/csi.bin   records: u64 recv_ns (server monotonic) | u16 len | datagram bytes
data/sessions/<id>/pose.jsonl, events.jsonl, meta.json
Only one session is active at a time; append_* calls without an active session are ignored.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import struct
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

KINDS = ("walk", "empty")
_REC = struct.Struct("<QH")


class SessionBusy(RuntimeError):
    pass


class SessionNotFound(KeyError):
    pass


@dataclass
class SessionInfo:
    id: str
    kind: str
    device_id: str
    started: float          # server monotonic
    started_wall: float     # unix time, for display
    stopped: float | None = None
    csi_packets: int = 0
    poses: int = 0
    events: int = 0
    plan_ok: bool = False
    plan_reason: str = ""
    duration_s: float | None = None  # "empty": planned length, auto-stop when reached


def read_csi_bin(path: Path) -> list[tuple[float, bytes]]:
    out = []
    data = Path(path).read_bytes()
    off = 0
    while off + _REC.size <= len(data):
        ns, n = _REC.unpack_from(data, off)
        off += _REC.size
        if off + n > len(data):
            break  # обрезанная последняя запись (сбой питания) — игнорируем
        out.append((ns / 1e9, data[off : off + n]))
        off += n
    return out


class Store:
    def __init__(self, db_path: Path, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, kind TEXT, device_id TEXT, started REAL,"
            " started_wall REAL, stopped REAL, csi_packets INT, poses INT, events INT, plan_ok INT, plan_reason TEXT,"
            " duration_s REAL)"
        )
        self._db.commit()
        self._active: SessionInfo | None = None
        self._files: dict[str, object] = {}
        self.last_activity = 0.0
        self._close_interrupted()

    # --- lifecycle -----------------------------------------------------
    def _close_interrupted(self) -> None:
        rows = self._db.execute("SELECT id, started FROM sessions WHERE stopped IS NULL").fetchall()
        for sid, started in rows:
            d = self.session_dir(sid)
            csi = len(read_csi_bin(d / "csi.bin")) if (d / "csi.bin").exists() else 0
            poses = self._count_lines(d / "pose.jsonl")
            events = self._count_lines(d / "events.jsonl")
            self._db.execute(
                "UPDATE sessions SET stopped=?, csi_packets=?, poses=?, events=?, plan_reason=? WHERE id=?",
                (started, csi, poses, events, "interrupted", sid),
            )
        self._db.commit()

    @staticmethod
    def _count_lines(p: Path) -> int:
        return sum(1 for _ in p.open()) if p.exists() else 0

    def session_dir(self, sid: str) -> Path:
        if "/" in sid or ".." in sid:
            raise SessionNotFound(sid)
        return self.sessions_dir / sid

    def start_session(self, kind: str, device_id: str, now: float, duration_s: float | None = None) -> SessionInfo:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if kind == "empty" and duration_s is None:
            duration_s = 600.0
        if duration_s is not None and not (10.0 <= duration_s <= 4 * 3600):
            raise ValueError("duration_s must be within 10 s .. 4 h")
        with self._lock:
            if self._active is not None:
                raise SessionBusy(self._active.id)
            sid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            info = SessionInfo(sid, kind, device_id.lower(), now, time.time(), duration_s=duration_s)
            d = self.session_dir(sid)
            d.mkdir(parents=True)
            (d / "meta.json").write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2))
            self._files = {
                "csi": open(d / "csi.bin", "ab"),
                "pose": open(d / "pose.jsonl", "a", encoding="utf-8"),
                "event": open(d / "events.jsonl", "a", encoding="utf-8"),
            }
            self._db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,NULL,0,0,0,0,'',?)",
                (sid, kind, info.device_id, now, info.started_wall, duration_s),
            )
            self._db.commit()
            self._active = info
            self.last_activity = now
            return info

    def stop_session(self, sid: str, now: float) -> SessionInfo:
        with self._lock:
            if self._active is None or self._active.id != sid:
                if self._row(sid) is None:
                    raise SessionNotFound(sid)
                return self._row(sid)  # уже остановлена
            info = self._active
            for f in self._files.values():
                f.flush()
                os.fsync(f.fileno())
                f.close()
            self._files = {}
            info.stopped = now
            self._db.execute(
                "UPDATE sessions SET stopped=?, csi_packets=?, poses=?, events=? WHERE id=?",
                (now, info.csi_packets, info.poses, info.events, sid),
            )
            self._db.commit()
            meta = self.session_dir(sid) / "meta.json"
            meta.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2))
            self._active = None
            return info

    def active(self) -> SessionInfo | None:
        return self._active

    def set_plan_status(self, sid: str, ok: bool, reason: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sessions SET plan_ok=?, plan_reason=? WHERE id=?", (int(ok), reason, sid))
            self._db.commit()

    def _row(self, sid: str) -> SessionInfo | None:
        r = self._db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return self._to_info(r) if r else None

    @staticmethod
    def _to_info(r) -> SessionInfo:
        return SessionInfo(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], bool(r[9]), r[10] or "", r[11])

    def get(self, sid: str) -> SessionInfo:
        with self._lock:
            if self._active and self._active.id == sid:
                return self._active
            info = self._row(sid)
            if info is None:
                raise SessionNotFound(sid)
            return info

    def list_sessions(self) -> list[SessionInfo]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM sessions ORDER BY started_wall").fetchall()
            out = [self._to_info(r) for r in rows]
            if self._active:
                out = [self._active if s.id == self._active.id else s for s in out]
            return out

    def delete_session(self, sid: str) -> None:
        with self._lock:
            if self._active and self._active.id == sid:
                raise SessionBusy(sid)
            if self._row(sid) is None:
                raise SessionNotFound(sid)
            self._db.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self._db.commit()
            shutil.rmtree(self.session_dir(sid), ignore_errors=True)

    # --- appends (active session only) ----------------------------------
    def append_csi(self, recv_t: float, buf: bytes) -> None:
        with self._lock:
            if self._active is None:
                return
            f = self._files["csi"]
            f.write(_REC.pack(int(recv_t * 1e9), len(buf)) + buf)
            f.flush()
            self._active.csi_packets += 1

    def append_pose(self, rec: dict) -> None:
        self._append("pose", rec, "poses")

    def append_event(self, rec: dict) -> None:
        self._append("event", rec, "events")

    def _append(self, key: str, rec: dict, counter: str) -> None:
        with self._lock:
            if self._active is None:
                return
            f = self._files[key]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            setattr(self._active, counter, getattr(self._active, counter) + 1)
