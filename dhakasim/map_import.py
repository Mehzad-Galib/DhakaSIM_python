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


def prune_to_junction(folder: str, keep_link_ids,
                      roundabout=None) -> None:
    """Cut a staged network down to one junction and its chosen legs.

    Rewrites ``link.txt``, ``node.txt``, ``geometry.txt`` and
    ``link_names.txt`` in place, keeping only the given links, renumbering
    both id spaces densely (the simulator indexes arrays by id).  A former
    junction left with one leg becomes a boundary node and is stored at
    its arm's endpoint -- the stored-coordinate-equals-arm-endpoint test
    is exactly how the file format tells the two apart.  Comment lines in
    ``geometry.txt`` travel verbatim, the recorded centre anchor with
    them, so the imagery georeference does not move.

    ``roundabout``, when given as ``(island_m, ring_m)``, declares the
    kept junction a roundabout in ``geometry.txt`` -- OSM's fused
    main-roads graph states every junction as arms converging on a point,
    which is exactly the form the survey networks use, and
    ``Processor._open_the_circle`` builds the ring from the directive.
    The junction is then unsignalised, as a roundabout should be.

    Routes and demand are NOT touched here -- the caller regenerates them
    (``run_sim.py``), which is why this must run before that step.
    """
    keep = set(keep_link_ids)
    link_rows = [row for row
                 in network_files.read_link_rows(
                     os.path.join(folder, "link.txt"))
                 if row.link_id in keep]
    if len(link_rows) < 2:
        raise RuntimeError("keep at least two legs, or there is nothing "
                           "to route between")
    new_link_id = {row.link_id: i for i, row in enumerate(link_rows)}

    node_rows = network_files.read_node_rows(os.path.join(folder,
                                                          "node.txt"))
    kept_nodes = [row for row in node_rows
                  if any(i in keep for i in row.link_ids)]
    new_node_id = {row.node_id: i for i, row in enumerate(kept_nodes)}

    def arm_end(row, node_id):
        seg = (row.segments[0] if row.up == node_id else row.segments[-1])
        return ((seg.sx, seg.sy) if row.up == node_id
                else (seg.ex, seg.ey))

    links_out = []
    for row in link_rows:
        links_out.append((new_link_id[row.link_id],
                          new_node_id[row.up], new_node_id[row.down],
                          [(s.sx, s.sy, s.ex, s.ey, s.width)
                           for s in row.segments]))
    nodes_out = []
    for row in kept_nodes:
        incident = [i for i in row.link_ids if i in keep]
        x, y = row.x, row.y
        if len(incident) == 1:
            x, y = arm_end(next(r for r in link_rows
                                if r.link_id == incident[0]), row.node_id)
        elif len(incident) >= 2:
            x, y = 0.0, 0.0            # a junction, by the convention
        nodes_out.append((new_node_id[row.node_id], x, y,
                          sorted(new_link_id[i] for i in incident)))

    network_files.write_link_rows(os.path.join(folder, "link.txt"),
                                  links_out)
    network_files.write_node_rows(os.path.join(folder, "node.txt"),
                                  nodes_out)

    # geometry.txt: directives are filtered and remapped, comments (the
    # centre anchor included) pass through untouched.
    geometry_path = os.path.join(folder, "geometry.txt")
    kept_lines = []
    with open(geometry_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line, _, comment = raw.partition("#")
            tokens = line.split()
            if not tokens:
                kept_lines.append(raw)
                continue
            keyword = tokens[0].lower()
            tail = ("  #" + comment.rstrip("\n")) if comment else ""
            if keyword in ("median", "oneway", "straight"):
                link_id = int(tokens[1])
                if link_id in keep:
                    rest = " ".join(tokens[2:])
                    rest = (" " + rest) if rest else ""
                    kept_lines.append(
                        f"{keyword} {new_link_id[link_id]}{rest}{tail}\n")
            elif keyword == "turnlane":
                a, b = int(tokens[1]), int(tokens[2])
                if a in keep and b in keep:
                    kept_lines.append(
                        f"turnlane {new_link_id[a]} {new_link_id[b]} "
                        f"{tokens[3]} {tokens[4]}{tail}\n")
            elif keyword == "roundabout":
                node_id = int(tokens[1])
                if node_id in new_node_id:
                    rest = " ".join(tokens[2:])
                    kept_lines.append(
                        f"roundabout {new_node_id[node_id]} {rest}{tail}\n")
            else:
                kept_lines.append(raw)   # unknown directives pass through
    if roundabout is not None:
        # The kept junction is the node most of the legs meet at.
        counts = {}
        for _new_id, up, down, _rows in links_out:
            counts[up] = counts.get(up, 0) + 1
            counts[down] = counts.get(down, 0) + 1
        junction = max(counts, key=counts.get)
        island_m, ring_m = roundabout
        kept_lines.append(
            f"roundabout {junction} {island_m:g} {ring_m:g}"
            "   # declared a roundabout in the import dialog; edit these "
            "measured island/ring metres if known\n")
    with open(geometry_path, "w", encoding="utf-8") as handle:
        handle.writelines(kept_lines)

    names_path = os.path.join(folder, "link_names.txt")
    if os.path.isfile(names_path):
        renamed = []
        with open(names_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                tokens = raw.split(None, 1)
                if not tokens:
                    continue
                try:
                    link_id = int(tokens[0])
                except ValueError:
                    continue
                if link_id in keep:
                    name = tokens[1].strip() if len(tokens) > 1 else ""
                    renamed.append(f"{new_link_id[link_id]} {name}\n")
        with open(names_path, "w", encoding="utf-8") as handle:
            handle.writelines(renamed)


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

        bbox = bbox_around(self.lat, self.lon, self.radius_m)
        geojson = fetch_roads(bbox, self.classes, log, cancelled)
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
                "--classes", *self.classes,
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
        if self._run_script(
                ["fetch_basemap.py", "--network", self.folder,
                 "--provider", "osm", "--zoom", zoom],
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
