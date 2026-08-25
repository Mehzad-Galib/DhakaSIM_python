#!/usr/bin/env python3
"""Download the map imagery that sits behind a network in the GUI.

VISSIM models are built on an aerial photograph, and a DhakaSim network can be
too.  This fetches OpenStreetMap tiles covering a network's extent, stitches
them into one image, and writes it next to the network's ``link.txt`` as
``basemap.png`` plus a ``basemap.txt`` recording the corner coordinates.
:mod:`dhakasim.basemap` reads that pair; nothing else in the simulator touches
the network.

    python fetch_basemap.py --list
    python fetch_basemap.py --network kakrail_corridor
    python fetch_basemap.py --all --zoom 18

This is the only part of the project that goes online, and it is deliberately a
separate command rather than something a run does for you.

Where the imagery comes from
----------------------------

``--provider esri`` is the default and gives aerial photography from Esri's
World Imagery service, credited to Esri, Maxar and Earthstar Geographics.
``--provider osm`` gives the OpenStreetMap street rendering instead, (c)
OpenStreetMap contributors under the ODbL.  Either way the credit travels in
``basemap.txt`` and the GUI prints it in the corner of the view.

Photography is the better default.  A drawn street map generalises where roads
are and how wide they are, so a network laid over one looks misaligned even
where it is not.

``--provider google`` uses the Google Static Maps API, with
``--maptype terrain`` by default and ``satellite``, ``hybrid`` and ``roadmap``
also available.  It needs **your own** API key, read from the
``GOOGLE_MAPS_API_KEY`` environment variable; this never asks for one and never
stores one.  Note what it costs: the Static Maps API returns at most 640 pixels
square per request and stamps a Google logo into the corner of every one, so a
junction comes back as a grid of images each carrying its own logo.  That is a
condition of the licence rather than something to work around -- cropping the
logo off would breach it.  The tile servers Google's own site uses are not an
option: their terms allow access through the APIs, not by fetching from those.

``--tile-url`` points this at any other XYZ tile service, which is how to use
another provider you hold a licence for.

Whatever the source, this asks for tiles one at a time with a pause between,
identifies itself properly, keeps what it has already downloaded in a cache,
and refuses outright to fetch more than ``--max-tiles``.

Which networks can have one
---------------------------

Any network whose ``geometry.txt`` records the lat/lon it was built around.
That covers every network converted from OpenStreetMap.  A network without one
cannot be placed on the Earth, so ``--centre lat,lon`` exists to supply it: the
coordinate must be the junction the network was drawn around, which for the
surveyed Dhaka networks is the point stored at (500, 500).

Standard library only, in keeping with the rest of the project.  The tiles are
decoded and stitched by Tk, which reads and writes PNG in 8.6, so there is no
image library here and none is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from dhakasim import basemap


#: Web Mercator tiles are 256 px square by long-standing convention.
TILE_PIXELS = 256

#: Sent with every request.  The OSM tile usage policy requires a User-Agent
#: that identifies the application, and blocks generic library defaults.
USER_AGENT = ("DhakaSim-basemap/1.0 (traffic simulation research; "
              "one-off network basemap fetch)")

#: Seconds between requests.  Slow on purpose.
REQUEST_DELAY = 0.12

CACHE_DIR = os.path.join("input", ".tilecache")

#: Half the circumference of the Earth at the equator, in Web Mercator metres.
#: The projection's square world runs from -MERCATOR_EDGE to +MERCATOR_EDGE on
#: both axes, which is what turns a tile index into a bounding box.
MERCATOR_EDGE = 20037508.342789244

#: Logical pixels a side in one Static Maps request.  Google caps a free-tier
#: image at 640 square, and every one carries a logo, so bigger is better.
GOOGLE_BLOCK_PX = 640

#: Tiles to a side in one ``export`` request.  Eight is 2048 pixels, which
#: every ArcGIS service will return in a single call, and it turns the hundred
#: requests a junction needs at zoom 19 into four.
BLOCK_TILES = 8


# --------------------------------------------------------------------------
# web mercator
# --------------------------------------------------------------------------

def deg_to_tile(lat: float, lon: float, zoom: int):
    """Fractional tile coordinates of a lat/lon at *zoom*."""
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    phi = math.radians(lat)
    y = (1.0 - math.log(math.tan(phi) + 1.0 / math.cos(phi)) / math.pi) / 2.0 * n
    return x, y


def tile_to_deg(x: float, y: float, zoom: int):
    """Lat/lon of a fractional tile coordinate, i.e. of a tile's NW corner."""
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def tile_to_mercator(x: float, y: float, zoom: int):
    """Web Mercator metres at a fractional tile coordinate.

    Tile rows count southwards from the top of the world and Mercator northings
    count upwards from the equator, hence the sign on y.
    """
    span = 2.0 * MERCATOR_EDGE / (2.0 ** zoom)
    return (-MERCATOR_EDGE + x * span, MERCATOR_EDGE - y * span)


