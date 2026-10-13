"""Physics-based synthetic CSI for the 9 radio links of this apartment.

Model (CSI-speed / Fresnel model from the literature, e.g. CARM MobiCom'15, Fresnel-zone respiration
papers): a person adds a reflected path  Tx -> body -> Rx  to every link. For subcarrier frequency f_k

    H_k(t) = B_k(t) + g(t) * |B̄_k| * exp(-j 2π f_k d(t) / c) * e^{j φ(t)}

  B_k(t)  real recorded CSI of the link (complex I/Q, quiet period) — keeps the real static multipath,
          noise, packet timing and per-frame phase offsets;
  d(t)    reflected path length from the plan geometry (transmitter xy/z, board at the origin, person xy/z);
  g(t)    reflection strength: G0 * (L / d)^2 (L = direct Tx–Rx distance) — strongest near the link
          (inside the first Fresnel zones), weak far away;
  φ(t)    common phase offset of the recorded frame (CFO/SFO), so the synthetic path rotates with it.

Activities (label `act`): 0 empty (background only), 1 still+breathing (chest 5–12 mm, 8–24 /min),
2 fine motion (limb bursts 5–30 cm in 0.3–1.5 s, plus breathing), 3 walking (0.5–1.3 m/s inside a zone,
torso + leg swing). Label `zone`: zone of the added person (-1 for empty).
Output: 2 s windows -> per-link features identical to build_multilink.py (var, peak), + labels.

usage: python synth_physics.py --data /data --n 20000 --out /data/ml/synth_v1.npz
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from wsn.geometry import point_in_polygon
from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

C = 299_792_458.0
F0 = 2.412e9  # канал 1
FK = F0 + (np.arange(64) - 32) * 312.5e3  # поднесущие -32..31
REORDER = np.r_[32:64, 0:32]
LINKS = ["02:00:00:00:a1:18", "98:b3:ef:02:da:28", "98:b3:ef:02:da:14", "02:00:00:00:a1:2c",
         "02:00:00:00:f6:7c", "02:00:00:00:e5:57", "d8:d6:68:b3:4c:23", "d8:d6:68:b3:7a:6f",
         "02:00:00:00:a7:e6"]
ZONES = ["спальня", "зал", "кухня", "коридор", "санузел", "комната"]
BOARD = np.array([0.0, 0.0, 0.8])
QUIET = [("20261013-050259-94cd", "05:06:15", "05:11:00"), ("20261013-042804-6eba", "04:54:10", "04:58:00")]
rng = np.random.default_rng(1)


def ltf_complex(data: bytes) -> np.ndarray:
    iq = np.frombuffer(data, dtype=np.int8, count=128, offset=128).astype(np.float32).reshape(64, 2)
    return (iq[:, 1] + 1j * iq[:, 0])[REORDER]  # (imag, real) в буфере ESP


def load_quiet(data: Path, keys: KeyStore):
    """Complex CSI per link from quiet periods: {mac: [(t[], H[T,64]) per period]}."""
    import time
    out = defaultdict(list)
    for sid, hms0, hms1 in QUIET:
        sdir = data / "sessions" / sid
        meta = json.loads((sdir / "meta.json").read_text())
        day = time.strftime("%Y-%m-%d ", time.localtime(meta["started_wall"]))
        def mono(h):
            w = time.mktime(time.strptime(day + h, "%Y-%m-%d %H:%M:%S"))
            return meta["started"] + (w - meta["started_wall"])
        t0, t1 = mono(hms0), mono(hms1)
        per = defaultdict(list)
        for recv_t, buf in read_csi_bin(sdir / "csi.bin"):
            if not t0 <= recv_t <= t1:
                continue
            try:
                p = parse_packet(buf, keys.get(peek_device_id(buf)))
            except (ProtocolError, TypeError):
                continue
            if p.type != TYPE_CSI_SRC:
                continue
            last = p.frames[-1].t_us
            for f in p.frames:
                if len(f.data) >= 256 and f.src in LINKS:
                    per[f.src].append((recv_t - (last - f.t_us) / 1e6, ltf_complex(f.data)))
        for src, v in per.items():
            if len(v) > 200:
                v.sort(key=lambda x: x[0])
                out[src].append((np.array([t for t, _ in v]), np.stack([h for _, h in v])))
    return out


def geometry(data: Path, plan: str = "plan_overrides.json"):
    ov = json.loads((data / plan).read_text(encoding="utf-8"))
    plan_router = None
    tx = {}
    for o in ov["objects"]:
        if o.get("mac") in LINKS:
            z = o.get("z", 0.0) + (o.get("size") or [0, 0, 0.3])[2] * 0.8
            tx[o["mac"]] = np.array([o["xy"][0], o["xy"][1], z])
    # роутер B: точка «роутер» плана (на тумбе ТВ), задаётся обходом, а не правками
    tx.setdefault("02:00:00:00:a1:18", np.array([2.77, 0.0, 0.7]))
    zones = {}
    if "кухня" in ov.get("zones", {}):
        zones["кухня"] = ov["zones"]["кухня"]
    for n in ZONES:
        if n in ov.get("rooms", {}):
            zones[n] = ov["rooms"][n]
    return tx, zones


def zone_of(xy, zones):
    for n in ["кухня"] + [z for z in ZONES if z != "кухня"]:
        if n in zones and point_in_polygon(xy, zones[n]):
            return n
    return None


def random_point(zones, name):
    poly = np.array(zones[name])
    lo, hi = poly.min(0), poly.max(0)
    for _ in range(1000):
        p = rng.uniform(lo, hi)
        if zone_of(p, zones) == name:
            return p
    return poly.mean(0)


def trajectory(act: int, zones, zone: str, t: np.ndarray):
    """Person torso position [T,3] and extra limb path offsets [T] (metres) for the activity."""
    T = len(t)
    tt = t - t[0]
    br = rng.uniform(8, 24) / 60 * (1 + 0.05 * np.sin(2 * np.pi * tt / rng.uniform(20, 60)))  # вариабельность ритма
    chest = rng.uniform(0.005, 0.012) * np.sin(2 * np.pi * np.cumsum(br * np.gradient(tt)))
    if act == 3:  # ходьба по отрезкам между случайными точками зоны
        speed = rng.uniform(0.5, 1.3)
        pts = [random_point(zones, zone)]
        pos = np.zeros((T, 2))
        cur, tgt = pts[0], random_point(zones, zone)
        for i in range(T):
            dt = tt[i] - tt[i - 1] if i else 0.0
            v = tgt - cur
            dist = np.linalg.norm(v)
            if dist < 0.2:
                tgt = random_point(zones, zone)
            else:
                cur = cur + v / dist * min(dist, speed * dt)
            pos[i] = cur
        z = np.full(T, 1.1) + 0.03 * np.sin(2 * np.pi * 1.8 * tt)  # покачивание корпуса в такт шагам
        limb = 0.25 * np.sin(2 * np.pi * rng.uniform(0.8, 1.0) * tt)  # размах ног/рук
        return np.c_[pos, z], limb, 0.5
    p = random_point(zones, zone)
    h = rng.choice([1.1, 0.6, 0.45])  # стоит / сидит / лежит
    pos = np.tile([p[0], p[1], h], (T, 1)).astype(float)
    limb = np.zeros(T)
    if act == 2:  # мелкая моторика: 1–3 всплеска
        for _ in range(rng.integers(1, 4)):
            c, w = rng.uniform(tt[0], tt[-1]), rng.uniform(0.3, 1.5)
            amp = rng.uniform(0.05, 0.3)
            env = np.exp(-0.5 * ((tt - c) / (w / 2.5)) ** 2)
            limb += amp * env * np.sin(2 * np.pi * rng.uniform(0.5, 3) * tt + rng.uniform(0, 6.28))
    return pos, limb + chest, 0.3 if act == 2 else 0.0


def synth_link(B: np.ndarray, t: np.ndarray, tx: np.ndarray, pos: np.ndarray, limb: np.ndarray,
               limb_gain: float, chest_only: bool, G0: float) -> np.ndarray:
    L = np.linalg.norm(tx - BOARD)
    d = np.linalg.norm(pos - tx, axis=1) + np.linalg.norm(pos - BOARD, axis=1)
    g = G0 * (L / d) ** 2
    mag = np.abs(B).mean(0)  # средняя амплитуда поднесущих
    phi = np.angle((B * np.conj(B.mean(0))).sum(1))  # общий фазовый сдвиг кадра
    path = d if not chest_only else d.mean() + limb  # дыхание: только микросдвиги длины пути
    H = B + (g[:, None] * mag[None]) * np.exp(-2j * np.pi * FK[None] * path[:, None] / C) * np.exp(1j * phi)[:, None]
    if limb_gain > 0 and not chest_only:  # конечности — отдельный слабый путь
        H = H + (limb_gain * g[:, None] * mag[None]) * np.exp(-2j * np.pi * FK[None] * (d + limb)[:, None] / C) \
            * np.exp(1j * phi)[:, None]
    return H


def features(t: np.ndarray, H: np.ndarray, t0: float) -> tuple[float, float]:
    A = np.abs(H)
    live = A.mean(0) > 2
    A = A[:, live] / (A[:, live].mean(1, keepdims=True) + 1e-3)
    def cv(chunk):
        k = np.floor((t - t0) / chunk).astype(int)
        return [float(A[k == i].std(0).mean()) for i in np.unique(k) if (k == i).sum() >= 3]
    v1, v05 = cv(1.0), cv(0.5)
    if not v1:
        return 0.0, 0.0
    return 100 * float(np.mean(v1)), 100 * float(max(v05 or v1))


def gen_samples(n, quiet, tx, zones, gains, acts_p=(0.25, 0.25, 0.2, 0.3), zone_pool=None, log=False):
    zl = zone_pool or [z for z in ZONES if z in zones]
    X, act_y, zone_y = [], [], []
    while len(X) < n:
        act = int(rng.choice(4, p=list(acts_p)))
        zone = zl[rng.integers(len(zl))]
        row = np.zeros((len(LINKS), 2), np.float32)
        per_idx = rng.integers(len(QUIET))
        state = None
        for li, mac in enumerate(LINKS):
            if mac not in quiet or len(quiet[mac]) <= per_idx or mac not in tx:
                continue
            tq, Hq = quiet[mac][per_idx]
            if state is None:
                t0 = rng.uniform(tq[0], tq[-1] - 2.0)
            m = (tq >= t0) & (tq < t0 + 2.0)
            if m.sum() < 6:
                continue
            t, B = tq[m], Hq[m]
            if state is None:  # одна траектория человека на все линии
                grid = np.arange(t0, t0 + 2.0, 0.005)
                pos_g, limb_g, lg = trajectory(act, zones, zone, grid) if act else (None, None, 0)
                state = (grid, pos_g, limb_g, lg)
            grid, pos_g, limb_g, lg = state
            if act:
                pos = np.stack([np.interp(t, grid, pos_g[:, j]) for j in range(3)], 1)
                limb = np.interp(t, grid, limb_g)
                H = synth_link(B, t, tx[mac], pos, limb, lg, chest_only=(act == 1),
                               G0=gains[mac] * rng.uniform(0.6, 1.4))
            else:
                H = B
            row[li] = features(t, H, t0)
        X.append(row.reshape(-1)); act_y.append(act); zone_y.append(ZONES.index(zone) if act else -1)
        if log and len(X) % 2000 == 0:
            print("generated", len(X), flush=True)
    return np.stack(X), np.array(act_y), np.array(zone_y)


def calibrate(quiet, tx, zones, target: dict, rounds=3, n=240):
    """Per-link reflection strength so that synthetic walking in зал/кухня changes every link
    like the real walk 04:51:40–04:54:00 did (median var ratio walking/still)."""
    gains = {m: 0.25 for m in LINKS}
    for r in range(rounds):
        Xe, _, _ = gen_samples(n // 2, quiet, tx, zones, gains, acts_p=(1, 0, 0, 0))
        Xw, _, _ = gen_samples(n, quiet, tx, zones, gains, acts_p=(0, 0, 0, 1), zone_pool=["зал", "зал", "кухня"])
        msg = []
        for li, mac in enumerate(LINKS):
            if mac not in tx or mac not in target:
                continue
            e = np.median(Xe.reshape(-1, len(LINKS), 2)[:, li, 0]); w = np.median(Xw.reshape(-1, len(LINKS), 2)[:, li, 0])
            syn, tgt = w / max(e, 1e-6), max(target[mac], 1.0)
            k = (tgt - 1 + 0.02) / max(syn - 1, 0.02)  # отклик ~ линеен по силе отражения
            gains[mac] = float(np.clip(gains[mac] * np.clip(k, 0.2, 5), 0.003, 2.0))
            msg.append(f"{mac[-5:]}: syn {syn:.2f} -> target {tgt:.2f}, g={gains[mac]:.3f}")
        print(f"calibration round {r + 1}: " + "; ".join(msg), flush=True)
    return gains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--target", default="/data/ml/real_walk_ratio.json")
    ap.add_argument("--out", default="/data/ml/synth_v1.npz")
    ap.add_argument("--plan", default="plan_overrides.json")
    a = ap.parse_args()
    data = Path(a.data)
    quiet = load_quiet(data, KeyStore(data / "device_keys.json"))
    tx, zones = geometry(data, a.plan)
    print("links with backgrounds:", {k[-5:]: sum(len(x[0]) for x in v) for k, v in quiet.items()})
    print("transmitters:", {k[-5:]: np.round(v, 2).tolist() for k, v in tx.items()})
    gains = calibrate(quiet, tx, zones, json.loads(Path(a.target).read_text()))
    X, act, zone = gen_samples(a.n, quiet, tx, zones, gains, log=True)
    np.savez_compressed(a.out, X=X, act=act, zone=zone, links=np.array(LINKS), zones=np.array(ZONES),
                        gains=json.dumps(gains))
    print("saved", a.out)


if __name__ == "__main__":
    main()
