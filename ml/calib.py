"""Калибровка вероятностей: Brier, ECE, температура. Только numpy — годится и для learner, и для отчётов.

Зачем: «уверенность 0.9» должна означать «прав в ~90 % случаев». Точность (accuracy) этого не проверяет:
модель может стать точнее и при этом самоувереннее. Гейт learner сравнивает старую и новую модель ещё и по
ECE/Brier (spec v2, п. «Калибровка»). Температура подбирается после обучения/квантования; при сдвиге домена
она устаревает (Ovadia et al., NeurIPS 2019), поэтому пересчитывается на свежих метках при каждой выдаче модели.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-12


def _as_probs(p: np.ndarray) -> np.ndarray:
    """[N] вероятность класса 1 -> [N, 2]; [N, K] без изменений."""
    p = np.asarray(p, dtype=np.float64)
    if p.ndim == 1:
        p = np.stack([1.0 - p, p], axis=1)
    if p.ndim != 2:
        raise ValueError(f"probabilities must be [N] or [N, K], got shape {p.shape}")
    return p


def _check(p: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = _as_probs(p)
    y = np.asarray(y).astype(np.int64)
    if len(p) != len(y):
        raise ValueError(f"{len(p)} probability rows but {len(y)} labels")
    if len(y) and (y.min() < 0 or y.max() >= p.shape[1]):
        raise ValueError("labels out of range (unlabelled -1 must be filtered by the caller)")
    return p, y


def brier(p: np.ndarray, y: np.ndarray) -> float:
    """Многоклассовый Brier: среднее по окнам ||p - onehot(y)||^2 (0 — идеально, 2 — максимум)."""
    p, y = _check(p, y)
    if len(y) == 0:
        return float("nan")
    oh = np.zeros_like(p)
    oh[np.arange(len(y)), y] = 1.0
    return float(((p - oh) ** 2).sum(axis=1).mean())


def ece(p: np.ndarray, y: np.ndarray, bins: int = 15) -> float:
    """Expected Calibration Error по верхнему классу, равные по ширине корзины уверенности."""
    p, y = _check(p, y)
    if len(y) == 0:
        return float("nan")
    conf = p.max(axis=1)
    hit = (p.argmax(axis=1) == y).astype(np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1], right=True), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(hit[m].mean() - conf[m].mean())
    return float(total)


def nll(p: np.ndarray, y: np.ndarray) -> float:
    p, y = _check(p, y)
    if len(y) == 0:
        return float("nan")
    return float(-np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0)).mean())


def apply_temperature(p: np.ndarray, t: float) -> np.ndarray:
    """softmax(log p / t). Для ансамбля p — среднее вероятностей; argmax от температуры не меняется."""
    if not t > 0:
        raise ValueError(f"temperature must be > 0, got {t}")
    squeeze = np.asarray(p).ndim == 1
    p = _as_probs(p)
    z = np.log(np.clip(p, EPS, 1.0)) / t
    z -= z.max(axis=1, keepdims=True)
    q = np.exp(z)
    q /= q.sum(axis=1, keepdims=True)
    return q[:, 1] if squeeze else q


def fit_temperature(p: np.ndarray, y: np.ndarray, lo: float = 0.25, hi: float = 8.0, steps: int = 200) -> float:
    """Температура с минимальным NLL: сетка в лог-шкале + уточнение золотым сечением. 1.0, если меток мало."""
    p, y = _check(p, y)
    if len(y) < 20 or len(np.unique(y)) < 2:
        return 1.0
    grid = np.exp(np.linspace(np.log(lo), np.log(hi), steps))
    losses = np.array([nll(apply_temperature(p, t), y) for t in grid])
    i = int(np.argmin(losses))
    a, b = grid[max(0, i - 1)], grid[min(len(grid) - 1, i + 1)]
    g = (np.sqrt(5.0) - 1.0) / 2.0
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(40):
        if nll(apply_temperature(p, c), y) < nll(apply_temperature(p, d), y):
            b = d
        else:
            a = c
        c, d = b - g * (b - a), a + g * (b - a)
    return float((a + b) / 2.0)


def report(p: np.ndarray, y: np.ndarray, bins: int = 15) -> dict:
    """Сводка для журнала: n, точность, Brier, ECE, NLL (округлено)."""
    p, y = _check(p, y)
    if len(y) == 0:
        return {"n": 0}
    return {"n": int(len(y)), "acc": round(float((p.argmax(1) == y).mean()), 4), "brier": round(brier(p, y), 4),
            "ece": round(ece(p, y, bins), 4), "nll": round(nll(p, y), 4)}
