#!/usr/bin/env python3
"""Turn an OpenStreetMap extract into a DhakaSim network.

The six shipped networks were derived from overpass-turbo GeoJSON exports by
hand.  This does the same mechanically, so a new city is a command rather than
an afternoon of transcription.

Get the GeoJSON from https://overpass-turbo.eu with a query like::

    [out:json][timeout:60];
    way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential)$"]
      (25.46,-80.50,25.50,-80.44);
    (._;>;);
    out geom;

then Export -> GeoJSON, and::

    python make_network.py export.geojson --out input/miami
    python make_network.py export.geojson --out input/miami --centre 25.48,-80.47 --radius 1500

Writes ``node.txt``, ``link.txt`` and a ``geometry.txt`` stub into ``--out``.
Run ``run_sim.py --network <name>`` afterwards to generate the routes and
demand the simulator also needs.

Only the standard library, in keeping with the rest of the project.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# First-party, not a dependency: the file format and its safeguards live in
# the package, shared with fit_roads.py and the simulator's own readers.
from dhakasim import network_files  # noqa: E402

# Road classes worth simulating.  Footways and cycleways are excluded: the
# model puts pedestrians on the carriageway rather than giving them their own
# links, so importing them would invent roads that carry no traffic.
DEFAULT_CLASSES = (
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "residential", "unclassified", "living_street",
)

# Total carriageway width in metres when OSM does not say, by road class.
# These count both directions, as the simulator's segment width does.
DEFAULT_WIDTHS = {
    "motorway": 24.0, "motorway_link": 8.0,
    "trunk": 21.0, "trunk_link": 8.0,
    "primary": 18.0, "primary_link": 7.0,
    "secondary": 14.0, "secondary_link": 7.0,
    "tertiary": 11.0, "tertiary_link": 6.0,
    "residential": 8.0, "unclassified": 8.0, "living_street": 6.0,
    "service": 5.0,
}
LANE_WIDTH = 3.25

# Two points closer than this are the same junction.  OSM ways that meet share
# a node exactly, but coordinates round-trip through text, so compare on a grid.
SNAP_METRES = 0.5


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------

class Projector:
    """Local flat-earth projection about a reference point.

    Accurate to well under a metre across the few kilometres a network spans,
    which is far finer than the road widths being modelled.  Y increases
    southwards so the result is oriented like the screen, not like a map.
    """

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        phi = math.radians(lat0)
        # WGS84 metres per degree, standard series expansion
        self.m_per_deg_lat = (111132.92 - 559.82 * math.cos(2 * phi)
                              + 1.175 * math.cos(4 * phi)
                              - 0.0023 * math.cos(6 * phi))
        self.m_per_deg_lon = (111412.84 * math.cos(phi)
                              - 93.5 * math.cos(3 * phi)
                              + 0.118 * math.cos(5 * phi))

    def to_xy(self, lon: float, lat: float):
        return ((lon - self.lon0) * self.m_per_deg_lon,
                (self.lat0 - lat) * self.m_per_deg_lat)


def distance(p, q) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def polyline_length(points) -> float:
    return sum(distance(points[i], points[i + 1]) for i in range(len(points) - 1))


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def read_generator(path: str) -> str:
    """The GeoJSON ``generator`` field, so the credit line can tell the truth.

    Attributing a synthetic grid to OpenStreetMap would be a false claim about
    where the geometry came from.
    """
    with open(path, "r", encoding="utf-8") as f:
        return str(json.load(f).get("generator", ""))


def load_ways(path: str, classes):
    """GeoJSON LineStrings that are roads, as (properties, coordinates)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ways = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        if props.get("highway") not in classes:
            continue
        if geometry.get("type") == "LineString":
            parts = [geometry["coordinates"]]
        elif geometry.get("type") == "MultiLineString":
            parts = geometry["coordinates"]
        else:
            continue
        for coords in parts:
            if len(coords) >= 2:
                ways.append((props, coords))
    return ways


def _vertex_key(point):
    return (round(point[0], 7), round(point[1], 7))


