"""In-run visualisation as self-contained SVG (no third-party dependencies).

Captures a few frames while a simulation runs and returns two inline animated
SVGs (pure-CSS flip-books) ready to embed directly in the HTML report: the same
frames of the same run seen in plan view and through the 3D camera. It needs
nothing beyond the standard library and renders in any browser.

The picture is the same picture the live animation draws -- both of them. The
plan view goes through ``_SVGGraphics`` below; the 3D view goes through
:class:`dhakasim.render3d.Scene3D`, the GUI's own renderer, pointed at an
``_SVGCanvas`` instead of at the window, so the report cannot disagree with what
was on screen about the camera or about what a bus looks like.

Everything is built in
the simulator's own pixel space -- metres times ``Parameters.pixel_per_meter``,
the space vehicle bodies and intersection paths are already expressed in -- and
the SVG backend applies one uniform scale on the way out, exactly as the GUI's
affine transform does. Roads come from :mod:`dhakasim.road_geometry`, the painter
the GUI also calls, and vehicles from ``Vehicle.draw_vehicle`` with the real
simulation parameters. Only the report's own chrome -- node name labels, north
arrow, scale bar -- is drawn in output pixels, because the simulation has no
equivalent.
"""

from __future__ import annotations

import math

from .constants import Constants
from .javacompat import Color
from .parameters import Parameters
from . import basemap as basemap_module
from . import road_geometry

# The road surfaces in the report are taken straight from the colours the live
# animation paints with, so a report picture matches what was on screen.  Only
# the report's own chrome -- node markers, name labels, north arrow, scale bar --
# uses its own palette, because the simulation has no equivalent.
BACKGROUND = Constants.background_color.to_hex()


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class _SVGGraphics:
    """The drawing surface the simulator's draw methods expect; emits SVG.

    Coordinates arrive in simulation pixel space and are scaled and translated
    into the frame here, so callers never have to know the output size. Widths
    and radii are scaled the same way, which is what keeps a kerb line or a
    vehicle the right thickness relative to the road.
    """

    def __init__(self, scale, tx, ty):
        self._s = scale
        self._tx = tx
        self._ty = ty
        self._c = "#000000"
        self._w = 1.0
        self._alpha = 1.0
        self.parts = []

    def set_transform(self, *a):
        pass

    def begin_prop(self, kind, type_index=None):
        # Part of the drawing-surface protocol; only the 3D view acts on it.
        pass

    def end_prop(self):
        pass

    def set_alpha(self, fraction):
        """Real opacity, unlike the canvas, which can only dither a stipple."""
        self._alpha = max(0.0, min(1.0, float(fraction)))

    def _opacity(self):
        return "" if self._alpha >= 0.999 else f' fill-opacity="{self._alpha:.2f}"'

    def set_color(self, c):
        self._c = c.to_hex() if hasattr(c, "to_hex") else c

    def set_stroke(self, w):
        self._w = max(0.4, w * self._s)

    def set_font(self, *a):
        pass

    def draw_line(self, x1, y1, x2, y2):
        self.parts.append(
            f'<line x1="{self._tx(x1):.1f}" y1="{self._ty(y1):.1f}" '
            f'x2="{self._tx(x2):.1f}" y2="{self._ty(y2):.1f}" '
            f'stroke="{self._c}" stroke-width="{self._w:.2f}"/>')

    def fill_polygon(self, xs, ys, n):
        pts = " ".join(f"{self._tx(xs[i]):.1f},{self._ty(ys[i]):.1f}"
                       for i in range(n))
        self.parts.append(
            f'<polygon points="{pts}" fill="{self._c}"{self._opacity()}/>')

    def fill_oval(self, x, y, w, h):
        self.parts.append(
            f'<ellipse cx="{self._tx(x + w / 2):.1f}" cy="{self._ty(y + h / 2):.1f}" '
            f'rx="{w / 2 * self._s:.1f}" ry="{h / 2 * self._s:.1f}" '
            f'fill="{self._c}"{self._opacity()}/>')

    def draw_oval(self, x, y, w, h):
        self.parts.append(
            f'<ellipse cx="{self._tx(x + w / 2):.1f}" cy="{self._ty(y + h / 2):.1f}" '
            f'rx="{w / 2 * self._s:.1f}" ry="{h / 2 * self._s:.1f}" '
            f'fill="none" stroke="{self._c}" stroke-width="{self._w:.2f}"/>')

    def draw_string(self, *a):
        pass


