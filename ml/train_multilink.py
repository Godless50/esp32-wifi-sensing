"""Multi-link models on multilink_v1.npz: does adding radio links help?

Tasks: moving_any (somebody moves) and bathroom_occupied (санузел занят).
Feature sets: B only (1 link) | 5 links present since 04:21 | all 9 links (only windows where all 9 are live).
Split: per session, last 25 % of time = test (2 s gap). Model: L2 logistic regression on log-features
(few samples -> a linear model; MLP would only memorise). Baseline for moving_any: threshold on B 'var'.

usage: python train_multilink.py --ds /data/ml/multilink_v1.npz --out /data/ml/multilink_v1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

SETS = {
    "B": ["02:00:00:00:a1:18"],
    "5links": ["02:00:00:00:a1:18", "98:b3:ef:02:da:28", "98:b3:ef:02:da:14", "02:00:00:00:e5:57", "d8:d6:68:b3:4c:23"],
    "9links": None,  # все
}


def split(session, t, frac=0.25, gap=2.0):
    tr, te = np.zeros(len(t), bool), np.zeros(len(t), bool)
    for s in np.unique(session):
        m = session == s
        cut = np.quantile(t[m], 1 - frac)
        te |= m & (t >= cut)
        tr |= m & (t < cut - gap)
    return tr, te


def metrics(y, p):
    tp = int(((p == 1) & (y == 1)).sum()); tn = int(((p == 0) & (y == 0)).sum())
    fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    tpr = tp / max(1, tp + fn); tnr = tn / max(1, tn + fp)
    return {"bal_acc": round(0.5 * (tpr + tnr), 3), "recall": round(tpr, 3), "specificity": round(tnr, 3),
            "tp": tp, "fn": fn, "tn": tn, "fp": fp}


def logreg(X, y, l2=1.0, iters=3000, lr=0.1):
    w, b = np.zeros(X.shape[1]), 0.0
    pos = y.mean()
    sw = np.where(y == 1, 0.5 / max(pos, 1e-3), 0.5 / max(1 - pos, 1e-3))  # баланс классов
    for _ in range(iters):
        z = np.clip(X @ w + b, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = sw * (p - y)
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", default="/data/ml/multilink_v1.npz")
    ap.add_argument("--out", default="/data/ml/multilink_v1")
    a = ap.parse_args()
    d = np.load(a.ds)
    X, Y, t, sess = d["X"], d["Y"], d["t"], d["session"]
    links, feats, zones = list(d["links"]), list(d["feats"]), list(d["zones"])
    nf = len(feats)
    tasks = {"moving_any": Y[:, 0], "bathroom_occupied": Y[:, 1 + 2 * len(zones) - len(zones) + zones.index("санузел")]}
    tasks["bathroom_occupied"] = Y[:, 1 + len(zones) + zones.index("санузел")]
    live = X.reshape(len(X), len(links), nf)[:, :, 2] > 0  # rate > 0
    report = {}
    for task, y in tasks.items():
        report[task] = {}
        for name, use in SETS.items():
            idx = [links.index(l) for l in (use or links)]
            ok = (y >= 0) & live[:, idx].all(1)
            cols = [i * nf + j for i in idx for j in range(nf - 1)]  # var, peak (rate не используем — он отражает пинг, не людей)
            Xs = np.log1p(X[ok][:, cols]); ys = y[ok].astype(float)
            tr, te = split(sess[ok], t[ok])
            if ys[tr].min() == ys[tr].max() or ys[te].min() == ys[te].max():
                report[task][name] = {"skipped": f"one class only (train {int(ys[tr].sum())}/{int(tr.sum())}, "
                                                 f"test {int(ys[te].sum())}/{int(te.sum())})"}
                continue
            mu, sd = Xs[tr].mean(0), Xs[tr].std(0) + 1e-6
            w, b = logreg((Xs[tr] - mu) / sd, ys[tr])
            p = ((((Xs[te] - mu) / sd) @ w + b) > 0).astype(int)
            r = {"windows_train": int(tr.sum()), "windows_test": int(te.sum()),
                 "positives_test": int(ys[te].sum()), "model": metrics(ys[te].astype(int), p)}
            if task == "moving_any":  # базовая линия: порог по 'var' линии B, подобранный на обучающей части
                bv = np.log1p(X[ok][:, links.index("02:00:00:00:a1:18") * nf])
                cands = np.quantile(bv[tr], np.linspace(0.05, 0.95, 91))
                thr = max(cands, key=lambda c: metrics(ys[tr].astype(int), (bv[tr] > c).astype(int))["bal_acc"])
                r["baseline_B_threshold"] = metrics(ys[te].astype(int), (bv[te] > thr).astype(int))
            names = [f"{links[i][-5:]}.{feats[j]}" for i in idx for j in range(nf - 1)]
            top = np.argsort(-np.abs(w))[:5]
            r["top_weights"] = {names[i]: round(float(w[i]), 2) for i in top}
            r["weights"] = {"w": w.tolist(), "b": float(b), "mu": mu.tolist(), "sd": sd.tolist(), "features": names}
            report[task][name] = r
            print(f"{task:18s} {name:7s} train={r['windows_train']:5d} test={r['windows_test']:4d} "
                  f"pos_test={r['positives_test']:4d} model={r['model']} "
                  + (f"baseline={r['baseline_B_threshold']}" if "baseline_B_threshold" in r else ""), flush=True)
            print(f"{'':27s} top: {r['top_weights']}", flush=True)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
