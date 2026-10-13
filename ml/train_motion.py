"""Train the v0 motion model (moving / still) on ds_v0.npz, compare with a std-threshold baseline,
export an int8 TFLite model for TFLite Micro on ESP32-S3.

Split: per session, the last 25 % of time is the test set (with a 2 s gap: windows overlap by 1.5 s),
so the test never sees seconds adjacent to training windows.

usage (tensorflow image): python train_motion.py --ds /data/ml/ds_v0.npz --out /data/ml/motion_v0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

SUB_MIN_AMP = 2.0  # поднесущие с меньшей средней амплитудой — защитные/DC, отбрасываем


def preprocess(X: np.ndarray, live: np.ndarray) -> np.ndarray:
    """[N,T,64] amplitudes -> [N,T,S] AGC-normalised variations around the window mean."""
    A = X[:, :, live]
    A = A / (A.mean(axis=2, keepdims=True) + 1e-3)  # убрать общий уровень (АРУ) в каждом кадре
    A = A - A.mean(axis=1, keepdims=True)  # оставить только изменения во времени
    return (A * 10.0).astype(np.float32)  # масштаб ~[-1, 1] для int8


def split(session: np.ndarray, t: np.ndarray, frac: float = 0.25, gap_s: float = 2.0):
    tr, te = np.zeros(len(t), bool), np.zeros(len(t), bool)
    for s in np.unique(session):
        m = session == s
        cut = np.quantile(t[m], 1 - frac)
        te |= m & (t >= cut)
        tr |= m & (t < cut - gap_s)
    return tr, te


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    tp = int(((p == 1) & (y == 1)).sum()); tn = int(((p == 0) & (y == 0)).sum())
    fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    acc = (tp + tn) / max(1, len(y))
    bal = 0.5 * (tp / max(1, tp + fn) + tn / max(1, tn + fp))
    return {"acc": round(acc, 3), "balanced_acc": round(bal, 3), "tp": tp, "tn": tn, "fp": fp, "fn": fn}


def build_model(T: int, S: int) -> tf.keras.Model:
    inp = tf.keras.Input((T, S))
    x = tf.keras.layers.Conv1D(16, 5, strides=2, activation="relu")(inp)
    x = tf.keras.layers.Conv1D(16, 5, strides=2, activation="relu")(x)
    x = tf.keras.layers.Conv1D(24, 3, strides=2, activation="relu")(x)
    x = tf.keras.layers.GlobalAveragePooling1D()(x)
    x = tf.keras.layers.Dropout(0.3)(x)
    out = tf.keras.layers.Dense(2, activation="softmax")(x)
    return tf.keras.Model(inp, out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", default="/data/ml/ds_v0.npz")
    ap.add_argument("--out", default="/data/ml/motion_v0")
    ap.add_argument("--epochs", type=int, default=40)
    a = ap.parse_args()
    tf.random.set_seed(0)
    np.random.seed(0)
    d = np.load(a.ds)
    X, y, t, sess = d["X"], d["motion"].astype(int), d["t"], d["session"]
    live = X.reshape(-1, 64).mean(axis=0) > SUB_MIN_AMP
    Xp = preprocess(X, live)
    tr, te = split(sess, t)
    print(f"windows train={tr.sum()} test={te.sum()} subcarriers={int(live.sum())} "
          f"moving share train={y[tr].mean():.2f} test={y[te].mean():.2f}")

    # базовая линия: порог по средней временной дисперсии (как индикатор движения в UI)
    energy = Xp.std(axis=1).mean(axis=1)
    cands = np.quantile(energy[tr], np.linspace(0.05, 0.95, 91))
    best = max(cands, key=lambda c: metrics(y[tr], (energy[tr] > c).astype(int))["balanced_acc"])
    base = metrics(y[te], (energy[te] > best).astype(int))
    print("baseline std-threshold test:", base)

    model = build_model(Xp.shape[1], Xp.shape[2])
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"])
    w = {0: 0.5 / max(1e-3, 1 - y[tr].mean()), 1: 0.5 / max(1e-3, y[tr].mean())}
    model.fit(Xp[tr], y[tr], validation_data=(Xp[te], y[te]), epochs=a.epochs, batch_size=64,
              class_weight=w, verbose=2)
    cnn = metrics(y[te], model.predict(Xp[te], verbose=0).argmax(1))
    print("cnn float test:", cnn)

    rep = Xp[tr][np.random.choice(tr.sum(), min(300, tr.sum()), replace=False)]
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = lambda: ([r[None]] for r in rep)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    tfl = conv.convert()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model_int8.tflite").write_bytes(tfl)

    it = tf.lite.Interpreter(model_content=tfl)
    it.allocate_tensors()
    i_d, o_d = it.get_input_details()[0], it.get_output_details()[0]
    s_in, z_in = i_d["quantization"]
    preds = []
    for xw in Xp[te]:
        q = np.clip(np.round(xw / s_in + z_in), -128, 127).astype(np.int8)[None]
        it.set_tensor(i_d["index"], q)
        it.invoke()
        preds.append(int(it.get_tensor(o_d["index"])[0].argmax()))
    q8 = metrics(y[te], np.array(preds))
    print("cnn int8 test:", q8)
    report = {"dataset": a.ds, "windows_train": int(tr.sum()), "windows_test": int(te.sum()),
              "subcarriers": int(live.sum()), "live_mask": live.astype(int).tolist(),
              "baseline": base, "baseline_threshold": float(best), "cnn_float": cnn, "cnn_int8": q8,
              "tflite_bytes": len(tfl), "params": int(model.count_params()),
              "input": {"T": int(Xp.shape[1]), "S": int(Xp.shape[2]), "scale": float(s_in), "zero_point": int(z_in)}}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report[k] for k in ("tflite_bytes", "params", "baseline", "cnn_float", "cnn_int8")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
