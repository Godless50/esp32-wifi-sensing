"""Сравнение двух замеров tools/link_metrics.py: что изменилось на каждой плате (база -> текущий, дельта).

usage: python3 tools/metrics_diff.py <base.json> <current.json> [--device MAC]
"""

from __future__ import annotations

import argparse
import json
import sys

DEV_KEYS = (("infer_ms", "p50"), ("infer_ms", "p95"), ("infer_ms", "p99"), ("heap_free_min",), ("psram_free_min",),
            ("uplink", "loss_pct"))
LINK_KEYS = ("hz", "dt_ms_p50", "dt_ms_p95", "dt_ms_p99", "dt_ms_max", "gaps_gt_200ms", "gaps_gt_1000ms")


def get(d: dict, path: tuple):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def fmt(a, b) -> str:
    if a is None or b is None:
        return f"{a} -> {b}"
    delta = b - a
    rel = f" ({100 * delta / a:+.1f}%)" if a else ""
    return f"{a} -> {b}  Δ {delta:+.4g}{rel}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("current")
    ap.add_argument("--device", default="")
    a = ap.parse_args()
    base, cur = json.load(open(a.base)), json.load(open(a.current))
    print(f"base:    {base.get('started')} {base.get('repo')} {base.get('tag')}")
    print(f"current: {cur.get('started')} {cur.get('repo')} {cur.get('tag')}")
    devs = [a.device] if a.device else sorted(set(base["devices"]) & set(cur["devices"]))
    for dev in devs:
        b, c = base["devices"].get(dev), cur["devices"].get(dev)
        if not b or not c:
            print(f"{dev}: missing in one of the files")
            continue
        print(f"\n{dev}  fw {b.get('fw_ver')} -> {c.get('fw_ver')}  model {b.get('model_ver')} -> {c.get('model_ver')}")
        for k in DEV_KEYS:
            print(f"  {'.'.join(k):<18} {fmt(get(b, k), get(c, k))}")
        for link in sorted(set(b["links"]) & set(c["links"])):
            print(f"  link {link}")
            for k in LINK_KEYS:
                print(f"    {k:<16} {fmt(b['links'][link].get(k), c['links'][link].get(k))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
