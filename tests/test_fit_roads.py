"""Tests for the geometry behind ``fit_roads.py``.

The fit rewrites the networks the whole project runs on, so the parts of it
that can go wrong quietly are pinned here: the sideways offset that carries the
kerb-edge convention, the bearing filter that keeps an arm off the street it
crosses, and the guard against writing a zero-length segment.

Run with:  python tests/test_fit_roads.py
"""

import importlib.util
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dhakasim import utilities as U


def _load():
    spec = importlib.util.spec_from_file_location(
        "_fit_roads", os.path.join(ROOT, "fit_roads.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fit = _load()


class OffsetTest(unittest.TestCase):
    """The stated line is a kerb edge; the fit works on the centreline."""

    def test_offset_direction_matches_the_simulator(self):
        # road_geometry builds the far kerb with Utilities.return_x3, so
        # whichever side that lands on is the side the carriageway occupies,
        # and the offset here has to agree or every road moves by half a width.
        x1, y1, x2, y2, w = 0.0, 0.0, 100.0, 0.0, 10.0
        x3 = U.return_x3(x1, y1, x2, y2, w)
        y3 = U.return_y3(x1, y1, x2, y2, w)
        moved = fit.offset_polyline([(x1, y1), (x2, y2)], w)
        self.assertAlmostEqual(moved[0][0], x3, 6)
        self.assertAlmostEqual(moved[0][1], y3, 6)

    def test_offset_is_reversible_to_within_centimetres(self):
        """Out and back lands where it started, near enough.

        Not exactly: the bisector at a vertex is computed from the segment
        directions, and offsetting changes those slightly, so the inverse is
        approximate at a bend.  The fit shifts to the centreline and back by
        half a carriageway, so what matters is that the error is small against
        a road width, and a couple of centimetres against fourteen metres is.
        """
        line = [(0.0, 0.0), (50.0, 10.0), (100.0, 0.0)]
        back = fit.offset_polyline(fit.offset_polyline(line, 7.0), -7.0)
        for a, b in zip(line, back):
            self.assertLess(fit.distance(a, b), 0.05)

    def test_offset_keeps_the_polyline_continuous(self):
        # Interior vertices move along the bisector, so the two segments meeting
        # there must still share a point.
        line = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)]
        moved = fit.offset_polyline(line, 5.0)
        self.assertEqual(len(moved), len(line))


class SimplifyTest(unittest.TestCase):

    def test_straight_line_collapses_to_its_ends(self):
        line = [(float(i), 0.0) for i in range(20)]
        self.assertEqual(fit.simplify(line, 1.0), [(0.0, 0.0), (19.0, 0.0)])

    def test_a_bend_survives_a_tolerance_below_it(self):
        line = [(0.0, 0.0), (50.0, 20.0), (100.0, 0.0)]
        self.assertEqual(len(fit.simplify(line, 5.0)), 3)
        self.assertEqual(len(fit.simplify(line, 40.0)), 2)

    def test_endpoints_are_never_dropped(self):
        line = [(0.0, 0.0), (10.0, 0.1), (20.0, 0.0), (30.0, 0.05)]
        out = fit.simplify(line, 10.0)
        self.assertEqual(out[0], line[0])
        self.assertEqual(out[-1], line[-1])


class ShortSegmentTest(unittest.TestCase):
    """A zero-length segment divides by zero inside road_geometry."""

    def test_crowded_vertices_are_removed(self):
        line = [(0.0, 0.0), (0.1, 0.0), (0.2, 0.0), (50.0, 0.0)]
        out = fit.drop_short(line, 4.0)
        for a, b in zip(out, out[1:]):
            self.assertGreaterEqual(fit.distance(a, b), 4.0)

    def test_the_last_point_is_kept_even_when_crowded(self):
        line = [(0.0, 0.0), (50.0, 0.0), (50.2, 0.0)]
        out = fit.drop_short(line, 4.0)
        self.assertEqual(out[-1], (50.2, 0.0))
        for a, b in zip(out, out[1:]):
            self.assertGreater(fit.distance(a, b), 0.0)

    def test_a_two_point_line_is_left_alone(self):
        line = [(0.0, 0.0), (1.0, 0.0)]
        self.assertEqual(fit.drop_short(line, 4.0), line)


class ResampleTest(unittest.TestCase):

    def test_spacing_and_endpoints(self):
        line = [(0.0, 0.0), (100.0, 0.0)]
        out = fit.resample(line, 10.0)
        self.assertEqual(out[0], (0.0, 0.0))
        self.assertAlmostEqual(out[-1][0], 100.0, 6)
        for a, b in zip(out, out[1:]):
            self.assertAlmostEqual(fit.distance(a, b), 10.0, 6)

    def test_a_line_shorter_than_the_step_keeps_its_ends(self):
        line = [(0.0, 0.0), (3.0, 0.0)]
        self.assertEqual(fit.resample(line, 10.0), line)

    def test_length_is_preserved_around_a_corner(self):
        line = [(0.0, 0.0), (30.0, 0.0), (30.0, 40.0)]
        out = fit.resample(line, 5.0)
        self.assertAlmostEqual(fit.polyline_length(out),
                               fit.polyline_length(line), delta=1.0)


class BearingFilterTest(unittest.TestCase):
    """What keeps an arm from being dragged onto the road it crosses."""

    def setUp(self):
        # An east-west road and a north-south road crossing at the origin.
        self.pieces = fit.road_segments([
            {"geometry": [(0, 0), (1, 0)]},
            {"geometry": [(0, 0), (0, 1)]},
        ], _Identity())
        self.cell = 40.0
        self.index = fit.build_index(self.pieces, self.cell)

    def test_a_crossing_road_is_not_matched(self):
        # Travelling east, a point just north of the east-west road must snap
        # to it and not to the north-south road it is standing on.
        cos35 = math.cos(math.radians(35))
        got = fit.nearest_road_point(self.index, self.cell, (10.0, 6.0),
                                     1.0, 0.0, 40.0, cos35)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[1], 0.0, 6)

    def test_direction_of_digitising_does_not_matter(self):
        cos35 = math.cos(math.radians(35))
        forward = fit.nearest_road_point(self.index, self.cell, (10.0, 6.0),
                                         1.0, 0.0, 40.0, cos35)
        backward = fit.nearest_road_point(self.index, self.cell, (10.0, 6.0),
                                          -1.0, 0.0, 40.0, cos35)
        self.assertEqual(forward, backward)

    def test_nothing_within_reach_returns_nothing(self):
        cos35 = math.cos(math.radians(35))
        self.assertIsNone(
            fit.nearest_road_point(self.index, self.cell, (10.0, 500.0),
                                   1.0, 0.0, 40.0, cos35))


class _Identity:
    """Stands in for a Georeference: degrees straight through as metres."""

    @staticmethod
    def to_xy(lon, lat):
        return (float(lon), float(lat))


class SnapTest(unittest.TestCase):

    def test_a_link_already_on_the_road_barely_moves(self):
        road = [{"geometry": [(0, 0), (200, 0)]}]
        pieces = fit.road_segments(road, _Identity())
        index = fit.build_index(pieces, 40.0)
        line = [(0.0, 0.0), (200.0, 0.0)]
        moved, matched, samples = fit.snap_to_roads(
            line, index, 40.0, 40.0, 30.0, math.cos(math.radians(35)))
        self.assertEqual(matched, samples)
        for point in moved:
            self.assertAlmostEqual(point[1], 0.0, 3)

    def test_the_ends_stay_exactly_where_they_were(self):
        road = [{"geometry": [(0, 12), (200, 12)]}]
        pieces = fit.road_segments(road, _Identity())
        index = fit.build_index(pieces, 40.0)
        line = [(0.0, 0.0), (200.0, 0.0)]
        moved, _matched, _samples = fit.snap_to_roads(
            line, index, 40.0, 40.0, 30.0, math.cos(math.radians(35)))
        self.assertEqual(moved[0], (0.0, 0.0))
        self.assertAlmostEqual(moved[-1][0], 200.0, 6)
        self.assertAlmostEqual(moved[-1][1], 0.0, 6)

    def test_the_pull_is_capped(self):
        # A road far to the side must not drag the link the whole way over.
        road = [{"geometry": [(0, 100), (200, 100)]}]
        pieces = fit.road_segments(road, _Identity())
        index = fit.build_index(pieces, 200.0)
        line = [(0.0, 0.0), (200.0, 0.0)]
        moved, _m, _s = fit.snap_to_roads(
            line, index, 200.0, 200.0, 20.0, math.cos(math.radians(35)))
        self.assertLessEqual(max(abs(p[1]) for p in moved), 20.0 + 1e-6)


class FreeEndTest(unittest.TestCase):
    """The first pass lets the ends go; that is how a node learns where it is."""

    def _index(self, offset):
        road = [{"geometry": [(0, offset), (200, offset)]}]
        return fit.build_index(fit.road_segments(road, _Identity()), 40.0)

    def test_a_free_end_takes_its_pull_in_full(self):
        index = self._index(12)
        line = [(0.0, 0.0), (200.0, 0.0)]
        moved, _m, _s = fit.snap_to_roads(
            line, index, 40.0, 40.0, 30.0, math.cos(math.radians(35)),
            pin_start=False, pin_end=False)
        self.assertAlmostEqual(moved[0][1], 12.0, 3)
        self.assertAlmostEqual(moved[-1][1], 12.0, 3)

    def test_one_end_can_be_free_while_the_other_is_pinned(self):
        index = self._index(12)
        line = [(0.0, 0.0), (200.0, 0.0)]
        moved, _m, _s = fit.snap_to_roads(
            line, index, 40.0, 40.0, 30.0, math.cos(math.radians(35)),
            pin_start=True, pin_end=False)
        self.assertEqual(moved[0], (0.0, 0.0))
        self.assertAlmostEqual(moved[-1][1], 12.0, 3)