def collapse_roundabouts(ways):
    """Replace every ``junction=roundabout`` ring with its centre point.

    OSM draws a roundabout as a circular way (or a few arcs of one) with
    the approach roads ending on the ring.  Left alone, the ring's
    opposite sides sit within the dual-carriageway separation and get
    fused into a centreline through the island, the approaches reconnect
    to whatever is left, and Mirpur 10 came out as one junction with a
    hooked 100 m stub for a south leg and no north leg at all.  The
    survey networks state a roundabout as arms converging on a point plus
    a ``roundabout`` directive, which is also the form the import
    dialog's picker and ``Processor._open_the_circle`` want -- so the
    ring goes and every road that touched it now ends at its centre.

    Rings that share a vertex are one roundabout (a circle mapped as
    several arcs); a way with two or more ring vertices (one that runs
    through) has the whole run between them replaced by the single
    centre point.  Returns ``(ways, number of roundabouts)``.
    """
    rings = [(props, coords) for props, coords in ways
             if str(props.get("junction", "")).strip() == "roundabout"]
    if not rings:
        return ways, 0
    # Group arcs that share vertices into whole roundabouts.
    owner = list(range(len(rings)))

    def find(i):
        while owner[i] != i:
            owner[i] = owner[owner[i]]
            i = owner[i]
        return i

    seen = {}
    for i, (_props, coords) in enumerate(rings):
        for point in coords:
            key = _vertex_key(point)
            if key in seen:
                owner[find(i)] = find(seen[key])
            seen[key] = i
    groups = {}
    for i, (_props, coords) in enumerate(rings):
        groups.setdefault(find(i), set()).update(_vertex_key(p) for p in coords)
    centre_of = {}
    for keys in groups.values():
        cx = sum(k[0] for k in keys) / len(keys)
        cy = sum(k[1] for k in keys) / len(keys)
        for key in keys:
            centre_of[key] = (cx, cy)

    out = []
    for props, coords in ways:
        if str(props.get("junction", "")).strip() == "roundabout":
            continue
        new, i = [], 0
        while i < len(coords):
            centre = centre_of.get(_vertex_key(coords[i]))
            if centre is None:
                new.append(coords[i])
                i += 1
                continue
            last = max(j for j in range(i, len(coords))
                       if centre_of.get(_vertex_key(coords[j])) == centre)
            if not new or _vertex_key(new[-1]) != _vertex_key(centre):
                new.append([centre[0], centre[1]])
            i = last + 1
        if len(new) >= 2:
            out.append((props, new))
    return out, len(groups)


def way_width(props) -> float:
    """Carriageway width from the OSM tags, falling back to the road class."""
    raw = props.get("width")
    if raw is not None:
        try:
            # tags appear as "12", "12 m", "12.5"
            return float(str(raw).split()[0].replace("m", "").strip())
        except (ValueError, IndexError):
            pass
    lanes = props.get("lanes")
    if lanes is not None:
        try:
            return max(5.5, int(str(lanes).split(";")[0]) * LANE_WIDTH)
        except ValueError:
            pass
    return DEFAULT_WIDTHS.get(props.get("highway"), 8.0)


# --------------------------------------------------------------------------
# graph building
# --------------------------------------------------------------------------

def snap(point):
    return (round(point[0] / SNAP_METRES), round(point[1] / SNAP_METRES))


def build_edges(ways, projector, centre_xy=None, radius=None):
    """Split ways where they meet, giving edges between junctions.

    A point is a junction if two or more ways pass through it, or if a way
    ends there.  Everything between two junctions stays as one polyline.
    """
    projected = []
    for props, coords in ways:
        # A way tagged oneway=-1 runs against its own point order; flip it
        # here so that everywhere downstream point order IS the travel
        # direction and "oneway" needs no second flavour.
        if str(props.get("oneway", "")).strip() == "-1":
            props = dict(props, oneway="yes")
            coords = list(coords)[::-1]
        points = [projector.to_xy(lon, lat) for lon, lat, *_ in coords]
        if radius is not None:
            # keep a way only while it is inside the disc; a way that leaves
            # and returns is cut into the pieces that are inside
            runs, current = [], []
            for point in points:
                if distance(point, centre_xy) <= radius:
                    current.append(point)
                elif current:
                    runs.append(current)
                    current = []
            if current:
                runs.append(current)
            for run in runs:
                if len(run) >= 2:
                    projected.append((props, run))
        else:
            projected.append((props, points))

    # how many ways touch each grid cell
    touches = {}
    for _, points in projected:
        for key in {snap(p) for p in points}:
            touches[key] = touches.get(key, 0) + 1

    edges = []
    for props, points in projected:
        # cut at any interior point shared with another way
        cut_at = [0]
        for index in range(1, len(points) - 1):
            if touches.get(snap(points[index]), 0) > 1:
                cut_at.append(index)
        cut_at.append(len(points) - 1)
        for a, b in zip(cut_at, cut_at[1:]):
            piece = points[a:b + 1]
            if len(piece) >= 2 and polyline_length(piece) > 0:
                edges.append({"props": props, "points": piece,
                              "width": way_width(props)})
    return edges


def _resample(points, count: int):
    """`count` points spaced evenly along a polyline by arc length."""
    total = polyline_length(points)
    if total == 0:
        return [points[0]] * count
    out, target, walked, index = [], 0.0, 0.0, 0
    step = total / (count - 1)
    for _ in range(count - 1):
        while index < len(points) - 2 and walked + distance(
                points[index], points[index + 1]) < target:
            walked += distance(points[index], points[index + 1])
            index += 1
        leg = distance(points[index], points[index + 1])
        t = 0.0 if leg == 0 else (target - walked) / leg
        t = min(max(t, 0.0), 1.0)
        out.append((points[index][0] + t * (points[index + 1][0] - points[index][0]),
                    points[index][1] + t * (points[index + 1][1] - points[index][1])))
        target += step
    out.append(points[-1])
    return out


