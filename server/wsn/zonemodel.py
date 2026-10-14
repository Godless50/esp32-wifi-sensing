"""Room model of the client's flat: link features, a small neural-net ensemble in numpy, live inference.

The model is trained by `python -m wsn.train` (train.sh) on the walk recorded with the phone app and is run here,
inside the server, every second. Training and live use the same code below, so the net sees exactly what it was
trained on:
  FrameFeed (live.py)  CSI frames -> (link, capture time on the server clock, amplitude of 64 subcarriers);
  link_stats           one link, one window of WIN_S: motion statistics of the AGC-normalised amplitude;
  LinkFloors           every statistic relative to the link's own quiet level (low percentile over FLOOR_S, past
                       only). The radio background drifts (14.10: a threshold fitted on one half of an empty flat
                       did not hold on the other half); the ratio to the recent quiet level drifts much less;
  Ensemble             K small MLPs (numpy forward): softmax over the rooms + EMPTY, sigmoid "somebody moves".
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

EMPTY = "пусто"          # класс «в квартире никого»
WIN_S = 4.0              # окно признаков
STEP_S = 1.0             # ответ модели раз в секунду (и шаг окон при обучении)
LAG_S = 0.5              # кадры приходят пачками: окно кончается чуть раньше «сейчас»
BIN_S = 0.25
MIN_FRAMES = 8
MIN_LIVE_SC = 8
NULL_AMP = 1.0
FLOOR_S, FLOOR_Q, FLOOR_MIN_N = 3600.0, 10.0, 300  # тихий уровень: 10-й перцентиль за час (не меньше 5 мин)
STATS = ("e", "e_last", "jit", "dev")
FEATURES = ("log_e_rel", "log_e_last_rel", "log_jit_rel", "log_dev_rel", "log_e", "alive")
NF = len(FEATURES)
EPS = 1e-4
BUF_S = WIN_S + LAG_S + 2.0
NET_KEYS = ("W1", "b1", "W2", "b2", "Wz", "bz", "Wm", "bm")


def link_id(device_id: str, src: str) -> str:
    """Link name: receiving board | transmitter."""
    return f"{device_id.lower()}|{src.lower()}"


def link_stats(t: np.ndarray, A: np.ndarray, t0: float, t1: float) -> np.ndarray | None:
    """Motion statistics of one link in [t0, t1): t sorted capture times, A amplitudes [n, 64]. None: too few frames.

    e       mean over live subcarriers of the temporal std of the normalised amplitude (whole window)
    e_last  the same over the last second (fresh movement)
    jit     median over BIN_S bins of the std inside the bin (fast motion: steps, arms)
    dev     mean |bin mean - window mean| (slow change: a person moved to another place)
    """
    i0, i1 = np.searchsorted(t, [t0, t1])
    if i1 - i0 < MIN_FRAMES:
        return None
    tt = t[i0:i1]
    a = np.asarray(A[i0:i1], dtype=np.float32)
    live = a.mean(axis=0) > NULL_AMP
    if live.sum() < MIN_LIVE_SC:
        return None
    a = a[:, live]
    a = a / (a.mean(axis=1, keepdims=True) + 1e-3)  # АРУ и мощность передатчика: только форма канала
    e = float(a.std(axis=0).mean())
    last = tt >= t1 - 1.0
    e_last = float(a[last].std(axis=0).mean()) if last.sum() >= 3 else e
    nb = int(round((t1 - t0) / BIN_S))
    b = np.minimum(((tt - t0) / BIN_S).astype(int), nb - 1)
    mean = a.mean(axis=0)
    jits, devs = [], []
    for k in range(nb):
        m = b == k
        n = int(m.sum())
        if n == 0:
            continue
        bm = a[m].mean(axis=0)
        devs.append(float(np.abs(bm - mean).mean()))
        if n >= 2:
            jits.append(float(a[m].std(axis=0).mean()))
    jit = float(np.median(jits)) if jits else e
    dev = float(np.mean(devs)) if devs else 0.0
    return np.array([e, e_last, jit, dev], dtype=np.float64)


class LinkFloors:
    """Quiet level of every statistic of every link: FLOOR_Q-th percentile over the past FLOOR_S (causal).

    Until a link has FLOOR_MIN_N windows of history its floor comes from `warm` (training: the first minute of the
    recording; live: the floors saved before a restart) or, without it, from the short history itself.
    """

    def __init__(self, warm: dict[str, np.ndarray] | None = None):
        self.hist: dict[str, deque] = {}
        self.warm = {k: np.asarray(v, float) for k, v in (warm or {}).items()}

    def floor(self, link: str) -> np.ndarray | None:
        q = self.hist.get(link)
        if q and len(q) >= FLOOR_MIN_N:
            return np.percentile(np.stack([s for _, s in q]), FLOOR_Q, axis=0)
        if link in self.warm:
            return self.warm[link]
        if q:
            return np.percentile(np.stack([s for _, s in q]), FLOOR_Q, axis=0)
        return None

    def features(self, link: str, t: float, st: np.ndarray | None) -> np.ndarray:
        out = np.zeros(NF)
        if st is None:
            return out  # линия молчит: все признаки 0, alive = 0
        q = self.hist.setdefault(link, deque())
        q.append((t, st))
        while q and q[0][0] < t - FLOOR_S:
            q.popleft()
        fl = self.floor(link)
        out[:4] = np.log((st + EPS) / (fl + EPS))
        out[4] = math.log(st[0] + EPS)
        out[5] = 1.0
        return out

    def snapshot(self) -> dict[str, list[float]]:
        return {k: [round(float(v), 6) for v in fl] for k in self.hist if (fl := self.floor(k)) is not None}


def warm_floors(stats: dict[str, list[np.ndarray]]) -> dict[str, np.ndarray]:
    """Floors from the first FLOOR_MIN_N windows of each link (start of a recording)."""
    out = {}
    for link, rows in stats.items():
        rows = [r for r in rows if r is not None][:FLOOR_MIN_N]
        if rows:
            out[link] = np.percentile(np.stack(rows), FLOOR_Q, axis=0)
    return out


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0)


def net_forward(net: dict, Z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z standardised inputs [N, D] -> zone logits [N, K], moving logit [N], last hidden layer [N, H]."""
    h1 = _relu(Z @ net["W1"] + net["b1"])
    h2 = _relu(h1 @ net["W2"] + net["b2"])
    return h2 @ net["Wz"] + net["bz"], (h2 @ net["Wm"] + net["bm"])[:, 0], h2


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


