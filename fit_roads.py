#!/usr/bin/env python3
"""Bend a network's links onto the real road centrelines from OpenStreetMap.

The surveyed Dhaka networks were transcribed by hand as one straight segment
per link, so an arm that curves in reality is drawn as a chord across the
curve.  Over aerial imagery that is obvious: the model leaves the road and
comes back.  This replaces each link's straight line with a chain of segments
that follows the road it represents.

    python fit_roads.py --list
    python fit_roads.py --network khamarbari --dry-run
    python fit_roads.py --network khamarbari
    python fit_roads.py --restore --network khamarbari

**This changes the model, not just the picture.**  A curve is longer than its
chord, so travel distances, journey times and every speed statistic move with
it.  Results from before a fit and after it are not comparable, and the paper
comparison in ``experiments/`` was run against the straight networks.  The
original ``link.txt`` is copied to ``link.straight.txt`` before anything is
written, and ``--restore`` puts it back.

What is deliberately *not* changed
----------------------------------

Link ids, node ids, which nodes a link joins, and carriageway widths.  Those
are what ``demand.txt``, ``path.txt`` and ``vehicle_mix.txt`` are keyed by, and
they carry survey data that took real work to collect.  Only the shape of each
link changes, so nothing else in the network folder needs regenerating.

Where the endpoints go
----------------------

The interior of a link is easy: every sample is free to move on its own.  The
ends are not, because every arm at a junction shares one point and a boundary
node repeats its arm's endpoint in ``node.txt``.  Pinning them was the first
answer and it was visibly wrong: the middles landed on the road to within a
metre or two while the ends stayed ten to forty-five metres out, so each arm
ran off its street exactly where a reader looks first.

So the fit runs twice.  The first pass lets both ends go and only reads off
where each link would like its ends to be; the arms at a node then vote, and
the node moves to their mean.  The second pass is the pinned fit again, with
the stated line's ends already sitting on the node's new position, which is
what keeps the taper and the smoothness of the original approach.

One node never moves: the one :func:`basemap.anchor_point` picks.  The whole
georeference hangs off it -- it is the single control point tying network
metres to the Earth -- so moving it would slide the imagery along with the
roads and gain nothing at all.

``node.txt`` is rewritten for the nodes that state their own coordinates, and
``node.straight.txt`` is the original.

A roundabout is the exception.  Its arms are not pinned to the node at all;
their ends are placed afterwards by :func:`aim_arms_at_the_circle`, which
brings each arm's *mouth* as near the circle as its own road allows while
keeping the mean of the stated ends exactly on the anchor.  Read that
function for why the two do not fight.

Dual carriageways
-----------------

One link can stand for two OSM ways with a gap between them -- that is what a
``median`` line in ``geometry.txt`` says -- and then "nearest road" is the
wrong target.  It scores such a link as perfectly fitted the moment it lands
on *either* carriageway, and the far half of the band ends up on open ground:
Manik Mia Avenue was eleven metres out for precisely this reason and read as
the worst misalignment on the network.  Those links aim at the middle of the
pair instead, and the pull is chased rather than stepped, because the middle
moves as the line does.

The kerb-edge convention
------------------------

A segment's stated x/y in ``link.txt`` is one **kerb edge**, not the
centreline: the carriageway lies to the left of the direction of travel, half a
width away.  So the fit works on the centreline -- shifting the stated line
onto it, bending that onto the road, and shifting back -- because snapping the
stated line straight onto a road centreline would leave every carriageway
half a width out.

Judging the result
------------------

Distance from the fitted centreline to the nearest OSM road is the obvious
score and it is misleading, for the reason above: a band sitting squarely on
one half of a dual carriageway reads as a perfect fit.  Khamarbari's median
distance went *up*, 1.2 m to 3.1 m, when Manik Mia Avenue was moved onto its
median -- and the share of the drawn carriageway lying on a road the map
actually paints went from 41% to 51%.  Prefer the second question.

Standard library only.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from dhakasim import basemap
from dhakasim import road_geometry


#: Road classes worth matching against, the same set ``make_network.py`` keeps.
ROAD_CLASSES = (
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "residential", "unclassified", "living_street",
)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = ("DhakaSim-fit-roads/1.0 (traffic simulation research; "
              "one-off network geometry fit)")

#: Cached Overpass answer, so re-running costs nothing and the fit is
#: reproducible against the data it was made from.
CACHE_NAME = "osm_roads.json"

#: Original straight geometry, kept beside the fitted one.
BACKUP_NAME = "link.straight.txt"
NODE_BACKUP_NAME = "node.straight.txt"

#: Two OSM vertices closer than this are treated as the same graph node.  Ways
#: that meet share a vertex exactly, but coordinates round-trip through text.
SNAP_METRES = 0.5

#: How far past its own kerb a dual-carriageway link looks for the other half
#: of the road.  It has to clear the far carriageway's centreline, which sits
#: just inside the link's own edge, without reaching the next street over.
SPAN_MARGIN = 10.0

# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def distance(p, q) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def polyline_length(points) -> float:
    return sum(distance(points[i], points[i + 1])
               for i in range(len(points) - 1))


def point_to_segment(p, a, b) -> float:
    """Distance from *p* to the segment *a*-*b*."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return distance(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify(points, tolerance: float):
    """Douglas-Peucker, iteratively so a long way cannot blow the stack."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, worst_at = -1.0, None
        for i in range(first + 1, last):
            d = point_to_segment(points[i], points[first], points[last])
            if d > worst:
                worst, worst_at = d, i
        if worst_at is not None and worst > tolerance:
            keep[worst_at] = True
            stack.append((first, worst_at))
            stack.append((worst_at, last))
    return [p for p, k in zip(points, keep) if k]


def drop_short(points, minimum: float):
    """Remove vertices that would leave a segment shorter than *minimum*.

    A zero-length segment divides by zero on its way to a perpendicular, in two
    separate places in ``road_geometry``, so it must never reach the file.
    """
    if len(points) < 2:
        return list(points)
    out = [points[0]]
    for p in points[1:-1]:
        if distance(out[-1], p) >= minimum:
            out.append(p)
    # The last point is pinned, so pull back any vertex crowding it instead.
    while len(out) > 1 and distance(out[-1], points[-1]) < minimum:
        out.pop()
    out.append(points[-1])
    return out


def offset_polyline(points, distance_m: float):
    """Shift a polyline sideways by *distance_m*, positive to the left.

    "Left" is the normal ``(-dy, dx)`` of the direction of travel, which is the
    side ``Utilities.return_x3`` puts the carriageway on.  Interior vertices
    move along the bisector of their two segment normals, which keeps the
    result continuous; the bends here are gentle enough not to need the
    miter-length correction a general offsetter would apply.
    """
    if len(points) < 2:
        return list(points)
    normals = []
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length = math.hypot(dx, dy)
        normals.append((0.0, 0.0) if length <= 1e-12
                       else (-dy / length, dx / length))
    out = []
    for i, point in enumerate(points):
        if i == 0:
            nx, ny = normals[0]
        elif i == len(points) - 1:
            nx, ny = normals[-1]
        else:
            ax, ay = normals[i - 1]
            bx, by = normals[i]
            nx, ny = ax + bx, ay + by
            length = math.hypot(nx, ny)
            nx, ny = (ax, ay) if length <= 1e-12 else (nx / length, ny / length)
        out.append((point[0] + nx * distance_m, point[1] + ny * distance_m))
    return out


# --------------------------------------------------------------------------
# OpenStreetMap
# --------------------------------------------------------------------------

def fetch_ways(network: str, south, west, north, east, refresh=False):
    """Road ways covering a bounding box, cached in the network folder."""
    path = basemap.network_path(network, CACHE_NAME)
    if os.path.exists(path) and not refresh:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    classes = "|".join(ROAD_CLASSES)
    query = (f'[out:json][timeout:90];'
             f'way["highway"~"^({classes})$"]'
             f'({south:.6f},{west:.6f},{north:.6f},{east:.6f});'
             f'out geom;')
    request = urllib.request.Request(
        OVERPASS_URL, data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": USER_AGENT})
    # Overpass is a shared free service and answers 429 when it is busy, so
    # a refusal is a reason to wait rather than to give up.
    payload = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 504) or attempt == 3:
                raise SystemExit(f"Overpass request failed: {exc}")
            wait = 20 * (attempt + 1)
            print(f"  Overpass busy ({exc.code}); waiting {wait}s")
            time.sleep(wait)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SystemExit(f"Overpass request failed: {exc}")

    ways = [{"tags": e.get("tags", {}),
             "geometry": [(g["lon"], g["lat"]) for g in e.get("geometry", [])]}
            for e in payload.get("elements", [])
            if e.get("type") == "way" and e.get("geometry")]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(ways, handle)
    return ways


def road_segments(ways, geo):
    """Every OSM way turned into straight pieces, in network metres.

    Returned as ``(a, b, ux, uy, class)`` with the unit direction
    precomputed, since the bearing test below runs for every candidate of
    every sample point.  The class is the ``highway`` tag, which is what tells
    the two halves of a dual carriageway apart from the side street running
    alongside them: they are always tagged the same, and it is not.
    """
    pieces = []
    for way in ways:
        klass = (way.get("tags") or {}).get("highway")
        projected = [geo.to_xy(lon, lat) for lon, lat in way["geometry"]]
        for a, b in zip(projected, projected[1:]):
            length = distance(a, b)
            if length <= 1e-9:
                continue
            pieces.append((a, b, (b[0] - a[0]) / length, (b[1] - a[1]) / length,
                           klass))
    return pieces


def build_index(pieces, cell: float):
    """Bucket road pieces into a square grid, so a lookup reads a few cells.

    Every piece is registered in each cell its bounding box touches, which is
    enough because the pieces are short compared with the cell.
    """
    index = {}
    for piece in pieces:
        (ax, ay), (bx, by), _ux, _uy, _klass = piece
        for cx in range(int(min(ax, bx) // cell), int(max(ax, bx) // cell) + 1):
            for cy in range(int(min(ay, by) // cell), int(max(ay, by) // cell) + 1):
                index.setdefault((cx, cy), []).append(piece)
    return index


def closest_on_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return a
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
    return (a[0] + t * dx, a[1] + t * dy)


def nearest_road_point(index, cell, point, ux, uy, limit, cos_limit):
    """The nearest point on a road running roughly the same way as *ux, uy*.

    The bearing test is what stops an arm being pulled sideways onto the street
    it crosses: without it the nearest road to a point near a junction is
    frequently the other one.  Either digitising direction counts, hence the
    absolute value.
    """
    cx, cy = int(point[0] // cell), int(point[1] // cell)
    best, best_d = None, limit
    for gx in (cx - 1, cx, cx + 1):
        for gy in (cy - 1, cy, cy + 1):
            for a, b, sx, sy, _klass in index.get((gx, gy), ()):
                if abs(sx * ux + sy * uy) < cos_limit:
                    continue
                q = closest_on_segment(point, a, b)
                d = distance(point, q)
                if d < best_d:
                    best, best_d = q, d
    return best


def spanning_road_point(index, cell, point, ux, uy, limit, cos_limit):
    """The middle of the roads running the same way, not the nearest of them.

    A dual carriageway is two OSM ways with a gap between them, and one link
    stands for both -- that is what ``geometry.txt``'s ``median`` line says.
    :func:`nearest_road_point` scores such a link as perfectly fitted the
    moment it lands on *either* carriageway, and a thirty-two metre band
    centred on one eleven metre carriageway has its whole far half lying on
    open ground.  Khamarbari's Manik Mia Avenue was eleven metres out for
    exactly this reason and read as the worst misalignment on the network.

    So take the extreme lateral offsets of everything parallel within reach
    and aim at their midpoint.  Extremes rather than a mean, because the two
    carriageways are digitised with different numbers of vertices and a mean
    would follow whichever is busier.

    **Only roads of the same class count.**  A dual carriageway is one road
    tagged twice, so both halves carry the same ``highway`` value; a service
    road, a slip road or the side street running alongside does not.  Without
    that test Bijoy Sarani's northern arm counted Agargaon Link Road --
    secondary, eighteen metres away, an entirely different street -- as the
    far half of trunk Old Airport Road, and settled twelve metres east of
    where the pair actually is.
    """
    cx, cy = int(point[0] // cell), int(point[1] // cell)
    nx, ny = -uy, ux
    cells = [index.get((gx, gy), ())
             for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1)]

    wanted, best_d = None, limit
    for bucket in cells:
        for a, b, sx, sy, klass in bucket:
            if abs(sx * ux + sy * uy) < cos_limit:
                continue
            d = distance(point, closest_on_segment(point, a, b))
            if d < best_d:
                wanted, best_d = klass, d
    if best_d >= limit:
        return None

    low = high = None
    for bucket in cells:
        for a, b, sx, sy, klass in bucket:
            if klass != wanted or abs(sx * ux + sy * uy) < cos_limit:
                continue
            q = closest_on_segment(point, a, b)
            if distance(point, q) > limit:
                continue
            lateral = (q[0] - point[0]) * nx + (q[1] - point[1]) * ny
            low = lateral if low is None else min(low, lateral)
            high = lateral if high is None else max(high, lateral)
    if low is None:
        return None
    middle = (low + high) / 2.0
    return (point[0] + nx * middle, point[1] + ny * middle)


def resample(points, step: float):
    """Points every *step* metres along a polyline, ends included."""
    total = polyline_length(points)
    if total <= step:
        return [points[0], points[-1]]
    count = max(2, int(round(total / step)) + 1)
    out, target, walked, at = [], 0.0, 0.0, 0
    spacing = total / (count - 1)
    for i in range(count):
        target = spacing * i
        while at < len(points) - 2 and walked + distance(points[at], points[at + 1]) < target:
            walked += distance(points[at], points[at + 1])
            at += 1
        leg = distance(points[at], points[at + 1])
        t = 0.0 if leg <= 1e-12 else min(1.0, (target - walked) / leg)
        out.append((points[at][0] + (points[at + 1][0] - points[at][0]) * t,
                    points[at][1] + (points[at + 1][1] - points[at][1]) * t))
    return out


def smooth(values, window: int):
    """Moving average over a list of (dx, dy), keeping the list length."""
    if window <= 1 or len(values) < 3:
        return list(values)
    half = window // 2
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        chunk = values[lo:hi]
        out.append((sum(v[0] for v in chunk) / len(chunk),
                    sum(v[1] for v in chunk) / len(chunk)))
    return out


def snap_to_roads(centre, index, cell, snap_limit, max_offset, cos_limit,
                  sample_step=8.0, smooth_window=5, taper=0.18,
                  pin_start=True, pin_end=True, span=False, chase=3):
    """Bend a link's centreline onto the roads underneath it.

    Sample along the line, pull each sample to the nearest road running the
    same way, smooth the pulls so the result is a curve rather than a rattle,
    and taper them to nothing at whichever ends are pinned, so the link still
    starts and finishes where the network says it does.

    An unpinned end takes its pull in full and is not tapered.  That is the
    first pass, which exists only to ask each link where it would like its
    ends; the ends that survive into ``link.txt`` are pinned, to a node
    position the arms agreed on -- except at a roundabout, where the arm's end
    is placed afterwards by :func:`aim_arms_at_the_circle` instead.

    *span* switches the pull from the nearest road to the middle of the
    parallel ones, which is what a link standing for a dual carriageway needs.

    Returns the new centreline and how many samples found a road, which is the
    honest measure of whether this link was fitted or merely left alone.
    """
    samples = resample(centre, sample_step)
    matched = 0
    find = spanning_road_point if span else nearest_road_point
    pulls = [(0.0, 0.0)] * len(samples)
    # Chase the road rather than stepping at it once.  One step is short of
    # the mark whenever the target moves with the line, which is exactly the
    # dual-carriageway case: standing on one carriageway the middle of the
    # pair is half the gap away, and having gone there the answer has changed.
    # Re-aiming from the moved point, while measuring the pull from the fixed
    # sample, settles it in two or three turns and -- because the smoothing
    # and the simplification still happen once, afterwards -- adds none of the
    # wiggle that repeating the whole fit does.
    for _ in range(max(1, chase)):
        matched = 0
        stepped = []
        for i, base in enumerate(samples):
            point = (base[0] + pulls[i][0], base[1] + pulls[i][1])
            j = min(i, len(samples) - 2)
            dx = samples[j + 1][0] - samples[j][0]
            dy = samples[j + 1][1] - samples[j][1]
            length = math.hypot(dx, dy)
            if length <= 1e-12:
                stepped.append((0.0, 0.0))
                continue
            target = find(index, cell, point, dx / length, dy / length,
                          snap_limit, cos_limit)
            if target is None:
                stepped.append(pulls[i])
                continue
            matched += 1
            px, py = target[0] - base[0], target[1] - base[1]
            reach = math.hypot(px, py)
            if reach > max_offset:                   # never yank it that far
                px, py = px / reach * max_offset, py / reach * max_offset
            stepped.append((px, py))
        pulls = stepped

    pulls = smooth(pulls, smooth_window)
    count = len(samples)
    moved = []
    for i, (point, (px, py)) in enumerate(zip(samples, pulls)):
        t = i / (count - 1) if count > 1 else 0.0
        # Ramp in and out, so a pinned end is approached smoothly instead of
        # with a kink.  A free end has nothing to ramp towards.
        weight = 1.0
        if pin_start and t < taper:
            weight = min(weight, t / taper)
        if pin_end and t > 1.0 - taper:
            weight = min(weight, (1.0 - t) / taper)
        moved.append((point[0] + px * weight, point[1] + py * weight))
    if pin_start:
        moved[0] = samples[0]
    if pin_end:
        moved[-1] = samples[-1]
    return moved, matched, count


# --------------------------------------------------------------------------
# fitting one network
# --------------------------------------------------------------------------

def read_link_file(network: str):
    """``link.txt`` as ``[(id, up, down, [(sx, sy, ex, ey, width), ...])]``."""
    with open(basemap.network_path(network, "link.txt"),
              "r", encoding="utf-8") as handle:
        tokens = handle.read().split()
    at = 0
    count = int(tokens[at]); at += 1
    links = []
    for _ in range(count):
        link_id, up, down, segments = (int(tokens[at + i]) for i in range(4))
        at += 4
        rows = []
        for _ in range(segments):
            values = tokens[at:at + 6]
            at += 6
            rows.append(tuple(float(v) for v in values[1:]))
        links.append((link_id, up, down, rows))
    return links


def read_node_file(network: str):
    """``node.txt`` as ``[(id, x, y, [link id, ...])]``."""
    with open(basemap.network_path(network, "node.txt"),
              "r", encoding="utf-8") as handle:
        lines = [line.split() for line in handle if line.split()]
    count = int(lines[0][0])
    nodes = []
    for parts in lines[1:1 + count]:
        nodes.append((int(parts[0]), float(parts[1]), float(parts[2]),
                      [int(v) for v in parts[3:]]))
    return nodes


def write_node_file(network: str, nodes) -> None:
    path = basemap.network_path(network, "node.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(nodes)}\n")
        for node_id, x, y, link_ids in nodes:
            arms = "".join(f" {i}" for i in link_ids)
            handle.write(f"{node_id} {x:.0f} {y:.0f}{arms}\n")


def write_link_file(network: str, links) -> None:
    path = basemap.network_path(network, "link.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{len(links)}\n")
        for link_id, up, down, rows in links:
            handle.write(f"{link_id} {up} {down} {len(rows)}\n")
            for index, (sx, sy, ex, ey, width) in enumerate(rows):
                handle.write(f"{index} {sx:.0f} {sy:.0f} {ex:.0f} {ey:.0f} "
                             f"{width:.1f}\n")


def stated_line(rows):
    """A link's stated polyline: its kerb edge, as read from ``link.txt``."""
    return [(rows[0][0], rows[0][1])] + [(r[2], r[3]) for r in rows]


