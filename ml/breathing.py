"""Breathing check: is there a stable 0.1–0.5 Hz (6–30 breaths/min) component in each radio link?

Method (literature: chest moves 5–12 mm -> path length change -> slow periodic CSI amplitude change;
amplitude-only single antenna has blind spots, so we pick the best subcarriers per link):
  per link: AGC-normalised amplitude [T, S] -> uniform 10 Hz grid -> remove slow drift (60 s moving mean)
  -> per subcarrier power spectrum over the window -> score = peak power in 0.1–0.5 Hz / median power
  in 0.7–2.0 Hz. Report the dominant breathing frequency of the 5 best subcarriers and the score.
A real breathing source shows the same frequency in consecutive windows; noise jumps around.

--mode phase runs the same scoring on the phase of adjacent-subcarrier products H_k·conj(H_k+1) with the
per-frame mean removed (CSCR-like: the common phase offset and the timing slope cancel), --mode both prints both.

usage: python breathing.py --session /data/sessions/<id> [--win 120] [--step 60] [--from HH:MM:SS --to HH:MM:SS]
       [--mode amp|phase|both]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from wsn.keys import KeyStore
from wsn.protocol import CSI_TYPES, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

FS = 10.0
REORDER = np.r_[32:64, 0:32]
NAMES = {"02:00:00:00:a1:18": "роутер B (ТВ)", "98:b3:ef:02:da:28": "узел у платы", "98:b3:ef:02:da:14": "роутер коридор",
         "02:00:00:00:a1:2c": "узел санузел", "02:00:00:00:f6:7c": "холодильник", "02:00:00:00:e5:57": "принтер (спальня)",
         "d8:d6:68:b3:4c:23": "Tuya коридор", "d8:d6:68:b3:7a:6f": "Tuya кухня", "02:00:00:00:a7:e6": "iComm"}


def load(sdir: Path, keys: KeyStore):
    per = defaultdict(list)
    for recv_t, buf in read_csi_bin(sdir / "csi.bin"):
        try:
            p = parse_packet(buf, keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError):
            continue
        if p.type not in CSI_TYPES or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) >= 256:
                iq = np.frombuffer(f.data, dtype=np.int8, count=128, offset=128).astype(np.float32).reshape(64, 2)
                per[f.src].append((recv_t - (last - f.t_us) / 1e6, (iq[:, 1] + 1j * iq[:, 0])[REORDER]))
    return {s: (np.array([t for t, _ in v]), np.stack([a for _, a in v]).astype(np.complex64))
            for s, v in per.items() if len(v) > 100}


def series(H: np.ndarray, mode: str) -> np.ndarray:
    """Per-frame values [T, S] to look for breathing in: AGC-normalised amplitude or adjacent-pair phase."""
    A = np.abs(H)
    live = A.mean(0) > 2
    if mode == "amp":
        return A[:, live] / (A[:, live].mean(1, keepdims=True) + 1e-3)
    idx = np.flatnonzero(live)
    pairs = idx[:-1][np.diff(idx) == 1]
    D = H[:, pairs] * np.conj(H[:, pairs + 1])
    D = D / (np.abs(D) + 1e-6)
    c = D.mean(1, keepdims=True)
    D = D * np.conj(c / (np.abs(c) + 1e-6))  # общий для кадра сдвиг и наклон фазы
    ref = D.mean(0)
    return np.angle(D * np.conj(ref / (np.abs(ref) + 1e-6)))  # отклонение от среднего за окно, рад


def breathing_score(t: np.ndarray, H: np.ndarray, t0: float, t1: float, mode: str = "amp"):
    m = (t >= t0) & (t < t1)
    if m.sum() < (t1 - t0) * 5:  # нужно хотя бы ~5 кадров/с
        return None
    tt, AA = t[m], series(H[m], mode)
    grid = np.arange(t0, t1, 1 / FS)
    X = np.stack([np.interp(grid, tt, AA[:, k]) for k in range(AA.shape[1])], axis=1)
    w = int(60 * FS) | 1
    kern = np.ones(w) / w
    trend = np.stack([np.convolve(np.pad(X[:, k], w // 2, mode="edge"), kern, "valid") for k in range(X.shape[1])], 1)
    X = (X - trend[: len(X)]) * np.hanning(len(X))[:, None]
    P = np.abs(np.fft.rfft(X, axis=0)) ** 2
    f = np.fft.rfftfreq(len(X), 1 / FS)
    br = (f >= 0.1) & (f <= 0.5)
    ref = (f >= 0.7) & (f <= 2.0)
    score = P[br].max(0) / (np.median(P[ref], axis=0) + 1e-12)
    best = np.argsort(-score)[:5]
    freqs = f[br][P[br][:, best].argmax(0)]
    return float(np.median(score[best])), float(np.median(freqs)) * 60, float(freqs.std() * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--data", default="/data")
    ap.add_argument("--win", type=float, default=120)
    ap.add_argument("--step", type=float, default=60)
    ap.add_argument("--from_", "--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--mode", choices=["amp", "phase", "both"], default="amp")
    a = ap.parse_args()
    modes = ["amp", "phase"] if a.mode == "both" else [a.mode]
    sdir = Path(a.session)
    meta = json.loads((sdir / "meta.json").read_text())
    links = load(sdir, KeyStore(Path(a.data) / "device_keys.json"))
    to_mono = lambda hms: meta["started"] + (time.mktime(time.strptime(time.strftime("%Y-%m-%d ", time.localtime(meta["started_wall"])) + hms, "%Y-%m-%d %H:%M:%S")) - meta["started_wall"])
    lo = to_mono(a.frm) if a.frm else min(v[0][0] for v in links.values())
    hi = to_mono(a.to) if a.to else max(v[0][-1] for v in links.values())
    for t0 in np.arange(lo, hi - a.win + 1e-6, a.step):
        wall = meta["started_wall"] + (t0 - meta["started"])
        print(f"--- {time.strftime('%H:%M:%S', time.localtime(wall))} + {a.win:.0f} s")
        for src in sorted(links, key=lambda s: NAMES.get(s, s)):
            for mode in modes:
                r = breathing_score(*links[src], t0, t0 + a.win, mode)
                if r is None:
                    continue
                sc, bpm, spread = r
                mark = "  <== дыхание" if sc > 25 and spread < 2 else ""  # шумовой фон этой оценки ~10–14
                print(f"  {NAMES.get(src, src):20s} {mode:5s} score={sc:6.1f}  частота={bpm:5.1f}/мин (разброс {spread:4.1f}){mark}")


if __name__ == "__main__":
    main()
