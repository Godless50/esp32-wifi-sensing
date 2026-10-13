"""Breathing check on one radio link: a stable 0.1-0.5 Hz (6-30 /min) component in the CSI.

Chest motion of 5-12 mm changes the reflected path length, so a still, breathing person near a link makes a slow
periodic change of the amplitude on some subcarriers. Per window: values [T, S] (e.g. AGC-normalised amplitude) ->
uniform 10 Hz grid -> minus a 60 s moving mean -> per-series power spectrum -> score = peak power in 0.1-0.5 Hz /
median power in 0.7-2.0 Hz; the 5 best series vote for the frequency. The score of pure noise is 10-14
(max over many bins and series), a sleeping person next to the printer link gives 40-75.
"""

from __future__ import annotations

import numpy as np

FS = 10.0
SCORE_OK = 25.0     # выше шумового фона 10–14 с запасом
SPREAD_OK = 2.0     # лучшие поднесущие согласны в частоте, вдохов/мин


def score_series(t: np.ndarray, X: np.ndarray, t0: float, t1: float, min_rate: float = 5.0):
    """(score, breaths per minute, spread of the 5 best series) or None if there are too few frames."""
    m = (t >= t0) & (t < t1)
    if m.sum() < (t1 - t0) * min_rate or X.shape[1] < 5:
        return None
    tt, XX = t[m], X[m]
    grid = np.arange(t0, t1, 1 / FS)
    G = np.stack([np.interp(grid, tt, XX[:, k]) for k in range(XX.shape[1])], axis=1)
    G = (G - _moving_mean(G, int(60 * FS) | 1)) * np.hanning(len(G))[:, None]
    P = np.abs(np.fft.rfft(G, axis=0)) ** 2
    f = np.fft.rfftfreq(len(G), 1 / FS)
    br, ref = (f >= 0.1) & (f <= 0.5), (f >= 0.7) & (f <= 2.0)
    score = P[br].max(0) / (np.median(P[ref], axis=0) + 1e-12)
    best = np.argsort(-score)[:5]
    freqs = f[br][P[br][:, best].argmax(0)]
    return float(np.median(score[best])), float(np.median(freqs)) * 60, float(freqs.std() * 60)


def _moving_mean(G: np.ndarray, w: int) -> np.ndarray:
    """Centered moving mean along time with edge padding (O(T), same result as convolving with ones(w)/w)."""
    pad = np.pad(G, ((w // 2, w // 2), (0, 0)), mode="edge")
    c = np.vstack([np.zeros((1, G.shape[1])), np.cumsum(pad, axis=0)])
    return (c[w:] - c[:-w]) / w


def amplitude_series(A: np.ndarray) -> np.ndarray:
    """AGC-normalised amplitude of the live subcarriers: [T, 64] -> [T, S]."""
    live = A.mean(0) > 2
    return A[:, live] / (A[:, live].mean(1, keepdims=True) + 1e-3)