def _closest_on_polyline(point, points):
    """Nearest point on a polyline, and how far away it is.

    Used instead of comparing sample points index for index: two carriageways
    of the same road are routinely clipped to different extents, so the nth
    point of one is not opposite the nth point of the other, and pairing them
    that way reports the mismatch as separation.
    """
    best, best_distance = points[0], float("inf")
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        span = dx * dx + dy * dy
        if span == 0:
            candidate = a
        else:
            t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / span
            t = min(max(t, 0.0), 1.0)
            candidate = (a[0] + t * dx, a[1] + t * dy)
        d = distance(point, candidate)
        if d < best_distance:
            best, best_distance = candidate, d
    return best, best_distance


def _arc_at(points, point):
    """Arc length along `points` of the point nearest to `point` -- the
    same projection as :func:`_closest_on_polyline`, reported as a position
    rather than a place."""
    best, best_distance, walked = 0.0, float("inf"), 0.0
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        span = dx * dx + dy * dy
        leg = math.sqrt(span)
        t = 0.0
        if span > 0:
            t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / span
            t = min(max(t, 0.0), 1.0)
        d = distance(point, (a[0] + t * dx, a[1] + t * dy))
        if d < best_distance:
            best, best_distance = walked + t * leg, d
        walked += leg
    return best


def _point_at(points, s):
    """The point `s` metres along a polyline (clamped to its ends)."""
    walked = 0.0
    for a, b in zip(points, points[1:]):
        leg = distance(a, b)
        if walked + leg >= s and leg > 0:
            t = min(max((s - walked) / leg, 0.0), 1.0)
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        walked += leg
    return points[-1]


def _slice(points, s0, s1):
    """The stretch of a polyline between arc lengths `s0` and `s1`."""
    out, walked = [_point_at(points, s0)], 0.0
    for a, b in zip(points, points[1:]):
        walked += distance(a, b)
        if s0 < walked < s1:
            out.append(b)
    out.append(_point_at(points, s1))
    return out


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _direction(points):
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    span = math.hypot(dx, dy)
    return (0.0, 0.0) if span == 0 else (dx / span, dy / span)


def _is_oneway(edge) -> bool:
    return str(edge["props"].get("oneway", "")).lower() in ("yes", "1", "-1")


def chain_oneway_pieces(edges):
    """Join a one-way carriageway's pieces through its degree-2 points.

    OSM chops a carriageway wherever a lane is added or a turn bay begins:
    Campbell Drive at Homestead arrives as forty ways of 20 to 150 m, cut
    at different places on its two sides.  Pairing those pieces one by one
    matched the odd lengths badly -- a 3 m fragment fused with something
    30 m away and got a 30 m "median" -- and left the road broken between
    them.  Joined first, each side between two real junctions is one
    polyline and pairs with its opposite number cleanly.

    Only aligned one-way pieces are joined (first runs into the point,
    second runs out of it); head-to-head pieces are two roads meeting, and
    a two-way piece is left for :func:`collapse_and_prune`.
    """
    def endpoints(edge):
        return snap(edge["points"][0]), snap(edge["points"][-1])

    changed = True
    while changed:
        changed = False
        incident = {}
        for edge in edges:
            for key in endpoints(edge):
                incident.setdefault(key, []).append(edge)
        for key, touching in incident.items():
            if len(touching) != 2:
                continue
            first, second = touching
            if first is second:
                continue                      # a loop back to itself
            if not (_is_oneway(first) and _is_oneway(second)):
                continue
            if snap(first["points"][-1]) != key:
                first, second = second, first
            if (snap(first["points"][-1]) != key
                    or snap(second["points"][0]) != key):
                continue                      # head to head or tail to tail
            if snap(first["points"][0]) == snap(second["points"][-1]):
                continue                      # merging would close a ring
            length_a = polyline_length(first["points"])
            length_b = polyline_length(second["points"])
            merged = {"props": dict(first["props"]),
                      "points": first["points"] + second["points"][1:],
                      "width": ((first["width"] * length_a
                                 + second["width"] * length_b)
                                / max(length_a + length_b, 1e-9))}
            edges = [e for e in edges if e is not first and e is not second]
            edges.append(merged)
            changed = True
            break
    return edges


#: Endpoints closer than this are one node.  Fusing a dual carriageway
#: averages two ways into a centreline whose ends no longer coincide with
#: the shared vertex the approach roads meet at -- typically a metre off,
#: which the whole-metre snap grid can put in a different cell.  Real
#: junctions are never this close together; three metres is wider than any
#: fusing residual seen and narrower than any real gap.
WELD_METRES = 3.0


#: A carriageway must run alongside its partner for at least this far to
#: be fused with it; anything shorter is a crossing, not a divided road.
OVERLAP_MIN = 10.0


