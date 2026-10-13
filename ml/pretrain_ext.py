"""Pretrain the per-link encoder on atanasuke (other home, other people, ESP32-C6), then fine-tune on our recordings.

Pretraining: Net.enc reads one link's 40x4 sequence; heads predict the 9 posture states (stand/sit/lie + 6
transitions) and the body motion speed from video pose. 4 projects are held out to check that it generalises.
Fine-tuning: train_nn_torch.fit with the encoder initialised from pretraining, against the same fit from scratch,
both on the honest leave-one-session-out test over our two scripted recordings, same seeds and epochs.

usage: HSA_OVERRIDE_GFX_VERSION=10.3.0 .venv-rocm/bin/python ml/pretrain_ext.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from nn_common import SCRIPTED, act_class, bal, squash
from train_nn_torch import DEV, Net, fit, predict

TRANSITION = {1, 2, 4, 5, 7, 8}


def aug1(X, rng):
    """Gain, time stretch/shift, noise for single-link windows (no link drop: there is only one)."""
    n, L, T, C = X.shape
    out = X * np.exp(rng.uniform(np.log(0.5), np.log(2.0), (n, L, 1, 1)))
    src = (np.arange(T)[None] - T / 2) / rng.uniform(0.7, 1.4, (n, 1)) + T / 2 + rng.uniform(-5, 5, (n, 1))
    src = np.clip(src, 0, T - 1)
    lo = np.floor(src).astype(int); hi = np.minimum(lo + 1, T - 1); w = (src - lo)[:, None, :, None]
    idx = np.arange(n)[:, None]
    out = out[idx, :, lo].transpose(0, 2, 1, 3) * (1 - w) + out[idx, :, hi].transpose(0, 2, 1, 3) * w
    return (out + rng.normal(0, 0.05, out.shape) * (np.abs(out).mean((2, 3), keepdims=True) + 1e-3)).astype(np.float32)


class Pre(nn.Module):
    def __init__(self, C=4, w=32):
        super().__init__()
        self.enc = Net(1, C, w=w).enc
        self.cls = nn.Sequential(nn.Dropout(0.2), nn.Linear(w, 9))
        self.spd = nn.Linear(w, 1)

    def forward(self, x):  # [B, 1, T, C]
        e = self.enc(x[:, 0].transpose(1, 2)).reshape(len(x), -1)
        return self.cls(e), self.spd(e)[:, 0]


def pretrain(d, epochs, hold, seed=0):
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    X, y, sp, pr = d["X"], d["label9"].astype(np.int64), d["speed"], d["project"]
    va = np.isin(pr, hold)
    net = Pre().to(DEV)
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-3)
    ty = torch.tensor(y, device=DEV)
    ts = torch.tensor(np.nan_to_num(sp, nan=-1.0), device=DEV)
    tr = np.flatnonzero(~va)
    for ep in range(epochs):
        net.train()
        for b in np.array_split(rng.permutation(tr), max(1, len(tr) // 256)):
            x = torch.tensor(squash(aug1(X[b], rng)), device=DEV)
            pc, ps = net(x)
            yb, sb = ty[b], ts[b]
            mc, ms = yb >= 0, sb >= 0
            loss = (F.cross_entropy(pc[mc], yb[mc]) if mc.any() else 0) + (F.mse_loss(ps[ms], sb[ms]) if ms.any() else 0)
            opt.zero_grad(); loss.backward(); opt.step()
    net.eval()
    with torch.no_grad():
        pcs, pss = [], []
        for b in np.array_split(np.flatnonzero(va), 20):
            pc, ps = net(torch.tensor(squash(X[b]), device=DEV))
            pcs.append(pc.argmax(1).cpu().numpy()); pss.append(ps.cpu().numpy())
    pc, ps = np.concatenate(pcs), np.concatenate(pss)
    yv, sv = y[va], sp[va]
    m = yv >= 0
    mv = np.isin(yv, list(TRANSITION)).astype(int)
    pm = np.isin(pc, list(TRANSITION)).astype(int)
    fin = np.isfinite(sv)
    val = {"posture9_bal_acc": bal(yv[m], pc[m]), "transition_vs_still_bal_acc": bal(mv[m], pm[m]),
           "speed_corr": round(float(np.corrcoef(sv[fin], ps[fin])[0, 1]), 3), "val_windows": int(va.sum())}
    return net.enc.state_dict(), val


def loso(X, sess, ya, yz, ym, epochs, seeds, init_enc):
    res = {}
    for test in SCRIPTED:
        tr, te = sess != test, (sess == test) & (ya >= 0)
        nets = [fit(X[tr], ya[tr], yz[tr], ym[tr], epochs, s, init_enc=init_enc) for s in range(seeds)]
        pa, pz, pm = predict(nets, X[te])
        zt = yz[te]
        res[test] = {"activity_bal_acc": bal(ya[te], pa), "zone_bal_acc": bal(zt[zt >= 0], pz[zt >= 0]),
                     "moving_bal_acc": bal((ya[te] > 0).astype(int), (pm > 0.5).astype(int))}
    res["mean"] = {k: round(float(np.mean([res[t][k] for t in SCRIPTED])), 3) for k in res[SCRIPTED[0]]}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", default="../data/ml/ext_atanasuke.npz")
    ap.add_argument("--ours", default="../data/ml/seq_v1.npz")
    ap.add_argument("--out", default="../data/ml/pretrain_v1")
    ap.add_argument("--pre-epochs", type=int, default=25)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    e = np.load(a.ext)
    ext = {k: e[k] for k in e.files}
    projects = sorted(set(ext["project"].tolist()))
    hold = projects[::8]  # каждый восьмой проект — проверка предобучения
    enc, val = pretrain(ext, a.pre_epochs, hold)
    torch.save(enc, out / "encoder.pt")
    rep = {"pretrain": {"windows": int(len(ext["X"])), "held_out_projects": hold, **val, "seconds": round(time.time() - t0)}}
    print("pretrain", rep["pretrain"], flush=True)
    d = np.load(a.ours)
    X, sess, act, zone = d["X"].astype(np.float32), d["session"], d["act"], d["zone"]
    ya = act_class(act)
    yz = np.array([{1: 0, 2: 1, 3: 2, 4: 3}.get(int(z), -1) if a_ >= 1 else -1 for z, a_ in zip(zone, act)])
    ym = np.where(np.isin(sess, SCRIPTED), np.where(ya < 0, -1, (ya > 0).astype(int)), d["moving"])
    rep["scratch"] = loso(X, sess, ya, yz, ym, a.epochs, a.seeds, None)
    print("scratch", rep["scratch"]["mean"], f"{time.time() - t0:.0f}s", flush=True)
    rep["pretrained"] = loso(X, sess, ya, yz, ym, a.epochs, a.seeds, enc)
    print("pretrained", rep["pretrained"]["mean"], f"{time.time() - t0:.0f}s", flush=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != "pretrain"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
