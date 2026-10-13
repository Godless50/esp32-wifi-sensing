"""Train on real labels (scripted session) with honest splits; test across sessions.

E1 zone of the active person, leave-one-activity-out: train on two of {stand, wave, walk}, test on the third.
E2 activity {no motion (rest+stand), wave, walk}, leave-one-zone-out: train on three points, test on the fourth;
   rest windows are split into 4 contiguous blocks, block k goes to the test fold k.
E3 cross-session: models trained on the whole scripted session, tested on the spoken sessions
   (moving_any; zone of the mover when exactly one zone moves).
Baselines on the same windows: nearest link (largest lvar rise over the rest baseline), motion threshold.
Features: per link FEATS of build_rich, minus the session baseline (median over the session's quietest third
by total lvar) - the same normalisation can run live with a running median.

usage (wsn-ml image): python train_real.py --data /data/ml/rich_v1.npz --out /data/ml/real_v1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SCRIPT = "20261013-053501-ee64"
LINK_ZONE = {"02:00:00:00:a1:18": "зал", "98:b3:ef:02:da:28": "спальня", "98:b3:ef:02:da:14": "коридор",
             "02:00:00:00:a1:2c": "санузел", "02:00:00:00:f6:7c": "кухня", "02:00:00:00:e5:57": "спальня",
             "d8:d6:68:b3:4c:23": "коридор", "d8:d6:68:b3:7a:6f": "кухня", "02:00:00:00:a7:e6": "зал",
             "02:00:00:00:b8:14": "зал"}
ACT3 = ["нет движения", "машет", "ходит", "мелкие"]  # стоит/покой -> 0, мелкие движения (act 4) -> 3


def bal(y, p):
    return round(float(np.mean([(p[y == c] == c).mean() for c in np.unique(y)])), 3)


def confusion(y, p, names):
    return {names[t]: {names[q]: int(((y == t) & (p == q)).sum()) for q in sorted(set(p.tolist()))}
            for t in sorted(set(y.tolist()))}


def models():
    return {
        "logreg": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.3, class_weight="balanced", max_iter=3000)),
        "hgb": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=150,
                                                      class_weight="balanced", random_state=0),
    }


def prepare(d, use: list[str]):
    """Per-session baseline-subtracted features [N, L*(F-1)+L] and the lvar rise [N, L]."""
    links, feats = list(d["links"]), list(d["feats"])
    X = d["X"][:, [links.index(m) for m in use], :].astype(np.float64)
    lv, rt = feats.index("lvar"), feats.index("rate")
    out = np.zeros_like(X)
    for s in np.unique(d["session"]):
        for j in range(len(use)):
            m = (d["session"] == s) & (X[:, j, rt] > 0)  # фон линии — по окнам, где она жива
            if m.sum() < 10:
                continue
            q = m & (X[:, j, lv] <= np.quantile(X[m, j, lv], 1 / 3))
            out[m, j] = X[m, j] - np.median(X[q, j], axis=0)
    keep = [i for i, f in enumerate(feats) if f != "rate"]
    rise = out[:, :, lv]
    rel = rise - rise.mean(1, keepdims=True)  # какая линия выделяется (для деревьев)
    Fl = np.concatenate([out[:, :, keep].reshape(len(X), -1), rel], 1)
    return Fl, pooled(out[:, :, keep], rise, d), rise


def pooled(out, rise, d, lags=(4, 8)):
    """Link-agnostic features (activity must not depend on where it happens) + causal context.

    For every feature: max / mean over links and the value on the most disturbed link; number of disturbed links;
    for the window 4 s and 8 s earlier (non-overlapping): how much the rise pattern changed and whether the most
    disturbed link moved - walking moves through links, waving stays on one.
    """
    n = len(rise)
    am = rise.argmax(1)
    cols = []
    for k in range(out.shape[2]):
        v = out[:, :, k]
        cols += [v.max(1), v.mean(1), v[np.arange(n), am]]
    cols.append((rise > 0.15).sum(1).astype(float))
    pos = np.clip(rise, 0, None)
    for lag in lags:
        prev = np.arange(n) - lag
        ok = prev >= 0
        ok[ok] &= d["session"][prev[ok]] == d["session"][ok]
        ok[ok] &= np.abs(d["t"][ok] - d["t"][prev[ok]] - lag) < 0.5
        p = np.where(ok, prev, np.arange(n))
        dr = rise - rise[p]
        cos = (pos * pos[p]).sum(1) / (np.linalg.norm(pos, axis=1) * np.linalg.norm(pos[p], axis=1) + 1e-6)
        cols += [np.abs(dr).max(1), np.linalg.norm(dr, axis=1), np.where(ok, 1 - cos, 0), (am != am[p]).astype(float)]
    return np.stack(cols, 1)


def nearest(rise, use, zones):
    return np.array([zones.index(LINK_ZONE[use[i]]) for i in rise.argmax(1)])


def e1_zone(F, act, zone, zones, rise, use):
    sel = act >= 1
    res = {"windows": int(sel.sum())}
    for name, mk in models().items():
        P = np.full(len(act), -1)
        for a_test in (1, 2, 3):
            tr, te = sel & (act != a_test), sel & (act == a_test)
            P[te] = mk().fit(F[tr], zone[tr]).predict(F[te])
        res[name] = {"bal_acc": bal(zone[sel], P[sel]),
                     "by_test_activity": {k: bal(zone[sel & (act == a)], P[sel & (act == a)])
                                          for k, a in (("стоит", 1), ("машет", 2), ("ходит", 3))},
                     "confusion": confusion(zone[sel], P[sel], zones)}
    hz = nearest(rise, use, zones)
    res["nearest_link"] = {"bal_acc": bal(zone[sel], hz[sel]), "confusion": confusion(zone[sel], hz[sel], zones)}
    return res


def e2_activity(F, act, zone, gap=3):
    y = np.where(act <= 1, 0, act - 1)
    zs = sorted(set(zone[act >= 1].tolist()))
    fold = np.full(len(act), -1)
    blocks = np.array_split(np.flatnonzero(act == 0), len(zs))
    for k, z in enumerate(zs):
        fold[(act >= 1) & (zone == z)] = k
        fold[blocks[k][gap:len(blocks[k]) - gap]] = k  # без перекрытия окон на границах блоков
    sel = fold >= 0
    res = {"windows": int(sel.sum())}
    for name, mk in models().items():
        P = np.full(len(act), -1)
        for k in range(len(zs)):
            tr, te = sel & (fold != k), fold == k
            P[te] = mk().fit(F[tr], y[tr]).predict(F[te])
        res[name] = {"bal_acc": bal(y[sel], P[sel]), "confusion": confusion(y[sel], P[sel], ACT3)}
    return res


def e3_cross(Fa, Fz, rise, d, use, zones, sc, te):
    """Fit on the scripted session (sc), test on spoken windows (te)."""
    act, zone, Y = d["act"], d["zone"], d["Y"]
    y3 = np.where(act <= 1, 0, act - 1)
    mv = Y[:, 0]
    res = {"windows": int(te.sum()), "moving": int((mv[te] == 1).sum())}
    tot = rise.sum(1)
    cand = np.quantile(tot[sc], np.linspace(0.05, 0.95, 91))
    thr = max(cand, key=lambda c: bal((y3[sc] > 0).astype(int), (tot[sc] > c).astype(int)))
    res["threshold_moving_bal_acc"] = bal(mv[te], (tot[te] > thr).astype(int))
    one = te & (Y[:, 1:1 + len(zones)].sum(1) == 1)
    zt = np.where(one, Y[:, 1:1 + len(zones)].argmax(1), -1)
    trained = sorted(set(zone[sc & (act >= 1)].tolist()))
    zk = one & np.isin(zt, trained)
    res["zone_windows"] = {zones[z]: int((zt[zk] == z).sum()) for z in trained}
    hz = nearest(rise, use, zones)
    res["zone_nearest_link_bal_acc"] = bal(zt[zk], hz[zk]) if zk.any() else None
    for name, mk in models().items():
        p = mk().fit(Fa[sc & (act >= 0)], y3[sc & (act >= 0)]).predict(Fa[te])
        r = {"moving_bal_acc": bal(mv[te], (p > 0).astype(int))}
        if zk.any():
            pz = mk().fit(Fz[sc & (act >= 1)], zone[sc & (act >= 1)]).predict(Fz[zk])
            r["zone_bal_acc"] = bal(zt[zk], pz)
            r["zone_confusion"] = confusion(zt[zk], pz, zones)
        res[name] = r
    return res


def export_logreg(pipe, use, classes, path: Path):
    sc, lr = pipe[0], pipe[1]
    path.write_text(json.dumps({"links": use, "classes": classes, "mean": sc.mean_.tolist(), "scale": sc.scale_.tolist(),
                                "coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist()}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data/ml/rich_v2.npz")
    ap.add_argument("--out", default="/data/ml/real_v2")
    a = ap.parse_args()
    d = np.load(a.data)
    links, feats, zones = list(d["links"]), list(d["feats"]), list(d["zones"])
    sc = d["session"] == SCRIPT
    rate = d["X"][:, :, feats.index("rate")]
    use = [m for i, m in enumerate(links) if (rate[sc & (d["act"] >= 0), i] > 0).mean() > 0.9]
    Fl, Fp, rise = prepare(d, use)
    Fz = np.concatenate([Fl, Fp], 1)
    alive = (rate[:, [links.index(m) for m in use]] > 0).all(1)
    act, zone = np.where(alive & sc, d["act"], -1), d["zone"]
    rep = {"links": [m[-5:] for m in use], "scripted_windows": {k: int((act == c).sum()) for k, c in
                                                                (("покой", 0), ("стоит", 1), ("машет", 2), ("ходит", 3))}}
    rep["E1_zone_leave_one_activity_out"] = e1_zone(Fz, act, zone, zones, rise, use)
    rep["E2_activity_leave_one_zone_out"] = {"pooled": e2_activity(Fp, act, zone), "per_link": e2_activity(Fl, act, zone)}
    te = ~sc & alive & (d["Y"][:, 0] >= 0)
    dd = {"act": act, "zone": zone, "Y": d["Y"]}
    rep["E3_cross_session"] = e3_cross(Fp, Fz, rise, dd, use, zones, sc & alive, te)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    y3 = np.where(act <= 1, 0, act - 1)
    mk = models()["logreg"]
    export_logreg(mk().fit(Fp[act >= 0], y3[act >= 0]), use, ACT3, out / "activity_logreg.json")
    zc = sorted(set(zone[act >= 1].tolist()))
    export_logreg(mk().fit(Fz[act >= 1], zone[act >= 1]), use, [zones[z] for z in zc], out / "zone_logreg.json")
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
