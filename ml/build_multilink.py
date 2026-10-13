"""Multi-link dataset: per 2 s window, motion features of every radio link + occupancy labels.

Per link and window (frames of that transmitter only, AGC-normalised per frame):
  var   — mean over subcarriers of the temporal std inside 1 s chunks (motion level, as in link_energy)
  peak  — max of that over 0.5 s chunks (short bursts)
  rate  — frames per second (0 = link silent -> features are zero and masked)
Labels from spoken occupancy events (+ retro corrections), one row per window:
  moving_any — somebody moves (activity has a movement word); windows with ambiguous labels are dropped
  moving_<zone>, occupied_<zone> for ZONES

usage: python build_multilink.py --data /data --out /data/ml/multilink_v1.npz [--sessions id1,id2]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from wsn.keys import KeyStore
from wsn.live import ltf_amplitude
from wsn.protocol import CSI_TYPES, TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

WIN_S, STRIDE_S = 2.0, 0.5
ZONES = ["спальня", "зал", "кухня", "коридор", "санузел", "комната"]
MOVE = re.compile(r"ход|идёт|идет|иду|транзит|встал|встаю|ушёл|ушел|пошёл|пошел|машет|вошёл|вошел", re.I)
AMBIGUOUS = re.compile(r"ногу|скан|перезагрузк", re.I)  # кратковременные/служебные события: окна не используем
LINKS = ["02:00:00:00:a1:18", "98:b3:ef:02:da:28", "98:b3:ef:02:da:14", "02:00:00:00:a1:2c",
         "02:00:00:00:f6:7c", "02:00:00:00:e5:57", "d8:d6:68:b3:4c:23", "d8:d6:68:b3:7a:6f",
         "02:00:00:00:a7:e6"]
FEATS = ["var", "peak", "rate"]


def link_frames(path: Path, keys: KeyStore) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    per = defaultdict(list)
    for recv_t, buf in read_csi_bin(path):
        try:
            p = parse_packet(buf, keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError):
            continue
        if p.type != TYPE_CSI_SRC or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) < 256:
                continue
            a = ltf_amplitude(f.data, "ht")
            if a is not None:
                per[f.src].append((recv_t - (last - f.t_us) / 1e6, a))
    out = {}
    for src, v in per.items():
        v.sort(key=lambda x: x[0])
        out[src] = (np.array([t for t, _ in v]), np.stack([a for _, a in v]).astype(np.float32))
    return out


def chunk_var(t: np.ndarray, A: np.ndarray, t0: float, chunk: float) -> list[float]:
    k = np.floor((t - t0) / chunk).astype(int)
    return [float(A[k == i].std(0).mean()) for i in np.unique(k) if (k == i).sum() >= 3]


def window_features(frames, t0: float, t1: float) -> np.ndarray:
    row = np.zeros((len(LINKS), len(FEATS)), np.float32)
    for li, src in enumerate(LINKS):
        if src not in frames:
            continue
        t, A = frames[src]
        i0, i1 = np.searchsorted(t, [t0, t1])
        if i1 - i0 < 6:
            continue
        tt, AA = t[i0:i1], A[i0:i1]
        live = AA.mean(0) > 2
        AA = AA[:, live] / (AA[:, live].mean(1, keepdims=True) + 1e-3)
        v1, v05 = chunk_var(tt, AA, t0, 1.0), chunk_var(tt, AA, t0, 0.5)
        if not v1:
            continue
        row[li] = [100 * np.mean(v1), 100 * max(v05 or v1), (i1 - i0) / (t1 - t0)]
    return row.reshape(-1)


def label_rows(events, t0, t1):
    occ = sorted((e["t"], e) for e in events if e.get("kind") == "occupancy")
    state = [e for t, e in occ if t <= t0]
    if not state or any(t0 < t <= t1 for t, _ in occ):
        return None  # окно должно лежать внутри одного состояния разметки
    e = state[-1]
    acts = " ".join(z.get("activity", "") for z in e["zones"].values())
    if AMBIGUOUS.search(acts) or AMBIGUOUS.search(e.get("note", "")):
        return None
    zones = e["zones"]
    moving = [int(z in zones and zones[z].get("count", 0) > 0 and bool(MOVE.search(zones[z].get("activity", ""))))
              for z in ZONES]
    occupied = [int(z in zones and zones[z].get("count", 0) > 0) if z in zones else -1 for z in ZONES]
    return [int(any(moving))] + moving + occupied


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/data/ml/multilink_v1.npz")
    ap.add_argument("--sessions", default="20261013-042804-6eba,20261013-050259-94cd")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    X, Y, T, S = [], [], [], []
    for sid in a.sessions.split(","):
        sdir = data / "sessions" / sid
        frames = link_frames(sdir / "csi.bin", keys)
        events = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        lo = max(v[0][0] for v in frames.values() if len(v[0]))
        lo = min(v[0][0] for v in frames.values())
        hi = max(v[0][-1] for v in frames.values())
        n_ok = 0
        for t0 in np.arange(lo, hi - WIN_S, STRIDE_S):
            lab = label_rows(events, t0, t0 + WIN_S)
            if lab is None:
                continue
            X.append(window_features(frames, t0, t0 + WIN_S)); Y.append(lab); T.append(t0 + WIN_S / 2); S.append(sid)
            n_ok += 1
        rates = {s[-5:]: round(len(frames[s][0]) / (hi - lo), 1) for s in LINKS if s in frames}
        print(f"{sid}: {(hi - lo) / 60:.1f} min, windows={n_ok}, link Hz={rates}")
    X, Y = np.stack(X), np.array(Y, np.int8)
    np.savez_compressed(a.out, X=X, Y=Y, t=np.array(T), session=np.array(S),
                        links=np.array(LINKS), feats=np.array(FEATS), zones=np.array(ZONES))
    print(f"saved {a.out}: X={X.shape}, moving_any={int(Y[:, 0].sum())}/{len(Y)}")
    for zi, z in enumerate(ZONES):
        occ = Y[:, 1 + len(ZONES) + zi]
        print(f"  {z:8s} moving={int(Y[:, 1 + zi].sum()):5d}  occupied={int((occ == 1).sum()):5d} empty={int((occ == 0).sum()):5d}")


if __name__ == "__main__":
    main()