class _SVGCanvas:
    """A stand-in for ``tkinter.Canvas`` that records SVG instead of drawing.

    :class:`dhakasim.render3d.Scene3D` treats its canvas purely as an output
    device -- polygons, lines, rectangles and text, all in screen coordinates,
    all already projected -- so handing it one of these renders the very same
    3D scene the GUI shows into the report, with no second copy of the camera
    or the vehicle models.
    """

    def __init__(self):
        self.parts = []
        self.background = None

    # ---- the slice of the Canvas API Scene3D uses ------------------------

    def configure(self, **kw) -> None:
        if "bg" in kw:
            self.background = kw["bg"]

    def delete(self, *_args) -> None:
        self.parts.clear()

    def create_rectangle(self, x0, y0, x1, y1, fill="", outline="", **_kw):
        self.parts.append(
            f'<rect x="{min(x0, x1):.1f}" y="{min(y0, y1):.1f}" '
            f'width="{abs(x1 - x0):.1f}" height="{abs(y1 - y0):.1f}" '
            f'fill="{fill or "none"}"'
            + (f' stroke="{outline}"' if outline else "") + "/>")

    def create_polygon(self, *args, fill="", outline="", **_kw):
        coords = _flatten(args)
        if len(coords) < 6:
            return
        pts = " ".join(f"{coords[i]:.1f},{coords[i + 1]:.1f}"
                       for i in range(0, len(coords) - 1, 2))
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill or "none"}"'
            + (f' stroke="{outline}"' if outline else "") + "/>")

    def create_line(self, *args, fill="", width=1.0, **_kw):
        coords = _flatten(args)
        if len(coords) < 4:
            return
        pts = " ".join(f"{coords[i]:.1f},{coords[i + 1]:.1f}"
                       for i in range(0, len(coords) - 1, 2))
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{fill}" '
            f'stroke-width="{max(0.4, width):.2f}"/>')

    def create_text(self, x, y, text="", anchor="sw", fill="", font=None, **_kw):
        size = 12
        weight = "normal"
        if font:
            if len(font) > 1:
                size = font[1]
            if len(font) > 2 and font[2]:
                weight = font[2]
        # tkinter anchors name the corner of the text box that sits on (x, y);
        # SVG says the same thing with text-anchor plus a baseline.
        if "w" in anchor:
            align = "start"
        elif "e" in anchor:
            align = "end"
        else:
            align = "middle"
        if "n" in anchor:
            baseline = ' dominant-baseline="text-before-edge"'
        elif "s" in anchor:
            baseline = ""            # SVG's default is the alphabetic baseline
        else:
            baseline = ' dominant-baseline="central"'
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{align}"{baseline} '
            f'font-family="Segoe UI,Arial,sans-serif" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}">{_esc(text)}</text>')


