"""Per-link model v2 on build_link2.py features (20 s windows: motion, breathing band, static change vs the link's
own reference). Small MLP (and plain logistic regression for comparison), checked on boards / transmitters /
sessions never seen in training. Features are standardised per link over the session (z-score against the
link's own distribution) so that a strong and a weak link speak the same language.

usage: python train_link2.py --data ../data/ml/link2_v1.npz --eval board|link|session [--final --model DIR]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from nn_common import bal

NAMES = ["никого", "неподвижен", "движется"]


def per_link_norm(F, link, session):
    """Each feature as a robust z-score within its (link, session): no absolute level survives."""
    out = np.zeros_like(F)
    key = np.char.add(np.char.add(link.astype(str), "@"), session.astype(str))
    for k in np.unique(key):
        m = key == k
        med = np.median(F[m], 0)
        iqr = np.percentile(F[m], 75, 0) - np.percentile(F[m], 25, 0) + 1e-6
        out[m] = (F[m] - med) / iqr
    return np.clip(out, -8, 8)


def fit_mlp(X, y, epochs=60, seed=0):
    torch.manual_seed(seed)
    net = nn.Sequential(nn.Linear(X.shape[1], 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 3))
    w = torch.tensor(len(y) / (3 * np.maximum(np.bincount(y, minlength=3), 1)), dtype=torch.float32)
    opt = torch.optim.AdamW(net.parameters(), lr=3e-3, weight_decay=1e-3)
    lossf = nn.CrossEntropyLoss(weight=w)
    Xt, yt = torch.tensor(X, dtype=torch.float32), torch.tensor(y.astype(np.int64))
    rng = np.random.default_rng(seed)
    for _ in range(epochs):
        idx = rng.permutation(len(y))
        for i in range(0, len(idx), 512):
            b = idx[i:i + 512]
            loss = lossf(net(Xt[b] + 0.05 * torch.randn_like(Xt[b])), yt[b])
            opt.zero_grad()
            loss.backward()
            opt.step()
    return net.eval()


def fit_lr(X, y):
    lin = nn.Linear(X.shape[1], 3)
    w = torch.tensor(len(y) / (3 * np.maximum(np.bincount(y, minlength=3), 1)), dtype=torch.float32)
    opt = torch.optim.LBFGS(lin.parameters(), max_iter=300)
    lossf = nn.CrossEntropyLoss(weight=w)
    Xt, yt = torch.tensor(X, dtype=torch.float32), torch.tensor(y.astype(np.int64))

    def closure():
        opt.zero_grad()
        loss = lossf(lin(Xt), yt) + 1e-3 * (lin.weight ** 2).sum()
        loss.backward()
        return loss

    opt.step(closure)
    return lin.eval()


@torch.no_grad()
def pred(m, X):
    return m(torch.tensor(X, dtype=torch.float32)).argmax(1).numpy()


def report(name, y, p, q):
    b = lambda yy, pp: bal(yy.astype(int), pp.astype(int))
    print(f"  {name:30s} n={len(y):6d} | 3 класса: MLP {bal(y, p)} ЛР {bal(y, q)}"
          f" | «кто-то рядом»: MLP {b(y > 0, p > 0)} ЛР {b(y > 0, q > 0)}"
          f" | «неподвижен» (среди не движущихся): MLP {b(y[y < 2] == 1, p[y < 2] == 1)}"
          f" | «движется»: MLP {b(y == 2, p == 2)} ЛР {b(y == 2, q == 2)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--eval", choices=["session", "link", "board", "none"], default="board")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--model", default="../data/ml/link2_model_v1")
    a = ap.parse_args()
    d = np.load(a.data, allow_pickle=True)
    y = d["y"].astype(int)
    X = per_link_norm(d["F"].astype(np.float32), d["link"], d["session"])
    feats = list(d["feats"])
    print(f"{len(y)} windows, labels {np.bincount(y, minlength=3).tolist()}, features {feats}")
    groups = {"session": d["session"], "link": np.array([l.split("|")[1] for l in d["link"]]),
              "board": d["board"]}.get(a.eval)
    if groups is not None:
        Y, P, Q = [], [], []
        for g in sorted(set(groups)):
            te = groups == g
            if te.sum() < 200 or len(set(y[te])) < 2 or len(set(y[~te])) < 3:
                continue
            t0 = time.time()
            m, l = fit_mlp(X[~te], y[~te]), fit_lr(X[~te], y[~te])
            p, q = pred(m, X[te]), pred(l, X[te])
            report(f"{a.eval} {str(g)[-17:]} ({time.time() - t0:.0f} s)", y[te], p, q)
            Y.append(y[te]); P.append(p); Q.append(q)
        if Y:
            report("ВСЕГО (не виденные)", np.concatenate(Y), np.concatenate(P), np.concatenate(Q))
    if a.final:
        m = fit_mlp(X, y)
        out = Path(a.model)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(m.state_dict(), out / "mlp.pt")
        (out / "meta.json").write_text(json.dumps({"version": int(time.time()), "features": feats, "classes": NAMES,
                                                   "norm": "per (link, session) median / IQR", "windows": int(len(y))},
                                                  ensure_ascii=False, indent=1))
        print(f"-> {out}")


if __name__ == "__main__":
    main()
