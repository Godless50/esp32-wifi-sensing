"""Опись записей: сколько секунд размечено как «пусто» / «неподвижны» / «ходят» и какие линии (плата ← передатчик,
HT-кадры, Гц) есть в csi_all.bin (или csi.bin). Нужна, чтобы знать, из чего можно собрать синтетику для текущих линий.

usage: cd ml && PYTHONPATH=../server python session_inventory.py [--out ../data/eval/inventory.json]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from build_rich import MOVE
from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI, TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id
from wsn.store import read_csi_bin


def label_kind(e: dict) -> str | None:
    """empty / still / moving / None (переход или пусто по зонам без чисел)."""
    zones = e.get("zones") or {}
    note = e.get("note", "")
    subj = e.get("subject")
    if "переход" in note:
        return None
    if subj:
        act = subj.get("act", "")
        return "moving" if act in ("ходит", "машет", "мелкие") else "still" if act in ("покой", "стоит") else None
    if not zones:
        return None
    if all(z.get("count", 0) == 0 for z in zones.values()):
        return "empty"
    acts = " ".join(z.get("activity", "") for z in zones.values())
    moving = any(z.get("moving") for z in zones.values()) or bool(MOVE.search(acts))
    return "moving" if moving else "still"


def label_durations(sdir: Path) -> dict:
    ev = [json.loads(x) for x in (sdir / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    occ = sorted(((e["t"] + float(e.get("delay_s", 0) or 0), e) for e in ev if e.get("kind") == "occupancy"),
                 key=lambda x: x[0])
    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    end = meta.get("stopped") or (occ[-1][0] if occ else 0)
    out = defaultdict(float)
    changes = 0
    prev = None
    for i, (t, e) in enumerate(occ):
        t1 = occ[i + 1][0] if i + 1 < len(occ) else end
        k = label_kind(e)
        if k:
            out[k] += max(0.0, t1 - t)
            if prev and k != prev:
                changes += 1
            prev = k
    return {**{k: round(v) for k, v in out.items()}, "changes": changes}


def links(sdir: Path, keys: KeyStore) -> dict:
    path = sdir / "csi_all.bin"
    if not path.exists():
        path = sdir / "csi.bin"
    if not path.exists():
        return {}
    n = defaultdict(int)
    t0 = t1 = None
    for recv_t, buf in read_csi_bin(path):
        t0 = recv_t if t0 is None else t0
        t1 = recv_t
        try:
            dev = peek_device_id(buf)
            p = parse_packet(buf, keys.get(dev))
        except (ProtocolError, TypeError):
            continue
        if p.type not in (TYPE_CSI, TYPE_CSI_SRC):
            continue
        for f in p.frames:
            if len(f.data) >= 256:
                n[f"{p.device_id[-5:]}<-{(f.src or 'ap')[-5:]}"] += 1
    dur = max(1e-9, (t1 or 0) - (t0 or 0))
    return {k: round(v / dur, 1) for k, v in sorted(n.items()) if v / dur >= 5}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    data = Path(a.data)
    keys = KeyStore(data / "device_keys.json")
    rep = {}
    for sdir in sorted((data / "sessions").iterdir()):
        if not (sdir / "events.jsonl").exists() or not (sdir / "meta.json").exists():
            continue
        meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
        rep[sdir.name] = {"kind": meta.get("kind"), "device": meta.get("device_id", "")[-5:],
                          "labels": label_durations(sdir), "links_hz": links(sdir, keys)}
        r = rep[sdir.name]
        print(f"{sdir.name} {r['kind']:6} {r['device']} labels {r['labels']} links {r['links_hz']}", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