def metres_per_pixel(lat: float, zoom: int) -> float:
    """Ground resolution of a tile pixel at this latitude."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2.0 ** zoom)


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def cache_key(url_template: str) -> str:
    """A short, stable folder name for a tile service.

    Keyed by the URL rather than the provider name so a custom --tile-url gets
    its own cache: two services' tile 12/34/56 are different pictures, and one
    silently standing in for the other would be very hard to spot.
    """
    host = url_template.split("//")[-1].split("/")[0]
    digest = hashlib.sha1(url_template.encode("utf-8")).hexdigest()[:6]
    return f"{host}_{digest}"


def tile_path(url_template: str, zoom: int, x: int, y: int) -> str:
    return os.path.join(CACHE_DIR, cache_key(url_template),
                        str(zoom), str(x), f"{y}.png")


def fetch_tile(url_template: str, zoom: int, x: int, y: int,
               retries: int = 3) -> str:
    """Return the path to a tile, downloading it if it is not already cached."""
    path = tile_path(url_template, zoom, x, y)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    url = url_template.format(z=zoom, x=x, y=y)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            break
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries - 1:
                raise SystemExit(f"could not fetch {url}: {exc}")
            time.sleep(delay)
            delay *= 2
    with open(path, "wb") as handle:
        handle.write(data)
    time.sleep(REQUEST_DELAY)
    return path


def pixel_to_deg(px: float, py: float, zoom: int):
    """Lat/lon of a Web Mercator pixel coordinate at *zoom*."""
    return tile_to_deg(px / TILE_PIXELS, py / TILE_PIXELS, zoom)


def fetch_google(service_url: str, api_key: str, maptype: str, zoom: int,
                 px: int, py: int, wide: int, high: int,
                 retries: int = 3) -> str:
    """One Static Maps image covering a block of the pixel grid.

    Addressed by centre and zoom rather than by bounding box, which is what the
    API takes, so the block's centre pixel is converted back to lat/lon here.
    """
    path = os.path.join(CACHE_DIR, f"google_{maptype}", str(zoom),
                        f"{px}_{py}_{wide}x{high}.png")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    lat, lon = pixel_to_deg(px + wide / 2.0, py + high / 2.0, zoom)
    query = urllib.parse.urlencode({
        "center": f"{lat:.7f},{lon:.7f}",
        "zoom": str(zoom),
        "size": f"{wide}x{high}",
        "maptype": maptype,
        "format": "png",
        "key": api_key,
    })
    request = urllib.request.Request(f"{service_url}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as exc:
            # The URL carries the key, so it must never reach the console.
            body = exc.read()[:200].decode("utf-8", "replace")
            raise SystemExit(f"Google Static Maps refused the request "
                             f"({exc.code}): {body}")
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries - 1:
                raise SystemExit(f"Google Static Maps request failed: {exc}")
            time.sleep(delay)
            delay *= 2
    if not data.startswith(b"\x89PNG"):
        raise SystemExit("Google Static Maps did not return a PNG.")
    with open(path, "wb") as handle:
        handle.write(data)
    time.sleep(REQUEST_DELAY)
    return path


def block_path(url_template: str, zoom: int, x: int, y: int,
               tiles_x: int, tiles_y: int) -> str:
    return os.path.join(CACHE_DIR, cache_key(url_template), str(zoom),
                        f"block_{x}_{y}_{tiles_x}x{tiles_y}.png")


def fetch_block(service_url: str, zoom: int, x: int, y: int,
                tiles_x: int, tiles_y: int, retries: int = 3) -> str:
    """Fetch one rectangle of imagery from an ArcGIS ``export`` service.

    Asked for in Web Mercator on both sides, so the picture that comes back
    lines up with the tile grid exactly rather than being reprojected into it.
    """
    path = block_path(service_url, zoom, x, y, tiles_x, tiles_y)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    west, north = tile_to_mercator(x, y, zoom)
    east, south = tile_to_mercator(x + tiles_x, y + tiles_y, zoom)
    query = urllib.parse.urlencode({
        "bbox": f"{west},{south},{east},{north}",
        "bboxSR": "3857",
        "imageSR": "3857",
        "size": f"{tiles_x * TILE_PIXELS},{tiles_y * TILE_PIXELS}",
        "format": "png",
        "f": "image",
    })
    url = f"{service_url}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            break
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries - 1:
                raise SystemExit(f"could not fetch {url}: {exc}")
            time.sleep(delay)
            delay *= 2
    if not data.startswith(b"\x89PNG"):
        raise SystemExit(
            f"{service_url} did not return a PNG. Tk can only decode PNG, GIF "
            f"and PPM, so a JPEG service cannot be used here.")
    with open(path, "wb") as handle:
        handle.write(data)
    time.sleep(REQUEST_DELAY)
    return path


def stitch(paths, columns: int, rows: int, out_path: str) -> None:
    """Assemble a grid of tiles into one PNG.

    Tk decodes and writes PNG itself, so the whole job is a sequence of
    ``image copy`` calls into one big photo.  The root window is never shown.
    """
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        canvas = tk.PhotoImage(width=columns * TILE_PIXELS,
                               height=rows * TILE_PIXELS)
        for (column, row), path in paths.items():
            piece = tk.PhotoImage(file=path)
            canvas.tk.call(canvas, "copy", piece, "-to",
                           int(round(column * TILE_PIXELS)),
                           int(round(row * TILE_PIXELS)))
            # Tk keeps every photo alive until told otherwise, and a few
            # hundred of them adds up.
            del piece
        canvas.write(out_path, format="png")
    finally:
        root.destroy()


# --------------------------------------------------------------------------
# one network
# --------------------------------------------------------------------------

def build(network: str, zoom: int, margin: float, url_template: str,
          max_tiles: int, centre=None, dry_run: bool = False,
          attribution: str = "", kind: str = "xyz",
          api_key: str = "", maptype: str = "terrain") -> bool:
    try:
        link_list, node_list = basemap.read_network(network)
    except OSError as exc:
        print(f"{network}: cannot read the network ({exc.strerror})")
        return False

    if centre is not None:
        anchor = basemap.anchor_point(link_list, node_list,
                                      basemap.read_roundabout(network))
        geo = basemap.Georeference(anchor[0], anchor[1], centre[0], centre[1],
                                   anchor[2]) if anchor else None
    else:
        geo = basemap.georeference(network, link_list, node_list)
    if geo is None:
        print(f"{network}: no centre coordinate in geometry.txt. "
              f"Pass --centre lat,lon to place it by hand.")
        return False

    bounds = basemap.network_bounds(link_list, margin)
    if bounds is None:
        print(f"{network}: the network has no roads")
        return False
    left, top, right, bottom = bounds

    # Corners as lat/lon, then as the tile grid that covers them.
    west, north = geo.to_lonlat(left, top)[0], geo.to_lonlat(left, top)[1]
    east, south = geo.to_lonlat(right, bottom)[0], geo.to_lonlat(right, bottom)[1]
    x0f, y0f = deg_to_tile(north, west, zoom)
    x1f, y1f = deg_to_tile(south, east, zoom)
    x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
    x1, y1 = int(math.ceil(x1f)), int(math.ceil(y1f))
    columns, rows = x1 - x0, y1 - y0
    count = columns * rows

    resolution = metres_per_pixel(geo.lat, zoom)
    print(f"{network}: {right - left:.0f} x {bottom - top:.0f} m, "
          f"anchored on {geo.source} at {geo.lat:.6f},{geo.lon:.6f}")
    print(f"  zoom {zoom} = {resolution:.3f} m per pixel; "
          f"{columns} x {rows} = {count} tiles "
          f"-> {columns * TILE_PIXELS} x {rows * TILE_PIXELS} px")
    if count > max_tiles:
        print(f"  refusing: more than --max-tiles ({max_tiles}). "
              f"Use a lower --zoom or raise the limit.")
        return False
    if dry_run:
        return True

    paths = {}
    if kind == "google":
        # Google is addressed in pixels rather than tiles, so the tile grid is
        # converted once and the blocks laid out on it.
        px0, py0 = x0 * TILE_PIXELS, y0 * TILE_PIXELS
        wide_px, high_px = columns * TILE_PIXELS, rows * TILE_PIXELS
        blocks = [(bx, by,
                   min(GOOGLE_BLOCK_PX, wide_px - bx),
                   min(GOOGLE_BLOCK_PX, high_px - by))
                  for bx in range(0, wide_px, GOOGLE_BLOCK_PX)
                  for by in range(0, high_px, GOOGLE_BLOCK_PX)]
        print(f"  {len(blocks)} Static Maps request(s); each carries its own "
              f"Google logo, which the licence requires be left in place")
        for done, (bx, by, wide, high) in enumerate(blocks, start=1):
            paths[(bx / float(TILE_PIXELS), by / float(TILE_PIXELS))] = (
                fetch_google(url_template, api_key, maptype, zoom,
                             px0 + bx, py0 + by, wide, high))
            print(f"  {done}/{len(blocks)}", flush=True)
    elif kind == "export":
        # One request per block of BLOCK_TILES square, clipped at the edges.
        blocks = [(cx, cy, min(BLOCK_TILES, columns - cx),
                   min(BLOCK_TILES, rows - cy))
                  for cx in range(0, columns, BLOCK_TILES)
                  for cy in range(0, rows, BLOCK_TILES)]
        print(f"  {len(blocks)} request(s) of up to "
              f"{BLOCK_TILES * TILE_PIXELS} px square")
        for done, (cx, cy, wide, high) in enumerate(blocks, start=1):
            paths[(cx, cy)] = fetch_block(url_template, zoom,
                                          x0 + cx, y0 + cy, wide, high)
            print(f"  {done}/{len(blocks)}", flush=True)
    else:
        cached = sum(1 for cx in range(x0, x1) for cy in range(y0, y1)
                     if os.path.exists(tile_path(url_template, zoom, cx, cy)))
        print(f"  {cached} already cached, {count - cached} to download")
        done = 0
        for column in range(columns):
            for row in range(rows):
                paths[(column, row)] = fetch_tile(url_template, zoom,
                                                  x0 + column, y0 + row)
                done += 1
                if done % 25 == 0 or done == count:
                    print(f"  {done}/{count}", flush=True)

    image_path = basemap.network_path(network, basemap.IMAGE_NAME)
    stitch(paths, columns, rows, image_path)

    # The written bounds are the tile grid's own corners, not the network's,
    # because that is what the image actually covers.
    grid_north, grid_west = tile_to_deg(x0, y0, zoom)
    grid_south, grid_east = tile_to_deg(x1, y1, zoom)
    index_path = basemap.network_path(network, basemap.INDEX_NAME)
    with open(index_path, "w", encoding="utf-8") as handle:
        handle.write("# DhakaSim basemap, written by fetch_basemap.py.\n")
        handle.write(f"# {attribution}\n")
        handle.write(f"# Anchored on {geo.source} at "
                     f"{geo.lat:.6f},{geo.lon:.6f}.\n")
        handle.write("#\n# bounds are west north east south, in degrees.\n")
        handle.write(f"source {url_template}\n")
        handle.write(f"zoom {zoom}\n")
        handle.write(f"size {columns * TILE_PIXELS} {rows * TILE_PIXELS}\n")
        handle.write(f"bounds {grid_west:.7f} {grid_north:.7f} "
                     f"{grid_east:.7f} {grid_south:.7f}\n")
        handle.write(f"attribution {attribution}\n")

    size_mb = os.path.getsize(image_path) / (1024.0 * 1024.0)
    print(f"  wrote {image_path} ({size_mb:.1f} MB) and {index_path}")
    return True


def networks_with_a_place():
    """Every network folder under ``input/``, and whether it can be placed."""
    found = []
    root = "input"
    for entry in sorted(os.listdir(root)):
        if not os.path.isfile(os.path.join(root, entry, "node.txt")):
            continue
        found.append((entry, basemap.read_centre(entry)))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch OpenStreetMap imagery to sit behind a network.")
    parser.add_argument("--network", help="folder under input/ to fetch for")
    parser.add_argument("--all", action="store_true",
                        help="every network that records a centre coordinate")
    parser.add_argument("--list", action="store_true",
                        help="show which networks can have a basemap")
    parser.add_argument("--zoom", type=int, default=18,
                        help="tile zoom level; 18 is about 0.55 m per pixel "
                             "in Dhaka (default: 18)")
    parser.add_argument("--margin", type=float, default=60.0,
                        help="metres of imagery beyond the outermost kerb "
                             "(default: 60)")
    parser.add_argument("--centre",
                        help="lat,lon of the junction the network was drawn "
                             "around, for a network whose geometry.txt does "
                             "not record one")
    parser.add_argument("--provider", default=basemap.DEFAULT_PROVIDER,
                        choices=sorted(basemap.PROVIDERS),
                        help="imagery source: esri is aerial photography, "
                             "osm is the drawn street map "
                             f"(default: {basemap.DEFAULT_PROVIDER})")
    parser.add_argument("--tile-url",
                        help="tile URL template with {z}/{x}/{y}, for a "
                             "service you hold a licence for; overrides "
                             "--provider")
    parser.add_argument("--attribution",
                        help="credit to record with a custom --tile-url")
    parser.add_argument("--maptype", default="terrain",
                        choices=("terrain", "satellite", "hybrid", "roadmap"),
                        help="Google map style (default: terrain)")
    parser.add_argument("--max-tiles", type=int, default=400,
                        help="refuse to fetch more than this many (default: 400)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the tile count and stop")
    args = parser.parse_args(argv)

    preset = basemap.PROVIDERS[args.provider]
    url_template = args.tile_url or preset["url"]
    kind = "xyz" if args.tile_url else preset["kind"]
    attribution = args.attribution or (
        "unattributed custom source" if args.tile_url else preset["attribution"])
    if args.zoom > preset["max_zoom"] and not args.tile_url:
        print(f"note: {args.provider} imagery runs out around zoom "
              f"{preset['max_zoom']}; above that it may come back blank.")

    api_key = ""
    if kind == "google":
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        if not api_key:
            print("--provider google needs your own Google Maps Platform key.\n"
                  "Set it in the environment first, so it never has to be "
                  "typed into a command line or a chat:\n"
                  "    setx GOOGLE_MAPS_API_KEY \"your-key\"      (Windows)\n"
                  "    export GOOGLE_MAPS_API_KEY='your-key'    (bash)\n"
                  "then open a new shell and run this again.")
            return 1
        print(f"using Google Static Maps, maptype {args.maptype}")

    if args.list or not (args.network or args.all):
        print("network            centre in geometry.txt")
        for name, centre in networks_with_a_place():
            where = (f"{centre[0]:.6f},{centre[1]:.6f}" if centre
                     else "-- none; needs --centre lat,lon")
            print(f"  {name:<18} {where}")
        if not (args.network or args.all):
            print("\nPass --network <name> or --all to fetch.")
        return 0

    centre = None
    if args.centre:
        lat, _, lon = args.centre.partition(",")
        centre = (float(lat), float(lon))

    if args.all:
        targets = [name for name, found in networks_with_a_place() if found]
        if not targets:
            print("no network records a centre coordinate")
            return 1
    else:
        targets = [args.network]

    if len(targets) > 1 and centre is not None:
        print("--centre applies to one network; use --network with it")
        return 1

    failures = 0
    for name in targets:
        if not build(name, args.zoom, args.margin, url_template,
                     args.max_tiles, centre, args.dry_run, attribution, kind,
                     api_key, args.maptype):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
