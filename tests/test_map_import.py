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

    def test_tile_projection_round_trips(self):
        lat, lon, zoom = 23.7266, 90.3854, 16
        xt, yt = map_import.deg_to_tile(lat, lon, zoom)
        back = map_import.tile_to_latlon(xt * map_import.TILE_PIXELS,
                                         yt * map_import.TILE_PIXELS, zoom)
        self.assertAlmostEqual(back[0], lat, places=9)
        self.assertAlmostEqual(back[1], lon, places=9)

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

    def test_an_occupied_folder_is_replaced_only_when_allowed(self):
        job = map_import.ImportJob("T", "_test_import_tmp", 23.7, 90.4,
                                   300.0, ("primary",))
        # Empty folder, no marker: not an import -- refused.
        with self.assertRaises(RuntimeError):
            job.clear_way(self.FOLDER)
        with open(os.path.join(self.FOLDER, map_import.IMPORT_MARKER),
                  "w", encoding="utf-8") as handle:
            handle.write("{}")
        # An import, but not told it may go: refused, folder intact.
        with self.assertRaises(RuntimeError):
            job.clear_way(self.FOLDER)
        self.assertTrue(os.path.isdir(self.FOLDER))
        job.replace = True
        job.clear_way(self.FOLDER)
        self.assertFalse(os.path.exists(self.FOLDER))
        # A shipped network is refused whatever the flag says.
        shipped = map_import.ImportJob("B", "banani_23", 23.7, 90.4, 300.0,
                                       ("primary",), replace=True)
        with self.assertRaises(RuntimeError):
            shipped.clear_way(os.path.join("input", "banani_23"))
        self.assertTrue(os.path.isfile(
            os.path.join("input", "banani_23", "link.txt")))

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
    """The junction picker's legs and its file surgery, on a synthetic
    staged network shaped like what make_network leaves at a real
    crossing.

    Node 0 is the chosen junction.  Its east leg runs through a side
    street's junction (node 4, where link 6 turns off at 90 degrees) and
    on to the boundary; its south arm is a 10 m stub (link 3) to node 5, a
    second crossing-node of the same junction, from which the real south
    leg (link 4, one-way outward) and a south-west leg (link 5) leave.  So
    the junction has four legs -- west, east, south, south-west -- though
    node 0 itself touches three links and one of those is a stub.
    """

    FOLDER = os.path.join("input", "_test_import_tmp")

    def setUp(self):
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.makedirs(self.FOLDER, exist_ok=True)
        from dhakasim import network_files
        network_files.write_link_rows(
            os.path.join(self.FOLDER, "link.txt"),
            [(0, 1, 0, [(100, 500, 500, 500, 10.0)]),          # west leg
             (1, 0, 4, [(500, 500, 700, 500, 10.0)]),          # east, 1st
             (2, 4, 2, [(700, 500, 900, 500, 12.0)]),          # east, 2nd
             (3, 0, 5, [(500, 500, 500, 510, 33.0)]),          # the stub
             (4, 5, 3, [(500, 510, 500, 900, 8.0)]),           # south
             (5, 5, 6, [(500, 510, 200, 800, 8.0)]),           # south-west
             (6, 4, 7, [(700, 500, 700, 200, 6.0)])])          # side street
        network_files.write_node_rows(
            os.path.join(self.FOLDER, "node.txt"),
            [(0, 0, 0, [0, 1, 3]), (1, 100, 500, [0]),
             (2, 900, 500, [2]), (3, 500, 900, [4]),
             (4, 0, 0, [1, 2, 6]), (5, 0, 0, [3, 4, 5]),
             (6, 200, 800, [5]), (7, 700, 200, [6])])
        with open(os.path.join(self.FOLDER, "geometry.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("# Centre 23.700000,90.400000 at 500.0,500.0.\n"
                         "# a provenance line\n"
                         "median 2 2.0   # east leg, far half\n"
                         "median 3 5.0\n"
                         "oneway 4\n")
        with open(os.path.join(self.FOLDER, "link_names.txt"), "w",
                  encoding="utf-8") as handle:
            for i in range(7):
                handle.write(f"{i} road {i}\n")
        self.shape = map_import.read_network_shape(self.FOLDER)

    def tearDown(self):
        shutil.rmtree(self.FOLDER, ignore_errors=True)

    def legs(self):
        links, nodes, _ = self.shape
        return map_import.junction_legs(links, nodes, 0)

    def test_the_legs_are_whole_roads_not_first_fragments(self):
        legs, centre = self.legs()
        # Four legs, clockwise from north (none here, so east, south,
        # south-west, west), the stub absorbed into the junction and the
        # side street left alone.
        self.assertEqual([leg["end"] for leg in legs], [2, 3, 6, 1])
        east = legs[0]
        self.assertEqual(east["links"], [1, 2])          # walked through node 4
        self.assertEqual(east["points"], [(500, 500), (700, 500), (900, 500)])
        south = legs[1]
        self.assertEqual(south["links"], [4])
        self.assertEqual(south["forward"], [True])
        self.assertEqual(south["points"][0], (500, 510))
        # The convergence point is the mean of the two crossing-nodes.
        self.assertEqual(centre, (500.0, 505.0))
        # No leg walked the stub or the side street.
        walked = {lid for leg in legs for lid in leg["links"]}
        self.assertNotIn(3, walked)
        self.assertNotIn(6, walked)

    def test_a_sharp_turn_ends_the_leg(self):
        links, nodes, _ = self.shape
        # With the straight-on east half gone, the walk reaches node 4 and
        # finds only the 90-degree side street: the leg stops there.
        links = {lid: link for lid, link in links.items() if lid != 2}
        nodes = dict(nodes)
        nodes[4] = dict(nodes[4], links=[1, 6])
        legs, _ = map_import.junction_legs(links, nodes, 0)
        east = next(leg for leg in legs if leg["links"][0] == 1)
        self.assertEqual(east["links"], [1])
        self.assertEqual(east["end"], 4)

    def test_pruning_writes_one_link_per_leg_converging_on_the_junction(self):
        from dhakasim import network_files
        legs, centre = self.legs()
        kept = [legs[0], legs[1], legs[3]]                # drop south-west
        map_import.prune_to_junction(self.FOLDER, kept, centre)
        links = {row.link_id: row for row in network_files.read_link_rows(
            os.path.join(self.FOLDER, "link.txt"))}
        self.assertEqual(sorted(links), [0, 1, 2])
        nodes = {row.node_id: row for row in network_files.read_node_rows(
            os.path.join(self.FOLDER, "node.txt"))}
        self.assertEqual(len(nodes), 4)
        # Node 0 is the junction, stored at (0,0), with every leg on it.
        self.assertEqual((nodes[0].x, nodes[0].y), (0.0, 0.0))
        self.assertEqual(nodes[0].link_ids, [0, 1, 2])
        # The east leg is one link now, both fragments walked, its width
        # length-weighted (200 m of 10 and 200 m of 12), starting at the
        # convergence point and ending at the boundary node.
        east = links[0]
        self.assertEqual(len(east.segments), 2)
        self.assertEqual((east.segments[0].sx, east.segments[0].sy),
                         (500, 505))
        self.assertEqual((east.segments[-1].ex, east.segments[-1].ey),
                         (900, 500))
        self.assertAlmostEqual(east.segments[0].width, 11.0)
        self.assertEqual((nodes[1].x, nodes[1].y), (900.0, 500.0))
        self.assertEqual(nodes[1].link_ids, [0])
        # The one-way south leg runs outward, so it is written junction
        # first, as travel direction demands.
        south = links[1]
        self.assertEqual((south.up, south.down), (0, 2))
        facts = network_files.read_geometry(
            os.path.join(self.FOLDER, "geometry.txt"))
        self.assertEqual(facts.oneways, {1})
        self.assertEqual(facts.medians, {0: 2.0})     # the far half's median
        self.assertEqual(facts.centre, (23.7, 90.4))  # anchor untouched
        self.assertEqual(facts.anchor, (500.0, 500.0))
        with open(os.path.join(self.FOLDER, "geometry.txt"),
                  encoding="utf-8") as handle:
            self.assertIn("# a provenance line", handle.read())
        with open(os.path.join(self.FOLDER, "link_names.txt"),
                  encoding="utf-8") as handle:
            names = handle.read().splitlines()
        # A leg is named after the longest named road it walked.
        self.assertEqual(names, ["0 road 1", "1 road 4", "2 road 0"])

    def test_an_inward_one_way_leg_is_written_towards_the_junction(self):
        from dhakasim import network_files
        with open(os.path.join(self.FOLDER, "geometry.txt"), "a",
                  encoding="utf-8") as handle:
            handle.write("oneway 1\noneway 2\n")
        self.shape = map_import.read_network_shape(self.FOLDER)
        legs, centre = self.legs()
        links, nodes, _ = self.shape
        # Reverse the east fragments so travel runs towards the junction.
        for lid in (1, 2):
            links[lid] = dict(links[lid], up=links[lid]["down"],
                              down=links[lid]["up"],
                              points=links[lid]["points"][::-1])
        legs, centre = map_import.junction_legs(links, nodes, 0)
        east = next(leg for leg in legs if leg["end"] == 2)
        self.assertEqual(east["forward"], [False, False])
        map_import.prune_to_junction(self.FOLDER, [east, legs[-1]], centre)
        rows = {row.link_id: row for row in network_files.read_link_rows(
            os.path.join(self.FOLDER, "link.txt"))}
        self.assertEqual((rows[0].up, rows[0].down), (1, 0))
        self.assertEqual((rows[0].segments[0].sx, rows[0].segments[0].sy),
                         (900, 500))
        facts = network_files.read_geometry(
            os.path.join(self.FOLDER, "geometry.txt"))
        self.assertEqual(facts.oneways, {0})

    def test_two_junctions_share_the_road_between_them(self):
        from dhakasim import network_files
        links, nodes, _ = self.shape
        # Node 0 and the side street's junction (node 4, 200 m east):
        # the east leg from 0 now ends at 4 and joins the two, the walk
        # back from 4 finds it claimed, and 4 offers its own remaining
        # legs -- east to the boundary, and the side street north.
        legs, centres = map_import.network_legs(links, nodes, [0, 4])
        self.assertEqual(list(centres), [0, 4])
        joining = [leg for leg in legs if leg["end_junction"] is not None]
        self.assertEqual(len(joining), 1)
        self.assertEqual((joining[0]["junction"], joining[0]["end_junction"]),
                         (0, 4))
        self.assertEqual(joining[0]["links"], [1])
        from_4 = [leg for leg in legs if leg["junction"] == 4]
        self.assertEqual(sorted(leg["links"][0] for leg in from_4), [2, 6])
        self.assertEqual(len(legs), 6)
        self.assertIsNone(map_import.check_legs(legs, centres))

        map_import.prune_to_junctions(self.FOLDER, legs, centres)
        rows = {row.link_id: row for row in network_files.read_link_rows(
            os.path.join(self.FOLDER, "link.txt"))}
        nodes_out = {row.node_id: row for row in
                     network_files.read_node_rows(
                         os.path.join(self.FOLDER, "node.txt"))}
        # Junctions first, both stored at (0,0); node 1 is the side
        # street's junction with three links on it.
        self.assertEqual((nodes_out[0].x, nodes_out[0].y), (0.0, 0.0))
        self.assertEqual((nodes_out[1].x, nodes_out[1].y), (0.0, 0.0))
        self.assertEqual(len(nodes_out[1].link_ids), 3)
        self.assertEqual(len(nodes_out), 2 + 5)        # five boundary ends
        # The joining link runs centre to centre.
        join = next(row for row in rows.values()
                    if {row.up, row.down} == {0, 1})
        self.assertEqual((join.segments[0].sx, join.segments[0].sy),
                         (500, 505))
        self.assertEqual((join.segments[-1].ex, join.segments[-1].ey),
                         (700, 500))

    def test_disconnected_or_dead_end_junctions_are_refused(self):
        links, nodes, _ = self.shape
        legs, centres = map_import.network_legs(links, nodes, [0, 4])
        # Drop the joining leg: two islands.
        apart = [leg for leg in legs if leg["end_junction"] is None]
        self.assertIn("not joined", map_import.check_legs(apart, centres))
        # Keep only one leg on junction 4: a dead end, not a junction.
        thin = [leg for leg in legs
                if leg["junction"] == 0 or leg["links"] == [6]]
        thin = [leg for leg in thin if leg["end_junction"] is None]
        self.assertIn("at least two legs",
                      map_import.check_legs(thin, centres))
        with self.assertRaises(RuntimeError):
            map_import.prune_to_junctions(self.FOLDER, apart, centres)

    def test_a_road_passing_a_chosen_junction_without_a_node_joins_it(self):
        links, nodes, _ = self.shape
        # A second road from the side street's junction (node 4) runs
        # west and bends north 20 m from node 0 -- inside its reach, but
        # with no OSM node at the bend.  Chosen together, the walk from 4
        # must stop at that bend and join node 0, and the northward rest
        # must become node 0's own leg, not a hooked 400 m road that
        # merely passes the crossing.
        links = dict(links)
        links[7] = {"points": [(700, 500), (520, 480), (520, 100)],
                    "up": 4, "down": 8, "width": 8.0}
        nodes = dict(nodes)
        nodes[4] = dict(nodes[4], links=[1, 2, 6, 7])
        nodes[8] = {"x": 520, "y": 100, "links": [7], "junction": False}
        legs, centres = map_import.network_legs(links, nodes, [0, 4])
        joining = [leg for leg in legs if leg["end_junction"] is not None]
        self.assertEqual(sorted(leg["links"][0] for leg in joining), [1, 7])
        north = next(leg for leg in legs
                     if leg["junction"] == 0 and leg["end"] == 8)
        self.assertEqual(north["links"], [7])
        self.assertEqual(north["points"][-1], (520, 100))
        # Neither piece hooks: the bend vertex is inside node 0's reach,
        # so it is not a vertex of either leg once written.
        self.assertGreater(north["start_clear"], 20.0)

    def test_candidates_are_nodes_with_three_walked_legs(self):
        links, nodes, _ = self.shape
        # Node 0 (three links, one a stub) and node 4 (the side street's
        # junction) are junctions; node 5 sits inside node 0's reach and
        # walks the same legs, so it is offered too -- clicking either
        # dot means the same crossing.  Boundary nodes never are.
        self.assertEqual(sorted(map_import.junction_candidates(links, nodes)),
                         [0, 4, 5])
        # A crossing drawn as degree-2 fragments still counts: split the
        # west leg's node 1 end into a chain -- node 1 becomes a degree-2
        # node 20 m from a new boundary, and gains a side road -- and it
        # is offered although no single node there has three links.
        links = dict(links)
        links[7] = {"points": [(100, 500), (100, 300)], "up": 1, "down": 8,
                    "width": 6.0}
        links[8] = {"points": [(100, 500), (0, 500)], "up": 1, "down": 9,
                    "width": 10.0}
        del links[0]
        links[0] = {"points": [(100, 500), (500, 500)], "up": 1, "down": 0,
                    "width": 10.0}
        nodes = dict(nodes)
        nodes[1] = dict(nodes[1], links=[0, 7])
        nodes[8] = {"x": 100, "y": 300, "links": [7], "junction": False}
        nodes[9] = {"x": 0, "y": 500, "links": [8], "junction": False}
        # node 1 has two links; node 10 at (100, 500) is a second node of
        # the same crossing carrying the third road
        nodes[10] = {"x": 100, "y": 500, "links": [8], "junction": False}
        links[8]["up"] = 10
        nodes[9]["links"] = [8]
        self.assertIn(1, map_import.junction_candidates(links, nodes))

    def test_a_node_inside_another_junctions_cluster_is_that_junction(self):
        links, nodes, _ = self.shape
        # Node 5 is the stub's far end, 10 m from node 0: choosing both
        # is one junction, and the legs are node 0's alone.
        legs, centres = map_import.network_legs(links, nodes, [0, 5])
        self.assertEqual(list(centres), [0])
        self.assertEqual(len(legs), 4)

    def test_the_shape_reader_places_the_junction_at_its_arms(self):
        links, nodes, to_latlon = self.shape
        self.assertEqual(len(links), 7)
        junction = nodes[0]
        self.assertTrue(junction["junction"])
        # Stored at (0,0); drawable position is the mean of the arm
        # endpoints, which all meet at (500, 500).
        self.assertEqual((junction["x"], junction["y"]), (500.0, 500.0))
        # The anchor maps its own point back to the recorded centre.
        lat, lon = to_latlon(500.0, 500.0)
        self.assertAlmostEqual(lat, 23.7, places=9)
        self.assertAlmostEqual(lon, 90.4, places=9)

    def test_pruning_refuses_a_single_leg(self):
        legs, centre = self.legs()
        with self.assertRaises(RuntimeError):
            map_import.prune_to_junction(self.FOLDER, legs[:1], centre)

    def test_a_declared_roundabout_lands_on_the_junction(self):
        from dhakasim import network_files
        legs, centre = self.legs()
        map_import.prune_to_junction(self.FOLDER, legs, centre,
                                     roundabout=(8.0, 7.0))
        facts = network_files.read_geometry(
            os.path.join(self.FOLDER, "geometry.txt"))
        self.assertEqual(facts.roundabouts, {0: 8.0})
        self.assertEqual(facts.circulatory, {0: 7.0})
        nodes = {row.node_id: row for row in network_files.read_node_rows(
            os.path.join(self.FOLDER, "node.txt"))}
        self.assertEqual(len(nodes[0].link_ids), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
