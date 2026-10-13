"""PyTorch version of train_nn.py for the server GPU (ROCm, RX 6600: HSA_OVERRIDE_GFX_VERSION=10.3.0).

Same data, augmentation, model shape and leave-one-session-out test as train_nn.py; trains on the GPU when
torch.cuda.is_available() (ROCm exposes HIP as cuda), else on the CPU. Several seeds are averaged per test.

usage: HSA_OVERRIDE_GFX_VERSION=10.3.0 .venv-rocm/bin/python ml/train_nn_torch.py --data data/ml/seq_v1.npz --out data/ml/nn_torch_v1
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from nn_common import ACT_NAMES, SCRIPTED, act_class, augment, bal, confusion, squash

DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class Net(nn.Module):
    def __init__(self, L: int, C: int, n_zone: int = 4, w: int = 32):
        super().__init__()
        self.enc = nn.Sequential(  # общий кодер одной линии: [C, T] -> [w]
            nn.Conv1d(C, 24, 5, padding=2), nn.ReLU(),
            nn.Conv1d(24, w, 5, padding=4, dilation=2), nn.ReLU(),
            nn.Conv1d(w, w, 3, padding=4, dilation=4), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.zone = nn.Sequential(nn.Linear(L * w, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, n_zone))
        self.trunk = nn.Sequential(nn.Linear(2 * w, 48), nn.ReLU(), nn.Dropout(0.3))
        self.act, self.move = nn.Linear(48, 4), nn.Linear(48, 1)

    def forward(self, x):  # x: [B, L, T, C]
        B, L, T, C = x.shape
        e = self.enc(x.reshape(B * L, T, C).transpose(1, 2)).reshape(B, L, -1)
        h = self.trunk(torch.cat([e.max(1).values, e.mean(1)], 1))  # активность — не зависит от того, где
        return self.act(h), self.zone(e.reshape(B, -1)), self.move(h)[:, 0]


def fit(Xtr, ya, yz, ym, epochs, seed, bs=64):
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    net = Net(Xtr.shape[1], Xtr.shape[3]).to(DEV)
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 3e-3, total_steps=epochs * ((len(Xtr) + bs - 1) // bs))
    cnt = np.array([(ya == c).sum() for c in range(4)], float)
    cw = torch.tensor(np.where(cnt > 0, cnt.sum() / (4 * np.maximum(cnt, 1)), 0), dtype=torch.float32, device=DEV)
    ce = nn.CrossEntropyLoss(reduction="none")
    t_a, t_z, t_m = (torch.tensor(np.asarray(v, np.int64), device=DEV) for v in (ya, yz, ym))
    for _ in range(epochs):
        net.train()
        X = torch.tensor(squash(augment(Xtr, rng, ya, yz)), dtype=torch.float32, device=DEV)
        for idx in torch.randperm(len(X), device=DEV).split(bs):
            pa, pz, pm = net(X[idx])
            a, z, m = t_a[idx], t_z[idx], t_m[idx]
            la = (ce(pa, a.clamp(min=0)) * cw[a.clamp(min=0)] * (a >= 0)).sum() / (a >= 0).sum().clamp(min=1)
            lz = (ce(pz, z.clamp(min=0)) * (z >= 0)).sum() / (z >= 0).sum().clamp(min=1)
            lm = (nn.functional.binary_cross_entropy_with_logits(pm, m.clamp(min=0).float(), reduction="none") * (m >= 0)).sum() / (m >= 0).sum().clamp(min=1)
            opt.zero_grad(); (la + lz + 0.5 * lm).backward(); opt.step(); sched.step()
    return net


@torch.no_grad()
def predict(nets, X):
    Xt = torch.tensor(squash(X), dtype=torch.float32, device=DEV)
    pa = pz = pm = 0
    for net in nets:
        net.eval()
        a, z, m = net(Xt)
        pa, pz, pm = pa + a.softmax(1), pz + z.softmax(1), pm + torch.sigmoid(m)
    return pa.argmax(1).cpu().numpy(), pz.argmax(1).cpu().numpy(), (pm / len(nets)).cpu().numpy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ml/seq_v1.npz")
    ap.add_argument("--out", default="data/ml/nn_torch_v1")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    d = np.load(a.data)
    X = d["X"].astype(np.float32)
    sess, act, zone = d["session"], d["act"], d["zone"]
    ZMAP, zn = {1: 0, 2: 1, 3: 2, 4: 3}, ["зал", "кухня", "коридор", "санузел"]
    ya = act_class(act)
    yz = np.array([ZMAP.get(int(z), -1) if a_ >= 1 else -1 for z, a_ in zip(zone, act)])
    ym = np.where(np.isin(sess, SCRIPTED), np.where(ya < 0, -1, (ya > 0).astype(int)), d["moving"])
    t0 = time.time()
    rep = {"device": str(DEV), "gpu": torch.cuda.get_device_name(0) if DEV.type == "cuda" else None,
           "epochs": a.epochs, "seeds": a.seeds, "loso": {}}
    for test in SCRIPTED:
        tr, te = sess != test, (sess == test) & (ya >= 0)
        nets = [fit(X[tr], ya[tr], yz[tr], ym[tr], a.epochs, s) for s in range(a.seeds)]
        pa, pz, pm = predict(nets, X[te])
        zt = yz[te]
        r = {"activity_bal_acc": bal(ya[te], pa), "zone_bal_acc": bal(zt[zt >= 0], pz[zt >= 0]),
             "moving_bal_acc": bal((ya[te] > 0).astype(int), (pm > 0.5).astype(int)),
             "activity_confusion": confusion(ya[te], pa, ACT_NAMES), "zone_confusion": confusion(zt[zt >= 0], pz[zt >= 0], zn)}
        rep["loso"][test] = r
        print(test, {k: v for k, v in r.items() if "confusion" not in k}, f"{time.time() - t0:.0f}s", flush=True)
    nets = [fit(X, ya, yz, ym, a.epochs, s) for s in range(a.seeds)]  # итоговые модели на всех данных
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for i, n in enumerate(nets):
        torch.save(n.state_dict(), out / f"net{i}.pt")
    rep["params"] = sum(p.numel() for p in nets[0].parameters())
    rep["seconds"] = round(time.time() - t0)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print("done", rep["device"], rep["gpu"], rep["params"], "params", rep["seconds"], "s")


if __name__ == "__main__":
    main()
