"""The one home of the network input-file grammar.

``link.txt``, ``node.txt``, ``geometry.txt`` and the small name files used
to be parsed independently by the processor, ``run_sim.py``, ``basemap``,
and ``fit_roads.py`` -- four readers for the link files and seven partial
grammars for ``geometry.txt``, each knowing a subset and silently ignoring
the rest.  Three real defects (a cp1252 crash on Arabic street names, a BOM
crash, a zero-length-segment crash) were each fixed in one reader while
staying latent in the others.  This module is where that class of bug goes
to die: every reader below is the grammar, and every consumer goes through
one of them.

Interface contract, stated once:

* Every file is UTF-8.  No reader here ever opens with the platform
  default codepage.
* ``link.txt``: a count line, then per link ``id up down n_segments``
  followed by ``n_segments`` rows of ``seg_id sx sy ex ey width`` -- whole
  metres for coordinates, one decimal for width, y increasing SOUTH.
* ``node.txt``: a count line, then per node ``id x y link_id...``.  A
  junction is stored at (0, 0); only degree-1 boundary nodes carry real
  coordinates.  The trailing ids are LINK IDS, not indices.
* ``geometry.txt``: ``#`` starts a comment; directive lines are
  ``median <link> <gap_m>``, ``roundabout <node> <island_r> [ring_w]``,
  ``oneway <link>``, ``turnlane <from> <to> <lo> <hi>``, ``straight
  <link>`` (fit-only), and unknown directives are ignored.  Two facts ride
  in comments by long convention: ``# Centre <lat>,<lon>`` (loose -- the
  surveyed files bury it mid-sentence) and the recorded anchor ``at
  <x>,<y>``, which is only honoured on a line that *starts* with
  ``Centre`` so that prose in a provenance note cannot hijack the
  georeference.

Everything here is arrays-in, arrays-out plus file reads: no Parameters,
no Constants, importable before the simulation is configured.  The
processor builds its heavy ``Segment`` objects (strips, sensors) from
these rows itself -- depth here is the grammar, not the simulation model.
"""

from __future__ import annotations

import os

from .link import Link
from .node import Node


# --------------------------------------------------------------------------
# row shapes
# --------------------------------------------------------------------------

class SegmentRow:
    """One ``link.txt`` segment row, answering the drawing accessors.

    Spelled like :class:`dhakasim.segment.Segment`'s read side on purpose:
    ``road_geometry`` and the basemap ask their questions of either without
    knowing which they hold.  This is the row, not a shadow of the model --
    a real ``Segment`` measures its sensor and builds its strips in its
    constructor, which needs the run's configuration and has no place in a
    file reader.
    """

    __slots__ = ("seg_id", "sx", "sy", "ex", "ey", "width")

    def __init__(self, seg_id, sx, sy, ex, ey, width):
        self.seg_id = seg_id
        self.sx, self.sy, self.ex, self.ey = sx, sy, ex, ey
        self.width = width

    def get_start_x(self):
        return self.sx

    def get_start_y(self):
        return self.sy

    def get_end_x(self):
        return self.ex

    def get_end_y(self):
        return self.ey

    def get_segment_width(self):
        return self.width


class LinkRow:
    __slots__ = ("link_id", "up", "down", "segments")

    def __init__(self, link_id, up, down, segments):
        self.link_id = link_id
        self.up = up
        self.down = down
        self.segments = segments        # list of SegmentRow


class NodeRow:
    __slots__ = ("node_id", "x", "y", "link_ids")

    def __init__(self, node_id, x, y, link_ids):
        self.node_id = node_id
        self.x = x
        self.y = y
        self.link_ids = link_ids        # LINK IDS as stated in the file


class GeometryFacts:
    """Everything ``geometry.txt`` states, directives and comments alike."""

    __slots__ = ("medians", "roundabouts", "circulatory", "oneways",
                 "turn_lanes", "straight", "centre", "anchor")

    def __init__(self):
        self.medians = {}        # link id -> gap metres
        self.roundabouts = {}    # node id -> island radius metres
        self.circulatory = {}    # node id -> ring width metres
        self.oneways = set()     # link ids
        self.turn_lanes = {}     # (from link, to link) -> (lo strip, hi strip)
        self.straight = set()    # link ids the fit must leave as the chord
        self.centre = None       # (lat, lon) or None
        self.anchor = None       # (x, y) network metres, or None


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------

