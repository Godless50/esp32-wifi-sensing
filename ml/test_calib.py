"""ml/calib.py. Запуск: cd ml && ../.venv-rocm/bin/python -m pytest -q test_calib.py"""

import numpy as np
import pytest

from calib import apply_temperature, brier, ece, fit_temperature, nll, report


def test_brier_bounds():
    y = np.array([0, 1, 2])
    perfect = np.eye(3)[y]
    assert brier(perfect, y) == 0.0
    worst = np.eye(3)[(y + 1) % 3]
    assert brier(worst, y) == pytest.approx(2.0)
    assert brier(np.full((3, 3), 1 / 3), y) == pytest.approx(2 / 3)


def test_binary_input_equals_two_class():
    p1 = np.array([0.9, 0.2, 0.6])
    y = np.array([1, 0, 0])
    p2 = np.stack([1 - p1, p1], 1)
    assert brier(p1, y) == pytest.approx(brier(p2, y))
    assert ece(p1, y) == pytest.approx(ece(p2, y))


def test_ece_zero_when_confidence_matches_frequency():
    rng = np.random.default_rng(0)
    n = 200_000
    conf = rng.uniform(0.5, 1.0, n)
    y = (rng.uniform(size=n) < conf).astype(int)  # класс 1 верен с вероятностью conf
    p = np.stack([1 - conf, conf], 1)
    assert ece(p, y) < 0.01


def test_ece_detects_overconfidence():
    n = 1000
    p = np.tile([0.01, 0.99], (n, 1))
    y = np.r_[np.ones(n // 2, int), np.zeros(n // 2, int)]  # уверенность 0.99, точность 0.5
    assert ece(p, y) == pytest.approx(0.49, abs=1e-6)


def test_temperature_recovers_overconfidence():
    rng = np.random.default_rng(1)
    n, k, t_true = 20_000, 4, 2.5
    logits = rng.normal(0, 2.0, (n, k))
    cal = np.exp(logits) / np.exp(logits).sum(1, keepdims=True)
    y = np.array([rng.choice(k, p=row) for row in cal])  # метки из калиброванных вероятностей
    over = apply_temperature(cal, 1 / t_true)          # та же модель, но самоуверенная
    t = fit_temperature(over, y)
    assert t == pytest.approx(t_true, rel=0.08)
    fixed = apply_temperature(over, t)
    assert ece(fixed, y) < ece(over, y) / 3
    assert nll(fixed, y) < nll(over, y)
    assert (fixed.argmax(1) == over.argmax(1)).all()  # температура не меняет ответ


def test_fit_temperature_falls_back_on_too_few_labels():
    assert fit_temperature(np.full((5, 3), 1 / 3), np.array([0, 1, 2, 0, 1])) == 1.0
    assert fit_temperature(np.full((50, 3), 1 / 3), np.zeros(50, int)) == 1.0


def test_input_validation():
    with pytest.raises(ValueError):
        brier(np.full((3, 2), 0.5), np.array([0, 1]))
    with pytest.raises(ValueError):
        ece(np.full((2, 2), 0.5), np.array([0, -1]))  # -1 (нет метки) отфильтровывает вызывающий
    with pytest.raises(ValueError):
        apply_temperature(np.full((2, 2), 0.5), 0.0)
    assert report(np.zeros((0, 3)), np.zeros(0, int)) == {"n": 0}
