"""Real map imagery behind the road network.

VISSIM draws its networks over an aerial photograph, which is what makes a
model recognisable as a *place* rather than as a diagram.  This module does the
same for DhakaSim: it works out where a network sits on the Earth, and it
displays a pre-fetched raster underneath the carriageways.

Nothing here goes near the network.  Downloading imagery is
``fetch_basemap.py``'s job, run by hand; this module only reads what that left
behind.  A network with no ``basemap.png`` simply has no basemap, and every
entry point below returns ``None`` rather than raising.

Georeferencing
--------------

The networks were converted from OpenStreetMap by ``make_network.py``, which
projects longitude and latitude onto a local flat-earth grid of metres with
**y increasing southwards**, the same sense as the screen and the same sense as
a web-map tile row.  So a network's metre grid and a slippy map differ by a
translation and a uniform scale, with no rotation and no flip.  Pinning the
translation therefore needs exactly one control point:

* every converted network's ``geometry.txt`` records the lat/lon the extract
  was centred on, which for the surveyed Dhaka junctions is the junction
  itself;
* in the network that point is the node where the arms meet, which is the one
  node of highest degree.

Where no single node is busiest -- the idealised Miami and Riyadh grids, whose
junctions are all alike -- the centre of the network's bounding box is used
instead, because that is what ``--centre`` was given when the grid was built.

Scaling, and why the zoom snaps
-------------------------------

``tkinter.PhotoImage`` can only rescale by whole-number factors: it subsamples
by an integer and zooms by an integer.  An arbitrary zoom therefore cannot be
matched exactly, and "nearly" is not good enough, since a fraction of a percent
of error over a few thousand pixels puts the imagery tens of metres off the
roads drawn on top of it.  :meth:`BaseMap.snap` rounds the requested scale to
one the image can hit exactly.  That gives a ladder of zoom steps, which is
what every slippy map does anyway, and it keeps the photograph and the model in
register at every step.

Because the view is moved onto the ratio rather than the other way round, the
ratio does not need to be *near* the requested scale.  What it needs to be is
**crisp**.  Tk subsamples before it zooms, so one surviving source pixel is
painted as a square block of screen pixels whose side is the zoom factor: a
ratio of 16/13 throws away twelve pixels in thirteen and paints the rest as
sixteen-pixel blocks, while 5/4 sits two per cent further from the request and
looks three times better.  :meth:`BaseMap._ratio` therefore searches by
ascending zoom and takes the first ratio that is close enough, rather than the
closest one it can find.
"""

from __future__ import annotations

import math
import os
import re

from .parameters import Parameters
from . import road_geometry


#: Imagery sources ``fetch_basemap.py`` knows about.
#:
#: ``kind`` says how a source is asked for a picture.  ``xyz`` is the ordinary
#: slippy-map tile path.  ``export`` is an ArcGIS REST service, which takes a
#: bounding box and an output size, so it can be asked for one big block
#: instead of a few hundred tiles -- and, unlike Esri's tile path, it will
#: return PNG.  That matters because Tk reads PNG, GIF and PPM and nothing
#: else, so the JPEG the tile path serves could not be decoded here at all.
#:
#: Recorded in this module rather than in the fetcher so that the credit a
#: basemap must carry travels with the code that draws it.
PROVIDERS = {
    "esri": {
        "kind": "export",
        "url": ("https://services.arcgisonline.com/arcgis/rest/services/"
                "World_Imagery/MapServer/export"),
        "attribution": "Imagery (c) Esri, Maxar, Earthstar Geographics",
        "max_zoom": 19,
    },
    "osm": {
        "kind": "xyz",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "(c) OpenStreetMap contributors",
        "max_zoom": 19,
    },
    # Through the Static Maps API with your own key, which is the licensed way
    # in.  See fetch_basemap.py for what that costs you in picture quality.
    "google": {
        "kind": "google",
        "url": "https://maps.googleapis.com/maps/api/staticmap",
        "attribution": "Map data (c) Google",
        "max_zoom": 20,
    },
}

#: Aerial photography rather than a drawn street map, which is what a model
#: wants underneath it: a drawn map generalises road width and position, so a
#: network laid over one looks misaligned even where it is not.
DEFAULT_PROVIDER = "esri"