def anchor_node_id(network, link_list, node_list):
    """The node holding the georeference, which must not be allowed to move.

    :func:`basemap.anchor_point` answers with a coordinate rather than a node,
    because a coordinate is all the imagery needs.  Here it has to be matched
    back to the node it came from, since everything downstream is keyed by
    node id.  Its last rule falls back to the middle of the bounding box,
    which is not a node at all; then nothing is pinned, and correctly so -- a
    network placed by its bounding box has no control point to protect.
    """
    anchor = basemap.anchor_point(link_list, node_list,
                                  basemap.read_roundabout(network))
    if anchor is None:
        return None
    for node in node_list:
        px, py = road_geometry.node_point(link_list, node)
        if abs(px - anchor[0]) < 1e-6 and abs(py - anchor[1]) < 1e-6:
            return node.get_id()
    return None


def read_medians(network: str):
    """Link ids that ``geometry.txt`` says carry a median.

    That declaration is the network's own statement that the link stands for a
    dual carriageway -- two OSM ways with a gap between them -- which is what
    :func:`spanning_road_point` needs to know.
    """
    try:
        with open(basemap.network_path(network, "geometry.txt"),
                  "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "median":
            try:
                out.add(int(parts[1]))
            except ValueError:
                pass
    return out


def read_straight(network: str):
    """Link ids that ``geometry.txt`` asks to be left as the survey drew them.

    ``straight 0`` on its own line.  Some arms are better as the chord: the
    fit bends towards whatever road is nearest and same-ish bearing, and where
    a station forecourt, a slip road or a car park entrance runs alongside the
    real street that pull is real but wrong.  Bijoy Sarani's western arm came
    out wandering twenty-nine metres off its own chord for that reason.

    The simulator's own reader ignores directives it does not know, so this
    costs nothing there.
    """
    try:
        with open(basemap.network_path(network, "geometry.txt"),
                  "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and parts[0].lower() == "straight":
            try:
                out.add(int(parts[1]))
            except ValueError:
                pass
    return out


def fit_circle(points):
    """Least-squares circle through *points*, as ``(cx, cy, radius)``.

    Kasa's algebraic fit: exact for points on a circle and good enough for a
    digitised loop, which is all this is ever asked about.
    """
    n = len(points)
    if n < 3:
        return None
    sx = sum(p[0] for p in points) / n
    sy = sum(p[1] for p in points) / n
    suu = suv = svv = suuu = svvv = suvv = svuu = 0.0
    for x, y in points:
        u, v = x - sx, y - sy
        suu += u * u
        suv += u * v
        svv += v * v
        suuu += u * u * u
        svvv += v * v * v
        suvv += u * v * v
        svuu += v * u * u
    det = suu * svv - suv * suv
    if abs(det) < 1e-9:
        return None
    c1 = (suuu + suvv) / 2.0
    c2 = (svvv + svuu) / 2.0
    cx = sx + (c1 * svv - c2 * suv) / det
    cy = sy + (suu * c2 - suv * c1) / det
    radius = sum(distance((x, y), (cx, cy)) for x, y in points) / n
    return (cx, cy, radius)


def osm_circle(ways, geo):
    """The circle OSM's ``junction=roundabout`` loop draws, in network metres.

    Returned as ``(cx, cy, radius, rms)``.  The loop is somebody's tracing of
    the circulating carriageway rather than a survey, so its *radius* is not
    to be trusted as the island; the *centre* is another matter, because a
    closed loop pins a centre far better than it pins a radius, and at
    Khamarbari it agrees to within a metre with a circle fitted to the island
    kerb the map draws separately.
    """
    points = []
    for way in ways:
        if (way.get("tags") or {}).get("junction") != "roundabout":
            continue
        points.extend(geo.to_xy(lon, lat) for lon, lat in way["geometry"])
    circle = fit_circle(points)
    if circle is None:
        return None
    cx, cy, radius = circle
    rms = math.sqrt(sum((distance(p, (cx, cy)) - radius) ** 2
                        for p in points) / len(points))
    return (cx, cy, radius, rms)


def aim_arms_at_the_circle(arms, centre, anchor):
    """Bring every arm's mouth as near *centre* as its own road allows.

    An arm's stated line is a kerb, so its *mouth* -- the middle of the
    carriageway where it meets the junction -- is half a width off to one
    side.  With every arm ending on the same coordinate, as a survey draws a
    junction, the mouths pinwheel round it instead of straddling it, by half a
    width each: sixteen metres for Manik Mia Avenue.  Aiming the mouths at the
    circle instead is what makes the arms meet the ring squarely.

    Two freedoms, and the trick is that they do not fight.  Across the arm
    nothing is on offer -- the carriageway is on its road and must stay there,
    so if that road passes the circle off-centre the mouth does too, and all
    this can do is stop *adding* half a width to the miss.  The stated end
    therefore goes to the foot of the perpendicular from
    ``centre - (w/2) * normal``.  *Along* the arm nothing is fixed at all,
    because the processor cuts every arm back to the ring anyway and the tail
    inside is discarded.  That slack is spent keeping the mean of the stated
    ends exactly on *anchor*, which is the point the whole georeference hangs
    off: move it and the imagery slides with it.

    *arms* is ``[(direction, normal, half_width, line_point), ...]``, all in
    network metres, with *direction* the unit vector pointing at the junction.
    Returns the new stated end for each arm, in the same order.
    """
    feet, dirs = [], []
    for (dx, dy), (nx, ny), half, (px, py) in arms:
        wanted = (centre[0] - nx * half, centre[1] - ny * half)
        along = (wanted[0] - px) * dx + (wanted[1] - py) * dy
        feet.append((px + dx * along, py + dy * along))
        dirs.append((dx, dy))

    # Slide along the arms by the least amount that lands the mean on the
    # anchor: minimise sum(t^2) subject to sum(t * direction) = residual.
    n = len(feet)
    rx = anchor[0] * n - sum(p[0] for p in feet)
    ry = anchor[1] * n - sum(p[1] for p in feet)
    a = sum(dx * dx for dx, _dy in dirs)
    b = sum(dx * dy for dx, dy in dirs)
    c = sum(dy * dy for _dx, dy in dirs)
    det = a * c - b * b
    if abs(det) < 1e-9:                    # every arm parallel: nothing to do
        return feet
    lx = (c * rx - b * ry) / det
    ly = (a * ry - b * rx) / det
    return [(px + dx * (dx * lx + dy * ly), py + dy * (dx * lx + dy * ly))
            for (px, py), (dx, dy) in zip(feet, dirs)]


def agree_on_nodes(wanted, max_offset, keep_still):
    """Where each node should move to, given what its arms asked for.

    Every arm at a node shares one stated point, so they cannot each go their
    own way: the arms vote and the node takes the mean.  A node whose arms
    disagree wildly is a node the fit understood poorly, and the clamp to
    *max_offset* is what stops one confident arm dragging a junction across
    the map.
    """
    moves = {}
    for node_id, (now, asked) in wanted.items():
        if node_id in keep_still or not asked:
            continue
        mx = sum(p[0] for p in asked) / len(asked)
        my = sum(p[1] for p in asked) / len(asked)
        dx, dy = mx - now[0], my - now[1]
        reach = math.hypot(dx, dy)
        if reach <= SNAP_METRES:
            continue
        if reach > max_offset:
            dx, dy = dx / reach * max_offset, dy / reach * max_offset
            reach = max_offset
        moves[node_id] = ((now[0] + dx, now[1] + dy), reach, len(asked))
    return moves


def _aim_at_circle(fitted, circle_id, circle, link_list, node_list, network):
    """Rewrite a roundabout's arms so their mouths straddle the circle.

    Kept apart from :func:`fit_network` because it is the one step that acts
    on a junction rather than on a link, and because what it must not disturb
    -- the anchor -- is easiest to state on its own.
    """
    cx, cy, radius, rms = circle
    anchor = basemap.anchor_point(link_list, node_list,
                                  basemap.read_roundabout(network))
    if anchor is None:
        return fitted
    anchor = (anchor[0], anchor[1])

    arms, where = [], []
    for index, (link_id, up, down, rows) in enumerate(fitted):
        at_start = up == circle_id
        if not at_start and down != circle_id:
            continue
        sx, sy, ex, ey, width = rows[0] if at_start else rows[-1]
        length = math.hypot(ex - sx, ey - sy)
        if length <= 1e-9:
            continue
        dx, dy = (ex - sx) / length, (ey - sy) / length
        # The carriageway is to the left of the direction of travel whichever
        # end of the link the junction is at, so the normal is read off the
        # travel direction and not off "towards the node".
        line_point = (ex, ey) if at_start else (sx, sy)
        arms.append(((dx, dy), (-dy, dx), width / 2.0, line_point))
        where.append((index, at_start))
    if len(arms) < 2:
        return fitted

    ends = aim_arms_at_the_circle(arms, (cx, cy), anchor)
    moved = []
    for (index, at_start), end in zip(where, ends):
        link_id, up, down, rows = fitted[index]
        rows = list(rows)
        before = ((rows[0][0], rows[0][1]) if at_start
                  else (rows[-1][2], rows[-1][3]))
        if at_start:
            rows[0] = (end[0], end[1], rows[0][2], rows[0][3], rows[0][4])
        else:
            rows[-1] = (rows[-1][0], rows[-1][1], end[0], end[1], rows[-1][4])
        fitted[index] = (link_id, up, down, rows)
        moved.append((link_id, distance(before, end)))

    print(f"  node {circle_id}: OSM's roundabout loop fits r={radius:.1f} m "
          f"(rms {rms:.2f} m); arms aimed at its centre, "
          f"{distance(anchor, (cx, cy)):.1f} m from the anchor")
    print("    " + ", ".join(f"link {i} end moved {d:.0f} m"
                             for i, d in moved))
    return fitted


def fit_network(network: str, tolerance: float, max_detour: float,
                max_offset: float, snap_limit: float, min_segment: float,
                bearing_window: float, min_match: float,
                refresh: bool, dry_run: bool) -> bool:
    try:
        link_list, node_list = basemap.read_network(network)
    except OSError as exc:
        print(f"{network}: cannot read the network ({exc.strerror})")
        return False
    geo = basemap.georeference(network, link_list, node_list)
    if geo is None:
        print(f"{network}: no centre coordinate in geometry.txt, so it cannot "
              f"be placed on the Earth and cannot be fitted.")
        return False

    bounds = basemap.network_bounds(link_list, 150.0)
    west, north = geo.to_lonlat(bounds[0], bounds[1])
    east, south = geo.to_lonlat(bounds[2], bounds[3])
    ways = fetch_ways(network, south, west, north, east, refresh)
    pieces = road_segments(ways, geo)
    cell = max(snap_limit, 20.0)
    index = build_index(pieces, cell)
    cos_limit = math.cos(math.radians(bearing_window))
    print(f"{network}: {len(ways)} OSM ways, {len(pieces)} road pieces")
    if not pieces:
        print("  nothing to fit against")
        return False

    links = read_link_file(network)
    medians = read_medians(network)
    keep_straight = read_straight(network)
    if keep_straight:
        print(f"  link(s) {', '.join(map(str, sorted(keep_straight)))}: "
              f"geometry.txt asks for the chord, so they are not bent")
    if medians:
        print(f"  dual carriageway link(s) {', '.join(map(str, sorted(medians)))}"
              f": aiming at the middle of the pair, not the nearer of them")

    def reach_for(link_id, width):
        """How far this link may look, and whether it looks for a pair."""
        if link_id not in medians:
            return snap_limit, False
        return min(snap_limit, max(width / 2.0 + SPAN_MARGIN, 25.0)), True

    origins = {link_id: stated_line(rows) for link_id, _u, _d, rows in links}
    anchor_id = anchor_node_id(network, link_list, node_list)
    circle_id = basemap.read_roundabout(network)
    before = sum(polyline_length(line) for line in origins.values())

    # ---- first pass: ask every link where it would put its own ends ------
    #
    # Nothing from this pass is written.  A link with both ends free follows
    # the road all the way to its last sample, and that last sample is the
    # only evidence there is about where the node it runs to really sits.
    wanted = {}
    for link_id, up, down, rows in links:
        width = rows[0][4]
        limit, span = reach_for(link_id, width)
        stated = stated_line(rows)
        centre = offset_polyline(stated, width / 2.0)
        loose, matched, samples = snap_to_roads(
            centre, index, cell, limit, max_offset, cos_limit,
            pin_start=False, pin_end=False, span=span)
        if not samples or matched / samples < min_match:
            continue                      # it has no opinion worth counting
        if polyline_length(loose) > polyline_length(centre) * max_detour:
            continue
        edge = offset_polyline(loose, -width / 2.0)
        for node_id, now, proposed in ((up, stated[0], edge[0]),
                                       (down, stated[-1], edge[-1])):
            wanted.setdefault(node_id, (now, []))[1].append(proposed)

    keep_still = {anchor_id} if anchor_id is not None else set()
    moves = agree_on_nodes(wanted, max_offset, keep_still)
    if anchor_id is not None:
        print(f"  node {anchor_id} holds the georeference and stays put")
    for node_id, (_point, reach, arms) in sorted(moves.items()):
        print(f"  node {node_id} moves {reach:.0f} m "
              f"({arms} arm{'s' if arms != 1 else ''} agreed)")

    # ---- second pass: the real fit, onto the nodes' new positions --------
    #
    # A roundabout's arms are the exception to the pinning.  Their ends are
    # placed afterwards, by the circle rather than by the survey, so letting
    # the fit take that end wherever the road goes is what it wants: pinning
    # it first and moving it after would leave the taper's kink behind.
    #
    # Two passes and no more.  Repeating the whole fit was tried, because the
    # first version of the dual-carriageway pull needed a second turn to land
    # on the median: it does not converge.  Each repeat lets a link drift onto
    # whichever way is nearest by then, so the road grows -- +4.5% of network
    # length after one round, +8.9% after six -- while the share of the drawn
    # carriageway sitting on a road the map paints stops improving (50.8% to
    # 53.3%).  Settling the pull *inside* ``snap_to_roads`` instead gets the
    # same placement without the wandering, because the smoothing and the
    # simplification still happen exactly once.
    fitted, unchanged, after = [], 0, 0.0

    for link_id, up, down, rows in links:
        width = rows[0][4]
        limit, span = reach_for(link_id, width)
        stated = stated_line(rows)
        origin = origins[link_id]
        # The ends are pinned again here, but to where the arms agreed rather
        # than to where the survey happened to cut the network.
        if up in moves:
            stated[0] = moves[up][0]
        if down in moves:
            stated[-1] = moves[down][0]
        pin_start = up != circle_id
        pin_end = down != circle_id

        # Onto the centreline, which is what an OSM way describes.
        centre = offset_polyline(stated, width / 2.0)
        moved, matched, samples = snap_to_roads(
            centre, index, cell, limit, max_offset, cos_limit,
            pin_start=pin_start, pin_end=pin_end, span=span)

        share = matched / samples if samples else 0.0
        length = polyline_length(moved)
        straight = polyline_length(centre)
        reason = None
        if link_id in keep_straight:
            reason = "geometry.txt asks for the chord"
        elif share < min_match:
            reason = f"only {share * 100:.0f}% of it lies near a road"
        elif length > straight * max_detour:
            reason = (f"fitted line is "
                      f"{length / max(straight, 1e-9):.2f}x as long")

        if reason is not None:
            # Straight, but not where it was: its nodes may have moved, and a
            # link that ignored them would tear the junction open.
            edge = stated
            print(f"  link {link_id}: kept straight ({reason})")
        else:
            shaped = drop_short(simplify(moved, tolerance), min_segment)
            # Back to the kerb-edge convention, then pin the ends exactly
            # where they were so node.txt and the junction geometry still
            # agree.
            edge = offset_polyline(shaped, -width / 2.0)
            if pin_start:
                edge[0] = stated[0]
            if pin_end:
                edge[-1] = stated[-1]
            edge = drop_short(edge, min_segment)
            if len(edge) < 2:
                edge = stated
                reason = "the fit collapsed to a point"

        rows_out = [(edge[i][0], edge[i][1], edge[i + 1][0], edge[i + 1][1],
                     width) for i in range(len(edge) - 1)]
        fitted.append((link_id, up, down, rows_out))
        after += polyline_length(edge)
        if reason is not None:
            unchanged += 1
        else:
            bend = max(point_to_segment(p, origin[0], origin[-1]) for p in edge)
            print(f"  link {link_id}: {len(origin) - 1} -> {len(rows_out)} "
                  f"segments, {polyline_length(origin):.0f} -> "
                  f"{polyline_length(edge):.0f} m, bends up to {bend:.0f} m, "
                  f"{share * 100:.0f}% on road")
    links = fitted

    # ---- the roundabout: aim the arms' mouths at the circle --------------
    if circle_id is not None:
        circle = osm_circle(ways, geo)
        if circle is None:
            print(f"  node {circle_id} is a roundabout but OSM draws no loop "
                  f"there, so its arms keep the ends the survey gave them")
        else:
            fitted = _aim_at_circle(fitted, circle_id, circle,
                                    link_list, node_list, network)

    change = (after / before - 1.0) * 100.0 if before else 0.0
    print(f"  total road length {before:.0f} -> {after:.0f} m "
          f"({change:+.1f}%), {len(links) - unchanged}/{len(links)} links fitted")
    if unchanged:
        print(f"  {unchanged} link(s) left straight")
    if dry_run:
        print("  dry run, nothing written")
        return True

    backup = basemap.network_path(network, BACKUP_NAME)
    if not os.path.exists(backup):
        shutil.copyfile(basemap.network_path(network, "link.txt"), backup)
        print(f"  original geometry saved to {backup}")
    node_backup = basemap.network_path(network, NODE_BACKUP_NAME)
    if not os.path.exists(node_backup):
        shutil.copyfile(basemap.network_path(network, "node.txt"), node_backup)
    write_link_file(network, fitted)
    print(f"  wrote {basemap.network_path(network, 'link.txt')}")

    # A boundary node repeats its arm's endpoint in node.txt, and the
    # simulator reads it to work out which way round a link runs, so the two
    # have to agree.  A junction node states (0, 0) and has its position
    # derived from the arms instead; the test below is exactly that
    # distinction, asked of the data rather than assumed.
    stored = read_node_file(network)
    rewritten = []
    changed = 0
    for node_id, x, y, arms in stored:
        move = moves.get(node_id)
        if move is not None and node_id in wanted:
            now = wanted[node_id][0]
            if abs(x - now[0]) < 1e-6 and abs(y - now[1]) < 1e-6:
                x, y = move[0]
                changed += 1
        rewritten.append((node_id, x, y, arms))
    if changed:
        write_node_file(network, rewritten)
        print(f"  wrote {basemap.network_path(network, 'node.txt')} "
              f"({changed} node coordinate(s) followed their arms)")
    return True


def restore(network: str) -> bool:
    backup = basemap.network_path(network, BACKUP_NAME)
    if not os.path.exists(backup):
        print(f"{network}: no {BACKUP_NAME} to restore from")
        return False
    shutil.copyfile(backup, basemap.network_path(network, "link.txt"))
    node_backup = basemap.network_path(network, NODE_BACKUP_NAME)
    if os.path.exists(node_backup):
        shutil.copyfile(node_backup, basemap.network_path(network, "node.txt"))
    print(f"{network}: straight geometry restored")
    return True


def placeable():
    found = []
    for entry in sorted(os.listdir("input")):
        if os.path.isfile(os.path.join("input", entry, "node.txt")):
            found.append((entry, basemap.read_centre(entry)))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bend a network's links onto the real roads.")
    parser.add_argument("--network")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--restore", action="store_true",
                        help="put the straight geometry back")
    parser.add_argument("--tolerance", type=float, default=2.0,
                        help="metres of deviation allowed when simplifying the "
                             "fitted line; larger means fewer segments "
                             "(default: 2)")
    parser.add_argument("--max-detour", type=float, default=1.35,
                        help="reject a route longer than this many times the "
                             "straight line (default: 1.35)")
    parser.add_argument("--max-offset", type=float, default=30.0,
                        help="reject a route straying more than this many "
                             "metres from the original line (default: 30)")
    parser.add_argument("--snap", type=float, default=45.0,
                        help="metres a link end may be from a road and still "
                             "match it (default: 45)")
    parser.add_argument("--min-segment", type=float, default=4.0,
                        help="shortest segment to write, in metres "
                             "(default: 4)")
    parser.add_argument("--bearing", type=float, default=35.0,
                        help="degrees a road may differ from the link's own "
                             "direction and still be matched; this is what "
                             "keeps an arm off the street it crosses "
                             "(default: 35)")
    parser.add_argument("--min-match", type=float, default=0.55,
                        help="fraction of a link that must lie near a road "
                             "before it is reshaped at all (default: 0.55)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download the OSM extract instead of using the "
                             "cached one")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.list or not (args.network or args.all):
        print("network            centre in geometry.txt   fitted?")
        for name, centre in placeable():
            where = (f"{centre[0]:.4f},{centre[1]:.4f}" if centre
                     else "-- none, cannot fit")
            done = "yes" if os.path.exists(
                basemap.network_path(name, BACKUP_NAME)) else "no"
            print(f"  {name:<18} {where:<24} {done}")
        if not (args.network or args.all):
            print("\nPass --network <name> or --all.")
        return 0

    targets = ([name for name, centre in placeable() if centre]
               if args.all else [args.network])
    failures = 0
    for name in targets:
        if args.restore:
            ok = restore(name)
        else:
            ok = fit_network(name, args.tolerance, args.max_detour,
                             args.max_offset, args.snap, args.min_segment,
                             args.bearing, args.min_match,
                             args.refresh, args.dry_run)
        if not ok:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
