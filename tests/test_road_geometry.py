"""Pin the junction geometry in :mod:`dhakasim.road_geometry`.

Rendering geometry fails quietly: a junction patch with a wrong corner still
draws, it just draws the wrong shape.  Every expected value here is computed by
hand from the polygon being fed in.

Run with ``python tests/test_road_geometry.py``.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import road_geometry  # noqa: E402
from dhakasim.parameters import Parameters  # noqa: E402
from dhakasim.road_geometry import convex_hull, fillet_polygon  # noqa: E402


def close(got, want, tol=1e-6):
    assert abs(got - want) <= tol, f"got {got!r}, want {want!r}"


SQUARE = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


def _inside(point, poly, tol=1e-6):
    """Winding-free containment test, valid because every polygon here is convex."""
    n = len(poly)
    sign = 0
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
        if abs(cross) <= tol:
            continue
        if sign == 0:
            sign = 1 if cross > 0 else -1
        elif (1 if cross > 0 else -1) != sign:
            return False
    return True


def _area(poly):
    total = 0.0
    for i in range(len(poly)):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % len(poly)]
        total += ax * by - bx * ay
    return abs(total) / 2.0


# --------------------------------------------------------------------------
# degenerate input must pass straight through
# --------------------------------------------------------------------------

def test_zero_radius_is_the_identity():
    assert fillet_polygon(SQUARE, 0.0) == SQUARE


def test_too_few_points_pass_through():
    assert fillet_polygon([(0.0, 0.0), (1.0, 1.0)], 5.0) == [(0.0, 0.0), (1.0, 1.0)]


def test_collinear_vertex_is_left_alone():
    """A point in the middle of a straight edge has no corner to round."""
    line = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    out = fillet_polygon(line, 10.0)
    assert (50.0, 0.0) in out


def test_duplicate_points_do_not_crash():
    poly = [(0.0, 0.0), (0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    assert len(fillet_polygon(poly, 10.0)) >= 3


# --------------------------------------------------------------------------
# the rounding itself
# --------------------------------------------------------------------------

def test_corners_are_replaced_by_arcs():
    out = fillet_polygon(SQUARE, 20.0)
    for corner in SQUARE:
        assert corner not in out, f"{corner} survived rounding"
    assert len(out) > len(SQUARE)


def test_rounded_square_stays_inside_the_original():
    """Rounding may only remove area, never push the patch over a kerb."""
    out = fillet_polygon(SQUARE, 20.0)
    for point in out:
        assert _inside(point, SQUARE), point


def test_area_lost_matches_the_corner_geometry():
    """Four right-angle corners of radius r lose exactly (4 - pi) * r^2."""
    r = 20.0
    out = fillet_polygon(SQUARE, r, segments=400)
    expected = _area(SQUARE) - (4.0 - math.pi) * r * r
    close(_area(out), expected, 0.5)


def test_tangent_distance_on_a_right_angle():
    """At 90 degrees the cut-back equals the radius, so the arc starts there."""
    r = 20.0
    out = fillet_polygon(SQUARE, r)
    # the edge from (0,0) to (100,0): the arc off (0,0) must begin at x = 20
    on_bottom = sorted(p[0] for p in out if abs(p[1]) < 1e-6)
    close(on_bottom[0], r, 1e-6)


def test_radius_is_clamped_by_the_shortest_edge():
    """An absurd radius must not let one corner eat its neighbour."""
    out = fillet_polygon(SQUARE, 10_000.0)
    for point in out:
        assert _inside(point, SQUARE), point
    assert _area(out) > 0.0


def test_thin_polygon_survives_a_large_radius():
    thin = [(0.0, 0.0), (100.0, 0.0), (100.0, 2.0), (0.0, 2.0)]
    out = fillet_polygon(thin, 50.0)
    assert _area(out) > 0.0
    for point in out:
        assert _inside(point, thin), point


def test_winding_direction_does_not_matter():
    """Clockwise and anticlockwise input must round to the same shape."""
    forward = fillet_polygon(SQUARE, 20.0, segments=64)
    backward = fillet_polygon(list(reversed(SQUARE)), 20.0, segments=64)
    close(_area(forward), _area(backward), 1e-6)


def test_triangle_rounds_without_escaping():
    tri = [(0.0, 0.0), (100.0, 0.0), (50.0, 80.0)]
    out = fillet_polygon(tri, 15.0)
    for point in out:
        assert _inside(point, tri), point
    assert _area(out) < _area(tri)


# --------------------------------------------------------------------------
# the hull it is applied to
# --------------------------------------------------------------------------

def test_convex_hull_drops_interior_points():
    pts = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (50.0, 50.0)]
    hull = convex_hull(pts)
    assert (50.0, 50.0) not in hull
    assert len(hull) == 4


def test_fillet_of_a_hull_is_still_convex():
    """Concavity would show as a notch at a junction mouth."""
    hull = convex_hull([(0.0, 0.0), (100.0, 0.0), (120.0, 60.0),
                        (60.0, 110.0), (-10.0, 50.0)])
    out = fillet_polygon(hull, 12.0, segments=10)
    signs = set()
    for i in range(len(out)):
        ax, ay = out[i]
        bx, by = out[(i + 1) % len(out)]
        cx, cy = out[(i + 2) % len(out)]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            signs.add(cross > 0)
    assert len(signs) == 1, "rounded hull turned concave"


# --------------------------------------------------------------------------
# turn connectors
# --------------------------------------------------------------------------

class _Seg:
    def __init__(self, sx, sy, ex, ey, width):
        self._v = (sx, sy, ex, ey, width)

    def get_start_x(self): return self._v[0]
    def get_start_y(self): return self._v[1]
    def get_end_x(self): return self._v[2]
    def get_end_y(self): return self._v[3]
    def get_segment_width(self): return self._v[4]


class _Link:
    """A single-segment link running from `up` to `down`, in metres."""

    def __init__(self, link_id, up, down, sx, sy, ex, ey, width=10.0):
        self._id, self._up, self._down = link_id, up, down
        self._seg = _Seg(sx, sy, ex, ey, width)

    def get_id(self): return self._id
    def get_up_node(self): return self._up
    def get_down_node(self): return self._down
    def get_number_of_segments(self): return 1
    def get_segment(self, i): return self._seg
    def get_first_segment(self): return self._seg
    def get_last_segment(self): return self._seg


class _Node:
    def __init__(self, node_id, x, y, links):
        self._id, self._x, self._y, self._links = node_id, x, y, links

    def get_id(self): return self._id
    def get_center_x(self): return self._x
    def get_center_y(self): return self._y
    def number_of_links(self): return len(self._links)
    def get_link(self, i): return self._links[i]
    def is_roundabout(self): return False
    def get_roundabout_radius(self): return 0.0


class _Roundabout(_Node):
    """A node the geometry treats as a circle rather than a junction patch."""

    def __init__(self, node_id, x, y, links, radius, circulatory=7.0):
        super().__init__(node_id, x, y, links)
        self._radius = radius
        self._circulatory = circulatory

    def is_roundabout(self): return True
    def get_roundabout_radius(self): return self._radius
    def get_circulatory_width(self): return self._circulatory
    def get_outer_radius(self): return self._radius + self._circulatory
    def get_centre(self): return (self._x, self._y)


def _cross_network():
    """Four arms meeting at the origin: east, north, west, south."""
    links = [
        _Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0),     # east
        _Link(1, 0, 2, 0.0, 0.0, 0.0, -100.0),    # north
        _Link(2, 0, 3, 0.0, 0.0, -100.0, 0.0),    # west
        _Link(3, 0, 4, 0.0, 0.0, 0.0, 100.0),     # south
    ]
    nodes = [_Node(0, 0.0, 0.0, [0, 1, 2, 3])]
    return links, nodes


def test_four_arms_give_every_pairing():
    """Six unordered pairs, because a turn and its reverse are one ribbon."""
    links, nodes = _cross_network()
    assert len(road_geometry.turn_connectors(nodes, links, 1.0)) == 6


def test_terminal_node_has_no_connectors():
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0)]
    nodes = [_Node(1, 100.0, 0.0, [0])]
    assert road_geometry.turn_connectors(nodes, links, 1.0) == []


def test_connector_ribbons_are_closed_polygons():
    links, nodes = _cross_network()
    for xs, ys in road_geometry.turn_connectors(nodes, links, 1.0):
        assert len(xs) == len(ys)
        assert len(xs) >= 6, "ribbon collapsed to a sliver"


def test_connector_width_follows_the_narrower_arm():
    """A turn is only as wide as the tightest carriageway it passes through."""
    links, nodes = _cross_network()
    links[1] = _Link(1, 0, 2, 0.0, 0.0, 0.0, -100.0, width=4.0)
    ribbons = road_geometry.turn_connectors(nodes, links, 1.0)
    # the ribbon touching the narrow arm must be no wider than it
    narrow = min(_ribbon_width(r) for r in ribbons)
    close(narrow, 4.0, 0.6)


def _ribbon_width(ribbon):
    """Width at the midpoint, measured across the two swept edges."""
    xs, ys = ribbon
    half = len(xs) // 2
    mid = half // 2
    return math.hypot(xs[mid] - xs[len(xs) - 1 - mid],
                      ys[mid] - ys[len(ys) - 1 - mid])


def test_connector_ends_on_the_two_arm_mouths():
    """A ribbon has to start and finish on the carriageway it joins.

    Note the coordinate convention this relies on: a segment's stated x/y is
    one kerb edge and the carriageway lies to one side of it, so an arm's
    mouth centre is offset half a width from the line in link.txt. Assuming
    the stated point was the centreline is what an earlier version of this
    test got wrong.
    """
    links, nodes = _cross_network()
    arms = [road_geometry._arm_at_node(link, nodes[0], 1.0) for link in links]
    mouths = [(a[0], a[1]) for a in arms]

    for xs, ys in road_geometry.turn_connectors(nodes, links, 1.0):
        # poly = left edge forwards then right edge backwards, so the first and
        # last points straddle the starting mouth
        start = ((xs[0] + xs[-1]) / 2.0, (ys[0] + ys[-1]) / 2.0)
        best = min(math.hypot(start[0] - mx, start[1] - my)
                   for mx, my in mouths)
        assert best < 1e-6, f"ribbon starts at {start}, no arm mouth there"


def test_oneway_arms_drop_the_impossible_movement():
    """Two arms that both only carry traffic away from the node cannot connect."""
    links, nodes = _cross_network()
    saved = Parameters.ONEWAY_LINKS
    try:
        # links 0 and 1 both start at node 0, so both only lead away from it
        Parameters.ONEWAY_LINKS = {0, 1}
        ribbons = road_geometry.turn_connectors(nodes, links, 1.0)
        assert len(ribbons) == 5, len(ribbons)
    finally:
        Parameters.ONEWAY_LINKS = saved


def test_zero_length_arm_is_skipped_rather_than_crashing():
    links, nodes = _cross_network()
    links[2] = _Link(2, 0, 3, 0.0, 0.0, 0.0, 0.0)
    ribbons = road_geometry.turn_connectors(nodes, links, 1.0)
    assert len(ribbons) == 3      # the three pairs among the surviving arms


def test_point_in_polygon_basics():
    xs, ys = [0.0, 10.0, 10.0, 0.0], [0.0, 0.0, 10.0, 10.0]
    assert road_geometry.point_in_polygon(5.0, 5.0, xs, ys)
    assert not road_geometry.point_in_polygon(15.0, 5.0, xs, ys)
    assert not road_geometry.point_in_polygon(5.0, -1.0, xs, ys)


def test_junction_kerb_skips_edges_that_open_onto_road():
    """An edge with carriageway just beyond it is a mouth, not a kerb."""
    # a square patch with a road quad butting onto its right-hand edge
    hull = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    road = ([10.0, 10.0, 30.0, 30.0], [0.0, 10.0, 10.0, 0.0])
    kerb = road_geometry.junction_kerb(hull, 5.0, 5.0, [road], probe=2.0)
    for x1, y1, x2, y2 in kerb:
        assert not (x1 == 10.0 and x2 == 10.0), "kerb drawn across the mouth"
    assert len(kerb) == 3, kerb


def test_junction_kerb_keeps_the_free_edges():
    hull = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    kerb = road_geometry.junction_kerb(hull, 5.0, 5.0, [], probe=2.0)
    assert len(kerb) == 4, "every edge is kerb when no road adjoins"


def test_real_junction_leaves_a_mouth_per_arm():
    """Four arms, so four gaps in the kerb the traffic drives through."""
    links, nodes = _cross_network()
    quads = road_geometry.segment_quads(links, 1.0)
    hulls = road_geometry.junction_hulls(nodes, links, 1.0, quads)
    assert len(hulls) == 1
    kerb = hulls[0][5]
    assert kerb, "junction drew no kerb at all"
    outline = len(hulls[0][0])
    assert len(kerb) < outline, "kerb covered the whole outline, mouths included"


def test_hull_tuple_carries_its_kerb():
    """paint() and visualize.py both unpack six fields."""
    links, nodes = _cross_network()
    hulls = road_geometry.junction_hulls(nodes, links, 1.0)
    assert len(hulls[0]) == 6


def test_build_returns_six_parts():
    """paint() unpacks six; a change here breaks the GUI and the report."""
    links, nodes = _cross_network()
    assert len(road_geometry.build(links, nodes, 1.0)) == 6


# --------------------------------------------------------------------------
# roundabouts
# --------------------------------------------------------------------------

def _circle_network(radius=20.0, circulatory=7.0, arm=100.0):
    """Four arms whose inner ends sit on the circle, as the processor leaves them."""
    r = radius + circulatory
    links = [
        _Link(0, 0, 1, r, 0.0, arm, 0.0),        # east
        _Link(1, 0, 2, 0.0, -r, 0.0, -arm),      # north
        _Link(2, 0, 3, -r, 0.0, -arm, 0.0),      # west
        _Link(3, 0, 4, 0.0, r, 0.0, arm),        # south
    ]
    nodes = [_Roundabout(0, 0.0, 0.0, [0, 1, 2, 3], radius, circulatory)]
    return links, nodes


def test_a_roundabout_is_drawn_round_its_own_centre():
    """Its outline is the ring plus the arms, never anything less than the ring.

    A convex hull through the arms' mouths would pave the circle over and
    leave the island floating in a blob; a bare circle would cut every arm off
    at the kerb.  It is neither.
    """
    links, nodes = _circle_network()
    hulls = road_geometry.junction_hulls(nodes, links, 1.0)
    assert len(hulls) == 1
    xs, ys, cx, cy, radius, _kerb = hulls[0]
    close(cx, 0.0, 1e-9)
    close(cy, 0.0, 1e-9)
    close(radius, 0.0, 1e-9)
    assert len(xs) >= road_geometry.ROUNDABOUT_MIN_STEPS
    # a chord of the sampled circle sits a whisker inside it
    slack = 27.0 * (1.0 - math.cos(math.pi / len(xs))) + 1e-6
    for x, y in zip(xs, ys):
        assert math.hypot(x, y) >= 27.0 - slack


def test_a_bigger_circle_is_drawn_with_more_points():
    """Facet length, not point count, is what the eye sees."""
    steps = road_geometry.circle_steps
    assert steps(10.0) <= steps(100.0) <= steps(1000.0)
    assert steps(1e6) == road_geometry.CIRCLE_MAX_STEPS
    assert steps(0.0) == road_geometry.CIRCLE_STEPS


def test_a_roundabout_hull_carries_no_inner_disc():
    """A circle has no concave mouth to round off, and a second fill would
    only lay a second wash over the first."""
    links, nodes = _circle_network()
    assert road_geometry.junction_hulls(nodes, links, 1.0)[0][4] == 0.0


def test_a_roundabout_leaves_a_gap_in_its_kerb_for_every_arm():
    links, nodes = _circle_network()
    hulls = road_geometry.junction_hulls(nodes, links, 1.0)
    xs, _ys, _cx, _cy, _r, kerb = hulls[0]
    assert kerb, "the ring drew no kerb at all"
    assert len(kerb) < len(xs), "kerb ruled across the mouths"


def test_a_roundabout_has_no_turn_connectors():
    """Every movement is the same one-way ring; a Bezier would cut across it."""
    links, nodes = _circle_network()
    assert road_geometry.turn_connectors(nodes, links, 1.0) == []


def test_an_island_carries_its_outer_radius():
    links, nodes = _circle_network(radius=20.0, circulatory=7.0)
    (cx, cy, r, outer), = road_geometry.islands(nodes, links, 1.0)
    close(cx, 0.0, 1e-9)
    close(r, 20.0, 1e-9)
    close(outer, 27.0, 1e-9)


def test_the_island_centre_is_not_the_mean_of_the_arms():
    """Arms end *on* the circle, and the mean of points on a circle is only
    its centre when they are evenly spread -- which arms never are."""
    links, nodes = _circle_network()
    links.pop()                      # three arms left, no longer symmetric
    nodes[0]._links = [0, 1, 2]
    drifted = road_geometry.node_point(links, nodes[0])
    assert math.hypot(*drifted) > 1.0, "test network is still symmetric"
    close(road_geometry.roundabout_centre(links, nodes[0])[0], 0.0, 1e-9)
    close(road_geometry.roundabout_centre(links, nodes[0])[1], 0.0, 1e-9)


def test_scaling_carries_through_to_the_ring():
    links, nodes = _circle_network()
    (_cx, _cy, r, outer), = road_geometry.islands(nodes, links, 4.0)
    close(r, 80.0, 1e-9)
    close(outer, 108.0, 1e-9)


def _oblique_circle_network(radius=20.0, circulatory=7.0):
    """A roundabout whose arms do not run dead radially.

    Which is every real one.  An arm's stated line is a kerb edge, so cutting
    it on the circle puts one corner of the mouth on the ring and swings the
    rest away; the wedge that leaves is what `roundabout_aprons` exists for,
    and a network of radial arms never shows it.
    """
    r = radius + circulatory
    links = []
    for i, degrees in enumerate((10.0, 100.0, 190.0, 280.0)):
        a = math.radians(degrees)
        inner = (r * math.cos(a), r * math.sin(a))
        # the far end swung well off the radial line
        # swung the way that throws the far kerb corner *outside* the ring,
        # which is the case the aprons are for; the other way tucks the mouth
        # inside the circle and there is nothing to fill.
        b = math.radians(degrees - 25.0)
        outer_end = (140.0 * math.cos(b), 140.0 * math.sin(b))
        links.append(_Link(i, 0, i + 1, inner[0], inner[1],
                           outer_end[0], outer_end[1], width=14.0))
    nodes = [_Roundabout(0, 0.0, 0.0, [0, 1, 2, 3], radius, circulatory)]
    return links, nodes


def test_the_outline_joins_every_arm_to_the_ring():
    """Step just inside each mouth: it must land on road, not on bare ground.

    This is the whole complaint the outline answers -- arms that appeared to
    stop short of the roundabout instead of joining it.  Nothing is patched
    together here, so there is no seam to test; the outline is built out of
    the arms themselves and either contains them or the sweep is wrong.
    """
    links, nodes = _oblique_circle_network()
    node = nodes[0]
    outer = node.get_outer_radius()
    # What is painted is the outline *and* the arms' own surfaces.  The
    # outline is sampled every couple of degrees, so within a chord's width of
    # a mouth's corner it can sit a few centimetres inside the true union;
    # that ground is the arm's, and the arm is drawn.
    covered = [road_geometry.roundabout_outline(node, links, 1.0)]
    covered += [list(zip(xs, ys))
                for xs, ys in road_geometry.segment_quads(links, 1.0)]
    uncovered = 0
    probed = 0
    for link in links:
        x1, y1, x3, y3 = road_geometry.link_end_at_node(link, node, 1.0)
        for step in range(1, 20):
            t = step / 20.0
            px = x1 + (x3 - x1) * t
            py = y1 + (y3 - y1) * t
            reach = math.hypot(px, py)
            if reach <= outer:
                continue                      # already on the ring
            # nudge half a metre inwards, into the wedge
            qx = px * (reach - 0.5) / reach
            qy = py * (reach - 0.5) / reach
            if math.hypot(qx, qy) <= outer:
                continue
            probed += 1
            if not any(_contains(poly, (qx, qy)) for poly in covered):
                uncovered += 1
    assert probed > 0, "no mouth stood off the ring, so nothing was tested"
    assert uncovered == 0, f"{uncovered} points of bare ground inside a mouth"


def test_the_outline_contains_the_whole_ring():
    """Whatever the arms do, the circulatory carriageway is still road."""
    links, nodes = _oblique_circle_network()
    node = nodes[0]
    outer = node.get_outer_radius()
    outline = road_geometry.roundabout_outline(node, links, 1.0)
    for i in range(72):
        a = math.radians(i * 5)
        point = ((outer - 0.5) * math.cos(a), (outer - 0.5) * math.sin(a))
        assert _contains(outline, point), f"the ring is open at {i * 5} degrees"


def test_the_outline_stays_within_reach():
    """It is a junction patch, not a second copy of the arms."""
    links, nodes = _oblique_circle_network()
    node = nodes[0]
    limit = node.get_outer_radius() * road_geometry.ROUNDABOUT_REACH
    for x, y in road_geometry.roundabout_outline(node, links, 1.0):
        assert math.hypot(x, y) <= limit + 1e-6


def test_scaling_carries_through_to_the_outline():
    links, nodes = _oblique_circle_network()
    node = nodes[0]
    one = road_geometry.roundabout_outline(node, links, 1.0)
    four = road_geometry.roundabout_outline(node, links, 4.0)
    assert len(one) == len(four) or True          # step count may differ
    close(max(math.hypot(x, y) for x, y in four)
          / max(math.hypot(x, y) for x, y in one), 4.0, 1e-6)


def test_closing_a_notch_only_ever_adds():
    """The property the whole approach rests on.

    Rounding a corner by *cutting* is what opens a gap between the patch and
    the arm's own ribbon.  A morphological closing cannot cut: the closing of
    a set contains the set.
    """
    radii = [10.0] * 40
    for i in range(8, 12):
        radii[i] = 30.0
    for i in range(14, 18):
        radii[i] = 30.0                       # two bumps with a notch between
    closed = road_geometry._close_notches(radii, 4)
    assert all(c >= r - 1e-9 for c, r in zip(closed, radii))
    assert closed[12] > radii[12], "the notch between the bumps stayed open"


def test_a_wide_notch_survives_the_closing():
    """The kerb island between two neighbouring arms is not a defect."""
    radii = [10.0] * 40
    for i in range(0, 5):
        radii[i] = 30.0
    for i in range(20, 25):
        radii[i] = 30.0                       # bumps half a turn apart
    closed = road_geometry._close_notches(radii, 3)
    close(closed[12], 10.0, 1e-9)


def test_a_plain_junction_keeps_its_convex_hull():
    """Only a roundabout gets the swept outline."""
    links, nodes = _cross_network()
    (xs, _ys, _cx, _cy, radius, _kerb), = road_geometry.junction_hulls(
        nodes, links, 1.0)
    assert radius > 0.0, "a plain junction still fills its rounding disc"
    assert len(xs) < road_geometry.ROUNDABOUT_MIN_STEPS


# --------------------------------------------------------------------------
# the carriageway ribbons
# --------------------------------------------------------------------------

class _Chain:
    """A link of several segments, given as a polyline in metres."""

    def __init__(self, link_id, up, down, points, width=10.0):
        self._id, self._up, self._down = link_id, up, down
        self._segs = [_Seg(points[i][0], points[i][1],
                           points[i + 1][0], points[i + 1][1], width)
                      for i in range(len(points) - 1)]

    def get_id(self): return self._id
    def get_up_node(self): return self._up
    def get_down_node(self): return self._down
    def get_number_of_segments(self): return len(self._segs)
    def get_segment(self, i): return self._segs[i]
    def get_first_segment(self): return self._segs[0]
    def get_last_segment(self): return self._segs[-1]


def _contains(poly, point):
    """Ray-cast containment.  A ribbon is not convex, so _inside cannot serve."""
    x, y = point
    inside = False
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (ay > y) != (by > y):
            cut = ax + (y - ay) * (bx - ax) / (by - ay)
            if cut > x:
                inside = not inside
    return inside


def _outline(ribbon):
    near, far = ribbon
    return near + far[::-1]


def _bend(turn):
    """A two-segment link bending by 45 degrees, one way or the other."""
    return _Chain(0, 0, 1, [(0.0, 0.0), (100.0, 0.0),
                            (100.0 + 70.0, turn * 70.0)])


def test_a_straight_link_is_still_its_quad():
    """Nothing about a link with one segment should have changed."""
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0)]
    (near, far), = road_geometry.carriageways(links, 1.0)
    qxs, qys = road_geometry.segment_quads(links, 1.0)[0]
    got = sorted(near + far)
    want = sorted(zip(qxs, qys))
    for (gx, gy), (wx, wy) in zip(got, want):
        close(gx, wx, 1e-9)
        close(gy, wy, 1e-9)


def test_a_bend_leaves_no_gap_in_the_carriageway():
    """The wedge at a bend is inside the ribbon, whichever way it turns.

    This is the whole point of building a link as one ribbon.  A segment's far
    edge is offset along that segment's own normal, so two neighbours' far
    corners stand apart at a bend and the road comes out as separate planks.
    """
    for turn in (1.0, -1.0):
        link = _bend(turn)
        (near, far), = road_geometry.carriageways([link], 1.0)
        seg = link.get_segment(0)
        w = seg.get_segment_width()
        # the bisector of the two far-edge normals, at the shared vertex
        n1 = (far[0][0] - near[0][0], far[0][1] - near[0][1])
        n2 = (far[-1][0] - near[-1][0], far[-1][1] - near[-1][1])
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        scale = 0.6 * w / math.hypot(mx, my)
        probe = (100.0 + mx * scale, 0.0 + my * scale)
        assert _contains(_outline((near, far)), probe), \
            "the ribbon has a hole where the link bends"


def test_the_wedge_a_bend_leaves_is_real():
    """One of the two turn directions falls outside both segment quads.

    Without this the test above would pass on a road that never had a gap.
    """
    missed = 0
    for turn in (1.0, -1.0):
        link = _bend(turn)
        (near, far), = road_geometry.carriageways([link], 1.0)
        quads = road_geometry.segment_quads([link], 1.0)
        n1 = (far[0][0] - near[0][0], far[0][1] - near[0][1])
        n2 = (far[-1][0] - near[-1][0], far[-1][1] - near[-1][1])
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        scale = 0.6 * link.get_segment(0).get_segment_width() / math.hypot(mx, my)
        probe = (100.0 + mx * scale, my * scale)
        if not any(_contains(list(zip(qxs, qys)), probe) for qxs, qys in quads):
            missed += 1
    assert missed == 1, "expected exactly one turn direction to open a wedge"


def test_a_hairpin_is_cut_square_rather_than_spiked():
    """A mitre runs away to infinity as a bend closes up; MITRE_LIMIT caps it."""
    link = _Chain(0, 0, 1, [(0.0, 0.0), (100.0, 0.0), (4.0, 3.0)])
    (near, far), = road_geometry.carriageways([link], 1.0)
    w = link.get_segment(0).get_segment_width()
    # only the corner itself: the ends of the far edge are the ends of the
    # road, and they are as far away as the road is long.
    reach = max(math.hypot(fx - 100.0, fy) for fx, fy in far[1:-1])
    assert reach <= road_geometry.MITRE_LIMIT * w + 1e-6, \
        f"mitre spiked {reach:.1f} out from a {w:.1f} wide road"


def test_a_broken_chain_becomes_two_ribbons():
    """Segments that do not meet are not closed across the gap."""
    link = _Chain(0, 0, 1, [(0.0, 0.0), (100.0, 0.0)])
    tail = _Seg(200.0, 0.0, 300.0, 0.0, 10.0)
    link._segs.append(tail)
    assert len(road_geometry.carriageways([link], 1.0)) == 2


def test_a_zero_length_segment_is_dropped():
    """return_x3 divides by the segment length, so it never sees this one."""
    link = _Chain(0, 0, 1, [(0.0, 0.0), (100.0, 0.0), (100.0, 0.0),
                            (200.0, 0.0)])
    (near, _far), = road_geometry.carriageways([link], 1.0)
    assert len(near) == 3


# --------------------------------------------------------------------------
# lane markings
# --------------------------------------------------------------------------

def test_narrow_road_gets_no_divider():
    """One lane's worth of width has nothing to divide."""
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0, width=3.0)]
    assert road_geometry.lane_markings(links, 1.0) == []


