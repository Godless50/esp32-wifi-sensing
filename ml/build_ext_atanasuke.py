"""External pretraining data: atanasuke/wifi-csi-9action-pose (HF, MIT; ESP32-C6, HT20, ping CSI, video pose labels).

Every (project, transmitter) stream is cut into 4 s windows (stride 1 s) and turned into our per-link sequence
format (build_seq.link_seq: 40 steps x 4 channels). Their links run at ~93 Hz, ours at ~20 Hz, so each window is
built from a random subset of frames thinned to 12-40 Hz (several rates per window = augmentation that also matches
our rate). Labels per window, from the video frames inside it:
  label9  majority of the 9 posture states (stand, sit, lie and the 6 transitions) if >= 70 % agree, else -1
  speed   body motion: mean speed of confident COCO-17 joints, in body heights per second (log1p(10*v))

usage: .venv-rocm/bin/python ml/build_ext_atanasuke.py --src data/ext/atanasuke --out data/ml/ext_atanasuke.npz
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from build_seq import NC, NT, link_seq

WIN_S, STRIDE_S = 4.0, 1.0


def frame_speed(f: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per video frame: time (s) and joint speed in body heights per second (nan where pose unreliable)."""
    t = f.wall_ms.to_numpy() / 1000
    kp = np.stack(f.keypoints.to_numpy()).reshape(-1, 17, 3)
    ok = kp[:, :, 2] > 0.3
    xy = np.where(ok[..., None], kp[:, :, :2], np.nan)
    h = np.nanmax(xy[:, :, 1], 1) - np.nanmin(xy[:, :, 1], 1)  # рост в кадре, пикс
    d = np.linalg.norm(np.diff(xy, axis=0), axis=2)  # [n-1, 17]
    dt = np.diff(t)[:, None]
    v = np.nanmean(d / np.maximum(dt, 1e-3), 1) / np.maximum(h[1:], 20)
    return t[1:], v


def one_project(args):
    raw_path, rates, seed = args
    rng = np.random.default_rng(seed)
    pid = raw_path.stem
    r = pd.read_parquet(raw_path)
    f = pd.read_parquet(raw_path.parent.parent / "frames" / raw_path.name)
    f = f[f.in_keep_range]
    ft, flab = f.wall_ms.to_numpy() / 1000, f.label.to_numpy()
    st, sv = frame_speed(f)
    X, L9, SP, DV = [], [], [], []
    for dev, g in r.groupby("device_id"):
        g = g.sort_values("pc_ts_ms")
        t = g.pc_ts_ms.to_numpy() / 1000
        H = np.stack(g.csi_real.to_numpy()) + 1j * np.stack(g.csi_imag.to_numpy())
        A = np.abs(H)
        amp_med = np.median(A[:, A.mean(0) > 0.1 * A.mean(0).max()])
        H = (H * (20.0 / max(amp_med, 1e-6))).astype(np.complex64)  # уровень как у наших int8-кадров
        for t0 in np.arange(t[0], t[-1] - WIN_S, STRIDE_S):
            m = (ft >= t0) & (ft < t0 + WIN_S)
            if m.sum() < 0.5 * WIN_S * 15:
                continue
            lab = flab[m]
            vals, cnt = np.unique(lab, return_counts=True)
            l9 = int(vals[cnt.argmax()]) if cnt.max() >= 0.7 * len(lab) and vals[cnt.argmax()] >= 0 else -1
            ms = (st >= t0) & (st < t0 + WIN_S)
            sp = float(np.nanmedian(sv[ms])) if np.isfinite(sv[ms]).sum() >= 10 else np.nan
            i0, i1 = np.searchsorted(t, [t0, t0 + WIN_S])
            for rate in rates:
                keep = np.sort(rng.choice(np.arange(i0, i1), size=min(i1 - i0, int(rate * WIN_S)), replace=False))
                X.append(link_seq(t[keep], H[keep], t0)[None])
                L9.append(l9); SP.append(np.log1p(10 * sp) if np.isfinite(sp) else np.nan); DV.append(dev)
    print(f"{pid}: {len(X)} windows", flush=True)
    return pid, np.stack(X).astype(np.float32), L9, SP, DV


def main() -> None:
    import multiprocessing as mp
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/ext/atanasuke")
    ap.add_argument("--out", default="data/ml/ext_atanasuke.npz")
    ap.add_argument("--rates", default="12,20,40")
    ap.add_argument("--jobs", type=int, default=12)
    a = ap.parse_args()
    rates = [float(r) for r in a.rates.split(",")]
    paths = sorted(Path(a.src, "raw_csi").glob("*.parquet"))
    with mp.Pool(a.jobs, initializer=warnings.filterwarnings, initargs=("ignore",)) as pool:
        parts = pool.map(one_project, [(p, rates, i) for i, p in enumerate(paths)])
    X = np.concatenate([p[1] for p in parts])
    L9 = sum((p[2] for p in parts), []); SP = sum((p[3] for p in parts), []); DV = sum((p[4] for p in parts), [])
    PR = sum(([p[0]] * len(p[1]) for p in parts), [])
    np.savez_compressed(a.out, X=X, label9=np.array(L9, np.int8), speed=np.array(SP, np.float32),
                        project=np.array(PR), device=np.array(DV))
    print("saved", a.out, X.shape, "labelled", int((np.array(L9) >= 0).sum()))


if __name__ == "__main__":
    main()
