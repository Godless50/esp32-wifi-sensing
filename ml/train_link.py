"""Per-link model (one small net for every link of every board): nobody near / somebody still near / somebody
moving near, from one link's 4 s window (build_link.py). Checks that it does not depend on the place:

  --eval session   leave-one-session-out;
  --eval link      leave-one-transmitter-out: the links of one transmitter are never seen in training
                   (other places in the flat, other paths) — the test of "works for a new link";
  --eval board     leave-one-board-out (sessions recorded with all boards);
  --final          train on everything -> --model dir (net.pt + meta.json).

Baseline printed next to it: logistic regression on 8 window statistics (mean / std of each feature channel),
the kind of rule the tracker uses now — the net must beat it to be worth deploying.
usage: python train_link.py --data ../data/ml/link_v1.npz --eval link [--final --model ../data/ml/link_model_v1]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from nn_common import bal, squash

DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")
NAMES = ["никого", "неподвижен", "движется"]


class LinkNet(nn.Module):
    """[B, T=40, C=4] -> 3 logits. ~7k parameters: fits the board next to the zone model."""

    def __init__(self, c: int = 4, k: int = 3):
        super().__init__()
        self.f = nn.Sequential(
            nn.Conv1d(c, 16, 5, padding=2), nn.ReLU(),
            nn.Conv1d(16, 32, 5, stride=2, padding=2), nn.ReLU(),
            nn.Conv1d(32, 32, 3, stride=2, padding=1), nn.ReLU(),
        )
        self.head = nn.Linear(64, k)

    def forward(self, x):
        h = self.f(x.transpose(1, 2))
        return self.head(torch.cat([h.mean(-1), h.amax(-1)], 1))


def stats(X):
    return np.concatenate([X.mean(1), X.std(1)], 1)


def fit(X, y, epochs=25, seed=0, lr=2e-3):
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = LinkNet().to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-3)
    w = torch.tensor(len(y) / (3 * np.maximum(np.bincount(y, minlength=3), 1)), dtype=torch.float32, device=DEV)
    lossf = nn.CrossEntropyLoss(weight=w)
    Xs = squash(X).astype(np.float32)
    for ep in range(epochs):
        net.train()
        idx = rng.permutation(len(y))
        for i in range(0, len(idx), 256):
            b = idx[i:i + 256]
            xb = Xs[b] * np.exp(rng.uniform(np.log(0.7), np.log(1.4), (len(b), 1, 1))).astype(np.float32)
            xb = torch.tensor(xb, device=DEV)
            loss = lossf(net(xb), torch.tensor(y[b].astype(np.int64), device=DEV))
            opt.zero_grad()
            loss.backward()
            opt.step()
    return net.eval()


@torch.no_grad()
def predict(net, X):
    out = []
    Xs = squash(X).astype(np.float32)
    for i in range(0, len(X), 2048):
        out.append(net(torch.tensor(Xs[i:i + 2048], device=DEV)).softmax(1).cpu().numpy())
    return np.concatenate(out)


def logreg(Xtr, ytr, Xte, steps=400):
    """Balanced multinomial logistic regression on window statistics (torch; no sklearn on the server)."""
    a, b = stats(Xtr), stats(Xte)
    mu, sd = a.mean(0), a.std(0) + 1e-6
    A = torch.tensor((a - mu) / sd, dtype=torch.float32)
    B = torch.tensor((b - mu) / sd, dtype=torch.float32)
    yt = torch.tensor(ytr.astype(np.int64))
    w = torch.tensor(len(ytr) / (3 * np.maximum(np.bincount(ytr, minlength=3), 1)), dtype=torch.float32)
    lin = nn.Linear(A.shape[1], 3)
    opt = torch.optim.LBFGS(lin.parameters(), max_iter=steps)
    lossf = nn.CrossEntropyLoss(weight=w)

    def closure():
        opt.zero_grad()
        loss = lossf(lin(A), yt) + 1e-3 * (lin.weight ** 2).sum()
        loss.backward()
        return loss

    opt.step(closure)
    with torch.no_grad():
        return lin(B).argmax(1).numpy()


def report(name, y, p_net, p_lr):
    any_y, any_n, any_l = y > 0, p_net > 0, p_lr > 0
    mv_y, mv_n, mv_l = y == 2, p_net == 2, p_lr == 2
    print(f"  {name:28s} n={len(y):6d} | 3 класса: сеть {bal(y, p_net)}  порог {bal(y, p_lr)}"
          f" | «кто-то рядом»: сеть {bal(any_y.astype(int), any_n.astype(int))} порог {bal(any_y.astype(int), any_l.astype(int))}"
          f" | «движется»: сеть {bal(mv_y.astype(int), mv_n.astype(int))} порог {bal(mv_y.astype(int), mv_l.astype(int))}",
          flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--eval", choices=["session", "link", "board", "none"], default="link")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--model", default="../data/ml/link_model_v1")
    a = ap.parse_args()
    d = np.load(a.data, allow_pickle=True)
    X, y = d["X"].astype(np.float32), d["y"].astype(int)
    groups = {"session": d["session"], "link": np.array([l.split("|")[1] for l in d["link"]]),
              "board": d["board"]}.get(a.eval)
    print(f"{len(y)} samples, labels {np.bincount(y, minlength=3).tolist()}, device {DEV}")
    if groups is not None:
        ys, ps, ls = [], [], []
        for g in sorted(set(groups)):
            te = groups == g
            if te.sum() < 200 or len(set(y[te])) < 2 or len(set(y[~te])) < 3:
                continue
            t0 = time.time()
            net = fit(X[~te], y[~te], a.epochs)
            p = predict(net, X[te]).argmax(1)
            pl = logreg(X[~te], y[~te], X[te])
            report(f"{a.eval} {str(g)[-17:]} ({time.time() - t0:.0f} s)", y[te], p, pl)
            ys.append(y[te]); ps.append(p); ls.append(pl)
        if ys:
            report("ВСЕГО (не виденные)", np.concatenate(ys), np.concatenate(ps), np.concatenate(ls))
    if a.final:
        net = fit(X, y, a.epochs)
        out = Path(a.model)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(net.cpu().state_dict(), out / "net.pt")
        meta = {"version": int(time.time()), "classes": NAMES, "input": "build_seq.link_seq [40, 4], squash",
                "samples": int(len(y)), "labels": np.bincount(y, minlength=3).tolist(),
                "params": int(sum(p.numel() for p in net.parameters())), "data": a.data}
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        print(f"-> {out} ({meta['params']} parameters)")


if __name__ == "__main__":
    main()
