"""The palette's legibility, pinned as numbers.

The start screen's captions were once drawn at 2.4:1 and nobody noticed
until a reader said they could not read them.  The October heuristic
evaluation found the same kind of thing in three more places -- a focus
ring that vanished on the selected cell, toolbar sliders that vanished on
the toolbar, and signal bars whose red and green were two shades of olive
to a colour-blind reader -- none of which a test was looking at.  These
are the sums that found them, so a palette change that loses legibility
fails here rather than in front of somebody:

* WCAG 2.2's relative luminance and contrast ratio: 4.5:1 for text,
  3:1 for the parts of a control that say what it is and what state it
  is in (1.4.3, 1.4.11, 2.4.7).
* Machado, Oliveira and Fernandes (2009), severity 1, for what protan,
  deutan and tritan readers see.
* CIE 1976 colour difference, to keep a signal from looking like a
  vehicle.

Pure arithmetic over the shipped constants; no display needed.
"""
import itertools
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import road_geometry  # noqa: E402
from dhakasim.constants import Constants  # noqa: E402
from dhakasim.gui import _UI, zoom_message  # noqa: E402


def _rgb(colour):
    if isinstance(colour, str):
        h = colour.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    if hasattr(colour, "r"):
        return (colour.r, colour.g, colour.b)
    return tuple(colour)


def _linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour):
    r, g, b = (_linear(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


#: Machado et al. (2009), severity 1.0, applied to linear RGB.
DEFICIENCIES = {
    "protan": ((0.152286, 1.052583, -0.204868),
               (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968),
               (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779),
               (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def seen_by(colour, deficiency):
    """``colour`` as a reader with ``deficiency`` sees it."""
    lin = [_linear(c) for c in _rgb(colour)]
    out = []
    for row in DEFICIENCIES[deficiency]:
        v = min(1.0, max(0.0, sum(m * c for m, c in zip(row, lin))))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        out.append(round(v * 255))
    return tuple(out)


def _lab(colour):
    r, g, b = (_linear(c) for c in _rgb(colour))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def difference(a, b):
    """CIE 1976 colour difference."""
    return math.dist(_lab(a), _lab(b))


class TextContrastTest(unittest.TestCase):
    """Every pairing of text and ground the two screens use, at 4.5:1."""

    PAIRS = (
        ("text", "panel", "a row's name"),
        ("muted", "panel", "a caption, a unit"),
        ("faint", "panel", "the faintest caption"),
        ("faint", "band", "a disabled toolbar button"),
        ("accent_text", "panel", "a section title"),
        ("accent_text", "band", "the run's status line"),
        ("text", "seg_off", "an unselected choice"),
        ("on_accent", "accent", "the selected choice, Start"),
        ("on_accent", "accent_hover", "the selected choice under the pointer"),
        ("muted", "ground", "the tagline"),
    )

    def test_every_pairing_reads(self):
        for fg, bg, where in self.PAIRS:
            ratio = contrast(_UI[fg], _UI[bg])
            self.assertGreaterEqual(ratio, 4.5, "%s on %s (%s): %.2f:1"
                                    % (fg, bg, where, ratio))


class FocusRingTest(unittest.TestCase):
    def test_the_ring_shows_on_every_fill_it_can_surround(self):
        """Selected and unselected, hovered or not, and the grounds behind.

        Most controls showed no ring at all until it was drawn by hand
        (``gui._ring``); the one control that showed focus by colour, the
        playback handle, changed from accent to accent_hover, 1.21:1.
        """
        for fill in ("accent", "accent_hover", "seg_off", "seg_hover",
                     "panel", "band", "ground"):
            ratio = contrast(_UI["focus"], _UI[fill])
            self.assertGreaterEqual(ratio, 3.0, "focus on %s: %.2f:1"
                                    % (fill, ratio))


class ToolbarSliderTest(unittest.TestCase):
    def test_the_handle_stands_off_its_trough(self):
        """The run toolbar's sliders: handle muted on a panel trough.  It was
        seg_off on panel, 1.39:1, and the control all but vanished."""
        self.assertGreaterEqual(contrast(_UI["muted"], _UI["panel"]), 3.0)
        self.assertGreaterEqual(contrast(_UI["muted"], _UI["band"]), 3.0)


class SignalColourTest(unittest.TestCase):
    RED = road_geometry.SIGNAL_COLOURS["RED"]
    GREEN = road_geometry.SIGNAL_COLOURS["GREEN"]

    def test_red_and_green_differ_in_lightness_for_every_reader(self):
        """A bar has no position to say which light is lit, so lightness
        has to: 3:1 between red and green for every reader.  The first pair
        was 1.53:1 to a deutan reader."""
        ratio = contrast(self.RED, self.GREEN)
        self.assertGreaterEqual(ratio, 3.0, "normal vision: %.2f:1" % ratio)
        for deficiency in DEFICIENCIES:
            ratio = contrast(seen_by(self.RED, deficiency),
                             seen_by(self.GREEN, deficiency))
            self.assertGreaterEqual(ratio, 3.0, "%s: %.2f:1"
                                    % (deficiency, ratio))

    def test_red_and_green_read_on_their_casing(self):
        casing = road_geometry.SIGNAL_CASING_COLOUR
        self.assertGreaterEqual(contrast(self.RED, casing), 3.0)
        self.assertGreaterEqual(contrast(self.GREEN, casing), 3.0)

    def test_no_signal_looks_like_a_vehicle(self):
        """Further from every vehicle colour than the first red was (13.9):
        a lit bar must never pass for a car or a CNG."""
        for name, colour in road_geometry.SIGNAL_COLOURS.items():
            for vehicle in Constants.VEHICLE_TYPE_COLORS:
                self.assertGreaterEqual(difference(colour, vehicle), 13.9,
                                        "%s against %s" % (name, vehicle))


class VehiclePaletteTest(unittest.TestCase):
    def test_every_class_is_distinct_to_normal_vision(self):
        """The floor the palette was designed to.  The colour-vision gap in
        it is recorded in docs/dhakasim-heuristics-beyond-nielsen.html and
        left to the owner, since these colours are written into every
        trace and every report."""
        shown = [Constants.VEHICLE_TYPE_COLORS[i]
                 for i in (0, 1, 2, 3, 4, 7, 8, 10, 12)]
        closest = min(difference(a, b)
                      for a, b in itertools.combinations(shown, 2))
        self.assertGreaterEqual(closest, 30.0)


class ZoomMessageTest(unittest.TestCase):
    def test_a_zoom_is_a_percentage_of_a_named_reference(self):
        self.assertEqual("Zoom 92%, where 100% fits the whole network",
                         zoom_message(0.92))
        self.assertEqual("Zoom 400%, where 100% fits the whole network",
                         zoom_message(4.0))


if __name__ == "__main__":
    unittest.main()