def _merge_pass(edges, max_separation: float, samples: int):
    """Fuse the two one-way sides of a dual carriageway into one link.

    OSM maps a divided road as two one-way ways; this simulator's links are
    bidirectional, with the two directions sharing a link's strips and a
    physical median expressed in ``geometry.txt``.  Importing both sides
    verbatim would therefore give the road twice the capacity at half the
    width apiece -- and leave a stack of degenerate cross-links where the
    carriageways connect.

    Pairs are matched greedily by mean separation: antiparallel, comparable in
    length, and closer together than ``max_separation``.  The merged link takes
    the centreline between the two, a width covering both carriageways and the
    gap, and records that gap as its median.

    Only the stretch where the two run alongside each other is fused.  The
    first version built the centreline over the whole of one carriageway
    and threw the other away entirely, so wherever the two were cut to
    different extents the longer one's overhang vanished from the network
    -- a 12 m hole in Campbell Drive, found by the picker walking a leg
    into it.  The overhangs now survive as the one-way pieces they are;
    :func:`reconnect_to_fused` joins their inner ends to the fused road.
    """
    candidates = [e for e in edges if _is_oneway(e)]
    others = [e for e in edges if not _is_oneway(e)]

    scored = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            da, db = _direction(a["points"]), _direction(b["points"])
            if da[0] * db[0] + da[1] * db[1] > -0.7:
                continue                       # not running against each other
            length_a = polyline_length(a["points"])
            length_b = polyline_length(b["points"])
            if not 0.5 <= length_a / max(length_b, 1e-9) <= 2.0:
                continue
            # the stretch each runs alongside the other: between the
            # projections of the other's two ends
            a_lo, a_hi = sorted((_arc_at(a["points"], b["points"][0]),
                                 _arc_at(a["points"], b["points"][-1])))
            b_lo, b_hi = sorted((_arc_at(b["points"], a["points"][0]),
                                 _arc_at(b["points"], a["points"][-1])))
            # the shared stretch must be most of the shorter piece: two
            # pieces that overlap for 13 m of their 70 belong to other
            # partners, and pairing them robbed those partners (Campbell
            # Drive at Flagler Avenue, where the greedy pick by gap alone
            # tied and took the wrong one)
            shared = min(a_hi - a_lo, b_hi - b_lo)
            if shared < max(OVERLAP_MIN, 0.5 * min(length_a, length_b)):
                continue                       # they cross or barely touch
            a_part = _slice(a["points"], a_lo, a_hi)
            b_part = _slice(b["points"], b_lo, b_hi)
            left = _resample(a_part, samples)
            opposite = [_closest_on_polyline(p, b_part) for p in left]
            gaps = [d for _, d in opposite]
            # the median resists the ends, where one carriageway often runs on
            # past the other and the true separation says nothing
            gap = _median(gaps)
            if gap > max_separation:
                continue
            # genuinely parallel roads stay parallel; if a third of the samples
            # are far out, these two are diverging rather than paired
            if sum(1 for d in gaps if d > max_separation) > len(gaps) / 3:
                continue
            scored.append((gap, i, candidates.index(b), left,
                           [q for q, _ in opposite],
                           (a_lo, a_hi, b_lo, b_hi)))

    # Longest shared stretch first, nearest second.  Ordering by gap alone
    # let a 13 m fragment of one carriageway claim the other side's 79 m
    # piece and leave that piece's real partner, 66 m alongside it, single.
    scored.sort(key=lambda s: (-min(s[5][1] - s[5][0], s[5][3] - s[5][2]),
                               s[0]))
    used, merged, overhangs = set(), [], []
    for gap, i, j, left, right, extents in scored:
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        a, b = candidates[i], candidates[j]
        # what each carriageway runs on past the other stays a one-way
        # road; a stub shorter than the weld tolerance is a residual of
        # the fusing, not road, and the weld absorbs its neighbour's end
        a_lo, a_hi, b_lo, b_hi = extents
        for edge, lo, hi in ((a, a_lo, a_hi), (b, b_lo, b_hi)):
            total = polyline_length(edge["points"])
            for s0, s1 in ((0.0, lo), (hi, total)):
                if s1 - s0 > WELD_METRES:
                    overhangs.append(dict(edge, points=_slice(edge["points"],
                                                              s0, s1)))
        centre = [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                  for p, q in zip(left, right)]
        # OSM puts each way down the centre of its own carriageway, so the gap
        # between the two spans half of each plus the divider between them.
        half_carriageways = (a["width"] + b["width"]) / 2.0
        width = gap + half_carriageways          # outer kerb to outer kerb
        median = max(0.0, gap - half_carriageways)
        props = dict(a["props"])
        props.pop("oneway", None)              # it is two-way once fused
        merged.append({"props": props, "points": centre, "width": width,
                       "median": median})

    unmatched = [candidates[i] for i in range(len(candidates)) if i not in used]
    return others + unmatched + merged + overhangs, len(merged), len(overhangs)


def merge_dual_carriageways(edges, max_separation: float, samples: int = 12):
    """Fuse the one-way sides of every dual carriageway (see
    :func:`_merge_pass` for the rules), until nothing more pairs.

    More than one pass, because a pass creates candidates.  Campbell
    Drive's eastbound side runs 866 m unbroken while the westbound one is
    cut at 1325 m by a junction only it touches; the first pass fuses the
    798 m the two share and leaves the eastbound 66 m overhang beside the
    westbound 80 m remainder -- a pair as plain as any, but both were born
    after the greedy pick had run.  The second pass fuses them.
    """
    total = 0
    while True:
        edges, fused, leftovers = _merge_pass(edges, max_separation, samples)
        total += fused
        if not fused or not leftovers:
            return edges, total


