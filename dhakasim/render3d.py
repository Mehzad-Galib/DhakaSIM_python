"""A VISSIM-style 3D perspective view of the animation.

The GUI already draws through a small graphics surface -- :class:`CanvasGraphics`
in :mod:`dhakasim.gui` for the live window, ``_SVGGraphics`` in
:mod:`dhakasim.visualize` for the report.  :class:`Scene3D` is a third one.  It
accepts exactly the same calls, so *nothing in the simulation changes*:
``Vehicle.draw_vehicle``, ``RoadsideObject.draw_object``,
``Pedestrian.draw_mobile_pedestrian`` and ``road_geometry.paint`` all run
unmodified, and the picture is guaranteed to show the same vehicles in the same
places as the 2D view because it is built from the same four corner points.

What the surface does with those corners is the whole idea.  A road polygon is
laid flat on the ground plane; a vehicle's four corners are instead read as a
frame -- ``corner0`` is the rear-left, ``corner0->corner1`` runs along the body,
``corner0->corner3`` runs across it -- and a per-type 3D model is stood up on it.
A bus becomes a bus, a rickshaw gets its hood and its driver, a truck gets a cab
and a cargo box.

Everything is drawn with ``tkinter``'s own polygons, so the view needs no
third-party package, no OpenGL and no GPU, in keeping with the rest of the
simulator.  Three things make that fast enough for a live animation:

* **Convexity.**  Each model part is a box, and the visible faces of a convex
  solid never overlap one another on screen, so faces within a part need no
  sorting.  Parts are declared bottom-up, which is already the right paint
  order for any camera above the road, so a vehicle needs no sorting inside
  itself either.  Only whole vehicles are depth-sorted against each other.
* **Back-face culling.**  Of a box's six faces at most three ever face the
  camera; the rest are dropped before they reach the canvas.
* **Level of detail and culling.**  A vehicle whose body projects to a few
  pixels is drawn as a single block, and one that lands off-screen is skipped
  entirely.

The ground is painted immediately as it arrives, because
``road_geometry.paint`` already emits it in the order it wants (surfaces, kerbs,
junction patches, islands) and it is all coplanar.  Only the raised geometry is
buffered until :meth:`Scene3D.flush`.
"""

from __future__ import annotations

import math

from .constants import Constants
from .parameters import Parameters
from . import utilities as Utilities

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

#: Direction the light comes *from*, in world axes (x east, y south, z up).
#: Late-afternoon sun over the shoulder of the default camera, which is what
#: makes the near side of a body read brighter than the far side.
_SUN = (-0.34, -0.52, 0.78)
_SUN_LEN = math.sqrt(sum(c * c for c in _SUN))
_SUN = tuple(c / _SUN_LEN for c in _SUN)

#: How much light a face gets with none of the sun on it.  Pure Lambert goes
#: black in the shade and looks like a hole; this keeps unlit sides readable.
_AMBIENT = 0.46

SKY_COLOR = "#dae7f2"
GROUND_COLOR = "#e6e3da"          # the ground plane the network sits on
SHADOW_COLOR = "#9aa0a8"          # roughly the road fill at 72% brightness
HORIZON_COLOR = "#c3ccd6"

#: The sky and the ground are graded, not flat: a handful of horizontal bands
#: from a blue zenith down to a pale warm horizon, and from a hazed far ground
#: up to the true ground colour underfoot.  Tk has no gradients, but the bands
#: are screen-space rectangles in the *static* layer, so they cost a dozen
#: canvas items once per camera move rather than anything per frame.
SKY_TOP_RGB = (164, 196, 227)
SKY_HORIZON_RGB = (236, 242, 246)
GROUND_RGB = (230, 227, 218)      # GROUND_COLOR, as numbers the bands can mix
GROUND_FAR_RGB = (216, 220, 222)  # the ground just under the horizon, hazed

#: Aerial perspective.  Everything solid fades towards this colour with
#: distance -- the single strongest depth cue a picture this simple can give,
#: and it declutters the far half of a big network for free, because a fully
#: saturated bus half a kilometre away no longer shouts as loudly as one by
#: the camera.  The colour sits between the far ground and the horizon sky so
#: hazed bodies sink into both.
HAZE_RGB = (222, 227, 231)
#: How much haze a body at the camera's own orbit distance gets (none), and
#: the most any body may get -- full haze would erase it entirely.
HAZE_MAX = 0.52

#: The sun is warm and the shade is cool.  Faces are tinted as well as
#: brightened: full sun pulls the colour a little towards amber, full shade a
#: little towards blue.  Small on any one face, but it is what separates two
#: faces of the same body far better than brightness alone, and it is the
#: whole difference between "shaded" and "lit".
_WARM_TINT = (1.05, 1.00, 0.93)
_COOL_TINT = (0.94, 0.98, 1.05)

#: Line-art style.  The face a part shows the camera is not painted in its own
#: shaded colour; the part is drawn once as its outline over a pale wash of
#: the same colour.  The wash is not decoration -- it is what stops the drawing
#: turning into a thicket the moment two vehicles overlap, because an unfilled
#: outline hides nothing behind it -- and keeping the vehicle's own hue in it
#: is what lets a bus still read as purple and a CNG as green.
#:
#: The outline is darkened to a *luminance ceiling* rather than by a fixed
#: factor.  Simply multiplying leaves a white truck or a silver car drawn in
#: near-white on a near-white road, where it disappears; capping how bright
#: the line may be guarantees every type holds against the road whatever its
#: body colour.
LINE_LUMA_CEILING = 118.0   # 0-255, the brightest an outline may be
LINE_OUTLINE = 0.55         # and never brighter than this fraction of the body
LINE_FILL_BLEND = 0.84      # how far the wash is carried towards white
LINE_SHADOW = "#c3c8cf"

_line_cache: dict = {}


def _line_colours(rgb, haze=0):
    """``(fill, outline)`` for one part in line style, cached like _shaded.

    ``haze`` is a quantised haze step (0..16); a distant part's outline fades
    towards :data:`HAZE_RGB`, which is the line-art reading of aerial
    perspective -- far vehicles thin out instead of staying full-ink thickets.
    """
    key = (rgb, haze)
    hexed = _line_cache.get(key)
    if hexed is None:
        h = haze / 16.0
        luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        factor = min(LINE_OUTLINE, LINE_LUMA_CEILING / max(luma, 1.0))
        outline = "#%02x%02x%02x" % tuple(
            int(_clamp(component * factor * (1.0 - h) + HAZE_RGB[i] * h,
                       0, 255))
            for i, component in enumerate(rgb))
        blend = LINE_FILL_BLEND
        fill = "#%02x%02x%02x" % tuple(
            int(_clamp(component + (255 - component) * blend, 0, 255))
            for component in rgb)
        hexed = (fill, outline)
        _line_cache[key] = hexed
    return hexed

