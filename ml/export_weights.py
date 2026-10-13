"""Torch side of the board export: one net of the live ensemble -> numpy weights + reference outputs.

usage: tools/gpu_run.sh /tmp/expw.log export_weights.py --model ../data/ml/live_model_v2 --net 0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from nn_common import squash
from train_nn_torch import Net


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="../data/ml/live_model_v2")
    ap.add_argument("--net", type=int, default=0)
    ap.add_argument("--data", default="../data/ml/seq_v1.npz")
    a = ap.parse_args()
    md = Path(a.model)
    meta = json.loads((md / "meta.json").read_text(encoding="utf-8"))
    net = Net(len(meta["links"]), 4, arch=meta.get("arch", "dil"))
    net.load_state_dict(torch.load(md / f"net{a.net}.pt", map_location="cpu"))
    net.eval()
    d = np.load(a.data)
    X = squash(d["X"].astype(np.float32))
    with torch.no_grad():
        pa, pz, pm = net(torch.tensor(X))
    w = {k: v.numpy() for k, v in net.state_dict().items()}
    np.savez(md / f"net{a.net}_numpy.npz", **{k.replace(".", "_"): v for k, v in w.items()},
             ref_act=pa.numpy(), ref_zone=pz.numpy(), ref_move=pm.numpy())
    print("saved", md / f"net{a.net}_numpy.npz", list(w))


if __name__ == "__main__":
    main()
