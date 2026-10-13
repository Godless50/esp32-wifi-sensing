"""Two small public ESP32-S3 datasets in our per-link sequence format (build_seq.link_seq: 40 steps x 4 channels).

  WiCount (HF RS2002/WiCount): meeting room, 0-3 people, L-LTF 52 subcarriers, ~90 Hz, 15 files x 60 s.
      label n_all = number of people (folder name). The 0 and 3 classes have one file each, so every file is also
      split in time: windows starting in its last 25 % get part = "tail" (a held-out piece of every file).
  ESP32-S3 HAR (HF Marcolini/esp32s3-csi-har-2025, CC-BY-4.0): one person, 4 postures, ~20 Hz, 80 files.
      label pose: standing -> 0 стоит, sitting -> 1 сидит, lying -> 2 лежит, walking -> 3 идёт.

Frames are thinned to 12 / 20 / 40 Hz (as for atanasuke) so the rate matches our links.

usage: .venv-rocm/bin/python ml/build_ext_small.py --wicount data/ext/wicount/unz/data --har data/ext/esp32s3_har/data \
           --out data/ml/ext_small.npz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from build_seq import link_seq

WIN_S, STRIDE_S = 4.0, 1.0
POSE_HAR = {"standing": 0, "sitting": 1, "lying": 2, "walking": 3}


def parse_iq(cell: str, n_sc: int) -> np.ndarray:
    v = np.array(json.loads(cell), dtype=np.float32)
    iq = v[: 2 * n_sc].reshape(n_sc, 2)
    return iq[:, 0] + 1j * iq[:, 1]  # для амплитуды порядок re/im не важен


def windows(t: np.ndarray, H: np.ndarray, rates, rng):
    out = []
    for t0 in np.arange(t[0], t[-1] - WIN_S, STRIDE_S):
        i0, i1 = np.searchsorted(t, [t0, t0 + WIN_S])
        for rate in rates:
            k = min(i1 - i0, int(rate * WIN_S))
            if k < 12:
                continue
            keep = np.sort(rng.choice(np.arange(i0, i1), size=k, replace=False))
            out.append((t0, link_seq(t[keep], H[keep], t0)))
    return out


def load_file(path: Path, n_sc: int) -> tuple[np.ndarray, np.ndarray]:
    d = pd.read_csv(path)
    t = (d["local_timestamp"].to_numpy(np.float64) - d["local_timestamp"].iloc[0]) / 1e6
    H = np.zeros((len(d), 64), np.complex64)
    H[:, :n_sc] = np.stack([parse_iq(c, n_sc) for c in d["data"]])
    o = np.argsort(t, kind="stable")
    return t[o], H[o]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wicount", default="data/ext/wicount/unz/data")
    ap.add_argument("--har", default="data/ext/esp32s3_har/data")
    ap.add_argument("--out", default="data/ml/ext_small.npz")
    ap.add_argument("--rates", default="12,20,40")
    a = ap.parse_args()
    rates = [float(r) for r in a.rates.split(",")]
    rng = np.random.default_rng(0)
    X, src, group, part, n_all, pose = [], [], [], [], [], []
    for f in sorted(Path(a.wicount).glob("*/*.csv")):
        t, H = load_file(f, 52)
        dur = t[-1] - t[0]
        for t0, x in windows(t, H, rates, rng):
            X.append(x[None]); src.append("wicount"); group.append(f.stem); n_all.append(int(f.parent.name)); pose.append(-1)
            part.append("tail" if t0 - t[0] >= 0.75 * dur - WIN_S else "head")
    for f in sorted(Path(a.har).glob("*.csv")):
        cls = f.stem.rstrip("0123456789")
        if cls not in POSE_HAR:
            continue
        t, H = load_file(f, 64)  # первые 64 комплексных значения — L-LTF
        for t0, x in windows(t, H, rates, rng):
            X.append(x[None]); src.append("esp32s3_har"); group.append(f.stem); n_all.append(-1); pose.append(POSE_HAR[cls])
            part.append("all")
    X = np.concatenate(X)[:, None].astype(np.float32)  # [N, 1 линия, 40, 4]
    np.savez_compressed(a.out, X=X, src=np.array(src), group=np.array(group), part=np.array(part),
                        n_all=np.array(n_all, np.int8), pose=np.array(pose, np.int8))
    s = np.array(src)
    print("saved", a.out, X.shape, {k: int((s == k).sum()) for k in set(src)},
          "wicount by count", np.bincount(np.array(n_all)[s == "wicount"]).tolist(),
          "har by pose", np.bincount(np.array(pose)[s == "esp32s3_har"]).tolist())


if __name__ == "__main__":
    main()
