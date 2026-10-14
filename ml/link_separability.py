"""Отличает ли сам сигнал «ходят» от «пусто» — без нейросети. По каждой паре (плата, передатчик) из csi_all.bin
сессии: окна 4 с, шаг 1 с; признак движения = среднее по поднесущим стандартного отклонения нормированной
амплитуды во времени. AUROC «ходят» против «пусто» по меткам сессии (1.0 — разделяет идеально, 0.5 — никак).

usage: cd ml && PYTHONPATH=../server python link_separability.py <session_id> [--min-hz 20]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from build_rich import ltf_complex
from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI, TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

WIN, STEP, EDGE = 4.0, 1.0, 5.0  # окно, шаг; EDGE с — отступ от смены метки


def auroc(pos: np.ndarray, neg: np.ndarray) -> float:
    s = np.concatenate([pos, neg])
    r = s.argsort().argsort() + 1.0
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def segments(sdir: Path):
    """[(t0, t1, 'moving'|'empty')] из меток occupancy; конец последней — остановка сессии."""
    ev = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    occ = sorted((e["t"], e) for e in ev if e.get("kind") == "occupancy")
    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    out = []
    for i, (t, e) in enumerate(occ):
        t1 = occ[i + 1][0] if i + 1 < len(occ) else meta["stopped"]
        zones = e.get("zones") or {}
        if not zones or "переход" in e.get("note", ""):
            continue
        kind = "empty" if all(z.get("count", 0) == 0 for z in zones.values()) else "moving"
        out.append((t + EDGE, t1 - EDGE, kind))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--data", default="../data")
    ap.add_argument("--min-hz", type=float, default=20.0)
    a = ap.parse_args()
    data = Path(a.data)
    sdir = data / "sessions" / a.session
    keys = KeyStore(data / "device_keys.json")
    frames = defaultdict(list)  # (dev, src) -> [(t, amp[n])]
    for recv_t, buf in read_csi_bin(sdir / "csi_all.bin"):
        try:
            dev = peek_device_id(buf)
            p = parse_packet(buf, keys.get(dev))
        except (ProtocolError, TypeError):
            continue
        if p.type not in (TYPE_CSI, TYPE_CSI_SRC) or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) < 256:
                continue
            amp = np.abs(ltf_complex(f.data, 128))
            m = amp.mean()
            if m > 0:
                frames[(p.device_id, f.src or "ap")].append((recv_t - (last - f.t_us) / 1e6, amp / m))
    segs = segments(sdir)
    print("segments:", [(round(t1 - t0), k) for t0, t1, k in segs])
    rows = []
    for (dev, src), fr in sorted(frames.items()):
        t = np.array([x[0] for x in fr])
        A = np.stack([x[1] for x in fr])
        live = A.mean(0) > 0.05
        A = A[:, live]
        feats = {"moving": [], "empty": []}
        for t0, t1, kind in segs:
            w = t0
            while w + WIN <= t1:
                m = (t >= w) & (t < w + WIN)
                if m.sum() >= a.min_hz * WIN:
                    feats[kind].append(float(A[m].std(0).mean()))
                w += STEP
        pos, neg = np.array(feats["moving"]), np.array(feats["empty"])
        hz = len(t) / max(1e-9, t[-1] - t[0])
        if len(pos) >= 20 and len(neg) >= 20:
            rows.append((auroc(pos, neg), dev, src, hz, len(pos), len(neg), np.median(pos), np.median(neg)))
    for r in sorted(rows, reverse=True):
        print(f"AUROC {r[0]:.3f}  {r[1][-5:]} <- {r[2][-5:] if r[2] != 'ap' else 'ap'} {r[3]:6.1f} Hz  "
              f"windows moving/empty {r[4]}/{r[5]}  median motion {r[6]:.4f} / {r[7]:.4f}")


if __name__ == "__main__":
    main()
