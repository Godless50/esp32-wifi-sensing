"""Per-board link activity and breathing on a recording: what each board sees while people are where they are.

usage: PYTHONPATH=server python3 tools/board_stats.py <plan.json> <dump.jsonl> [...]
"""
import collections
import json
import sys

import numpy as np

from wsn.person import PersonTracker


def main():
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    for dump in sys.argv[2:]:
        tr = PersonTracker()
        tr.set_plan(plan)
        per = collections.defaultdict(list)
        breath = collections.Counter()
        breath_n = collections.Counter()
        nt = None
        for line in open(dump, encoding="utf-8"):
            m = json.loads(line)
            ty = m.get("type")
            if ty == "breath":
                d = str(m.get("device_id", ""))[-5:]
                breath_n[d] += 1
                breath[d] += bool(m.get("ok"))
            if ty != "motion":
                continue
            tr.on_message(m)
            t = m["t"]
            nt = nt or t
            if t < nt:
                continue
            nt = t + 1
            b = collections.defaultdict(list)
            for (d, _), v in tr._link_activity(t).items():
                b[d[-5:]].append(v)
            for d, v in b.items():
                if len(v) >= 3:
                    per[d].append(float(np.mean(v)))
        print(dump)
        for d, v in sorted(per.items()):
            p = np.percentile(v, [10, 50, 90])
            print(f"  board {d}: mean link activity p10 {p[0]:.2f} p50 {p[1]:.2f} p90 {p[2]:.2f}"
                  f" | breathing ok {breath[d]}/{breath_n[d]}")


if __name__ == "__main__":
    main()
