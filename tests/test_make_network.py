"""Pin the geometry in :mod:`make_network`.

The conversion is mostly geometry, and geometry fails quietly -- a network with
a subtly wrong width or a mispaired carriageway still loads and still runs, it
just models a different road.  Every expected value here is hand-computed from
the shape being fed in.

Run with ``python tests/test_make_network.py``.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from make_network import (  # noqa: E402
    Projector, _closest_on_polyline, _median, _resample, collapse_roundabouts,
    merge_dual_carriageways, polyline_length, simplify, way_width,
)

TOL = 1e-6


def close(got, want, tol=TOL):
    assert abs(got - want) <= tol, f"got {got!r}, want {want!r}"


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------

def test_projection_origin_is_the_centre():
    p = Projector(23.79, 90.41)
    x, y = p.to_xy(90.41, 23.79)
    close(x, 0.0, 1e-9)
    close(y, 0.0, 1e-9)


def test_projection_scale_is_metres():
    """A hundredth of a degree of latitude is about 1.11 km anywhere."""
    p = Projector(23.79, 90.41)
    _, y = p.to_xy(90.41, 23.80)
    assert 1100.0 < abs(y) < 1115.0, y


def test_projection_y_grows_southwards():
    """Screen orientation, not map orientation: north must be the smaller y."""
    p = Projector(23.79, 90.41)
    _, north = p.to_xy(90.41, 23.80)
    _, south = p.to_xy(90.41, 23.78)
    assert north < 0 < south


def test_projection_longitude_shrinks_with_latitude():
    """A degree of longitude is narrower nearer the pole."""
    near_equator = Projector(0.0, 0.0)
    far_north = Projector(60.0, 0.0)
    x_equator, _ = near_equator.to_xy(1.0, 0.0)
    x_north, _ = far_north.to_xy(1.0, 60.0)
    # cos(60) = 1/2, so it should be about half
    close(x_north / x_equator, 0.5, 0.01)


# --------------------------------------------------------------------------
# polyline helpers
# --------------------------------------------------------------------------

def test_closest_point_on_a_straight_run():
    line = [(0.0, 0.0), (10.0, 0.0)]
    point, d = _closest_on_polyline((5.0, 3.0), line)
    close(point[0], 5.0)
    close(point[1], 0.0)
    close(d, 3.0)


def test_closest_point_clamps_to_the_ends():
    """Past the end of a polyline the nearest point is the end itself."""
    line = [(0.0, 0.0), (10.0, 0.0)]
    point, d = _closest_on_polyline((-4.0, 3.0), line)
    close(point[0], 0.0)
    close(d, 5.0)


def test_closest_point_uses_the_nearest_leg():
    line = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    _, d = _closest_on_polyline((12.0, 5.0), line)
    close(d, 2.0)


def test_resample_spaces_points_evenly():
    points = _resample([(0.0, 0.0), (100.0, 0.0)], 5)
    assert len(points) == 5
    for index, point in enumerate(points):
        close(point[0], index * 25.0, 1e-6)


def test_resample_keeps_the_endpoints_of_a_bent_line():
    line = [(0.0, 0.0), (30.0, 0.0), (30.0, 40.0)]
    points = _resample(line, 8)
    close(points[0][0], 0.0)
    close(points[-1][0], 30.0)
    close(points[-1][1], 40.0)
    close(polyline_length(points), 70.0, 1e-6)


def test_median_of_odd_and_even_counts():
    close(_median([3.0, 1.0, 2.0]), 2.0)
    close(_median([4.0, 1.0, 3.0, 2.0]), 2.5)


def test_median_ignores_extreme_ends():
    """Why the median rather than the mean: one wild value must not carry it."""
    close(_median([10.0, 11.0, 12.0, 13.0, 400.0]), 12.0)


# --------------------------------------------------------------------------
# simplification
# --------------------------------------------------------------------------

def test_simplify_collapses_collinear_points():
    line = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    assert simplify(line, 1.0) == [(0.0, 0.0), (30.0, 0.0)]


def test_simplify_keeps_a_real_corner():
    line = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert simplify(line, 1.0) == line


def test_simplify_respects_the_tolerance():
    """A 3 m deviation survives a 1 m tolerance and not a 5 m one."""
    line = [(0.0, 0.0), (10.0, 3.0), (20.0, 0.0)]
    assert len(simplify(line, 1.0)) == 3
    assert len(simplify(line, 5.0)) == 2


# --------------------------------------------------------------------------
# widths
# --------------------------------------------------------------------------

def test_width_prefers_the_explicit_tag():
    close(way_width({"highway": "primary", "width": "12.5"}), 12.5)
    close(way_width({"highway": "primary", "width": "12 m"}), 12.0)


def test_width_falls_back_to_lane_count_then_class():
    close(way_width({"highway": "primary", "lanes": "4"}), 13.0)   # 4 * 3.25
    close(way_width({"highway": "primary"}), 18.0)
    close(way_width({"highway": "residential"}), 8.0)


def test_width_survives_junk_tags():
    close(way_width({"highway": "residential", "width": "wide"}), 8.0)
    close(way_width({"highway": "residential", "lanes": "?"}), 8.0)


# --------------------------------------------------------------------------
# dual carriageways
# --------------------------------------------------------------------------

def _oneway(points, width=7.0, name="Test Ave"):
    return {"props": {"highway": "primary", "oneway": "yes", "name": name},
            "points": points, "width": width}


def test_dual_carriageway_pair_is_fused():
    """Two 7 m one-way roads 14 m apart become one road, kerb to kerb 21 m,
    with a 7 m divider between the carriageways."""
    east = _oneway([(0.0, 0.0), (200.0, 0.0)])
    west = _oneway([(200.0, 14.0), (0.0, 14.0)])
    edges, fused = merge_dual_carriageways([east, west], max_separation=45.0)
    assert fused == 1
    assert len(edges) == 1
    merged = edges[0]
    close(merged["width"], 21.0, 1e-3)          # 14 gap + 7/2 + 7/2
    close(merged["median"], 7.0, 1e-3)          # 14 gap - 7/2 - 7/2
    for point in merged["points"]:              # centreline sits between them
        close(point[1], 7.0, 1e-3)
    assert "oneway" not in merged["props"]


def test_roads_too_far_apart_are_left_alone():
    east = _oneway([(0.0, 0.0), (200.0, 0.0)])
    west = _oneway([(200.0, 300.0), (0.0, 300.0)])
    edges, fused = merge_dual_carriageways([east, west], max_separation=45.0)
    assert fused == 0
    assert len(edges) == 2


def test_roads_running_the_same_way_are_not_a_pair():
    """Two parallel one-way roads in the same direction are a couplet, not a
    divided road; fusing them would erase a carriageway."""
    a = _oneway([(0.0, 0.0), (200.0, 0.0)])
    b = _oneway([(0.0, 14.0), (200.0, 14.0)])
    edges, fused = merge_dual_carriageways([a, b], max_separation=45.0)
    assert fused == 0


def test_two_way_roads_are_never_fused():
    a = {"props": {"highway": "primary"}, "points": [(0.0, 0.0), (200.0, 0.0)],
         "width": 7.0}
    b = {"props": {"highway": "primary"}, "points": [(200.0, 14.0), (0.0, 14.0)],
         "width": 7.0}
    edges, fused = merge_dual_carriageways([a, b], max_separation=45.0)
    assert fused == 0
    assert len(edges) == 2


def test_unequal_extents_do_not_inflate_the_separation():
    """The bug this replaced: one carriageway clipped shorter than the other.

    Comparing sample points index for index made the mismatch look like 30 m of
    separation on a road whose carriageways are 14 m apart, so the fused link
    came out far too wide.  Measuring each point against the nearest point of
    the other polyline is immune to it.
    """
    east = _oneway([(0.0, 0.0), (200.0, 0.0)])
    west = _oneway([(120.0, 14.0), (0.0, 14.0)])     # only 60% as long
    edges, fused = merge_dual_carriageways([east, west], max_separation=45.0)
    assert fused == 1
    close(edges[0]["median"], 7.0, 0.5)
    close(edges[0]["width"], 21.0, 0.5)


def test_diverging_roads_are_not_paired():
    """Close at one end, far apart at the other: a fork, not a divided road."""
    a = _oneway([(0.0, 0.0), (300.0, 0.0)])
    b = _oneway([(300.0, 260.0), (0.0, 10.0)])
    edges, fused = merge_dual_carriageways([a, b], max_separation=45.0)
    assert fused == 0


def test_a_roundabout_ring_collapses_to_its_centre():
    """A junction=roundabout ring goes, and every road that touched it
    ends at the ring's centre -- the survey form of a roundabout.  A way
    running through (two ring vertices) keeps one centre point, an arc
    of the same ring shares the centre, and roads elsewhere are untouched.
    """
    ring = [[0.0, 1.0], [1.0, 0.0], [0.0, -1.0], [-1.0, 0.0], [0.0, 1.0]]
    ways = [
        ({"highway": "primary", "junction": "roundabout"}, ring[:3]),
        ({"highway": "primary", "junction": "roundabout"}, ring[2:]),
        ({"highway": "primary"}, [[0.0, 5.0], [0.0, 1.0]]),        # ends on it
        ({"highway": "primary"}, [[5.0, 0.0], [1.0, 0.0], [0.0, 0.2],
                                  [-1.0, 0.0], [-5.0, 0.0]]),      # through it
        ({"highway": "tertiary"}, [[9.0, 9.0], [9.0, 12.0]]),      # elsewhere
    ]
    out, count = collapse_roundabouts(ways)
    assert count == 1, count
    assert len(out) == 3, out
    north, through, far = out
    assert north[1] == [[0.0, 5.0], [0.0, 0.0]], north[1]
    assert through[1] == [[5.0, 0.0], [0.0, 0.0], [-5.0, 0.0]], through[1]
    assert far[1] == [[9.0, 9.0], [9.0, 12.0]]
    # No ring at all: the ways come back as they were.
    same, none = collapse_roundabouts(ways[2:])
    assert none == 0 and same == ways[2:]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            else:
                print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
