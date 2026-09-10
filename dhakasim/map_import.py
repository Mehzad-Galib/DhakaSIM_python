"""Import a real road network from OpenStreetMap -- the logic, no GUI.

The start screen's "Import map" dialog is a thin shell over this module,
which is itself a thin shell over the canonical command-line chain::

    (Overpass extract)                       -> <folder>/osm_extract.geojson
    make_network.py <extract> --out <folder> -> node/link/geometry/link_names
    run_sim.py --network <name>              -> path.txt, demand.txt
    fetch_basemap.py --network <name>        -> basemap.png/basemap.txt
    fit_roads.py --network <name>            -> links bent onto the roads

The scripts stay the canonical, testable path and are run as subprocesses
rather than imported: the package must not import a top-level script (the
same rule that makes ``basemap.Projector`` a copy), and a crash in one step
must not take the GUI down with it.

Everything here is standard library, like the rest of the project.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess

from . import network_files
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#: Identifies us to the OSM services, as their usage policies require.
USER_AGENT = "DhakaSim map import (research use)"

#: Overpass endpoints, tried in order.  The main instance 504s under load
#: often enough that the fallbacks are not optional -- on the day the demo
#: network was built, only the mail.ru mirror answered.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: What marks an ``input/`` folder as having come from the import dialog.
#: hash_run.py keys off the same file to keep imports out of the parity
#: baseline, so the two must never disagree about the marker's name.
IMPORT_MARKER = "osm_extract.geojson"

#: Import metadata -- currently just ``kind single|multi``, the user's own
#: answer to whether this is one junction or a network of them, which
#: decides which start-screen group the tile joins.
KIND_FILE = "imported.txt"

#: Road-class presets, phrased for the dialog.  "Main roads" matches what the
#: BUET-DU-DMC demo was built from; residential streets are offered but not
#: default, because in a dense city they multiply the link count tenfold.
CLASS_PRESETS = (
    ("Main roads",
     ("trunk", "trunk_link", "primary", "primary_link",
      "secondary", "secondary_link")),
    ("+ local streets",
     ("trunk", "trunk_link", "primary", "primary_link",
      "secondary", "secondary_link", "tertiary", "tertiary_link")),
    ("Everything drivable",
     ("trunk", "trunk_link", "primary", "primary_link",
      "secondary", "secondary_link", "tertiary", "tertiary_link",
      "residential", "unclassified", "living_street")),
)


# --------------------------------------------------------------------------
# location parsing
# --------------------------------------------------------------------------

def parse_location(text: str):
    """A pasted location -> ``(lat, lon)``, or ``None`` for a place name.

    Accepts what people actually have in their clipboard:

    * a plain ``lat, lon`` pair;
    * a Google Maps URL -- a dropped pin carries the point as ``!3d<lat>``
      ``!4d<lon>``, which is preferred over the ``@lat,lon,zoom`` that only
      states where the *viewport* was, which itself beats a bare ``?q=``;
    * an OpenStreetMap URL -- ``?mlat=&mlon=`` is the marker, ``#map=z/lat/lon``
      the viewport.

    Anything else returns ``None`` and should be geocoded as a place name.
    """
    text = (text or "").strip()
    if not text:
        return None

    def _valid(lat, lon):
        return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0

    # A URL: try the marker forms first, then the viewport forms.
    if "://" in text or text.startswith("www."):
        pin = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", text)
        if pin:
            lat, lon = float(pin.group(1)), float(pin.group(2))
            if _valid(lat, lon):
                return lat, lon
        marker = re.search(
            r"[?&]mlat=(-?\d+(?:\.\d+)?)&mlon=(-?\d+(?:\.\d+)?)", text)
        if marker:
            lat, lon = float(marker.group(1)), float(marker.group(2))
            if _valid(lat, lon):
                return lat, lon
        view = re.search(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", text)
        if view:
            lat, lon = float(view.group(1)), float(view.group(2))
            if _valid(lat, lon):
                return lat, lon
        osm_view = re.search(
            r"#map=\d+/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)", text)
        if osm_view:
            lat, lon = float(osm_view.group(1)), float(osm_view.group(2))
            if _valid(lat, lon):
                return lat, lon
        q = re.search(r"[?&]q=(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", text)
        if q:
            lat, lon = float(q.group(1)), float(q.group(2))
            if _valid(lat, lon):
                return lat, lon
        return None

    # A bare coordinate pair.
    pair = re.fullmatch(
        r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)", text)
    if pair:
        lat, lon = float(pair.group(1)), float(pair.group(2))
        if _valid(lat, lon):
            return lat, lon
    return None


def geocode(name: str):
    """Place name -> ``(lat, lon, display_name)`` via Nominatim, or ``None``."""
    query = urllib.parse.urlencode(
        {"q": name, "format": "json", "limit": 1})
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.load(response)
    if not results:
        return None
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"]), str(hit["display_name"])


def slugify(name: str) -> str:
    """A display name -> an ``input/`` folder name.

    ASCII lowercase words joined by underscores, because the folder name
    travels through file paths, the console and ``--network`` flags.
    """
    words = re.findall(r"[A-Za-z0-9]+", name)
    return "_".join(w.lower() for w in words) or "imported"


def is_imported(network: str) -> bool:
    """Whether an ``input/`` folder came from the import dialog."""
    return os.path.isfile(os.path.join("input", network, IMPORT_MARKER))


def imported_kind(network: str) -> str:
    """``"single"`` or ``"multi"`` -- what the user said at import time.

    Imports from before the question existed have no ``imported.txt`` and
    read as ``multi``, which is what they were grouped as then.
    """
    try:
        with open(os.path.join("input", network, KIND_FILE), "r",
                  encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "kind":
                    return "single" if parts[1] == "single" else "multi"
    except OSError:
        pass
    return "multi"


def remove(network: str) -> None:
    """Delete an imported network's folder, and only an imported one.

    The shipped networks are survey data and must never be one misclick
    from gone, so anything without the import marker is refused.
    """
    if not is_imported(network):
        raise RuntimeError(
            f"input/{network} was not made by the import dialog; refusing "
            "to delete it")
    shutil.rmtree(os.path.join("input", network))


def read_network_shape(folder: str):
    """A staged network as drawable shapes, for the junction picker.

    Returns ``(links, nodes, to_latlon)``:

    * ``links`` -- ``{link_id: {"points": [(x, y), ...], "up": id,
      "down": id, "width": m}}`` in network metres;
    * ``nodes`` -- ``{node_id: {"x": x, "y": y, "links": [ids],
      "junction": bool}}``.  A junction is *stored* at (0, 0) by the file
      convention, so its drawable position is the mean of its arms'
      endpoints -- the same answer ``Processor.node_point`` gives;
    * ``to_latlon(x, y)`` -- the inverse of ``make_network``'s projection,
      built from the recorded ``# Centre lat,lon at x,y`` anchor, so the
      shapes can be laid over web-map tiles.
    """
    links = {}
    for row in network_files.read_link_rows(os.path.join(folder, "link.txt")):
        points = [(row.segments[0].sx, row.segments[0].sy)]
        points += [(s.ex, s.ey) for s in row.segments]
        links[row.link_id] = {"points": points, "up": row.up,
                              "down": row.down,
                              "width": row.segments[0].width}

    def arm_end(link, node_id):
        pts = link["points"]
        return pts[0] if link["up"] == node_id else pts[-1]

    nodes = {}
    for row in network_files.read_node_rows(os.path.join(folder,
                                                         "node.txt")):
        incident = [i for i in row.link_ids if i in links]
        x, y = row.x, row.y
        ends = [arm_end(links[i], row.node_id) for i in incident]
        junction = len(incident) >= 2
        if junction and ends:
            x = sum(e[0] for e in ends) / len(ends)
            y = sum(e[1] for e in ends) / len(ends)
        nodes[row.node_id] = {"x": x, "y": y, "links": incident,
                              "junction": len(incident) >= 3}

    facts = network_files.read_geometry(os.path.join(folder,
                                                     "geometry.txt"))
    if facts.centre is None or facts.anchor is None:
        raise RuntimeError(f"{folder} records no centre anchor")
    lat0, lon0 = facts.centre
    x0, y0 = facts.anchor
    phi = math.radians(lat0)
    m_per_deg_lat = (111132.92 - 559.82 * math.cos(2 * phi)
                     + 1.175 * math.cos(4 * phi)
                     - 0.0023 * math.cos(6 * phi))
    m_per_deg_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
                     + 0.118 * math.cos(5 * phi))

    def to_latlon(x, y):
        # y increases southwards in the network plane, like a tile row.
        return (lat0 - (y - y0) / m_per_deg_lat,
                lon0 + (x - x0) / m_per_deg_lon)

    return links, nodes, to_latlon


#: Crossing-nodes this close to the chosen one are the same junction.  OSM
#: maps a dual carriageway as two one-way ways, so one real crossing is a
#: square of four OSM junctions a carriageway-gap apart; make_network's
#: fusing catches most of that, but what it leaves is a stub of a few
#: metres between two "junctions" (measured at Palashi: 6 m and 18 m), and
#: a leg that stops at the stub's far end is a leg a few metres long.
JUNCTION_CLUSTER_M = 35.0
#: Walking outward, a leg carries on through a side-street junction only
#: along the continuation that turns less than this.  A sharper turn is a
#: different road, and the leg ends there.
LEG_MAX_TURN_DEG = 60.0
#: Vertices closer than this to the junction's convergence point are
#: dropped when the legs are written: the arms then leave the point clean
#: instead of doubling back through the crossing they came from.
MOUTH_CLEAR_M = 12.0


def _bearing(a, b) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _turn_degrees(incoming: float, outgoing: float) -> float:
    turn = abs(outgoing - incoming) % (2.0 * math.pi)
    return math.degrees(min(turn, 2.0 * math.pi - turn))


def network_legs(links, nodes, junctions, cluster_m: float = JUNCTION_CLUSTER_M,
                 max_turn_deg: float = LEG_MAX_TURN_DEG):
    """The legs of the chosen junctions, each the whole road walked outward.

    ``links`` and ``nodes`` are :func:`read_network_shape`'s; ``junctions``
    the chosen node ids, in the order chosen.  The staged network is OSM's
    graph, cut at every crossing -- so the links touching a chosen node
    are only its first fragments, a few metres long where a side street
    or the other half of a dual carriageway joins.  A leg the way a
    surveyed network means it is the approach road all the way out to the
    edge of the extract, or to the next chosen junction, and that is what
    this walks:

    * a *junction* is the chosen node together with every crossing-node
      within ``cluster_m`` of it that a link joins it to -- one real
      crossing, however many OSM nodes it is drawn as -- plus any
      boundary node that close, a dangling arm end being a reconnection
      the build missed rather than a road that ends at the crossing.  A
      chosen node that already lies inside an earlier junction's cluster
      is that junction, and gets no entry of its own;
    * every link leaving a cluster starts a leg, which continues through
      each further node along the outgoing link that turns least,
      provided the turn is under ``max_turn_deg``, and stops at a boundary
      node, a sharper turn, a node it has already passed, a link another
      leg has claimed -- or on reaching another chosen junction's cluster,
      in which case the leg joins the two junctions and the walk back
      from the other side finds its links claimed.  A road that loops
      back to its own crossing is not a leg.

    Returns ``(legs, centres)``: ``centres`` maps each junction (in the
    order chosen) to the mean of its cluster's node positions, and each
    leg is ``{"junction": chosen id, "links": [ids, junction end first],
    "forward": [bool per link -- True where the link's stated up->down
    order runs outward], "points": [(x, y), ...] from the junction
    outward, "end": node id, "end_junction": chosen id or None}``.  Legs
    come grouped by junction, each group clockwise from north by the
    bearing it leaves on, so they read round the junction like a clock
    face.
    """
    def position(nid):
        return nodes[nid]["x"], nodes[nid]["y"]

    def other_end(lid, nid):
        link = links[lid]
        return link["down"] if link["up"] == nid else link["up"]

    def outward(lid, from_nid):
        """The link's points leaving ``from_nid``, and whether that is its
        own stated direction."""
        link = links[lid]
        forward = link["up"] == from_nid
        return (link["points"] if forward else link["points"][::-1]), forward

    # Boundary nodes join a cluster too: an arm whose inner end stands
    # alone a few metres from the crossing is one make_network failed to
    # reconnect after fusing its carriageways (Shahbag's west arm ended
    # 10 m short of the crossing), and the arm is still a leg.  A dead
    # end that close would have been trimmed as a stub anyway.
    owners = {}                       # node id -> the junction it belongs to
    centres = {}
    order = {}
    for junction in junctions:
        if junction in owners:
            continue
        jx, jy = position(junction)
        cluster = {junction}
        frontier = [junction]
        while frontier:
            nid = frontier.pop()
            for lid in nodes[nid]["links"]:
                far = other_end(lid, nid)
                if far in cluster or far in owners:
                    continue
                fx, fy = position(far)
                if math.hypot(fx - jx, fy - jy) <= cluster_m:
                    cluster.add(far)
                    frontier.append(far)
        for nid, node in nodes.items():
            if (nid not in cluster and nid not in owners
                    and len(node["links"]) == 1):
                fx, fy = position(nid)
                if math.hypot(fx - jx, fy - jy) <= cluster_m:
                    cluster.add(nid)
        for nid in cluster:
            owners[nid] = junction
        centres[junction] = (
            sum(position(n)[0] for n in cluster) / len(cluster),
            sum(position(n)[1] for n in cluster) / len(cluster))
        order[junction] = len(order)

    claimed = set()
    exits = []
    for nid, owner in owners.items():
        for lid in nodes[nid]["links"]:
            if owners.get(other_end(lid, nid)) == owner:
                claimed.add(lid)          # internal to the crossing
            else:
                exits.append((lid, nid, owner))

    def leaving_bearing(item):
        # Clockwise from north (y is down, so north is -pi/2), like a
        # clock face: the legs list as N, E, S, W.
        lid, nid, owner = item
        pts, _forward = outward(lid, nid)
        bearing = _bearing(centres[owner], pts[1] if len(pts) > 1 else pts[0])
        return (order[owner], (bearing + math.pi / 2.0) % (2.0 * math.pi))

    legs = []
    for lid, nid, owner in sorted(exits, key=leaving_bearing):
        if lid in claimed:
            continue
        pts, forward = outward(lid, nid)
        chain, forwards, points = [lid], [forward], list(pts)
        claimed.add(lid)
        visited = {n for n, o in owners.items() if o == owner}
        node = other_end(lid, nid)
        while (node not in owners and node not in visited
               and len(nodes[node]["links"]) >= 2):
            visited.add(node)
            incoming = _bearing(points[-2], points[-1])
            best, best_turn = None, max_turn_deg
            for cand in nodes[node]["links"]:
                if cand in claimed or other_end(cand, node) in visited:
                    continue
                cpts, _f = outward(cand, node)
                turn = _turn_degrees(incoming, _bearing(cpts[0], cpts[1]))
                if turn < best_turn:
                    best, best_turn = cand, turn
            if best is None:
                break
            cpts, forward = outward(best, node)
            chain.append(best)
            forwards.append(forward)
            points.extend(cpts[1:])
            claimed.add(best)
            node = other_end(best, node)
        end_junction = owners.get(node)
        if end_junction == owner:
            continue                  # looped back to its own crossing
        legs.append({"junction": owner, "links": chain, "forward": forwards,
                     "points": points, "end": node,
                     "end_junction": end_junction})
    return legs, centres


def junction_legs(links, nodes, junction, cluster_m: float = JUNCTION_CLUSTER_M,
                  max_turn_deg: float = LEG_MAX_TURN_DEG):
    """One junction's legs -- :func:`network_legs` for a single choice.

    Returns ``(legs, centre)`` with ``centre`` that junction's own.
    """
    legs, centres = network_legs(links, nodes, [junction], cluster_m,
                                 max_turn_deg)
    return legs, centres[junction]


def check_legs(legs, centres):
    """Why this choice of junctions and legs cannot be written, or ``None``.

    Every junction needs two legs (one makes it a dead end, not a
    junction), and the junctions must be joined by kept legs into one
    network -- ``run_sim`` routes between boundary nodes, and a piece
    nothing connects to is a route it cannot build.  Called by the dialog
    before Finish, so the answer reaches the user with the pick still on
    screen, and again by :func:`prune_to_junctions` as its own defence.
    """
    counts = {jid: 0 for jid in centres}
    parent = {jid: jid for jid in centres}

    def root(jid):
        while parent[jid] != jid:
            jid = parent[jid]
        return jid

    for leg in legs:
        counts[leg["junction"]] += 1
        if leg["end_junction"] is not None:
            counts[leg["end_junction"]] += 1
            parent[root(leg["junction"])] = root(leg["end_junction"])
    if len(legs) < 2:
        return "keep at least two legs, or there is nothing to route between"
    short = [jid for jid, n in counts.items() if n < 2]
    if short:
        return ("every junction needs at least two legs -- one of the "
                "chosen junctions has fewer")
    if len({root(jid) for jid in centres}) > 1:
        return ("the chosen junctions are not joined by a road -- choose "
                "junctions along the same streets, or import them "
                "separately")
    return None


def prune_to_junction(folder: str, legs, centre, roundabout=None) -> None:
    """:func:`prune_to_junctions` for one junction and its legs."""
    junction = legs[0]["junction"] if legs else None
    prune_to_junctions(folder, legs, {junction: centre},
                       {junction: roundabout} if roundabout else None)


def prune_to_junctions(folder: str, legs, centres, roundabouts=None) -> None:
    """Cut a staged network down to the chosen junctions and their legs.

    ``legs`` are :func:`network_legs` entries and ``centres`` its
    convergence points, in the order the junctions were chosen.  Rewrites
    ``link.txt``, ``node.txt``, ``geometry.txt`` and ``link_names.txt``
    in place: each leg becomes **one link**, from its junction (nodes
    ``0..J-1`` in that order, stored at (0, 0) by the format's
    convention) to either a boundary node stored at the leg's far end or
    the other junction it reached, ids dense in both spaces (the
    simulator indexes arrays by id).  Every leg starts -- and a joining
    leg ends -- at a junction's centre: the survey networks state a
    junction as arms converging on a point, and
    ``Processor._open_the_circle`` and the drawn junction patch both rely
    on it; vertices inside ``MOUTH_CLEAR_M`` of a centre are dropped.

    A leg takes its width length-weighted over the links it walked and its
    median from the widest of them (``make_network``'s own chain rules);
    it is one-way only if every link along it is, all running the same
    way, in which case the link is written in the direction of travel.
    ``geometry.txt`` keeps its leading comment block -- the recorded centre
    anchor, so the imagery georeference does not move -- and the
    directives are regenerated for the new links.

    ``roundabouts`` maps a junction to ``(island_m, ring_m)`` to declare
    it one: converging arms plus the directive is exactly the form the
    surveyed kakrail uses, so the ring gets built and the junction goes
    unsignalised, as a roundabout should.

    Routes and demand are NOT touched here -- the caller regenerates them
    (``run_sim.py``), which is why this must run before that step.
    """
    legs = list(legs)
    problem = check_legs(legs, centres)
    if problem:
        raise RuntimeError(problem)
    junction_ids = {jid: i for i, jid in enumerate(centres)}
    rows = {row.link_id: row for row in network_files.read_link_rows(
        os.path.join(folder, "link.txt"))}
    facts = network_files.read_geometry(os.path.join(folder, "geometry.txt"))
    names = {}
    names_path = os.path.join(folder, "link_names.txt")
    if os.path.isfile(names_path):
        with open(names_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                tokens = raw.split(None, 1)
                if len(tokens) == 2 and tokens[0].isdigit():
                    names[int(tokens[0])] = tokens[1].strip()

    def length(lid):
        return sum(math.hypot(s.ex - s.sx, s.ey - s.sy)
                   for s in rows[lid].segments)

    def clear_of(point, centre):
        return math.hypot(point[0] - centre[0],
                          point[1] - centre[1]) >= MOUTH_CLEAR_M

    links_out, boundary_nodes = [], []
    incident = {i: [] for i in junction_ids.values()}
    medians, oneways, leg_names = {}, set(), {}
    for new_id, leg in enumerate(legs):
        chain = list(leg["links"])
        lengths = [max(length(lid), 1e-9) for lid in chain]
        width = (sum(rows[lid].segments[0].width * n
                     for lid, n in zip(chain, lengths)) / sum(lengths))
        median = max((facts.medians.get(lid, 0.0) for lid in chain),
                     default=0.0)
        forwards = list(leg["forward"])
        all_oneway = all(lid in facts.oneways for lid in chain)
        outward_travel = all_oneway and all(forwards)
        inward_travel = all_oneway and not any(forwards)

        start = centres[leg["junction"]]
        up = junction_ids[leg["junction"]]
        if leg["end_junction"] is not None:
            finish = centres[leg["end_junction"]]
            down = junction_ids[leg["end_junction"]]
            points = [start] + [p for p in leg["points"][1:-1]
                                if clear_of(p, start) and clear_of(p, finish)
                                ] + [finish]
        else:
            finish = leg["points"][-1]
            down = len(junction_ids) + len(boundary_nodes)
            boundary_nodes.append((down, finish[0], finish[1], [new_id]))
            points = [start] + [p for p in leg["points"][1:]
                                if clear_of(p, start)]
            if len(points) < 2:
                points = [start, finish]
        if inward_travel:
            points, up, down = points[::-1], down, up
        links_out.append((new_id, up, down,
                          [(a[0], a[1], b[0], b[1], width)
                           for a, b in zip(points, points[1:])]))
        for node_id in (up, down):
            if node_id in incident:
                incident[node_id].append(new_id)
        if median > 0:
            medians[new_id] = median
        if outward_travel or inward_travel:
            oneways.add(new_id)
        named = [lid for lid in chain if names.get(lid)]
        if named:
            leg_names[new_id] = names[max(named, key=length)]
    nodes_out = ([(i, 0.0, 0.0, sorted(incident[i]))
                  for i in sorted(incident)] + boundary_nodes)

    network_files.write_link_rows(os.path.join(folder, "link.txt"),
                                  links_out)
    network_files.write_node_rows(os.path.join(folder, "node.txt"),
                                  nodes_out)

    # geometry.txt: the provenance block on top (the centre anchor lives
    # there) is kept verbatim; every directive is regenerated.
    geometry_path = os.path.join(folder, "geometry.txt")
    kept_lines = []
    with open(geometry_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#"):
                break
            if stripped.startswith(("# Medians", "# No divided",
                                    "# One-way")):
                break                 # make_network's section headers
            kept_lines.append(raw)
    kept_lines.append(f"# Cut down to {len(centres)} junction(s) and "
                      f"{len(legs)} legs by the import dialog's junction "
                      "picker.\n")
    if medians:
        kept_lines.append("# Medians, measured as the gap between the two "
                          "one-way\n# carriageways OSM maps a divided road "
                          "as.\n")
        for lid, gap in medians.items():
            name = leg_names.get(lid, "")
            kept_lines.append(f"median {lid} {gap:.1f}"
                              + (f"   # {name}" if name else "") + "\n")
    if oneways:
        kept_lines.append(
            "# One-way carriageways.  Travel runs up -> down (the\n"
            "# order the link's segments are written in); the\n"
            "# simulator opens the full width to that direction and\n"
            "# run_sim.py routes nothing against it.\n")
        for lid in sorted(oneways):
            name = leg_names.get(lid, "")
            kept_lines.append(f"oneway {lid}"
                              + (f"   # {name}" if name else "") + "\n")
    for jid, ring in (roundabouts or {}).items():
        if ring is None or jid not in junction_ids:
            continue
        island_m, ring_m = ring
        kept_lines.append(
            f"roundabout {junction_ids[jid]} {island_m:g} {ring_m:g}"
            "   # declared a roundabout in the import dialog; edit these "
            "measured island/ring metres if known\n")
    with open(geometry_path, "w", encoding="utf-8") as handle:
        handle.writelines(kept_lines)

    if os.path.isfile(names_path):
        with open(names_path, "w", encoding="utf-8") as handle:
            for lid, name in sorted(leg_names.items()):
                handle.write(f"{lid} {name}\n")


def bbox_around(lat: float, lon: float, radius_m: float):
    """``(south, west, north, east)`` of the square holding the radius."""
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


# --------------------------------------------------------------------------
# the preview map
# --------------------------------------------------------------------------

TILE_PIXELS = 256
#: Tried in order per tile; the main host throttles heavy use (measured:
#: connection timeouts after a day of previews), and the German instance
#: serves the same scheme from separate infrastructure.
TILE_URLS = ("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
             "https://tile.openstreetmap.de/{z}/{x}/{y}.png")
#: Beside fetch_basemap.py's cache, in its own drawer -- the two derive
#: their cache keys differently and sharing a layout would couple a package
#: module to a top-level script's internals.
PREVIEW_CACHE = os.path.join("input", ".tilecache", "preview_osm")


def deg_to_tile(lat: float, lon: float, zoom: int):
    """Fractional slippy-map tile coordinates of a point (standard maths)."""
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    phi = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(phi)) / math.pi) / 2.0 * n
    return x, y


def tile_to_latlon(px: float, py: float, zoom: int):
    """Global web-mercator pixels at *zoom* -> (lat, lon).

    The inverse of ``deg_to_tile(...) * TILE_PIXELS`` -- what turns "the
    point now under the preview's centre" back into a place on Earth when
    the map has been dragged.
    """
    n = 2.0 ** zoom
    xt, yt = px / TILE_PIXELS, py / TILE_PIXELS
    lon = xt / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * yt / n))))
    return lat, lon


def metres_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2.0 ** zoom)


def preview_zoom(lat: float, radius_m: float, span_px: int) -> int:
    """The deepest zoom at which the radius circle still fits the preview.

    The circle is held to about two thirds of the shorter canvas side, so
    there is always context around it.
    """
    wanted = 2.0 * radius_m / (0.66 * span_px)
    zoom = 17
    while zoom > 3 and metres_per_pixel(lat, zoom) < wanted:
        zoom -= 1
    return zoom


def _fetch_tile(zoom: int, x: int, y: int) -> str:
    """One OSM tile as a cached PNG on disk; returns its path."""
    path = os.path.join(PREVIEW_CACHE, str(zoom), str(x), f"{y}.png")
    if os.path.exists(path):
        return path
    error = None
    for template in TILE_URLS:
        request = urllib.request.Request(
            template.format(z=zoom, x=x, y=y),
            headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read()
            break
        except (urllib.error.URLError, OSError) as exc:
            error = exc
    else:
        raise error
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def preview_view(lat: float, lon: float, radius_m: float,
                 width: int, height: int):
    """The preview's projection alone: ``(mpp, (zoom, gx0, gy0))``.

    Pure arithmetic -- what the pan and the junction picker need even
    when no tile can be fetched, so a tile-server outage degrades the
    preview to a dark canvas instead of taking the import down.
    """
    zoom = preview_zoom(lat, radius_m, min(width, height))
    xt, yt = deg_to_tile(lat, lon, zoom)
    return metres_per_pixel(lat, zoom), (
        zoom, xt * TILE_PIXELS - width / 2.0,
        yt * TILE_PIXELS - height / 2.0)


def preview_tiles(lat: float, lon: float, radius_m: float,
                  width: int, height: int):
    """Everything a canvas needs to show the spot: ``(mpp, placed, view)``.

    ``placed`` is ``[(canvas_x, canvas_y, png_path), ...]`` with the given
    point at the canvas centre; ``mpp`` converts the radius to pixels; and
    ``view`` is ``(zoom, gx0, gy0)`` -- the global-pixel origin of the
    canvas at that zoom, which is what lets the junction picker project a
    lat/lon onto the same picture
    (``deg_to_tile(...)*256 - (gx0, gy0)``).  Tiles come from the on-disk
    cache when they can, so moving the radius around a place already
    looked at costs no traffic.
    """
    mpp, (zoom, gx0, gy0) = preview_view(lat, lon, radius_m, width,
                                         height)
    n = 2 ** zoom
    placed = []
    for tx in range(math.floor(gx0 / TILE_PIXELS),
                    math.floor((gx0 + width) / TILE_PIXELS) + 1):
        for ty in range(math.floor(gy0 / TILE_PIXELS),
                        math.floor((gy0 + height) / TILE_PIXELS) + 1):
            if not (0 <= ty < n):
                continue
            try:
                path = _fetch_tile(zoom, tx % n, ty)
            except (urllib.error.URLError, OSError):
                # A missing tile is a dark square, not a failed preview.
                continue
            placed.append((round(tx * TILE_PIXELS - gx0),
                           round(ty * TILE_PIXELS - gy0), path))
    return mpp, placed, (zoom, gx0, gy0)


# --------------------------------------------------------------------------
# the Overpass extract
# --------------------------------------------------------------------------

def _overpass_query(bbox, classes) -> str:
    south, west, north, east = bbox
    pattern = "|".join(classes)
    return (f'[out:json][timeout:120];\n'
            f'way["highway"~"^({pattern})$"]'
            f'({south:.6f},{west:.6f},{north:.6f},{east:.6f});\n'
            f'out geom;\n')


def fetch_roads(bbox, classes, log=print, cancelled=lambda: False) -> dict:
    """The Overpass extract as overpass-turbo-style GeoJSON.

    Walks the endpoint list, waiting out 429/504 -- the request itself is
    cheap, the queue is the problem.  Raises ``RuntimeError`` with a
    readable sentence when every endpoint stays busy.
    """
    body = urllib.parse.urlencode({"data": _overpass_query(bbox, classes)})
    last_error = "no endpoint answered"
    for attempt in range(6):
        if cancelled():
            raise RuntimeError("cancelled")
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        host = urllib.parse.urlparse(endpoint).netloc
        log(f"asking {host} for the roads...")
        request = urllib.request.Request(
            endpoint, data=body.encode(),
            headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.load(response)
            return _to_geojson(data)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            code = getattr(exc, "code", None)
            last_error = f"{host}: {exc}"
            log(f"  {host} did not answer ({code or exc}); trying the next")
            if code not in (429, 504, None):
                raise RuntimeError(
                    f"the map server refused the request ({exc})") from exc
            time.sleep(5)
    raise RuntimeError(
        "every map server is overloaded right now -- try again in a few "
        f"minutes ({last_error})")


def _to_geojson(data: dict) -> dict:
    features = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        coords = [[pt["lon"], pt["lat"]] for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        props = dict(el.get("tags", {}))
        props["@id"] = f"way/{el['id']}"
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return {
        "type": "FeatureCollection",
        "generator": "overpass-api via DhakaSim map import",
        "copyright": "The data included in this document is from "
                     "www.openstreetmap.org. The data is made available "
                     "under ODbL.",
        "features": features,
    }


# --------------------------------------------------------------------------
# the build chain
# --------------------------------------------------------------------------

class ImportJob:
    """One import, from a point on Earth to a runnable ``input/`` folder.

    ``run(log, cancelled)`` drives the whole chain; ``log`` receives one
    human-readable line at a time and ``cancelled()`` is polled between and
    during steps.  On success the network folder's name is returned; on
    failure ``RuntimeError`` carries a sentence fit for the dialog.
    """

    def __init__(self, place_name: str, folder: str, lat: float, lon: float,
                 radius_m: float, classes, merge_dual: bool = True,
                 kind: str = "multi"):
        self.place_name = place_name
        self.folder = folder
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m
        self.classes = tuple(classes)
        self.merge_dual = merge_dual
        self.kind = "single" if kind == "single" else "multi"
        self._process = None
        self.out_dir = None
        self.warnings: list[str] = []

    # -- subprocess plumbing ------------------------------------------------

    def _run_script(self, args, log, cancelled, what: str) -> int:
        """One command-line tool, output streamed into the log.

        UTF-8 is forced on the child (PYTHONIOENCODING): the scripts print
        street names, and a cp1252 console kills them on the first Bengali
        character otherwise.
        """
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self._process = subprocess.Popen(
            [sys.executable] + list(args), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for raw in self._process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    log("  " + line)
                if cancelled():
                    self._process.terminate()
                    raise RuntimeError("cancelled")
            return self._process.wait()
        finally:
            self._process = None

    def cancel(self) -> None:
        process = self._process
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    # -- the chain ----------------------------------------------------------

    def run(self, log=print, cancelled=lambda: False) -> str:
        """The whole chain in one go -- the multi-intersection path."""
        self.fetch_and_build(log, cancelled)
        return self.finish(log, cancelled)

    def fetch_and_build(self, log=print, cancelled=lambda: False) -> str:
        """Stage one: fetch the roads and build node/link/geometry.

        Stops there, so a single-intersection import can put the junction
        picker between the build and everything that depends on the final
        link set (routes, demand, imagery, fitting).  On failure the
        half-built folder is removed -- a folder with a ``node.txt`` is a
        start-screen tile, and a broken tile crashes the run.
        """
        out_dir = os.path.join("input", self.folder)
        if os.path.exists(out_dir):
            raise RuntimeError(
                f"input/{self.folder} already exists -- pick another name")

        # No slip roads (``*_link``).  The model states a junction as arms
        # converging on a point and has nothing to do with a slip road;
        # what the slip roads do is wreck the staged graph -- at Shahbag
        # they fused into a hairpin and a loop, left an arm dangling 10 m
        # short of the crossing, and the junction came out with two legs.
        # Without them the same extract is four crossing-nodes and four
        # arms.  Both kinds go through the junction picker now, so both
        # want the clean graph.
        classes = tuple(c for c in self.classes if not c.endswith("_link"))
        bbox = bbox_around(self.lat, self.lon, self.radius_m)
        geojson = fetch_roads(bbox, classes, log, cancelled)
        ways = len(geojson["features"])
        if ways == 0:
            raise RuntimeError(
                "no roads of the chosen classes there -- try a larger "
                "radius, or include local streets")
        log(f"{ways} roads fetched.")

        os.makedirs(out_dir)
        self.out_dir = out_dir
        try:
            extract = os.path.join(out_dir, "osm_extract.geojson")
            with open(extract, "w", encoding="utf-8") as handle:
                json.dump(geojson, handle)

            log("building the network...")
            args = [
                "make_network.py", extract, "--out", out_dir,
                "--centre", f"{self.lat},{self.lon}",
                "--radius", str(float(self.radius_m)),
                "--classes", *classes,
                "--note", f"Imported via the GUI map-import dialog "
                          f"around {self.lat:.5f},{self.lon:.5f}",
            ]
            if not self.merge_dual:
                args.append("--no-merge-dual")
            if self._run_script(args, log, cancelled, "make_network") != 0:
                raise RuntimeError("the network build failed -- see the "
                                   "log")
            if not os.path.exists(os.path.join(out_dir, "link.txt")):
                raise RuntimeError("the network build produced no links")
        except BaseException:
            self.discard()
            raise
        return out_dir

    def discard(self) -> None:
        """Remove the staged folder -- for a cancelled or abandoned pick."""
        if self.out_dir:
            shutil.rmtree(self.out_dir, ignore_errors=True)

    def finish(self, log=print, cancelled=lambda: False) -> str:
        """Stage two: routes and demand, metadata, imagery, road fitting.

        Runs after any pruning of the staged network; everything here
        derives from the final link set.  On failure the folder is
        removed, same as stage one.
        """
        try:
            return self._finish(log, cancelled)
        except BaseException:
            self.discard()
            raise

    def _finish(self, log, cancelled) -> str:
        out_dir = self.out_dir

        log("generating routes and demand...")
        if self._run_script(["run_sim.py", "--network", self.folder],
                            log, cancelled, "run_sim") != 0:
            raise RuntimeError("route generation failed -- the extract may "
                               "have too few entry points; try a larger "
                               "radius")
        if not os.path.exists(os.path.join(out_dir, "demand.txt")):
            raise RuntimeError("no demand was generated -- the extract has "
                               "fewer than two boundary roads; try a larger "
                               "radius")

        # The name people will see, and a sensible signal default.  Written
        # UTF-8 without a BOM: a BOM here reaches the legend as text.
        with open(os.path.join(out_dir, "place.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(f"{self.place_name} (imported from OpenStreetMap)\n")
        with open(os.path.join(out_dir, "defaults.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "# Place-not-run settings (see defaults.txt handling in\n"
                "# utilities.apply_network_defaults).\n"
                "SignalChangeDuration 20     # s of green per approach -- "
                "VISSIM-style fixed timing\n")
        with open(os.path.join(out_dir, KIND_FILE), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "# Written by the GUI map-import dialog.\n"
                f"kind {self.kind}\n")

        # From here on the network is runnable; imagery and fitting improve
        # it but a hiccup must not throw the import away.
        log("fetching the background map...")
        # Zoom 18 over a wide extract busts fetch_basemap's tile budget;
        # one step down quarters the tile count and is still street-legible.
        zoom = "18" if self.radius_m <= 1500 else "17"
        # The arms stop at the import circle; the imagery carries on well
        # past them, or the window shows bare ground the moment the view
        # is wider than the network (a 60 m margin round a 300 m junction
        # left half the screen blank).  Less for a multi import, whose
        # kilometre radius is already near fetch_basemap's tile budget.
        margin = "200" if self.kind == "single" else "120"
        if self._run_script(
                ["fetch_basemap.py", "--network", self.folder,
                 "--provider", "osm", "--zoom", zoom, "--margin", margin],
                log, cancelled, "fetch_basemap") != 0:
            self.warnings.append(
                "the background map could not be fetched -- run "
                f"fetch_basemap.py --network {self.folder} later")

        log("bending the links onto the real roads...")
        if self._run_script(["fit_roads.py", "--network", self.folder],
                            log, cancelled, "fit_roads") != 0:
            self.warnings.append(
                "road fitting failed -- the network keeps its straight "
                f"links; run fit_roads.py --network {self.folder} later")

        log("done.")
        return self.folder
