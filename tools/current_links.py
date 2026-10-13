"""Какие линии (передатчики) каждая плата видит сейчас — список для build_seq.py --links.

Модель должна обучаться на тех линиях, что есть в эфире: после отключения прослушивания (d9d050d) из 10 линий
live_model_s2 у платы осталась одна. Строка вывода по плате = MAC через запятую, сначала самые частые.

usage: python3 tools/current_links.py [--min-hz 20] [--api http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def pick(links: dict, min_hz: float) -> list[str]:
    live = [(mac, v.get("hz", 0.0)) for mac, v in links.items() if v.get("hz", 0.0) >= min_hz]
    return [mac for mac, _ in sorted(live, key=lambda x: -x[1])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-hz", type=float, default=20.0)
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    a = ap.parse_args()
    with urllib.request.urlopen(a.api + "/api/links", timeout=5) as r:
        devs = json.load(r).get("devices", {})
    if not devs:
        print("no boards in /api/links", file=sys.stderr)
        return 1
    for dev, links in sorted(devs.items()):
        sel = pick(links, a.min_hz)
        rates = ", ".join(f"{m[-5:]} {links[m]['hz']:.0f} Hz" for m in sel)
        print(f"{dev}: {rates}")
        print(f"  --links {','.join(sel)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
