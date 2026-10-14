"""Train the flat's room model from the walk recorded with the phone app (train.sh -> python -m wsn.train).

Data: the newest finished walk session (phone AR poses + CSI of every board, csi_all.bin), plus earlier walks and
"empty" sessions recorded on the same radio links. Labels come from the phone, not from people's memory:
  * zone   — where the phone (carried by the person) was at the end of the window, in the zones of the plan
             (the same labels as the tracker on the map); outside the plan during an "empty" step of the scenario
             -> EMPTY (nobody home);
  * moving — the phone's floor speed over the last 2 s of the window (> 0.25 m/s yes, < 0.08 m/s no).
Recordings labelled on the /people or /scenario pages (occupancy events) add "nobody home" and "somebody moves"
windows with zone sets instead of exact zones.

Model: wsn.zonemodel features (all links of all boards, relative to each link's quiet level), an ensemble of small
MLPs trained in numpy on all CPU cores (more cores -> faster). Before the final fit, an honest check: the recording
is cut into contiguous time blocks, each block is predicted by a net that never saw it (plus a WIN_S gap).
Result: data/model/{model.npz, meta.json, report.txt}; the running server picks it up within 10 s.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):  # параллелим сетями, не BLAS
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import multiprocessing as mp  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from .live import FrameFeed  # noqa: E402
from .person import Grid  # noqa: E402
from .plan import build_plan  # noqa: E402
from .protocol import CSI_TYPES, ProtocolError, parse_packet  # noqa: E402
from .store import Store, read_csi_bin  # noqa: E402
from .zonemodel import (  # noqa: E402
    EMPTY, FEATURES, FLOOR_Q, NF, STATS, STEP_S, WIN_S, LinkFloors, link_id, link_stats, net_forward,
    save_model, sigmoid, softmax, warm_floors,
)

log = logging.getLogger("train")

POS_LAG_S = 0.5          # место человека — в конце окна (последние полсекунды)
POS_MAX_GAP_S = 1.5      # дальше от ближайшей позы телефона — место неизвестно
NEAR_PLAN_M = 0.6        # вне плана ближе этого — ближайшая зона (стоит у стены)
MOVE_YES, MOVE_NO = 0.25, 0.08  # м/с за последние 2 с окна
EMPTY_GRACE_S = 20.0     # после начала шага «выйдите из квартиры» — время дойти до двери
LINK_MIN_SHARE = 0.3     # линия участвует, если жива хотя бы в такой доле окон новой записи
SAME_LINKS = 0.7         # прежняя запись подходит, если в ней жива такая доля линий новой
MIN_WINDOWS = 120


class Status:
    """data/run/train_status.json: what the phone app and the console show while training runs."""

    def __init__(self, path: Path | None):
        self.path = path
        self.started = time.time()

    def set(self, state: str, stage: str, progress: float, message: str = "", **extra) -> None:
        if self.path is None:
            return
        body = {"state": state, "stage": stage, "progress": round(progress, 3), "message": message,
                "started": self.started, "updated": time.time(), **extra}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


@dataclass
class Recording:
    sid: str
    kind: str
    t0: float
    t1: float
    events: list[dict]
    frames: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)  # линия -> (t, A [n, 64])


def read_events(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def load_recording(store: Store, info) -> Recording:
    """CSI of every board recorded in the session, placed and picked exactly as the live server does (FrameFeed)."""
    d = store.session_dir(info.id)
    path = d / "csi_all.bin"
    if not path.exists() or path.stat().st_size == 0:
        path = d / "csi.bin"
    feed = FrameFeed()
    acc: dict[str, tuple[list, list]] = {}
    for recv_t, buf in read_csi_bin(path) if path.exists() else []:
        try:
            p = parse_packet(buf, None, verify=False)  # подпись проверена при приёме; ключ мог смениться после
        except ProtocolError:
            continue
        if p.type not in CSI_TYPES or not p.frames:
            continue
        for src, ft, _kind, amp, _ in feed.frames(p, recv_t):
            ts, amps = acc.setdefault(link_id(p.device_id, src), ([], []))
            ts.append(ft)
            amps.append(amp)
    rec = Recording(info.id, info.kind, info.started, info.stopped, read_events(d / "events.jsonl"))
    for lk, (ts, amps) in acc.items():
        t = np.asarray(ts)
        o = np.argsort(t, kind="stable")
        rec.frames[lk] = (t[o], np.asarray(amps, dtype=np.float32)[o])
    return rec


def window_ends(rec: Recording) -> np.ndarray:
    return np.arange(rec.t0 + WIN_S + 1.0, rec.t1 - 0.5, STEP_S)


def recording_stats(rec: Recording, links: list[str], ends: np.ndarray) -> dict[str, list]:
    out = {}
    for lk in links:
        fr = rec.frames.get(lk)
        out[lk] = [None if fr is None else link_stats(fr[0], fr[1], te - WIN_S, te) for te in ends]
    return out


def recording_features(rec: Recording, links: list[str], ends: np.ndarray,
                       quiet: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """X [windows, links, NF]: the same features as LiveInfer computes live, quiet levels in time order.

    Until a link has its own history (FLOOR_MIN_N windows) the quiet level comes from the windows of this
    recording when nobody was home (`quiet` mask, the "leave the flat" steps) — live, the server has the past
    hour for that; without such windows — from the start of the recording."""
    stats = recording_stats(rec, links, ends)
    warm = warm_floors(stats)
    if quiet is not None and quiet.sum() >= 30:
        for lk in links:
            rows = [s for s, q in zip(stats[lk], quiet) if q and s is not None]
            if len(rows) >= 30:
                warm[lk] = np.percentile(np.stack(rows), FLOOR_Q, axis=0)
    floors = LinkFloors(warm)
    X = np.zeros((len(ends), len(links), NF))
    for i, te in enumerate(ends):
        for j, lk in enumerate(links):
            X[i, j] = floors.features(lk, float(te), stats[lk][i])
    return X, warm


class Labeler:
    """Window labels: allowed zone set (mask over zones + EMPTY; all zeros = unknown) and moving 1 / 0 / -1."""

    def __init__(self, plan: dict):
        self.grid = Grid(plan)
        self.zones = list(self.grid.labels) + [EMPTY]
        self.E = len(self.zones) - 1
        self.plan = plan

    def label_at(self, xy: np.ndarray) -> int | None:
        g = self.grid
        d = np.hypot(*(g.xy - xy).T)
        i = int(np.argmin(d))
        return int(g.label_idx[i]) if d[i] <= NEAR_PLAN_M else None

    def _empty(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        return np.zeros((n, len(self.zones))), np.full(n, -1.0)

    def walk(self, rec: Recording, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Z, M = self._empty(len(ends))
        traj = np.asarray(self.plan.get("trajectories", {}).get(rec.sid) or [], float).reshape(-1, 3)
        steps = sorted((e for e in rec.events if e.get("kind") == "step" and isinstance(e.get("t"), (int, float))),
                       key=lambda e: e["t"])
        empty_iv = []
        for k, e in enumerate(steps):
            end = steps[k + 1]["t"] if k + 1 < len(steps) else rec.t1
            if e.get("activity") == "empty":
                empty_iv.append((e["t"] + EMPTY_GRACE_S, end))
        for i, te in enumerate(ends):
            in_empty = any(a <= te - WIN_S and te <= b for a, b in empty_iv)
            lab = None
            if len(traj):
                tp = te - POS_LAG_S
                j = int(np.argmin(np.abs(traj[:, 0] - tp)))
                if abs(traj[j, 0] - tp) <= POS_MAX_GAP_S:
                    lab = self.label_at(traj[j, 1:])
                    seg = traj[(traj[:, 0] >= te - 2.0) & (traj[:, 0] <= te)]
                    if lab is not None and len(seg) >= 5 and seg[-1, 0] - seg[0, 0] >= 1.5:
                        v = np.hypot(*np.diff(seg[:, 1:], axis=0).T).sum() / (seg[-1, 0] - seg[0, 0])
                        M[i] = 1.0 if v > MOVE_YES else 0.0 if v < MOVE_NO else -1.0
            if in_empty and lab is None:
                Z[i, self.E], M[i] = 1.0, 0.0  # шаг «выйдите из квартиры», телефон вне плана
            elif lab is not None:
                Z[i, lab] = 1.0
        return Z, M

    def empty_session(self, rec: Recording, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Z, M = self._empty(len(ends))
        ok = ends - WIN_S >= rec.t0 + EMPTY_GRACE_S
        Z[ok, self.E], M[ok] = 1.0, 0.0
        return Z, M

    def occupancy(self, rec: Recording, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Labels typed on the /people or /scenario pages: zone counts (and how many move) from that moment on."""
        Z, M = self._empty(len(ends))
        evs = sorted((e for e in rec.events if e.get("kind") == "occupancy" and isinstance(e.get("t"), (int, float))),
                     key=lambda e: e["t"])
        if not evs:
            return Z, M
        ts = np.array([e["t"] for e in evs])
        for i, te in enumerate(ends):
            k = int(np.searchsorted(ts, te, side="right")) - 1
            if k < 0 or ts[k] > te - WIN_S:  # до первой метки или смена метки внутри окна
                continue
            zones = evs[k].get("zones") or {}
            total = sum(int(z.get("count", 0)) for z in zones.values())
            if total == 0:
                Z[i, self.E], M[i] = 1.0, 0.0
                continue
            idx = [self.zones.index(n) for n, z in zones.items() if int(z.get("count", 0)) > 0 and n in self.zones[:-1]]
            Z[i, idx if idx else list(range(self.E))] = 1.0
            mv = [z.get("moving") for z in zones.values() if int(z.get("count", 0)) > 0]
            acts = " ".join(str(z.get("activity", "")) for z in zones.values()).lower()
            if any(isinstance(v, int) and v > 0 for v in mv) or "ход" in acts:
                M[i] = 1.0
            elif mv and all(isinstance(v, int) and v == 0 for v in mv):
                M[i] = 0.0
        return Z, M

    def labels(self, rec: Recording, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if rec.kind == "walk":
            return self.walk(rec, ends)
        if rec.kind == "empty":
            return self.empty_session(rec, ends)
        return self.occupancy(rec, ends)


@dataclass
class FitConfig:
    hidden: tuple[int, int] = (64, 32)
    epochs: int = 80
    batch: int = 128
    lr: float = 3e-3
    weight_decay: float = 1e-4
    link_drop: float = 0.15   # линия «умерла» (плата перезагружается, сосед вне эфира) — модель не должна теряться
    noise: float = 0.05
    move_w: float = 0.5


def sample_weights(Z: np.ndarray) -> np.ndarray:
    """Balanced weights for windows with one exact zone; zone sets (labels by hand) count half."""
    single = Z.sum(axis=1) == 1
    w = np.where(Z.sum(axis=1) > 0, 0.5, 0.0)
    if single.any():
        cls = Z[single].argmax(axis=1)
        counts = np.bincount(cls, minlength=Z.shape[1]).astype(float)
        k = int((counts > 0).sum())
        cw = np.where(counts > 0, single.sum() / (k * np.maximum(counts, 1)), 0.0)
        w[single] = np.clip(cw[cls], 0.2, 5.0)
    return w


def fit_net(X: np.ndarray, Z: np.ndarray, M: np.ndarray, mu: np.ndarray, sd: np.ndarray, seed: int,
            cfg: FitConfig) -> dict:
    """One MLP (numpy, AdamW). X raw features [N, L, NF]; Z allowed-zone masks [N, K]; M moving 1/0/-1."""
    rng = np.random.default_rng(seed)
    N, L, _ = X.shape
    D, K = L * NF, Z.shape[1]
    h1, h2 = cfg.hidden
    net = {"W1": rng.normal(0, math.sqrt(2 / D), (D, h1)), "b1": np.zeros(h1),
           "W2": rng.normal(0, math.sqrt(2 / h1), (h1, h2)), "b2": np.zeros(h2),
           "Wz": rng.normal(0, math.sqrt(1 / h2), (h2, K)), "bz": np.zeros(K),
           "Wm": rng.normal(0, math.sqrt(1 / h2), (h2, 1)), "bm": np.zeros(1)}
    m1 = {k: np.zeros_like(v) for k, v in net.items()}
    m2 = {k: np.zeros_like(v) for k, v in net.items()}
    wz = sample_weights(Z)
    wm = (M >= 0).astype(float)
    use = np.flatnonzero((wz > 0) | (wm > 0))
    step = 0
    for ep in range(cfg.epochs):
        lr = cfg.lr * (0.5 * (1 + math.cos(math.pi * ep / cfg.epochs)))  # косинусное затухание
        rng.shuffle(use)
        for b0 in range(0, len(use), cfg.batch):
            idx = use[b0:b0 + cfg.batch]
            xb = X[idx].copy()
            if cfg.link_drop > 0 and L > 1:
                dead = rng.random((len(idx), L)) < cfg.link_drop
                xb[dead] = 0.0
            Zs = (xb.reshape(len(idx), D) - mu) / sd
            Zs = Zs + rng.normal(0, cfg.noise, Zs.shape)
            a1 = Zs @ net["W1"] + net["b1"]
            r1 = np.maximum(a1, 0)
            a2 = r1 @ net["W2"] + net["b2"]
            r2 = np.maximum(a2, 0)
            lz = r2 @ net["Wz"] + net["bz"]
            lm = (r2 @ net["Wm"] + net["bm"])[:, 0]
            p = softmax(lz)
            T = Z[idx]
            S = np.maximum((p * T).sum(axis=1, keepdims=True), 1e-12)
            wzb, wmb = wz[idx], wm[idx]
            nb = max(float(wzb.sum() + cfg.move_w * wmb.sum()), 1e-9)
            dlz = (p - p * T / S) * wzb[:, None] / nb  # -log P(зона из разрешённого набора)
            dlm = ((sigmoid(lm) - np.clip(M[idx], 0, 1)) * wmb * cfg.move_w / nb)[:, None]
            g = {"Wz": r2.T @ dlz, "bz": dlz.sum(0), "Wm": r2.T @ dlm, "bm": dlm.sum(0)}
            d2 = (dlz @ net["Wz"].T + dlm @ net["Wm"].T) * (a2 > 0)
            g["W2"], g["b2"] = r1.T @ d2, d2.sum(0)
            d1 = (d2 @ net["W2"].T) * (a1 > 0)
            g["W1"], g["b1"] = Zs.T @ d1, d1.sum(0)
            step += 1
            for k in net:
                m1[k] = 0.9 * m1[k] + 0.1 * g[k]
                m2[k] = 0.999 * m2[k] + 0.001 * g[k] ** 2
                upd = (m1[k] / (1 - 0.9 ** step)) / (np.sqrt(m2[k] / (1 - 0.999 ** step)) + 1e-8)
                net[k] = net[k] - lr * (upd + (cfg.weight_decay * net[k] if k.startswith("W") else 0.0))
    return net


def _fit_job(args) -> dict:
    return fit_net(*args)


def predict(nets: list[dict], X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    Zs = (X.reshape(len(X), -1) - mu) / sd
    pz, pm = 0.0, 0.0
    for net in nets:
        lz, lm, _ = net_forward(net, Zs)
        pz, pm = pz + softmax(lz), pm + sigmoid(lm)
    return pz / len(nets), pm / len(nets)


def standardisation(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    F = X.reshape(len(X), -1)
    return F.mean(axis=0), np.maximum(F.std(axis=0), 1e-3)


def metrics(Z: np.ndarray, M: np.ndarray, pz: np.ndarray, pm: np.ndarray, zones: list[str]) -> dict:
    """Held-out quality: zone recall per class (exact labels), nobody-home, moving (balanced accuracies)."""
    out: dict = {"windows": int((Z.sum(axis=1) > 0).sum())}
    E = len(zones) - 1
    single = Z.sum(axis=1) == 1
    if single.any():
        y, yh = Z[single].argmax(axis=1), pz[single].argmax(axis=1)
        per = {zones[k]: {"n": int((y == k).sum()), "recall": round(float((yh[y == k] == k).mean()), 3)}
               for k in range(len(zones)) if (y == k).sum() >= 5}
        out["zones"] = per
        rooms = [v["recall"] for z, v in per.items() if z != EMPTY]
        out["zone_bal_acc"] = round(float(np.mean(rooms)), 3) if rooms else None
        out["chance"] = round(1.0 / max(1, len(rooms)), 3) if rooms else None
        out["confusion"] = {zones[k]: {zones[j]: int(((y == k) & (yh == j)).sum()) for j in range(len(zones))
                                       if ((y == k) & (yh == j)).any()} for k in range(len(zones)) if (y == k).any()}
    labelled = Z.sum(axis=1) > 0
    if labelled.any():
        out["in_set"] = round(float(Z[labelled, pz[labelled].argmax(axis=1)].mean()), 3)
    empty, present = Z[:, E] == 1, labelled & (Z[:, E] == 0)
    if empty.sum() >= 5 and present.sum() >= 5:
        pe = pz[:, E]
        out["nobody_home_bal_acc"] = round(0.5 * float((pe[empty] > 0.5).mean() + (pe[present] <= 0.5).mean()), 3)
    yes, no = M == 1, M == 0
    if yes.sum() >= 5 and no.sum() >= 5:
        out["moving_bal_acc"] = round(0.5 * float((pm[yes] > 0.5).mean() + (pm[no] <= 0.5).mean()), 3)
    return out


def cv_folds(rec_idx: np.ndarray, ends: np.ndarray, k: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Contiguous time blocks per recording; train windows closer than WIN_S to the held-out block are dropped."""
    fold = np.zeros(len(ends), int)
    for r in np.unique(rec_idx):
        ii = np.flatnonzero(rec_idx == r)
        fold[ii] = np.minimum((np.arange(len(ii)) * k) // max(len(ii), 1), k - 1)
    out = []
    for f in range(k):
        test = fold == f
        train = ~test
        for r in np.unique(rec_idx[test]):
            tt = ends[test & (rec_idx == r)]
            near = (rec_idx == r) & (ends >= tt.min() - WIN_S) & (ends <= tt.max() + WIN_S)
            train &= ~near
        out.append((train, test))
    return out


def alive_share(X: np.ndarray) -> np.ndarray:
    return X[:, :, -1].mean(axis=0) if len(X) else np.zeros(X.shape[1])


def pick_sessions(store: Store, plan: dict, explicit: list[str] | None) -> list:
    done = {s.id: s for s in store.list_sessions() if s.stopped is not None}
    if explicit:
        missing = [i for i in explicit if i not in done]
        if missing:
            raise SystemExit(f"нет таких законченных записей: {', '.join(missing)}")
        return [done[i] for i in explicit]
    traj = plan.get("trajectories") or {}
    walks = [s for s in done.values() if s.kind == "walk" and len(traj.get(s.id) or []) >= 100]
    if not walks:
        raise SystemExit("нет записи обхода: пройдите сценарий в приложении и нажмите «Сохранить» "
                         "(нужны отмеченные платы и хотя бы одна комната)")
    walks.sort(key=lambda s: s.started_wall, reverse=True)
    empties = [s for s in done.values() if s.kind == "empty"]
    return walks + sorted(empties, key=lambda s: s.started_wall, reverse=True)


def say(msg: str) -> None:
    print(msg, flush=True)


def run(a, status: Status) -> dict:
    data = Path(a.data)
    store = Store(data / "wsn.db", data / "sessions", readonly=True)  # рядом работает сервер: только чтение
    plan = build_plan(store)
    if not plan["rooms"]:
        raise SystemExit("на плане нет комнат: отметьте углы комнат в приложении и сохраните запись")
    lab = Labeler(plan)
    cands = pick_sessions(store, plan, a.sessions.split(",") if a.sessions else None)
    say(f"Зоны плана: {', '.join(lab.zones[:-1])}")
    recs, links, quiet_floors = [], None, None
    live = set(a.live_links.split(",")) - {""} if a.live_links else None  # линии в эфире сейчас (train.sh)
    for n, info in enumerate(cands, 1):
        status.set("running", "load", 0.4 * n / len(cands), f"чтение записи {info.id}")
        say(f"Запись {info.id} ({info.kind}, {(info.stopped - info.started) / 60:.1f} мин): чтение CSI…")
        rec = load_recording(store, info)
        ends = window_ends(rec)
        if len(ends) < 10:
            say("  слишком короткая — пропуск")
            continue
        if links is None:  # самая новая подходящая запись задаёт набор линий
            share = {lk: float(np.mean([s is not None for s in st]))
                     for lk, st in recording_stats(rec, sorted(rec.frames), ends).items()
                     if not lk.endswith("|")}  # кадры без передатчика (старая прошивка) — не линия
            cand = sorted(lk for lk, v in share.items() if v >= LINK_MIN_SHARE)
            if live is not None:
                same = len(set(cand) & live) / max(1, len(live))
                if same < SAME_LINKS:
                    say(f"  записана на других платах или линиях (совпадает {same:.0%} нынешних) — пропуск")
                    continue
            if not cand:
                say("  в записи нет живых линий — пропуск")
                continue
            links = cand
            say(f"  линий: {len(links)} ({', '.join(links)})")
        Z, M = lab.labels(rec, ends)
        X, warm = recording_features(rec, links, ends, quiet=Z[:, lab.E] == 1)
        if quiet_floors is None:  # тихий уровень новой записи: с него сервер начнёт, пока не накопит свой
            quiet_floors = {lk: [round(float(v), 6) for v in fl] for lk, fl in warm.items()}
        alive = alive_share(X)
        if recs and float((alive >= LINK_MIN_SHARE).mean()) < SAME_LINKS:
            say(f"  другие линии радио (живы {int((alive >= LINK_MIN_SHARE).sum())} из {len(links)}) — пропуск")
            continue
        say(f"  окон {len(ends)}: с зоной {int((Z.sum(1) == 1).sum())}, «пусто» {int(Z[:, lab.E].sum())}, "
            f"движение да/нет {int((M == 1).sum())}/{int((M == 0).sum())}")
        recs.append((info.id, ends, X, Z, M))
    if not recs:
        raise SystemExit("нет записи обхода на нынешних платах: пройдите сценарий в приложении и нажмите «Сохранить»"
                         if live is not None else "нет подходящих записей")
    rec_idx = np.concatenate([np.full(len(r[1]), i) for i, r in enumerate(recs)])
    ends = np.concatenate([r[1] for r in recs])
    X = np.concatenate([r[2] for r in recs])
    Z = np.concatenate([r[3] for r in recs])
    M = np.concatenate([r[4] for r in recs])
    keep = (Z.sum(axis=1) > 0) | (M >= 0)
    rec_idx, ends, X, Z, M = rec_idx[keep], ends[keep], X[keep], Z[keep], M[keep]
    room_windows = int(((Z.sum(axis=1) == 1) & (Z[:, lab.E] == 0)).sum())
    if not a.sessions and room_windows < MIN_WINDOWS // 2:  # одни «пусто» — комнатам не научить
        raise SystemExit("в записях почти нет окон с известной комнатой: пройдите сценарий обхода в приложении")
    if len(X) < MIN_WINDOWS:
        raise SystemExit(f"мало размеченных окон ({len(X)} < {MIN_WINDOWS}): запишите обход подольше")
    cfg = FitConfig(epochs=a.epochs)
    jobs = max(1, a.jobs)
    t0 = time.time()
    # честная проверка: каждый блок времени предсказывает сеть, которая его не видела
    folds = cv_folds(rec_idx, ends, a.folds)
    pz, pm = np.zeros(Z.shape), np.zeros(len(Z))
    status.set("running", "check", 0.45, f"проверка на отложенных блоках ({a.folds})")
    say(f"Проверка: {a.folds} блоков по времени, {jobs} процессов…")
    fold_jobs = []
    for f, (tr, te) in enumerate(folds):
        mu, sd = standardisation(X[tr])
        fold_jobs.append((X[tr], Z[tr], M[tr], mu, sd, 1000 + f, cfg))
    with mp.get_context("fork").Pool(jobs) as pool:
        fold_nets = pool.map(_fit_job, fold_jobs)
        for (tr, te), net, job in zip(folds, fold_nets, fold_jobs):
            pz[te], pm[te] = predict([net], X[te], job[3], job[4])
        cv = metrics(Z, M, pz, pm, lab.zones)
        say(f"  {report_line(cv)}")
        status.set("running", "fit", 0.7, f"обучение ансамбля из {a.nets} сетей")
        say(f"Обучение {a.nets} сетей на всех окнах ({len(X)})…")
        mu, sd = standardisation(X)
        nets = pool.map(_fit_job, [(X, Z, M, mu, sd, s, cfg) for s in range(a.nets)])
    fit_pz, fit_pm = predict(nets, X, mu, sd)
    train_m = metrics(Z, M, fit_pz, fit_pm, lab.zones)
    meta = {
        "version": int(time.time()), "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "links": links, "zones": lab.zones, "features": list(FEATURES), "stats": list(STATS), "win_s": WIN_S,
        "floors": quiet_floors,
        "sessions": [r[0] for r in recs], "windows": int(len(X)), "seconds": round(time.time() - t0, 1),
        "metrics": {"heldout": cv, "train": train_m}, "config": cfg.__dict__ | {"hidden": list(cfg.hidden)},
    }
    return {"meta": meta, "mu": mu, "sd": sd, "nets": nets}


def report_line(m: dict) -> str:
    parts = []
    if m.get("zone_bal_acc") is not None:
        parts.append(f"зона {m['zone_bal_acc']:.0%} (случайно {m['chance']:.0%})")
    if m.get("nobody_home_bal_acc") is not None:
        parts.append(f"«никого нет» {m['nobody_home_bal_acc']:.0%}")
    if m.get("moving_bal_acc") is not None:
        parts.append(f"движение {m['moving_bal_acc']:.0%}")
    if m.get("in_set") is not None:
        parts.append(f"ответ в разметке {m['in_set']:.0%}")
    return "; ".join(parts) or "нет меток для оценки"


def write_report(path: Path, meta: dict) -> str:
    cv = meta["metrics"]["heldout"]
    lines = [f"Модель квартиры v{meta['version']} ({meta['created']}), окон {meta['windows']}, "
             f"линий {len(meta['links'])}, записи: {', '.join(meta['sessions'])}",
             f"Честная проверка (блоки времени, которых сеть не видела): {report_line(cv)}"]
    for z, v in (cv.get("zones") or {}).items():
        lines.append(f"  {z}: узнаётся в {v['recall']:.0%} из {v['n']} окон")
    missing = [z for z in meta["zones"][:-1] if z not in (cv.get("zones") or {})]
    if missing:
        lines.append(f"  без примеров в записи (модель их не знает): {', '.join(missing)} — пройдите их в сценарии")
    text = "\n".join(lines)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Обучить модель квартиры по записи обхода")
    ap.add_argument("--data", default=os.environ.get("WSN_DATA", "/data"))
    ap.add_argument("--sessions", default="", help="id записей через запятую (по умолчанию — новый обход и «пусто»)")
    ap.add_argument("--nets", type=int, default=8)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", default="", help="папка модели (по умолчанию <data>/model)")
    ap.add_argument("--dry-run", action="store_true", help="только проверка качества, модель не сохранять")
    ap.add_argument("--live-links", default="", help="линии в эфире сейчас (через запятую): записи на других "
                    "платах пропускаются (train.sh берёт их у сервера)")
    a = ap.parse_args(argv)
    data = Path(a.data)
    (data / "run").mkdir(parents=True, exist_ok=True)
    status = Status(data / "run" / "train_status.json")
    status.set("running", "start", 0.0, "подготовка")
    try:
        res = run(a, status)
    except SystemExit as e:
        status.set("error", "done", 1.0, str(e))
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — в статус для приложения, затем как есть
        status.set("error", "done", 1.0, f"{type(e).__name__}: {e}")
        raise
    meta = res["meta"]
    out = Path(a.out) if a.out else data / "model"
    if a.dry_run:
        say(report_line(meta["metrics"]["heldout"]))
        status.set("done", "done", 1.0, "проверка без сохранения", metrics=meta["metrics"]["heldout"])
        return 0
    if (out / "meta.json").exists():  # прежняя модель остаётся рядом: откат — переименовать папку
        prev = out.with_name(out.name + ".prev")
        if prev.exists():
            for f in prev.iterdir():
                f.unlink()
            prev.rmdir()
        out.rename(prev)
    save_model(out, meta, res["mu"], res["sd"], res["nets"])
    text = write_report(out / "report.txt", meta)
    say(text)
    say(f"Модель сохранена: {out}. Сервер подхватит её в течение 10 секунд.")
    status.set("done", "done", 1.0, "модель обучена", version=meta["version"], metrics=meta["metrics"]["heldout"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