def read_link_rows(path):
    """``link.txt`` at *path* as a list of :class:`LinkRow`.

    Line-by-line with the same token conversions the simulator has always
    made, so the values a heavy loader builds from these rows are the
    values it parsed for itself before this module existed.
    """
    rows = []
    with open(path, "r", encoding="utf-8") as reader:
        num_links = int(reader.readline())
        for _ in range(num_links):
            tokens = reader.readline().split()
            link_id, up, down = int(tokens[0]), int(tokens[1]), int(tokens[2])
            segment_count = int(tokens[3])
            segments = []
            for _ in range(segment_count):
                tokens = reader.readline().split()
                segments.append(SegmentRow(int(tokens[0]),
                                           float(tokens[1]), float(tokens[2]),
                                           float(tokens[3]), float(tokens[4]),
                                           float(tokens[5])))
            rows.append(LinkRow(link_id, up, down, segments))
    return rows


def read_node_rows(path):
    """``node.txt`` at *path* as a list of :class:`NodeRow`."""
    rows = []
    with open(path, "r", encoding="utf-8") as reader:
        num_nodes = int(reader.readline())
        for _ in range(num_nodes):
            tokens = reader.readline().split()
            rows.append(NodeRow(int(tokens[0]),
                                float(tokens[1]), float(tokens[2]),
                                [int(t) for t in tokens[3:]]))
    return rows


def read_geometry(path):
    """``geometry.txt`` at *path* as :class:`GeometryFacts`.

    Raises ``OSError`` if the file cannot be read and ``ValueError`` on a
    malformed directive, exactly as the processor's own parse did -- the
    caller decides whether that is fatal, a warning, or an empty answer.
    """
    facts = GeometryFacts()
    with open(path, "r", encoding="utf-8") as reader:
        for raw in reader:
            comment = ""
            line = raw
            if "#" in raw:
                line, comment = raw.split("#", 1)
            line = line.strip()
            if line:
                tokens = line.split()
                keyword = tokens[0].lower()
                if len(tokens) == 3 and keyword == "median":
                    facts.medians[int(tokens[1])] = float(tokens[2])
                elif len(tokens) >= 3 and keyword == "roundabout":
                    facts.roundabouts[int(tokens[1])] = float(tokens[2])
                    if len(tokens) >= 4:
                        facts.circulatory[int(tokens[1])] = float(tokens[3])
                elif len(tokens) == 2 and keyword == "oneway":
                    facts.oneways.add(int(tokens[1]))
                elif len(tokens) == 5 and keyword == "turnlane":
                    facts.turn_lanes[(int(tokens[1]), int(tokens[2]))] = (
                        int(tokens[3]), int(tokens[4]))
                elif len(tokens) == 2 and keyword == "straight":
                    facts.straight.add(int(tokens[1]))
                # unknown directives are ignored, so the file can carry
                # notes a given reader has no use for
            if comment:
                _comment_facts(comment.strip(), facts)
    return facts


def _comment_facts(text, facts) -> None:
    """The two facts that ride in comments: the centre, and its anchor.

    The centre is matched loosely (first occurrence wins) because the
    surveyed files bury it mid-sentence.  The anchor is only honoured when
    the comment *starts* with ``Centre``: an unanchored match let any
    provenance note containing "...centre 1.0,2.0 at 3,4..." silently move
    the imagery, which is a georeference bug nothing would raise.
    """
    lowered = text.lower()
    # The anchored form must be found on its own even when an earlier prose
    # comment already supplied the centre: kakrail's header mentions the
    # centre mid-sentence two lines before the recorded-anchor line.
    if facts.anchor is None and lowered.startswith("centre"):
        pair = _float_pair(text[len("centre"):])
        if pair is not None:
            centre, rest = pair
            if facts.centre is None:
                facts.centre = centre
            rest = rest.lstrip()
            if rest.lower().startswith("at"):
                anchored = _float_pair(rest[2:])
                if anchored is not None:
                    facts.anchor = anchored[0]
            return
    if facts.centre is not None:
        return
    at = lowered.find("centre")
    if at < 0:
        return
    pair = _float_pair(text[at + len("centre"):])
    if pair is not None:
        facts.centre = pair[0]