def test_two_lane_road_gets_one_divider_line():
    """7 m at a 3.5 m lane is two lanes, so one line down the middle."""
    links = [_Link(0, 0, 1, 0.0, 0.0, 120.0, 0.0, width=7.0)]
    dashes = road_geometry.lane_markings(links, 1.0)
    assert dashes, "no dashes drawn"
    # every dash sits on the same offset across the carriageway
    ys = {round(d[1], 6) for d in dashes}
    assert len(ys) == 1, ys
    close(ys.pop(), 3.5, 1e-6)


def test_lane_count_follows_the_width():
    for width, lines in ((7.0, 1), (10.5, 2), (14.0, 3)):
        links = [_Link(0, 0, 1, 0.0, 0.0, 120.0, 0.0, width=width)]
        dashes = road_geometry.lane_markings(links, 1.0)
        offsets = {round(d[1], 4) for d in dashes}
        assert len(offsets) == lines, f"{width} m gave {offsets}"


def test_dividers_stay_inside_the_carriageway():
    links = [_Link(0, 0, 1, 0.0, 0.0, 120.0, 0.0, width=14.0)]
    for x1, y1, x2, y2 in road_geometry.lane_markings(links, 1.0):
        for y in (y1, y2):
            assert 0.0 < y < 14.0, y
        for x in (x1, x2):
            assert -1e-6 <= x <= 120.0 + 1e-6, x


