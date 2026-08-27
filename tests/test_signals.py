"""Pin the VISSIM-style junction discipline and its signal bars.

Two things are pinned against a real network (banani_23, the four-arm
junction whose box used to carpet over with vehicles).  First, the stop
lines: every approach to the signalised junction gets a setback on its mouth
segment, so a red light's phantom leader stands at the edge of the junction
box instead of at the point where the surveyed arms converge.  Second, the
signal bars: one cased red-or-green bar per approach, drawn by
``road_geometry.signal_bars`` for the window, the report and the 3D view
alike, and never on a roundabout.

The traffic-model side of KeepClearMode is deliberately *not* asserted here
-- the suites do not run the traffic model.  Its off-switch is pinned by
``python hash_run.py`` staying byte-identical with ``KeepClearMode Off``.

Run with ``python tests/test_signals.py``.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim.constants import Constants          # noqa: E402
from dhakasim.parameters import Parameters        # noqa: E402
from dhakasim import road_geometry                # noqa: E402
from dhakasim import utilities as U               # noqa: E402


class _Recorder:
    """Just enough of a drawing surface to log what signal_bars asks for."""

    def __init__(self):
        self.lines = []            # (colour rgb, stroke, x1, y1, x2, y2)
        self._colour = None
        self._stroke = None

    def set_color(self, colour):
        self._colour = (colour.get_red(), colour.get_green(),
                        colour.get_blue())

    def set_stroke(self, width):
        self._stroke = width

    def draw_line(self, x1, y1, x2, y2):
        self.lines.append((self._colour, self._stroke, x1, y1, x2, y2))


def _rgb(colour):
    return (colour.get_red(), colour.get_green(), colour.get_blue())


class SignalTest(unittest.TestCase):
    processor = None

    @classmethod
    def setUpClass(cls):
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        U.initialize()
        Parameters.NETWORK_DIR = "banani_23"
        U.apply_network_defaults("banani_23")
        U.finalise_settings()
        Constants.initialize()
        U.index_trace_file()
        from dhakasim.processor import Processor
        cls.processor = Processor()
        cls.junction = next(node for node in cls.processor.node_list
                            if node.number_of_links() >= 3)

    def _mouth_and_setback(self, link):
        """The chain end nearer the junction, and the setback stored there."""
        first, last = link.get_first_segment(), link.get_last_segment()
        # banani's junction arms all start at the node, chain running
        # outwards or inwards; take whichever end carries a setback.
        if last.stop_setback_at_end > 0:
            return last, last.stop_setback_at_end
        return first, first.stop_setback_at_start

    def test_every_approach_gets_a_stop_line(self):
        for j in range(self.junction.number_of_links()):
            link = self.processor.link_list[self.junction.get_link(j)]
            seg, setback = self._mouth_and_setback(link)
            self.assertGreater(setback, 0.0, f"link {link.get_id()}")
            # A segment never carries more line than its own length; a box
            # deeper than the mouth walks the rest into earlier segments.
            self.assertLessEqual(setback, seg.get_length() + 1e-9)
            at_end = seg is link.get_last_segment() \
                and seg.stop_setback_at_end > 0
            total = (seg.stop_total_at_end if at_end
                     else seg.stop_total_at_start)
            # The full reach sits at the edge of the drawn box, which
            # reaches a full arm-width past the mouth -- not at the crossing
            # arm's half-width, which left every queue standing on the patch.
            self.assertGreater(total, 5.0)
            self.assertGreaterEqual(total, setback - 1e-9)
            # The distributed line lands inside the link: walking inward
            # from the node, some segment has room to spare after it.
            count = link.get_number_of_segments()
            order = (range(count - 1, -1, -1) if at_end else range(count))
            carried = 0.0
            carrier_found = False
            for i in order:
                candidate = link.get_segment(i)
                held = (candidate.stop_setback_at_end if at_end
                        else candidate.stop_setback_at_start)
                carried += held
                if held < candidate.get_length() - 1e-9:
                    carrier_found = True
                    break
            self.assertTrue(carrier_found,
                            f"link {link.get_id()}: line never lands")
            self.assertAlmostEqual(
                carried, min(total, sum(
                    link.get_segment(i).get_length()
                    for i in range(count)) * 0.85), delta=0.01)

    def test_the_far_end_of_an_arm_has_no_stop_line(self):
        for j in range(self.junction.number_of_links()):
            link = self.processor.link_list[self.junction.get_link(j)]
            first, last = link.get_first_segment(), link.get_last_segment()
            self.assertEqual(
                0.0, min(first.stop_setback_at_start,
                         last.stop_setback_at_end),
                "a boundary end grew a stop line")

    def test_one_cased_bar_per_approach_and_one_green(self):
        recorder = _Recorder()
        road_geometry.signal_bars(recorder, self.processor.link_list,
                                  self.processor.node_list,
                                  Parameters.pixel_per_meter)
        arms = self.junction.number_of_links()
        # Casing under every bar: two lines per approach.
        self.assertEqual(len(recorder.lines), 2 * arms)
        casing = _rgb(road_geometry.SIGNAL_CASING_COLOUR)
        green = _rgb(road_geometry.SIGNAL_COLOURS["GREEN"])
        red = _rgb(road_geometry.SIGNAL_COLOURS["RED"])
        colours = [line[0] for line in recorder.lines]
        self.assertEqual(colours.count(casing), arms)
        # Fixed-time control starts with exactly one approach green.
        self.assertEqual(colours.count(green), 1)
        self.assertEqual(colours.count(red), arms - 1)

    def test_the_bar_sits_on_its_own_approach_half(self):
        """Bars span half the carriageway -- the half the signal speaks to --
        so two of them never merge into one wall across a dual carriageway."""
        recorder = _Recorder()
        road_geometry.signal_bars(recorder, self.processor.link_list,
                                  self.processor.node_list,
                                  Parameters.pixel_per_meter)
        for _colour, _stroke, x1, y1, x2, y2 in recorder.lines:
            length_m = (((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                        / Parameters.pixel_per_meter)
            widest = max(
                self.processor.link_list[self.junction.get_link(j)]
                .get_first_segment().get_segment_width()
                for j in range(self.junction.number_of_links()))
            self.assertLess(length_m, widest * 0.75,
                            "a bar spans the whole carriageway")

    def test_a_roundabout_gets_no_bars(self):
        node = self.junction
        saved = node._roundabout_radius
        try:
            node._roundabout_radius = 5.0
            recorder = _Recorder()
            road_geometry.signal_bars(recorder, self.processor.link_list,
                                      self.processor.node_list,
                                      Parameters.pixel_per_meter)
            self.assertEqual(recorder.lines, [])
        finally:
            node._roundabout_radius = saved

    def test_keep_clear_is_the_shipped_default(self):
        """Off is the Java-parity mode; a fresh copy runs the VISSIM-style
        discipline.  hash_run.py's baseline is recorded with this default."""
        self.assertTrue(Parameters.KEEP_CLEAR_MODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
