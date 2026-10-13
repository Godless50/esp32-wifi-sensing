"""v1 motion model: training on real windows + heavy synthetic augmentation, test on untouched real data.

Train: random windows from the first 75 % of every session (any offset), each passed through
       channel augmentations; a share of samples is synthesised by composing the variation pattern
       of a real moving window with a still background from another session.
Test:  last 25 % of every session (2 s gap), fixed 0.5 s stride, no augmentation — same split as v0.
Models: CNN on the time series and a tiny MLP on per-subcarrier variation features; both exported int8.

usage (tensorflow image): python train_motion_v1.py --streams /data/ml/streams_v1.npz --out /data/ml/motion_v1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

FS, WIN, TEST_FRAC, GAP_S = 50, 100, 0.25, 2.0
rng = np.random.default_rng(0)


def load(path: str):
    d = np.load(path)
    names = sorted(k[2:] for k in d.files if k.startswith("A_"))
    S = {n: {"A": d[f"A_{n}"].astype(np.float32), "L": d[f"L_{n}"].astype(np.int8)} for n in names}
    for s in S.values():
        n = len(s["L"])
        cut = int(n * (1 - TEST_FRAC))
        s["train_end"] = cut - int(GAP_S * FS)  # окно обучения целиком до этой точки
        s["test_start"] = cut
    return S


def window_label(L: np.ndarray) -> int:
    """Label of a window: its central second must be uniformly labelled; else -1."""
    c = L[WIN // 4 : 3 * WIN // 4]
    if (c < 0).any() or c.min() != c.max():
        return -1
    return int(c[0])


def preprocess(W: np.ndarray, live: np.ndarray) -> np.ndarray:
    A = W[..., live]
    A = A / (A.mean(axis=-1, keepdims=True) + 1e-3)
    A = A - A.mean(axis=-2, keepdims=True)
    return (A * 10.0).astype(np.float32)


def features(Xp: np.ndarray) -> np.ndarray:
    """Per-subcarrier temporal std + its spread + 3 frequency-band powers of the mean variation."""
    std = Xp.std(axis=1)
    m = Xp.mean(axis=2)
    spec = np.abs(np.fft.rfft(m, axis=1)) ** 2
    bands = np.stack([spec[:, 1:5].sum(1), spec[:, 5:15].sum(1), spec[:, 15:].sum(1)], axis=1)
    return np.concatenate([std, std.std(axis=1, keepdims=True), np.log1p(bands)], axis=1).astype(np.float32)


def augment(W: np.ndarray) -> np.ndarray:
    """Channel augmentations on a raw amplitude window [T, 64] (window may be longer than WIN)."""
    T = len(W)
    out = W[np.linspace(0, T - 1, WIN).round().astype(int)]  # растяжение/сжатие по времени
    out = out * np.exp(rng.normal(0, 0.05, 64))[None] * np.exp(rng.normal(0, 0.15))  # усиление
    out = out + rng.normal(0, 0.02 * out.mean(), out.shape)  # шум
    drop = rng.random(WIN) < rng.uniform(0, 0.1)  # потеря пакетов: держим прошлый кадр
    for i in np.flatnonzero(drop):
        if i:
            out[i] = out[i - 1]
    return out


def sample(S, names, weights, want: int | None):
    """Random real window from a train part, optionally with a fixed label."""
    for _ in range(200):
        n = names[rng.choice(len(names), p=weights)]
        s = S[n]
        r = rng.uniform(0.8, 1.25)
        L_len = int(round(WIN * r))
        if s["train_end"] <= L_len:
            continue
        i = rng.integers(0, s["train_end"] - L_len)
        lab = window_label(s["L"][i : i + L_len][np.linspace(0, L_len - 1, WIN).round().astype(int)])
        if lab < 0 or (want is not None and lab != want) or (s["A"][i : i + L_len] < 0).any():
            continue
        return n, s["A"][i : i + L_len], lab
    return None


def generate(S, n_samples: int, synth_share: float):
    names = [n for n in S if S[n]["train_end"] > 2 * WIN]
    w = np.array([S[n]["train_end"] for n in names], float)
    w /= w.sum()
    X, y = [], []
    while len(X) < n_samples:
        if rng.random() < synth_share:
            # сборный пример: узор изменений одного окна на фоне спокойного окна другой записи
            want = int(rng.random() < 0.5)
            a = sample(S, names, w, want)
            b = sample(S, names, w, 0)
            if a is None or b is None or a[0] == b[0]:
                continue
            pa = augment(a[1]); pb = augment(b[1])
            ra = np.clip(pa / (pa.mean(0, keepdims=True) + 1e-3), 0.05, None)
            rb = np.clip(pb / (pb.mean(0, keepdims=True) + 1e-3), 0.05, None)
            synth = pb.mean(0, keepdims=True) * ra * np.sqrt(rb)
            X.append(synth); y.append(want)
        else:
            a = sample(S, names, w, int(rng.random() < 0.5))
            if a is None:
                continue
            X.append(augment(a[1])); y.append(a[2])
    return np.stack(X).astype(np.float32), np.array(y)


def real_set(S, part: str):
    """Real windows, fixed 0.5 s stride: part='test' (last 25 %) or 'train' (first part, for the baseline)."""
    X, y = [], []
    for s in S.values():
        lo, hi = (s["test_start"], len(s["L"]) - WIN) if part == "test" else (0, s["train_end"] - WIN)
        for i in range(lo, hi, WIN // 4):
            lab = window_label(s["L"][i : i + WIN])
            if lab < 0 or (s["A"][i : i + WIN] < 0).any():
                continue
            X.append(s["A"][i : i + WIN]); y.append(lab)
    return np.stack(X).astype(np.float32), np.array(y)


def metrics(y, p) -> dict:
    tp = int(((p == 1) & (y == 1)).sum()); tn = int(((p == 0) & (y == 0)).sum())
    fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    return {"acc": round((tp + tn) / max(1, len(y)), 3),
            "balanced_acc": round(0.5 * (tp / max(1, tp + fn) + tn / max(1, tn + fp)), 3),
            "tp": tp, "tn": tn, "fp": fp, "fn": fn}


def cnn(T, Sn):
    reg = tf.keras.regularizers.l2(1e-4)
    i = tf.keras.Input((T, Sn))
    x = tf.keras.layers.Conv1D(16, 5, strides=2, activation="relu", kernel_regularizer=reg)(i)
    x = tf.keras.layers.Conv1D(16, 5, strides=2, activation="relu", kernel_regularizer=reg)(x)
    x = tf.keras.layers.Conv1D(24, 3, strides=2, activation="relu", kernel_regularizer=reg)(x)
    x = tf.keras.layers.GlobalAveragePooling1D()(x)
    x = tf.keras.layers.Dropout(0.4)(x)
    return tf.keras.Model(i, tf.keras.layers.Dense(2, activation="softmax")(x))


def mlp(F):
    i = tf.keras.Input((F,))
    x = tf.keras.layers.Dense(24, activation="relu")(i)
    x = tf.keras.layers.Dropout(0.3)(x)
    x = tf.keras.layers.Dense(12, activation="relu")(x)
    return tf.keras.Model(i, tf.keras.layers.Dense(2, activation="softmax")(x))


def int8_eval(model, Xtr, Xte, yte, path: Path):
    rep = Xtr[rng.choice(len(Xtr), 400, replace=False)]
    c = tf.lite.TFLiteConverter.from_keras_model(model)
    c.optimizations = [tf.lite.Optimize.DEFAULT]
    c.representative_dataset = lambda: ([r[None]] for r in rep)
    c.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    c.inference_input_type = c.inference_output_type = tf.int8
    tfl = c.convert()
    path.write_bytes(tfl)
    it = tf.lite.Interpreter(model_content=tfl); it.allocate_tensors()
    i_d, o_d = it.get_input_details()[0], it.get_output_details()[0]
    s, z = i_d["quantization"]
    p = []
    for x in Xte:
        it.set_tensor(i_d["index"], np.clip(np.round(x / s + z), -128, 127).astype(np.int8)[None]); it.invoke()
        p.append(int(it.get_tensor(o_d["index"])[0].argmax()))
    return metrics(yte, np.array(p)), len(tfl), {"scale": float(s), "zero_point": int(z)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--streams", default="/data/ml/streams_v1.npz")
    ap.add_argument("--out", default="/data/ml/motion_v1")
    ap.add_argument("--n", type=int, default=40000)
    ap.add_argument("--synth", type=float, default=0.35)
    a = ap.parse_args()
    tf.random.set_seed(0)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    S = load(a.streams)
    allA = np.concatenate([s["A"][(s["A"] >= 0).all(1)] for s in S.values()])
    live = allA.mean(0) > 2.0
    Xte_raw, yte = real_set(S, "test")
    Xrt_raw, yrt = real_set(S, "train")
    Xte = preprocess(Xte_raw, live)
    print(f"test windows={len(yte)} moving={int(yte.sum())}; generating {a.n} train samples...", flush=True)
    Xtr_raw, ytr = generate(S, a.n, a.synth)
    Xtr = preprocess(Xtr_raw, live)
    del Xtr_raw
    print(f"train samples={len(ytr)} moving share={ytr.mean():.2f}", flush=True)

    # базовая линия подбирается на РЕАЛЬНЫХ окнах обучающей части (не на синтетике)
    Xrt = preprocess(Xrt_raw, live)
    e_tr, e_te = Xrt.std(1).mean(1), Xte.std(1).mean(1)
    cands = np.quantile(e_tr, np.linspace(0.05, 0.95, 91))
    thr = max(cands, key=lambda c: metrics(yrt, (e_tr > c).astype(int))["balanced_acc"])
    rep = {"baseline_threshold": float(thr), "test_windows": int(len(yte)), "train_samples": int(len(ytr)), "synth_share": a.synth,
           "baseline": metrics(yte, (e_te > thr).astype(int)), "live_mask": live.astype(int).tolist()}
    print("baseline:", rep["baseline"], flush=True)

    cb = [tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=4, restore_best_weights=True)]
    # валидация — отдельная часть синтетики, тест не участвует в выборе эпохи
    nv = len(ytr) // 10
    m1 = cnn(Xtr.shape[1], Xtr.shape[2])
    m1.compile("adam", "sparse_categorical_crossentropy", metrics=["accuracy"])
    m1.fit(Xtr[nv:], ytr[nv:], validation_data=(Xtr[:nv], ytr[:nv]), epochs=30, batch_size=128, callbacks=cb, verbose=2)
    rep["cnn_float"] = metrics(yte, m1.predict(Xte, verbose=0).argmax(1))
    rep["cnn_int8"], rep["cnn_bytes"], rep["cnn_input_q"] = int8_eval(m1, Xtr, Xte, yte, out / "cnn_int8.tflite")
    print("cnn:", rep["cnn_float"], rep["cnn_int8"], flush=True)

    Ftr, Fte = features(Xtr), features(Xte)
    mu, sd = Ftr.mean(0), Ftr.std(0) + 1e-6
    Ftr, Fte = (Ftr - mu) / sd, (Fte - mu) / sd
    m2 = mlp(Ftr.shape[1])
    m2.compile("adam", "sparse_categorical_crossentropy", metrics=["accuracy"])
    m2.fit(Ftr[nv:], ytr[nv:], validation_data=(Ftr[:nv], ytr[:nv]), epochs=40, batch_size=256, callbacks=cb, verbose=2)
    rep["mlp_float"] = metrics(yte, m2.predict(Fte, verbose=0).argmax(1))
    rep["mlp_int8"], rep["mlp_bytes"], rep["mlp_input_q"] = int8_eval(m2, Ftr, Fte, yte, out / "mlp_int8.tflite")
    rep["mlp_feature_norm"] = {"mean": mu.tolist(), "std": sd.tolist()}
    print("mlp:", rep["mlp_float"], rep["mlp_int8"], flush=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps({k: rep[k] for k in ("baseline", "cnn_float", "cnn_int8", "mlp_float", "mlp_int8",
                                          "cnn_bytes", "mlp_bytes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
