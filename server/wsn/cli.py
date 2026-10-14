"""Small helpers for install.sh / train.sh (run inside the server container: no Python needed on the computer).

python -m wsn.cli status          what works now (boards, plan, recordings, model)
python -m wsn.cli boards          number of boards streaming CSI
python -m wsn.cli nets < scan     networks from `python -m wsn.provision scan` -> "name<TAB>rssi" lines
python -m wsn.cli wait-model V    wait until the server runs a model newer than V (seconds: --timeout)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8080"


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=5) as r:
        return json.loads(r.read().decode())


def fmt_status(st: dict) -> str:
    out = []
    boards = st.get("boards") or []
    if not boards:
        out.append("  ! сервер не получает данных от плат")
    for b in boards:
        out.append(f"  ✓ {b['name']}: {b['csi_hz']:.0f} кадров/с, линий {b['links']}, точка доступа {b.get('ap') or '?'}")
    plan = st.get("plan") or {}
    rooms = plan.get("rooms") or []
    out.append("  план: " + (f"комнаты {', '.join(rooms)}; платы на плане {len(plan.get('boards') or [])}"
                             if rooms else "нет (разметьте квартиру в приложении)"))
    walks = st.get("walks") or []
    if walks:
        w = walks[-1]
        out.append(f"  последняя запись обхода: {w['id']}, {w['duration_s'] / 60:.1f} мин")
    m = st.get("model") or {}
    if m.get("loaded"):
        h = ((m.get("metrics") or {}).get("heldout") or {})
        acc = f", комната угадывается в {h['zone_bal_acc']:.0%} окон" if h.get("zone_bal_acc") is not None else ""
        out.append(f"  модель квартиры: v{m['version']} от {m['created']}{acc}")
    else:
        out.append("  модель квартиры: нет (после записи обхода: ./train.sh)")
    t = st.get("training") or {}
    if t.get("state") == "running":
        out.append(f"  идёт обучение: {t.get('message', '')} ({t.get('progress', 0):.0%})")
    elif t.get("state") == "error":
        out.append(f"  ! последнее обучение не удалось: {t.get('message', '')}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("status", "boards", "nets", "wait-model"))
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args(argv)
    if a.cmd == "nets":
        raw = sys.stdin.read().strip()
        for name, rssi in (json.loads(raw).get("networks") or []) if raw else []:
            print(f"{name}\t{rssi}")
        return 0
    if a.cmd == "status":
        print(fmt_status(get("/api/status")))
        return 0
    if a.cmd == "boards":
        print(len(get("/api/status").get("boards") or []))
        return 0
    old = int(a.arg or 0)
    end = time.monotonic() + a.timeout
    while time.monotonic() < end:
        m = get("/api/model").get("model") or {}
        if m.get("loaded") and int(m.get("version") or 0) > old:
            print(m["version"])
            return 0
        time.sleep(1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
