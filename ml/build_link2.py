"""Per-link dataset v2: 20 s windows with features that also see a STILL person (v1, 4 s mean-removed, only saw
motion: "somebody still near" was at chance).

Per link the amplitudes of the live subcarriers are normalised per frame (shape only, no absolute level) and
averaged into 0.1 s bins. For a window ending at t (20 s, step 1 s):
  motion    — mean over subcarriers of the std over time, last 4 s and whole 20 s;
  breath    — first principal component of the 20 s window: power in 0.15-0.6 Hz (breathing) and its share of
              0.05-3 Hz, plus the height of the breathing peak over the band median;
  fidget    — power share in 0.6-3 Hz;
  static    — distance of the window's mean shape from the link's reference shape (median shape over the
              previous REF_S seconds): a still person changes the multipath without moving;
  quality   — frames per second (a link that is barely heard says little).
Labels and geometry as in build_link.py. Nothing here says where the link is.

usage: PYTHONPATH=../server python build_link2.py --plan ../data/eval/plan_1450.json --out ../data/ml/link2_v1.npz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from build_link import coverage, link_label, read_links, timeline, TRANSITION_S
from wsn.keys import KeyStore
from wsn.person import Grid

BIN_S, WIN_S, REF_S = 0.1, 20.0, 600.0
FEATS = ["motion4", "motion20", "breath_pow", "breath_share", "breath_peak", "fidget_share", "static", "static_ref_n",
         "rate"]


def bins(t: np.ndarray, H: np.ndarray):
    A = np.abs(H)
    live = A.mean(0) > 2
    if live.sum() < 8:
        return None
    A = A[:, live]
    A = A / (A.mean(1, keepdims=True) + 1e-3)
    t0 = t[0]
    k = ((t - t0) / BIN_S).astype(int)
    n = k[-1] + 1
    cnt = np.bincount(k, minlength=n).astype(np.float32)
    S = np.zeros((n, A.shape[1]), np.float32)
    np.add.at(S, k, A)
    ok = cnt > 0
    S[ok] /= cnt[ok, None]
    # пропуски: линейная интерполяция по каждому поднесущему
    idx = np.arange(n)
    if (~ok).any() and ok.sum() >= 2:
        for j in range(S.shape[1]):
            S[~ok, j] = np.interp(idx[~ok], idx[ok], S[ok, j])
    return t0, S, cnt


def window_feats(S: np.ndarray, cnt: np.ndarray, ref: np.ndarray | None) -> np.ndarray:
    w4 = S[-int(4 / BIN_S):]
    motion4 = float(w4.std(0).mean())
    motion20 = float(S.std(0).mean())
    X = S - S.mean(0)
    _, _, vt = np.linalg.svd(X, full_matrices=False)
    pc = X @ vt[0]
    pc = (pc - pc.mean()) * np.hanning(len(pc))
    P = np.abs(np.fft.rfft(pc)) ** 2
    f = np.fft.rfftfreq(len(pc), BIN_S)
    band = lambda lo, hi: float(P[(f >= lo) & (f < hi)].sum())
    tot = band(0.05, 3.0) + 1e-9
    br = band(0.15, 0.6)
    bm = P[(f >= 0.15) & (f < 0.6)]
    peak = float(bm.max() / (np.median(bm) + 1e-9)) if len(bm) else 0.0
    shape = S.mean(0)
    static = float(np.abs(shape - ref).mean()) if ref is not None else 0.0
    rate = float(cnt.sum() / WIN_S)
    return np.array([motion4, motion20, np.log1p(br * 1e3), br / tot, np.log1p(peak), band(0.6, 3.0) / tot,
                     static, float(ref is not None), rate], np.float32)


def build(sid: str, sdir: Path, grid: Grid, keys: KeyStore):
    ev = timeline(sdir)
    if not ev:
        return None
    path = sdir / "csi_all.bin" if (sdir / "csi_all.bin").exists() else sdir / "csi.bin"
    links = read_links(path, keys)
    change_t = np.array([t for t, _ in ev])
    F, y, lid, ts = [], [], [], []
    nw, nr = int(WIN_S / BIN_S), int(REF_S / BIN_S)
    for (dev, src), (t, H) in links.items():
        cov = coverage(grid, dev, src)
        if cov is None:
            continue
        b = bins(t, H)
        if b is None:
            continue
        t0, S, cnt = b
        shapes = None
        for e in range(nw, len(S), int(1.0 / BIN_S)):
            tc = t0 + e * BIN_S
            if tc - WIN_S < ev[0][0]:
                continue
            k = int(np.searchsorted(change_t, tc - WIN_S / 2, side="right")) - 1
            near_change = np.min(np.abs(change_t - (tc - WIN_S / 2))) < TRANSITION_S + WIN_S / 2
            if k < 0 or near_change:
                continue
            lab = link_label(cov, ev[k][1])
            if lab < 0:
                continue
            w = S[e - nw:e]
            if cnt[e - nw:e].sum() < 100:
                continue
            lo = max(0, e - nr)
            ref = np.median(S[lo:e - nw:10], 0) if e - nw - lo >= 600 else None  # опорная форма: прошлые >= 60 с
            F.append(window_feats(w, cnt[e - nw:e], ref))
            y.append(lab)
            lid.append(f"{dev}|{src}")
            ts.append(tc)
    if not F:
        return None
    n = len(F)
    return {"F": np.stack(F), "y": np.array(y, np.int8), "link": np.array(lid),
            "board": np.array([l.split("|")[0] for l in lid]), "session": np.array([sid] * n), "t": np.array(ts)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", default="")
    a = ap.parse_args()
    data = Path(a.data)
    grid = Grid(json.loads(Path(a.plan).read_text(encoding="utf-8")))
    keys = KeyStore(data / "device_keys.json")
    sids = a.sessions.split(",") if a.sessions else sorted(p.name for p in (data / "sessions").iterdir())
    parts = []
    for sid in sids:
        r = build(sid, data / "sessions" / sid, grid, keys)
        if r is None:
            continue
        print(f"{sid}: {len(r['y'])} windows, {len(set(r['link']))} links, labels {np.bincount(r['y'], minlength=3).tolist()}",
              flush=True)
        parts.append(r)
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    out["feats"] = np.array(FEATS)
    np.savez_compressed(a.out, **out)
    print(f"-> {a.out}: {len(out['y'])} windows, labels {np.bincount(out['y'], minlength=3).tolist()}")


if __name__ == "__main__":
    main()