def _float_pair(text):
    """Leading ``<float>,<float>`` of *text*, and what follows it."""
    text = text.lstrip()
    head, comma, tail = text.partition(",")
    if not comma:
        return None
    try:
        first = float(head.split()[0]) if head.split() else None
        second_token = tail.split()[0] if tail.split() else ""
        # trim trailing punctuation a prose sentence leaves behind
        second_token = second_token.rstrip(".,;")
        second = float(second_token)
    except (ValueError, IndexError):
        return None
    if first is None:
        return None
    rest = tail.lstrip()[len(tail.lstrip().split()[0]):] if tail.split() else ""
    return (first, second), rest


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------

def write_link_rows(path, links) -> None:
    """Write ``link.txt``: ``[(id, up, down, [(sx, sy, ex, ey, w), ...])]``.

    ``link.txt`` states whole metres, and rounding can collapse a sub-metre
    segment to zero length -- whose kerb perpendicular downstream is NaN.
    The defence lives here, in the one writer, rather than in whichever
    producer remembered it: consecutive points that round to the same
    coordinate are collapsed (endpoints survive, because a dropped point
    equalled its kept neighbour), and a link that collapses entirely is
    refused rather than written wrong -- the producer must merge its end
    nodes, as ``make_network``'s collapse-merge does.

    Rows must be contiguous (each starts where the previous ended); both
    producers build them that way.
    """
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(links)}\n")
        for link_id, up, down, rows in links:
            if not rows:
                raise ValueError(f"link {link_id} has no segments")
            points = [(round(rows[0][0]), round(rows[0][1]))]
            widths = []
            for _sx, _sy, ex, ey, width in rows:
                p = (round(ex), round(ey))
                if p != points[-1]:
                    points.append(p)
                    widths.append(width)
            if len(points) < 2:
                raise ValueError(
                    f"link {link_id} collapses to a point at whole-metre "
                    f"precision; merge its end nodes instead of writing it")
            handle.write(f"{link_id} {up} {down} {len(points) - 1}\n")
            for index, ((ax, ay), (bx, by)) in enumerate(
                    zip(points, points[1:])):
                handle.write(f"{index} {ax} {ay} {bx} {by} "
                             f"{widths[index]:.1f}\n")


def write_node_rows(path, nodes) -> None:
    """Write ``node.txt``: ``[(id, x, y, [link ids])]``.

    Junctions are stored at (0, 0) by convention -- the caller decides
    which nodes those are; this writer only owns the format.
    """
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(nodes)}\n")
        for node_id, x, y, link_ids in nodes:
            arms = "".join(f" {i}" for i in link_ids)
            handle.write(f"{node_id} {x:.0f} {y:.0f}{arms}\n")


# --------------------------------------------------------------------------
# the light network
# --------------------------------------------------------------------------

def read_network(network: str):
    """A network's shape as real :class:`Link` and :class:`Node` objects.

    For callers that need the geometry and nothing else -- the basemap, the
    fitter's anchor arithmetic, diagnostics -- without reading demand,
    generating routes or seeding a fleet.  The links carry
    :class:`SegmentRow` shapes rather than heavy ``Segment`` objects, and
    the nodes are as constructed: no bundles, no roundabout flags (those
    come from geometry.txt and only matter to a running simulation).
    """
    folder = os.path.join("input", network) if network else "input"
    link_rows = read_link_rows(os.path.join(folder, "link.txt"))
    node_rows = read_node_rows(os.path.join(folder, "node.txt"))

    links = []
    index_of = {}
    for i, row in enumerate(link_rows):
        link = Link(i, row.link_id, row.up, row.down)
        for segment in row.segments:
            link.add_segment(segment)
        index_of[row.link_id] = i
        links.append(link)

    nodes = []
    for i, row in enumerate(node_rows):
        node = Node(i, row.node_id, row.x, row.y)
        for link_id in row.link_ids:
            node.add_link(index_of[link_id])
        nodes.append(node)
    return links, nodes
