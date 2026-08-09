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

    def __init__(self, canvas):
        self.canvas = canvas
        self.camera = Camera()
        self.pixel_per_meter = 1.0
        self.width = 1
        self.height = 1
        self.show_shadows = True
        self._color = (0, 0, 0)
        self._stroke = 1.0
        self._font = ("Serif", 12)
        self._prop = None
        self._props = []       # (depth, [(screen points, colour), ...])
        self._labels = []      # (depth, x, y, text, colour, anchor, size)
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

    def begin_frame(self, width, height, pixel_per_meter) -> None:
        self.width = max(1, width)
        self.height = max(1, height)
        self.pixel_per_meter = pixel_per_meter or 1.0
        self._focal = (self.height / 2.0) / math.tan(self.camera.fov / 2.0)
        self._eye, self._fwd, self._right, self._up = self.camera.basis()
        self._props = []
        self._labels = []
        self._prop = None

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
        horizon = self.height / 2.0 - self._focal * math.tan(self.camera.pitch)
        if horizon < self.height:
            canvas.create_rectangle(0, max(horizon, 0), self.width, self.height,
                                    fill=GROUND_COLOR, outline="")
            if 0 < horizon < self.height:
                canvas.create_line(0, horizon, self.width, horizon,
                                   fill=HORIZON_COLOR)

    def flush(self) -> None:
        """Paint the raised geometry, farthest vehicle first."""
        canvas = self.canvas
        self._props.sort(key=lambda item: -item[0])
        for _depth, faces in self._props:
            for points, colour in faces:
                canvas.create_polygon(points, fill=colour, outline="")
        for _depth, x, y, text, colour, anchor, size in sorted(
                self._labels, key=lambda item: -item[0]):
            font = ("Segoe UI", size, "bold")
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                canvas.create_text(x + ox, y + oy, text=text, anchor=anchor,
                                   fill="#ffffff", font=font)
            canvas.create_text(x, y, text=text, anchor=anchor, fill=colour,
                               font=font)

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
        view = [self._view(*p) for p in points]
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
                self.canvas.create_polygon(ribbon, fill=_shaded(self._color, 1.0),
                                           outline="")
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
        self.canvas.create_line(points[0], points[1], points[2], points[3],
                                fill=_shaded(self._color, 1.0), width=width,
                                **opts)

    def fill_polygon(self, xs, ys, n) -> None:
        if self._prop is not None and n == 4:
            self._fill_prop(xs, ys)
            return
        points = self._project([(xs[i], ys[i], 0.0) for i in range(n)])
        if points:
            self.canvas.create_polygon(points, fill=_shaded(self._color, 1.0),
                                       outline="")

    def fill_oval(self, x, y, w, h) -> None:
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
                                       outline="")

    def draw_oval(self, x, y, w, h) -> None:
        points = self._project(_disc(x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0))
        if len(points) < 6:
            return
        depth = self._view(x + w / 2.0, y + h / 2.0, 0.0)[2]
        width = _clamp(self._stroke * self._focal / max(depth, self.NEAR), 1.0, 8.0)
        self.canvas.create_line(*points, points[0], points[1],
                                fill=_shaded(self._color, 1.0), width=width)

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
                             _shaded(self._color, 1.0), anchor, size))

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

        height = _MODEL_HEIGHT.get(id(model), 2.0)
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
                self.canvas.create_polygon(shadow, fill=SHADOW_COLOR, outline="")

        if not detailed:
            # Too small to resolve: one block, and no shadow either -- at this
            # size it is a sub-pixel smudge that costs as much as a face.
            model = _LOD_BLOCK.get(id(model), model)

        base = self._color
        ppm = self.pixel_per_meter
        eye = self._eye
        faces = []
        for part in model:
            _emit_box(faces, self._project, eye, base, ppm,
                      ox, oy, fx, fy, rx, ry, part)
        if faces:
            self._props.append((depth, faces))


def _disc(cx, cy, rx, ry, steps=28):
    """A circle on the ground plane as a polygon, for islands and patches."""
    return [(cx + rx * math.cos(2 * math.pi * i / steps),
             cy + ry * math.sin(2 * math.pi * i / steps), 0.0)
            for i in range(steps)]


def _emit_box(faces, project, eye, base, ppm, ox, oy, fx, fy, rx, ry, part):
    """Append the camera-facing faces of one model part to ``faces``.

    The bottom face is never emitted: a part either sits on the road or sits on
    another part, so it is never the thing you see.
    """
    u0, u1, v0, v1, z0, z1, side_spec, top_spec = part

    def corner(u, v, z):
        return (ox + u * fx + v * rx, oy + u * fy + v * ry, z * ppm)

    c = (corner(u0, v0, z0), corner(u1, v0, z0), corner(u1, v1, z0),
         corner(u0, v1, z0), corner(u0, v0, z1), corner(u1, v0, z1),
         corner(u1, v1, z1), corner(u0, v1, z1))
    mid = (sum(p[0] for p in c) / 8.0,
           sum(p[1] for p in c) / 8.0,
           sum(p[2] for p in c) / 8.0)

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

        points = project([c[i] for i in indices])
        if not points:
            continue
        lit = nx * _SUN[0] + ny * _SUN[1] + nz * _SUN[2]
        light = _AMBIENT + (1.0 - _AMBIENT) * (lit if lit > 0.0 else 0.0)
        faces.append((points, _shaded(rgb, light)))


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
