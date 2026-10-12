"""Apartment plan from recorded walk sessions (spec 6.4) and live validation of capture events."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .geometry import GeometryError, frame_from_points, polygon_valid, xr_to_floor
from .store import Store

TRAJ_MIN_DT = 0.2  # 5 Гц


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # недописанная строка после сбоя
    return out


def _last(events: list[dict], kind: str) -> dict | None:
    for e in reversed(events):
        if e.get("kind") == kind and e.get("p") is not None:
            return e
    return None


def build_plan(store: Store) -> dict:
    rooms: dict[str, list] = {}
    objects: list[dict] = []
    trajectories: dict[str, list] = {}
    skipped: dict[str, str] = {}
    router_xy = None
    for s in store.list_sessions():
        if s.stopped is None or s.kind != "walk":
            continue
        d = store.session_dir(s.id)
        events = _read_jsonl(d / "events.jsonl")
        origin, router = _last(events, "origin"), _last(events, "router")
        reason = ""
        if origin is None:
            reason = "no origin mark (board)"
        elif router is None:
            reason = "no router mark"
        else:
            try:
                frame = frame_from_points(origin["p"], router["p"])
            except GeometryError as e:
                reason = str(e)
        if reason:
            skipped[s.id] = reason
            store.set_plan_status(s.id, False, reason)
            continue
        router_xy = [round(v, 3) for v in frame.apply(router["p"])]
        current: tuple[str, list] | None = None
        for e in events:
            k = e.get("kind")
            if k == "room_new":
                current = (str(e.get("name") or "комната"), [])
            elif k == "corner" and current is not None and e.get("p") is not None:
                current[1].append([round(v, 3) for v in frame.apply(e["p"])])
            elif k == "room_close" and current is not None:
                if polygon_valid(current[1]) is None:
                    rooms[current[0]] = current[1]
                current = None
            elif k == "object" and e.get("p") is not None:
                objects.append({"type": e.get("type", "другое"), "xy": [round(v, 3) for v in frame.apply(e["p"])],
                                "size": e.get("size") or [0.5, 0.5, 0.8], "session": s.id})
        traj, last_t = [], -math.inf
        for p in _read_jsonl(d / "pose.jsonl"):
            if p.get("p") is None or p["t"] - last_t < TRAJ_MIN_DT - 1e-9:
                continue
            x, y = frame.apply(p["p"])
            traj.append([round(p["t"], 3), round(x, 3), round(y, 3)])
            last_t = p["t"]
        trajectories[s.id] = traj
        store.set_plan_status(s.id, True, "")
    return {
        "rooms": [{"name": n, "polygon": poly} for n, poly in rooms.items()],
        "objects": objects,
        "board": [0.0, 0.0],
        "router": router_xy,
        "trajectories": trajectories,
        "skipped": skipped,
    }


class RoomTracker:
    """Validates capture events of the active session as they arrive (rigid-transform invariant checks)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.origin = None
        self.corners: list | None = None

    def check(self, kind: str, rec: dict) -> tuple[str, str] | None:
        if kind == "session_start":
            self.reset()
        elif kind == "origin" and rec.get("p") is not None:
            self.origin = rec["p"]
        elif kind == "router":
            if self.origin is None:
                return ("bad_router", "mark the board position first")
            dist = math.dist(xr_to_floor(self.origin), xr_to_floor(rec.get("p") or self.origin))
            if dist < 0.5:
                return ("bad_router", f"router is {dist:.2f} m from the board; walk to the router first")
        elif kind == "room_new":
            self.corners = []
        elif kind == "corner":
            if self.corners is None:
                return ("no_room", "start a new room first")
            self.corners.append(xr_to_floor(rec.get("p") or (0, 0, 0)))
        elif kind == "room_close":
            if self.corners is None:
                return ("no_room", "no open room")
            problem = polygon_valid(self.corners)
            if problem:
                return ("bad_polygon", problem)
            self.corners = None
        return None
