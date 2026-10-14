"""Синтетика «ходят / пусто» на текущих линиях всех плат + честная проверка на реальной записи.

Почему синтетика: в живой записи 14.10 «пусто» идёт после «ходят» один раз, и классификатор может выучить время
(дрейф фона), а не движение. Здесь «ходят» собирается на ФОНЕ ПУСТОЙ КВАРТИРЫ: к нормированной амплитуде окна
пустой квартиры добавляется колебание (амплитуда минус её среднее за окно) из окна, где ходили в другой записи.
Возмущение от движущегося человека в первом приближении аддитивно по амплитуде (как в build_multi.py real_mix).
Синтетическое «ходят» и настоящее «пусто» тогда имеют один и тот же фон и отличаются только движением.

Данные (по линиям плата ← передатчик, только HT-кадры):
  движение  — --moving-src (по умолчанию 20261013-164757-72a4: 27 мин, все нынешние линии между платами);
  пусто     — первая половина «пусто» в --test-session (20261014-084514-4511);
  проверка  — та же --test-session: все окна «ходят» (в синтез не входят) и вторая половина «пусто».
Признаки линии в окне 4 с (корзины 0.1 с, амплитуда нормирована по кадру): разброс во времени (среднее по
поднесущим), то же за последнюю 1 с, 90-й перцентиль по поднесущим. Модель: логистическая регрессия.

usage: cd ml && PYTHONPATH=../server python synth_presence.py [--out ../data/ml/presence_v1.json]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from build_rich import ltf_complex
from link_separability import auroc, segments
from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI, TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

BIN, NB = 0.1, 40  # 40 корзин по 0.1 с = окно 4 с
MIN_FILLED = 0.6   # доля непустых корзин, иначе линия в окне «молчит»


def load_links(sdir: Path, keys: KeyStore) -> dict:
    """(board, src) -> (t[n], A[n, 64]) нормированная по кадру амплитуда HT-LTF, время сервера."""
    fr = defaultdict(list)
    for recv_t, buf in read_csi_bin(sdir / "csi_all.bin"):
        try:
            dev = peek_device_id(buf)
            p = parse_packet(buf, keys.get(dev))
        except (ProtocolError, TypeError):
            continue
        if p.type not in (TYPE_CSI, TYPE_CSI_SRC) or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) < 256:
                continue
            a = np.abs(ltf_complex(f.data, 128))
            m = a.mean()
            if m > 0:
                fr[(p.device_id, f.src or "ap")].append((recv_t - (last - f.t_us) / 1e6, a / m))
    out = {}
    for k, v in fr.items():
        v.sort(key=lambda x: x[0])
        out[k] = (np.array([x[0] for x in v]), np.stack([x[1] for x in v]).astype(np.float32))
    return out


def binned(t: np.ndarray, A: np.ndarray, t0: float, live: np.ndarray):
    """Корзины 0.1 с окна [t0, t0+4): [NB, S] средние, маска непустых."""
    i0, i1 = np.searchsorted(t, t0), np.searchsorted(t, t0 + NB * BIN)
    B = np.zeros((NB, int(live.sum())), np.float32)
    ok = np.zeros(NB, bool)
    if i1 <= i0:
        return B, ok
    idx = ((t[i0:i1] - t0) / BIN).astype(int).clip(0, NB - 1)
    X = A[i0:i1][:, live]
    for b in np.unique(idx):
        B[b] = X[idx == b].mean(0)
        ok[b] = True
    return B, ok


def feats(B: np.ndarray, ok: np.ndarray) -> np.ndarray | None:
    if ok.mean() < MIN_FILLED:
        return None
    X = B[ok]
    sd = X.std(0)
    last = B[-10:][ok[-10:]]
    sd1 = last.std(0).mean() if len(last) >= 3 else sd.mean()
    return np.array([sd.mean(), sd1, np.percentile(sd, 90)], np.float32)


def window_starts(segs, kind, half=None):
    out = []
    for t0, t1, k in segs:
        if k != kind:
            continue
        if half is not None:
            mid = (t0 + t1) / 2
            t0, t1 = (t0, mid) if half == 0 else (mid, t1)
        w = t0
        while w + NB * BIN <= t1:
            out.append(w)
            w += 1.0
    return out


def fit_logreg(X, y, l2=1e-2, iters=3000, lr=0.1):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = p - y
        w -= lr * (Z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return lambda Xn: 1 / (1 + np.exp(-(((Xn - mu) / sd) @ w + b)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--moving-src", default="20261013-164757-72a4")
    ap.add_argument("--test-session", default="20261014-084514-4511")
    ap.add_argument("--per-empty", type=int, default=6, help="synthetic moving windows per empty base window")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rng = np.random.default_rng(0)
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    S = {s: load_links(data / "sessions" / s, keys) for s in (a.moving_src, a.test_session)}
    test_segs = segments(data / "sessions" / a.test_session)
    mov_segs = segments(data / "sessions" / a.moving_src)
    links = sorted(set(S[a.test_session]) & set(S[a.moving_src]))
    print("links in both:", [f"{d[-5:]}<-{s[-5:]}" for d, s in links])
    empty_train = window_starts(test_segs, "empty", half=0)
    empty_test = window_starts(test_segs, "empty", half=1)
    moving_test = window_starts(test_segs, "moving")
    moving_src = window_starts(mov_segs, "moving")
    print(f"windows: empty train {len(empty_train)}, empty test {len(empty_test)}, moving test {len(moving_test)}, "
          f"moving source {len(moving_src)}")
    per_link = {}
    for L in links:
        tt, At = S[a.test_session][L]
        tm, Am = S[a.moving_src][L]
        live = (At.mean(0) > 0.05) & (Am.mean(0) > 0.05)
        Xtr, ytr = [], []
        for w in empty_train:
            Bb, okb = binned(tt, At, w, live)
            f = feats(Bb, okb)
            if f is None:
                continue
            Xtr.append(f); ytr.append(0)
            for w2 in rng.choice(moving_src, a.per_empty):
                Bm, okm = binned(tm, Am, w2, live)
                both = okb & okm
                if both.mean() < MIN_FILLED:
                    continue
                d = Bm - Bm[okm].mean(0)
                syn = Bb + rng.uniform(0.7, 1.3) * d
                f2 = feats(syn, both)
                if f2 is not None:
                    Xtr.append(f2); ytr.append(1)
        Xte, yte = [], []
        for ws, lab in ((moving_test, 1), (empty_test, 0)):
            for w in ws:
                f = feats(*binned(tt, At, w, live))
                if f is not None:
                    Xte.append(f); yte.append(lab)
        Xtr, ytr, Xte, yte = map(np.array, (Xtr, ytr, Xte, yte))
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        model = fit_logreg(Xtr, ytr)
        p = model(Xte)
        per_link[L] = {"train": [int((ytr == 0).sum()), int((ytr == 1).sum())],
                       "test": [int((yte == 0).sum()), int((yte == 1).sum())],
                       "auroc_model": round(auroc(p[yte == 1], p[yte == 0]), 3),
                       "auroc_raw_motion": round(auroc(Xte[yte == 1, 0], Xte[yte == 0, 0]), 3),
                       "acc_at_0.5": round(float(((p > 0.5) == yte).mean()), 3),
                       "false_moving_on_empty": round(float((p[yte == 0] > 0.5).mean()), 3),
                       "missed_moving": round(float((p[yte == 1] <= 0.5).mean()), 3)}
        r = per_link[L]
        print(f"{L[0][-5:]}<-{L[1][-5:]}: train empty/syn {r['train']} test empty/moving {r['test']} | AUROC model "
              f"{r['auroc_model']} raw {r['auroc_raw_motion']} | ложное «ходят» {r['false_moving_on_empty']} "
              f"пропуск {r['missed_moving']}", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps({f"{d}<-{s}": v for (d, s), v in per_link.items()}, indent=1))
        print("->", a.out)


if __name__ == "__main__":
    main()