GLASS = (58, 76, 92)
TYRE = (34, 34, 38)
SKIN = (198, 152, 112)
CLOTH = (58, 68, 110)
CANOPY = (38, 38, 42)
CARGO = (152, 122, 82)
HELMET = (228, 228, 232)


def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


_shade_cache: dict = {}


def _shaded(rgb, light):
    """``rgb`` at brightness ``light``, as a ``#rrggbb`` string.

    Called once per visible face, so the results are cached on a quantised
    brightness -- 64 steps is far finer than the eye resolves and turns almost
    every call into a dict hit.
    """
    key = (rgb, int(light * 64))
    hexed = _shade_cache.get(key)
    if hexed is None:
        f = key[1] / 64.0
        hexed = "#%02x%02x%02x" % (
            int(_clamp(rgb[0] * f, 0, 255)),
            int(_clamp(rgb[1] * f, 0, 255)),
            int(_clamp(rgb[2] * f, 0, 255)))
        _shade_cache[key] = hexed
    return hexed


_lit_cache: dict = {}


def _lit(rgb, light, haze=0):
    """A model face's colour: brightness, sun tint and aerial haze together.

    Separate from :func:`_shaded` on purpose.  Roads, kerbs and markings keep
    the exact 2D palette so the two views stay recognisably the same picture;
    the lighting model applies only to the solid bodies stood on top of it.
    ``haze`` is quantised to sixteenths and ``light`` to 64 steps, so the
    cache stays small and almost every call is a dict hit.
    """
    key = (rgb, int(light * 64), haze)
    hexed = _lit_cache.get(key)
    if hexed is None:
        f = key[1] / 64.0
        # 0 in full shade, 1 in full sun -- how far to lean warm over cool.
        sun = _clamp((f - _AMBIENT) / (1.0 - _AMBIENT), 0.0, 1.0)
        h = haze / 16.0
        channels = []
        for i in range(3):
            tint = _COOL_TINT[i] + (_WARM_TINT[i] - _COOL_TINT[i]) * sun
            value = rgb[i] * f * tint
            channels.append(int(_clamp(
                value * (1.0 - h) + HAZE_RGB[i] * h, 0, 255)))
        hexed = "#%02x%02x%02x" % tuple(channels)
        _lit_cache[key] = hexed
    return hexed


_SHADOW_RGB = (154, 160, 168)     # SHADOW_COLOR, as numbers haze can reach
_shadow_cache: dict = {}


def _shadow_colour(haze=0):
    """The drop shadow, faded into the ground with distance like its owner."""
    hexed = _shadow_cache.get(haze)
    if hexed is None:
        h = haze / 16.0
        hexed = "#%02x%02x%02x" % tuple(
            int(_clamp(_SHADOW_RGB[i] * (1.0 - h) + GROUND_FAR_RGB[i] * h,
                       0, 255))
            for i in range(3))
        _shadow_cache[haze] = hexed
    return hexed


# --------------------------------------------------------------------------
# vehicle models
# --------------------------------------------------------------------------
#
# A model is a list of boxes given in the vehicle's own frame:
#
#     u   0 = rear, 1 = front       as a fraction of the body length
#     v   0 = left, 1 = right       as a fraction of the body width
#     z   metres above the road
#
# so a model is written once and fits every size of that type automatically --
# the two bus types differ by 0.8 m of length and the same model covers both.
# Values slightly outside 0..1 are deliberate: that is how a tyre stands proud
# of the bodywork and a windscreen sits in front of the cab.
#
# The last two entries are colour specs, for the four sides and for the top.  A
# float is a brightness multiplier on the vehicle's own colour, so a bus stays
# purple and a CNG stays Dhaka-green and the view still agrees with the 2D
# picture and the report legend.  An explicit ``(r, g, b)`` paints the things
# that are not body-coloured: glass, tyres, a rickshaw hood, a load of cargo.
#
# Parts are listed bottom-up.  That is the order they are painted in, and for
# any camera above the road it is already the correct order.

def _axle(u0, u1, top, tread=0.16, proud=0.02):
    """A pair of wheels across the body, between ``u0`` and ``u1``."""
    return [(u0, u1, -proud, tread, 0.0, top, TYRE, TYRE),
            (u0, u1, 1.0 - tread, 1.0 + proud, 0.0, top, TYRE, TYRE)]


_CAR = [
    *_axle(0.10, 0.26, 0.34),
    *_axle(0.70, 0.86, 0.34),
    (0.00, 1.00, 0.02, 0.98, 0.26, 0.84, 1.00, 1.00),   # body
    (0.22, 0.68, 0.06, 0.94, 0.84, 1.38, GLASS, 0.90),  # greenhouse
]

_BUS = [
    *_axle(0.06, 0.21, 0.52, tread=0.14),
    *_axle(0.62, 0.77, 0.52, tread=0.14),
    (0.00, 1.00, 0.00, 1.00, 0.44, 3.05, 1.00, 0.92),        # shell
    (0.02, 0.98, -0.02, 1.02, 1.55, 2.45, GLASS, GLASS),     # window band
    (0.98, 1.01, 0.04, 0.96, 1.45, 2.60, GLASS, GLASS),      # windscreen
    (0.06, 0.94, 0.06, 0.94, 3.05, 3.22, 0.72, 0.80),        # roof rack
]

_TRUCK = [
    *_axle(0.10, 0.25, 0.56, tread=0.15),
    *_axle(0.27, 0.42, 0.56, tread=0.15),
    *_axle(0.70, 0.85, 0.56, tread=0.15),
    (0.00, 1.00, 0.10, 0.90, 0.46, 0.96, 0.55, 0.55),        # chassis
    (0.00, 0.66, 0.00, 1.00, 0.96, 2.95, 0.80, 0.66),        # cargo body
    (0.68, 1.00, 0.02, 0.98, 0.96, 2.45, 1.00, 0.92),        # cab
    (0.99, 1.02, 0.08, 0.92, 1.55, 2.32, GLASS, GLASS),      # windscreen
]

_CNG = [
    (0.80, 0.94, 0.42, 0.58, 0.0, 0.36, TYRE, TYRE),         # single front wheel
    *_axle(0.10, 0.27, 0.36, tread=0.18),
    (0.00, 0.98, 0.04, 0.96, 0.22, 0.92, 1.00, 1.00),        # tub
    (0.02, 0.72, 0.00, 1.00, 0.92, 1.82, 1.00, 0.34),        # canopy, dark roof
    (0.70, 0.88, 0.30, 0.70, 0.92, 1.62, GLASS, GLASS),      # driver bay
]