def _flatten(args):
    """tkinter takes coordinates either loose or as one sequence; accept both."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    out = []
    for a in args:
        if isinstance(a, (list, tuple)):
            out.extend(a)
        else:
            out.append(a)
    return out


class RunRecorder:
    """Captures frames during a run and encodes them as SVG for the report."""

    def __init__(self, processor, ppm=2.5, max_frames=24, margin=30):
        self.ok = False
        self.frames = []
        if max_frames <= 0:
            return
        self.proc = processor

        roads = []
        xs, ys = [], []
        for link in processor.link_list:
            for j in range(link.get_number_of_segments()):
                s = link.get_segment(j)
                roads.append((s.get_start_x(), s.get_start_y(),
                              s.get_end_x(), s.get_end_y(), s.get_segment_width()))
        if not roads:
            return
        for sx, sy, ex, ey, _w in roads:
            xs += [sx, ex]
            ys += [sy, ey]
        minx_m, maxx_m = min(xs), max(xs)
        miny_m, maxy_m = min(ys), max(ys)
        bbox_w = max(maxx_m - minx_m, 1.0)
        # ppm is the *output* scale: how many frame pixels one metre becomes.
        self.ppm = min(ppm, 1000.0 / bbox_w)
        # k is the simulator's own pixel scale. Geometry is built at k -- the
        # space intersection paths and vehicle bodies already live in -- and
        # `scale` shrinks it to the frame, so shapes match the GUI exactly.
        self.k = Parameters.pixel_per_meter or 1.0
        self.scale = self.ppm / self.k

        # The road geometry the GUI paints, built once at the simulator's scale.
        # Drawn wider over imagery, for the reason in Constants -- the report
        # has to agree with the window about the shape of the road.  Whether
        # there *is* imagery is settled here rather than read off
        # ``self.basemap``, which is not built until the frame is sized, and
        # the geometry has to exist before that to size it.
        self.has_basemap = basemap_module.BaseMap.load(
            Parameters.NETWORK_DIR, processor.link_list,
            processor.node_list) is not None
        self.geometry = road_geometry.build(
            processor.link_list, processor.node_list, self.k,
            widen=(Constants.OVERLAY_WIDEN_METRES if self.has_basemap
                   else 0.0))

        # Work out where every label will sit *before* fixing the canvas, so
        # the frame can be sized to include the labels rather than clipping
        # them. Everything here is in un-shifted simulation pixels (metres * k);
        # the scale and translation are applied once the extent is known.
        self.labels = []
        # Two extents, not one.  ``road`` is the carriageway and nothing else,
        # and it is what the frame gets centred on; ``extent`` is everything
        # that has to fit inside the frame, labels included.
        road = []
        extent = []
        (_quads, hulls, discs, connectors,
         _markings, carriage) = self.geometry
        for near, far in carriage:               # the painted road surface
            road.extend(near)
            road.extend(far)
        for cxs, cys in connectors:              # turning paths, which swing
            road.extend(zip(cxs, cys))           # wider than the patch
        for hxs, hys, cx, cy, radius, _kerb in hulls:   # junction patches
            road.append((cx - radius, cy - radius))
            road.append((cx + radius, cy + radius))
            road.extend(zip(hxs, hys))
        for cx, cy, _r, outer in discs:          # roundabouts, ring and all
            road.append((cx - outer, cy - outer))
            road.append((cx + outer, cy + outer))
        extent.extend(road)
        # Street names sit on the carriageway itself, so they are kept apart
        # from self.labels, which carries leader lines and a 3D counterpart
        # that a road name has no use for.
        self.link_labels = []
        for link in processor.link_list:
            link_name = Parameters.LINK_NAMES.get(link.get_id(),
                                                  str(link.get_id()))
            count = link.get_number_of_segments()
            if count <= 0:
                continue
            seg = link.get_segment(count // 2)
            mx = (seg.get_start_x() + seg.get_end_x()) / 2.0 * self.k
            my = (seg.get_start_y() + seg.get_end_y()) / 2.0 * self.k
            self.link_labels.append((link_name, mx, my))
            extent.append((mx, my))

        for node in processor.node_list:
            name = Parameters.NODE_NAMES.get(node.get_id(), str(node.get_id()))
            px, py = self._node_point(processor, node)
            dx, dy = self._label_direction(processor, node, px, py)
            widest = 0.0
            for k in range(node.number_of_links()):
                lk = processor.link_list[node.get_link(k)]
                for s in range(lk.get_number_of_segments()):
                    widest = max(widest, lk.get_segment(s).get_segment_width())
            clearance = (widest * 2.2 + 26.0 if node.number_of_links() > 1
                         else widest * 1.1 + 16.0)
            lx = (px + dx * clearance) * self.k
            ly = (py + dy * clearance) * self.k
            nx = (px + dx * clearance * 0.28) * self.k
            ny = (py + dy * clearance * 0.28) * self.k
            anchor = "end" if dx < -0.35 else ("start" if dx > 0.35 else "middle")
            self.labels.append((name, px * self.k, py * self.k,
                                lx, ly, nx, ny, anchor))
            # bounding box of the text itself, at font-size 14 bold; the text is
            # drawn in output pixels, so convert its box back to build space
            text_w = len(name) * 8.4 / self.scale
            left = {"start": 0.0, "end": -text_w, "middle": -text_w / 2.0}[anchor]
            extent.append((lx + left, ly - 16.0 / self.scale))
            extent.append((lx + left + text_w, ly + 6.0 / self.scale))

        # Centre the frame on the carriageway rather than on the whole
        # picture.  A node name is pushed well clear of the roads and is as
        # long as the street happens to be called, so a frame sized to
        # everything drawn slides the junction sideways by however far the
        # longest name overhangs -- a couple of hundred pixels at Khamarbari,
        # which is exactly the lopsidedness the animation had.  The names
        # still have to fit, so each half-width is taken out to whichever
        # reaches further, road or label, and then matched on the other side.
        mid_x = (min(p[0] for p in road) + max(p[0] for p in road)) / 2.0
        mid_y = (min(p[1] for p in road) + max(p[1] for p in road)) / 2.0
        half_w = max(max(abs(p[0] - mid_x) for p in extent), 1.0)
        half_h = max(max(abs(p[1] - mid_y) for p in extent), 1.0)
        minx, maxx = mid_x - half_w, mid_x + half_w
        miny, maxy = mid_y - half_h, mid_y + half_h
        # leave room for the north arrow and the scale bar as well.  Half of
        # it goes into the offset: added to the size alone it would push the
        # picture back off centre by half a pad.
        pad = 24.0
        self.TXv = margin + pad / 2.0 - minx * self.scale
        self.TYv = margin + pad / 2.0 - miny * self.scale
        self.W = int((maxx - minx) * self.scale + 2 * margin + pad)
        self.H = int((maxy - miny) * self.scale + 2 * margin + pad)

        # Map imagery underneath, when the network has any, so the report shows
        # the same place the window does.  The flip-book repeats the whole
        # static layer in every frame, so the picture itself is defined once
        # and referenced from each frame; embedding it in the layer directly
        # would multiply several megabytes by the frame count.
        bg = []
        self.basemap_defs = self._embed_basemap(processor)
        self.basemap = ('<use href="#dsbasemap"/>' if self.basemap_defs else "")

        # The road network exactly as the GUI paints it -- surfaces, kerbs,
        # junction patches and roundabout islands -- followed by the report's
        # own node markers and name labels.
        road = _SVGGraphics(self.scale, self._tx, self._ty)
        road_geometry.paint(road, processor.link_list, processor.node_list,
                            self.k, self.geometry, fill=not self.basemap)
        bg.append(self.basemap)
        bg.extend(road.parts)

        # Node names, pushed clear of the carriageway and joined back to the
        # junction with an arrow, so a name never sits on top of a road.
        for name, px_px, py_px, lx_r, ly_r, nx_r, ny_r, anchor in self.labels:
            lx, ly = self._tx(lx_r), self._ty(ly_r)
            nx, ny = self._tx(nx_r), self._ty(ny_r)
            bg.append(
                f'<circle cx="{self._tx(px_px):.1f}" '
                f'cy="{self._ty(py_px):.1f}" r="4" fill="#1b3a5b"/>'
                f'<line x1="{lx:.1f}" y1="{ly:.1f}" x2="{nx:.1f}" y2="{ny:.1f}" '
                f'stroke="#5a5a5a" stroke-width="1" marker-end="url(#dsarrow)"/>'
                # halo drawn as a separate element underneath, rather than with
                # paint-order, which not every SVG renderer honours
                f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                f'font-family="Segoe UI,Arial,sans-serif" font-size="14" '
                f'font-weight="bold" fill="none" stroke="{BACKGROUND}" '
                f'stroke-width="4" stroke-linejoin="round">{_esc(name)}</text>'
                f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                f'font-family="Segoe UI,Arial,sans-serif" font-size="14" '
                f'font-weight="bold" fill="#12263a">{_esc(name)}</text>')
        # Street names, drawn on the road with the same halo treatment.
        for name, mx_r, my_r in getattr(self, "link_labels", []):
            mx, my = self._tx(mx_r), self._ty(my_r)
            bg.append(
                f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" '
                f'font-family="Segoe UI,Arial,sans-serif" font-size="11" '
                f'fill="none" stroke="{BACKGROUND}" stroke-width="3.5" '
                f'stroke-linejoin="round">{_esc(name)}</text>'
                f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" '
                f'font-family="Segoe UI,Arial,sans-serif" font-size="11" '
                f'fill="#33414d">{_esc(name)}</text>')

        self.bg = "".join(bg)
        self.north = self._north_arrow()
        self.furniture = self.north + self._scale_bar()

        end = max(1, Parameters.simulation_end_time)
        self.interval = max(1, end // max_frames)
        self.max_frames = max_frames
        self.ok = True

        # The same run again, through the GUI's 3D camera.  Optional: if it
        # cannot be set up the report simply keeps the plan view.
        self.frames3d = []
        self.bg3d = ""
        self.scene = None
        try:
            self._init_3d(processor)
        except Exception as exc:
            print(f"3D run animation disabled: {exc!r}")
            self.scene = None

    #: Widest the embedded imagery is allowed to be.  The report is one
    #: self-contained file and base64 adds a third on top of the PNG, so this
    #: is the main thing standing between a report that opens quickly and one
    #: that does not.
    BASEMAP_MAX_PIXELS = 900

    def _embed_basemap(self, processor) -> str:
        """An ``<image>`` element carrying the network's imagery, or "".

        Imagery is optional and a report must never fail for want of it, so
        every way this can go wrong ends in an empty string and a plain
        background.
        """
        try:
            base = basemap_module.BaseMap.load(Parameters.NETWORK_DIR,
                                               processor.link_list,
                                               processor.node_list)
            if base is None:
                return ""
            x = self._tx(base.left * self.k)
            y = self._ty(base.top * self.k)
            width = (base.right - base.left) * self.k * self.scale
            height = (base.bottom - base.top) * self.k * self.scale
            if width <= 0 or height <= 0:
                return ""
            self.basemap_credit = base.attribution
            # No more pixels than the report draws, and never more than
            # BASEMAP_MAX_PIXELS across: this is the single largest thing in
            # the file, and the reader is looking at a thumbnail of a junction.
            encoded = base.png_bytes(min(int(width) + 1, self.BASEMAP_MAX_PIXELS))
            return ('<svg width="0" height="0" aria-hidden="true" '
                    'style="position:absolute">'
                    f'<defs><image id="dsbasemap" x="{x:.1f}" y="{y:.1f}" '
                    f'width="{width:.1f}" height="{height:.1f}" '
                    f'preserveAspectRatio="none" '
                    f'href="data:image/png;base64,{encoded}"/></defs></svg>')
        except Exception as exc:
            print(f"report basemap skipped: {exc!r}")
            return ""

    #: The report places node labels with SVG anchors; Scene3D speaks tkinter's.
    _TK_ANCHOR = {"start": "sw", "end": "se", "middle": "s"}

    def _fit_3d_camera(self, extent, margin=48) -> None:
        """Zoom and re-aim the camera until the network fills the frame.

        There is no closed form for this: perspective makes the projected size
        of the network depend on the very distance being solved for, and the
        ground centre does not project to the centre of the frame.  Nudging the
        aim and the distance towards a fit converges in a handful of passes,
        and it only runs once per report.
        """
        scene, camera = self.scene, self.scene.camera
        x0, y0, x1, y1 = extent
        # The carriageway is what the shot is aimed at; the names only have to
        # fit in it.  Measuring both off one list would let a long name on one
        # side swing the camera away from the junction, which is the same
        # lopsidedness the plan view had.
        ground = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        corners = ground + [(lx, ly)
                            for _n, lx, ly, _nx, _ny, _a in self.labels3]

        for _ in range(40):
            scene.begin_frame(self.W3, self.H3, self.k)
            view = [scene._view(cx, cy, 0.0) for cx, cy in corners]
            if any(v[2] < scene.NEAR for v in view):
                camera.distance *= 1.5          # some of it is behind the lens
                continue
            pts = [scene._screen(v) for v in view]
            aim = pts[:len(ground)]
            centre_x = (min(p[0] for p in aim) + max(p[0] for p in aim)) / 2.0
            top = min(p[1] for p in pts)
            bottom = max(p[1] for p in pts)

            # Aimed a little below centre, which leaves a band of sky along the
            # top.  Without it the ground fills the frame edge to edge and the
            # picture loses the one cue that says it is a perspective view.
            camera.pan((self.W3 / 2.0) - centre_x,
                       (self.H3 * 0.57) - (top + bottom) / 2.0, scene.focal)

            # Width is measured as a reach out from the aim point rather than
            # as a plain bounding box, so a name overhanging one side pulls
            # the camera back instead of sliding the junction across.
            reach = max(abs(p[0] - centre_x) for p in pts)
            fit = max(2 * reach / max(self.W3 - 2 * margin, 1),
                      (bottom - top) / max(self.H3 - 2 * margin, 1))
            if 0.97 <= fit <= 1.03:
                break
            camera.distance *= fit
        scene.begin_frame(self.W3, self.H3, self.k)

    def _init_3d(self, processor) -> None:
        """Set up the 3D camera and render the parts of the scene that never
        move -- sky, ground, roads and node names -- once."""
        from .render3d import Scene3D, network_extent

        # The 3D view gets its own frame rather than the plan view's.  A
        # perspective shot wants a letterbox: the ground runs away towards a
        # horizon across the top, so the height the plan view needs to fit a
        # north-south network is mostly empty here.
        self.W3 = self.W
        self.H3 = max(240, int(self.W * 0.52))

        extent = network_extent(processor.link_list, self.k)
        scene = Scene3D(_SVGCanvas())
        scene.set_ground_extent(*extent)
        scene.camera.frame(*extent)
        # Lower than the GUI opens at.  The report gets one fixed camera, so it
        # may as well be one with enough perspective to read as
        # three-dimensional at a glance.
        #
        # The horizon of a ground plane lands at height/2 - focal*tan(pitch),
        # and focal is (height/2)/tan(fov/2), so the horizon is on screen only
        # while pitch stays under half the field of view.  At exactly half it
        # sits on the top edge and no sky shows at all; 21 degrees against this
        # 52 degree lens leaves a band of it.
        scene.camera.pitch = math.radians(21.0)
        # A whole network in one frame puts a car at a few pixels long, where
        # the detailed models and their shadows would cost tens of thousands of
        # SVG polygons to draw something no reader can resolve.  Scene3D drops
        # to its single-block level of detail by itself at that size; turning
        # the shadows off as well keeps the report a sane size.
        scene.show_shadows = False
        self.scene = scene

        # The plan view pushes a name well clear of the carriageway so it never
        # sits on a road. Perspective does that job by itself -- the ground
        # around a junction is already empty -- and the full push would throw a
        # terminal's name off the frame, so the 3D labels sit half as far out.
        self.labels3 = [
            (name, px + (lx - px) * 0.5, py + (ly - py) * 0.5,
             px + (nx - px) * 0.5, py + (ny - py) * 0.5, anchor)
            for name, px, py, lx, ly, nx, ny, anchor in self.labels]

        self._fit_3d_camera(extent)

        canvas = _SVGCanvas()
        scene.set_output(canvas)
        scene.begin_frame(self.W3, self.H3, self.k)
        road_geometry.paint(scene, processor.link_list, processor.node_list,
                            self.k, self.geometry)
        # Label size falls off with distance like everything else in the scene,
        # which over a whole network puts the nearest name several times the
        # size of the furthest. A smaller world size drives most of them onto
        # Scene3D's minimum, so the spread stays narrow enough to read as one
        # set of labels rather than a ransom note.
        scene.set_font("Serif", 52)
        for name, lx, ly, nx, ny, anchor in self.labels3:
            scene.set_color(Color(90, 90, 90))
            scene.draw_line(lx, ly, nx, ny, arrow=True)
            scene.set_color(Color.BLACK)
            scene.draw_string(name, lx, ly, self._TK_ANCHOR.get(anchor, "s"))
        scene.flush()
        self.bg3d = "".join(canvas.parts)
        self.sky = canvas.background or BACKGROUND

    def _tx(self, x):
        """Simulation pixel space -> frame pixels."""
        return x * self.scale + self.TXv

    def _ty(self, y):
        return y * self.scale + self.TYv

    @staticmethod
    def _node_point(processor, node):
        """Where a node actually sits: junctions are stored at (0, 0)."""
        return road_geometry.node_point(processor.link_list, node)

    @staticmethod
    def _label_direction(processor, node, px, py):
        """Which way to push a node's label so it clears the roads.

        A terminal goes outwards along its own road; a junction goes into the
        widest gap between its arms, which also sends neighbouring junctions'
        names in different directions.
        """
        bearings = []
        for k in range(node.number_of_links()):
            lk = processor.link_list[node.get_link(k)]
            if lk.get_up_node() == node.get_id():
                seg = lk.get_first_segment()
                fx, fy = seg.get_end_x(), seg.get_end_y()
            else:
                seg = lk.get_last_segment()
                fx, fy = seg.get_start_x(), seg.get_start_y()
            bearings.append(math.atan2(fx - px, -(fy - py)) % (2 * math.pi))
        if not bearings:
            return 0.0, -1.0
        if len(bearings) == 1:
            a = (bearings[0] + math.pi) % (2 * math.pi)   # away from the junction
            return math.sin(a), -math.cos(a)
        bearings.sort()
        best, best_gap = 0.0, -1.0
        for i in range(len(bearings)):
            a = bearings[i]
            gap = (bearings[(i + 1) % len(bearings)] - a) % (2 * math.pi)
            if gap > best_gap:
                best_gap, best = gap, (a + gap / 2.0) % (2 * math.pi)
        return math.sin(best), -math.cos(best)

    def _north_arrow(self, width=None):
        """North arrow, in the same units as the frame.

        Networks are built with bearings measured from screen-up, and the
        report's 3D camera looks due north, so up is north in both pictures.
        """
        cx, cy = (self.W if width is None else width) - 46, 46
        return (
            f'<circle cx="{cx}" cy="{cy}" r="24" fill="#ffffff" stroke="#c7ccd3"/>'
            f'<polygon points="{cx},{cy - 17} {cx - 7},{cy + 10} {cx},{cy + 5} '
            f'{cx + 7},{cy + 10}" fill="#12263a"/>'
            f'<text x="{cx}" y="{cy + 21}" text-anchor="middle" '
            f'font-family="Segoe UI,Arial,sans-serif" font-size="11" '
            f'font-weight="bold" fill="#12263a">N</text>')

    def _scale_bar(self):
        """Scale bar, in the same units as the frame.

        The plan view only: under perspective the metres a pixel covers change
        from the top of the frame to the bottom, so a single bar would be a
        lie.
        """
        parts = []
        nice = None
        for candidate in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
            if candidate * self.ppm >= 90:
                nice = candidate
                break
        if nice is None:
            nice = 1000
        bar = nice * self.ppm
        x0, y0 = 18, self.H - 20
        label = f"{nice} m" if nice < 1000 else "1 km"
        parts.append(
            f'<rect x="{x0 - 8}" y="{y0 - 24}" width="{bar + 16:.1f}" height="34" '
            f'fill="#ffffff" stroke="#c7ccd3"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{x0 + bar:.1f}" y2="{y0}" '
            f'stroke="#12263a" stroke-width="3"/>'
            f'<line x1="{x0}" y1="{y0 - 5}" x2="{x0}" y2="{y0 + 4}" '
            f'stroke="#12263a" stroke-width="2"/>'
            f'<line x1="{x0 + bar:.1f}" y1="{y0 - 5}" x2="{x0 + bar:.1f}" '
            f'y2="{y0 + 4}" stroke="#12263a" stroke-width="2"/>'
            f'<text x="{x0 + bar / 2:.1f}" y="{y0 - 9}" text-anchor="middle" '
            f'font-family="Segoe UI,Arial,sans-serif" font-size="11" '
            f'font-weight="bold" fill="#12263a">{label}</text>')
        return "".join(parts)

    def maybe_capture(self, step):
        if not self.ok or len(self.frames) >= self.max_frames:
            return
        if step % self.interval != 0:
            return
        g = _SVGGraphics(self.scale, self._tx, self._ty)
        try:
            vehicles = self.proc.get_vehicle_list()
        except Exception:
            vehicles = []
        for v in vehicles:
            try:
                # the simulator's own parameters: vehicles in junctions are
                # positioned from intersection paths built in this same space
                v.draw_vehicle(None, g, Parameters.pixel_per_strip,
                               Parameters.pixel_per_meter,
                               Parameters.pixel_per_footpath_strip)
            except Exception:
                pass
        caption = (f'<text x="12" y="22" font-family="Segoe UI,Arial,sans-serif" '
                   f'font-size="15" font-weight="bold" fill="#12263a">'
                   f'DhakaSim &#183; step {step}</text>')
        self.frames.append("".join(g.parts) + caption)

        if self.scene is not None:
            self.frames3d.append(self._capture_3d(vehicles) + caption)

    def _capture_3d(self, vehicles):
        """One frame of the 3D view: the vehicles only.

        The sky, the ground and the roads are the same in every frame, so they
        are rendered once into the static layer and dropped here -- an opaque
        ground rectangle repeated on every frame would hide the roads under it
        as well as costing a copy of the network per frame.
        """
        canvas = _SVGCanvas()
        scene = self.scene
        scene.set_output(canvas)
        scene.begin_frame(self.W3, self.H3, self.k)
        canvas.parts.clear()
        for v in vehicles:
            try:
                # The type picks the 3D model, exactly as the GUI passes it.
                scene.begin_prop("vehicle", v.get_type())
                v.draw_vehicle(None, scene, Parameters.pixel_per_strip,
                               Parameters.pixel_per_meter,
                               Parameters.pixel_per_footpath_strip)
            except Exception:
                pass
            finally:
                scene.end_prop()
        scene.flush()
        return "".join(canvas.parts)

    def _svg(self, body, bg, furniture, background, w, h):
        defs = ('<defs><marker id="dsarrow" markerWidth="9" markerHeight="9" '
                'refX="8" refY="3" orient="auto">'
                '<path d="M0,0 L9,3 L0,6 z" fill="#5a5a5a"/></marker></defs>')
        return (f'<svg viewBox="0 0 {w} {h}" '
                f'style="width:100%;height:auto;display:block;'
                f'background:{background}" '
                f'xmlns="http://www.w3.org/2000/svg">{defs}{bg}{body}'
                f'{furniture}</svg>')

    def _film(self, frames, bg, furniture, background, prefix, w, h):
        """A flip-book: every frame side by side, stepped along by one CSS
        animation.  ``prefix`` keeps two films on the same page from sharing
        class names."""
        n = len(frames)
        dur = max(1.5, n * 0.18)
        strip = "".join(
            f'<div class="{prefix}f">'
            f'{self._svg(fr, bg, furniture, background, w, h)}</div>'
            for fr in frames)
        return (
            "<style>"
            f".{prefix}wrap{{max-width:{w}px;overflow:hidden;"
            f"border-radius:8px}}"
            f".{prefix}film{{display:flex;width:{w * n}px;"
            f"animation:{prefix}play {dur:.1f}s steps({n}) infinite}}"
            f".{prefix}f{{flex:0 0 {w}px}}"
            f".{prefix}f svg{{width:100%;height:auto;display:block}}"
            f"@keyframes {prefix}play{{to{{transform:translateX(-100%)}}}}"
            "</style>"
            f'<div class="{prefix}wrap"><div class="{prefix}film">{strip}</div>'
            f'</div>')

    def finish(self):
        """Return ``{"animation": html, "animation_3d": html}`` or ``None``.

        ``animation_3d`` is absent if the 3D track could not be set up.
        """
        if not self.ok or not self.frames:
            return None
        film = self._film(self.frames, self.bg, self.furniture,
                          BACKGROUND, "ds", self.W, self.H)
        # The imagery goes in front of the film, once, where every frame's
        # <use> can reach it: references resolve across the whole document.
        out = {"animation": getattr(self, "basemap_defs", "") + film}
        # Every imagery licence this can use asks for a credit wherever the
        # picture appears, so it travels with the animation rather than being
        # left to whoever assembles the page.
        credit = getattr(self, "basemap_credit", "")
        if credit and getattr(self, "basemap_defs", ""):
            out["basemap_credit"] = credit
        if self.frames3d:
            # No scale bar in perspective, and the sky is the backdrop.
            out["animation_3d"] = self._film(self.frames3d, self.bg3d,
                                             self._north_arrow(self.W3),
                                             self.sky, "ds3",
                                             self.W3, self.H3)
        return out
