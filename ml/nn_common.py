"""Shared pieces of the neural-net trainers (TensorFlow train_nn.py and PyTorch train_nn_torch.py)."""

from __future__ import annotations

import numpy as np

ACT_NAMES = ["нет движения", "машет", "ходит", "мелкие"]
SCRIPTED = ["20261013-053501-ee64", "20261013-065019-45cd"]


def act_class(act):  # 0 покой/стоит -> 0, 2 машет -> 1, 3 ходит -> 2, 4 мелкие -> 3
    return np.where(act < 0, -1, np.where(act <= 1, 0, act - 1))


def squash(x):
    return np.sign(x) * np.log1p(np.abs(x))


def augment(X, rng, y_a, y_z):
    n, L, T, C = X.shape
    out = X.copy()
    out *= np.exp(rng.uniform(np.log(0.5), np.log(2.0), (n, L, 1, 1)))
    for i in range(n):  # растяжение и сдвиг по времени
        s = rng.uniform(0.7, 1.4)
        src = (np.arange(T) - T / 2) / s + T / 2 + rng.uniform(-5, 5)
        src = np.clip(src, 0, T - 1)
        lo = np.floor(src).astype(int); hi = np.minimum(lo + 1, T - 1); w = (src - lo)[None, :, None]
        out[i] = out[i][:, lo] * (1 - w) + out[i][:, hi] * w
    out += rng.normal(0, 0.05, out.shape) * (np.abs(out).mean((2, 3), keepdims=True) + 1e-3)
    drop = rng.random((n, L)) < 0.12
    out[drop] = 0
    # смешивание: окно + другое окно с теми же метками
    key = y_a * 10 + y_z
    for i in np.flatnonzero(rng.random(n) < 0.3):
        same = np.flatnonzero(key == key[i])
        j = rng.choice(same)
        lam = rng.uniform(0.6, 0.9)
        out[i] = lam * out[i] + (1 - lam) * X[j]
    return out


def bal(y, p):
    cs = np.unique(y)
    return round(float(np.mean([(p[y == c] == c).mean() for c in cs])), 3) if len(cs) else None


def confusion(y, p, names):
    return {names[t]: {names[q]: int(((y == t) & (p == q)).sum()) for q in sorted(set(p.tolist()))} for t in sorted(set(y.tolist()))}

