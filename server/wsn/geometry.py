"""Floor-plan geometry (spec 6.4).

WebXR local-floor: x right, y up, z towards the viewer. Horizontal projection (x, -z) is right-handed with up.
Plan frame: origin = board, +X towards the mesh node (router), +Y to the left of that axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_ROUTER_DIST_M = 0.5
MIN_AREA_M2 = 0.01


class GeometryError(ValueError):
    pass


def xr_to_floor(p) -> tuple[float, float]:
    return (float(p[0]), -float(p[2]))


@dataclass(frozen=True)
class Frame:
    origin: tuple[float, float]
    angle: float

    def apply(self, p_xr) -> tuple[float, float]:
        x, y = xr_to_floor(p_xr)
        vx, vy = x - self.origin[0], y - self.origin[1]
        c, s = math.cos(self.angle), math.sin(self.angle)
        return (vx * c + vy * s, -vx * s + vy * c)


def frame_from_points(origin_xr, router_xr) -> Frame:
    o, r = xr_to_floor(origin_xr), xr_to_floor(router_xr)
    dx, dy = r[0] - o[0], r[1] - o[1]
    if math.hypot(dx, dy) < MIN_ROUTER_DIST_M:
        raise GeometryError(f"router too close to board ({math.hypot(dx, dy):.2f} m < {MIN_ROUTER_DIST_M} m)")
    return Frame(o, math.atan2(dy, dx))


def _area(pts) -> float:
    return 0.5 * sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(p1, p2, p3, p4) -> bool:
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def polygon_valid(pts) -> str | None:
    pts = [tuple(map(float, p)) for p in pts]
    n = len(pts)
    if n < 3:
        return "too few corners"
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # соседние рёбра
            if _segments_intersect(*edges[i], *edges[j]):
                return "self-intersecting"  # раньше площади: у «бабочки» знаковая площадь ≈ 0
    if abs(_area(pts)) < MIN_AREA_M2:
        return "zero area"
    return None


DEDUPE_M = 0.25


def normalize_corners(pts) -> list[tuple[float, float]]:
    """Drop repeated presses (< 25 cm from a kept corner) and order corners by angle around the centroid,
    so the order in which the user tapped corners does not matter (fine for convex / mildly concave rooms)."""
    kept: list[tuple[float, float]] = []
    for p in pts:
        p = (float(p[0]), float(p[1]))
        if all(math.dist(p, k) >= DEDUPE_M for k in kept):
            kept.append(p)
    if len(kept) < 3:
        return kept
    cx = sum(x for x, _ in kept) / len(kept)
    cy = sum(y for _, y in kept) / len(kept)
    return sorted(kept, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def point_in_polygon(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside
