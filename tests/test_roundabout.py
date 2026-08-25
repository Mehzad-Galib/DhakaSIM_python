"""Pin the roundabout: the ring exists, and nothing drives across the island.

A roundabout is the one junction whose middle is *not* road.  The survey draws
every junction as a point -- all the arms at a node share one coordinate -- so
without the pull-back in ``Processor._open_the_circle`` the arms all end in the
middle of the island, traffic crosses it, and there is no circulatory
carriageway for anything to be on.  None of that raises; it just draws wrongly,
which is why it is pinned here.

Run with ``python tests/test_roundabout.py``.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim.constants import Constants          # noqa: E402
from dhakasim.intersection_strip import IntersectionStrip  # noqa: E402
from dhakasim.parameters import Parameters        # noqa: E402
from dhakasim import utilities as U               # noqa: E402
import fit_roads as fit                           # noqa: E402


def _strip(start, end):
    """A bare intersection path from *start* to *end*, in pixel space."""
    return IntersectionStrip(0, 0, 1, 0, start[0], start[1], end[0], end[1],
                             None, None)


class CircleCrossingTest(unittest.TestCase):
    """Where an arm meets the outer kerb of the circle."""

    def setUp(self):
        from dhakasim.processor import _circle_crossing
        self.crossing = _circle_crossing

    def test_a_straight_arm_is_cut_on_the_circle(self):
        points = [(100.0, 0.0), (0.0, 0.0)]        # running inwards to the centre
        index, point = self.crossing(points, 0.0, 0.0, 30.0)
        self.assertEqual(index, 0)
        self.assertAlmostEqual(math.hypot(*point), 30.0, 6)

    def test_the_cut_lands_on_the_segment_that_crosses(self):
        """Several segments in, so the ones inside the circle are dropped."""
        points = [(100.0, 0.0), (60.0, 0.0), (20.0, 0.0), (0.0, 0.0)]
        index, point = self.crossing(points, 0.0, 0.0, 30.0)
        self.assertEqual(index, 1, "cut the wrong segment")
        self.assertAlmostEqual(point[0], 30.0, 6)

    def test_a_link_shorter_than_the_circle_is_left_alone(self):
        """Better an arm that overruns than one cut to nothing."""
        points = [(10.0, 0.0), (0.0, 0.0)]
        self.assertIsNone(self.crossing(points, 0.0, 0.0, 30.0))

    def test_an_arm_that_never_reaches_the_centre_still_cuts(self):
        # ends short of the middle but well inside the ring
        points = [(100.0, 0.0), (5.0, 3.0)]
        index, point = self.crossing(points, 0.0, 0.0, 30.0)
        self.assertEqual(index, 0)
        self.assertAlmostEqual(math.hypot(*point), 30.0, 6)


class ArcTest(unittest.TestCase):
    """set_arc has to bend the crossing *round* the island, not across it."""

    RADIUS = 30.0

    def _on_circle(self, degrees):
        a = math.radians(degrees)
        return (self.RADIUS * math.cos(a), self.RADIUS * math.sin(a))

    def test_a_path_between_two_points_on_the_ring_stays_on_the_ring(self):
        strip = _strip(self._on_circle(0), self._on_circle(90))
        strip.set_arc(0.0, 0.0)
        self.assertTrue(strip.is_curved())
        for step in range(11):
            x, y = strip.point_at(strip.get_length() * step / 10.0)
            self.assertAlmostEqual(math.hypot(x, y), self.RADIUS, 6)

    def test_the_sweep_is_clockwise(self):
        """Traffic drives on the left here, so it circulates clockwise, and
        screen y grows downward, which makes increasing angle clockwise."""
        strip = _strip(self._on_circle(0), self._on_circle(90))
        strip.set_arc(0.0, 0.0)
        self.assertAlmostEqual(strip.arc_sweep, math.pi / 2, 6)
        # the long way round, if the exit is behind the entry
        strip = _strip(self._on_circle(90), self._on_circle(0))
        strip.set_arc(0.0, 0.0)
        self.assertAlmostEqual(strip.arc_sweep, 3 * math.pi / 2, 6)

    def test_going_round_is_further_than_cutting_across(self):
        strip = _strip(self._on_circle(0), self._on_circle(180))
        chord = 2 * self.RADIUS
        strip.set_arc(0.0, 0.0)
        self.assertAlmostEqual(strip.get_length(), math.pi * self.RADIUS, 6)
        self.assertGreater(strip.get_length(), chord)

    def test_a_path_nowhere_near_a_centre_stays_straight(self):
        """Everywhere but a roundabout, the path is the chord it always was."""
        strip = _strip((0.0, 0.0), (10.0, 0.0))
        self.assertFalse(strip.is_curved())
        self.assertAlmostEqual(strip.point_at(5.0)[0], 5.0, 6)


class LoadedNetworkTest(unittest.TestCase):
    """The whole thing, on the network that ships a roundabout."""

    NETWORKS = ("kakrail_corridor",)

    def _load(self, network, geometry):
        U.initialize()
        Parameters.NETWORK_DIR = network
        Parameters.GEOMETRY_MODE = geometry
        Parameters.REPORT_ANIMATION_FRAMES = 0
        U.apply_network_defaults(network)
        U.finalise_settings()
        Constants.initialize()
        from dhakasim.processor import Processor
        return Processor()

    def test_every_arm_stops_on_the_outer_kerb(self):
        for network in self.NETWORKS:
            proc = self._load(network, True)
            circles = [n for n in proc.node_list if n.is_roundabout()]
            self.assertTrue(circles, f"{network} declared no roundabout")
            for node in circles:
                cx, cy = node.get_centre()
                outer = node.get_outer_radius()
                for j in range(node.number_of_links()):
                    link = proc.link_list[node.get_link(j)]
                    if link.get_up_node() == node.get_id():
                        seg = link.get_first_segment()
                        end = (seg.get_start_x(), seg.get_start_y())
                    else:
                        seg = link.get_last_segment()
                        end = (seg.get_end_x(), seg.get_end_y())
                    self.assertAlmostEqual(
                        math.hypot(end[0] - cx, end[1] - cy), outer, 3,
                        f"{network} link {link.get_id()} does not reach the ring")

    def test_the_ring_is_wide_enough_to_drive_on(self):
        for network in self.NETWORKS:
            proc = self._load(network, True)
            for node in proc.node_list:
                if node.is_roundabout():
                    self.assertGreater(node.get_circulatory_width(), 0.0)
                    self.assertGreater(node.get_outer_radius(),
                                       node.get_roundabout_radius())

    def test_geometry_mode_off_changes_nothing(self):
        """Off is the Java-parity mode: no island, no ring, no arms moved.

        Asked of ``link.txt`` rather than of the arms' agreement with each
        other.  The older version of this test checked that every arm at the
        junction ended on the same coordinate, which was true of the survey
        and is a statement about the *data*, not about the mode: a fitted
        roundabout's arms are aimed at the circle now and meet it separately.
        What Off actually promises is that nothing in the file is touched.
        """
        proc = self._load("kakrail_corridor", False)
        self.assertFalse(any(n.is_roundabout() for n in proc.node_list))
        stated = fit.read_link_file("kakrail_corridor")
        self.assertEqual(len(stated), len(proc.link_list))
        for link_id, _up, _down, rows in stated:
            link = [l for l in proc.link_list if l.get_id() == link_id][0]
            self.assertEqual(link.get_number_of_segments(), len(rows), link_id)
            for i, (sx, sy, ex, ey, width) in enumerate(rows):
                seg = link.get_segment(i)
                self.assertAlmostEqual(seg.get_start_x(), sx, 6)
                self.assertAlmostEqual(seg.get_start_y(), sy, 6)
                self.assertAlmostEqual(seg.get_end_x(), ex, 6)
                self.assertAlmostEqual(seg.get_end_y(), ey, 6)
                self.assertAlmostEqual(seg.get_segment_width(), width, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
