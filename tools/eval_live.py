"""Разбор записи /ws/live против того, где люди на самом деле (со слов пользователя).

usage: python3 tools/eval_live.py <dump.jsonl> <occupied rooms, comma> [<empty rooms, comma>]
Печатает по каждой комнате: доля секунд «движение/слабое/тихо», источник; ответы модели платы и сервера
(распределение зон); трекер (зона, geo, число людей); и сводку попаданий.
"""
import collections
import json
import sys


def main() -> None:
    path, occ = sys.argv[1], set(sys.argv[2].split(","))
    empty = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else set()
    rooms = collections.defaultdict(collections.Counter)
    src = {}
    pred = collections.defaultdict(collections.Counter)
    pers_zone, geo_zone, n_people, minp = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    moving = collections.Counter()
    n_rooms = 0
    for line in open(path, encoding="utf-8"):
        m = json.loads(line)
        ty = m.get("type")
        if ty == "rooms":
            n_rooms += 1
            for r in m["rooms"]:
                rooms[r["name"]][r["state"]] += 1
                src[r["name"]] = r["source"]
        elif ty == "pred":
            who = f'{m.get("device_id", "?")[-5:]}:{m.get("source", m.get("model", "?"))}'
            pred[who][m.get("zone")] += 1
        elif ty == "person":
            pers_zone[m.get("zone")] += 1
            geo_zone[(m.get("geo") or {}).get("zone")] += 1
            n_people[(m.get("people") or {}).get("n")] += 1
            minp[m.get("min_people")] += 1
            moving[m.get("moving")] += 1
    print(f"rooms messages: {n_rooms}")
    hit = miss = fa = 0
    for name, c in sorted(rooms.items()):
        tot = sum(c.values())
        on = (c["движение"] + c["слабое"]) / tot
        truth = "есть люди" if name in occ else "пусто" if name in empty else "?"
        print(f"  {name:10s} [{src[name]:8s}] {truth:9s} движение {c['движение'] / tot:.2f} слабое {c['слабое'] / tot:.2f} тихо {c['тихо'] / tot:.2f}")
        if name in occ:
            hit += on
            miss += 1 - on
        elif name in empty:
            fa += on
    print(f"  попадания (есть люди и видно) {hit:.2f}, пропуски {miss:.2f}, ложные (пусто, а движение) {fa:.2f}")
    for who, c in pred.items():
        tot = sum(c.values())
        print(f"pred {who}: " + ", ".join(f"{z} {n / tot:.2f}" for z, n in c.most_common()))
    def show(name, c):
        tot = sum(c.values()) or 1
        print(f"{name}: " + ", ".join(f"{k} {n / tot:.2f}" for k, n in c.most_common()))
    show("tracker zone", pers_zone)
    show("geo zone", geo_zone)
    show("people n (moving)", n_people)
    show("min_people", minp)
    show("moving", moving)


if __name__ == "__main__":
    main()
