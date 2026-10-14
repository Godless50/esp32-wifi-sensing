"""Apartment plan from recorded walk sessions (spec 6.4) and live validation of capture events."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .geometry import Frame, GeometryError, frame_from_points, normalize_corners, polygon_valid, xr_to_floor
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


def device_marks(events: list[dict]) -> dict[str, dict]:
    """Phone marks next to boards / access points: mac -> its last mark, in the order the devices were first marked."""
    out: dict[str, dict] = {}
    for e in events:
        if e.get("kind") == "device" and e.get("p") is not None and e.get("role") in ("board", "ap") and e.get("mac"):
            out[str(e["mac"]).lower()] = e  # повторная отметка заменяет прежнюю; порядок словаря — по первой
    return out


def frame_marks(events: list[dict]) -> tuple[dict | None, dict | None]:
    """Marks that fix the plan axes: the origin is the "origin" mark or the first marked board; the x axis points to
    the "router" mark, else to the first marked access point (or the next board) far enough from the origin."""
    origin, router = _last(events, "origin"), _last(events, "router")
    marks = list(device_marks(events).values())
    boards = [e for e in marks if e.get("role") == "board"]
    if origin is None and boards:
        origin = boards[0]
    if router is None and origin is not None:
        cands = [e for e in marks if e.get("role") == "ap"] + [b for b in boards if b is not origin]
        for c in cands:  # плата может стоять у самого роутера: берём первую отметку, от которой строится ось
            try:
                frame_from_points(origin["p"], c["p"])
            except (GeometryError, TypeError, ValueError, IndexError):
                continue
            return origin, c
    return origin, router


def overrides_path(store: Store) -> Path:
    return store.sessions_dir.parent / "plan_overrides.json"


def build_plan(store: Store) -> dict:
    rooms: dict[str, list] = {}
    objects: list[dict] = []
    trajectories: dict[str, list] = {}
    skipped: dict[str, str] = {}
    warnings: dict[str, str] = {}
    router_xy = None
    walk_boards: dict[str, dict] = {}
    walk_aps: dict[str, dict] = {}
    walks = []
    for s in store.list_sessions():
        if s.stopped is None or s.kind != "walk":
            continue
        events = _read_jsonl(store.session_dir(s.id) / "events.jsonl")
        walks.append((s, events, *frame_marks(events)))
    # обходы с полной системой координат совместимы между собой; без роутера — только если других нет
    have_full = any(o is not None and r is not None for _, _, o, r in walks)
    for s, events, origin, router in walks:
        d = store.session_dir(s.id)
        reason, warn = "", ""
        if origin is None:
            reason = "no origin mark (board)"
        elif router is None and have_full:
            reason = "no router mark"
        elif router is None:
            # без роутера: начало — плата, оси — оси AR-сессии (поворот плана произвольный)
            frame = Frame(xr_to_floor(origin["p"]), 0.0)
            warn = "no router mark: axes are arbitrary"
        else:
            try:
                frame = frame_from_points(origin["p"], router["p"])
            except GeometryError as e:
                reason = str(e)
        if reason:
            skipped[s.id] = reason
            store.set_plan_status(s.id, False, reason)
            continue
        if warn:
            warnings[s.id] = warn
        elif router.get("role") != "board":  # ось на вторую плату: роутера на плане нет
            router_xy = [round(v, 3) for v in frame.apply(router["p"])]
        for mac, e in device_marks(events).items():  # места плат и точек доступа (поздний обход заменяет ранний)
            xy = [round(v, 3) for v in frame.apply(e["p"])]
            if e["role"] == "board":
                walk_boards[mac] = {"device_id": mac, "xy": xy, "z": 0.8, "note": "отмечена в обходе " + s.id}
            else:
                walk_aps[mac] = {"type": "точка доступа", "xy": xy, "size": [0.3, 0.3, 0.1], "rot": 0.0, "z": 1.0,
                                 "mac": mac, "session": s.id}
        current: tuple[str, list] | None = None

        def finish(room) -> None:
            if room is None:
                return
            poly = normalize_corners(room[1])
            if polygon_valid(poly) is None:
                rooms[room[0]] = [[round(x, 3), round(y, 3)] for x, y in poly]

        for e in events:
            k = e.get("kind")
            if k == "room_new":
                finish(current)  # незакрытая комната тоже засчитывается, если углов хватает
                current = (str(e.get("name") or "комната"), [])
            elif k == "corner" and current is not None and e.get("p") is not None:
                current[1].append(frame.apply(e["p"]))
            elif k == "room_close" and current is not None:
                finish(current)
                current = None
            elif k == "object" and e.get("p") is not None:
                objects.append({"type": e.get("type", "другое"), "xy": [round(v, 3) for v in frame.apply(e["p"])],
                                "size": e.get("size") or [0.5, 0.5, 0.8], "session": s.id})
        finish(current)
        traj, last_t = [], -math.inf
        for p in _read_jsonl(d / "pose.jsonl"):
            if p.get("p") is None or p["t"] - last_t < TRAJ_MIN_DT - 1e-9:
                continue
            x, y = frame.apply(p["p"])
            traj.append([round(p["t"], 3), round(x, 3), round(y, 3)])
            last_t = p["t"]
        trajectories[s.id] = traj
        store.set_plan_status(s.id, True, warn)
    # ручные правки плана (по словам пользователя) имеют приоритет над обходами
    edited = set()
    zones: list[dict] = []
    boards: list[dict] = []
    ov_path = overrides_path(store)
    if ov_path.exists():
        try:
            ov = json.loads(ov_path.read_text(encoding="utf-8"))
            for name, poly in ov.get("rooms", {}).items():
                pts = [(float(x), float(y)) for x, y in poly]
                if polygon_valid(pts) is None:
                    rooms[name] = [[round(x, 3), round(y, 3)] for x, y in pts]
                    edited.add(name)
            for name, poly in ov.get("zones", {}).items():  # зоны без стен внутри комнаты (открытая кухня)
                pts = [(float(x), float(y)) for x, y in poly]
                if polygon_valid(pts) is None:
                    zones.append({"name": name, "polygon": [[round(x, 3), round(y, 3)] for x, y in pts]})
            for dev, b in (ov.get("boards") or {}).items():  # места плат-приёмников (первая — начало координат)
                boards.append({"device_id": str(dev).lower(), "xy": [float(b["xy"][0]), float(b["xy"][1])],
                               "z": float(b.get("z", 0.8)), "note": str(b.get("note", ""))})
            if isinstance(ov.get("objects"), list):  # ручной список предметов заменяет отмеченные в обходах
                objects = [{"type": str(o["type"]), "xy": [float(o["xy"][0]), float(o["xy"][1])],
                            "size": [float(v) for v in o.get("size", [0.5, 0.5, 0.8])][:3],
                            "rot": float(o.get("rot", 0.0)), "z": float(o.get("z", 0.0)),  # z — высота низа (ТВ на стене)
                            "mac": str(o.get("mac") or "").lower(),  # передатчик дополнительной линии связи
                            "session": "edited"} for o in ov["objects"]]
        except (ValueError, TypeError, AttributeError):
            warnings["overrides"] = "plan_overrides.json is malformed; ignored"
    # платы и точки доступа, отмеченные телефоном в обходе (ручные правки тех же устройств важнее)
    have = {b["device_id"] for b in boards}
    boards += [b for mac, b in walk_boards.items() if mac not in have]
    have = {str(o.get("mac") or "").lower() for o in objects}
    objects += [o for mac, o in walk_aps.items() if mac not in have]
    return {
        "rooms": [{"name": n, "polygon": poly, "edited": n in edited} for n, poly in rooms.items()],
        "zones": zones,
        "objects": objects,
        "board": [0.0, 0.0],
        "boards": boards,
        "router": router_xy,
        "trajectories": trajectories,
        "skipped": skipped,
        "warnings": warnings,
    }


class RoomTracker:
    """Validates capture events of the active session as they arrive (rigid-transform invariant checks)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.origin = None
        self.router = None
        self.corners: list | None = None
        self.marks: list[dict] = []

    def check(self, kind: str, rec: dict) -> tuple[str, str] | None:
        # плата и роутер — в любом порядке: систему координат считаем после записи
        if kind == "session_start":
            self.reset()
        elif kind == "device":
            if rec.get("p") is None or rec.get("role") not in ("board", "ap") or not rec.get("mac"):
                return ("bad_device", "a mark needs p, mac and role board|ap")
            self.marks.append({**rec, "kind": "device"})
            o, r = frame_marks(self.marks)  # те же оси, что потом у плана: живая точка телефона на карте
            self.origin = o["p"] if o else None
            self.router = r["p"] if r else None
        elif kind in ("origin", "router") and rec.get("p") is not None:
            other = self.router if kind == "origin" else self.origin
            if other is not None:
                dist = math.dist(xr_to_floor(other), xr_to_floor(rec["p"]))
                if dist < 0.5:
                    code = "bad_router" if kind == "router" else "bad_origin"
                    return (code, f"board and router are {dist:.2f} m apart; mark each one standing next to it")
            setattr(self, kind, rec["p"])
            self.marks.append({**rec, "kind": kind})
        elif kind == "room_new":
            self.corners = []
        elif kind == "corner":
            if self.corners is None:
                return ("no_room", "start a new room first")
            self.corners.append(xr_to_floor(rec.get("p") or (0, 0, 0)))
        elif kind == "room_close":
            if self.corners is None:
                return ("no_room", "no open room")
            problem = polygon_valid(normalize_corners(self.corners))
            if problem:
                return ("bad_polygon", problem)
            self.corners = None
        return None
