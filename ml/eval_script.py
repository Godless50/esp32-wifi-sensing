"""Evaluate synthetic-trained models on the scripted walk (zone × {stand, wave, walk}) — clean real labels.

Trains on synth_v1.npz only (links without positions or without data in the scripted session are dropped),
tests on every 2 s window of the scripted session that lies inside one scripted phase.
Also reports the nearest-link heuristic (zone of the transmitter whose link deviates most from its baseline).

usage (tensorflow image): python eval_script.py --synth /data/ml/synth_v1.npz --real /data/ml/script_v1.npz
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import tensorflow as tf

ACT = [("стоит", 1), ("машет", 2), ("ход|идёт|идет|встаёт", 3)]
LINK_ZONE = {"02:00:00:00:a1:18": "зал", "98:b3:ef:02:da:28": "спальня", "98:b3:ef:02:da:14": "коридор",
             "02:00:00:00:a1:2c": "санузел", "02:00:00:00:f6:7c": "кухня", "02:00:00:00:e5:57": "спальня",
             "d8:d6:68:b3:4c:23": "коридор", "d8:d6:68:b3:7a:6f": "кухня"}


def script_labels(r, sessions_dir):
    zones = list(r["zones"])
    out_a, out_z = [], []
    cache = {}
    for t, sid in zip(r["t"], r["session"]):
        if sid not in cache:
            ev = [json.loads(x) for x in (Path(sessions_dir) / sid / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            cache[sid] = sorted((e["t"], e) for e in ev if e.get("kind") == "occupancy")
        st = [e for tt, e in cache[sid] if tt <= t - 1.0]
        nxt = [tt for tt, e in cache[sid] if t - 1.0 < tt <= t + 1.0]
        if not st or nxt or not st[-1].get("script") or "переход" in st[-1].get("note", ""):
            out_a.append(-1); out_z.append(-1); continue
        a, z = 0, -1
        for n, v in st[-1]["zones"].items():
            act = v.get("activity", "")
            for pat, code in ACT:
                if v.get("count", 0) > 0 and re.search(pat, act):
                    a, z = code, zones.index(n)
                    break
        if "лежит на диване" in st[-1].get("note", ""):
            a, z = 0, -1  # опорный покой: все лежат/спят
        out_a.append(a); out_z.append(z)
    return np.array(out_a), np.array(out_z)


def mlp(n_in, n_out):
    i = tf.keras.Input((n_in,))
    x = tf.keras.layers.Dense(32, activation="relu")(i)
    x = tf.keras.layers.Dropout(0.2)(x)
    x = tf.keras.layers.Dense(16, activation="relu")(x)
    return tf.keras.Model(i, tf.keras.layers.Dense(n_out, activation="softmax")(x))


def confusion(y, p, names):
    return {names[t]: {names[q]: int(((y == t) & (p == q)).sum()) for q in sorted(set(p.tolist()))} for t in sorted(set(y.tolist()))}


def bal(y, p):
    return round(float(np.mean([(p[y == c] == c).mean() for c in np.unique(y)])), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth", default="/data/ml/synth_v1.npz")
    ap.add_argument("--real", default="/data/ml/script_v1.npz")
    ap.add_argument("--sessions", default="/data/sessions")
    a = ap.parse_args()
    tf.random.set_seed(0)
    s, r = np.load(a.synth), np.load(a.real)
    sl, rl = list(s["links"]), list(r["links"])
    Xr3 = r["X"].reshape(len(r["X"]), len(rl), 3)
    use = [m for m in LINK_ZONE if m in sl and m in rl and (Xr3[:, rl.index(m), 2] > 0).mean() > 0.9]
    print("links used:", [m[-5:] for m in use])
    f = lambda X, links, nf: np.log1p(X.reshape(len(X), len(links), nf)[:, [list(links).index(m) for m in use], :2]
                                      .reshape(len(X), -1)).astype(np.float32)
    Xs, Xr = f(s["X"], sl, 2), f(r["X"], rl, 3)
    live = (Xr3[:, [rl.index(m) for m in use], 2] > 0).all(1)
    ra, rz = script_labels(r, a.sessions)
    ok = live & (ra >= 0)
    mu, sd = Xs.mean(0), Xs.std(0) + 1e-6
    Zs, Zr = (Xs - mu) / sd, (Xr - mu) / sd
    act, zone = s["act"], s["zone"]
    names_a = ["покой", "стоит", "машет", "ходит"]
    zones = list(s["zones"])
    rep = {"windows": int(ok.sum()), "per_class": {names_a[c]: int((ra[ok] == c).sum()) for c in range(4)}}

    # активность: 0/1 (нет движения) | 2 машет | 3 ходит
    ys = np.where(act <= 1, 0, act - 1)
    m = mlp(Zs.shape[1], 3); m.compile("adam", "sparse_categorical_crossentropy"); m.fit(Zs, ys, epochs=15, batch_size=256, verbose=0)
    yr = np.where(ra[ok] <= 1, 0, ra[ok] - 1)
    p = m.predict(Zr[ok], verbose=0).argmax(1)
    rep["activity_bal_acc"] = bal(yr, p)
    rep["activity_confusion"] = confusion(yr, p, ["нет движения", "машет", "ходит"])
    # зона двигающегося (машет/ходит/стоит — где человек)
    sel = act >= 1
    m2 = mlp(Zs.shape[1], 6); m2.compile("adam", "sparse_categorical_crossentropy"); m2.fit(Zs[sel], zone[sel], epochs=20, batch_size=256, verbose=0)
    zk = ok & (rz >= 0) & (ra >= 2)
    pz = m2.predict(Zr[zk], verbose=0).argmax(1)
    rep["zone_moving_bal_acc"] = bal(rz[zk], pz)
    rep["zone_moving_confusion"] = confusion(rz[zk], pz, zones)
    # эвристика «ближайшая линия»: отклонение от фона (фон — окна общего покоя)
    V = Xr3[:, [rl.index(m) for m in use], 0]
    base = np.median(V[ok & (ra == 0)], axis=0)
    hz = np.array([zones.index(LINK_ZONE[use[i]]) for i in (V / base).argmax(1)])
    rep["zone_moving_nearest_link_bal_acc"] = bal(rz[zk], hz[zk])
    rep["zone_moving_nearest_link_confusion"] = confusion(rz[zk], hz[zk], zones)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