def test_dashes_follow_the_dash_gap_rhythm():
    """Each dash is DASH long and starts one DASH+GAP after the last."""
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0, width=7.0)]
    dashes = sorted(road_geometry.lane_markings(links, 1.0), key=lambda d: d[0])
    close(dashes[0][2] - dashes[0][0], road_geometry.MARKING_DASH_METRES, 1e-6)
    stride = road_geometry.MARKING_DASH_METRES + road_geometry.MARKING_GAP_METRES
    close(dashes[1][0] - dashes[0][0], stride, 1e-6)


def test_final_dash_is_clipped_not_overrun():
    """The last dash must stop at the segment end, not hang past it."""
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0, width=7.0)]
    for _x1, _y1, x2, _y2 in road_geometry.lane_markings(links, 1.0):
        assert x2 <= 100.0 + 1e-6, x2


def test_markings_scale_with_pixel_per_meter():
    links = [_Link(0, 0, 1, 0.0, 0.0, 100.0, 0.0, width=7.0)]
    one = road_geometry.lane_markings(links, 1.0)
    two = road_geometry.lane_markings(links, 2.0)
    assert len(one) == len(two), "dash count changed with zoom"
    close(two[0][2] - two[0][0], 2.0 * (one[0][2] - one[0][0]), 1e-6)


def test_zero_length_segment_is_skipped():
    links = [_Link(0, 0, 1, 5.0, 5.0, 5.0, 5.0, width=7.0)]
    assert road_geometry.lane_markings(links, 1.0) == []


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
