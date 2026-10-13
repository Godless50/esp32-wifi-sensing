"""Train on physics-based synthetic windows, test on REAL labelled windows (never seen in training).

Synthetic: synth_v1.npz (act: 0 empty, 1 still+breathing, 2 fine motion, 3 walking; zone of the person).
Real:      multilink_v2.npz (spoken labels): evaluated where the label is unambiguous:
  motion  = some zone has a movement word (walking or waving) vs everybody still/asleep;
  fine    = waving hands (машет) vs walking;
  zone    = zone of the single moving person.
Links: 8 with known positions (iComm excluded — its location is unknown, synthetic cannot model it).

usage (tensorflow image): python train_synth.py --synth /data/ml/synth_v1.npz --real /data/ml/multilink_v2.npz
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import tensorflow as tf

WALK = re.compile(r"ход|идёт|идет|иду|встаёт|встает|встал|пошёл|пошел", re.I)
FINE = re.compile(r"машет", re.I)
ANYMOVE = re.compile(r"движ", re.I)  # «движется у узла»: движение есть, тип неизвестен
USE = ["02:00:00:00:a1:18", "98:b3:ef:02:da:28", "98:b3:ef:02:da:14", "02:00:00:00:a1:2c",
       "02:00:00:00:f6:7c", "02:00:00:00:e5:57", "d8:d6:68:b3:4c:23", "d8:d6:68:b3:7a:6f"]


def feats(X, links, nf):
    idx = [list(links).index(m) for m in USE]
    return np.log1p(X.reshape(len(X), len(links), nf)[:, idx, :2].reshape(len(X), -1)).astype(np.float32)


def real_labels(ds_path, sessions_dir):
    """Per real window: motion (0/1/-1), kind (2 fine / 3 walk / -1), moving zone index (or -1)."""
    r = np.load(ds_path)
    zones = list(r["zones"])
    # восстановим текст активности окна по событиям (в npz только бинарные метки)
    out_m, out_k, out_z = [], [], []
    cache = {}
    for t, sid in zip(r["t"], r["session"]):
        if sid not in cache:
            ev = [json.loads(x) for x in (Path(sessions_dir) / sid / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            cache[sid] = sorted((e["t"], e["zones"]) for e in ev if e.get("kind") == "occupancy")
        st = [z for tt, z in cache[sid] if tt <= t - 1.0]
        if not st:
            out_m.append(-1); out_k.append(-1); out_z.append(-1); continue
        z = st[-1]
        movers = [(n, v.get("activity", "")) for n, v in z.items() if v.get("count", 0) > 0 and
                  (WALK.search(v.get("activity", "")) or FINE.search(v.get("activity", "")) or ANYMOVE.search(v.get("activity", "")))]
        if not movers:
            out_m.append(0); out_k.append(-1); out_z.append(-1)
        elif len(movers) == 1:
            n, act = movers[0]
            kind = 2 if FINE.search(act) else 3 if WALK.search(act) else -1
            out_m.append(1); out_k.append(kind); out_z.append(zones.index(n) if n in zones else -1)
        else:
            out_m.append(1); out_k.append(-1); out_z.append(-1)
    return np.array(out_m), np.array(out_k), np.array(out_z)


def mlp(n_in, n_out):
    i = tf.keras.Input((n_in,))
    x = tf.keras.layers.Dense(32, activation="relu")(i)
    x = tf.keras.layers.Dropout(0.2)(x)
    x = tf.keras.layers.Dense(16, activation="relu")(x)
    return tf.keras.Model(i, tf.keras.layers.Dense(n_out, activation="softmax")(x))


def bal_acc(y, p, classes):
    accs = [float((p[y == c] == c).mean()) for c in classes if (y == c).any()]
    return round(float(np.mean(accs)), 3), {int(c): (int((p[y == c] == c).sum()), int((y == c).sum())) for c in classes if (y == c).any()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth", default="/data/ml/synth_v1.npz")
    ap.add_argument("--real", default="/data/ml/multilink_v2.npz")
    ap.add_argument("--sessions", default="/data/sessions")
    ap.add_argument("--out", default="/data/ml/synth_v1_model")
    a = ap.parse_args()
    tf.random.set_seed(0)
    s = np.load(a.synth)
    r = np.load(a.real)
    Xs = feats(s["X"], s["links"], 2)
    live = (r["X"].reshape(len(r["X"]), len(r["links"]), 3)[:, [list(r["links"]).index(m) for m in USE], 2] > 0).all(1)
    Xr = feats(r["X"], r["links"], 3)
    rm, rk, rz = real_labels(a.real, a.sessions)
    ok = live & (rm >= 0)
    mu, sd = Xs.mean(0), Xs.std(0) + 1e-6
    Zs, Zr = (Xs - mu) / sd, (Xr - mu) / sd
    act, zone = s["act"], s["zone"]
    rep = {"synthetic": int(len(Xs)), "real_windows_eval": int(ok.sum())}

    # 1) движение: синтетика {0,1} -> 0, {2,3} -> 1
    ym = (act >= 2).astype(int)
    m1 = mlp(Zs.shape[1], 2); m1.compile("adam", "sparse_categorical_crossentropy")
    m1.fit(Zs, ym, epochs=15, batch_size=256, verbose=0, validation_split=0.1)
    pm = m1.predict(Zr[ok], verbose=0).argmax(1)
    rep["motion_real"] = bal_acc(rm[ok], pm, [0, 1])
    # 2) мелкая моторика vs ходьба (только реальные окна с одним двигающимся)
    sel = act >= 2
    m2 = mlp(Zs.shape[1], 2); m2.compile("adam", "sparse_categorical_crossentropy")
    m2.fit(Zs[sel], (act[sel] == 3).astype(int), epochs=15, batch_size=256, verbose=0, validation_split=0.1)
    k_ok = ok & (rk >= 2)
    pk = m2.predict(Zr[k_ok], verbose=0).argmax(1)
    rep["fine_vs_walk_real"] = bal_acc((rk[k_ok] == 3).astype(int), pk, [0, 1])
    # 3) зона движения
    sel = act >= 2
    m3 = mlp(Zs.shape[1], 6); m3.compile("adam", "sparse_categorical_crossentropy")
    m3.fit(Zs[sel], zone[sel], epochs=20, batch_size=256, verbose=0, validation_split=0.1)
    z_ok = ok & (rz >= 0)
    pz = m3.predict(Zr[z_ok], verbose=0).argmax(1)
    zones = list(s["zones"])
    rep["zone_real"] = bal_acc(rz[z_ok], pz, sorted(set(rz[z_ok].tolist())))
    rep["zone_real_confusion"] = {zones[t]: {zones[p]: int(((rz[z_ok] == t) & (pz == p)).sum()) for p in set(pz.tolist())}
                                  for t in sorted(set(rz[z_ok].tolist()))}
    # синтетика на самой себе (проверка, что задача вообще решаема по признакам)
    rep["zone_synthetic_selfcheck"] = bal_acc(zone[sel][-2000:], m3.predict(Zs[sel][-2000:], verbose=0).argmax(1), list(range(6)))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