_RICKSHAW = [
    (0.78, 0.94, 0.44, 0.56, 0.0, 0.44, TYRE, TYRE),
    *_axle(0.06, 0.27, 0.52, tread=0.14),
    (0.02, 0.58, 0.06, 0.94, 0.34, 0.92, 1.00, 1.00),        # passenger seat
    (0.00, 0.46, 0.02, 0.98, 0.92, 1.64, CANOPY, CANOPY),    # hood
    (0.60, 0.76, 0.34, 0.66, 0.46, 1.14, CLOTH, CLOTH),      # driver
    (0.62, 0.74, 0.36, 0.64, 1.14, 1.52, CLOTH, CLOTH),
    (0.63, 0.73, 0.38, 0.62, 1.52, 1.70, SKIN, SKIN),
]

_MOTORBIKE = [
    (0.04, 0.24, 0.32, 0.68, 0.0, 0.56, TYRE, TYRE),
    (0.76, 0.96, 0.32, 0.68, 0.0, 0.56, TYRE, TYRE),
    (0.18, 0.82, 0.16, 0.84, 0.34, 0.80, 1.00, 1.00),        # tank / frame
    (0.62, 0.98, 0.10, 0.90, 0.72, 1.04, 1.00, 0.90),        # fairing
    (0.30, 0.62, 0.02, 0.98, 0.80, 1.44, CLOTH, CLOTH),      # rider
    (0.34, 0.58, 0.10, 0.90, 1.44, 1.68, HELMET, HELMET),
]

_BICYCLE = [
    (0.04, 0.22, 0.36, 0.64, 0.0, 0.62, TYRE, TYRE),
    (0.78, 0.96, 0.36, 0.64, 0.0, 0.62, TYRE, TYRE),
    (0.20, 0.80, 0.34, 0.66, 0.40, 0.74, 1.00, 1.00),
    (0.28, 0.58, 0.00, 1.00, 0.74, 1.38, CLOTH, CLOTH),      # rider
    (0.32, 0.54, 0.08, 0.92, 1.38, 1.62, SKIN, SKIN),
]

_CART = [
    *_axle(0.10, 0.31, 0.42, tread=0.16),
    (0.66, 0.82, 0.44, 0.56, 0.0, 0.40, TYRE, TYRE),
    (0.00, 0.80, 0.00, 1.00, 0.40, 0.62, 1.00, 1.00),        # flat bed
    (0.04, 0.72, 0.06, 0.94, 0.62, 1.18, CARGO, CARGO),      # load
    (0.82, 0.96, 0.32, 0.68, 0.42, 1.18, CLOTH, CLOTH),      # puller
    (0.83, 0.95, 0.34, 0.66, 1.18, 1.56, CLOTH, CLOTH),
    (0.84, 0.94, 0.36, 0.64, 1.56, 1.74, SKIN, SKIN),
]

_PERSON = [
    (0.18, 0.82, 0.18, 0.82, 0.00, 0.86, CLOTH, CLOTH),      # legs
    (0.02, 0.98, 0.02, 0.98, 0.86, 1.48, 1.00, 1.00),        # torso
    (0.22, 0.78, 0.22, 0.78, 1.48, 1.70, SKIN, SKIN),        # head
]

#: One model per vehicle type index; the indices are the ones used everywhere
#: else in the simulator (see ``report.TYPE_NAMES``).
VEHICLE_MODELS = (
    _BICYCLE,    # 0  bicycle
    _RICKSHAW,   # 1  rickshaw
    _CART,       # 2  van / cart
    _MOTORBIKE,  # 3  motorbike
    _CAR,        # 4  car
    _CAR,        # 5  car
    _CAR,        # 6  car
    _CNG,        # 7  CNG / auto
    _BUS,        # 8  bus
    _BUS,        # 9  bus
    _TRUCK,      # 10 truck
    _TRUCK,      # 11 truck
    _PERSON,     # 12 pedestrian along the road
)

#: Roadside objects, keyed by ``RoadsideObject.object_type``.
OBJECT_MODELS = {
    1: _PERSON,     # standing pedestrian
    2: _CAR,        # parked car
    3: _RICKSHAW,   # parked rickshaw
    4: _CNG,        # parked CNG
}

#: Every model there is.  The two tables below are keyed by ``id()``, which is
#: safe precisely because this tuple holds a reference to each one for the life
#: of the process, so no model's identity can be reused.
_ALL_MODELS = (_CAR, _BUS, _TRUCK, _CNG, _RICKSHAW, _MOTORBIKE, _BICYCLE,
               _CART, _PERSON)

#: Tallest point of each model, in metres -- how far its shadow is thrown, and
#: how much room it needs above the road when the culler asks.
_MODEL_HEIGHT = {id(m): max(part[5] for part in m) for m in _ALL_MODELS}

#: Stand-in for a vehicle only a few pixels long: one block roughly the height
#: of the real model, which at that size is all the eye can resolve anyway.
_LOD_BLOCK = {id(m): [(0.0, 1.0, 0.0, 1.0, 0.0, _MODEL_HEIGHT[id(m)] * 0.72,
                       1.0, 1.0)]
              for m in _ALL_MODELS}


def model_for_size(length_m, width_m):
    """The model whose real-world type is closest to ``length_m`` x ``width_m``.

    Used when the caller cannot say which type it is drawing -- trace replay
    records only the four corners and a colour -- so a replayed run still gets
    buses that look like buses.
    """
    best, best_cost = _CAR, None
    for type_index in range(len(VEHICLE_MODELS)):
        dl = Utilities.get_car_length(type_index) - length_m
        dw = Utilities.get_car_width(type_index) - width_m
        cost = dl * dl + 4.0 * dw * dw   # width separates the types more sharply
        if best_cost is None or cost < best_cost:
            best, best_cost = VEHICLE_MODELS[type_index], cost
    return best


# --------------------------------------------------------------------------
# camera
# --------------------------------------------------------------------------