class Ensemble:
    """Trained model: meta.json (links, zones, metrics) + model.npz (standardisation, K nets)."""

    def __init__(self, meta: dict, arrays: dict[str, np.ndarray]):
        self.meta = meta
        self.links: list[str] = list(meta["links"])
        self.zones: list[str] = list(meta["zones"])  # последний — EMPTY
        self.version = int(meta.get("version", 0))
        self.mu = np.asarray(arrays["mu"], float)
        self.sd = np.asarray(arrays["sd"], float)
        self.nets = [{k: np.asarray(arrays[f"n{i}_{k}"], float) for k in NET_KEYS} for i in range(int(meta["nets"]))]
        if self.mu.shape != (len(self.links) * NF,) or not self.nets:
            raise ValueError("model arrays do not match its links")

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """X [N, links, NF] -> P(zone) [N, zones], P(moving) [N] (mean over the nets)."""
        Z = (X.reshape(len(X), -1) - self.mu) / self.sd
        pz, pm = 0.0, 0.0
        for net in self.nets:
            lz, lm, _ = net_forward(net, Z)
            pz = pz + softmax(lz)
            pm = pm + sigmoid(lm)
        return pz / len(self.nets), pm / len(self.nets)

    @classmethod
    def load(cls, model_dir: Path) -> "Ensemble":
        meta = json.loads((Path(model_dir) / "meta.json").read_text(encoding="utf-8"))
        with np.load(Path(model_dir) / "model.npz") as z:
            arrays = {k: z[k] for k in z.files}
        return cls(meta, arrays)


