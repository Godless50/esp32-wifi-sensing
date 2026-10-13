"""Sequence dataset for the neural net: per scripted window (4 s, stride 1 s) and link, a 10 Hz sequence of 4 channels.

Per link and 0.1 s bin (AGC-normalised amplitude, live subcarriers):
  dev   mean over subcarriers of |bin mean - window mean|      (slow body motion / position change)
  jit   mean over subcarriers of the std inside the bin        (fast motion: arms, steps, fingers)
  pc1, pc2  projection of the bin mean on the 2 main directions of change in the window
Empty bins are linearly filled; a silent link is all zeros. Labels as in build_rich (act, zone, place, session).

usage (wsn-ml image): python build_seq.py --sessions 20261013-053501-ee64,20261013-065019-45cd --out /data/ml/seq_v1.npz
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from build_rich import LINKS, WIN_S, link_csi, load_occupancy, script_label, spoken_label
from wsn.keys import KeyStore

STEP_S, STRIDE_S = 0.1, 1.0
NT, NC = int(WIN_S / STEP_S), 4


def link_seq(t: np.ndarray, H: np.ndarray, t0: float) -> np.ndarray:
    out = np.zeros((NT, NC), np.float32)
    i0, i1 = np.searchsorted(t, [t0, t0 + WIN_S])
    if i1 - i0 < 12:
        return out
    tt, A = t[i0:i1], np.abs(H[i0:i1])
    live = A.mean(0) > 2
    if live.sum() < 8:
        return out
    A = A[:, live] / (A[:, live].mean(1, keepdims=True) + 1e-3)
    A = A - A.mean(0)
    b = np.minimum(((tt - t0) / STEP_S).astype(int), NT - 1)
    means = np.full((NT, A.shape[1]), np.nan, np.float32)
    jit = np.full(NT, np.nan, np.float32)
    for k in range(NT):
        m = b == k
        if m.any():
            means[k] = A[m].mean(0)
            jit[k] = A[m].std(0).mean() if m.sum() > 1 else np.nan
    ok = ~np.isnan(means[:, 0])
    if ok.sum() < 4:
        return out
    x = np.arange(NT)
    means = np.stack([np.interp(x, x[ok], means[ok, j]) for j in range(means.shape[1])], 1)
    okj = ~np.isnan(jit)
    jit = np.interp(x, x[okj], jit[okj]) if okj.sum() >= 2 else np.zeros(NT)
    _, _, vt = np.linalg.svd(means - means.mean(0), full_matrices=False)
    out[:, 0] = np.abs(means).mean(1)
    out[:, 1] = jit
    out[:, 2] = means @ vt[0]
    out[:, 3] = means @ vt[1] if len(vt) > 1 else 0
    return out * 100  # проценты от уровня сигнала


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--sessions", default="20261013-053501-ee64,20261013-065019-45cd")
    ap.add_argument("--out", default="/data/ml/seq_v1.npz")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    X, ACT, Z, PL, S, T, MV = [], [], [], [], [], [], []
    for sid in a.sessions.split(","):
        sdir = data / "sessions" / sid
        csi = link_csi(sdir / "csi.bin", keys)
        occ = load_occupancy(sdir)
        lo, hi = min(v[0][0] for v in csi.values()), max(v[0][-1] for v in csi.values())
        n = 0
        for t0 in np.arange(lo, hi - WIN_S, STRIDE_S):
            act, zone, _, place = script_label(occ, t0, t0 + WIN_S)
            y = spoken_label(occ, t0, t0 + WIN_S)
            if act < 0 and y is None:
                continue
            X.append(np.stack([link_seq(*csi[m], t0) if m in csi else np.zeros((NT, NC), np.float32) for m in LINKS]))
            ACT.append(act); Z.append(zone); PL.append(place); S.append(sid); T.append(t0); MV.append(y[0] if y else -1)
            n += 1
        print(f"{sid}: windows={n}", flush=True)
    np.savez_compressed(a.out, X=np.stack(X), act=np.array(ACT, np.int8), zone=np.array(Z, np.int8), place=np.array(PL),
                        session=np.array(S), t=np.array(T), moving=np.array(MV, np.int8), links=np.array(LINKS))
    print("saved", a.out, np.stack(X).shape)


if __name__ == "__main__":
    main()
