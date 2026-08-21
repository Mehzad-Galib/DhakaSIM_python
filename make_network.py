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


def merge_dual_carriageways(edges, max_separation: float, samples: int = 12):
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
            left = _resample(a["points"], samples)
            opposite = [_closest_on_polyline(p, b["points"]) for p in left]
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
                           [q for q, _ in opposite]))

    scored.sort(key=lambda s: s[0])
    used, merged = set(), []
    for gap, i, j, left, right in scored:
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        a, b = candidates[i], candidates[j]
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
    return others + unmatched + merged, len(merged)


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
    """
    fused = [e for e in edges if e.get("median", 0) > 0]
    if not fused:
        return edges

    for edge in edges:
        if edge.get("median", 0) > 0:
            continue
        points = list(edge["points"])
        for index in (0, -1):
            end = points[index]
            best = None
            for road in fused:
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
            merged = {"props": first["props"],
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

    with open(os.path.join(out_dir, "link.txt"), "w", encoding="utf-8") as f:
        f.write(f"{len(links)}\n")
        for link in links:
            points = link["points"]
            f.write(f"{link['id']} {link['up']} {link['down']} {len(points) - 1}\n")
            for segment, (a, b) in enumerate(zip(points, points[1:])):
                f.write(f"{segment} {a[0]:.0f} {a[1]:.0f} {b[0]:.0f} {b[1]:.0f} "
                        f"{link['width']:.1f}\n")

    with open(os.path.join(out_dir, "node.txt"), "w", encoding="utf-8") as f:
        f.write(f"{len(node_ids)}\n")
        for key, node_id in sorted(node_ids.items(), key=lambda kv: kv[1]):
            incident = node_links[key]
            # A junction is written at 0 0: the simulator treats that as "not a
            # boundary point" and excludes it from the view's bounding box,
            # taking the junction's real position from where its links meet.
            # Only the degree-1 nodes, where vehicles enter and leave, carry
            # coordinates.
            if len(incident) > 1:
                x = y = 0
            else:
                x, y = node_xy[key]
            f.write(f"{node_id} {x:.0f} {y:.0f} "
                    + " ".join(str(i) for i in incident) + "\n")

    boundary = [k for k in node_ids if len(node_links[k]) == 1]
    divided = [link for link in links if link["median"] > 0]
    with open(os.path.join(out_dir, "geometry.txt"), "w", encoding="utf-8") as f:
        f.write(f"# Generated by make_network.py from {os.path.basename(source)}.\n")
        f.write(f"# Centre {centre[0]:.6f},{centre[1]:.6f}.\n")
        if "overpass" in generator.lower() or "osm" in generator.lower():
            f.write("# Data (c) OpenStreetMap contributors, ODbL.\n")
        elif generator:
            f.write(f"# Source: {generator}.\n")
        if note:
            for line in note.split("\n"):
                f.write(f"# {line}\n")
        f.write("#\n# GeometryMode is Off by default; turn it on to apply these.\n")
        if divided:
            f.write("# Medians, measured as the gap between the two one-way\n"
                    "# carriageways OSM maps a divided road as.\n")
            for link in divided:
                name = link["props"].get("name", "")
                comment = f"   # {name}" if name else ""
                f.write(f"median {link['id']} {link['median']:.1f}{comment}\n")
        else:
            f.write("# No divided roads detected in the extract.\n")

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
        edges, fused = merge_dual_carriageways(edges, args.max_separation)
        edges = drop_degenerate(edges)
        if fused:
            edges = reconnect_to_fused(edges, args.max_separation)
            edges = resplit(edges)
            # snapping both sides of a crossing onto one point leaves the piece
            # that spanned the old carriageways with no length
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