#: Kept for callers that predate :data:`PROVIDERS`.
TILE_URL = PROVIDERS["osm"]["url"]
ATTRIBUTION = PROVIDERS["osm"]["attribution"]


#: Both files live beside the network's ``link.txt``.
IMAGE_NAME = "basemap.png"
INDEX_NAME = "basemap.txt"

#: ``# Centre 23.79,90.40`` and ``centre 23.75,90.38, radius 28.8 m`` both
#: appear in the shipped ``geometry.txt`` files, hence the loose match.
_CENTRE_RE = re.compile(r"centre\s+(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)",
                        re.IGNORECASE)

#: ``roundabout <node id> <radius>``.  A roundabout is the landmark a junction
#: extract gets centred on, so where one is declared it names the anchor node.
_ROUNDABOUT_RE = re.compile(r"^roundabout\s+(\d+)\s", re.MULTILINE)


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------

class Projector:
    """Local flat-earth projection about a reference point.

    A copy of ``make_network.Projector``, kept here so that the package does
    not import a top-level script.  ``tests/test_basemap.py`` pins the two
    against each other, since a silent divergence would slide every basemap.
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

    def to_lonlat(self, x: float, y: float):
        return (self.lon0 + x / self.m_per_deg_lon,
                self.lat0 - y / self.m_per_deg_lat)


class Georeference:
    """Ties a network's metre grid to longitude and latitude.

    ``anchor`` is a point in network metres known to be at ``lat``/``lon``.
    Everything else follows from the projection, because the two grids differ
    by a translation only.
    """

    def __init__(self, anchor_x: float, anchor_y: float,
                 lat: float, lon: float, source: str = ""):
        self.anchor_x = anchor_x
        self.anchor_y = anchor_y
        self.lat = lat
        self.lon = lon
        self.source = source
        self.projector = Projector(lat, lon)

    def to_xy(self, lon: float, lat: float):
        """Longitude/latitude to network metres."""
        x, y = self.projector.to_xy(lon, lat)
        return (self.anchor_x + x, self.anchor_y + y)

    def to_lonlat(self, x: float, y: float):
        """Network metres to longitude/latitude."""
        return self.projector.to_lonlat(x - self.anchor_x, y - self.anchor_y)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"Georeference(({self.anchor_x:.1f},{self.anchor_y:.1f}) = "
                f"{self.lat:.6f},{self.lon:.6f} via {self.source})")


# --------------------------------------------------------------------------
# working out where a network is
# --------------------------------------------------------------------------

def network_path(network: str, *parts: str) -> str:
    """Path to a file inside a network folder, honouring the flat layout.

    ``Parameters.NETWORK_DIR`` is empty when the inputs sit directly in
    ``input/``, which is how the Java original shipped.
    """
    return os.path.join("input", network, *parts) if network \
        else os.path.join("input", *parts)


def read_centre(network: str):
    """The lat/lon a network's ``geometry.txt`` says it was centred on."""
    path = network_path(network, "geometry.txt")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    match = _CENTRE_RE.search(text)
    if match is None:
        return None
    return (float(match.group(1)), float(match.group(2)))


def read_roundabout(network: str):
    """The node id of the network's roundabout, when it declares exactly one."""
    path = network_path(network, "geometry.txt")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            found = _ROUNDABOUT_RE.findall(handle.read())
    except OSError:
        return None
    return int(found[0]) if len(found) == 1 else None