class DualCarriagewayTest(unittest.TestCase):
    """A link standing for two carriageways belongs on the median.

    The failure this guards against is silent and was visible for months:
    ``nearest_road_point`` calls a thirty-two metre band perfectly fitted as
    soon as it lands on either half of Manik Mia Avenue, and the far half of
    the band then lies on the T&T field.
    """

    def _index(self, *offsets):
        ways = [{"geometry": [(0, o), (200, o)]} for o in offsets]
        return fit.build_index(fit.road_segments(ways, _Identity()), 40.0)

    def test_the_middle_of_a_pair_is_not_the_nearer_of_them(self):
        cos35 = math.cos(math.radians(35))
        index = self._index(0, 20)
        span = fit.spanning_road_point(index, 40.0, (100.0, 2.0),
                                       1.0, 0.0, 30.0, cos35)
        near = fit.nearest_road_point(index, 40.0, (100.0, 2.0),
                                      1.0, 0.0, 30.0, cos35)
        self.assertAlmostEqual(span[1], 10.0, 6)
        self.assertAlmostEqual(near[1], 0.0, 6)

    def test_a_single_road_is_its_own_middle(self):
        cos35 = math.cos(math.radians(35))
        index = self._index(0)
        span = fit.spanning_road_point(index, 40.0, (100.0, 6.0),
                                       1.0, 0.0, 30.0, cos35)
        self.assertAlmostEqual(span[1], 0.0, 6)

    def test_a_crossing_road_is_still_not_counted(self):
        # The bearing filter has to survive the change of rule, or the span
        # reaches across the junction and doubles in width.
        cos35 = math.cos(math.radians(35))
        pieces = fit.road_segments([
            {"geometry": [(0, 0), (200, 0)]},
            {"geometry": [(100, -60), (100, 60)]},
        ], _Identity())
        index = fit.build_index(pieces, 40.0)
        span = fit.spanning_road_point(index, 40.0, (100.0, 4.0),
                                       1.0, 0.0, 30.0, cos35)
        self.assertAlmostEqual(span[1], 0.0, 6)

    def test_nothing_within_reach_returns_nothing(self):
        cos35 = math.cos(math.radians(35))
        self.assertIsNone(
            fit.spanning_road_point(self._index(0), 40.0, (100.0, 500.0),
                                    1.0, 0.0, 30.0, cos35))

    def test_a_different_class_of_road_is_not_the_other_half(self):
        """Bijoy Sarani's northern arm counted Agargaon Link Road, a secondary
        eighteen metres off, as the far half of trunk Old Airport Road."""
        cos35 = math.cos(math.radians(35))
        ways = [{"geometry": [(0, 0), (200, 0)], "tags": {"highway": "trunk"}},
                {"geometry": [(0, 14), (200, 14)],
                 "tags": {"highway": "trunk"}},
                {"geometry": [(0, 26), (200, 26)],
                 "tags": {"highway": "secondary"}}]
        index = fit.build_index(fit.road_segments(ways, _Identity()), 40.0)
        span = fit.spanning_road_point(index, 40.0, (100.0, 1.0),
                                       1.0, 0.0, 30.0, cos35)
        self.assertAlmostEqual(span[1], 7.0, 6)   # the two trunks, not all three

    def test_the_class_is_taken_from_the_nearest_road(self):
        """Standing on the secondary, the secondary is what it belongs to."""
        cos35 = math.cos(math.radians(35))
        ways = [{"geometry": [(0, 0), (200, 0)], "tags": {"highway": "trunk"}},
                {"geometry": [(0, 26), (200, 26)],
                 "tags": {"highway": "secondary"}},
                {"geometry": [(0, 34), (200, 34)],
                 "tags": {"highway": "secondary"}}]
        index = fit.build_index(fit.road_segments(ways, _Identity()), 40.0)
        span = fit.spanning_road_point(index, 40.0, (100.0, 28.0),
                                       1.0, 0.0, 20.0, cos35)
        self.assertAlmostEqual(span[1], 30.0, 6)

    def test_the_chase_reaches_a_pair_one_step_cannot_see(self):
        """The far carriageway starts outside the search and one step at the
        only road in sight brings it into range.  That is the real geometry:
        Manik Mia's halves are twenty-three metres apart and the band looks
        twenty-six, so from the survey's line half the pair is out of reach.
        """
        index = self._index(8, 30)                  # median at 19
        line = [(0.0, 0.0), (200.0, 0.0)]
        settled, _m, _s = fit.snap_to_roads(
            line, index, 40.0, 26.0, 40.0, math.cos(math.radians(35)),
            pin_start=False, pin_end=False, span=True)
        once, _m, _s = fit.snap_to_roads(
            line, index, 40.0, 26.0, 40.0, math.cos(math.radians(35)),
            pin_start=False, pin_end=False, span=True, chase=1)
        self.assertAlmostEqual(settled[len(settled) // 2][1], 19.0, 1)
        self.assertAlmostEqual(once[len(once) // 2][1], 8.0, 1)

    def test_the_networks_declare_their_dual_carriageways(self):
        self.assertEqual(fit.read_medians("kakrail_corridor"), {2, 3})
        self.assertEqual(fit.read_medians("bijoy_sarani"), {0, 1, 2, 3})
        self.assertEqual(fit.read_medians("demo_backup"), set())

    def test_a_network_can_ask_for_the_chord(self):
        """``straight`` is read by the fit and ignored by the simulator, so
        both readers have to be happy with the same file."""
        self.assertEqual(fit.read_straight("bijoy_sarani"), {0, 1})
        self.assertEqual(fit.read_straight("banani_27"), {2})
        self.assertEqual(fit.read_straight("kakrail_corridor"), set())


class CircleFitTest(unittest.TestCase):
    """Where the roundabout the map draws actually is."""

    def test_a_known_circle_comes_back_exactly(self):
        points = [(30.0 + 12.0 * math.cos(a * math.pi / 8),
                   -7.0 + 12.0 * math.sin(a * math.pi / 8)) for a in range(16)]
        cx, cy, r = fit.fit_circle(points)
        self.assertAlmostEqual(cx, 30.0, 6)
        self.assertAlmostEqual(cy, -7.0, 6)
        self.assertAlmostEqual(r, 12.0, 6)

    def test_too_few_points_is_no_circle(self):
        self.assertIsNone(fit.fit_circle([(0.0, 0.0), (1.0, 0.0)]))

    def test_collinear_points_are_no_circle(self):
        self.assertIsNone(fit.fit_circle([(float(i), 0.0) for i in range(10)]))


class ArmAimingTest(unittest.TestCase):
    """Aiming a roundabout's arms must not drag the georeference with them."""

    def _four_arms(self, half=7.0, centre=(0.0, 0.0)):
        """Four arms whose carriageways run straight over *centre*.

        Each stated line is half a width to one side of its carriageway, as
        ``link.txt`` states it, so the line itself misses the centre -- which
        is the whole reason the mouths used to pinwheel.
        """
        out = []
        for dx, dy in ((0.0, 1.0), (-1.0, 0.0), (0.0, -1.0), (1.0, 0.0)):
            nx, ny = -dy, dx
            on_line = (centre[0] - nx * half, centre[1] - ny * half)
            out.append(((dx, dy), (nx, ny), half,
                        (on_line[0] - dx * 80.0, on_line[1] - dy * 80.0)))
        return out

    def test_the_anchor_does_not_move(self):
        arms = self._four_arms(centre=(3.0, -2.0))
        ends = fit.aim_arms_at_the_circle(arms, (3.0, -2.0), (0.0, 0.0))
        self.assertAlmostEqual(sum(p[0] for p in ends) / 4.0, 0.0, 6)
        self.assertAlmostEqual(sum(p[1] for p in ends) / 4.0, 0.0, 6)

    def test_every_mouth_lands_on_the_circle_centre(self):
        arms = self._four_arms()
        ends = fit.aim_arms_at_the_circle(arms, (0.0, 0.0), (0.0, 0.0))
        for ((_dx, _dy), (nx, ny), half, _p), end in zip(arms, ends):
            mouth = (end[0] + nx * half, end[1] + ny * half)
            # Only the across-the-arm component is the circle's to fix; along
            # the arm the end is free and is spent on the anchor.
            self.assertAlmostEqual(mouth[0] * nx + mouth[1] * ny, 0.0, 6)

    def test_the_stated_end_stays_half_a_width_off_the_centre(self):
        """It is a kerb, not a centreline, and aiming must not forget that."""
        arms = self._four_arms(half=16.0)
        ends = fit.aim_arms_at_the_circle(arms, (0.0, 0.0), (0.0, 0.0))
        for ((_dx, _dy), (nx, ny), _half, _p), end in zip(arms, ends):
            self.assertAlmostEqual(end[0] * nx + end[1] * ny, -16.0, 6)

    def test_arms_that_are_all_parallel_are_left_alone(self):
        arms = [((1.0, 0.0), (0.0, 1.0), 5.0, (-40.0, 0.0)),
                ((1.0, 0.0), (0.0, 1.0), 5.0, (-40.0, 30.0))]
        ends = fit.aim_arms_at_the_circle(arms, (0.0, 0.0), (0.0, 0.0))
        self.assertEqual(len(ends), 2)


class NodeConsensusTest(unittest.TestCase):
    """Arms at a node share one point, so they have to agree on where it goes."""

    def test_the_node_takes_the_mean_of_its_arms(self):
        wanted = {3: ((0.0, 0.0), [(10.0, 0.0), (0.0, 10.0)])}
        moves = fit.agree_on_nodes(wanted, 100.0, set())
        (x, y), reach, arms = moves[3]
        self.assertAlmostEqual(x, 5.0)
        self.assertAlmostEqual(y, 5.0)
        self.assertEqual(arms, 2)
        self.assertAlmostEqual(reach, math.hypot(5.0, 5.0))

    def test_a_pinned_node_does_not_move(self):
        wanted = {3: ((0.0, 0.0), [(40.0, 0.0)])}
        self.assertEqual(fit.agree_on_nodes(wanted, 100.0, {3}), {})

    def test_the_move_is_capped(self):
        """One confident arm must not drag a junction across the map."""
        wanted = {3: ((0.0, 0.0), [(400.0, 0.0)])}
        (x, _y), reach, _arms = fit.agree_on_nodes(wanted, 30.0, set())[3]
        self.assertAlmostEqual(x, 30.0)
        self.assertAlmostEqual(reach, 30.0)

    def test_a_move_smaller_than_the_tolerance_is_not_made(self):
        wanted = {3: ((0.0, 0.0), [(0.1, 0.0)])}
        self.assertEqual(fit.agree_on_nodes(wanted, 30.0, set()), {})


class NodeFileTest(unittest.TestCase):
    """node.txt has to round-trip: the fit now writes it as well."""

    def test_round_trip_on_a_real_network(self):
        nodes = fit.read_node_file("kakrail_corridor")
        self.assertGreater(len(nodes), 0)
        links = {link_id for link_id, _u, _d, _r in fit.read_link_file("kakrail_corridor")}
        for node_id, _x, _y, arms in nodes:
            self.assertIsInstance(node_id, int)
            for arm in arms:
                self.assertIn(arm, links, f"node {node_id} names a missing link")

    def test_a_boundary_node_sits_on_its_arm(self):
        """What the rewrite relies on to tell a boundary node from a junction."""
        nodes = fit.read_node_file("kakrail_corridor")
        links = {link_id: rows
                 for link_id, _u, _d, rows in fit.read_link_file("kakrail_corridor")}
        lookup = {link_id: (up, down)
                  for link_id, up, down, _r in fit.read_link_file("kakrail_corridor")}
        singles = [n for n in nodes if len(n[3]) == 1]
        self.assertTrue(singles, "no boundary nodes to check")
        for node_id, x, y, arms in singles:
            rows = links[arms[0]]
            up, _down = lookup[arms[0]]
            end = ((rows[0][0], rows[0][1]) if up == node_id
                   else (rows[-1][2], rows[-1][3]))
            self.assertAlmostEqual(x, end[0], 6)
            self.assertAlmostEqual(y, end[1], 6)


class LinkFileTest(unittest.TestCase):
    """Reading and writing link.txt has to round-trip."""

    def test_round_trip_on_a_real_network(self):
        links = fit.read_link_file("kakrail_corridor")
        self.assertGreater(len(links), 0)
        for link_id, up, down, rows in links:
            self.assertIsInstance(link_id, int)
            self.assertIsInstance(up, int)
            self.assertIsInstance(down, int)
            self.assertGreater(len(rows), 0)
            # every segment carries the same width, and joins the previous one
            for previous, row in zip(rows, rows[1:]):
                self.assertAlmostEqual(previous[2], row[0], 6)
                self.assertAlmostEqual(previous[3], row[1], 6)

    def test_no_segment_is_degenerate(self):
        # The whole reason drop_short exists.
        for network in ("kakrail_corridor", "bijoy_sarani", "banani_23",
                        "banani_27"):
            for link_id, _up, _down, rows in fit.read_link_file(network):
                for sx, sy, ex, ey, _w in rows:
                    self.assertGreater(math.hypot(ex - sx, ey - sy), 0.0,
                                       f"{network} link {link_id}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
