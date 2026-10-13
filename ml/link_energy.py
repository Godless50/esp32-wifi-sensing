"""Per-link motion level from a session recording: AGC-normalised amplitude variation in time buckets.

usage: python link_energy.py --session /data/sessions/<id> [--bucket 15] [--last 300]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from wsn.keys import KeyStore
from wsn.live import ltf_amplitude
from wsn.protocol import CSI_TYPES, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

CHUNK_S = 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--data", default="/data")
    ap.add_argument("--bucket", type=float, default=15.0)
    ap.add_argument("--last", type=float, default=0.0, help="only the last N seconds")
    a = ap.parse_args()
    keys = KeyStore(Path(a.data) / "device_keys.json")
    sdir = Path(a.session)
    meta = json.loads((sdir / "meta.json").read_text())
    per = defaultdict(list)  # src -> [(t, amp)]
    for recv_t, buf in read_csi_bin(sdir / "csi.bin"):
        try:
            p = parse_packet(buf, keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError):
            continue
        if p.type not in CSI_TYPES:
            continue
        last = p.frames[-1].t_us if p.frames else 0
        for f in p.frames:
            if len(f.data) >= 256:
                amp = ltf_amplitude(f.data, "ht")
                if amp is not None:
                    per[f.src].append((recv_t - (last - f.t_us) / 1e6, amp))
    t_end = max(x[-1][0] for x in per.values())
    t_start = t_end - a.last if a.last else min(x[0][0] for x in per.values())
    edges = np.arange(t_start, t_end + 1e-9, a.bucket)
    print("bucket(start, wall)  " + "  ".join(f"{s[-5:]:>7s}" for s in sorted(per)))
    rows = []
    for b0 in edges[:-1]:
        cells = []
        for s in sorted(per):
            sel = [(t, amp) for t, amp in per[s] if b0 <= t < b0 + a.bucket]
            if len(sel) < 6:
                cells.append(float("nan"))
                continue
            A = np.stack([amp for _, amp in sel])
            tt = np.array([t for t, _ in sel])
            live = A.mean(0) > 2
            A = A[:, live] / (A[:, live].mean(1, keepdims=True) + 1e-3)  # нормировка кадра (АРУ)
            # движение — средний по поднесущим разброс во времени внутри 1-секундных кусков
            # (по времени, а не по числу кадров: у линий разная частота кадров)
            sec = np.floor((tt - b0) / CHUNK_S).astype(int)
            vals = [A[sec == k].std(0).mean() for k in np.unique(sec) if (sec == k).sum() >= 3]
            cells.append(float(np.mean(vals)) * 100 if vals else float("nan"))
        rows.append(cells)
        wall = meta["started_wall"] + (b0 - meta["started"])
        import time
        print(f"{time.strftime('%H:%M:%S', time.localtime(wall))}            " + "  ".join(f"{c:7.2f}" for c in cells))


if __name__ == "__main__":
    main()
