"""Neural net on per-link CSI sequences (build_seq.py), heavy augmentation, honest leave-one-session-out test.

Input per window: 10 links x 40 steps (0.1 s) x 4 channels. A shared 1-D conv encoder reads every link's sequence;
link embeddings are concatenated in a fixed order (zone needs to know WHICH link moved) and pooled (activity
should not depend on where it happens). Heads: activity (still / wave / walk / fine), zone of the subject
(scripted windows), somebody moves (spoken sessions too). Missing labels are masked out of the loss.

Augmentation, new random variant of every window in every epoch:
  per-link gain 0.5-2x (log-uniform), time stretch 0.7-1.4x, time shift +-0.5 s, gaussian noise,
  dropping a link (zeros, as if it were silent), mixing two windows with the same labels.

usage (tensorflow image): python train_nn.py --data /data/ml/seq_v1.npz --out /data/ml/nn_v1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

from nn_common import ACT_NAMES, SCRIPTED, act_class, augment, bal, confusion, squash


def build(L, T, C, n_zone):
    inp = tf.keras.Input((L, T, C))
    enc = tf.keras.Sequential([
        tf.keras.layers.Conv1D(24, 5, padding="same", activation="relu"),
        tf.keras.layers.Conv1D(32, 5, padding="same", activation="relu", dilation_rate=2),
        tf.keras.layers.Conv1D(32, 3, padding="same", activation="relu", dilation_rate=4),
        tf.keras.layers.GlobalAveragePooling1D(),
    ])
    e = tf.keras.layers.TimeDistributed(enc)(inp)            # [L, 32] по линиям
    flat = tf.keras.layers.Flatten()(e)                       # какая линия — важно для зоны
    pooled = tf.keras.layers.Concatenate()([tf.keras.layers.GlobalMaxPooling1D()(e), tf.keras.layers.GlobalAveragePooling1D()(e)])
    h_z = tf.keras.layers.Dropout(0.3)(tf.keras.layers.Dense(64, activation="relu")(flat))
    h_a = tf.keras.layers.Dropout(0.3)(tf.keras.layers.Dense(48, activation="relu")(pooled))
    out_a = tf.keras.layers.Dense(4, activation="softmax", name="act")(h_a)
    out_z = tf.keras.layers.Dense(n_zone, activation="softmax", name="zone")(h_z)
    out_m = tf.keras.layers.Dense(1, activation="sigmoid", name="move")(h_a)
    return tf.keras.Model(inp, [out_a, out_z, out_m])


def fit_predict(Xtr, ya, yz, ym, Xte, epochs, seed):
    rng = np.random.default_rng(seed)
    tf.random.set_seed(seed)
    m = build(Xtr.shape[1], Xtr.shape[2], Xtr.shape[3], 4)
    m.compile(tf.keras.optimizers.Adam(2e-3),
              loss={"act": "sparse_categorical_crossentropy", "zone": "sparse_categorical_crossentropy", "move": "binary_crossentropy"})
    # веса классов активности (покой/стоит сильно больше остальных)
    cw = np.zeros(4)
    for c in range(4):
        k = (ya == c).sum()
        cw[c] = 0 if k == 0 else len(ya[ya >= 0]) / (4 * k)
    for ep in range(epochs):
        Xa = squash(augment(Xtr, rng, ya, yz))
        w_a = np.where(ya >= 0, cw[np.maximum(ya, 0)], 0.0)
        w_z = (yz >= 0).astype(float)
        w_m = (ym >= 0).astype(float)
        m.fit(Xa, {"act": np.maximum(ya, 0), "zone": np.maximum(yz, 0), "move": np.maximum(ym, 0).astype(float)},
              sample_weight={"act": w_a, "zone": w_z, "move": w_m}, batch_size=64, epochs=1, verbose=0)
    pa, pz, pm = m.predict(squash(Xte), verbose=0)
    return pa.argmax(1), pz.argmax(1), pm[:, 0], m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data/ml/seq_v1.npz")
    ap.add_argument("--out", default="/data/ml/nn_v1")
    ap.add_argument("--epochs", type=int, default=60)
    a = ap.parse_args()
    d = np.load(a.data)
    X = d["X"].astype(np.float32)
    sess, act, zone = d["session"], d["act"], d["zone"]
    ZMAP = {1: 0, 2: 1, 3: 2, 4: 3}  # зал, кухня, коридор, санузел
    zn = ["зал", "кухня", "коридор", "санузел"]
    ya = act_class(act)
    yz = np.array([ZMAP.get(int(z), -1) if a_ >= 1 else -1 for z, a_ in zip(zone, act)])
    ym = np.where(np.isin(sess, SCRIPTED), np.where(ya < 0, -1, (ya > 0).astype(int)), d["moving"])
    rep = {"windows": {s: int((sess == s).sum()) for s in np.unique(sess)}, "epochs": a.epochs, "loso": {}}
    for test in SCRIPTED:
        tr, te = sess != test, (sess == test) & (ya >= 0)
        pa, pz, pm, _ = fit_predict(X[tr], ya[tr], yz[tr], ym[tr], X[te], a.epochs, 0)
        zt = yz[te]
        rep["loso"][test] = {
            "activity_bal_acc": bal(ya[te], pa), "activity_confusion": confusion(ya[te], pa, ACT_NAMES),
            "zone_bal_acc": bal(zt[zt >= 0], pz[zt >= 0]), "zone_confusion": confusion(zt[zt >= 0], pz[zt >= 0], zn),
            "moving_bal_acc": bal((ya[te] > 0).astype(int), (pm > 0.5).astype(int)),
        }
        print(test, json.dumps({k: v for k, v in rep["loso"][test].items() if "confusion" not in k}), flush=True)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    allm = ya >= 0
    _, _, _, m = fit_predict(X, ya, yz, ym, X[:1], a.epochs, 0)  # итоговая модель на всех данных
    m.save(out / "model.keras")
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print("params", m.count_params(), "saved", out)


if __name__ == "__main__":
    main()
