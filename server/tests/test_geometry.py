import math

import pytest

from wsn.geometry import GeometryError, frame_from_points, point_in_polygon, polygon_valid, xr_to_floor


def test_xr_to_floor():
    assert xr_to_floor((1.0, 1.5, -2.0)) == (1.0, 2.0)


def test_frame_axes():
    f = frame_from_points((0, 0, 0), (0, 0, -3))
    assert f.apply((0, 0, -3)) == pytest.approx((3.0, 0.0))
    assert f.apply((-1, 0, 0)) == pytest.approx((0.0, 1.0))  # влево от оси плата→роутер = +Y


def test_frame_preserves_distances():
    f = frame_from_points((2.0, 0.1, 5.0), (-1.0, 0.0, 1.0))
    a, b = (0.3, 0, 0.7), (4.1, 1, -2.2)
    pa, pb = f.apply(a), f.apply(b)
    d_xr = math.dist(xr_to_floor(a), xr_to_floor(b))
    assert math.dist(pa, pb) == pytest.approx(d_xr)
    assert f.apply((2.0, 0.1, 5.0)) == pytest.approx((0.0, 0.0))


def test_router_too_close():
    with pytest.raises(GeometryError, match="router too close"):
        frame_from_points((0, 0, 0), (0.2, 0, -0.2))


@pytest.mark.parametrize(
    "pts,reason",
    [
        ([(0, 0), (1, 0)], "too few corners"),
        ([(0, 0), (1, 0), (2, 0)], "zero area"),
        ([(0, 0), (2, 2), (2, 0), (0, 2)], "self-intersecting"),  # «бабочка»
        ([(0, 0), (3, 0), (3, 2), (0, 2)], None),
    ],
)
def test_polygon_valid(pts, reason):
    assert polygon_valid(pts) == reason


def test_normalize_corners_dedupes_and_orders():
    from wsn.geometry import normalize_corners

    # углы прямоугольника 4×3 в «случайном» порядке + повторные нажатия в одной точке
    raw = [(0, 0), (4, 3), (0.05, 0.02), (4, 0), (0, 3), (4.1, 3.05)]
    out = normalize_corners(raw)
    assert len(out) == 4
    assert polygon_valid(out) is None
    assert {(round(x), round(y)) for x, y in out} == {(0, 0), (4, 0), (4, 3), (0, 3)}


def test_point_in_polygon():
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert point_in_polygon((1, 1), sq) and not point_in_polygon((3, 1), sq)
