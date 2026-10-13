"""Журнал присутствия из Home Assistant для слабых меток (ml/ha_weak.py): data/labels/ha.jsonl.

Раз в --every секунд читает /api/states и пишет, какие person.* дома, а какие нет. Токен HA (долгоживущий,
профиль пользователя HA -> Безопасность) лежит в data/ha.env и никуда не выводится:
    HA_URL=http://127.0.0.1:8123
    HA_TOKEN=<токен>
    HA_PERSONS=person.mikhail,person.dmitry   # необязательно: по умолчанию все person.*
Ошибка запроса -> строка не пишется: дыра в журнале, и ha_weak не выдаёт метку «никого нет» на этот отрезок.

usage: python3 tools/ha_labels.py [--every 30] [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("ha_labels")
UNKNOWN = {"unknown", "unavailable", ""}


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def classify(states: list[dict], persons: set[str] | None) -> dict:
    """person.* -> дома / не дома; unknown/unavailable не учитываются (не знаем — не метка)."""
    home, away = [], []
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith("person.") or (persons and eid not in persons):
            continue
        st = str(s.get("state", "")).lower()
        if st in UNKNOWN:
            continue
        (home if st == "home" else away).append(eid)
    return {"home": sorted(home), "away": sorted(away)}


def fetch(url: str, token: str, timeout: float = 5.0) -> list[dict]:
    req = urllib.request.Request(url.rstrip("/") + "/api/states", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=str(ROOT / "data" / "ha.env"))
    ap.add_argument("--out", default=str(ROOT / "data" / "labels" / "ha.jsonl"))
    ap.add_argument("--every", type=float, default=30.0)
    ap.add_argument("--once", action="store_true", help="one poll, print the result (setup check)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env_path = Path(a.env)
    if not env_path.exists():
        print(f"{env_path} not found: put HA_TOKEN=<long-lived token> there (see the docstring)", file=sys.stderr)
        return 2
    env = load_env(env_path)
    token = env.get("HA_TOKEN", "")
    if not token:
        print("HA_TOKEN is empty", file=sys.stderr)
        return 2
    url = env.get("HA_URL", "http://127.0.0.1:8123")
    persons = {p.strip() for p in env.get("HA_PERSONS", "").split(",") if p.strip()} or None
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fails = 0
    while True:
        t = time.time()
        try:
            row = {"t": round(t, 1), **classify(fetch(url, token), persons)}
            fails = 0
            if a.once:
                print(json.dumps(row, ensure_ascii=False))
                return 0 if row["home"] or row["away"] else 3
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except urllib.error.HTTPError as e:
            fails += 1
            log.error("HA answered HTTP %d (401 = wrong token)", e.code)
            if a.once:
                return 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            fails += 1
            log.warning("HA not reachable: %s", e)
            if a.once:
                return 1
        time.sleep(min(a.every * (2 ** min(fails, 4)), 600.0) if fails else a.every)


if __name__ == "__main__":
    sys.exit(main())
