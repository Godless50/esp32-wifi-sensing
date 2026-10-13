"""Build a training set from recorded sessions: 2 s windows of HT-LTF amplitude + labels.

Labels (v0):
  motion  — 1 if somebody moves in the window.
            static sessions: from spoken occupancy labels (activity contains a movement word);
            walk sessions:   phone speed over the window > SPEED_MOVING (the carrier walks).
  zone    — zone index of the walking phone (walk sessions only, -1 elsewhere), from plan polygons.

usage: python build_dataset.py --data /data --out /data/ml/ds_v0.npz
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from wsn.geometry import GeometryError, frame_from_points, point_in_polygon
from wsn.keys import KeyStore
from wsn.live import ltf_amplitude
from wsn.protocol import CSI_TYPES, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

FS = 50.0  # частота сетки, Гц
WIN = 100  # 2 с
STRIDE = 25  # 0.5 с
SPEED_MOVING = 0.3  # м/с
SPEED_STILL = 0.1
MOVE_WORDS = re.compile(r"ход|идёт|идет|иду|транзит|встал|встаю|ушёл|ушел|пошёл|пошел", re.I)
ZONES = ["спальня", "зал", "кухня", "коридор", "санузел", "комната"]


def read_frames(path: Path, keys: KeyStore) -> tuple[np.ndarray, np.ndarray]:
    """Server-time stamps and HT-LTF amplitudes [N, 64] of the board's AP link frames."""
    ts, amps = [], []
    for recv_t, buf in read_csi_bin(path):
        try:
            key = keys.get(peek_device_id(buf))
            p = parse_packet(buf, key) if key else None
        except ProtocolError:
            continue
        if p is None or p.type not in CSI_TYPES or not p.frames:
            continue
        last_us = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) < 256:
                continue
            a = ltf_amplitude(f.data, "ht")
            if a is None:
                continue
            ts.append(recv_t - (last_us - f.t_us) / 1e6)
            amps.append(a)
    if not ts:
        return np.zeros(0), np.zeros((0, 64), np.float32)
    order = np.argsort(ts)
    return np.asarray(ts)[order], np.stack(amps).astype(np.float32)[order]


def resample(ts: np.ndarray, amps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Zero-order hold onto a uniform FS grid; samples older than 0.2 s are invalid (NaN)."""
    grid = np.arange(ts[0], ts[-1], 1.0 / FS)
    idx = np.searchsorted(ts, grid, side="right") - 1
    out = amps[idx].copy()
    out[grid - ts[idx] > 0.2] = np.nan
    return grid, out


def moving_from_zones(zones: dict) -> bool:
    return any(MOVE_WORDS.search(z.get("activity", "")) for z in zones.values() if z.get("count", 0) > 0)


def load_plan(data: Path) -> dict:
    ov = json.loads((data / "plan_overrides.json").read_text(encoding="utf-8"))
    polys = {n: ov["rooms"][n] for n in ZONES if n in ov.get("rooms", {})}
    if "кухня" in ov.get("zones", {}):
        polys["кухня"] = ov["zones"]["кухня"]
    return polys


def zone_of(xy, polys: dict) -> int:
    if "кухня" in polys and point_in_polygon(xy, polys["кухня"]):  # зона внутри зала — первой
        return ZONES.index("кухня")
    for name, poly in polys.items():
        if name != "кухня" and point_in_polygon(xy, poly):
            return ZONES.index(name)
    return -1


def session_windows(sdir: Path, keys: KeyStore, polys: dict):
    meta = json.loads((sdir / "meta.json").read_text())
    ts, amps = read_frames(sdir / "csi.bin", keys)
    if len(ts) < WIN:
        return None
    grid, A = resample(ts, amps)
    events = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    poses = []
    if (sdir / "pose.jsonl").exists():
        poses = [json.loads(x) for x in (sdir / "pose.jsonl").read_text().splitlines() if x.strip()]
    poses = [p for p in poses if p.get("p")]
    frame = None
    origin = next((e for e in reversed(events) if e.get("kind") == "origin" and e.get("p")), None)
    router = next((e for e in reversed(events) if e.get("kind") == "router" and e.get("p")), None)
    if origin and router:
        try:
            frame = frame_from_points(origin["p"], router["p"])
        except GeometryError:
            frame = None
    pt = np.array([p["t"] for p in poses]) if poses else np.zeros(0)
    pxz = np.array([[p["p"][0], p["p"][2]] for p in poses]) if poses else np.zeros((0, 2))
    occ = sorted((e["t"], e["zones"]) for e in events if e.get("kind") == "occupancy")
    X, motion, zone, tcenter = [], [], [], []
    for s in range(0, len(grid) - WIN, STRIDE):
        w = A[s : s + WIN]
        if np.isnan(w).any():
            continue
        t0, t1 = grid[s], grid[s + WIN - 1]
        tc = (t0 + t1) / 2
        z = -1
        if meta["kind"] == "static":
            state = [zs for t, zs in occ if t <= t0]
            if not state or any(t0 < t <= t1 for t, _ in occ):
                continue  # окно должно целиком лежать внутри одного состояния разметки
            m = int(moving_from_zones(state[-1]))
        elif meta["kind"] == "walk" and len(pt) > 10:
            sel = (pt >= t0) & (pt <= t1)
            if sel.sum() < 20:
                continue
            path = pxz[sel]
            speed = np.sum(np.linalg.norm(np.diff(path, axis=0), axis=1)) / (t1 - t0)
            if speed > SPEED_MOVING:
                m = 1
            elif speed < SPEED_STILL:
                m = 0
            else:
                continue  # переходные окна не используем
            if frame is not None:
                c = path[len(path) // 2]
                z = zone_of(frame.apply([c[0], 0.0, c[1]]), polys)
        else:
            continue
        X.append(w)
        motion.append(m)
        zone.append(z)
        tcenter.append(tc)
    if not X:
        return None
    return {"X": np.stack(X), "motion": np.array(motion, np.int8), "zone": np.array(zone, np.int8),
            "t": np.array(tcenter), "session": meta["id"], "kind": meta["kind"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/data/ml/ds_v0.npz")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    polys = load_plan(data)
    parts = []
    for sdir in sorted((data / "sessions").iterdir()):
        if not (sdir / "meta.json").exists():
            continue
        r = session_windows(sdir, keys, polys)
        if r is None:
            print(f"{sdir.name}: skipped")
            continue
        print(f"{sdir.name} {r['kind']:6s} windows={len(r['motion']):5d} moving={int((r['motion'] == 1).sum()):5d} "
              f"zones={np.bincount(r['zone'][r['zone'] >= 0], minlength=len(ZONES)).tolist()}")
        parts.append(r)
    X = np.concatenate([p["X"] for p in parts]).astype(np.float32)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, X=X, motion=np.concatenate([p["motion"] for p in parts]),
                        zone=np.concatenate([p["zone"] for p in parts]),
                        t=np.concatenate([p["t"] for p in parts]),
                        session=np.concatenate([[p["session"]] * len(p["motion"]) for p in parts]),
                        zones=np.array(ZONES))
    print(f"saved {out}: X={X.shape}")


if __name__ == "__main__":
    main()