def _insert_vertex(points, q):
    """Add `q` to a polyline on the leg it falls on, if not already a vertex."""
    if any(snap(p) == snap(q) for p in points):
        return
    best_index, best_distance = None, float("inf")
    for index, (a, b) in enumerate(zip(points, points[1:])):
        # distance from q to this leg, same projection as _closest_on_polyline
        dx, dy = b[0] - a[0], b[1] - a[1]
        span = dx * dx + dy * dy
        t = 0.0 if span == 0 else ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / span
        t = min(max(t, 0.0), 1.0)
        d = distance(q, (a[0] + t * dx, a[1] + t * dy))
        if d < best_distance:
            best_index, best_distance = index, d
    if best_index is not None:
        points.insert(best_index + 1, q)


def reconnect_to_fused(edges, tolerance: float):
    """Reattach side streets to a divided road after its two sides are fused.

    Fusing moves the road onto the centreline between its carriageways, so
    every street that used to meet it at a carriageway is left ending in mid
    air a few metres short.  Left alone the network falls apart into
    disconnected pieces -- which is what happens to any real extract where a
    divided arterial has side roads, not merely to a synthetic grid.

    Any end lying inside a fused road's own footprint is pulled onto its
    centreline, and the meeting point added to the fused road as a vertex, so
    the later split sees a shared junction there.

    This deliberately does not skip ends that already meet something.  A street
    *crossing* a divided road was split into two pieces at each carriageway, so
    both of those ends have a neighbour and would be passed over -- leaving the
    street running straight through the arterial without joining it.  Both ends
    land on the same point of the centreline, which is the junction.

    The tolerance is the road's own half-width rather than `max_separation`:
    the question is whether the end lies within the road, and a genuinely
    separate street running alongside must not be swallowed.

    A fused road's *own* ends are reattached the same way, to any fused
    road but itself.  The first version skipped them, and a divided side
    street meeting a divided arterial then never joined it: both of its
    carriageways ended at the arterial's carriageways, so its fused end
    landed squarely on the arterial's centreline -- but between two of
    that centreline's vertices, where no node exists.  At Al Malaz
    (Riyadh) Jarir Street, six one-way ways tagged secondary, crossed Al
    Ahsa Street that way and the picker never offered the crossing; it
    appeared only when every residential alley was included, because one
    of those happened to end on the same spot and put a vertex there.
    """
    # A fused road is one that came out of the merge, whether or not any
    # divider was left once the two carriageways' own widths were taken
    # off the gap.  Testing ``median > 0`` missed Musab bin Umair at Al
    # Malaz -- two 11 m carriageways 9 m apart fuse to a 20 m road with no
    # median -- so its centreline never joined Al Ahsa Street, and the
    # whole of Al Ahsa left the network as the smaller component.
    fused = [e for e in edges if "median" in e]
    if not fused:
        return edges

    for edge in edges:
        points = list(edge["points"])
        for index in (0, -1):
            end = points[index]
            best = None
            for road in fused:
                if road is edge:
                    continue
                reach = min(road["width"] / 2.0 + 1.0, tolerance)
                q, d = _closest_on_polyline(end, road["points"])
                if d <= reach and (best is None or d < best[1]):
                    best = (road, d, q)
            if best is not None:
                road, _, q = best
                points[index] = q
                _insert_vertex(road["points"], q)
        edge["points"] = points
    return edges


def resplit(edges):
    """Cut edges again at any point they now share, keeping their attributes.

    Needed after :func:`reconnect_to_fused` introduces new shared vertices part
    way along a road.
    """
    touches = {}
    for edge in edges:
        for key in {snap(p) for p in edge["points"]}:
            touches[key] = touches.get(key, 0) + 1
    out = []
    for edge in edges:
        points = edge["points"]
        cut_at = [0]
        for index in range(1, len(points) - 1):
            if touches.get(snap(points[index]), 0) > 1:
                cut_at.append(index)
        cut_at.append(len(points) - 1)
        for a, b in zip(cut_at, cut_at[1:]):
            piece = points[a:b + 1]
            if len(piece) >= 2 and polyline_length(piece) > 0:
                out.append(dict(edge, points=piece))
    return out


def drop_degenerate(edges):
    """Remove edges whose ends land on the same node.

    Merging a dual carriageway pulls the short links that crossed between its
    two sides down to nothing.
    """
    return [e for e in edges
            if snap(e["points"][0]) != snap(e["points"][-1])
            and polyline_length(e["points"]) > SNAP_METRES]


