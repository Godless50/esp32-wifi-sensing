"""Rich per-link window features from raw CSI, for training on real labels.

Per link and window (WIN_S = 4 s, STRIDE_S = 1 s), frames of that transmitter only:
  lvar   log mean temporal std of AGC-normalised amplitude in 1 s chunks   (motion level)
  lpeak  log max of the same in 0.5 s chunks                                (bursts)
  b1..b4 log power of the amplitude's 1st principal component on a 20 Hz grid,
         bands 0.15-0.7 / 0.7-2 / 2-4.5 / 4.5-10 Hz                         (breathing / gestures, gait / fast)
  per    max autocorrelation of that component at lags 0.4-2.5 s            (periodicity: waving, steps)
  lpvar  log mean circular std (1 s chunks) of the adjacent-subcarrier phase difference after removing its
         per-frame mean - the timing slope and common phase cancel (CSCR-like)  (fine motion)
  rate   frames per second; a link with < MIN_FRAMES frames in the window is all zeros
Labels:
  every window of the session is kept (stride 1 s, labels -1 where unknown) so causal context features work;
  scripted sessions (events with "script": true; "subject" from the /scenario page when present) ->
  act (0 rest, 1 stand, 2 wave, 3 walk, 4 fine motion), zone of the
  active person, phase = index of the scripted event; windows crossing a label change or a transition: -1
  spoken sessions -> Y like build_multilink (moving_any, moving_<zone>, occupied_<zone>), else -1

usage: python build_rich.py --data /data --out /data/ml/rich_v2.npz --sessions id1,id2,...
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

WIN_S, STRIDE_S, GRID_HZ, MIN_FRAMES = 4.0, 1.0, 20.0, 12
LINKS = ["02:00:00:00:a1:18", "98:b3:ef:02:da:28", "98:b3:ef:02:da:14", "02:00:00:00:a1:2c",
         "02:00:00:00:f6:7c", "02:00:00:00:e5:57", "d8:d6:68:b3:4c:23", "d8:d6:68:b3:7a:6f",
         "02:00:00:00:a7:e6", "02:00:00:00:b8:14"]  # последним — робот-пылесос (только 11g, L-LTF)
FEATS = ["lvar", "lpeak", "b1", "b2", "b3", "b4", "per", "lpvar", "rate"]
BANDS = [(0.15, 0.7), (0.7, 2.0), (2.0, 4.5), (4.5, 10.0)]
ZONES = ["спальня", "зал", "кухня", "коридор", "санузел", "комната"]
ACT = [("стоит", 1), ("машет", 2), ("ход|идёт|идет|встаёт", 3)]
SUBJECT_ACT = {"покой": 0, "стоит": 1, "машет": 2, "ходит": 3, "мелкие": 4}  # метки страницы /scenario
MOVE = re.compile(r"ход|идёт|идет|иду|транзит|встал|встаю|ушёл|ушел|пошёл|пошел|машет|вошёл|вошел", re.I)
AMBIGUOUS = re.compile(r"ногу|скан|перезагрузк", re.I)
REORDER = np.r_[32:64, 0:32]


def ltf_complex(data: bytes, off: int = 128) -> np.ndarray:
    """64 complex subcarriers: off=128 HT-LTF (кадры 11n), off=0 L-LTF."""
    iq = np.frombuffer(data, dtype=np.int8, count=128, offset=off).astype(np.float32).reshape(64, 2)
    return (iq[:, 1] + 1j * iq[:, 0])[REORDER]  # (imag, real) в буфере ESP


def link_csi(path: Path, keys: KeyStore) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Complex LTF per transmitter: {mac: (t[n] server-monotonic, H[n, 64])}.

    HT-LTF from 11n frames; a transmitter that sends (almost) only 11a/g frames (robot vacuum) gets its L-LTF.
    The two are never mixed in one link.
    """
    per, legacy = defaultdict(list), defaultdict(list)
    for recv_t, buf in read_csi_bin(path):
        try:
            p = parse_packet(buf, keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError):
            continue
        if p.type != TYPE_CSI_SRC or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            t = recv_t - (last - f.t_us) / 1e6
            if len(f.data) >= 256:
                per[f.src].append((t, ltf_complex(f.data, 128)))
            elif len(f.data) >= 128:
                legacy[f.src].append((t, ltf_complex(f.data, 0)))
    out = {}
    for src in set(per) | set(legacy):
        v = per[src] if len(per[src]) >= 0.2 * (len(per[src]) + len(legacy[src])) else legacy[src]
        if not v:
            continue
        v.sort(key=lambda x: x[0])
        out[src] = (np.array([t for t, _ in v]), np.stack([h for _, h in v]).astype(np.complex64))
    return out


