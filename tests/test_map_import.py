"""Pin the map-import helpers -- the parsing, not the network calls.

``map_import.parse_location`` is what turns whatever is in the user's
clipboard into a point on Earth, and each accepted form is pinned here with
a real-shaped example.  Nothing in this file touches the network or the
filesystem.

Run with ``python tests/test_map_import.py``.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil  # noqa: E402

from dhakasim import map_import  # noqa: E402


class ParseLocationTest(unittest.TestCase):
    def test_a_plain_pair(self):
        self.assertEqual(map_import.parse_location("23.7345, 90.3950"),
                         (23.7345, 90.3950))
        self.assertEqual(map_import.parse_location("-33.86 151.21"),
                         (-33.86, 151.21))

    def test_a_google_maps_pin_beats_the_viewport(self):
        # The @ is where the *camera* was; !3d!4d is the dropped pin.
        url = ("https://www.google.com/maps/place/Bijoy+Sarani/"
               "@23.7600,90.4000,15z/data=!3m1!4b1!4m6!3m5!"
               "!8m2!3d23.7638!4d90.3889!16s")
        self.assertEqual(map_import.parse_location(url), (23.7638, 90.3889))

    def test_a_google_maps_viewport_alone_still_works(self):
        url = "https://www.google.com/maps/@23.7345,90.395,16z"
        self.assertEqual(map_import.parse_location(url), (23.7345, 90.395))

    def test_an_osm_marker_beats_the_viewport(self):
        url = ("https://www.openstreetmap.org/?mlat=23.7508&mlon=90.3930"
               "#map=16/23.7000/90.4000")
        self.assertEqual(map_import.parse_location(url), (23.7508, 90.3930))

    def test_an_osm_viewport_alone_still_works(self):
        url = "https://www.openstreetmap.org/#map=16/23.7345/90.3950"
        self.assertEqual(map_import.parse_location(url), (23.7345, 90.3950))

    def test_a_search_query_url(self):
        url = "https://maps.google.com/?q=23.81,90.41"
        self.assertEqual(map_import.parse_location(url), (23.81, 90.41))

    def test_a_place_name_is_not_a_point(self):
        self.assertIsNone(map_import.parse_location("Mirpur 10, Dhaka"))
        self.assertIsNone(map_import.parse_location(""))
        self.assertIsNone(map_import.parse_location(
            "https://www.google.com/maps/place/Dhaka"))

    def test_nonsense_coordinates_are_rejected(self):
        # A latitude of 191 is not on Earth; falling through to the
        # geocoder gives a sensible "not found" instead of a wild bbox.
        self.assertIsNone(map_import.parse_location("191.0, 90.0"))


class HelperTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(map_import.slugify("Mirpur 10, Dhaka"),
                         "mirpur_10_dhaka")
        self.assertEqual(map_import.slugify("  "), "imported")

    def test_bbox_is_centred_and_ordered(self):
        south, west, north, east = map_import.bbox_around(23.7, 90.4, 1000.0)
        self.assertLess(south, 23.7)
        self.assertLess(west, 90.4)
        self.assertGreater(north, 23.7)
        self.assertGreater(east, 90.4)
        self.assertAlmostEqual((south + north) / 2, 23.7, places=9)
        self.assertAlmostEqual((west + east) / 2, 90.4, places=9)
        # A kilometre is about 0.009 degrees of latitude.
        self.assertAlmostEqual(north - south, 2 * 1000.0 / 111320.0, places=6)

    def test_the_presets_nest(self):
        # Each preset is the previous one plus more, so switching up never
        # silently drops a class the user already had.
        (_, main), (_, local), (_, everything) = map_import.CLASS_PRESETS
        self.assertTrue(set(main) < set(local) < set(everything))
        self.assertIn("secondary", main)
        self.assertNotIn("residential", local)

    def test_tile_maths_at_the_origin(self):
        # At zoom 1 the world is a 2x2 grid and (0, 0) is its exact middle.
        self.assertEqual(map_import.deg_to_tile(0.0, 0.0, 1), (1.0, 1.0))

    def test_the_preview_zoom_fits_the_circle(self):
        span = 380
        for radius in (500.0, 1000.0, 3000.0):
            zoom = map_import.preview_zoom(23.7, radius, span)
            self.assertTrue(3 <= zoom <= 17)
            mpp = map_import.metres_per_pixel(23.7, zoom)
            # The circle's diameter stays within the canvas with room over.
            self.assertLessEqual(2 * radius / mpp, 0.67 * span)
        # A larger radius never zooms in further.
        self.assertGreaterEqual(
            map_import.preview_zoom(23.7, 500.0, span),
            map_import.preview_zoom(23.7, 3000.0, span))

    def test_the_overpass_query_carries_the_box_and_classes(self):
        query = map_import._overpass_query(
            (23.69, 90.39, 23.71, 90.41), ("trunk", "primary"))
        self.assertIn("23.690000,90.390000,23.710000,90.410000", query)
        self.assertIn("trunk|primary", query)
        self.assertIn("out geom", query)


class RemovalTest(unittest.TestCase):
    """The remove path, on a folder made for the purpose.

    These write under ``input/`` because the helpers are hard-wired to it
    -- the same working-directory contract the whole simulator runs on.
    The folder name is one no real import can collide with.
    """

    FOLDER = os.path.join("input", "_test_import_tmp")

    def setUp(self):
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.makedirs(self.FOLDER, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.FOLDER, ignore_errors=True)

    def test_the_marker_decides_what_is_an_import(self):
        self.assertFalse(map_import.is_imported("_test_import_tmp"))
        with open(os.path.join(self.FOLDER, map_import.IMPORT_MARKER),
                  "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertTrue(map_import.is_imported("_test_import_tmp"))

    def test_kind_reads_the_file_and_defaults_to_multi(self):
        self.assertEqual(map_import.imported_kind("_test_import_tmp"),
                         "multi")
        with open(os.path.join(self.FOLDER, map_import.KIND_FILE),
                  "w", encoding="utf-8") as handle:
            handle.write("# comment\nkind single\n")
        self.assertEqual(map_import.imported_kind("_test_import_tmp"),
                         "single")

    def test_remove_deletes_an_import_and_refuses_survey_data(self):
        with open(os.path.join(self.FOLDER, map_import.IMPORT_MARKER),
                  "w", encoding="utf-8") as handle:
            handle.write("{}")
        map_import.remove("_test_import_tmp")
        self.assertFalse(os.path.exists(self.FOLDER))
        # A shipped network has no marker; remove must refuse it whole.
        with self.assertRaises(RuntimeError):
            map_import.remove("banani_23")
        self.assertTrue(os.path.isfile(
            os.path.join("input", "banani_23", "link.txt")))


class PruneTest(unittest.TestCase):
    """The junction picker's file surgery, on a synthetic staged network.

    A 4-leg junction (node 0) with one link carrying on past a boundary
    node; keeping three legs must renumber both id spaces densely, turn
    the trimmed junction ends into boundary nodes stored at their arm
    endpoints, remap the geometry directives, and leave the centre-anchor
    comment (the imagery georeference) untouched.
    """

    FOLDER = os.path.join("input", "_test_import_tmp")

    def setUp(self):
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.makedirs(self.FOLDER, exist_ok=True)
        from dhakasim import network_files
        network_files.write_link_rows(
            os.path.join(self.FOLDER, "link.txt"),
            [(0, 1, 0, [(100, 500, 500, 500, 10.0)]),
             (1, 0, 2, [(500, 500, 900, 500, 10.0)]),
             (2, 3, 0, [(500, 100, 500, 500, 8.0)]),
             (3, 0, 4, [(500, 500, 500, 900, 8.0)]),
             (4, 4, 5, [(500, 900, 500, 1200, 8.0)])])
        network_files.write_node_rows(
            os.path.join(self.FOLDER, "node.txt"),
            [(0, 0, 0, [0, 1, 2, 3]), (1, 100, 500, [0]),
             (2, 900, 500, [1]), (3, 500, 100, [2]),
             (4, 0, 0, [3, 4]), (5, 500, 1200, [4])])
        with open(os.path.join(self.FOLDER, "geometry.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("# Centre 23.700000,90.400000 at 500.0,500.0.\n"
                         "median 1 2.0   # east leg\n"
                         "median 4 3.0\n"
                         "oneway 4\n")
        with open(os.path.join(self.FOLDER, "link_names.txt"), "w",
                  encoding="utf-8") as handle:
            for i in range(5):
                handle.write(f"{i} road {i}\n")

    def tearDown(self):
        shutil.rmtree(self.FOLDER, ignore_errors=True)

    def test_pruning_keeps_the_chosen_legs_and_renumbers(self):
        from dhakasim import network_files
        map_import.prune_to_junction(self.FOLDER, {0, 1, 3})
        links = network_files.read_link_rows(
            os.path.join(self.FOLDER, "link.txt"))
        self.assertEqual([row.link_id for row in links], [0, 1, 2])
        nodes = {row.node_id: row for row in network_files.read_node_rows(
            os.path.join(self.FOLDER, "node.txt"))}
        self.assertEqual(len(nodes), 4)
        # The junction keeps the stored-at-(0,0) convention and its three
        # remaining legs, remapped.
        junction = next(row for row in nodes.values()
                        if len(row.link_ids) == 3)
        self.assertEqual((junction.x, junction.y), (0.0, 0.0))
        self.assertEqual(sorted(junction.link_ids), [0, 1, 2])
        # The end that used to carry on to link 4 is a boundary node now,
        # stored at its arm's endpoint.
        south = next(row for row in nodes.values()
                     if row.link_ids == [2])
        self.assertEqual((south.x, south.y), (500.0, 900.0))
        facts = network_files.read_geometry(
            os.path.join(self.FOLDER, "geometry.txt"))
        self.assertEqual(facts.medians, {1: 2.0})     # remapped, 4's gone
        self.assertEqual(facts.oneways, set())
        self.assertEqual(facts.centre, (23.7, 90.4))  # anchor untouched
        self.assertEqual(facts.anchor, (500.0, 500.0))
        with open(os.path.join(self.FOLDER, "link_names.txt"),
                  encoding="utf-8") as handle:
            names = handle.read().splitlines()
        self.assertEqual(names, ["0 road 0", "1 road 1", "2 road 3"])

    def test_the_shape_reader_places_the_junction_at_its_arms(self):
        links, nodes, to_latlon = map_import.read_network_shape(self.FOLDER)
        self.assertEqual(len(links), 5)
        junction = nodes[0]
        self.assertTrue(junction["junction"])
        # Stored at (0,0); drawable position is the mean of the four arm
        # endpoints, which all meet at (500, 500).
        self.assertEqual((junction["x"], junction["y"]), (500.0, 500.0))
        # The anchor maps its own point back to the recorded centre.
        lat, lon = to_latlon(500.0, 500.0)
        self.assertAlmostEqual(lat, 23.7, places=9)
        self.assertAlmostEqual(lon, 90.4, places=9)

    def test_pruning_refuses_a_single_leg(self):
        with self.assertRaises(RuntimeError):
            map_import.prune_to_junction(self.FOLDER, {0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