class Camera:
    """An orbit camera: it looks at a point on the road from a given bearing,
    elevation and distance, which is the set of controls a VISSIM-style 3D view
    offers and the only set that is hard to get lost with.

    Distances are in the simulator's own pixel space (metres times
    ``pixel_per_meter``), the space every drawing call already arrives in.
    """

    MIN_PITCH = math.radians(4.0)
    MAX_PITCH = math.radians(89.0)

    def __init__(self):
        self.target = [0.0, 0.0]            # the ground point being looked at
        self.distance = 1200.0
        self.yaw = 0.0                      # 0 looks north, up the 2D map
        self.pitch = math.radians(34.0)
        self.fov = math.radians(52.0)
        self._home = None

    # ---- framing ---------------------------------------------------------

    def frame(self, x0, y0, x1, y1) -> None:
        """Look at the whole of the given box, and remember it as home."""
        self.target = [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
        span = max(x1 - x0, y1 - y0, 1.0)
        self.distance = span * 1.15
        self.yaw = 0.0
        self.pitch = math.radians(34.0)
        self._home = (list(self.target), self.distance, self.yaw, self.pitch)

    @property
    def home_distance(self) -> float:
        """The distance :meth:`frame` chose -- the unit an outside zoom control
        works in, so its handle means the same thing on every network."""
        return self._home[1] if self._home else self.distance

    def reset(self) -> None:
        if self._home is None:
            return
        target, distance, yaw, pitch = self._home
        self.target = list(target)
        self.distance = distance
        self.yaw = yaw
        self.pitch = pitch

    # ---- controls --------------------------------------------------------

    def orbit(self, dx, dy) -> None:
        self.yaw = (self.yaw + dx * 0.007) % (2 * math.pi)
        self.pitch = _clamp(self.pitch + dy * 0.005, self.MIN_PITCH, self.MAX_PITCH)

    def pan(self, dx, dy, focal) -> None:
        """Drag the ground under the cursor.  ``focal`` is the projection's
        focal length in pixels, which is what turns a screen displacement into
        the world displacement that matches it at the target's depth."""
        k = self.distance / max(focal, 1.0)
        right = (math.cos(self.yaw), math.sin(self.yaw))
        # the camera's forward direction flattened onto the ground plane
        ahead = (math.sin(self.yaw), -math.cos(self.yaw))
        self.target[0] += (-dx * right[0] + dy * ahead[0]) * k
        self.target[1] += (-dx * right[1] + dy * ahead[1]) * k

    def zoom(self, notches) -> None:
        self.distance = _clamp(self.distance * math.exp(notches * 0.16),
                               20.0, 400000.0)

    # ---- basis -----------------------------------------------------------

    def basis(self):
        """``(eye, forward, right, up)`` for the current settings."""
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        forward = (sy * cp, -cy * cp, -sp)
        right = (cy, sy, 0.0)
        # up = forward x right, which keeps the horizon level
        up = (forward[1] * right[2] - forward[2] * right[1],
              forward[2] * right[0] - forward[0] * right[2],
              forward[0] * right[1] - forward[1] * right[0])
        eye = (self.target[0] - forward[0] * self.distance,
               self.target[1] - forward[1] * self.distance,
               -forward[2] * self.distance)
        return eye, forward, right, up


# --------------------------------------------------------------------------
# the drawing surface
# --------------------------------------------------------------------------

class Scene3D:
    """A drawing surface that renders the simulator's own draw calls in 3D.

    Accepts the same calls as ``CanvasGraphics``.  A caller that knows what it
    is drawing wraps the call in :meth:`begin_prop` / :meth:`end_prop`; anything
    drawn outside such a pair is treated as road markings and laid flat.
    """

    NEAR = 1.0          # near clip plane, in pixel-space units
    #: A body shorter than this on screen is not worth its detail parts: below
    #: it a cabin is a pixel or two and the whole model reads as one block
    #: anyway, so it is drawn as one.
    LOD_PIXELS = 13.0

    #: Shortest a *lone* line may project to and still be drawn, in pixels.
    #: Aimed squarely at the lane markings.  Painting a network's dividers
    #: costs 979 canvas items at Khamarbari against 204 for every kerb in the
    #: network put together, and the median dash lands three pixels long -- so
    #: the majority of a 3D frame goes on marks the eye cannot resolve.  Lines
    #: that chain into a kerb are never dropped however short they are, only
    #: ones that stand alone, so an outline can never come out gapped.
    MIN_LINE_PIXELS = 3.0

    #: A model part whose largest dimension projects smaller than this is
    #: skipped.  It sits below :data:`LOD_PIXELS`' whole-body cut: between the
    #: two, a mid-distance car keeps its cabin but loses its wheels, which at
    #: two pixels were costing a box each while reading as noise on the tyre
    #: line of the road.
    PART_MIN_PIXELS = 2.5

    #: A prop whose footprint projects smaller than this is drawn as a single
    #: flat quad -- a speck.  At a whole-network framing *most* props are
    #: specks (a 4 m car on a 2 km network lands under a pixel), and they must
    #: not be dropped: several hundred dots are exactly how a wide view shows
    #: where the traffic is.  What they must not cost is a box each -- one
    #: canvas item marks the spot as well as three faces and a shadow do.
    SPECK_PIXELS = 6.0

    #: Canvas tags for the static-layer economy.  The report has always
    #: rendered sky, ground and roads once and repainted only the vehicles
    #: (``RunRecorder._capture_3d``); these tags let the live window do the
    #: same.  Everything the scene creates while the frame is static carries
    #: STATIC_TAG, everything after :meth:`begin_dynamic` carries DYNAMIC_TAG,
    #: and a caller that passed ``keep_static=True`` to :meth:`begin_frame`
    #: deletes only the dynamic items between frames.  Labels carry LABEL_TAG
    #: as well, so a reused frame can lift the (static) node names back above
    #: the vehicles just created on top of them.
    STATIC_TAG = "scene3d_static"
    DYNAMIC_TAG = "scene3d_dyn"
    LABEL_TAG = "scene3d_label"

    def __init__(self, canvas):
        self.canvas = canvas
        self.camera = Camera()
        self.pixel_per_meter = 1.0
        self.width = 1
        self.height = 1
        self.show_shadows = True
        # "solid" is the original shaded-face look; "line" draws every prop as
        # an outline instead.  Read once, here, so that a scene handed to the
        # report renders in whatever style the run used.
        self.style = getattr(Parameters, "RENDER_3D_STYLE", "solid")
        self._color = (0, 0, 0)
        self._stroke = 1.0
        self._font = ("Serif", 12)
        self._prop = None
        # A kerb arrives as a run of separate draw_line calls, each starting
        # where the last one ended.  Held here until the chain breaks, then
        # drawn as a single polyline: one canvas item for a whole kerb rather
        # than one per point pair.
        self._chain = None     # (colour, width, [x0, y0, x1, y1, ...])
        self._props = []       # (depth, [(screen points, fill, outline), ...], tag)
        self._labels = []      # (depth, x, y, text, colour, anchor, size, tag)
        self._tag = self.STATIC_TAG
        self._kept_static = False
        self._extent = None
        self._focal = 1.0
        self._eye = (0.0, 0.0, 1.0)
        self._fwd = (0.0, -1.0, 0.0)
        self._right = (1.0, 0.0, 0.0)
        self._up = (0.0, 0.0, 1.0)

    def set_output(self, canvas) -> None:
        """Point the scene at a different drawing device.

        The scene only ever asks its canvas for polygons, lines, rectangles and
        text in screen coordinates, so anything answering that much of
        ``tkinter.Canvas`` will do -- which is how the report renders the very
        same 3D scene to SVG instead of to the window.
        """
        self.canvas = canvas

    @property
    def focal(self) -> float:
        """Focal length in pixels for the frame being drawn; a caller turning a
        mouse drag into a world displacement needs it."""
        return self._focal

    # ---- frame lifecycle -------------------------------------------------

    def set_ground_extent(self, x0, y0, x1, y1) -> None:
        """The box the network occupies, in pixel space.  The ground the roads
        are laid on is drawn from it, so the network reads as sitting on
        something rather than floating in the sky."""
        self._extent = (x0, y0, x1, y1)

    def begin_frame(self, width, height, pixel_per_meter,
                    keep_static=False) -> None:
        """Start a frame.  With ``keep_static=True`` the caller is reusing the
        static layer from the previous frame -- same camera, same window, same
        roads -- so the sky and ground are not repainted and everything drawn
        from here on is tagged dynamic for the caller to delete next frame."""
        self.width = max(1, width)
        self.height = max(1, height)
        self.pixel_per_meter = pixel_per_meter or 1.0
        self._focal = (self.height / 2.0) / math.tan(self.camera.fov / 2.0)
        self._eye, self._fwd, self._right, self._up = self.camera.basis()
        self._props = []
        self._labels = []
        self._prop = None
        self._chain = None
        self._kept_static = keep_static
        self._tag = self.DYNAMIC_TAG if keep_static else self.STATIC_TAG
        if keep_static:
            return

        canvas = self.canvas
        canvas.configure(bg=SKY_COLOR)

        # The ground is everything below the horizon, filled in screen space.
        #
        # The obvious alternative -- project a very large quad lying on z = 0 --
        # does not survive contact with perspective: the quad has to reach past
        # the horizon to have its edge out of shot, near-plane clipping then
        # throws its corners millions of pixels off screen, and the canvas
        # cannot hold coordinates that big, so the fill tears open along a
        # diagonal.  A ground plane has no roll here, so its horizon is exactly
        # a horizontal line, and one rectangle gives the same picture with no
        # arithmetic to go wrong.
        #
        # Both halves are graded in horizontal bands -- deep sky at the top,
        # pale at the horizon; hazed ground under the horizon, true ground
        # colour underfoot.  The bands live in the static layer, so their cost
        # is per camera move, not per frame.
        horizon = self.height / 2.0 - self._focal * math.tan(self.camera.pitch)

        def band(y0, y1, colour):
            canvas.create_rectangle(0, y0, self.width, y1,
                                    fill="#%02x%02x%02x" % colour, outline="",
                                    tags=self._tag)

        def mix(a, b, t):
            return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

        if horizon > 0:
            # Squared interpolation keeps the pale part pinned to the horizon
            # where the eye expects it, instead of washing the whole sky out.
            # Sixteen bands is where the steps stop being visible at typical
            # window heights; they are static items, so the count is cheap.
            top = min(horizon, self.height)
            steps = 16
            for i in range(steps):
                t0, t1 = i / steps, (i + 1) / steps
                band(top * t0, top * t1,
                     mix(SKY_TOP_RGB, SKY_HORIZON_RGB, t1 * t1))
        if horizon < self.height:
            start = max(horizon, 0)
            # The haze hugs the horizon: it fades out over the first quarter
            # of the visible ground and the rest is one plain rectangle.
            reach = min(self.height, start + (self.height - start) * 0.28)
            steps = 6
            for i in range(steps):
                t0, t1 = i / steps, (i + 1) / steps
                band(start + (reach - start) * t0,
                     start + (reach - start) * t1,
                     mix(GROUND_FAR_RGB, GROUND_RGB, t0))
            band(reach, self.height, GROUND_RGB)
            if 0 < horizon < self.height:
                canvas.create_line(0, horizon, self.width, horizon,
                                   fill=HORIZON_COLOR, tags=self._tag)

    def begin_dynamic(self) -> None:
        """Declare the static half of the frame finished.

        Everything drawn before this call -- sky, ground, roads, markings,
        node names -- is tagged static; everything after it is tagged dynamic.
        A caller that keeps the static layer across frames deletes only the
        dynamic tag.  The chain buffer is flushed first, so a kerb run still
        being collected cannot leak into the dynamic layer and be deleted
        with the vehicles next frame.
        """
        self._flush_chain()
        self._tag = self.DYNAMIC_TAG

    def flush(self) -> None:
        """Paint the raised geometry, farthest vehicle first."""
        self._flush_chain()
        canvas = self.canvas
        self._props.sort(key=lambda item: -item[0])
        for _depth, faces, tag in self._props:
            for points, fill, outline in faces:
                canvas.create_polygon(points, fill=fill, outline=outline,
                                      tags=tag)
        for _depth, x, y, text, colour, anchor, size, tag in sorted(
                self._labels, key=lambda item: -item[0]):
            font = ("Segoe UI", size, "bold")
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                canvas.create_text(x + ox, y + oy, text=text, anchor=anchor,
                                   fill="#ffffff", font=font,
                                   tags=(tag, self.LABEL_TAG))
            canvas.create_text(x, y, text=text, anchor=anchor, fill=colour,
                               font=font, tags=(tag, self.LABEL_TAG))
        if self._kept_static:
            # The node names live in the static layer, but the vehicles were
            # just created on top of them; lift them back.  Only reached on a
            # live canvas -- the report and the tests always run full frames.
            canvas.tag_raise(self.LABEL_TAG)

    # ---- projection ------------------------------------------------------

    def _view(self, x, y, z):
        ex, ey, ez = self._eye
        dx, dy, dz = x - ex, y - ey, z - ez
        rx, ry, rz = self._right
        ux, uy, uz = self._up
        fx, fy, fz = self._fwd
        return (dx * rx + dy * ry + dz * rz,
                dx * ux + dy * uy + dz * uz,
                dx * fx + dy * fy + dz * fz)

    #: Projected coordinates are clamped to this many pixels either side of the
    #: frame.  A vertex just past the near plane projects arbitrarily far out,
    #: and a canvas cannot hold a coordinate of a few million -- it wraps, and
    #: the polygon tears.  Clamping this far out is invisible on screen and
    #: keeps every shape well formed.
    COORD_LIMIT = 20000.0

    def _screen(self, view):
        s = self._focal / view[2]
        return (_clamp(self.width / 2.0 + view[0] * s,
                       -self.COORD_LIMIT, self.width + self.COORD_LIMIT),
                _clamp(self.height / 2.0 - view[1] * s,
                       -self.COORD_LIMIT, self.height + self.COORD_LIMIT))

    def _project(self, points):
        """Project a world-space polygon, clipping it against the near plane.

        Returns a flat ``[x0, y0, x1, y1, ...]`` list ready for
        ``create_polygon``, or ``[]`` if the polygon is entirely behind the
        camera.  Clipping rather than dropping matters as soon as the camera
        drops near the road: a road quad reaching past the lens has to be cut,
        not discarded, or the surface tears open under the viewer.
        """
        return self._project_views([self._view(*p) for p in points])

    def _project_views(self, view):
        """The second half of :meth:`_project`, from view space on.

        Split out so a box can transform its eight corners once and project
        each of its faces from the shared result -- every corner sits on
        three faces, so projecting per face transforms half as much again
        for nothing.
        """
        if all(v[2] >= self.NEAR for v in view):
            out = []
            for v in view:
                sx, sy = self._screen(v)
                out.append(sx)
                out.append(sy)
            return out
        if all(v[2] < self.NEAR for v in view):
            return []
        clipped = []
        n = len(view)
        for i in range(n):
            a = view[i]
            b = view[(i + 1) % n]
            a_in = a[2] >= self.NEAR
            b_in = b[2] >= self.NEAR
            if a_in:
                clipped.append(a)
            if a_in != b_in:
                t = (self.NEAR - a[2]) / (b[2] - a[2])
                clipped.append((a[0] + (b[0] - a[0]) * t,
                                a[1] + (b[1] - a[1]) * t,
                                self.NEAR))
        if len(clipped) < 3:
            return []
        out = []
        for v in clipped:
            sx, sy = self._screen(v)
            out.append(sx)
            out.append(sy)
        return out

    # ---- graphics API ----------------------------------------------------

    def set_transform(self, width, height, scale, translate_x, translate_y) -> None:
        # The 2D pan/zoom has no meaning here; the camera replaces it.
        pass

    def set_color(self, color) -> None:
        self._color = (color.get_red(), color.get_green(), color.get_blue())

    def set_alpha(self, fraction) -> None:
        """Ignored: the 3D scene paints solid models, not washes.

        Present because the drawing-surface protocol requires every surface to
        answer every call -- trace replay drives all three through the same
        code path.
        """

    def set_stroke(self, width) -> None:
        self._stroke = width

    def set_font(self, family, size) -> None:
        self._font = (family, size)

    def begin_prop(self, kind, type_index=None) -> None:
        """Declare that what follows is a solid object of ``kind``
        (``"vehicle"``, ``"object"`` or ``"pedestrian"``) rather than road
        surface.  ``type_index`` picks the model; ``None`` means infer it from
        the footprint, which is what trace replay needs."""
        self._prop = (kind, type_index)

    def end_prop(self) -> None:
        self._prop = None

    #: A stroke at least this wide, in pixel space, is treated as paint on the
    #: road rather than as a drawn line.  The kerb outlines come through at
    #: ``KERB_WIDTH_METRES * pixel_per_meter``, well above it; a leader line or
    #: a trajectory comes through at 1 or 10 and stays a line.
    RIBBON_STROKE = 12.0

    def draw_line(self, x1, y1, x2, y2, arrow=False) -> None:
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if not arrow and length > 0.0 and self._stroke >= self.RIBBON_STROKE:
            # A kerb is a strip of road a metre wide, so it has to narrow with
            # distance exactly like the surface it is painted on.  Drawn as a
            # line of constant screen width it does the opposite -- the nearer
            # it gets the wider it gets -- and one kerb by the lens ends up
            # covering the whole frame.
            hx = -dy / length * self._stroke / 2.0
            hy = dx / length * self._stroke / 2.0
            ribbon = self._project([(x1 + hx, y1 + hy, 0.0),
                                    (x2 + hx, y2 + hy, 0.0),
                                    (x2 - hx, y2 - hy, 0.0),
                                    (x1 - hx, y1 - hy, 0.0)])
            if ribbon:
                self._flush_chain()
                self.canvas.create_polygon(ribbon, fill=_shaded(self._color, 1.0),
                                           outline="", tags=self._tag)
            return

        points = self._project([(x1, y1, 0.0), (x2, y2, 0.0)])
        if len(points) < 4:
            return
        depth = self._view((x1 + x2) / 2.0, (y1 + y2) / 2.0, 0.0)[2]
        width = _clamp(self._stroke * self._focal / max(depth, self.NEAR), 1.0, 8.0)
        opts = {}
        if arrow:
            size = max(4.0, min(14.0, width * 3.0))
            opts["arrow"] = "last"
            opts["arrowshape"] = (size * 1.6, size * 2.0, size * 0.7)
        colour = _shaded(self._color, 1.0)
        if arrow:
            self._flush_chain()
            self.canvas.create_line(points[0], points[1], points[2], points[3],
                                    fill=colour, width=width, tags=self._tag,
                                    **opts)
            return

        chain = self._chain
        if (chain is not None and chain[0] == colour
                and abs(chain[1] - width) < 0.51
                and abs(chain[2][-2] - points[0]) < 0.01
                and abs(chain[2][-1] - points[1]) < 0.01):
            chain[2].append(points[2])
            chain[2].append(points[3])
            return
        self._flush_chain()
        self._chain = (colour, width, [points[0], points[1],
                                       points[2], points[3]])

    def _flush_chain(self) -> None:
        """Draw the kerb run being collected, if it is worth drawing.

        Called before anything else reaches the canvas, so that buffering a
        kerb cannot move it above the junction patch that is meant to cover
        it.  Paint order in this renderer is the order the calls arrive in.
        """
        chain = self._chain
        if chain is None:
            return
        self._chain = None
        colour, width, points = chain
        if len(points) < 4:
            return
        if len(points) == 4:
            # A line on its own is road paint, not a kerb: drop it when it is
            # too small to read.  A chain of two or more is structure and is
            # always drawn.
            if math.hypot(points[2] - points[0],
                          points[3] - points[1]) < self.MIN_LINE_PIXELS:
                return
        self.canvas.create_line(*points, fill=colour, width=width,
                                tags=self._tag)

    def fill_polygon(self, xs, ys, n) -> None:
        self._flush_chain()
        if self._prop is not None and n == 4:
            self._fill_prop(xs, ys)
            return
        points = self._project([(xs[i], ys[i], 0.0) for i in range(n)])
        if points:
            self.canvas.create_polygon(points, fill=_shaded(self._color, 1.0),
                                       outline="", tags=self._tag)

    def fill_oval(self, x, y, w, h) -> None:
        self._flush_chain()
        cx, cy = x + w / 2.0, y + h / 2.0
        if self._prop is not None:
            # A pedestrian arrives as a circle; stand a person on its centre.
            size = max(w, h)
            self._stand_model(_PERSON, cx - size / 2.0, cy - size / 2.0,
                              size, 0.0, 0.0, size)
            return
        points = self._project(_disc(cx, cy, w / 2.0, h / 2.0))
        if points:
            self.canvas.create_polygon(points, fill=_shaded(self._color, 1.0),
                                       outline="", tags=self._tag)

    def draw_oval(self, x, y, w, h) -> None:
        self._flush_chain()
        points = self._project(_disc(x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0))
        if len(points) < 6:
            return
        depth = self._view(x + w / 2.0, y + h / 2.0, 0.0)[2]
        width = _clamp(self._stroke * self._focal / max(depth, self.NEAR), 1.0, 8.0)
        self.canvas.create_line(*points, points[0], points[1],
                                fill=_shaded(self._color, 1.0), width=width,
                                tags=self._tag)

    def draw_string(self, text, x, y, anchor="sw") -> None:
        # Node labels are drawn as a white halo behind black text.  In 3D the
        # halo is applied in screen space by flush(), so the caller's white
        # copies are dropped here rather than piling up on the same pixels.
        if self._color == (255, 255, 255):
            return
        view = self._view(x, y, 0.0)
        if view[2] < self.NEAR:
            return
        sx, sy = self._screen(view)
        if not (-200 <= sx <= self.width + 200 and -200 <= sy <= self.height + 200):
            return
        # The font size is quoted in pixel space like everything else, so it
        # shrinks with distance exactly as the road under it does.
        size = int(_clamp(self._font[1] * self._focal / view[2], 8, 34))
        self._labels.append((view[2], sx, sy, text,
                             _shaded(self._color, 1.0), anchor, size,
                             self._tag))

    # ---- solid objects ---------------------------------------------------

    def _fill_prop(self, xs, ys):
        """Stand the current prop's model on the footprint quad.

        The quad arrives as rear-left, front-left, front-right, rear-right --
        the order every ``draw_*`` method in the simulator builds it in -- so
        the body axis and the width axis fall straight out of it and the model
        inherits the vehicle's real position, heading and size.
        """
        ox, oy = xs[0], ys[0]
        fx, fy = xs[1] - ox, ys[1] - oy        # along the body, rear to front
        rx, ry = xs[3] - ox, ys[3] - oy        # across the body, left to right

        kind, type_index = self._prop
        if kind == "object":
            model = OBJECT_MODELS.get(type_index, _CAR)
        elif kind == "pedestrian":
            model = _PERSON
        elif type_index is None:
            ppm = self.pixel_per_meter
            model = model_for_size(math.hypot(fx, fy) / ppm,
                                   math.hypot(rx, ry) / ppm)
        else:
            model = VEHICLE_MODELS[abs(type_index) % len(VEHICLE_MODELS)]
        self._stand_model(model, ox, oy, fx, fy, rx, ry)

    def _stand_model(self, model, ox, oy, fx, fy, rx, ry):
        centre = self._view(ox + (fx + rx) / 2.0, oy + (fy + ry) / 2.0, 0.0)
        depth = centre[2]
        if depth < self.NEAR:
            return

        # Cull off-screen: the margin covers a tall body whose footprint is
        # just outside the frame but whose roof is not.
        scale = self._focal / depth
        cx, cy = self._screen(centre)
        body = math.hypot(fx, fy)
        margin = body * scale + 240.0
        if not (-margin <= cx <= self.width + margin
                and -margin <= cy <= self.height + margin):
            return

        # Aerial haze, quantised to sixteenths for the colour caches.  Zero at
        # the camera's orbit distance -- the depth the framed network sits at
        # -- growing on everything beyond it, so haze reads as distance into
        # the scene whatever the zoom rather than kicking in at a fixed range.
        haze = int(_clamp((depth / max(self.camera.distance, 1.0) - 1.05)
                          * 0.6, 0.0, HAZE_MAX) * 16.0)

        height = _MODEL_HEIGHT.get(id(model), 2.0)
        wide = math.hypot(rx, ry)
        if max(body, wide) * scale < self.SPECK_PIXELS:
            # Too small even for the one-block LOD: one flat quad at the
            # model's roofline, in the body colour, and done.
            top = height * 0.72 * self.pixel_per_meter
            quad = self._project_views([
                self._view(ox, oy, top),
                self._view(ox + fx, oy + fy, top),
                self._view(ox + fx + rx, oy + fy + ry, top),
                self._view(ox + rx, oy + ry, top)])
            if quad:
                if self.style == "line":
                    # Ink, not wash: a three-pixel wash is invisible.
                    colour = _line_colours(self._color, haze)[1]
                else:
                    colour = _lit(self._color, 0.9, haze)
                self._props.append((depth, [(quad, colour, "")], self._tag))
            return

        detailed = body * scale >= self.LOD_PIXELS
        if self.show_shadows and detailed:
            # Thrown away from the sun, by the amount a body of this height
            # casts at the sun's elevation.  One polygon, and it is the single
            # cheapest thing that stops vehicles looking like they hover.
            drop = height * self.pixel_per_meter * 0.55
            sx, sy = -_SUN[0] * drop, -_SUN[1] * drop
            shadow = self._project([
                (ox + sx, oy + sy, 0.0),
                (ox + fx + sx, oy + fy + sy, 0.0),
                (ox + fx + rx + sx, oy + fy + ry + sy, 0.0),
                (ox + rx + sx, oy + ry + sy, 0.0)])
            if shadow:
                # In line style the shadow is an outline too, or a solid grey
                # blob is the heaviest mark on the page and every vehicle
                # looks like it is sitting in a puddle.
                if self.style == "line":
                    self.canvas.create_polygon(shadow, fill="",
                                               outline=LINE_SHADOW,
                                               tags=self._tag)
                else:
                    self.canvas.create_polygon(shadow,
                                               fill=_shadow_colour(haze),
                                               outline="", tags=self._tag)

        if not detailed:
            # Too small to resolve: one block, and no shadow either -- at this
            # size it is a sub-pixel smudge that costs as much as a face.
            model = _LOD_BLOCK.get(id(model), model)

        base = self._color
        ppm = self.pixel_per_meter
        eye = self._eye
        line = self.style == "line"
        # A part's screen size is judged from its fractions of the body
        # length, the body width and the metre unit, all computed above.
        faces = []
        for part in model:
            if detailed and max((part[1] - part[0]) * body,
                                (part[3] - part[2]) * wide,
                                (part[5] - part[4]) * ppm) * scale \
                    < self.PART_MIN_PIXELS:
                # A wheel two pixels long is noise that costs a whole box.
                # Only detail parts can land here: the body spans the model,
                # and a model small enough for its body to go is one block
                # already.
                continue
            _emit_box(faces, self, eye, base, ppm,
                      ox, oy, fx, fy, rx, ry, part, line, haze)
        if faces:
            self._props.append((depth, faces, self._tag))


def _silhouette(points):
    """Outline of a projected box: the convex hull of the points it shows.

    A box is convex, so the hull of the corners on its camera-facing faces is
    exactly its silhouette -- one closed outline in place of three filled
    faces.  Monotone chain, on at most twelve points, which is cheaper than
    the three polygons it replaces are to hand to the canvas.

    Written here rather than borrowed from road_geometry because a drawing
    surface should not need to know about road geometry; the dependency runs
    the other way round.
    """
    pts = sorted(set(points))
    if len(pts) < 3:
        return []
    def half(order):
        chain = []
        for point in order:
            while len(chain) >= 2:
                (ax, ay), (bx, by) = chain[-2], chain[-1]
                if ((bx - ax) * (point[1] - ay)
                        - (by - ay) * (point[0] - ax)) > 0:
                    break
                chain.pop()
            chain.append(point)
        return chain[:-1]
    hull = half(pts) + half(reversed(pts))
    if len(hull) < 3:
        return []
    out = []
    for x, y in hull:
        out.append(x)
        out.append(y)
    return out


def _disc(cx, cy, rx, ry, steps=28):
    """A circle on the ground plane as a polygon, for islands and patches."""
    return [(cx + rx * math.cos(2 * math.pi * i / steps),
             cy + ry * math.sin(2 * math.pi * i / steps), 0.0)
            for i in range(steps)]


def _emit_box(faces, scene, eye, base, ppm, ox, oy, fx, fy, rx, ry, part,
              line=False, haze=0):
    """Append one model part to ``faces`` as ``(points, fill, outline)``.

    Solid style emits the camera-facing faces, each shaded by how much sun it
    catches.  The bottom face is never emitted: a part either sits on the road
    or sits on another part, so it is never the thing you see.

    Line style emits the same part as a single outlined silhouette instead --
    a third of the canvas items for the same shape, and no per-face lighting
    to work out.  The faces still have to be found and clipped, because the
    silhouette is built from their corners; what goes away is three fills, and
    on this renderer the canvas is the expensive half.
    """
    u0, u1, v0, v1, z0, z1, side_spec, top_spec = part

    def corner(u, v, z):
        return (ox + u * fx + v * rx, oy + u * fy + v * ry, z * ppm)

    c = (corner(u0, v0, z0), corner(u1, v0, z0), corner(u1, v1, z0),
         corner(u0, v1, z0), corner(u0, v0, z1), corner(u1, v0, z1),
         corner(u1, v1, z1), corner(u0, v1, z1))
    # Every corner sits on three faces; transform the eight once and let each
    # face project from the shared result.
    view = [scene._view(*p) for p in c]
    if all(v[2] < scene.NEAR for v in view):
        return
    mid = (sum(p[0] for p in c) / 8.0,
           sum(p[1] for p in c) / 8.0,
           sum(p[2] for p in c) / 8.0)

    seen = []
    side_rgb = (side_spec if isinstance(side_spec, tuple)
                else tuple(comp * side_spec for comp in base))
    top_rgb = (top_spec if isinstance(top_spec, tuple)
               else tuple(comp * top_spec for comp in base))

    for indices, rgb in (((4, 5, 6, 7), top_rgb),      # roof
                         ((0, 1, 5, 4), side_rgb),     # left flank
                         ((1, 2, 6, 5), side_rgb),     # front
                         ((2, 3, 7, 6), side_rgb),     # right flank
                         ((3, 0, 4, 7), side_rgb)):    # rear
        a, b, d = c[indices[0]], c[indices[1]], c[indices[2]]
        e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        e2 = (d[0] - a[0], d[1] - a[1], d[2] - a[2])
        nx = e1[1] * e2[2] - e1[2] * e2[1]
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        nz = e1[0] * e2[1] - e1[1] * e2[0]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length == 0.0:
            continue
        nx, ny, nz = nx / length, ny / length, nz / length

        # Point the normal away from the box centre whichever way the winding
        # ran, so both the cull and the lighting are right for every face.
        face_mid = ((a[0] + d[0]) / 2.0, (a[1] + d[1]) / 2.0, (a[2] + d[2]) / 2.0)
        if (nx * (face_mid[0] - mid[0]) + ny * (face_mid[1] - mid[1])
                + nz * (face_mid[2] - mid[2])) < 0.0:
            nx, ny, nz = -nx, -ny, -nz

        if (nx * (eye[0] - face_mid[0]) + ny * (eye[1] - face_mid[1])
                + nz * (eye[2] - face_mid[2])) <= 0.0:
            continue     # facing away from the camera

        points = scene._project_views([view[i] for i in indices])
        if not points:
            continue
        if line:
            # Collected rather than drawn: the silhouette is the hull of every
            # visible face's corners, so nothing can be emitted until all of
            # them have been clipped.
            seen.extend((points[i], points[i + 1])
                        for i in range(0, len(points) - 1, 2))
            continue
        lit = nx * _SUN[0] + ny * _SUN[1] + nz * _SUN[2]
        light = _AMBIENT + (1.0 - _AMBIENT) * (lit if lit > 0.0 else 0.0)
        faces.append((points, _lit(rgb, light, haze), ""))

    if line and seen:
        outline = _silhouette(seen)
        if outline:
            fill_hex, line_hex = _line_colours(side_rgb, haze)
            faces.append((outline, fill_hex, line_hex))


def network_extent(link_list, pixel_per_meter):
    """The box every road segment fits inside, in pixel space."""
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for link in link_list:
        for j in range(link.get_number_of_segments()):
            seg = link.get_segment(j)
            for x, y in ((seg.get_start_x(), seg.get_start_y()),
                         (seg.get_end_x(), seg.get_end_y())):
                x0 = min(x0, x)
                y0 = min(y0, y)
                x1 = max(x1, x)
                y1 = max(y1, y)
    if x0 > x1:
        return 0.0, 0.0, 1.0, 1.0
    return (x0 * pixel_per_meter, y0 * pixel_per_meter,
            x1 * pixel_per_meter, y1 * pixel_per_meter)