def weld_endpoints(edges, tolerance: float = WELD_METRES):
    """Move every edge endpoint onto the first earlier endpoint within
    ``tolerance``, so near-coincident ends share a snap cell.

    Found at Shahbag in a 1 km build: the fused Shahbag Road ended 1 m
    from the crossing's node, in the next snap cell, so it never joined;
    ``collapse_and_prune`` then chained it with Elephant Road into a
    711 m edge with both ends dangling and ``largest_component`` threw
    the whole road away -- the crossing came out with three legs.  The
    same residual left 5 m stubs of Kazi Nazrul Islam Avenue hanging
    and pulled Katabon's crossing apart.  Interior vertices are left
    alone: only where edges meet does the grid decide connectivity.
    """
    anchors = []                          # representative points, in order
    for edge in edges:
        pts = edge["points"]
        for index in (0, len(pts) - 1):
            point = pts[index]
            for anchor in anchors:
                if distance(point, anchor) <= tolerance:
                    pts[index] = (anchor[0], anchor[1])
                    break
            else:
                anchors.append((point[0], point[1]))
    return edges


def collapse_and_prune(edges, min_stub: float):
    """Merge chains through degree-2 junctions, then trim short dead ends.

    A degree-2 point is a bend in a road, not an intersection; leaving it as a
    node would give the simulator a junction to arbitrate at every kink.  The
    format carries the shape as multiple segments on one link instead.
    """
    def endpoints(edge):
        return snap(edge["points"][0]), snap(edge["points"][-1])

    changed = True
    while changed:
        changed = False

        incident = {}
        for edge in edges:
            for key in endpoints(edge):
                incident.setdefault(key, []).append(edge)

        # merge through degree-2 points
        for key, touching in incident.items():
            if len(touching) != 2:
                continue
            first, second = touching
            if first is second:
                continue                      # a loop back to itself
            a_start, a_end = endpoints(first)
            b_start, b_end = endpoints(second)
            # orient both so they run start -> key -> end
            a_points = first["points"] if a_end == key else first["points"][::-1]
            b_points = second["points"] if b_start == key else second["points"][::-1]
            if snap(a_points[0]) == snap(b_points[-1]):
                continue                      # merging would close a ring
            # A chain is only one-way if both halves are, running the same
            # way the merged points do.  A reversal here means the two
            # pieces point at (or away from) each other, and a mixed pair
            # means part of the road is two-way -- in either case claiming
            # one-way for the whole chain would be wrong, so drop the tag.
            props = dict(first["props"])
            if _is_oneway(first) or _is_oneway(second):
                aligned = (a_points is first["points"]
                           and b_points is second["points"])
                if not (aligned and _is_oneway(first)
                        and _is_oneway(second)):
                    props.pop("oneway", None)
            merged = {"props": props,
                      "points": a_points + b_points[1:],
                      # a chain is one road, so a median on either half is a
                      # median on the whole of it
                      "median": max(first.get("median", 0.0),
                                    second.get("median", 0.0)),
                      # length-weighted, so a short slip road cannot halve the
                      # width of the road it joins
                      "width": ((first["width"] * polyline_length(first["points"])
                                 + second["width"] * polyline_length(second["points"]))
                                / (polyline_length(first["points"])
                                   + polyline_length(second["points"])))}
            edges = [e for e in edges if e is not first and e is not second]
            edges.append(merged)
            changed = True
            break

        if changed:
            continue

        # trim stubs: a dead end too short to hold traffic
        incident = {}
        for edge in edges:
            for key in endpoints(edge):
                incident.setdefault(key, []).append(edge)
        for edge in edges:
            start, end = endpoints(edge)
            dangling = len(incident[start]) == 1 or len(incident[end]) == 1
            if dangling and polyline_length(edge["points"]) < min_stub:
                edges = [e for e in edges if e is not edge]
                changed = True
                break

    return edges


def largest_component(edges):
    """Keep only the biggest connected piece.

    An extract cut to a bounding box strands fragments that no route can
    reach; leaving them in gives the route generator origins with no
    destinations.
    """
    if not edges:
        return edges
    adjacency = {}
    for index, edge in enumerate(edges):
        a, b = snap(edge["points"][0]), snap(edge["points"][-1])
        adjacency.setdefault(a, []).append((b, index))
        adjacency.setdefault(b, []).append((a, index))

    seen_nodes, best = set(), []
    for start in adjacency:
        if start in seen_nodes:
            continue
        stack, component_nodes, component_edges = [start], {start}, set()
        while stack:
            node = stack.pop()
            for neighbour, index in adjacency[node]:
                component_edges.add(index)
                if neighbour not in component_nodes:
                    component_nodes.add(neighbour)
                    stack.append(neighbour)
        seen_nodes |= component_nodes
        if len(component_edges) > len(best):
            best = sorted(component_edges)
    return [edges[i] for i in best]


