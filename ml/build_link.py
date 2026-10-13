"""Per-link dataset: one sample = one radio link "transmitter -> board" over a 4 s window, label from the people
labels of the session and the plan geometry. Nothing in a sample says where the link is, so one model can serve
every link of every board and keeps working when a board is moved (rooms come from the plan, not from the model).

Label of a link at a moment (rooms = plan rooms and zones, people from the "occupancy" events of the session):
  2 — somebody moves in a room the link passes close to (max sensitivity there >= NEAR_S);
  1 — nobody moves there, but somebody is there (still: sits, lies, eats);
  0 — every person is in rooms the link does not reach (max sensitivity < FAR_S), or the flat is empty;
  -1 (dropped) — people only in rooms half-covered by the link, or within TRANSITION_S of a label change.
Input features: build_seq.link_seq (per-frame normalised amplitudes, 40 steps x 4: no absolute level).

usage: PYTHONPATH=../server python build_link.py --plan ../data/eval/plan_1450.json --out ../data/ml/link_v1.npz
       [--sessions id1,id2,...]   (default: every session with occupancy labels)
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from build_rich import ltf_complex
from build_seq import NC, NT, WIN_S, link_seq
from wsn.keys import KeyStore
from wsn.person import Grid
from wsn.protocol import TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin

NEAR_S, FAR_S = 0.5, 0.15
TRANSITION_S = 3.0


def read_links(path: Path, keys: KeyStore) -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    per = defaultdict(list)
    for recv_t, buf in read_csi_bin(path):
        try:
            p = parse_packet(buf, keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError, ValueError):
            continue
        if p.type != TYPE_CSI_SRC or not p.frames:
            continue
        last = p.frames[-1].t_us
        for f in p.frames:
            if len(f.data) < 256:
                continue
            lag = (last - f.t_us) / 1e6
            per[(p.device_id.lower(), f.src.lower())].append((recv_t - lag if 0 <= lag < 2 else recv_t,
                                                               ltf_complex(f.data, 128)))
    out = {}
    for k, v in per.items():
        if len(v) < 200:
            continue
        v.sort(key=lambda x: x[0])
        out[k] = (np.array([t for t, _ in v]), np.stack([h for _, h in v]))
    return out


def timeline(sdir: Path) -> list[tuple[float, dict]]:
    ev = []
    p = sdir / "events.jsonl"
    if not p.exists():
        return ev
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("kind") == "occupancy" and isinstance(e.get("zones"), dict):
            t = float(e["t"]) - float(e.get("delay_s") or 0.0)  # отметка «N секунд назад»
            ev.append((t, e["zones"]))
    ev.sort(key=lambda x: x[0])
    return ev


def coverage(grid: Grid, dev: str, src: str) -> dict[str, float] | None:
    s = grid.sensitivity(dev, src, src == "02:00:00:00:a1:18")
    if s is None:
        return None
    return {lab: float(s[grid.label_idx == i].max()) for i, lab in enumerate(grid.labels)}


MOVE_WORDS = ("движ", "ход", "маш", "бега", "игра", "убира", "готов")


def moving_count(v: dict) -> int:
    """People moving in a zone: the "moving" field of the people page, else words of the free-text activity
    (утренние сценарии: "движется у узла mesh", "лежит на диване", "2 ребёнка спят")."""
    if v.get("moving") is not None:
        return int(v["moving"])
    text = str(v.get("activity") or "").lower()
    if "не движ" in text or "0 из" in text:
        return 0
    return int(v.get("count", 0)) if any(w in text for w in MOVE_WORDS) else 0


def link_label(cov: dict[str, float], zones: dict) -> int:
    people = {z: v for z, v in zones.items() if (v or {}).get("count", 0) > 0}
    if not people:
        return 0
    near = [z for z in people if cov.get(z, 0.0) >= NEAR_S]
    if any(moving_count(people[z]) > 0 for z in near):
        return 2
    if near:
        return 1
    if all(cov.get(z, 0.0) < FAR_S for z in people):
        return 0
    return -1


def build(sid: str, sdir: Path, grid: Grid, keys: KeyStore):
    ev = timeline(sdir)
    if not ev:
        return None
    path = sdir / "csi_all.bin" if (sdir / "csi_all.bin").exists() else sdir / "csi.bin"
    links = read_links(path, keys)
    X, y, lid, ts = [], [], [], []
    change_t = np.array([t for t, _ in ev])
    for (dev, src), (t, H) in links.items():
        cov = coverage(grid, dev, src)
        if cov is None:
            continue
        for tc in np.arange(max(t[0], ev[0][0]) + WIN_S, t[-1], 1.0):
            k = int(np.searchsorted(change_t, tc - WIN_S / 2, side="right")) - 1
            if k < 0 or np.min(np.abs(change_t - (tc - WIN_S / 2))) < TRANSITION_S:
                continue
            lab = link_label(cov, ev[k][1])
            if lab < 0:
                continue
            x = link_seq(t, H, tc - WIN_S)
            if not np.abs(x).sum():
                continue
            X.append(x)
            y.append(lab)
            lid.append(f"{dev}|{src}")
            ts.append(tc)
    if not X:
        return None
    n = len(X)
    return {"X": np.stack(X).astype(np.float16), "y": np.array(y, np.int8), "link": np.array(lid),
            "board": np.array([l.split("|")[0] for l in lid]), "session": np.array([sid] * n), "t": np.array(ts)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", default="")
    a = ap.parse_args()
    data = Path(a.data)
    grid = Grid(json.loads(Path(a.plan).read_text(encoding="utf-8")))
    keys = KeyStore(data / "device_keys.json")
    sids = a.sessions.split(",") if a.sessions else sorted(p.name for p in (data / "sessions").iterdir())
    parts = []
    for sid in sids:
        r = build(sid, data / "sessions" / sid, grid, keys)
        if r is None:
            continue
        c = np.bincount(r["y"], minlength=3)
        print(f"{sid}: {len(r['y'])} samples, {len(set(r['link']))} links, boards {sorted(set(r['board']))},"
              f" labels nobody/still/moving {c.tolist()}", flush=True)
        parts.append(r)
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    np.savez_compressed(a.out, **out)
    print(f"-> {a.out}: {len(out['y'])} samples, labels {np.bincount(out['y'], minlength=3).tolist()}")


if __name__ == "__main__":
    main()
