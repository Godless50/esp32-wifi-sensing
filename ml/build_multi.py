"""Multi-person and body-pose training data, synthesised from the data we already have.

1. real_mix — superposition of real recordings (scripted sessions). The change of the channel caused by one moving
   person is, to first order, additive in amplitude:  |H0 + d1 + d2| ~ |H0| + Re(d1 e^-jφ0) + Re(d2 e^-jφ0).
   A window with the subject standing still (base) plus the perturbations (AGC-normalised amplitude minus its window
   mean) of 1-3 windows where the subject moved at other spots of the same session gives a window with 1-3 moving
   people plus the still one of the base (other people of the household were asleep in the bedroom and the room
   during the scripted sessions, so n_all is unknown there). Shadowing of one person by another is not modelled.
2. physics — the reflection model of synth_physics.py with articulated bodies (chest, belly, head, upper arms,
   forearms, thighs, shins as point scatterers) on quiet recorded backgrounds of board 1: 0-3 people anywhere on the
   plan (up to 5, the household size); postures стоит / сидит / лежит / идёт / переход; arms and legs bending.

Labels (-1 = unknown):
  n_move    moving people (fine motion, bending, walking, changing posture), 0..5
  n_all     people present, still ones included
  cmove[6], call[6]   the same per zone (ZONES order)
  pose      single-person windows: 0 стоит, 1 сидит, 2 лежит, 3 идёт, 4 переход
  arms      single person: 0 опущены, 1 согнуты в локтях, 2 подняты;  legs: 0 прямые, 1 согнуты в коленях
  angles[4] single-person physics windows: interior angle (180 = straight) elbow L, elbow R, knee L, knee R, degrees

usage: PYTHONPATH=server .venv-rocm/bin/python ml/build_multi.py --data data --out data/ml/multi_v1.npz
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path

import numpy as np

import synth_physics as sp
from build_rich import LINKS, WIN_S, ZONES, link_csi, load_occupancy, script_label
from build_seq import NC, NT, link_seq
from wsn.keys import KeyStore

SCRIPTED = ["20261013-053501-ee64", "20261013-065019-45cd"]
POSE = ["стоит", "сидит", "лежит", "идёт", "переход"]
ARMS = ["опущены", "согнуты", "подняты"]
LEGS = ["прямые", "согнуты"]


# ---------------------------------------------------------------- 1. наложение реальных записей
def _norm(t, H, t0):
    i0, i1 = np.searchsorted(t, [t0, t0 + WIN_S])
    if i1 - i0 < 12:
        return None
    tt, A = t[i0:i1], np.abs(H[i0:i1]).astype(np.float32)
    live = A.mean(0) > 2
    if live.sum() < 8:
        return None
    g = A[:, live].mean(1) + 1e-3
    return tt, A / g[:, None], g, live


def _mix(base, movers, t0b):
    tt, N, g, live = base
    out = N.copy()
    for m, t0m in movers:
        if m is None:
            continue
        tm, Nm, _, livem = m
        cols = np.nonzero(live & livem)[0]
        P = Nm[:, cols] - Nm[:, cols].mean(0)
        tau = tt - t0b + t0m
        out[:, cols] += np.stack([np.interp(tau, tm, P[:, j]) for j in range(len(cols))], 1)
    return link_seq(tt, np.clip(out * g[:, None], 0, None).astype(np.complex64), t0b)


def real_mix(data: Path, keys: KeyStore, n_per_session: int, rng) -> dict:
    rows = []
    for sid in SCRIPTED:
        sdir = data / "sessions" / sid
        csi = link_csi(sdir / "csi.bin", keys)
        occ = load_occupancy(sdir)
        lo, hi = min(v[0][0] for v in csi.values()), max(v[0][-1] for v in csi.values())
        still, moving = [], []
        for t0 in np.arange(lo, hi - WIN_S, 1.0):
            act, zone, _, place = script_label(occ, t0, t0 + WIN_S)
            if zone < 0:
                continue
            (still if act in (0, 1) else moving if act in (2, 3, 4) else []).append((t0, zone, place, act))
        cache = {}

        def norm(mac, t0):
            k = (mac, t0)
            if k not in cache:
                cache[k] = _norm(*csi[mac], t0) if mac in csi else None
            return cache[k]

        def sample(base, movs):
            x = np.zeros((len(LINKS), NT, NC), np.float32)
            for li, mac in enumerate(LINKS):
                b = norm(mac, base[0])
                if b is not None:
                    x[li] = _mix(b, [(norm(mac, m[0]), m[0]) for m in movs], base[0])
            cm, ca = np.zeros(6, np.int8), np.zeros(6, np.int8)
            for m in movs:
                cm[m[1]] += 1
            ca[:] = cm
            if base is not None:
                ca[base[1]] += 1
            return x, cm, ca

        for w in still:  # настоящие окна: один неподвижный
            x, cm, ca = sample(w, [])
            rows.append(dict(x=x, src="real", session=sid, n_move=0, n_all=1, cmove=cm, call=ca,
                             pose=0 if w[3] == 1 else -1, arms=-1, legs=-1))  # «стоит» — поза известна
        for w in moving:  # настоящие окна: один движется (база = само окно, без добавок)
            x = np.zeros((len(LINKS), NT, NC), np.float32)
            for li, mac in enumerate(LINKS):
                if mac in csi:
                    x[li] = link_seq(*csi[mac], w[0])
            cm = np.zeros(6, np.int8); cm[w[1]] = 1
            rows.append(dict(x=x, src="real", session=sid, n_move=1, n_all=1, cmove=cm, call=cm.copy(),
                             pose=-1, arms=-1, legs=-1))
        for _ in range(n_per_session):
            k = int(rng.choice([1, 2, 3, 4], p=[0.3, 0.3, 0.25, 0.15]))
            base = still[rng.integers(len(still))]
            movs, used = [], {base[2]}
            for _ in range(50):
                if len(movs) == k:
                    break
                m = moving[rng.integers(len(moving))]
                if m[2] in used or abs(m[0] - base[0]) < WIN_S:
                    continue
                used.add(m[2])
                movs.append(m)
            if len(movs) < k:
                continue
            x, cm, ca = sample(base, movs)
            rows.append(dict(x=x, src="real_mix", session=sid, n_move=k, n_all=-1, cmove=cm, call=ca,
                             pose=-1, arms=-1, legs=-1))
        print(f"real_mix {sid}: still {len(still)}, moving {len(moving)}, rows so far {len(rows)}", flush=True)
    return rows


# ---------------------------------------------------------------- 2. физическая модель с сочленённым телом
W = np.array([0.55, 0.45, 0.30, 0.12, 0.12, 0.10, 0.10, 0.25, 0.25, 0.15, 0.15])
W = W / W.sum() * 1.3  # как торс + конечности в synth_physics
U_ARM, F_ARM, THIGH, SHIN, TRUNK = 0.30, 0.27, 0.43, 0.42, 0.55


def body_points(hip, psi, pitch, se, el, hf, kf):
    """Scatterers [T, 11, 3]. hip [T,3]; psi heading, pitch trunk (0 upright, pi/2 lying on the back) [T];
    se/el/hf/kf [T,2] (left, right): shoulder elevation, elbow flexion, hip flexion, knee flexion, radians."""
    T = len(hip)
    f = np.stack([np.cos(psi), np.sin(psi), np.zeros(T)], 1)
    left = np.stack([-np.sin(psi), np.cos(psi), np.zeros(T)], 1)
    up = np.tile([0.0, 0.0, 1.0], (T, 1))
    cp, sp_ = np.cos(pitch)[:, None], np.sin(pitch)[:, None]
    d = cp * up + sp_ * f  # таз -> шея
    n = cp * f + sp_ * up  # «перед» тела
    neck = hip + TRUNK * d
    pts = [hip + 0.4 * d, hip + 0.15 * d, neck + 0.2 * d]
    arms, legs = [], []
    for side, s in ((0, 1.0), (1, -1.0)):
        sh = neck + s * 0.18 * left
        a = -np.cos(se[:, side])[:, None] * d + np.sin(se[:, side])[:, None] * n
        p = np.sin(se[:, side])[:, None] * d + np.cos(se[:, side])[:, None] * n
        b = np.cos(el[:, side])[:, None] * a + np.sin(el[:, side])[:, None] * p
        elbow = sh + U_ARM * a
        arms.append((sh + elbow) / 2)
        arms.append(elbow + F_ARM * b / 2)
        hj = hip + s * 0.1 * left
        c = -np.cos(hf[:, side])[:, None] * d + np.sin(hf[:, side])[:, None] * n
        q = np.sin(hf[:, side])[:, None] * d + np.cos(hf[:, side])[:, None] * n
        sv = np.cos(kf[:, side])[:, None] * c - np.sin(kf[:, side])[:, None] * q
        knee = hj + THIGH * c
        legs.append((hj + knee) / 2)
        legs.append(knee + SHIN * sv / 2)
    # порядок как в W: грудь, живот, голова, плечо Л, плечо П, предпл. Л, предпл. П, бедро Л, бедро П, голень Л, голень П
    return np.stack(pts + [arms[0], arms[2], arms[1], arms[3], legs[0], legs[2], legs[1], legs[3]], 1)


POSTURE = {  # высота таза, наклон корпуса, сгиб бедра, сгиб колена (рад)
    0: (0.95, 0.0, 0.0, 0.05),
    1: (0.47, 0.1, np.pi / 2, np.pi / 2),
    2: (0.45, np.pi / 2, 0.05, 0.1),
}


def person(rng, zones, t):
    """One person over the frame grid t: scatterers [T,11,3], labels."""
    T = len(t)
    tt = t - t[0]
    zone = sp.ZONES[rng.integers(len(sp.ZONES))]
    while zone not in zones:
        zone = sp.ZONES[rng.integers(len(sp.ZONES))]
    act = rng.choice(["still", "bend", "walk", "trans"], p=[0.3, 0.3, 0.25, 0.15])
    post = int(rng.choice([0, 1, 2], p=[0.45, 0.35, 0.2]))
    h, pitch, hfl, kfl = POSTURE[post]
    xy = sp.random_point(zones, zone)
    hip = np.tile([xy[0], xy[1], h], (T, 1)).astype(float)
    psi = np.full(T, rng.uniform(0, 2 * np.pi))
    pit = np.full(T, pitch)
    se = np.tile(rng.uniform(0.05, 0.3, 2), (T, 1))
    el = np.tile(rng.uniform(0.05, 0.4, 2), (T, 1))
    hf = np.full((T, 2), hfl)
    kf = np.full((T, 2), kfl)
    if rng.random() < 0.3:  # руки согнуты (держит телефон, скрещены)
        el[:] = rng.uniform(1.2, 2.2, 2)
        se[:] = rng.uniform(0.2, 0.8, 2)
    elif rng.random() < 0.12 and post != 2:  # руки подняты
        se[:] = rng.uniform(1.9, 2.9, 2)
        el[:] = rng.uniform(0.0, 0.5, 2)
    pose = post
    if act == "bend":  # сгибание-разгибание рук или ног
        f = rng.uniform(0.2, 1.0)
        env = (1 - np.cos(2 * np.pi * f * tt + rng.uniform(0, 6.28))) / 2
        if rng.random() < 0.7:
            sides = [0, 1] if rng.random() < 0.4 else [int(rng.integers(2))]
            for s in sides:
                el[:, s] = el[0, s] + rng.uniform(0.7, 2.0) * env
        else:
            s = int(rng.integers(2))
            kf[:, s] = np.clip(kf[0, s] + (rng.uniform(0.6, 1.4) if post != 1 else -rng.uniform(0.6, 1.3)) * env, 0, 2.6)
            if post == 0:
                hf[:, s] = hf[0, s] + 0.6 * env  # стоя: колено вверх (марш на месте)
    elif act == "walk":
        pose, post = 3, 0
        h, pitch, hfl, kfl = POSTURE[0]
        pit[:] = 0.0
        speed = rng.uniform(0.5, 1.3)
        cur, tgt = xy, sp.random_point(zones, zone)
        for i in range(T):
            dt = tt[i] - tt[i - 1] if i else 0.0
            v = tgt - cur
            dist = np.linalg.norm(v)
            if dist < 0.2:
                tgt = sp.random_point(zones, zone)
            else:
                cur = cur + v / dist * min(dist, speed * dt)
                psi[i] = np.arctan2(v[1], v[0])
            hip[i] = [cur[0], cur[1], 0.95 + 0.02 * np.sin(2 * np.pi * 1.8 * tt[i])]
        ph = 2 * np.pi * rng.uniform(0.8, 1.0) * tt
        hf[:, 0], hf[:, 1] = 0.4 * np.sin(ph), -0.4 * np.sin(ph)
        kf[:, 0], kf[:, 1] = 0.5 * np.clip(np.sin(ph + 1.2), 0, None), 0.5 * np.clip(-np.sin(ph + 1.2), 0, None)
        se[:, 0], se[:, 1] = 0.15 - 0.3 * np.sin(ph), 0.15 + 0.3 * np.sin(ph)
        se = np.abs(se)
    elif act == "trans":  # смена позы: стоит <-> сидит, сидит <-> лежит
        pose = 4
        other = {0: 1, 1: int(rng.choice([0, 2])), 2: 1}[post]
        a, b = POSTURE[post], POSTURE[other]
        c, w = rng.uniform(1.0, 3.0), rng.uniform(1.2, 2.5)
        s = np.clip((tt - c + w / 2) / w, 0, 1)
        s = s * s * (3 - 2 * s)
        hip[:, 2] = a[0] + (b[0] - a[0]) * s
        pit[:] = a[1] + (b[1] - a[1]) * s
        hf[:] = (a[2] + (b[2] - a[2]) * s)[:, None]
        kf[:] = (a[3] + (b[3] - a[3]) * s)[:, None]
    pts = body_points(hip, psi, pit, se, el, hf, kf)
    br = rng.uniform(8, 24) / 60  # дыхание: грудь 5-12 мм
    pts[:, 0] += (rng.uniform(0.005, 0.012) * np.sin(2 * np.pi * br * tt))[:, None] * np.array([0, 0, 1.0 if post == 2 else 0])
    if post != 2:
        f = np.stack([np.cos(psi), np.sin(psi), np.zeros(T)], 1)
        pts[:, 0] += (rng.uniform(0.005, 0.012) * np.sin(2 * np.pi * br * tt))[:, None] * f
    pts += rng.normal(0, 0.003, (1, 1, 3)) + np.cumsum(rng.normal(0, 0.0005, (T, 1, 3)), 0)  # покачивание
    moving = act != "still"
    arms = 2 if se.mean() > 1.7 else 1 if el.mean() > 0.8 else 0
    legs = 1 if kf.mean() > 0.8 else 0
    ang = np.degrees(np.pi - np.array([el[:, 0].mean(), el[:, 1].mean(), kf[:, 0].mean(), kf[:, 1].mean()]))
    return pts, dict(zone=sp.ZONES.index(zone), moving=moving, pose=pose, arms=arms, legs=legs, angles=ang)


def _physics_worker(args):
    seed, n, quiet, tx, zones, gains = args
    rng = np.random.default_rng(seed)
    sp.rng = rng  # random_point в synth_physics берёт свой rng
    rows = []
    rx = sp.BOARD
    while len(rows) < n:
        k = int(rng.choice([0, 1, 2, 3, 4, 5], p=[0.1, 0.25, 0.25, 0.18, 0.12, 0.1]))
        per_idx = int(rng.integers(len(sp.QUIET)))
        x = np.zeros((len(LINKS), NT, NC), np.float32)
        t0 = None
        people = None
        for mac in sp.LINKS:
            if mac not in quiet or len(quiet[mac]) <= per_idx or mac not in tx:
                continue
            tq, Hq = quiet[mac][per_idx]
            if t0 is None:
                t0 = rng.uniform(tq[0], tq[-1] - WIN_S)
                grid = np.arange(t0, t0 + WIN_S + 0.01, 0.02)
                people = [person(rng, zones, grid) for _ in range(k)]
            i0, i1 = np.searchsorted(tq, [t0, t0 + WIN_S])
            if i1 - i0 < 12:
                continue
            t, B = tq[i0:i1], Hq[i0:i1]
            H = B.copy()
            mag = np.abs(B).mean(0)
            phi = np.angle((B * np.conj(B.mean(0))).sum(1))
            L = np.linalg.norm(tx[mac] - rx)
            G0 = gains.get(mac, 0.25) * rng.uniform(0.6, 1.4)
            for pts_g, _ in people:
                pts = np.stack([np.stack([np.interp(t, grid, pts_g[:, p, j]) for j in range(3)], 1)
                                for p in range(pts_g.shape[1])], 1)  # [n, 11, 3]
                dd = np.linalg.norm(pts - tx[mac], axis=2) + np.linalg.norm(pts - rx, axis=2)  # [n, 11]
                g = G0 * W[None] * (L / dd) ** 2
                ph = np.exp(-2j * np.pi * sp.FK[None, None] * dd[:, :, None] / sp.C)  # [n, 11, 64]
                H = H + (g[:, :, None] * ph).sum(1) * mag[None] * np.exp(1j * phi)[:, None]
            x[LINKS.index(mac)] = link_seq(t, H, t0)
        if people is None:
            continue
        cm, ca = np.zeros(6, np.int8), np.zeros(6, np.int8)
        for _, lab in people:
            ca[lab["zone"]] += 1
            cm[lab["zone"]] += int(lab["moving"])
        one = people[0][1] if k == 1 else None
        rows.append(dict(x=x, src="physics", session="", n_move=int(cm.sum()), n_all=k, cmove=cm, call=ca,
                         pose=one["pose"] if one else -1, arms=one["arms"] if one else -1,
                         legs=one["legs"] if one else -1,
                         angles=one["angles"] if one else np.full(4, np.nan)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="data/ml/multi_v1.npz")
    ap.add_argument("--mix", type=int, default=4000, help="real_mix samples per scripted session")
    ap.add_argument("--physics", type=int, default=16000)
    ap.add_argument("--jobs", type=int, default=16)
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    rng = np.random.default_rng(7)
    rows = real_mix(data, keys, a.mix, rng)
    quiet = sp.load_quiet(data, keys)
    tx, zones = sp.geometry(data)
    gains = json.loads(str(np.load(data / "ml" / "synth_v1.npz")["gains"]))
    per = a.physics // a.jobs
    with mp.Pool(a.jobs) as pool:
        for part in pool.imap_unordered(_physics_worker, [(100 + i, per, quiet, tx, zones, gains) for i in range(a.jobs)]):
            rows += part
            print("physics part done, rows", len(rows), flush=True)
    get = lambda k, d=-1: np.array([r.get(k, d) for r in rows])
    np.savez_compressed(
        a.out, X=np.stack([r["x"] for r in rows]), src=get("src"), session=get("session"),
        n_move=get("n_move").astype(np.int8), n_all=get("n_all").astype(np.int8),
        cmove=np.stack([r["cmove"] for r in rows]), call=np.stack([r["call"] for r in rows]),
        pose=get("pose").astype(np.int8), arms=get("arms").astype(np.int8), legs=get("legs").astype(np.int8),
        angles=np.stack([r.get("angles", np.full(4, np.nan)) for r in rows]).astype(np.float32),
        links=np.array(LINKS), zones=np.array(ZONES), poses=np.array(POSE))
    src = get("src")
    print("saved", a.out, len(rows), {s: int((src == s).sum()) for s in set(src.tolist())},
          "n_move", np.bincount(get("n_move")).tolist())


if __name__ == "__main__":
    main()
