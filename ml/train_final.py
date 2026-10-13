"""Final live model: ensemble of train_nn_torch.Net with the atanasuke-pretrained link encoder, fit on ALL data.

Writes data/ml/live_model/{net0..N.pt, meta.json}; ml/learner.py loads it (and replaces it after online updates).

usage: tools/gpu_run.sh /tmp/final.log train_final.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from build_rich import LINKS
from nn_common import ACT_NAMES, SCRIPTED, act_class
from train_nn_torch import ARCH, Net, fit

ZONES4 = ["зал", "кухня", "коридор", "санузел"]
ZMAP = {1: 0, 2: 1, 3: 2, 4: 3}


def labels(d):
    sess, act, zone = d["session"], d["act"], d["zone"]
    ya = act_class(act)
    yz = np.array([ZMAP.get(int(z), -1) if a_ >= 1 else -1 for z, a_ in zip(zone, act)])
    ym = np.where(np.isin(sess, SCRIPTED), np.where(ya < 0, -1, (ya > 0).astype(int)), d["moving"])
    return ya, yz, ym


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data/ml/seq_v1.npz")
    ap.add_argument("--encoder", default="../data/ml/pretrain_v1/encoder.pt")
    ap.add_argument("--out", default="../data/ml/live_model")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    d = np.load(a.data)
    X = d["X"].astype(np.float32)
    ya, yz, ym = labels(d)
    enc = torch.load(a.encoder, map_location="cpu")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for s in range(a.seeds):
        net = fit(X, ya, yz, ym, a.epochs, s, init_enc=enc)
        torch.save({k: v.cpu() for k, v in net.state_dict().items()}, out / f"net{s}.pt")
    meta = {"version": int(time.time()), "arch": ARCH, "links": [str(m) for m in d["links"]] if "links" in d.files else list(LINKS), "zones": ZONES4, "activities": ACT_NAMES,
            "seeds": a.seeds, "epochs": a.epochs, "trained_on": sorted(set(d["session"].tolist())),
            "encoder": a.encoder, "params": sum(p.numel() for p in net.parameters()), "seconds": round(time.time() - t0)}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print("saved", out, meta)


if __name__ == "__main__":
    main()