def simplify(points, tolerance: float):
    """Douglas-Peucker, to keep a link's segment count sane.

    OSM traces curves with many closely spaced points; each would otherwise
    become a segment the simulator has to hand vehicles between.
    """
    if len(points) <= 2 or tolerance <= 0:
        return points
    start, end = points[0], points[-1]
    span = distance(start, end)
    worst_index, worst = 0, -1.0
    for index in range(1, len(points) - 1):
        point = points[index]
        if span == 0:
            deviation = distance(point, start)
        else:
            deviation = abs((end[0] - start[0]) * (start[1] - point[1])
                            - (start[0] - point[0]) * (end[1] - start[1])) / span
        if deviation > worst:
            worst_index, worst = index, deviation
    if worst <= tolerance:
        return [start, end]
    return (simplify(points[:worst_index + 1], tolerance)[:-1]
            + simplify(points[worst_index:], tolerance))


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def write_network(edges, out_dir: str, margin: float, tolerance: float,
                  source: str, centre, note: str = "",
                  generator: str = "") -> dict:
    edges = [dict(e, points=simplify(e["points"], tolerance)) for e in edges]

    # shift into positive coordinates with a margin, as the shipped networks do
    xs = [p[0] for e in edges for p in e["points"]]
    ys = [p[1] for e in edges for p in e["points"]]
    dx, dy = margin - min(xs), margin - min(ys)
    for edge in edges:
        edge["points"] = [(p[0] + dx, p[1] + dy) for p in edge["points"]]

    # Whole-metre rounding can collapse a very short link -- two crossings
    # under a metre apart -- to a single point.  Such a link cannot be
    # written (no segments, no direction), so its two junctions become one:
    # the edge goes, and every edge that ended on its far end is moved onto
    # its near end.  Repeated, because a merge can shorten a neighbour to
    # the same fate.
    while True:
        collapsed = next(
            (e for e in edges
             if len({(round(p[0]), round(p[1])) for p in e["points"]}) < 2),
            None)
        if collapsed is None:
            break
        keep, drop = collapsed["points"][0], collapsed["points"][-1]
        edges.remove(collapsed)
        for edge in edges:
            pts = edge["points"]
            if snap(pts[0]) == snap(drop):
                pts[0] = keep
            if snap(pts[-1]) == snap(drop):
                pts[-1] = keep

    node_ids, node_xy = {}, {}
    for edge in edges:
        for point in (edge["points"][0], edge["points"][-1]):
            key = snap(point)
            if key not in node_ids:
                node_ids[key] = len(node_ids)
                node_xy[key] = point

    node_links = {key: [] for key in node_ids}
    links = []
    for index, edge in enumerate(edges):
        start, end = snap(edge["points"][0]), snap(edge["points"][-1])
        links.append({"id": index, "up": node_ids[start], "down": node_ids[end],
                      "points": edge["points"], "width": edge["width"],
                      "props": edge["props"],
                      "median": edge.get("median", 0.0)})
        node_links[start].append(index)
        node_links[end].append(index)

    os.makedirs(out_dir, exist_ok=True)

    # The file format, the whole-metre rounding and the zero-length-segment
    # defence live in dhakasim.network_files, shared with fit_roads.
    network_files.write_link_rows(
        os.path.join(out_dir, "link.txt"),
        [(link["id"], link["up"], link["down"],
          [(a[0], a[1], b[0], b[1], link["width"])
           for a, b in zip(link["points"], link["points"][1:])])
         for link in links])

    node_rows = []
    for key, node_id in sorted(node_ids.items(), key=lambda kv: kv[1]):
        incident = node_links[key]
        # A junction is written at 0 0: the simulator treats that as "not a
        # boundary point" and excludes it from the view's bounding box,
        # taking the junction's real position from where its links meet.
        # Only the degree-1 nodes, where vehicles enter and leave, carry
        # coordinates.
        x, y = (0, 0) if len(incident) > 1 else node_xy[key]
        node_rows.append((node_id, x, y, incident))
    network_files.write_node_rows(os.path.join(out_dir, "node.txt"), node_rows)

    boundary = [k for k in node_ids if len(node_links[k]) == 1]
    divided = [link for link in links if link["median"] > 0]
    with open(os.path.join(out_dir, "geometry.txt"), "w", encoding="utf-8") as f:
        f.write(f"# Generated by make_network.py from {os.path.basename(source)}.\n")
        # "at x,y" is the centre's own position in network metres -- the
        # projector puts the centre at the origin, so after the shift into
        # positive coordinates it sits exactly at (dx, dy).  basemap.py
        # anchors the imagery there; without it the anchor has to be guessed
        # from the network's shape, and an asymmetric extract guesses wrong
        # by tens of metres.
        f.write(f"# Centre {centre[0]:.6f},{centre[1]:.6f} "
                f"at {dx:.1f},{dy:.1f}.\n")
        if "overpass" in generator.lower() or "osm" in generator.lower():
            f.write("# Data (c) OpenStreetMap contributors, ODbL.\n")
        elif generator:
            f.write(f"# Source: {generator}.\n")
        if note:
            for line in note.split("\n"):
                f.write(f"# {line}\n")
        f.write("#\n# Applied when GeometryMode is On, which it is by "
                "default.\n")
        if divided:
            f.write("# Medians, measured as the gap between the two one-way\n"
                    "# carriageways OSM maps a divided road as.\n")
            for link in divided:
                name = link["props"].get("name", "")
                comment = f"   # {name}" if name else ""
                f.write(f"median {link['id']} {link['median']:.1f}{comment}\n")
        else:
            f.write("# No divided roads detected in the extract.\n")
        oneways = [link for link in links if _is_oneway(link)]
        if oneways:
            f.write("# One-way carriageways.  Travel runs up -> down (the\n"
                    "# order the link's segments are written in); the\n"
                    "# simulator opens the full width to that direction and\n"
                    "# run_sim.py routes nothing against it.\n")
            for link in oneways:
                name = link["props"].get("name", "")
                comment = f"   # {name}" if name else ""
                f.write(f"oneway {link['id']}{comment}\n")

    # Street names, where the source carried them.  Written separately rather
    # than into link.txt so the simulator's own format stays byte-compatible
    # with the Java original's reader.
    named = [link for link in links if link["props"].get("name")]
    if named:
        with open(os.path.join(out_dir, "link_names.txt"), "w",
                  encoding="utf-8") as f:
            for link in named:
                f.write(f"{link['id']} {link['props']['name']}\n")

    total_km = sum(polyline_length(link["points"]) for link in links) / 1000.0
    return {"links": len(links), "nodes": len(node_ids),
            "junctions": len(node_ids) - len(boundary), "boundary": len(boundary),
            "km": total_km,
            "segments": sum(len(link["points"]) - 1 for link in links)}


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("geojson", help="overpass-turbo GeoJSON export")
    parser.add_argument("--out", required=True, help="e.g. input/miami")
    parser.add_argument("--centre", help="lat,lon; default is the extract's middle")
    parser.add_argument("--radius", type=float,
                        help="metres from the centre to keep")
    parser.add_argument("--classes", nargs="+", default=list(DEFAULT_CLASSES))
    parser.add_argument("--min-stub", type=float, default=40.0,
                        help="drop dead ends shorter than this (metres)")
    parser.add_argument("--tolerance", type=float, default=8.0,
                        help="Douglas-Peucker tolerance for link shape (metres)")
    parser.add_argument("--margin", type=float, default=30.0,
                        help="left/top margin in the output coordinates")
    parser.add_argument("--keep-fragments", action="store_true",
                        help="keep pieces disconnected from the main network")
    parser.add_argument("--no-merge-dual", action="store_true",
                        help="import each carriageway of a divided road as its "
                             "own link, instead of fusing the pair")
    parser.add_argument("--note", default="",
                        help="provenance line(s) to record in geometry.txt")
    parser.add_argument("--max-separation", type=float, default=45.0,
                        help="widest gap between two carriageways still taken "
                             "to be one divided road (metres)")
    args = parser.parse_args(argv)

    ways = load_ways(args.geojson, set(args.classes))
    if not ways:
        print(f"no roads of the requested classes in {args.geojson}")
        return 1
    ways, roundabouts = collapse_roundabouts(ways)
    if roundabouts:
        print(f"{roundabouts} roundabout ring(s) collapsed to a point; "
              "declare it with a roundabout line in geometry.txt (the "
              "import dialog's picker offers the choice)")

    if args.centre:
        lat0, lon0 = (float(v) for v in args.centre.split(","))
    else:
        lats = [c[1] for _, coords in ways for c in coords]
        lons = [c[0] for _, coords in ways for c in coords]
        lat0, lon0 = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2

    projector = Projector(lat0, lon0)
    edges = build_edges(ways, projector, (0.0, 0.0), args.radius)
    if not edges:
        print("nothing left after clipping; widen --radius")
        return 1
    fused = 0
    if not args.no_merge_dual:
        edges = chain_oneway_pieces(edges)
        edges, fused = merge_dual_carriageways(edges, args.max_separation)
        edges = drop_degenerate(edges)
        if fused:
            edges = reconnect_to_fused(edges, args.max_separation)
            edges = resplit(edges)
            # snapping both sides of a crossing onto one point leaves the piece
            # that spanned the old carriageways with no length
            edges = drop_degenerate(edges)
    # Before the chains are merged: a fused centreline's ends are a metre
    # or so off the vertex the approach roads share, and the snap grid
    # would read that as two nodes (see weld_endpoints).
    edges = weld_endpoints(edges)
    # welding both ends of a metre-long residual onto one anchor leaves a
    # zero-length edge, which the writer refuses
    edges = drop_degenerate(edges)
    edges = collapse_and_prune(edges, args.min_stub)
    if not args.keep_fragments:
        edges = largest_component(edges)
    if not edges:
        print("nothing left after pruning")
        return 1

    stats = write_network(edges, args.out, args.margin, args.tolerance,
                          args.geojson, (lat0, lon0), args.note,
                          read_generator(args.geojson))
    print(f"{args.out}: {stats['links']} links ({stats['segments']} segments), "
          f"{stats['nodes']} nodes "
          f"({stats['junctions']} junctions, {stats['boundary']} boundary), "
          f"{stats['km']:.2f} km")
    print(f"centre {lat0:.6f},{lon0:.6f}"
          + (f", radius {args.radius:.0f} m" if args.radius else "")
          + (f", {fused} divided roads fused" if fused else ""))
    if stats["boundary"] < 2:
        print("WARNING: fewer than two boundary nodes, so run_sim.py will have "
              "no origin/destination pairs to route between")
    print(f"next: python run_sim.py --network {os.path.basename(args.out.rstrip('/\\'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