def anchor_point(link_list, node_list, roundabout=None):
    """The network point that the recorded centre lat/lon refers to.

    Three rules, in order.  A declared roundabout wins, because a roundabout is
    the landmark whose coordinates get written down: on the Kakrail corridor
    the recorded centre is the circle, which is neither the middle of the
    corridor nor its only three-armed node.  Failing that, the busiest node,
    which is the junction a single-junction extract was cut around.  Failing
    that, the middle of the network, which is what a regular grid's centre
    coordinate means.

    Returns the point and a word saying which rule produced it, so a misplaced
    basemap can be diagnosed without re-deriving this by hand.
    """
    if roundabout is not None:
        for node in node_list:
            if node.get_id() == roundabout:
                x, y = road_geometry.node_point(link_list, node)
                return (x, y, f"roundabout node {roundabout}")

    best, runner_up, best_node = -1, -1, None
    for node in node_list:
        degree = node.number_of_links()
        if degree > best:
            best, runner_up, best_node = degree, best, node
        elif degree > runner_up:
            runner_up = degree
    if best_node is not None and best > runner_up:
        x, y = road_geometry.node_point(link_list, best_node)
        return (x, y, f"node {best_node.get_id()}")

    xs, ys = [], []
    for link in link_list:
        for i in range(link.get_number_of_segments()):
            segment = link.get_segment(i)
            xs.extend((segment.get_start_x(), segment.get_end_x()))
            ys.extend((segment.get_start_y(), segment.get_end_y()))
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0, "bounding box")


def georeference(network: str, link_list, node_list):
    """Where this network sits on the Earth, or ``None`` if it does not say."""
    centre = read_centre(network)
    if centre is None:
        return None
    anchor = anchor_point(link_list, node_list, read_roundabout(network))
    if anchor is None:
        return None
    lat, lon = centre
    x, y, rule = anchor
    return Georeference(x, y, lat, lon, rule)


def network_bounds(link_list, margin: float = 60.0):
    """Bounding box of every carriageway, in metres, with a margin round it.

    The margin keeps the imagery from stopping exactly at the kerb, which
    would look like the map had been cut out with scissors.
    """
    xs, ys = [], []
    for link in link_list:
        for i in range(link.get_number_of_segments()):
            segment = link.get_segment(i)
            xs.extend((segment.get_start_x(), segment.get_end_x()))
            ys.extend((segment.get_start_y(), segment.get_end_y()))
    if not xs:
        return None
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


# --------------------------------------------------------------------------
# a network, read without starting a simulation
# --------------------------------------------------------------------------

class _Segment:
    __slots__ = ("sx", "sy", "ex", "ey", "width")

    def __init__(self, sx, sy, ex, ey, width):
        self.sx, self.sy, self.ex, self.ey, self.width = sx, sy, ex, ey, width

    def get_start_x(self): return self.sx
    def get_start_y(self): return self.sy
    def get_end_x(self): return self.ex
    def get_end_y(self): return self.ey
    def get_segment_width(self): return self.width


class _Link:
    __slots__ = ("id", "up", "down", "segments")

    def __init__(self, id_, up, down, segments):
        self.id, self.up, self.down, self.segments = id_, up, down, segments

    def get_id(self): return self.id
    def get_up_node(self): return self.up
    def get_down_node(self): return self.down
    def get_number_of_segments(self): return len(self.segments)
    def get_segment(self, i): return self.segments[i]
    def get_first_segment(self): return self.segments[0]
    def get_last_segment(self): return self.segments[-1]


class _Node:
    __slots__ = ("id", "x", "y", "links")

    def __init__(self, id_, x, y, links):
        self.id, self.x, self.y, self.links = id_, x, y, links

    def get_id(self): return self.id
    def number_of_links(self): return len(self.links)
    def get_link(self, i): return self.links[i]

    # node.txt says nothing about roundabouts -- that lives in geometry.txt and
    # only applies under GeometryMode -- but road_geometry asks, so answer.
    # Without these a caller that reads a network this way and then builds its
    # geometry falls over on an attribute error rather than drawing a plain
    # junction, which is what it should get.
    def is_roundabout(self): return False
    def get_roundabout_radius(self): return 0.0
    def get_outer_radius(self): return 0.0


def read_network(network: str):
    """Read ``link.txt`` and ``node.txt`` into the accessors this module uses.

    ``fetch_basemap.py`` needs a network's shape and nothing else, and starting
    the real loader would mean reading demand, generating routes and seeding a
    fleet first.  The objects returned answer the same calls
    :func:`anchor_point` and :func:`network_bounds` make of the simulator's
    own, so both callers go down one code path.
    """
    with open(network_path(network, "link.txt"), "r", encoding="utf-8") as f:
        tokens = f.read().split()
    at = 0
    link_count = int(tokens[at]); at += 1
    links = []
    for _ in range(link_count):
        link_id, up, down, count = (int(tokens[at + i]) for i in range(4))
        at += 4
        segments = []
        for _ in range(count):
            values = tokens[at:at + 6]
            at += 6
            segments.append(_Segment(*(float(v) for v in values[1:])))
        links.append(_Link(link_id, up, down, segments))

    nodes = []
    with open(network_path(network, "node.txt"), "r", encoding="utf-8") as f:
        lines = [ln.split() for ln in f if ln.split()]
    for parts in lines[1:1 + int(lines[0][0])]:
        nodes.append(_Node(int(parts[0]), float(parts[1]), float(parts[2]),
                           [int(v) for v in parts[3:]]))
    return links, nodes