def _chunks(t: np.ndarray, t0: float, chunk: float):
    k = np.floor((t - t0) / chunk).astype(int)
    for i in np.unique(k):
        m = k == i
        if m.sum() >= 3:
            yield m


def link_window(t: np.ndarray, H: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """FEATS for one link and window [t0, t1)."""
    row = np.zeros(len(FEATS), np.float32)
    i0, i1 = np.searchsorted(t, [t0, t1])
    n = int(i1 - i0)
    if n < MIN_FRAMES:
        return row
    tt, HH = t[i0:i1], H[i0:i1]
    amp = np.abs(HH)
    live = amp.mean(0) > 2
    if live.sum() < 8:
        return row
    amp = amp[:, live] / (amp[:, live].mean(1, keepdims=True) + 1e-3)  # AGC: делим на средний уровень кадра
    v1 = [amp[m].std(0).mean() for m in _chunks(tt, t0, 1.0)]
    v05 = [amp[m].std(0).mean() for m in _chunks(tt, t0, 0.5)]
    if not v1:
        return row
    X = amp - amp.mean(0)
    s = X @ np.linalg.svd(X, full_matrices=False)[2][0]
    grid = np.arange(t0, t1, 1.0 / GRID_HZ)
    g = np.interp(grid, tt, s)
    x = np.arange(len(g))
    g = g - np.polyval(np.polyfit(x, g, 1), x)
    w = np.hanning(len(g))
    ps = np.abs(np.fft.rfft(g * w)) ** 2 / (w ** 2).sum()
    f = np.fft.rfftfreq(len(g), 1.0 / GRID_HZ)
    bands = [np.log10(ps[(f >= lo) & (f < hi)].sum() + 1e-7) for lo, hi in BANDS]
    den = float((g ** 2).sum()) + 1e-12
    per = max(float((g[:-L] * g[L:]).sum()) / den for L in range(int(0.4 * GRID_HZ), int(2.5 * GRID_HZ) + 1))
    idx = np.flatnonzero(live)
    pairs = idx[:-1][np.diff(idx) == 1]
    lpvar = 0.0
    if len(pairs) >= 4:
        D = HH[:, pairs] * np.conj(HH[:, pairs + 1])
        D = D / (np.abs(D) + 1e-6)
        c = D.mean(1, keepdims=True)
        D = D * np.conj(c / (np.abs(c) + 1e-6))  # убираем общий для кадра наклон фазы (тайминг) и общий сдвиг
        pv = [np.sqrt(-2 * np.log(np.abs(D[m].mean(0)).clip(1e-6, 1.0))).mean() for m in _chunks(tt, t0, 1.0)]
        lpvar = float(np.log10(np.mean(pv) + 1e-4)) if pv else 0.0
    row[:] = [np.log10(100 * np.mean(v1) + 1e-3), np.log10(100 * max(v05 or v1) + 1e-3), *bands, per, lpvar,
              n / (t1 - t0)]
    return row


def load_occupancy(sdir: Path) -> list[tuple[float, dict]]:
    ev = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return sorted(((e["t"], e) for e in ev if e.get("kind") == "occupancy"), key=lambda x: x[0])


def _state(occ, t0, t1):
    """Index of the label state covering the whole window, or None if the window crosses a change."""
    idx = [i for i, (t, _) in enumerate(occ) if t <= t0]
    if not idx or any(t0 < t <= t1 for t, _ in occ):
        return None
    return idx[-1]


def script_label(occ, t0, t1) -> tuple[int, int, int]:
    i = _state(occ, t0, t1)
    if i is None:
        return -1, -1, -1
    e = occ[i][1]
    note = e.get("note", "")
    subj = e.get("subject")
    if subj is not None:  # точная метка сценария со страницы /scenario
        a = SUBJECT_ACT.get(subj.get("act"), -1)
        if not e.get("script") or a < 0:
            return -1, -1, -1
        return a, (ZONES.index(subj["zone"]) if subj.get("zone") in ZONES else -1), i
    if not e.get("script") or "переход" in note:
        return -1, -1, -1
    if "лежит на диване" in note:
        return 0, -1, i  # опорный покой: все лежат/спят
    for name, v in e["zones"].items():
        if v.get("count", 0) > 0:
            for pat, code in ACT:
                if re.search(pat, v.get("activity", "")):
                    return code, ZONES.index(name), i
    return -1, -1, -1


def spoken_label(occ, t0, t1) -> list[int] | None:
    i = _state(occ, t0, t1)
    if i is None:
        return None
    e = occ[i][1]
    zones = e["zones"]
    acts = " ".join(z.get("activity", "") for z in zones.values())
    if AMBIGUOUS.search(acts) or AMBIGUOUS.search(e.get("note", "")) or "переход" in e.get("note", ""):
        return None
    moving = [int(z in zones and zones[z].get("count", 0) > 0 and bool(MOVE.search(zones[z].get("activity", ""))))
              for z in ZONES]
    occupied = [int(zones[z].get("count", 0) > 0) if z in zones else -1 for z in ZONES]
    return [int(any(moving))] + moving + occupied


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/data/ml/rich_v1.npz")
    ap.add_argument("--sessions", default="20261013-042804-6eba,20261013-050259-94cd,20261013-053501-ee64")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    X, ACTS, ZS, PH, Y, T, S = [], [], [], [], [], [], []
    empty = np.zeros(len(FEATS), np.float32)
    sids = a.sessions.split(",")
    if a.sessions == "all":  # все записи с разметкой «кто где»
        sids = sorted(p.parent.name for p in (data / "sessions").glob("*/events.jsonl")
                      if '"occupancy"' in p.read_text(encoding="utf-8") and (p.parent / "csi.bin").exists())
    for sid in sids:
        sdir = data / "sessions" / sid
        csi = link_csi(sdir / "csi.bin", keys)
        if not csi:
            print(f"{sid}: no per-transmitter CSI (old firmware), skipped", flush=True)
            continue
        occ = load_occupancy(sdir)
        lo, hi = min(v[0][0] for v in csi.values()), max(v[0][-1] for v in csi.values())
        n = 0
        for t0 in np.arange(lo, hi - WIN_S, STRIDE_S):
            t1 = t0 + WIN_S
            y = spoken_label(occ, t0, t1)
            act, zone, ph = script_label(occ, t0, t1)  # все окна подряд (шаг 1 с): нужны для признаков «было 4 с назад»
            X.append(np.stack([link_window(*csi[m], t0, t1) if m in csi else empty for m in LINKS]))
            ACTS.append(act); ZS.append(zone); PH.append(ph)
            Y.append(y if y is not None else [-1] * (1 + 2 * len(ZONES)))
            T.append(t0 + WIN_S / 2); S.append(sid); n += 1
        hz = {m[-5:]: round(len(csi[m][0]) / (hi - lo), 1) for m in LINKS if m in csi}
        print(f"{sid}: {(hi - lo) / 60:.1f} min, windows={n}, scripted={sum(1 for s_, a_ in zip(S, ACTS) if s_ == sid and a_ >= 0)}, Hz={hz}", flush=True)
    np.savez_compressed(a.out, X=np.stack(X), act=np.array(ACTS, np.int8), zone=np.array(ZS, np.int8),
                        phase=np.array(PH, np.int16), Y=np.array(Y, np.int8), t=np.array(T), session=np.array(S),
                        links=np.array(LINKS), feats=np.array(FEATS), zones=np.array(ZONES))
    print(f"saved {a.out}: X={np.stack(X).shape}")


if __name__ == "__main__":
    main()
