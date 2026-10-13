"""Rooms with their own board: how to tell "somebody here" from the board's links (offline check on recordings).

usage: PYTHONPATH=server python3 tools/sweep_local.py <plan.json> <dump1.jsonl> <occupied1> [<dump2.jsonl> <occupied2> ...]
occupied: comma-separated board rooms with people (the other board rooms are empty).
Variants: per board A = mean activity of its links, EMA over TAU s;
  abs  — A >= T;
  diff — A - median over boards of A >= T (motion elsewhere disturbs every board a little: common mode removed).
"""
import collections
import json
import math
import sys

import numpy as np

from wsn.person import PersonTracker

TAU = 3.0


def series(dump, plan):
    tr = PersonTracker()
    tr.set_plan(plan)
    g = tr.grid
    room_of = {dev: g.labels[g.label_idx[int(np.argmin(np.hypot(*(g.xy - p).T)))]] for dev, p in g.boards.items()}
    out, nt, ema, last = [], None, {}, None
    for line in open(dump, encoding="utf-8"):
        m = json.loads(line)
        if m.get("type") != "motion":
            continue
        tr.on_message(m)
        t = m["t"]
        nt = nt or t
        if t < nt:
            continue
        nt = t + 1
        per = collections.defaultdict(list)
        for (d, _), v in tr._link_activity(t).items():
            per[d].append(v)
        a = 1.0 if last is None else 1 - math.exp(-(t - last) / TAU)
        last = t
        for d in room_of:
            if len(per.get(d, [])) >= 3:
                ema[d] = ema.get(d, float(np.mean(per[d]))) + a * (float(np.mean(per[d])) - ema.get(d, 0.0))
        if len(ema) == len(room_of):
            out.append({room_of[d]: v for d, v in ema.items()})
    return out, sorted(room_of.values())


def main():
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    sets = [(sys.argv[i], set(sys.argv[i + 1].split(","))) for i in range(2, len(sys.argv), 2)]
    data = [(series(d, plan), occ) for d, occ in sets]
    for var in ("abs", "diff", "combo"):
        for T in (0.03, 0.05, 0.08, 0.12, 0.2, 0.35):
            row = []
            for (S, rooms), occ in data:
                hit = fa = 0.0
                for s in S:
                    med = float(np.median(list(s.values())))
                    for r in rooms:
                        if var == "combo":  # заметно выше остальных плат И не шум
                            on = s[r] >= T and s[r] - med >= 0.02
                        else:
                            v = s[r] if var == "abs" else s[r] - med
                            on = v >= T
                        if r in occ:
                            hit += on / len(occ)
                        else:
                            fa += on / (len(rooms) - len(occ))
                row.append(f"hit {hit / len(S):.2f} false {fa / len(S):.2f}")
            print(f"{var:4s} T {T:.2f}: " + " | ".join(row))


if __name__ == "__main__":
    main()
