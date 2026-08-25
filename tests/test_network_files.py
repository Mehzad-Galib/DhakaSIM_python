"""Pin the network-file grammar's one home.

The grammar used to live in four readers and seven partial geometry.txt
parsers; three real defects were each fixed in one while staying latent in
the others.  These tests pin the contract of the single reader that
replaced them: the directive grammar, the two facts that ride in comments,
the UTF-8 policy, and the light objects the drawing code consumes.

Run with:  python tests/test_network_files.py
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import basemap, network_files


def _write(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class GeometryGrammarTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_every_directive_is_collected(self):
        path = _write(self.folder, "geometry.txt", (
            "# Centre 23.700000,90.400000 at 512.5,487.0.\n"
            "median 2 1.2   # শহিদ ক্যাপ্টেন মনসুর আলী -- Arabic/Bangla must read\n"
            "roundabout 4 6.5 9.0\n"
            "oneway 7\n"
            "turnlane 1 2 3 8\n"
            "straight 0\n"
            "frobnicate 9   # unknown directives are notes, not errors\n"))
        facts = network_files.read_geometry(path)
        self.assertEqual(facts.medians, {2: 1.2})
        self.assertEqual(facts.roundabouts, {4: 6.5})
        self.assertEqual(facts.circulatory, {4: 9.0})
        self.assertEqual(facts.oneways, {7})
        self.assertEqual(facts.turn_lanes, {(1, 2): (3, 8)})
        self.assertEqual(facts.straight, {0})
        self.assertEqual(facts.centre, (23.7, 90.4))
        self.assertEqual(facts.anchor, (512.5, 487.0))

    def test_prose_cannot_hijack_the_anchor(self):
        # The anchored form only counts on a line that STARTS with "Centre".
        # An unanchored match once let any provenance note containing
        # "...centre 1.0,2.0 at 3,4..." silently slide the imagery.
        path = _write(self.folder, "geometry.txt", (
            "# Survey note: the network was recentred from 1.0,2.0 at 3,4\n"
            "# during the 2026 refit.\n"
            "median 0 1.0\n"))
        facts = network_files.read_geometry(path)
        self.assertIsNone(facts.anchor)
        # "recentred" is not "centre <lat>,<lon>" -- the loose match needs
        # the word followed by the pair, exactly as the old regex did.
        self.assertIsNone(facts.centre)

    def test_a_prose_centre_does_not_block_a_later_recorded_anchor(self):
        # kakrail's header mentions the centre in a sentence two lines
        # before the recorded-anchor line; both must be read.
        path = _write(self.folder, "geometry.txt", (
            "# Kakrail Church is a ROUNDABOUT: centre 23.737591,90.405234,\n"
            "# one-way circulation.\n"
            "# Centre 23.737591,90.405234 at 500.0,500.0.\n"))
        facts = network_files.read_geometry(path)
        self.assertEqual(facts.anchor, (500.0, 500.0))
        self.assertEqual(facts.centre, (23.737591, 90.405234))

    def test_malformed_directives_raise_for_the_caller_to_judge(self):
        path = _write(self.folder, "geometry.txt", "median 2 not-a-number\n")
        with self.assertRaises(ValueError):
            network_files.read_geometry(path)


class WriterTest(unittest.TestCase):
    """The one writer pair, and the zero-length defence it carries."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_round_trip_survives(self):
        path = os.path.join(self.folder, "link.txt")
        network_files.write_link_rows(path, [
            (0, 0, 1, [(30.0, 260.0, 191.0, 259.0, 10.0),
                       (191.0, 259.0, 232.0, 244.0, 10.0)])])
        rows = network_files.read_link_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0].segments), 2)
        self.assertEqual(rows[0].segments[1].get_end_x(), 232.0)

    def test_a_segment_that_rounds_to_nothing_is_collapsed(self):
        # 100.0 -> 100.4 rounds to 100 -> 100: the segment vanishes and the
        # chain closes over it, instead of a zero-length segment whose kerb
        # perpendicular downstream is NaN.
        path = os.path.join(self.folder, "link.txt")
        network_files.write_link_rows(path, [
            (0, 0, 1, [(0.0, 0.0, 100.0, 0.0, 8.0),
                       (100.0, 0.0, 100.4, 0.0, 8.0),
                       (100.4, 0.0, 200.0, 0.0, 8.0)])])
        rows = network_files.read_link_rows(path)
        self.assertEqual(len(rows[0].segments), 2)
        for seg in rows[0].segments:
            self.assertNotEqual((seg.sx, seg.sy), (seg.ex, seg.ey))

    def test_a_link_that_collapses_entirely_is_refused(self):
        # Writing it would give the simulator a link with no direction; the
        # producer has to merge the end nodes instead, as make_network does.
        path = os.path.join(self.folder, "link.txt")
        with self.assertRaises(ValueError):
            network_files.write_link_rows(path, [
                (3, 0, 1, [(50.0, 50.0, 50.3, 50.2, 8.0)])])

    def test_node_format_matches_the_shipped_files(self):
        path = os.path.join(self.folder, "node.txt")
        network_files.write_node_rows(path, [(0, 500.0, 30.0, [0]),
                                             (1, 0, 0, [0, 1, 2])])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "2\n0 500 30 0\n1 0 0 0 1 2\n")


class LightNetworkTest(unittest.TestCase):
    """The light objects answer the calls the drawing code makes."""

    def test_kakrail_reads_into_real_links_and_nodes(self):
        links, nodes = network_files.read_network("kakrail_corridor")
        self.assertEqual(len(links), 5)
        self.assertEqual(len(nodes), 6)
        # The accessors road_geometry.node_point and network_bounds use.
        seg = links[0].get_first_segment()
        for call in ("get_start_x", "get_start_y", "get_end_x", "get_end_y",
                     "get_segment_width"):
            self.assertIsInstance(getattr(seg, call)(), float)
        node = nodes[0]
        # Not a roundabout until geometry.txt says so; get_outer_radius is
        # only consulted for nodes that are (islands() gates on it), so its
        # constructed default is not pinned here.
        self.assertFalse(node.is_roundabout())
        # node.txt stores link IDS; the light reader hands out indices, the
        # same mapping the simulator's own loader makes.
        for j in range(node.number_of_links()):
            self.assertIsInstance(links[node.get_link(j)].get_id(), int)

    def test_basemap_reader_is_this_reader(self):
        ours = network_files.read_network("kakrail_corridor")
        theirs = basemap.read_network("kakrail_corridor")
        self.assertEqual(len(ours[0]), len(theirs[0]))
        self.assertEqual(len(ours[1]), len(theirs[1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
