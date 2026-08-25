"""Tests for the map imagery layer.

Two things here are worth more than the rest.  The first is that
:class:`dhakasim.basemap.Projector` still agrees with the one in
``make_network.py``: the networks were built with that projection, so a
divergence would slide every basemap off its roads without anything failing.
The second is the zoom ladder, which exists because Tk rescales an image by
whole numbers only, and which has to stay both exact and fine enough to feel
like ordinary zooming.

Run with:  python tests/test_basemap.py
"""

import contextlib
import importlib.util
import math
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import basemap


@contextlib.contextmanager
def _nowhere_network():
    """A throwaway network with no recorded centre, built under ``input/``.

    ``basemap`` resolves networks by name under ``input/``, so the fixture has
    to live there for the moment the test runs.  One link, two boundary
    nodes, no geometry.txt -- nothing for the georeference to hold on to.
    """
    name = "_test_nowhere"
    folder = os.path.join("input", name)
    os.makedirs(folder, exist_ok=True)
    try:
        with open(os.path.join(folder, "link.txt"), "w",
                  encoding="utf-8") as f:
            f.write("1\n0 0 1 1\n0 100 100 400 100 10.0\n")
        with open(os.path.join(folder, "node.txt"), "w",
                  encoding="utf-8") as f:
            f.write("2\n0 100 100 0\n1 400 100 0\n")
        yield name
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _load_make_network():
    """Import the top-level converter script by path.

    It is a command rather than a module, and the package deliberately does not
    depend on it, so there is nothing to import normally.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_make_network", os.path.join(root, "make_network.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionTest(unittest.TestCase):
    """The copy of the projection must not drift from the original."""

    def test_matches_make_network(self):
        original = _load_make_network()
        for lat0, lon0 in ((23.7585, 90.3838), (25.4775, -80.4725),
                           (24.6800, 46.7400)):
            mine = basemap.Projector(lat0, lon0)
            theirs = original.Projector(lat0, lon0)
            self.assertAlmostEqual(mine.m_per_deg_lat, theirs.m_per_deg_lat, 9)
            self.assertAlmostEqual(mine.m_per_deg_lon, theirs.m_per_deg_lon, 9)
            for dlon, dlat in ((0.01, 0.01), (-0.004, 0.002)):
                self.assertEqual(mine.to_xy(lon0 + dlon, lat0 + dlat),
                                 theirs.to_xy(lon0 + dlon, lat0 + dlat))

    def test_y_increases_southwards(self):
        # The whole placement rests on the network grid and a tile grid running
        # the same way up.  If this flips, every basemap is upside down.
        projector = basemap.Projector(23.7585, 90.3838)
        _, y_north = projector.to_xy(90.3838, 23.7685)
        _, y_south = projector.to_xy(90.3838, 23.7485)
        self.assertLess(y_north, 0.0)
        self.assertGreater(y_south, 0.0)

    def test_round_trip(self):
        geo = basemap.Georeference(500.0, 500.0, 23.7585, 90.3838)
        for x, y in ((500.0, 500.0), (0.0, 0.0), (1234.5, -67.8)):
            lon, lat = geo.to_lonlat(x, y)
            rx, ry = geo.to_xy(lon, lat)
            self.assertAlmostEqual(x, rx, 6)
            self.assertAlmostEqual(y, ry, 6)


class AnchorTest(unittest.TestCase):
    """Which point of a network the recorded lat/lon refers to."""

    def test_roundabout_wins_over_degree(self):
        # Kakrail has two three-armed nodes, so degree alone cannot choose; the
        # recorded coordinate is the circle, which geometry.txt names.
        links, nodes = basemap.read_network("kakrail_corridor")
        self.assertEqual(basemap.read_roundabout("kakrail_corridor"), 4)
        x, y, rule = basemap.anchor_point(links, nodes, 4)
        self.assertIn("roundabout", rule)
        # Half a metre, not exact: a roundabout's arms are aimed at the circle
        # rather than all ending on one coordinate, and link.txt states whole
        # metres, so the mean of the arms' ends carries the rounding.
        self.assertAlmostEqual(x, 500.0, delta=0.5)
        self.assertAlmostEqual(y, 500.0, delta=0.5)

    def test_single_junction_uses_busiest_node(self):
        links, nodes = basemap.read_network("bijoy_sarani")
        x, y, rule = basemap.anchor_point(links, nodes, None)
        self.assertTrue(rule.startswith("node"))
        self.assertAlmostEqual(x, 500.0, 1)
        self.assertAlmostEqual(y, 500.0, 1)

    def test_regular_grid_falls_back_to_the_middle(self):
        # Miami's junctions are all alike, so no node is the landmark.
        links, nodes = basemap.read_network("miami")
        _x, _y, rule = basemap.anchor_point(links, nodes, None)
        self.assertEqual(rule, "bounding box")

    def test_surveyed_junctions_all_sit_at_500_500(self):
        # Independent confirmation that the rule is the one the networks were
        # built with: four extracts, cut by hand, all centred on the same point.
        #
        # Half a metre rather than exactly, because a fitted roundabout's arms
        # no longer share one coordinate -- they are aimed at the circle, and
        # ``link.txt`` states whole metres, so the mean of six rounded points
        # cannot land dead on.  Anything larger than this would be a real move
        # and would take the imagery with it.
        for network in ("bijoy_sarani", "banani_23", "kakrail_corridor"):
            links, nodes = basemap.read_network(network)
            geo = basemap.georeference(network, links, nodes)
            self.assertIsNotNone(geo, network)
            self.assertAlmostEqual(geo.anchor_x, 500.0, delta=0.5, msg=network)
            self.assertAlmostEqual(geo.anchor_y, 500.0, delta=0.5, msg=network)

    def test_network_without_a_centre_has_no_georeference(self):
        # A network whose geometry.txt records no centre is nowhere.  This
        # used to be demo_backup, until that became a real OSM extract of
        # Mohakhali, so the placeless network is now built on the spot.
        with _nowhere_network() as name:
            links, nodes = basemap.read_network(name)
            self.assertIsNone(basemap.georeference(name, links, nodes))


class ReaderTest(unittest.TestCase):
    """The light reader must see the same network the simulator does."""

    def test_counts_and_bounds(self):
        links, nodes = basemap.read_network("bijoy_sarani")
        self.assertEqual(len(links), 4)
        self.assertEqual(len(nodes), 5)
        left, top, right, bottom = basemap.network_bounds(links, 0.0)
        self.assertLess(left, 500.0, "junction inside the bounds")
        self.assertGreater(right, 500.0, "junction inside the bounds")
        self.assertLess(top, 500.0, "junction inside the bounds")
        self.assertGreater(bottom, 500.0, "junction inside the bounds")

    def test_margin_grows_the_box_on_every_side(self):
        links, _nodes = basemap.read_network("bijoy_sarani")
        tight = basemap.network_bounds(links, 0.0)
        loose = basemap.network_bounds(links, 60.0)
        self.assertAlmostEqual(loose[0], tight[0] - 60.0, 6)
        self.assertAlmostEqual(loose[1], tight[1] - 60.0, 6)
        self.assertAlmostEqual(loose[2], tight[2] + 60.0, 6)
        self.assertAlmostEqual(loose[3], tight[3] + 60.0, 6)


class ScaleLadderTest(unittest.TestCase):
    """Tk scales an image by whole numbers, so the view has to meet it."""

    def setUp(self):
        # 1000 px covering 500 m, i.e. half a metre to the pixel.
        self.map = basemap.BaseMap("nowhere.png", 0.0, 0.0, 500.0, 500.0,
                                   1000, 1000)

    def test_every_ratio_is_a_pair_of_whole_numbers(self):
        for wanted in (0.02, 0.05, 0.1, 0.25, 0.4, 0.75, 1.0, 2.0, 4.0):
            zoom, subsample = self.map._ratio(wanted)
            self.assertEqual(zoom, int(zoom))
            self.assertEqual(subsample, int(subsample))
            self.assertGreaterEqual(zoom, 1)
            self.assertGreaterEqual(subsample, 1)
            self.assertLessEqual(zoom, self.map.MAX_ZOOM)

    def test_snapped_scale_is_reachable_exactly(self):
        for wanted in (0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.9):
            snapped = self.map.snap(wanted, 15.0)
            factor = self.map.metres_per_pixel * 15.0 * snapped
            zoom, subsample = self.map._ratio(factor)
            self.assertAlmostEqual(factor, zoom / float(subsample), 9,
                                   f"scale {wanted} is not on the ladder")

    def test_snapping_is_idempotent(self):
        # A repaint re-snaps whatever it last snapped, so a second pass must
        # not creep the view a little further each time.
        for wanted in (0.07, 0.18, 0.33, 0.51, 0.88):
            once = self.map.snap(wanted, 15.0)
            self.assertAlmostEqual(once, self.map.snap(once, 15.0), 12)

    def test_ladder_is_fine_enough_to_feel_continuous(self):
        steps, previous = [], None
        value = 0.02
        while value < 1.0:
            snapped = self.map.snap(value, 15.0)
            if previous is not None and snapped > previous * (1 + 1e-9):
                steps.append(snapped / previous - 1.0)
            previous = max(previous or 0.0, snapped)
            value += 0.0005
        # A slippy map's ladder, deliberately.  MAX_ZOOM is 2, so above the
        # imagery's own resolution there is nowhere to go but doubling, and
        # below it the rungs close up as the subsample grows.  Refusing to
        # magnify is what keeps the picture sharp; a coarse ladder near native
        # resolution is what that costs, and it costs no more than a slippy
        # map charges anyway.
        self.assertGreater(len(steps), 8, "too few zoom steps to be usable")
        self.assertLessEqual(max(steps), 1.01, "a zoom step more than doubles")

    def test_the_zoom_factor_stays_small(self):
        """The zoom numerator is the block size the eye sees, so it is capped.

        This is the property that a search for the *nearest* ratio quietly
        breaks: 16/13 sits a third of a per cent from the requested scale and
        paints sixteen-pixel blocks, which looks like the imagery has been
        smashed.  Nearness is nearly free here, since snap() moves the view
        onto whatever comes back; block size is not.
        """
        value = 0.02
        while value < 1.0:
            factor = self.map.metres_per_pixel * 15.0 * value
            zoom, _subsample = self.map._ratio(factor)
            self.assertLessEqual(zoom, self.map.MAX_ZOOM,
                                 f"scale {value:.3f} asks for {zoom}px blocks")
            value += 0.0005

    def test_a_crisper_ratio_beats_a_nearer_one(self):
        """Asked to magnify slightly, it declines and stays at native.

        1.23 is what the GUI's opening zoom works out to over imagery fetched
        at zoom 19.  Ratios much nearer to it exist -- 16/13 is a third of a
        per cent away -- and every one of them paints the imagery as blocks,
        because Tk enlarges by replicating pixels.  Coming back with 1/1 and
        letting snap() pull the view back to it is the whole design.
        """
        zoom, subsample = self.map._ratio(1.2308)
        self.assertLessEqual(zoom, self.map.MAX_ZOOM)
        self.assertEqual(zoom / subsample, 1.0)

    def test_it_will_not_magnify_beyond_the_cap(self):
        # However far in the view is pushed, the imagery is never enlarged by
        # more than MAX_ZOOM, so a block never exceeds that many pixels.
        for wanted in (1.1, 1.5, 2.0, 3.0, 8.0, 40.0):
            zoom, subsample = self.map._ratio(wanted)
            self.assertLessEqual(zoom / subsample, self.map.MAX_ZOOM)
            self.assertLessEqual(zoom, self.map.MAX_ZOOM)

    def test_native_resolution_is_left_alone(self):
        # A factor of one must not be resampled at all.
        self.assertEqual(self.map._ratio(1.0), (1, 1))

    def test_magnification_is_capped_rather_than_unbounded(self):
        zoom, subsample = self.map._ratio(1000.0)
        self.assertEqual((zoom, subsample), (self.map.MAX_ZOOM, 1))

    def test_shrinking_far_down_is_a_pure_subsample(self):
        # Discarding pixels that were never going to be shown is free, so a
        # tiny factor should cost no magnification at all.
        zoom, subsample = self.map._ratio(1.0 / 37.0)
        self.assertEqual(zoom, 1)
        self.assertGreater(subsample, 30)

    def test_a_useless_scale_is_refused_not_guessed(self):
        self.assertIsNone(self.map._ratio(0.0))
        self.assertIsNone(self.map._ratio(-1.0))


class PlacementTest(unittest.TestCase):
    """Where the imagery lands, in the metres everything else is drawn in."""

    def test_resolution_follows_the_bounds(self):
        one = basemap.BaseMap("nowhere.png", 0.0, 0.0, 500.0, 500.0, 1000, 1000)
        self.assertAlmostEqual(one.metres_per_pixel, 0.5, 9)
        two = basemap.BaseMap("nowhere.png", 100.0, 100.0, 356.0, 356.0, 512, 512)
        self.assertAlmostEqual(two.metres_per_pixel, 0.5, 9)

    def test_a_moved_network_does_not_drag_the_imagery_with_it(self):
        """The bug this pins displaced two whole networks.

        ``BaseMap.load`` used to georeference off the network it was handed,
        and every caller hands it a *live* one -- by which time the processor
        has pulled each arm at a roundabout back to the edge of the ring.  The
        anchor is the mean of a node's arm endpoints, so it became the
        centroid of six points scattered round a circle: 7.1 m out at
        Khamarbari and 5.2 m at Kakrail, half a carriageway, and only on the
        networks that have a roundabout.
        """
        links, nodes = basemap.read_network("kakrail_corridor")
        honest = basemap.BaseMap.load("kakrail_corridor", links, nodes)
        if honest is None:
            self.skipTest("kakrail_corridor has no imagery fetched")
        for link in links:                    # move every arm, as a run does
            for i in range(link.get_number_of_segments()):
                segment = link.get_segment(i)
                segment.sx += 25.0
                segment.ex += 25.0
        moved = basemap.BaseMap.load("kakrail_corridor", links, nodes)
        self.assertAlmostEqual(moved.left, honest.left, 6)
        self.assertAlmostEqual(moved.top, honest.top, 6)

    def test_missing_image_gives_no_basemap_rather_than_an_error(self):
        # Independent of whether imagery has actually been fetched, which is a
        # local choice and not something a test may assume either way.
        links, nodes = basemap.read_network("bijoy_sarani")
        original = basemap.IMAGE_NAME
        basemap.IMAGE_NAME = "no_such_basemap.png"
        try:
            self.assertIsNone(basemap.BaseMap.load("bijoy_sarani", links, nodes))
        finally:
            basemap.IMAGE_NAME = original

    def test_unplaceable_network_gives_no_basemap(self):
        with _nowhere_network() as name:
            links, nodes = basemap.read_network(name)
            self.assertIsNone(basemap.BaseMap.load(name, links, nodes))


class TileGridTest(unittest.TestCase):
    """The fetcher's Web Mercator arithmetic."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "_fetch_basemap", os.path.join(root, "fetch_basemap.py"))
        self.fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fetch)

    def test_tile_round_trip(self):
        for lat, lon in ((23.7585, 90.3838), (25.4775, -80.4725),
                         (24.6800, 46.7400)):
            for zoom in (16, 18, 19):
                x, y = self.fetch.deg_to_tile(lat, lon, zoom)
                back_lat, back_lon = self.fetch.tile_to_deg(x, y, zoom)
                self.assertAlmostEqual(lat, back_lat, 9)
                self.assertAlmostEqual(lon, back_lon, 9)

    def test_zooming_in_one_level_doubles_the_grid(self):
        a = self.fetch.deg_to_tile(23.7585, 90.3838, 18)
        b = self.fetch.deg_to_tile(23.7585, 90.3838, 19)
        self.assertAlmostEqual(b[0], a[0] * 2, 9)
        self.assertAlmostEqual(b[1], a[1] * 2, 9)

    def test_resolution_matches_the_stated_zoom(self):
        # 0.55 m per pixel at zoom 18 in Dhaka is what the help text promises.
        self.assertAlmostEqual(
            self.fetch.metres_per_pixel(23.7585, 18), 0.5468, 3)

    def test_tile_resolution_agrees_with_the_projected_span(self):
        # A tile's own width in metres, taken through the network's projection,
        # must match the Mercator ground resolution to well under a metre.
        # This is the check that would catch the two projections disagreeing.
        zoom = 18
        lat, lon = 23.758496, 90.383775
        x, y = self.fetch.deg_to_tile(lat, lon, zoom)
        north, west = self.fetch.tile_to_deg(math.floor(x), math.floor(y), zoom)
        _south, east = self.fetch.tile_to_deg(math.floor(x) + 1,
                                              math.floor(y) + 1, zoom)
        projector = basemap.Projector(lat, lon)
        span = projector.to_xy(east, north)[0] - projector.to_xy(west, north)[0]
        expected = self.fetch.metres_per_pixel(lat, zoom) * 256
        self.assertLess(abs(span - expected), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