# --------------------------------------------------------------------------
# the imagery itself
# --------------------------------------------------------------------------

class BaseMap:
    """A georeferenced raster, ready to draw under the road network.

    ``left``/``top``/``right``/``bottom`` are the image's edges in network
    metres, so placing it is a matter of the same transform everything else on
    the canvas already goes through.
    """

    #: Largest integer zoom applied to the imagery.  This is the one number
    #: that governs how the imagery *looks*: Tk subsamples and then zooms, so
    #: one surviving source pixel is painted as a ``zoom``-square block of
    #: screen pixels, and the zoom factor is the block size in pixels.
    #:
    #: Two, which is to say: essentially never magnify.  Tk's only enlargement
    #: is pixel replication, so any zoom above one is visible as blocks, and at
    #: five -- which is what the default view worked out at -- the imagery
    #: looks smashed.  Capping here means the view snaps back to the imagery's
    #: own resolution rather than blowing it up, which is what a slippy map
    #: does when it runs out of tiles.  Fetch at a finer zoom to see more,
    #: rather than asking Tk to invent it.
    MAX_ZOOM = 2

    #: Largest integer subsample.  A big subsample is only costly when paired
    #: with a big zoom, since discarding pixels you were never going to show is
    #: free: at a factor of 1/40 the image is meant to be tiny.
    MAX_SUBSAMPLE = 64

    #: How far the chosen ratio may sit from the requested one before a
    #: crisper alternative stops being worth it.  Some slack is free, because
    #: :meth:`snap` moves the view onto whatever ratio is chosen, so a "miss"
    #: is not an error in the picture: it only means the zoom rung sits a few
    #: per cent from where the slider was let go.
    RATIO_TOLERANCE = 0.04

    def __init__(self, path, left, top, right, bottom,
                 width_px, height_px, attribution=""):
        self.path = path
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
        self.width_px = width_px
        self.height_px = height_px
        self.attribution = attribution
        # Uniform, because the tiles were stitched on a square grid.
        self.metres_per_pixel = (right - left) / float(width_px)
        self._source = None      # the full-resolution PhotoImage, loaded lazily
        self._cache_key = None
        self._cache_image = None

    # ---- loading ---------------------------------------------------------

    @classmethod
    def load(cls, network: str, link_list, node_list):
        """Read a network's basemap, or ``None`` if it has not got one."""
        image_path = network_path(network, IMAGE_NAME)
        index_path = network_path(network, INDEX_NAME)
        if not (os.path.exists(image_path) and os.path.exists(index_path)):
            return None

        # Georeference off the *files*, not off the network handed in.  The
        # anchor is the mean of a node's arm endpoints, and by the time a
        # caller has a live network the processor has pulled every arm at a
        # roundabout back to the ring, so that mean is no longer the survey's
        # junction at all -- it is the centroid of six points scattered round
        # a circle.  That displaced the imagery by 7.1 m at Khamarbari and
        # 5.2 m at Kakrail, half a carriageway, on exactly the two networks
        # that have a roundabout and nowhere else.  Where the ground truth
        # sits is a property of the survey data and nothing a run does to it
        # may move it.
        try:
            link_list, node_list = read_network(network)
        except (OSError, ValueError, IndexError):
            pass                          # fall back on what the caller has
        geo = georeference(network, link_list, node_list)
        if geo is None:
            return None

        size = bounds = None
        attribution = ""
        try:
            with open(index_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _, rest = line.partition(" ")
                    rest = rest.strip()
                    if key == "size":
                        size = tuple(int(v) for v in rest.split())
                    elif key == "bounds":
                        bounds = tuple(float(v) for v in rest.split())
                    elif key == "attribution":
                        attribution = rest
        except (OSError, ValueError):
            return None
        if size is None or bounds is None or len(size) != 2 or len(bounds) != 4:
            return None

        west, north, east, south = bounds
        left, top = geo.to_xy(west, north)
        right, bottom = geo.to_xy(east, south)

        # Web Mercator and the network's flat-earth grid are two different
        # projections, and putting a corner of one onto a corner of the other
        # leaves the image very slightly out of square: over a 700 m junction
        # the two disagree by about four metres vertically.  Mercator is
        # conformal, so a square tile really is square on the ground and the
        # disagreement is scale, not shape.  Take one resolution for both axes
        # and hang the image off its own centre, which puts the residual at the
        # corners, furthest from the junction the network was cut around,
        # rather than letting it accumulate from one edge.
        width_px, height_px = size
        centre_x = (left + right) / 2.0
        centre_y = (top + bottom) / 2.0
        resolution = ((right - left) / width_px
                      + (bottom - top) / height_px) / 2.0
        left = centre_x - width_px * resolution / 2.0
        top = centre_y - height_px * resolution / 2.0
        return cls(image_path, left, top,
                   left + width_px * resolution,
                   top + height_px * resolution,
                   width_px, height_px, attribution)

    def source_image(self):
        """The unscaled imagery, loaded on first use."""
        if self._source is None:
            import tkinter as tk
            self._source = tk.PhotoImage(file=self.path)
        return self._source

    # ---- scaling ---------------------------------------------------------

    def _ratio(self, screen_px_per_image_px: float):
        """The crispest whole-number ``(zoom, subsample)`` near the factor.

        Searched by ascending zoom, and the first ratio close enough wins.
        That ordering is the whole point.  Hunting for the ratio nearest the
        requested factor finds things like 16/13, which keeps one pixel in
        thirteen and then paints each survivor as a sixteen-pixel block; 5/4 is
        two per cent further from the request and looks three times better.
        Since :meth:`snap` pulls the view onto whichever ratio comes back, the
        distance from the request costs nothing but a slightly different zoom,
        while the zoom factor is the block size the eye actually sees.
        """
        wanted = screen_px_per_image_px
        if wanted <= 0:
            return None

        # A factor that is already reachable maps to itself.  Without this the
        # search can answer a ratio it would not answer again: 6/11 comes back
        # for a request near it, but asked for 6/11 exactly the ascending scan
        # meets 5/9 first, one block crisper and still inside tolerance.  Since
        # snap() feeds its own output back in on every repaint and every toggle
        # of the imagery, that difference would walk the zoom along a step at a
        # time instead of holding still.
        for zoom in range(1, self.MAX_ZOOM + 1):
            subsample = int(round(zoom / wanted))
            if not 1 <= subsample <= self.MAX_SUBSAMPLE:
                continue
            if abs(zoom / float(subsample) - wanted) <= wanted * 1e-12:
                return (zoom, subsample)

        best, best_error = None, None
        for zoom in range(1, self.MAX_ZOOM + 1):
            subsample = int(round(zoom / wanted))
            if subsample < 1 or subsample > self.MAX_SUBSAMPLE:
                continue
            error = abs(zoom / float(subsample) - wanted) / wanted
            if error <= self.RATIO_TOLERANCE:
                return (zoom, subsample)
            if best_error is None or error < best_error:
                best, best_error = (zoom, subsample), error
        if best is None:
            # More magnification than MAX_ZOOM allows; give it everything.
            return (self.MAX_ZOOM, 1)
        return best

    def snap(self, scale: float, pixel_per_meter: float) -> float:
        """The nearest view scale this image can be drawn at exactly.

        The view's ``scale`` multiplies world pixels, of which there are
        ``pixel_per_meter`` to the metre, so one image pixel covers
        ``metres_per_pixel * pixel_per_meter * scale`` screen pixels.  Only
        whole-number ratios of that are reachable.
        """
        per_image_px = self.metres_per_pixel * pixel_per_meter
        if per_image_px <= 0:
            return scale
        ratio = self._ratio(per_image_px * scale)
        if ratio is None:
            return scale
        zoom, subsample = ratio
        return (zoom / float(subsample)) / per_image_px

    def scaled_region(self, left_screen, top_screen, width, height,
                      scale, pixel_per_meter):
        """Crop the visible part of the imagery and scale it to the screen.

        Cropping first is what makes this affordable.  Scaling the whole image
        up would need an intermediate of hundreds of megabytes; scaling only
        what is on screen bounds the work by the size of the window however far
        in the view is zoomed.

        Returns ``(photo, x, y)`` with the destination corner in canvas
        coordinates, or ``None`` when nothing of the imagery is in view.
        """
        import tkinter as tk

        factor = self.metres_per_pixel * pixel_per_meter * scale
        ratio = self._ratio(factor)
        if ratio is None:
            return None
        zoom, subsample = ratio
        factor = zoom / float(subsample)

        # Visible window expressed in source pixels.  The crop is snapped
        # outwards to whole subsample blocks, because Tk starts its sampling
        # at the crop's own origin: a crop beginning mid-block would shift the
        # sampled grid and make the imagery creep as the view is panned.
        block = subsample
        x0 = int(math.floor((-left_screen) / factor / block)) * block
        y0 = int(math.floor((-top_screen) / factor / block)) * block
        x1 = int(math.ceil((width - left_screen) / factor / block)) * block
        y1 = int(math.ceil((height - top_screen) / factor / block)) * block
        # Clamping to the image keeps the block alignment, because 0 and the
        # last whole block are both multiples of it.
        last_x = (self.width_px // block) * block
        last_y = (self.height_px // block) * block
        x0 = max(0, min(last_x, x0))
        y0 = max(0, min(last_y, y0))
        x1 = max(0, min(last_x, x1))
        y1 = max(0, min(last_y, y1))
        if x1 - x0 < block or y1 - y0 < block:
            return None

        key = (x0, y0, x1, y1, zoom, subsample)
        if key != self._cache_key:
            source = self.source_image()
            out_w = (x1 - x0) // subsample * zoom
            out_h = (y1 - y0) // subsample * zoom
            if out_w <= 0 or out_h <= 0:
                return None
            target = tk.PhotoImage(width=out_w, height=out_h)
            # Tk subsamples before it zooms, and writes straight into the
            # destination, so no full-size intermediate is ever built.
            target.tk.call(target, "copy", source,
                           "-from", x0, y0, x1, y1,
                           "-to", 0, 0,
                           "-subsample", subsample, subsample,
                           "-zoom", zoom, zoom)
            self._cache_key = key
            self._cache_image = target
        return (self._cache_image,
                left_screen + x0 * factor,
                top_screen + y0 * factor)

    def png_bytes(self, target_width: int):
        """The imagery as PNG bytes, shrunk to roughly *target_width* across.

        For embedding in the report, which is one self-contained file: at full
        resolution a junction's imagery is several megabytes before base64
        expands it by a third, and the report draws it at about a thousand
        pixels wide.  Shrinking first is the difference between a report that
        opens and one that does not.

        Whole-number subsampling again, this being Tk, but here the exact
        factor does not matter: the ``<image>`` element is given an explicit
        width and height, so SVG scales whatever it is handed to fit.
        """
        import base64
        import tempfile
        import tkinter as tk

        step = max(1, int(round(self.width_px / max(target_width, 1))))
        root = tk.Tk()
        root.withdraw()
        handle = None
        try:
            source = tk.PhotoImage(file=self.path, master=root)
            small = source.subsample(step, step) if step > 1 else source
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            handle.close()
            small.write(handle.name, format="png")
            with open(handle.name, "rb") as reader:
                data = reader.read()
        finally:
            root.destroy()
            if handle is not None:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
        return base64.b64encode(data).decode("ascii")

    def forget(self) -> None:
        """Drop the loaded imagery.  Called when a view is torn down."""
        self._source = None
        self._cache_key = None
        self._cache_image = None


def load_for_current_network(link_list, node_list):
    """Convenience wrapper reading the network the run was configured with."""
    try:
        return BaseMap.load(Parameters.NETWORK_DIR, link_list, node_list)
    except Exception as exc:      # imagery must never stop a run
        print(f"basemap unavailable: {exc}")
        return None
