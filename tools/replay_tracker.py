"""Replay a /ws/live recording through the current tracker code and compare its room states with the recording.

usage: PYTHONPATH=server python3 tools/replay_tracker.py <dump.jsonl> <plan.json> "<group1>;<group2>" "<empty rooms>" [NAME=value ...]
NAME=value overrides a constant of wsn.person for the replay (e.g. LOCAL_W=0).
A group (comma-separated rooms) is "seen" in a second when any of its rooms is at least "слабое".
"""
import collections
import json
import sys

from wsn.person import PersonTracker


def score(msgs, groups, empty):
    seen = [0.0] * len(groups)
    fa = collections.Counter()
    n = 0
    for m in msgs:
        on = {r["name"] for r in m["rooms"] if r["state"] != "тихо"}
        n += 1
        for i, g in enumerate(groups):
            seen[i] += bool(g & on)
        for e in empty:
            fa[e] += e in on
    n = max(n, 1)
    return [round(x / n, 2) for x in seen], {e: round(fa[e] / n, 2) for e in empty}


def main():
    dump, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    groups = [set(g.split(",")) for g in sys.argv[3].split(";")]
    empty = sys.argv[4].split(",")
    import wsn.person as P
    for kv in sys.argv[5:]:
        k, v = kv.split("=")
        setattr(P, k, float(v))
    out = []
    tr = PersonTracker(publish=lambda m: out.append(m) if m.get("type") == "rooms" else None)
    tr.set_plan(plan)
    live = []
    for line in open(dump, encoding="utf-8"):
        m = json.loads(line)
        if m.get("type") == "rooms":
            live.append(m)
        elif m.get("type") in ("motion", "pred", "breath"):
            tr.on_message(m)
    print("было  (запись):", *score(live, groups, empty))
    print("стало (код)   :", *score(out, groups, empty))


if __name__ == "__main__":
    main()