def save_model(model_dir: Path, meta: dict, mu: np.ndarray, sd: np.ndarray, nets: list[dict]) -> None:
    """Write model.npz then meta.json (the server reloads on a new meta.json, so it never sees half a model)."""
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    arrays = {"mu": mu, "sd": sd}
    for i, net in enumerate(nets):
        for k in NET_KEYS:
            arrays[f"n{i}_{k}"] = net[k]
    tmp = model_dir / ".model.tmp.npz"
    np.savez(tmp, **arrays)
    os.replace(tmp, model_dir / "model.npz")
    tmp = model_dir / ".meta.tmp.json"
    tmp.write_text(json.dumps({**meta, "nets": len(nets)}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, model_dir / "meta.json")


RELOAD_EVERY_S = 10.0
FLOORS_SAVE_S = 60.0
FLOORS_MAX_AGE_S = 3600.0


class LiveInfer:
    """The flat's model on live CSI: LiveHub frame listener + tick() every STEP_S from the server loop.

    Picks up a new model (data/model/meta.json) without a restart. The quiet levels of the links are kept even
    without a model (it starts warm) and are saved, so a restart of the server does not reset them.
    """

    def __init__(self, model_dir: Path, publish: Callable[[dict], None], state_path: Path | None = None):
        self.model_dir = Path(model_dir)
        self.publish = publish
        self.state_path = Path(state_path) if state_path else None
        self.model: Ensemble | None = None
        self.error: str | None = None
        self.last: dict | None = None
        self._mtime: int | None = None
        self._checked = -1e9
        self._saved = time.monotonic()
        self.buf: dict[str, deque] = {}
        self.floors = LinkFloors(self._load_floors())

    def _load_floors(self) -> dict | None:
        if self.state_path is None or not self.state_path.exists():
            return None
        try:
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
            if time.time() - float(st["wall"]) <= FLOORS_MAX_AGE_S:
                return {k: np.asarray(v, float) for k, v in st["floors"].items() if len(v) == len(STATS)}
        except (ValueError, KeyError, TypeError, OSError):
            log.warning("link floors %s unreadable; starting cold", self.state_path)
        return None

    def _save_floors(self) -> None:
        if self.state_path is None:
            return
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"wall": time.time(), "floors": self.floors.snapshot()}), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:
            log.warning("link floors not saved: %s", e)

    def reload(self, now: float) -> bool:
        self._checked = now
        try:
            mt = (self.model_dir / "meta.json").stat().st_mtime_ns
        except FileNotFoundError:
            if self.model is not None:
                log.warning("room model removed")
            self.model, self._mtime = None, None
            return False
        if mt == self._mtime:
            return False
        self._mtime = mt
        try:
            m = Ensemble.load(self.model_dir)
        except Exception as e:  # битая модель не должна ронять сервер: работаем по геометрии линий
            self.error = f"{type(e).__name__}: {e}"
            log.error("room model in %s not loaded: %s", self.model_dir, self.error)
            return False
        self.model, self.error = m, None
        for lk, fl in (m.meta.get("floors") or {}).items():  # тихий уровень из обучения — пока нет своего
            if len(fl) == len(STATS):
                self.floors.warm.setdefault(lk, np.asarray(fl, float))
        log.info("room model v%d: %d links, zones %s", m.version, len(m.links), m.zones)
        return True

    def on_frame(self, device_id: str, src: str, ft: float, amp: np.ndarray) -> None:
        k = link_id(device_id, src)
        q = self.buf.get(k)
        if q is None:
            q = self.buf[k] = deque()
        q.append((ft, amp))
        while q[0][0] < ft - BUF_S:
            q.popleft()

    def window_features(self, links: list[str], t1: float) -> np.ndarray:
        """Features [len(links), NF] of the window ending at t1; also advances the quiet levels of these links."""
        X = np.zeros((len(links), NF))
        for i, lk in enumerate(links):
            q = self.buf.get(lk)
            st = None
            if q:
                t = np.fromiter((x[0] for x in q), float, len(q))
                order = np.argsort(t, kind="stable")
                st = link_stats(t[order], np.stack([q[j][1] for j in order]), t1 - WIN_S, t1)
            X[i] = self.floors.features(lk, t1, st)
        return X

    def tick(self, now: float) -> dict | None:
        if now - self._checked >= RELOAD_EVERY_S:
            self.reload(now)
        m = self.model
        t1 = now - LAG_S
        links = list(m.links) if m else []
        extra = sorted(k for k in self.buf if k not in set(links))  # пол линий копится и без модели
        X = self.window_features(links + extra, t1)
        if now - self._saved >= FLOORS_SAVE_S:
            self._saved = now
            self._save_floors()
        if m is None:
            return None
        pz, pm = m.predict(X[None, : len(links)])
        p = pz[0]
        rooms = m.zones[:-1]
        pr = p[:-1] / max(float(p[:-1].sum()), 1e-9)  # где человек, если он есть
        best = int(np.argmax(pr))
        mv = float(pm[0])
        msg = {
            "type": "pred", "source": "model", "device_id": "", "t": now,
            "zone": rooms[best], "zone_p": round(float(pr[best]), 3),
            "probs": {r: round(float(v), 3) for r, v in zip(rooms, pr)},
            "empty_p": round(float(p[-1]), 3), "moving_p": round(mv, 3),
            "activity": "ходит" if mv >= 0.5 else "нет движения", "activity_p": round(max(mv, 1 - mv), 3),
            "links_live": int(X[: len(links), -1].sum()), "links_total": len(links),
            "model": f"квартира v{m.version}",
        }
        self.last = msg
        self.publish(msg)
        return msg

    def status(self) -> dict:
        m = self.model
        return {
            "loaded": m is not None, "error": self.error,
            "version": m.version if m else None,
            "zones": m.zones if m else None,
            "links": m.links if m else None,
            "created": m.meta.get("created") if m else None,
            "metrics": m.meta.get("metrics") if m else None,
            "links_seen": sorted(self.buf),
            # линии, по которым кадры идут прямо сейчас: обучение берёт только записи на этих же линиях
            "links_now": sorted(k for k, q in self.buf.items() if q and q[-1][0] >= time.monotonic() - 10.0),
        }
