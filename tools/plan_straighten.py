#!/usr/bin/env python3
"""Выпрямляет стены плана: прямые стены — строго под 90°, общие стены сливаются в одну линию,
отмеченные дуги сохраняются как дуги. Читает и пишет data/plan_overrides.json.

usage: plan_straighten.py OVERRIDES.json --arc "зал:1-4" [--merge 0.35] [--out FILE]
  --arc ROOM:I-J  вершины I..J комнаты (индексы с 0) лежат на дуге окружности
"""
import argparse
import json
import math


def dominant_angle(edges):
    # длина-взвешенное среднее направление по модулю 90° (угол ×4)
    sx = sy = 0.0
    for a, b in edges:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        t = 4 * math.atan2(dy, dx)
        sx += L * math.cos(t)
        sy += L * math.sin(t)
    return math.atan2(sy, sx) / 4


def rot(p, t):
    c, s = math.cos(t), math.sin(t)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def cluster(values, tol):
    """values: list of (value, weight, key) -> {key: merged value}"""
    out, group = {}, []
    for v in sorted(values) + [(math.inf, 0, None)]:
        if group and v[0] - group[-1][0] > tol:
            w = sum(g[1] for g in group)
            m = sum(g[0] * g[1] for g in group) / w
            for g in group:
                out[g[2]] = m
            group = []
        group.append(v)
    return out


def arc_points(p0, p1, mids, step=0.25):
    """Окружность через p0, p1 с центром на серединном перпендикуляре, ближе всего к mids."""
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    nx, ny = -dy / math.hypot(dx, dy), dx / math.hypot(dx, dy)
    best = None
    for i in range(-4000, 4001):
        t = i * 0.005
        cx, cy = mx + nx * t, my + ny * t
        r = math.hypot(p0[0] - cx, p0[1] - cy)
        err = sum((math.hypot(q[0] - cx, q[1] - cy) - r) ** 2 for q in mids)
        if best is None or err < best[0]:
            best = (err, cx, cy, r)
    _, cx, cy, r = best
    a0, a1 = math.atan2(p0[1] - cy, p0[0] - cx), math.atan2(p1[1] - cy, p1[0] - cx)
    am = math.atan2(mids[0][1] - cy, mids[0][0] - cx)
    # выбираем направление обхода, проходящее через промежуточные точки
    d = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > d:
        d -= 2 * math.pi
    n = max(2, int(abs(d) * r / step))
    return [(cx + r * math.cos(a0 + d * k / n), cy + r * math.sin(a0 + d * k / n)) for k in range(1, n)], r


def straighten(rooms, arcs, tol):
    edges, arc_edges = [], set()
    for name, P in rooms.items():
        if name in arcs:
            i, j = arcs[name]
            arc_edges |= {(name, k) for k in range(i, j)}
        for k in range(len(P)):
            if (name, k) not in arc_edges:
                edges.append((P[k], P[(k + 1) % len(P)]))
    theta = dominant_angle(edges)
    R = {n: [rot(p, -theta) for p in P] for n, P in rooms.items()}
    # ориентация и положение каждой прямой стены
    lines = {}
    for name, P in R.items():
        for k in range(len(P)):
            if (name, k) in arc_edges:
                continue
            a, b = P[k], P[(k + 1) % len(P)]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            horiz = abs(b[1] - a[1]) < abs(b[0] - a[0])
            val = (a[1] + b[1]) / 2 if horiz else (a[0] + b[0]) / 2
            lines[(name, k)] = ("H" if horiz else "V", val, L)
    merged = {}
    for o in "HV":
        merged.update(cluster([(v, L, key) for key, (oo, v, L) in lines.items() if oo == o], tol))
    out = {}
    for name, P in R.items():
        n = len(P)
        pts = []
        for k in range(n):  # вершина k между ребром k-1 и ребром k
            e0, e1 = (name, (k - 1) % n), (name, k)
            l0, l1 = lines.get(e0), lines.get(e1)
            if l0 and l1:
                if l0[0] == l1[0]:
                    continue  # коллинеарные стены — вершина лишняя
                u = merged[e0] if l0[0] == "V" else merged[e1]
                v = merged[e0] if l0[0] == "H" else merged[e1]
                pts.append(((u, v), k))
            elif l0 or l1:  # конец дуги: двигаем только поперёк прямой стены
                l, e = (l0, e0) if l0 else (l1, e1)
                p = P[k]
                pts.append(((merged[e], p[1]) if l[0] == "V" else (p[0], merged[e]), k))
            else:
                pts.append((None, k))  # внутренняя точка дуги — пересчитаем ниже
        if name in arcs:
            i, j = arcs[name]
            fixed = {k: p for p, k in pts if p is not None}
            curve, r = arc_points(fixed[i], fixed[j], [P[k] for k in range(i + 1, j)])
            res = []
            for p, k in pts:
                if p is None:
                    continue
                res.append(p)
                if k == i:
                    res.extend(curve)
            print(f"{name}: дуга радиус {r:.2f} м")
        else:
            res = [p for p, _ in pts]
        out[name] = [[round(x, 3), round(y, 3)] for x, y in (rot(p, theta) for p in res)]
    print(f"основное направление стен: {math.degrees(theta):.1f}°")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("overrides")
    ap.add_argument("--arc", action="append", default=[])
    ap.add_argument("--merge", type=float, default=0.35)
    ap.add_argument("--out")
    a = ap.parse_args()
    data = json.load(open(a.overrides, encoding="utf-8"))
    arcs = {}
    for s in a.arc:
        name, rng = s.rsplit(":", 1)
        i, j = map(int, rng.split("-"))
        arcs[name] = (i, j)
    data["rooms"] = straighten({n: [tuple(p) for p in P] for n, P in data["rooms"].items()}, arcs, a.merge)
    json.dump(data, open(a.out or a.overrides, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for n, P in data["rooms"].items():
        print(n, len(P), "вершин")


if __name__ == "__main__":
    main()
