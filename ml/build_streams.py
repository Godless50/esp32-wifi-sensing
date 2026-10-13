"""Continuous per-session streams for augmentation: uniform 50 Hz HT-LTF amplitudes + per-sample labels.

motion label per sample: 1 moving, 0 still, -1 unknown (transition / no label).
usage: python build_streams.py --data /data --out /data/ml/streams_v1.npz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from build_dataset import FS, SPEED_MOVING, SPEED_STILL, KeyStore, moving_from_zones, read_frames, resample


def labels_for(sdir: Path, grid: np.ndarray) -> np.ndarray:
    meta = json.loads((sdir / "meta.json").read_text())
    lab = np.full(len(grid), -1, np.int8)
    events = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if meta["kind"] == "static":
        occ = sorted((e["t"], e["zones"]) for e in events if e.get("kind") == "occupancy")
        for i, (t, z) in enumerate(occ):
            t_end = occ[i + 1][0] if i + 1 < len(occ) else grid[-1] + 1
            lab[(grid >= t + 1.0) & (grid < t_end - 1.0)] = int(moving_from_zones(z))  # ±1 с на реакцию
    elif meta["kind"] == "walk" and (sdir / "pose.jsonl").exists():
        poses = [json.loads(x) for x in (sdir / "pose.jsonl").read_text().splitlines() if x.strip()]
        poses = [p for p in poses if p.get("p")]
        if len(poses) < 10:
            return lab
        pt = np.array([p["t"] for p in poses])
        pxz = np.array([[p["p"][0], p["p"][2]] for p in poses])
        # скорость по 1-секундному окну вокруг каждого отсчёта сетки
        a = np.interp(grid - 0.5, pt, pxz[:, 0]), np.interp(grid - 0.5, pt, pxz[:, 1])
        b = np.interp(grid + 0.5, pt, pxz[:, 0]), np.interp(grid + 0.5, pt, pxz[:, 1])
        speed = np.hypot(b[0] - a[0], b[1] - a[1])
        covered = (grid > pt[0] + 0.5) & (grid < pt[-1] - 0.5)
        lab[covered & (speed > SPEED_MOVING)] = 1
        lab[covered & (speed < SPEED_STILL)] = 0
    return lab


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/data/ml/streams_v1.npz")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    arrays = {}
    for sdir in sorted((data / "sessions").iterdir()):
        if not (sdir / "meta.json").exists():
            continue
        ts, amps = read_frames(sdir / "csi.bin", keys)
        if len(ts) < 200:
            continue
        grid, A = resample(ts, amps)
        lab = labels_for(sdir, grid)
        ok = ~np.isnan(A).any(axis=1)
        print(f"{sdir.name}: {len(grid) / FS:6.0f} s, labelled moving={int((lab == 1).sum() / FS)} s "
              f"still={int((lab == 0).sum() / FS)} s, gaps={int((~ok).sum() / FS)} s")
        arrays[f"A_{sdir.name}"] = np.nan_to_num(A, nan=-1.0).astype(np.float16)
        arrays[f"L_{sdir.name}"] = np.where(ok, lab, -1).astype(np.int8)
        arrays[f"T_{sdir.name}"] = grid
    np.savez_compressed(a.out, **arrays)
    print("saved", a.out)


if __name__ == "__main__":
    main()
