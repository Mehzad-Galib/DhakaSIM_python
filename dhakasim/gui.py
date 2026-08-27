"""Port of ``DhakaSimFrame`` / ``OptionPanel`` / ``DhakaSimPanel`` to tkinter.

Swing's ``Graphics2D`` is replaced by :class:`CanvasGraphics`, a thin adapter
that applies the same affine transform Java set up
(``translate(w/2,h/2) · scale(s) · translate(-w/2,-h/2) · translate(tx,ty)``)
and draws onto a ``tkinter.Canvas``.  Everything the drawing code writes to
``trace.txt`` is produced from untransformed world coordinates, exactly as in
the Java version, so traces stay interchangeable between the two builds.
"""

from __future__ import annotations

import math
import os
import re
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

from .constants import Constants
from .javacompat import Color, JavaRandom, jbool, jbool_str, jint, jround, jstr
from .parameters import Parameters
from .processor import Processor
from . import basemap as basemap_module
from . import render3d
from . import road_geometry
from . import utilities as Utilities


class CanvasGraphics:
    """The ``Graphics2D`` surface used by the drawing methods."""

    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        self._color = Color.BLACK
        self._stroke = 1
        self._stipple = ""
        self._font = ("Serif", 12)
        self.scale = 1.0
        self.translate_x = 0.0
        self.translate_y = 0.0
        self.width = 1
        self.height = 1

    def set_transform(self, width, height, scale, translate_x, translate_y) -> None:
        self.width = width
        self.height = height
        self.scale = scale
        self.translate_x = translate_x
        self.translate_y = translate_y

    def _tx(self, x: float) -> float:
        return (x + self.translate_x - self.width / 2.0) * self.scale + self.width / 2.0

    def _ty(self, y: float) -> float:
        return (y + self.translate_y - self.height / 2.0) * self.scale + self.height / 2.0

    # ---- Graphics API ----------------------------------------------------

    def set_color(self, color) -> None:
        self._color = color

    def set_alpha(self, fraction) -> None:
        """How solid the next fills are, from 0 (invisible) to 1 (opaque).

        A Tk canvas has no alpha channel, so this is dithered: the item is
        drawn through a stipple mask that leaves a fraction of its pixels
        unpainted.  Coarse, but it is the only translucency Tk offers, and at
        the size a carriageway is drawn the eye reads it as a wash rather than
        as a pattern.  ``_SVGGraphics`` implements the same call with real
        opacity; ``Scene3D`` ignores it.
        """
        if fraction >= 0.95:
            self._stipple = ""
        elif fraction >= 0.62:
            self._stipple = "gray75"
        elif fraction >= 0.37:
            self._stipple = "gray50"
        elif fraction >= 0.15:
            self._stipple = "gray25"
        else:
            self._stipple = "gray12"

    def set_stroke(self, width) -> None:
        self._stroke = width

    def set_font(self, family, size) -> None:
        self._font = (family, size)

    def begin_prop(self, kind, type_index=None) -> None:
        """Ignored here; :class:`dhakasim.render3d.Scene3D` uses it to pick the
        3D model to stand on the footprint that follows."""

    def end_prop(self) -> None:
        pass

    def draw_line(self, x1, y1, x2, y2, arrow=False) -> None:
        opts = {}
        if arrow:
            # arrowhead at the far end, sized so it stays visible when zoomed out
            size = max(4.0, 9.0 * self.scale)
            opts["arrow"] = "last"
            opts["arrowshape"] = (size * 1.6, size * 2.0, size * 0.7)
        self.canvas.create_line(self._tx(x1), self._ty(y1), self._tx(x2), self._ty(y2),
                                fill=self._color.to_hex(),
                                width=max(1, self._stroke * self.scale), **opts)

    def fill_polygon(self, xs, ys, n) -> None:
        points = []
        for i in range(n):
            points.append(self._tx(xs[i]))
            points.append(self._ty(ys[i]))
        # offset "#0,0" pins every stipple to the same origin.  Tk otherwise
        # aligns the pattern to each item, so overlapping washes land on
        # different pixels and compound towards opaque -- and a junction is
        # fifty-odd overlapping quads, a hull and its connectors.
        self.canvas.create_polygon(points, fill=self._color.to_hex(),
                                   outline="", stipple=self._stipple,
                                   offset="#0,0")

    def fill_oval(self, x, y, w, h) -> None:
        x1 = self._tx(x)
        y1 = self._ty(y)
        self.canvas.create_oval(x1, y1, x1 + w * self.scale, y1 + h * self.scale,
                                fill=self._color.to_hex(), outline="",
                                stipple=self._stipple, offset="#0,0")

    def draw_oval(self, x, y, w, h) -> None:
        x1 = self._tx(x)
        y1 = self._ty(y)
        self.canvas.create_oval(x1, y1, x1 + w * self.scale, y1 + h * self.scale,
                                fill="", outline=self._color.to_hex(),
                                width=max(1, self.scale))

    def draw_string(self, text, x, y, anchor="sw") -> None:
        size = max(1, int(self._font[1] * self.scale))
        self.canvas.create_text(self._tx(x), self._ty(y), text=text, anchor=anchor,
                                fill=self._color.to_hex(), font=(self._font[0], size))

    def to_screen(self, x, y):
        """World pixels to canvas coordinates.

        The basemap is a raster rather than a shape, so it cannot go through
        the drawing calls above.  It still has to land in the same place as
        everything else, which means going through the same transform.
        """
        return self._tx(x), self._ty(y)


class ProgressSlider:
    """Stands in for the Swing ``JSlider`` the drawing code pokes at.

    A plain ``tk.Scale`` rather than ttk: under the Windows theme ttk paints
    its sliders from the OS and ignores every colour handed to it, and the
    run screen dresses in the start screen's palette (``_UI``).
    """

    def __init__(self, parent, minimum, maximum, value):
        self.var = tk.DoubleVar(value=value)
        self.widget = tk.Scale(parent, from_=minimum, to=max(maximum, minimum + 1),
                               orient="horizontal", variable=self.var,
                               showvalue=False, borderwidth=0,
                               highlightthickness=0, sliderrelief="flat",
                               sliderlength=26, width=9,
                               background=_UI["seg_off"],
                               troughcolor=_UI["band"],
                               activebackground=_UI["accent"])

    def set_value(self, value) -> None:
        try:
            self.var.set(value)
        except tk.TclError:
            pass

    def get_value(self):
        return int(self.var.get())


#: Unit offsets for the white text halo: the four sides and the four
#: diagonals (at 0.7 so the corner copies sit on the same radius).
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1),
                 (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7))


class DhakaSimPanel:
    """Port of ``thesisfinal.DhakaSimPanel``."""

    # The 2D zoom range every control shares -- slider, wheel and Reset.  Wide
    # enough for the BUET-DU-DMC demo at one end (whole network ~0.037) and
    # kerb-level inspection at the other.
    ZOOM_MIN = 0.02
    ZOOM_MAX = 3.0

    def __init__(self, frame, parent):
        self.frame = frame
        self.trace_reader = None
        self.trace_writer = None
        self.draw_roads = True
        self.draw_trajectories = False
        self.random = Parameters.random
        self.vehicle_id = 0
        self.scale = 0.10 + Constants.DEFAULT_SCALE * 0.05
        self._reference_x = -999999999
        self._reference_y = -999999999
        self._road_geometry = None
        self._road_geometry_widen = 0.0
        # node id -> chosen label direction, and link id -> label point,
        # both keyed off the drawn geometry; cleared whenever it is rebuilt.
        self._label_dirs = {}
        self._link_label_pts = {}
        # What the 3D static layer on the canvas was built from; None means
        # there is no reusable static layer.  See paint_component.
        self._static3d_key = None
        self._timer = None
        self._finished = False
        self._basemap = None
        self._basemap_photo = None
        self.show_basemap = True
        self.paused = False

        try:
            if Parameters.TRACE_MODE:
                self.trace_reader = open("trace.txt", "r")
                tokenizer = self.trace_reader.readline().split()
                Parameters.simulation_speed = int(tokenizer[0])
                Parameters.simulation_end_time = int(tokenizer[1])
                Parameters.across_pedestrian_mode = jbool(tokenizer[2])
            else:
                self.trace_writer = open("trace.txt", "w")
                self.trace_writer.write(
                    f"{Parameters.simulation_speed} {Parameters.simulation_end_time} "
                    f"{jbool_str(Parameters.across_pedestrian_mode)}\n")
                self.trace_writer.flush()
        except OSError as fe:
            print(fe)

        self.canvas = tk.Canvas(parent, bg=Constants.background_color.to_hex(),
                                highlightthickness=0)
        self.graphics = CanvasGraphics(self.canvas)
        # The 3D view is a second drawing surface taking the same calls, so
        # switching between them changes nothing about what is simulated or
        # where anything is -- only how the same four corners are drawn.
        self.scene3d = render3d.Scene3D(self.canvas)
        self.view_3d = Parameters.RENDER_3D
        self.canvas.bind("<ButtonPress-1>", self.mouse_pressed)
        self.canvas.bind("<B1-Motion>", self.mouse_dragged)
        self.canvas.bind("<ButtonPress-3>", self.mouse_pressed)
        self.canvas.bind("<B3-Motion>", self.mouse_dragged_right)
        self.canvas.bind("<Double-Button-1>", self.mouse_double_clicked)
        self.canvas.bind("<MouseWheel>", self.mouse_wheel_moved)

        self.processor = Processor()

        self.mid_point = self.processor.get_mid_point()
        self.pedestrians = self.processor.get_pedestrians()
        self.vehicle_list = self.processor.get_vehicle_list()
        self.object_list = self.processor.get_object_list()
        self.node_list = self.processor.get_node_list()
        self.link_list = self.processor.get_link_list()

        if Parameters.CENTERED_VIEW:
            self.translate_x = -self.mid_point.x * Parameters.pixel_per_meter
            self.translate_y = -self.mid_point.y * Parameters.pixel_per_meter
        else:
            self.translate_x = Parameters.DEFAULT_TRANSLATE_X
            self.translate_y = Parameters.DEFAULT_TRANSLATE_Y
        # The view a zoom Reset goes back to: this pan, and the whole-network
        # scale fit_scale() works out once the canvas has a size.
        self._home_translate = (self.translate_x, self.translate_y)
        self.home_scale = self.scale

        # Map imagery, when this network has had any fetched for it.  Absent
        # is the normal case, so nothing downstream may assume it is there.
        self._basemap = basemap_module.load_for_current_network(
            self.link_list, self.node_list)

        # Frame the whole network for the 3D camera, and remember it as the
        # view a double-click goes back to.
        extent = render3d.network_extent(self.link_list, Parameters.pixel_per_meter)
        self.scene3d.set_ground_extent(*extent)
        self.scene3d.camera.frame(*extent)

    def start(self) -> None:
        self._timer = self.canvas.after(max(1, Parameters.simulation_speed),
                                       self._on_timer)

    def basemap_active(self) -> bool:
        """Whether imagery is being drawn behind the plan view right now."""
        return (self._basemap is not None and self.show_basemap
                and not self.view_3d)

    def set_scale(self, scale: float) -> None:
        # Snap to a scale the imagery can be drawn at exactly.  Tk rescales a
        # photo by whole numbers only, so an unsnapped zoom would slide the
        # map off the roads.  See dhakasim.basemap.
        if self._basemap is not None and self.show_basemap:
            scale = self._basemap.snap(scale, Parameters.pixel_per_meter)
        self.scale = scale
        if self.view_3d:
            # One zoom control for both views: the slider's 2D scale is read as
            # a camera distance, so the same handle does the same job in each.
            base = self.scene3d.camera.home_distance
            self.scene3d.camera.distance = base * (0.30 / max(scale, 0.004))
        self.repaint()

    def set_view_3d(self, enabled: bool) -> None:
        self.view_3d = bool(enabled)
        if self.view_3d:
            self.set_scale(self.scale)   # re-derive the camera distance
        self.repaint()

    def get_trace_reader(self):
        return self.trace_reader

    def set_trace_reader(self, trace_reader) -> None:
        self.trace_reader = trace_reader

    # ---- painting --------------------------------------------------------

    def repaint(self) -> None:
        self.paint_component()

    def paint_component(self) -> None:
        canvas = self.canvas
        width = canvas.winfo_width() or 1
        height = canvas.winfo_height() or 1
        reuse_static = False
        if self.view_3d:
            g2d = self.scene3d
            camera = g2d.camera
            # Everything the projected road layer depends on.  While none of
            # it changes -- the common case, a run being watched from a still
            # camera -- the sky, ground, roads and node names stay on the
            # canvas and only the vehicles are deleted and redrawn, which is
            # the same economy the report has always used
            # (RunRecorder._capture_3d renders the static layer once).  The
            # canvas is the expensive half of this renderer, so not re-feeding
            # it several hundred static items a frame is the single biggest
            # saving available.
            static_key = (width, height, Parameters.pixel_per_meter,
                          round(camera.target[0], 2), round(camera.target[1], 2),
                          round(camera.distance, 2), round(camera.yaw, 5),
                          round(camera.pitch, 5), camera.fov,
                          self.draw_roads, id(self._road_geometry))
            reuse_static = static_key == self._static3d_key
            if reuse_static:
                canvas.delete(render3d.Scene3D.DYNAMIC_TAG)
                canvas.delete("furniture")
            else:
                canvas.delete("all")
            g2d.begin_frame(width, height, Parameters.pixel_per_meter,
                            keep_static=reuse_static)
        else:
            canvas.delete("all")
            # 2D items are untagged, so a later 3D frame must not mistake
            # what this frame paints for its own static layer.
            self._static3d_key = None
            g2d = self.graphics
            g2d.set_transform(width, height, self.scale,
                              self.translate_x, self.translate_y)
            canvas.configure(bg=Constants.background_color.to_hex())

        # The current list objects are replaced wholesale when entries are
        # removed, so re-read them from the processor each frame.
        self.pedestrians = self.processor.get_pedestrians()
        self.vehicle_list = self.processor.get_vehicle_list()
        self.object_list = self.processor.get_object_list()

        # keep the live per-type counter in the legend up to date
        try:
            self.frame.update_legend_counts(self.vehicle_list)
        except Exception:
            pass

        # Imagery underneath everything, so the network reads as being drawn
        # on the place rather than beside it.
        if self.basemap_active():
            self._draw_basemap(g2d)

        if self.draw_roads and not reuse_static:
            self.draw_road_network(g2d)

        if self.view_3d:
            # The static half of the 3D frame ends here; vehicles and
            # trajectories from now on are tagged for per-frame deletion.
            g2d.begin_dynamic()
            self._static3d_key = static_key

        # Signal state changes every few steps, so the bars are dynamic
        # content -- drawn each frame, under the vehicles that cross them.
        # A trace replay has no live signal state to show.
        if self.draw_roads and not Parameters.TRACE_MODE:
            road_geometry.signal_bars(g2d, self.link_list, self.node_list,
                                      Parameters.pixel_per_meter)

        if self.draw_trajectories:
            self.draw_all_trajectories(g2d)

        if Parameters.TRACE_MODE:
            Utilities.draw_trace(self.trace_reader, g2d)
        else:
            if Parameters.across_pedestrian_mode:
                self.trace_writer.write("Current Pedestrians\n")
                for pedestrian in self.pedestrians:
                    g2d.begin_prop("pedestrian")
                    pedestrian.draw_mobile_pedestrian(
                        self.trace_writer, g2d, Parameters.pixel_per_strip,
                        Parameters.pixel_per_meter, Parameters.pixel_per_footpath_strip)
                    g2d.end_prop()
            self.trace_writer.write("Current Vehicles\n")
            for vehicle in self.vehicle_list:
                # The type is what picks the 3D model, so a bus is drawn as a
                # bus and a rickshaw gets its hood; in 2D this is a no-op.
                g2d.begin_prop("vehicle", vehicle.get_type())
                vehicle.draw_vehicle(self.trace_writer, g2d, Parameters.pixel_per_strip,
                                     Parameters.pixel_per_meter,
                                     Parameters.pixel_per_footpath_strip)
                g2d.end_prop()

            self.trace_writer.write("Current Objects\n")
            for obj in self.object_list:
                g2d.begin_prop("object", obj.object_type)
                obj.draw_object(self.trace_writer, g2d, Parameters.pixel_per_strip,
                                Parameters.pixel_per_meter,
                                Parameters.pixel_per_footpath_strip)
                g2d.end_prop()

            self.trace_writer.write("End Step\n")
            self.trace_writer.flush()

        if self.view_3d:
            g2d.flush()
        # Furniture last, so a vehicle can never be painted over the compass.
        self._draw_map_furniture(width, height)

    def _draw_map_furniture(self, width, height) -> None:
        """North arrow and scale bar, drawn in screen space.

        These are map furniture rather than part of the network, so they are
        painted straight onto the canvas and stay put while the view is panned
        or zoomed -- only the distance the scale bar represents changes.
        Networks are built with bearings measured from screen-up, so north is
        simply up -- except in the 3D view, where the camera can be swung round
        and the needle has to swing with it.

        Every item carries the ``furniture`` tag: the 3D view deletes only
        its dynamic items between frames when the camera is still, and the
        furniture has to be deleted and redrawn with them or the new vehicles
        would stack above the compass.
        """
        canvas = self.canvas
        ink, paper = "#12263a", "#ffffff"

        # --- imagery credit, bottom right ---
        # The ODbL asks for the credit wherever the tiles are shown, so it is
        # drawn with the furniture rather than left to the documentation.
        if self.basemap_active():
            canvas.create_text(width - 8, height - 6, anchor="se",
                               text=self._basemap.attribution,
                               fill="#33465c", font=("Segoe UI", 8),
                               tags="furniture")

        # --- north arrow, top right ---
        cx, cy = width - 46, 46
        bearing = -self.scene3d.camera.yaw if self.view_3d else 0.0
        cos_b, sin_b = math.cos(bearing), math.sin(bearing)

        def needle(x, y):
            """Rotate an offset from the dial centre by the camera bearing."""
            return cx + x * cos_b - y * sin_b, cy + x * sin_b + y * cos_b

        canvas.create_oval(cx - 26, cy - 26, cx + 26, cy + 26,
                           fill=paper, outline="#c7ccd3", tags="furniture")
        canvas.create_polygon(*needle(0, -19), *needle(-8, 11), *needle(0, 5),
                              *needle(8, 11), fill=ink, outline="",
                              tags="furniture")
        canvas.create_text(*needle(0, 17), text="N", fill=ink,
                           font=("Segoe UI", 10, "bold"), tags="furniture")

        if self.view_3d:
            # A scale bar means nothing under perspective -- the metres a
            # pixel covers change from the top of the frame to the bottom --
            # so the corner carries the controls instead.
            canvas.create_text(
                16, height - 16, anchor="sw", fill="#33465c",
                font=("Segoe UI", 9),
                text="3D view — drag to orbit · right-drag or Shift+drag to "
                     "pan · wheel to zoom · double-click to reset",
                tags="furniture")
            return

        # --- scale bar, bottom left ---
        px_per_m = Parameters.pixel_per_meter * self.scale
        if px_per_m <= 0:
            return
        # pick a round distance whose bar is a comfortable width on screen
        nice = None
        for candidate in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
            if candidate * px_per_m >= 90:
                nice = candidate
                break
        if nice is None:
            nice = 5000
        bar = nice * px_per_m
        if bar > width * 0.6:
            return
        x0, y0 = 20, height - 26
        canvas.create_rectangle(x0 - 8, y0 - 24, x0 + bar + 12, y0 + 12,
                                fill=paper, outline="#c7ccd3", tags="furniture")
        canvas.create_line(x0, y0, x0 + bar, y0, fill=ink, width=3,
                           tags="furniture")
        for x in (x0, x0 + bar):
            canvas.create_line(x, y0 - 6, x, y0 + 4, fill=ink, width=2,
                               tags="furniture")
        label = f"{nice} m" if nice < 1000 else f"{nice // 1000} km"
        canvas.create_text(x0 + bar / 2, y0 - 13, text=label, fill=ink,
                           font=("Segoe UI", 10, "bold"), tags="furniture")

    def draw_all_trajectories(self, g2d) -> None:
        # TODO
        from .statistics import Statistics
        if Parameters.simulation_end_time <= Parameters.simulation_step:
            colors = (Color.GREEN, Color.RED, Color.CYAN, Color.MAGENTA, Color.BLUE)
            for vs in Statistics.vehicle_stats:
                self.draw_trajectory(g2d, vs.get_trajectory(),
                                     colors[vs.get_vehicle_id()])

    @staticmethod
    def draw_trajectory(g2d, points, color) -> None:
        g2d.set_color(color)
        g2d.set_stroke(10)
        for i in range(len(points) - 1):
            if points[i] is None or points[i + 1] is None:
                continue
            g2d.draw_line(jint(points[i].x), jint(points[i].y),
                          jint(points[i + 1].x), jint(points[i + 1].y))

    def _node_label_point(self, node):
        """World (metre) point at which to draw a node's label."""
        return road_geometry.node_point(self.link_list, node)

    def _point_clear_of_roads(self, x_m, y_m) -> bool:
        """Whether a world (metre) point lands on no drawn road surface.

        Tested against the cached geometry's segment quads and junction
        patches -- the actual paint, not idealised arm directions, because
        fitted arms bend right after their mouths.
        """
        if self._road_geometry is None:
            return True
        ppm = Parameters.pixel_per_meter
        px, py = x_m * ppm, y_m * ppm
        quads, hulls = self._road_geometry[0], self._road_geometry[1]
        for xs, ys in quads:
            if road_geometry.point_in_polygon(px, py, xs, ys):
                return False
        for hxs, hys, _cx, _cy, _radius, _kerb in hulls:
            if road_geometry.point_in_polygon(px, py, hxs, hys):
                return False
        return True
    def draw_node_id(self, g2d, node) -> None:
        # Prefer a friendly name (input/node_names.txt) over the numeric id.
        name = Parameters.NODE_NAMES.get(node.get_id(), str(node.get_id()))
        x_m, y_m = self._node_label_point(node)
        g2d.set_font("Serif", 100)
        g2d.set_color(Color.BLACK)

        # Push the label well clear of the carriageway and join it back to the
        # junction with a leader line, so a name is never read as sitting on a
        # road it does not belong to.
        widest = 0.0
        for j in range(node.number_of_links()):
            link = self.link_list[node.get_link(j)]
            for k in range(link.get_number_of_segments()):
                widest = max(widest, link.get_segment(k).get_segment_width())
        # Junction names sit inside the network, so they need the most room.
        if node.number_of_links() > 1:
            clearance = widest * 2.0 + 26.0
        else:
            clearance = widest * 0.8 + 10.0

        if node.number_of_links() == 1:
            # A terminal is pushed outwards along its own road.
            link = self.link_list[node.get_link(0)]
            if link.get_up_node() == node.get_id():
                seg = link.get_first_segment()
                ox, oy = seg.get_end_x(), seg.get_end_y()
            else:
                seg = link.get_last_segment()
                ox, oy = seg.get_start_x(), seg.get_start_y()
            vx, vy = x_m - ox, y_m - oy          # points away from the junction
            length = math.hypot(vx, vy)
            dx, dy = (vx / length, vy / length) if length > 0 else (0.0, -1.0)
        else:
            # A junction is pushed into its widest empty quadrant -- the
            # direction furthest from every arm -- so neighbouring junctions
            # send their labels different ways instead of stacking.
            bearings = []
            for j in range(node.number_of_links()):
                link = self.link_list[node.get_link(j)]
                if link.get_up_node() == node.get_id():
                    seg = link.get_first_segment()
                    fx, fy = seg.get_end_x(), seg.get_end_y()
                else:
                    seg = link.get_last_segment()
                    fx, fy = seg.get_start_x(), seg.get_start_y()
                bearings.append(math.atan2(fx - x_m, -(fy - y_m)) % (2 * math.pi))
            # Candidate directions: the midpoint of every angular gap
            # between consecutive arms, widest gap first.  The widest gap
            # alone is not enough, and neither is probing idealised arm
            # rays -- a fitted arm bends right after its mouth, so a ray
            # from the mouth's own bearing says nothing about where the
            # road actually goes (Banani 27 put its name straight onto the
            # west approach that way).  Each candidate is therefore tested
            # against the *drawn* road: the label point and the reach of
            # the text to either side must land on no segment quad and no
            # junction patch.  The first direction that does wins; if none
            # does, the widest gap is the least bad.  The answer depends
            # only on the network, so it is cached per node.
            bearings.sort()
            candidates = []
            for k in range(len(bearings)):
                a = bearings[k]
                b = bearings[(k + 1) % len(bearings)]
                gap = (b - a) % (2 * math.pi)
                candidates.append((gap, (a + gap / 2.0) % (2 * math.pi)))
            candidates.sort(reverse=True)
            best = self._label_dirs.get(node.get_id())
            if best is None:
                best = candidates[0][1]
                half_text = (len(name) * 26.0
                             / Parameters.pixel_per_meter)   # ~half the name
                for _gap, mid in candidates:
                    px = x_m + math.sin(mid) * clearance
                    py = y_m - math.cos(mid) * clearance
                    if all(self._point_clear_of_roads(px + off, py)
                           for off in (-half_text, 0.0, half_text)):
                        best = mid
                        break
                self._label_dirs[node.get_id()] = best
            dx, dy = math.sin(best), -math.cos(best)

        lx = (x_m + dx * clearance) * Parameters.pixel_per_meter
        ly = (y_m + dy * clearance) * Parameters.pixel_per_meter

        # Leader drawn from the label back to the junction, with the arrowhead
        # on the junction end so it points at what it names.
        g2d.set_color(Color(90, 90, 90))
        g2d.set_stroke(1)
        near = 0.30 * clearance
        g2d.draw_line(jint(lx), jint(ly),
                      jint((x_m + dx * near) * Parameters.pixel_per_meter),
                      jint((y_m + dy * near) * Parameters.pixel_per_meter),
                      arrow=True)

        # Anchor the text on the side away from the junction, so a label placed
        # to the left runs leftwards instead of back across the road.
        if dx < -0.35:
            anchor = "se"
        elif dx > 0.35:
            anchor = "sw"
        else:
            anchor = "s"

        # Halo: the same text in white behind the label -- eight copies, the
        # diagonals included, so the shadow closes into a solid outline
        # instead of the pinholes four offsets leave at the corners.  This
        # is what keeps a name legible over the carriageway, the imagery and
        # other names on tight networks.
        halo = max(1.0, 1.6 / max(self.scale, 0.0001))
        g2d.set_color(Color.WHITE)
        for ox, oy in _HALO_OFFSETS:
            g2d.draw_string(name, jint(lx + ox * halo), jint(ly + oy * halo),
                            anchor)
        g2d.set_color(Color.BLACK)
        g2d.draw_string(name, jint(lx), jint(ly), anchor)

    def draw_link_name(self, g2d, link) -> None:
        """Draw a link's street name just off its carriageway.

        Falls back to the numeric id, which is what the per-link CSV columns
        are indexed by, so a link can always be matched between the picture and
        the statistics.  The label used to sit on the road midpoint, where it
        was buried under the traffic; ``road_geometry.link_label_offset``
        probes it off the kerb instead, and the answer is cached per link
        because it depends only on the drawn geometry.
        """
        name = Parameters.LINK_NAMES.get(link.get_id(), str(link.get_id()))
        count = link.get_number_of_segments()
        if count <= 0:
            return
        pt = self._link_label_pts.get(link.get_id())
        if pt is None:
            # ~half the name's reach at font 78, and its cap height, in
            # metres.
            ppm = Parameters.pixel_per_meter
            pt = road_geometry.link_label_offset(
                link, self._road_geometry, ppm,
                len(name) * 20.0 / ppm, text_up_m=62.0 / ppm)
            self._link_label_pts[link.get_id()] = pt
        x_m, y_m = pt
        lx = x_m * Parameters.pixel_per_meter
        ly = y_m * Parameters.pixel_per_meter

        g2d.set_font("Serif", 78)
        halo = max(1.0, 1.6 / max(self.scale, 0.0001))
        g2d.set_color(Color.WHITE)
        for ox, oy in _HALO_OFFSETS:
            g2d.draw_string(name, jint(lx + ox * halo), jint(ly + oy * halo),
                            "s")
        g2d.set_color(Color(40, 40, 40))
        g2d.draw_string(name, jint(lx), jint(ly), "s")

    def _draw_basemap(self, g2d) -> None:
        """Place the map imagery under the network.

        The image is cropped to what is on screen and scaled by whole numbers,
        both of which :mod:`dhakasim.basemap` handles.  The only thing decided
        here is where its top-left corner lands, and that goes through the same
        transform every road does.
        """
        base = self._basemap
        ppm = Parameters.pixel_per_meter
        left, top = g2d.to_screen(base.left * ppm, base.top * ppm)
        placed = base.scaled_region(left, top,
                                    self.canvas.winfo_width() or 1,
                                    self.canvas.winfo_height() or 1,
                                    self.scale, ppm)
        if placed is None:
            return
        photo, x, y = placed
        # Tk drops a photo the moment nothing references it, and the canvas
        # item does not count as a reference.
        self._basemap_photo = photo
        self.canvas.create_image(x, y, image=photo, anchor="nw")

    def set_paused(self, paused: bool) -> None:
        """Hold the run where it is, or let it carry on.

        Only the stepping stops.  The view still repaints, so panning, zooming
        and switching to the 3D camera all keep working on the held frame,
        which is most of the point of being able to stop.
        """
        self.paused = bool(paused)
        self.repaint()

    def toggle_basemap(self) -> None:
        """Show or hide the imagery.  Does nothing if none was fetched."""
        if self._basemap is None:
            return
        self.show_basemap = not self.show_basemap
        # Turning it back on re-snaps the zoom; turning it off frees it again.
        self.set_scale(self.scale)

    def draw_road_network(self, g2d) -> None:
        # The road surface is painted by road_geometry.paint, which the report's
        # animation also calls, so the two pictures cannot diverge.  Cache the
        # geometry: it depends only on the network and pixelPerMeter, and
        # rebuilding it every frame is wasteful.
        # Two shapes, not one: over imagery the carriageway is drawn wider, so
        # the cache is keyed by which of the two is wanted.  Toggling the map
        # rebuilds; it does not silently reuse the other one's geometry.
        widen = (Constants.OVERLAY_WIDEN_METRES if self.basemap_active()
                 else 0.0)
        if self._road_geometry is None or self._road_geometry_widen != widen:
            self._road_geometry = road_geometry.build(
                self.link_list, self.node_list, Parameters.pixel_per_meter,
                widen=widen)
            self._road_geometry_widen = widen
            # The label placements were probed against the old shapes.
            self._label_dirs = {}
            self._link_label_pts = {}
        # Over imagery the carriageway is washed rather than painted: same
        # shapes, but a pale fill at part opacity, so the road still reads as
        # a surface without hiding the very thing the imagery is there for.
        road_geometry.paint(g2d, self.link_list, self.node_list,
                            Parameters.pixel_per_meter, self._road_geometry,
                            fill=not self.basemap_active())

        # Street names first, so a junction name drawn afterwards wins the
        # overlap where a short link's label reaches its node.  A network
        # whose imagery already carries its own street names turns the whole
        # set off with ShowLabels in defaults.txt.
        if Parameters.SHOW_LABELS:
            for link in self.link_list:
                self.draw_link_name(g2d, link)

            for node in self.node_list:
                g2d.set_color(Color.BLACK)
                self.draw_node_id(g2d, node)

    # ---- timer / events --------------------------------------------------

    def _on_timer(self) -> None:
        try:
            if not self.paused:
                self.action_performed()
        finally:
            # Re-armed either way.  Cancelling the timer while paused would
            # mean rebuilding it on resume and getting the bookkeeping in
            # dispose() wrong; skipping the step is the whole of pausing.
            if not self._finished:
                self._timer = self.canvas.after(max(1, Parameters.simulation_speed),
                                               self._on_timer)

    def action_performed(self) -> None:
        if Parameters.show_progress_slider is not None:
            Parameters.show_progress_slider.set_value(Parameters.simulation_step)
        if not Parameters.TRACE_MODE:
            self.trace_writer.write(f"SimulationStep: {Parameters.simulation_step}\n")

        self.processor.manual_process(self)

    def on_simulation_finished(self) -> None:
        self._finished = True
        if self.trace_writer is not None:
            self.trace_writer.flush()
        self.frame.on_simulation_finished()

    def dispose(self) -> None:
        """Stop the run and release what it holds.

        Called when the view is torn down -- either to go back to the option
        form or on shutdown -- so a half-finished run cannot keep stepping in
        the background and trace.txt is not left open.
        """
        self._finished = True
        if self._timer is not None:
            try:
                self.canvas.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        for handle in (self.trace_writer, self.trace_reader):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        self.trace_writer = None
        self.trace_reader = None
        if self._basemap is not None:
            self._basemap.forget()
        self._basemap_photo = None

    def mouse_pressed(self, event) -> None:
        self._reference_x = event.x
        self._reference_y = event.y

    def mouse_dragged(self, event) -> None:
        dx = event.x - self._reference_x
        dy = event.y - self._reference_y
        self._reference_x = event.x
        self._reference_y = event.y
        if self.view_3d:
            # Shift turns the orbit into a pan, the convention every 3D viewer
            # uses; right-drag does the same for a two-button mouse.
            if event.state & 0x0001:
                self.scene3d.camera.pan(dx, dy, self.scene3d.focal)
            else:
                self.scene3d.camera.orbit(dx, dy)
            if not Parameters.TRACE_MODE:
                self.repaint()
            return
        self.translate_x += dx * 30
        self.translate_y += dy * 30
        if not Parameters.TRACE_MODE:
            self.repaint()
        if Parameters.DEBUG_MODE:
            print(f"{self.translate_x} {self.translate_y}")

    def mouse_dragged_right(self, event) -> None:
        if not self.view_3d:
            return
        self.scene3d.camera.pan(event.x - self._reference_x,
                                event.y - self._reference_y,
                                self.scene3d.focal)
        self._reference_x = event.x
        self._reference_y = event.y
        if not Parameters.TRACE_MODE:
            self.repaint()

    def mouse_double_clicked(self, event) -> None:
        if not self.view_3d:
            return
        self.scene3d.camera.reset()
        self.repaint()

    def fit_scale(self, width: int, height: int) -> float:
        """The 2D scale that frames the whole network in a canvas this size.

        Multiplicative headroom rather than a fixed margin, because the
        networks span two orders of magnitude: a surveyed junction is a few
        hundred metres across and the BUET-DU-DMC demo is three kilometres.
        Starting every run from this fit is what makes a single junction open
        at a readable ~100 m framing instead of a wall of carriageway.
        """
        bounds = basemap_module.network_bounds(self.link_list, margin=30.0)
        if not bounds or width <= 1 or height <= 1:
            return self.scale
        x0, y0, x1, y1 = bounds
        ppm = Parameters.pixel_per_meter
        w, h = (x1 - x0) * ppm, (y1 - y0) * ppm
        if w <= 0 or h <= 0:
            return self.scale
        return min(self.ZOOM_MAX, max(self.ZOOM_MIN, min(width / w, height / h)))

    def reset_view(self) -> None:
        """Back to the whole-network framing: home pan, home scale."""
        self.translate_x, self.translate_y = self._home_translate
        if self.view_3d:
            self.scene3d.camera.reset()
        self.set_scale(self.home_scale)

    def mouse_wheel_moved(self, event) -> None:
        notches = -1 if event.delta > 0 else 1
        if self.view_3d:
            self.scene3d.camera.zoom(notches)
            self.repaint()
            return
        # Multiplicative, not additive: a fixed 0.004 step was a whole zoom
        # level near the bottom of the range and imperceptible near the top --
        # and with imagery on, a step smaller than the ladder's next rung
        # snapped straight back to where it started, which read as the zoom
        # being broken.  15% a notch clears a rung from anywhere.
        factor = 1.15 if notches < 0 else 1.0 / 1.15
        # Through set_scale rather than straight onto self.scale, so a wheel
        # zoom lands on the imagery's ladder like every other zoom does.
        self.set_scale(min(self.ZOOM_MAX,
                           max(self.ZOOM_MIN, self.scale * factor)))
        self.frame.sync_zoom_slider(self.scale)


#: The start screen's palette.  Warm charcoal rather than the usual near-black:
#: everything this program draws is warm -- brick, dust, orange rickshaws --
#: and a blue-grey panel in front of that reads as belonging to a different
#: application.  The accent is not a taste decision either.  It is lifted from
#: ``Constants.VEHICLE_TYPE_COLORS[7]``, the green this simulator has always
#: painted a CNG, so the colour that means "go" on this screen is the same one
#: that means "go" in the picture the screen leads to.
#: The surfaces are glass: each one carries a light edge along its top and a
#: shadow along its bottom, because tkinter has no per-widget alpha and a
#: highlight is what translucency actually looks like.  ``glass_hi`` and
#: ``glass_lo`` are those two hairlines and ``glow`` is the light behind the
#: title.  The explanatory text was two steps too dark to read against the
#: panel -- ``muted`` and ``faint`` are both a long way up from where they
#: started, and the whole palette is a shade lighter to match.
_UI = {
    "ground": "#131211",
    "panel": "#242120",
    "band": "#302C29",
    "seg_off": "#3A3634",
    "seg_hover": "#4A4542",
    "edge": "#443F3C",
    "glass_hi": "#524B47",
    "glass_lo": "#0C0B0A",
    "glow": "#26352C",
    "accent": "#1EA046",
    "accent_hover": "#25B851",
    "accent_text": "#5BD986",
    "on_accent": "#08160D",
    "text": "#F4F1ED",
    "muted": "#C3BBB4",
    "faint": "#948B84",
}


def _mix(colour, towards, amount):
    """Blend two ``#rrggbb`` colours.  Every gloss on this screen is one.

    A lit edge is the surface's own colour lifted towards white, so a control
    that changes colour -- selected, hovered -- gets its highlight for free
    rather than needing a second table of colours to keep in step with the
    first.
    """
    a, b = colour.lstrip("#"), towards.lstrip("#")
    return "#" + "".join(
        "%02X" % round(int(a[i:i + 2], 16) * (1.0 - amount)
                       + int(b[i:i + 2], 16) * amount)
        for i in (0, 2, 4))


def _glass(surface, top=None, bottom=None):
    """Give a surface the two hairlines that read as a pane of glass.

    Glass is not a flat fill: it catches the light along its top edge and
    drops a shadow along its bottom one.  Tk cannot composite, so the two
    edges *are* the effect.  They go on with ``place``, which does not
    disturb whichever geometry manager the surface's real children use --
    that is the whole reason this can be applied to a finished widget.
    """
    for colour, offset, rely in ((top or _UI["glass_hi"], 0, 0.0),
                                 (bottom or _UI["glass_lo"], -1, 1.0)):
        tk.Frame(surface, background=colour, height=1, borderwidth=0,
                 highlightthickness=0).place(x=0, y=offset, rely=rely,
                                             relwidth=1)


class _ToolButton(tk.Label):
    """A push button in the start screen's dress, for the run toolbar.

    Not ``ttk.Button`` for the same reason the start screen avoids ttk
    everywhere: the Windows theme paints ttk from the OS and ignores every
    colour it is given, so a ttk toolbar stays light grey however the rest of
    the window is dressed.  A label with the same fills, gloss and hover the
    start screen's buttons use keeps the two screens one application.

    ``configure(text=...)`` and ``configure(state="disabled"/"normal")`` work
    as they do on the ttk button this replaces, so the callers that flip the
    pause caption or enable the report button did not have to change.
    """

    def __init__(self, parent, text="", command=None, primary=False):
        super().__init__(
            parent, text=text, font=("Segoe UI Semibold", 10),
            background=_UI["accent"] if primary else _UI["seg_off"],
            foreground=_UI["on_accent"] if primary else _UI["text"],
            disabledforeground=_UI["faint"],
            padx=14, pady=5, cursor="hand2", takefocus=True,
            highlightthickness=2, highlightbackground=_UI["band"],
            highlightcolor=_UI["accent_hover"])
        self._primary = primary
        self._command = command
        self._gloss = tk.Frame(self, height=1, borderwidth=0,
                               highlightthickness=0)
        self._gloss.place(x=0, y=0, relwidth=1)
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.bind(sequence, self._fire)
        self.bind("<Enter>", lambda _e: self._paint(True))
        self.bind("<Leave>", lambda _e: self._paint(False))
        self._paint(False)

    def _fire(self, _event):
        if self._command is not None and str(self.cget("state")) != "disabled":
            self._command()

    def _paint(self, hovering):
        if str(self.cget("state")) == "disabled":
            colour = _UI["band"]
        elif self._primary:
            colour = _UI["accent_hover"] if hovering else _UI["accent"]
        else:
            colour = _UI["seg_hover"] if hovering else _UI["seg_off"]
        self.configure(background=colour)
        self._gloss.configure(background=_mix(colour, "#FFFFFF", 0.24))

    def configure(self, cnf=None, **kw):
        result = super().configure(cnf, **kw)
        if "state" in kw:
            # The fills are painted, not themed, so a state change has to
            # repaint them; the foreground follows from disabledforeground.
            self._paint(False)
        return result

    config = configure


class _Dropdown(tk.Frame):
    """A value and a menu, built from plain Tk widgets.

    Not a ``ttk.Combobox``: under the Windows theme ttk draws the field from
    the OS and ignores the colours given to it, so a combobox on this screen
    comes out white.  A ``tk.Menu`` takes colours, and twenty-five hours is a
    list rather than a strip -- putting them all on screen at once cost more
    height than the whole Control section.
    """

    def __init__(self, parent, options, variable, width=22,
                 font=("Segoe UI", 12), pad=(12, 7)):
        super().__init__(parent, background=_UI["panel"])
        self._var = variable
        self._button = tk.Label(
            self, textvariable=variable, font=font, width=width,
            anchor="w", padx=pad[0], pady=pad[1], cursor="hand2",
            takefocus=True, background=_UI["seg_off"], foreground=_UI["text"],
            highlightthickness=2, highlightbackground=_UI["edge"],
            highlightcolor=_UI["accent"])
        self._button.pack(side="left")
        _glass(self._button, top=_mix(_UI["seg_off"], "#FFFFFF", 0.24))
        caret = tk.Label(self, text="\u25be",
                         font=(font[0], max(8, font[1] - 2)),
                         padx=max(6, pad[0] - 2), pady=pad[1], cursor="hand2",
                         background=_UI["seg_off"], foreground=_UI["muted"])
        caret.pack(side="left", padx=(1, 0))

        self._menu = tk.Menu(self, tearoff=0, background=_UI["seg_off"],
                             foreground=_UI["text"],
                             activebackground=_UI["accent"],
                             activeforeground=_UI["on_accent"],
                             borderwidth=0, activeborderwidth=0,
                             font=(font[0], max(8, font[1] - 1)))
        for index, option in enumerate(options):
            # Twenty-five hours in one column runs off a short screen; break
            # it into columns of twelve so the menu stays inside the window.
            self._menu.add_command(
                label=option, columnbreak=1 if index and index % 13 == 0 else 0,
                command=lambda o=option: variable.set(o))

        for widget in (self._button, caret):
            for sequence in ("<Button-1>", "<Return>", "<space>"):
                widget.bind(sequence, self._open)
            widget.bind("<Enter>", lambda _e: self._paint(True))
            widget.bind("<Leave>", lambda _e: self._paint(False))
        self._caret = caret

    def _paint(self, hovering):
        colour = _UI["seg_hover"] if hovering else _UI["seg_off"]
        self._button.configure(background=colour)
        self._caret.configure(background=colour)

    def _open(self, _event=None):
        self._menu.tk_popup(self._button.winfo_rootx(),
                            self._button.winfo_rooty()
                            + self._button.winfo_height())


class _Segmented(tk.Frame):
    """A row of choices with the current one filled in.

    The control idiom of the whole screen, and not borrowed for its own sake:
    this simulator has no lanes.  A carriageway is a row of half-metre strips
    and a vehicle occupies one of them, so a row of cells with one filled is
    the shape the model is already made of.

    Rendered *from* its ``StringVar`` rather than merely writing to it, so a
    caller that sets the variable -- ``apply_network_defaults`` does, whenever
    the junction changes -- redraws this without knowing it exists.

    A value matching none of the offered choices is not dropped.  It is added
    as an extra cell and selected, because a surveyed network is entitled to a
    speed limit nobody thought to offer, and silently snapping it to the
    nearest button would change the run without saying so.

    *options* are values, or ``(value, label)`` when the two differ: the hour
    strip shows ``08`` and stores ``08:00 - 09:00``, which is what
    ``start_simulation`` looks up.
    """

    def __init__(self, parent, options, variable, command=None, columns=None,
                 font=("Segoe UI Semibold", 11), pad=(13, 6), wheel=None,
                 keep_unknown=True):
        super().__init__(parent, background=_UI["panel"])
        # Cells are destroyed and rebuilt whenever the value changes, which
        # takes their bindings with them.  The wheel one has to come back or
        # the form stops scrolling over whichever row was last touched.
        self._wheel = wheel
        # Off when several of these share one variable, as the two junction
        # groups do: to the Multi row a Dhaka junction is not an unoffered
        # value worth keeping, it is the other row's business, and keeping it
        # drew the selected network twice.
        self._keep_unknown = keep_unknown
        # Not ``self._options``: that is ``tkinter.Misc``'s own method, and
        # shadowing it breaks every grid call this widget makes.
        self._choices = [o if isinstance(o, tuple) else (str(o), str(o))
                         for o in options]
        self._var = variable
        self._command = command
        self._columns = columns
        self._font = font
        self._pad = pad
        self._cells = []
        self._render()
        # Held so it can be given back.  The whole form is rebuilt whenever
        # the window changes density, and a trace left behind by a destroyed
        # widget fires into nothing the next time the variable is written.
        self._trace = variable.trace_add("write", lambda *_a: self._render())
        self.bind("<Destroy>", self._forget)

    def _forget(self, event):
        # <Destroy> arrives for every descendant too, and this is only the
        # one occasion that matters.
        if event.widget is self and self._trace is not None:
            self._var.trace_remove("write", self._trace)
            self._trace = None

    @staticmethod
    def _same(a, b):
        """Equal as text, or as numbers when both are numbers.

        ``0.5`` and ``0.50`` are the same strip width, and the parameter file
        and this form do not always agree on how to spell one.
        """
        if a == b:
            return True
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False

    def _render(self):
        wanted = self._var.get()
        options = list(self._choices)
        if (self._keep_unknown and wanted
                and not any(self._same(v, wanted) for v, _l in options)):
            options.append((wanted, wanted))

        if [v for v, _l in options] != [v for v, _l, _c in self._cells]:
            for _v, _l, cell in self._cells:
                cell.destroy()
            self._cells = []
            width = self._columns or len(options)
            for index, (value, label) in enumerate(options):
                cell = tk.Label(self, text=label, font=self._font,
                                padx=self._pad[0], pady=self._pad[1],
                                cursor="hand2", takefocus=True,
                                highlightthickness=2,
                                highlightbackground=_UI["panel"],
                                highlightcolor=_UI["accent_hover"])
                cell.grid(row=index // width, column=index % width,
                          padx=(0, 3), pady=(0, 3), sticky="ew")
                # One lit pixel along the top edge, which is the whole
                # difference between a flat rectangle and a raised one.
                # ``_paint`` recolours it, so it follows the selection.
                cell.gloss = tk.Frame(cell, height=1, borderwidth=0,
                                      highlightthickness=0)
                cell.gloss.place(x=0, y=0, relwidth=1)
                for sequence in ("<Button-1>", "<Return>", "<space>"):
                    cell.bind(sequence, lambda _e, v=value: self._choose(v))
                cell.bind("<Enter>", lambda _e, c=cell: self._paint(c, True))
                cell.bind("<Leave>", lambda _e, c=cell: self._paint(c, False))
                cell.bind("<FocusIn>", lambda _e, c=cell: self._paint(c, False))
                cell.bind("<FocusOut>", lambda _e, c=cell: self._paint(c, False))
                if self._wheel is not None:
                    self._wheel(cell)
                self._cells.append((value, label, cell))
            if self._columns:
                for column in range(width):
                    self.columnconfigure(column, weight=1, uniform="seg")

        for _value, _label, cell in self._cells:
            self._paint(cell, False)

    def _paint(self, cell, hovering):
        value = next(v for v, _l, c in self._cells if c is cell)
        chosen = self._same(value, self._var.get())
        if chosen:
            background = _UI["accent_hover"] if hovering else _UI["accent"]
            cell.configure(background=background, foreground=_UI["on_accent"])
        else:
            background = _UI["seg_hover"] if hovering else _UI["seg_off"]
            cell.configure(background=background, foreground=_UI["text"])
        cell.gloss.configure(background=_mix(background, "#FFFFFF", 0.24))

    def _choose(self, value):
        self._var.set(value)
        if self._command is not None:
            self._command()


#: Four ways to draw the same seventeen settings, roomiest first.
#:
#: The screen picks one by *measuring*, not by asking how big the monitor is:
#: a maximised window is the screen less its title bar and its taskbar, and
#: both of those move with the DPI setting, the taskbar's position and
#: whether it hides itself.  ``_fit_to_window`` walks this list from the top
#: and stops at the first entry the window can show whole.
#:
#: The last entry drops the captions, and it has to.  A 1280x720 desktop
#: leaves about 1070 pixels of settings across two columns and the roomiest
#: layout wants 1570 -- a third more than there is -- and no amount of
#: shaving fonts and padding closes a gap that size while every row is
#: carrying two lines of explanation.  So at that size the explanations move
#: to a hint line in the footer, which costs one row's height for all
#: seventeen of them instead of two lines each.
_DENSITIES = (
    # row_pad was 6 and pad (18, 22): the signal-timing line under the
    # Signal control strip costs one entry's height, and roomy sat exactly
    # at the 1080p budget, so the slack came out of the padding rather than
    # out of a rung.
    dict(name="roomy",
         header=96, title=30, tagline=12,
         band=11, note=11, label=13, caption=11, group=10,
         cell=(12, 11), cell_pad=((16, 8), (13, 6)),
         value=12, unit=11, button=13, button_pad=(28, 12),
         field=330, wrap=300, row_pad=4, band_gap=6, pad=(18, 12),
         foot_pad=12, tail=10, tiles=None),
    dict(name="compact",
         header=94, title=26, tagline=11,
         band=10, note=10, label=12, caption=10, group=9,
         cell=(11, 10), cell_pad=((13, 7), (11, 5)),
         value=11, unit=10, button=12, button_pad=(22, 10),
         field=286, wrap=258, row_pad=5, band_gap=8, pad=(15, 18),
         foot_pad=10, tail=10, tiles=None),
    dict(name="dense",
         header=80, title=22, tagline=10,
         band=9, note=9, label=11, caption=9, group=8,
         cell=(10, 9), cell_pad=((11, 6), (9, 4)),
         value=10, unit=9, button=11, button_pad=(18, 8),
         field=246, wrap=222, row_pad=4, band_gap=6, pad=(12, 14),
         foot_pad=8, tail=8, tiles=None),
    # tiles=3, not 2: nine networks at two abreast wrap to five tile rows,
    # which is ~25 px more than a 720p viewport behind a deep taskbar has.
    dict(name="minimal",
         header=60, title=19, tagline=9,
         band=9, note=None, label=11, caption=None, group=8,
         cell=(10, 9), cell_pad=((9, 4), (8, 3)),
         value=10, unit=9, button=10, button_pad=(16, 5),
         field=196, wrap=None, row_pad=2, band_gap=4, pad=(11, 12),
         foot_pad=5, tail=4, tiles=3),
)


class OptionPanel:
    """The start screen: pick a junction, press Start.

    Laid out as a settings screen -- banded sections, one setting to a row,
    the control on the right -- rather than as the flat seventeen-row form it
    was.  Three things that look like cosmetics and are not:

    *Start lives in a footer that does not scroll.*  An earlier version put it
    at the top of the form for the same reason: the settings are taller than a
    short window, and a button below them is a button nobody can see.  Pinning
    it outside the scroller keeps it visible without putting it in front of
    the thing it starts.

    *Each caption sits under its own label rather than in a third column.*  In
    a third column a wrapped caption pushed the next row down by however many
    lines it took, so captions had to be one line and the form still ran wide.
    Stacked, they can wrap and cost the control column nothing.

    *The whole form is drawn again whenever the window changes size enough to
    want a different density.*  That is why every variable is made once in
    ``_make_vars`` and every widget in ``_init_components``: a rebuild throws
    away the widgets and keeps the values, so nothing the reader has typed is
    lost when their window is resized under them.
    """

    #: How green time is shared out.  Values are what ``Parameters.SIGNAL_MODE``
    #: takes; the labels are what a reader of the paper would call them.
    SIGNAL_CHOICES = {
        "Fixed time": "fixed",
        "Biased random": "biased-random",
        "Multi-objective v1": "moo-v1",
        "Multi-objective v2": "moo-v2",
    }

    #: Whether the 3D view is on and how it draws are one decision to anyone
    #: standing in front of the form, so they are one control.
    VIEW_CHOICES = {
        "Off": (False, "line"),
        "Line art": (True, "line"),
        "Solid": (True, "solid"),
    }

    #: What this program is, in the words its own paper uses.
    TAGLINE = ("A Non-Lane based heterogeneous Micro Simulation "
               "for Dhaka City")

    #: Which junctions are one intersection and which are a network of them.
    #: Kakrail is a two-junction corridor and sits with the single ones
    #: because that is how it is studied -- a pair of adjacent signals, not a
    #: grid.  Any network not named here joins the second group.
    SINGLE_JUNCTIONS = ("banani_23", "banani_27", "bijoy_sarani",
                        "kakrail_corridor")

    def __init__(self, dhaka_sim_frame, parent):
        self.dhaka_sim_frame = dhaka_sim_frame
        self.frame = tk.Frame(parent, background=_UI["ground"])
        self._level = 0
        self._busy = False
        self._waiting = 0
        self._viewport = None
        self._hint = None
        self._hint_rows = []
        self._make_vars()
        self.form = self._build_shell()
        self._build_form()
        self.frame.bind("<Configure>", lambda _e: self._fit_to_window())
        self.frame.update_idletasks()
        self._fit_to_window(force=True)

    @property
    def _d(self):
        """The sizes the form is currently being drawn at."""
        return _DENSITIES[self._level]

    # ---- the shell: header, scrolling settings, pinned footer -------------

    def _build_shell(self):
        # A canvas, not two labels, because the light behind the title has
        # to be painted: tkinter has no per-widget alpha, so a glow can only
        # be drawn, and a label over it would punch an opaque rectangle
        # straight through the middle of it.  Canvas text does not.
        header = tk.Canvas(self.frame, height=self._d["header"],
                           highlightthickness=0, bd=0,
                           background=_UI["ground"])
        header.pack(fill="x")
        self._header = header
        header.bind("<Configure>", self._paint_header)

        self._footer = tk.Frame(self.frame, background=_UI["band"])
        self._footer.pack(side="bottom", fill="x")

        body = tk.Frame(self.frame, background=_UI["ground"])
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, bd=0,
                           background=_UI["ground"])
        # A classic tk scrollbar, not a ttk one: under the Windows theme ttk
        # draws the trough from the OS and ignores every colour given to it,
        # so the only way to a dark bar without switching the whole app to
        # "clam" -- which would restyle the simulation panel too -- is the
        # widget that predates themes.
        bar = tk.Scrollbar(body, orient="vertical", command=canvas.yview,
                           background=_UI["seg_off"],
                           activebackground=_UI["seg_hover"],
                           troughcolor=_UI["ground"], borderwidth=0,
                           highlightthickness=0, elementborderwidth=0,
                           width=14)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # A full-width tray holding the form, rather than a centred canvas
        # item.  A canvas window item resolves its anchor against the width it
        # had when its coordinates were last set, so an item anchored to its
        # own middle drifts off to one side as the form grows underneath it,
        # and reports a bounding box that disagrees with where the widget
        # actually is.  Stretching the tray to the canvas and centring inside
        # it with weighted spacer columns is decided by the geometry manager
        # instead, which cannot fall out of step.
        tray = tk.Frame(canvas, background=_UI["ground"])
        window = canvas.create_window(0, 0, window=tray, anchor="nw")
        tray.columnconfigure(0, weight=1)
        tray.columnconfigure(2, weight=1)
        form = tk.Frame(tray, background=_UI["ground"])
        form.grid(row=0, column=1, sticky="n")

        # Two columns rather than one, because the whole point is to fit the
        # screen without scrolling: stacked, these settings run to some 1700
        # pixels and a maximised window has 800 at best and 460 at worst.
        self._columns = []
        for _index in (0, 1):
            panel = tk.Frame(form, background=_UI["panel"],
                             highlightthickness=1,
                             highlightbackground=_UI["edge"],
                             highlightcolor=_UI["edge"])
            panel.columnconfigure(1, weight=1)
            self._columns.append(panel)
        self._rows = [0, 0]
        self._col = 0
        self._stacked = None

        def fit(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            self._fit_to_window()

        def grew(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            self._show_bar()

        canvas.bind("<Configure>", fit)
        tray.bind("<Configure>", grew)
        self._canvas = canvas
        self._tray = tray
        self._bar = bar
        return form

    def _show_bar(self):
        """The whole point of the density ladder is that this never appears,
        and an inert bar says there is more below when there is not.

        Asked on the *tray's* resize as well as the canvas's: the canvas
        stops resizing long before the settings have finished being built, so
        a check made only there decides while the form is still short.
        """
        needed = self._tray.winfo_reqheight() > self._canvas.winfo_height()
        if needed and not self._bar.winfo_ismapped():
            self._bar.pack(side="right", fill="y", before=self._canvas)
        elif not needed and self._bar.winfo_ismapped():
            self._bar.pack_forget()

    def _fit_to_window(self, force=False) -> None:
        """Draw the settings at the roomiest density this window can show.

        Measured, never inferred from the screen size: a maximised window is
        the desktop less its title bar and its taskbar, and the DPI setting,
        the taskbar's edge and its auto-hiding all move that number.  So the
        list is walked from the top and the first entry whose form fits
        inside the canvas wins.

        The search always restarts at the roomiest entry rather than stepping
        down from wherever it is, because a window that has just been
        *un*-maximised has to be able to climb back.  It keys off the
        panel's own size rather than the canvas's, which is the only stable
        thing here -- the canvas's height is a consequence of the density,
        since a denser layout means a shorter header, so keying off it would
        chase itself.
        """
        size = (self.frame.winfo_width(), self.frame.winfo_height())
        if size[0] < 2 or self._busy or (size == self._viewport and not force):
            return
        if self._canvas.winfo_width() < 2:
            # The panel has its size but the canvas inside it has not been
            # laid out yet, and this runs from the panel's own <Configure>,
            # inside the very geometry pass that would give the canvas one.
            # Measuring here judges the roomiest layout against a one-pixel
            # canvas, finds it too wide, stacks it, and steps down a rung it
            # never needed.  Come back once Tk has finished the pass.
            if self._waiting < 20 and self.frame.winfo_ismapped():
                self._waiting += 1
                self.frame.after_idle(
                    lambda: self._fit_to_window(force=True))
            return
        self._waiting = 0
        self._busy = True
        try:
            for level in range(len(_DENSITIES)):
                if level != self._level:
                    self._level = level
                    self._build_form()
                # The columns are a different width at every density, so
                # whether they stand side by side has to be decided again --
                # ``_arrange`` short-circuits on its last answer, and a
                # stale one here reads as "stacked", which never fits and
                # sends the search all the way to the bottom of the list.
                self._stacked = None
                self.frame.update_idletasks()
                self._arrange(self._canvas.winfo_width())
                self.frame.update_idletasks()
                if (self._tray.winfo_reqheight()
                        <= self._canvas.winfo_height()):
                    break
            self._viewport = size
        finally:
            self._busy = False
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._show_bar()

    def _build_form(self) -> None:
        """Draw every setting again at the current density.

        The widgets go, the variables stay.  Everything the reader has typed
        lives in a ``StringVar`` made once in ``_make_vars``, so a rebuild is
        invisible except for the sizes.
        """
        for column in self._columns:
            for child in column.winfo_children():
                child.destroy()
            column.columnconfigure(0, minsize=self._d["field"])
            _glass(column)
        for child in self._footer.winfo_children():
            child.destroy()
        _glass(self._footer)
        self._header.configure(height=self._d["header"])
        self._rows = [0, 0]
        self._col = 0
        self._stacked = None
        self._hint = None
        self._hint_rows = []
        self._init_components()
        self._bind_wheel(self._tray)
        self._bind_wheel(self._canvas)
        for widgets, text in self._hint_rows:
            for widget in widgets:
                self._bind_hint(widget, text)
        self._paint_header()

    def _bind_hint(self, widget, text) -> None:
        """Show one row's explanation in the footer while the pointer is on
        it.  Added to whatever is already bound, never in place of it: the
        cells paint their own hover state on the same event."""
        widget.bind("<Enter>",
                    lambda _e: self._hint.configure(text=text), add="+")
        widget.bind("<Leave>",
                    lambda _e: self._hint.configure(text=""), add="+")
        for child in widget.winfo_children():
            self._bind_hint(child, text)

    def _paint_header(self, _event=None) -> None:
        """Draw the title, the line under it, and the light behind them.

        The glow is concentric filled ovals worked from the outside in, and
        the outermost one is the ground colour itself, so the bloom has no
        edge to give it away.  Sixty steps put the banding below what the eye
        separates at this contrast; a dozen would show rings.  It is green
        because the accent is -- the colour this simulator has always painted
        a CNG -- so the light behind the name is the light on Start.
        """
        canvas = self._header
        width, height = canvas.winfo_width(), self._d["header"]
        if width < 2:
            return
        canvas.delete("all")
        steps = 60
        centre_x, centre_y = width / 2.0, height * 0.52
        for index in range(steps):
            towards = index / (steps - 1.0)
            radius_x = width * (0.62 - 0.54 * towards)
            radius_y = height * (1.5 - 1.3 * towards)
            canvas.create_oval(centre_x - radius_x, centre_y - radius_y,
                               centre_x + radius_x, centre_y + radius_y,
                               outline="", fill=_mix(_UI["ground"],
                                                     _UI["glow"], towards))
        canvas.create_text(centre_x, height * 0.38, text="DhakaSim",
                           font=("Segoe UI Semibold", self._d["title"]),
                           fill=_UI["text"])
        canvas.create_text(centre_x, height * 0.74, text=self.TAGLINE,
                           font=("Segoe UI", self._d["tagline"]),
                           fill=_UI["muted"])
        canvas.create_line(0, height - 1, width, height - 1,
                           fill=_UI["edge"])

    def _arrange(self, width) -> None:
        """Side by side while there is room, stacked when there is not.

        The form has no horizontal scrollbar -- it never had one -- so a
        two-column layout wider than the window would simply lose its right
        half.  Measured rather than guessed at: the columns ask for what they
        ask for, and 60 px covers the gutter and the scrollbar.

        Stacking is a last resort and not a rung of the density ladder.  It
        doubles the height, so ``_fit_to_window`` will keep stepping down
        past it, and a denser layout is often narrow enough to come back
        apart again -- which is exactly what should happen on a 1280-wide
        screen.
        """
        if not self._columns:
            return
        wanted = sum(c.winfo_reqwidth() for c in self._columns) + 60
        stacked = width < wanted
        if stacked == self._stacked:
            return
        self._stacked = stacked
        for index, panel in enumerate(self._columns):
            if stacked:
                panel.grid(row=index, column=0, sticky="n",
                           pady=(0, 18 if index == 0 else 0))
            else:
                # Both panes stretch to the taller of the two, so they
                # end on the same line: one pane stopping short of its
                # neighbour reads as an unfinished layout, not as a shorter
                # list of settings.
                panel.grid(row=0, column=index, sticky="ns",
                           padx=(0, 18 if index == 0 else 0))

    def _bind_wheel(self, widget) -> None:
        """Let the wheel scroll the settings from anywhere over them.

        Tk delivers a wheel event to the widget under the pointer and walks
        that widget's own bindtags, so a binding on the containing frame never
        fires once the pointer is over an entry box.  Binding each descendant
        covers the whole form without reaching for ``bind_all``, which would
        still be in force during the run and fight the canvas's own zoom.
        """
        widget.bind("<MouseWheel>", lambda e: self._canvas.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        for child in widget.winfo_children():
            self._bind_wheel(child)

    # ---- the pieces a settings screen is made of -------------------------

    def _use(self, column):
        """Send the next bands and rows to one of the two columns."""
        self._col = column

    def _band(self, title, note=""):
        level = self._d
        column = self._columns[self._col]
        self._rows[self._col] += 1
        band = tk.Frame(column, background=_UI["band"])
        band.grid(row=self._rows[self._col], column=0, columnspan=2,
                  sticky="ew",
                  pady=(level["band_gap"] if self._rows[self._col] > 1 else 0,
                        0))
        _glass(band)
        tk.Label(band, text=title.upper(),
                 font=("Segoe UI Semibold", level["band"]),
                 background=_UI["band"], foreground=_UI["accent_text"],
                 padx=level["pad"][0], pady=max(4, level["row_pad"] + 1)
                 ).pack(side="left")
        if note and level["note"]:
            tk.Label(band, text=note, font=("Segoe UI", level["note"]),
                     background=_UI["band"], foreground=_UI["muted"]).pack(
                side="left", pady=max(4, level["row_pad"] + 1))

    def _row(self, label, caption):
        """One setting: name and explanation left, control right.

        At the densest layout the explanation is not drawn at all -- it goes
        to the footer, shown while the pointer is on the row.  Seventeen
        two-line captions are 400 pixels of height, which is the difference
        between fitting a 720p screen and not.
        """
        level = self._d
        column = self._columns[self._col]
        self._rows[self._col] += 1
        cell = tk.Frame(column, background=_UI["panel"])
        cell.grid(row=self._rows[self._col], column=0, sticky="nw",
                  padx=level["pad"], pady=(level["row_pad"], 0))
        tk.Label(cell, text=label, font=("Segoe UI", level["label"]),
                 background=_UI["panel"], foreground=_UI["text"]).pack(
            anchor="w")
        if level["caption"]:
            tk.Label(cell, text=caption, font=("Segoe UI", level["caption"]),
                     background=_UI["panel"], foreground=_UI["muted"],
                     wraplength=level["wrap"], justify="left").pack(
                anchor="w", pady=(1, 0))
        holder = tk.Frame(column, background=_UI["panel"])
        holder.grid(row=self._rows[self._col], column=1, sticky="nw",
                    padx=(0, level["pad"][0]),
                    pady=(max(1, level["row_pad"] - 1), 0))
        if not level["caption"]:
            self._hint_rows.append(((cell, holder), caption))
        return holder

    def _unit(self, holder, text):
        tk.Label(holder, text=text, font=("Segoe UI", self._d["unit"]),
                 background=_UI["panel"], foreground=_UI["faint"]).pack(
            side="left", padx=(9, 0))

    def _entry(self, holder, variable, width=9):
        box = tk.Entry(holder, textvariable=variable, width=width,
                       font=("Consolas", self._d["value"]), relief="flat",
                       background=_UI["seg_off"], foreground=_UI["text"],
                       insertbackground=_UI["accent"],
                       highlightthickness=2, highlightbackground=_UI["edge"],
                       highlightcolor=_UI["accent"])
        box.pack(side="left", ipady=max(2, self._d["row_pad"] - 1), ipadx=6)
        return box

    def _seg(self, holder, options, variable, large=False, **kwargs):
        """A choice strip at whatever size the form is being drawn at."""
        level = self._d
        index = 0 if large else 1
        return _Segmented(holder, options, variable,
                          font=("Segoe UI Semibold", level["cell"][index]),
                          pad=level["cell_pad"][index],
                          wheel=self._bind_wheel, **kwargs)

    def _button(self, parent, text, command, primary=False):
        level = self._d
        button = tk.Label(
            parent, text=text, font=("Segoe UI Semibold", level["button"]),
            background=_UI["accent"] if primary else _UI["seg_off"],
            foreground=_UI["on_accent"] if primary else _UI["text"],
            padx=level["button_pad"][0], pady=level["button_pad"][1],
            cursor="hand2", takefocus=True,
            highlightthickness=2, highlightbackground=_UI["band"],
            highlightcolor=_UI["accent_hover"])
        gloss = tk.Frame(button, height=1, borderwidth=0,
                         highlightthickness=0)
        gloss.place(x=0, y=0, relwidth=1)

        def paint(hovering):
            if primary:
                colour = _UI["accent_hover"] if hovering else _UI["accent"]
            else:
                colour = _UI["seg_hover"] if hovering else _UI["seg_off"]
            button.configure(background=colour)
            gloss.configure(background=_mix(colour, "#FFFFFF", 0.24))

        for sequence in ("<Button-1>", "<Return>", "<space>"):
            button.bind(sequence, lambda _e: command())
        button.bind("<Enter>", lambda _e: paint(True))
        button.bind("<Leave>", lambda _e: paint(False))
        paint(False)
        return button

    # ---- the settings themselves -----------------------------------------

    def _make_vars(self) -> None:
        """Every value the form edits, made once and kept.

        Separate from ``_init_components`` because the widgets are thrown
        away and drawn again whenever the window wants a different density,
        and a reader's half-typed end time must survive that.
        """
        self.fields = {}
        networks = Processor.available_networks()
        default = (Parameters.NETWORK_DIR if Parameters.NETWORK_DIR in networks
                   else (networks[0] if networks else ""))
        self.network_var = tk.StringVar(value=default)
        self.TIME_CHOICES = ["Peak hour (busiest)"] + [
            f"{h:02d}:00 - {(h + 1) % 24:02d}:00" for h in range(24)]
        current = Parameters.TIME_OF_DAY
        self.time_var = tk.StringVar(
            value=self.TIME_CHOICES[current + 1] if 0 <= current <= 23
            else self.TIME_CHOICES[0])
        self.fields["end_time"] = tk.StringVar(
            value=str(Parameters.simulation_end_time))
        self.fields["max_speed"] = tk.StringVar(
            value=jstr(round(Parameters.maximum_speed * 3.6, 2)))
        self.fields["seed"] = tk.StringVar(value=str(Parameters.seed))
        self.fields["accident"] = tk.StringVar(
            value=jstr(Parameters.encounter_per_accident))
        self.trace_var = tk.StringVar(
            value="On" if Parameters.TRACE_MODE else "Off")
        self.signal_var = tk.StringVar(
            value=next((label for label, mode in self.SIGNAL_CHOICES.items()
                        if mode == Parameters.SIGNAL_MODE), "Fixed time"))
        self.geometry_var = tk.StringVar(
            value="On" if Parameters.GEOMETRY_MODE else "Off")
        # VISSIM-style manual timing for the fixed controller: the operator
        # states the green each approach gets; the red follows from the other
        # approaches' greens.  Follows the network's defaults.txt when the
        # junction changes (see _on_network_change), and an edit here beats
        # both, matching the settings order pinned by test_network_defaults.
        self.fields["signal_green"] = tk.StringVar(
            value=str(Parameters.SIGNAL_CHANGE_DURATION))
        self.pedestrian_var = tk.StringVar(
            value="On" if Parameters.across_pedestrian_mode else "Off")
        # Side friction: parked cars/rickshaws/CNGs and standing pedestrians
        # blocking the kerbside strips.  Mirrors ObjectMode the same way the
        # pedestrian switch mirrors its parameter.
        self.friction_var = tk.StringVar(
            value="On" if Parameters.OBJECT_MODE else "Off")
        self.fields["strip"] = tk.StringVar(value=jstr(Parameters.strip_width))
        self.fields["footpath"] = tk.StringVar(
            value=jstr(Parameters.footpath_strip_width))
        self.render3d_var = tk.StringVar(
            value=next(
                (label for label, (on, style) in self.VIEW_CHOICES.items()
                 if on == Parameters.RENDER_3D
                 and (not on or style == Parameters.RENDER_3D_STYLE)), "Off"))
        self.fields["ppm"] = tk.StringVar(
            value=jstr(float(Parameters.pixel_per_meter)))
        self.fields["speed"] = tk.StringVar(
            value=str(Parameters.simulation_speed))

        # A speed limit belongs to the roads, not to the run, so changing the
        # junction has to move it.  Bound to the variable rather than to the
        # widget, so a script that sets ``network_var`` gets the same refresh
        # a click does -- and so a rebuild of the form does not add a second
        # copy of this.
        self._base_max_speed = round(Parameters.BASE_MAXIMUM_SPEED * 3.6, 2)
        # What the current network's defaults.txt pins, and which trimmable
        # rows the built form is showing.  None until the first build, so the
        # initial _on_network_change cannot ask for a rebuild of a form that
        # does not exist yet.
        self._pinned = frozenset()
        self._shown_rows = None
        self.network_var.trace_add("write",
                                   lambda *_a: self._on_network_change())
        self._on_network_change()

    def _init_components(self) -> None:
        level = self._d
        networks = Processor.available_networks()

        # ================= left column ====================================
        self._use(0)
        self._band("Junction", "  the network to simulate")
        holder = self._row("Intersection",
                           "Each is a surveyed junction with its own demand, "
                           "vehicle mix and road widths.")
        single = [n for n in networks if n in self.SINGLE_JUNCTIONS]
        multi = [n for n in networks if n not in self.SINGLE_JUNCTIONS]
        for caption, group in (("Single intersection", single),
                               ("Multi intersection", multi)):
            if not group:
                continue
            tk.Label(holder, text=caption, font=("Segoe UI", level["group"]),
                     background=_UI["panel"], foreground=_UI["faint"]).pack(
                anchor="w", pady=(0, 2))
            # Four tiles abreast is 440 pixels, which two columns of them
            # cannot afford on a 1280-wide screen; the densest layout wraps
            # them instead.
            self._seg(holder, [(n, _place_name(n)) for n in group],
                      self.network_var, large=True,
                      command=self._on_network_change,
                      columns=min(level["tiles"] or len(group), len(group)),
                      keep_unknown=False).pack(
                anchor="w", pady=(0, level["row_pad"] + 2))

        show_time, show_pedestrians, show_friction = self._trimmed_rows()
        self._shown_rows = (show_time, show_pedestrians, show_friction)
        if show_time:
            # Only a surveyed network carries an hourly demand profile; for
            # the OSM-derived ones the hour would change nothing, so the row
            # would be a lie.
            holder = self._row("Time of day",
                               "The busiest hour of the surveyed day, or one "
                               "you name.")
            _Dropdown(holder, self.TIME_CHOICES, self.time_var,
                      font=("Segoe UI", level["value"]),
                      pad=(12, max(3, level["row_pad"]))).pack(anchor="w")
        else:
            self.time_var.set(self.TIME_CHOICES[0])

        self._band("Run", "  how much traffic, and how repeatable")
        holder = self._row("End time",
                           "How many seconds of traffic to simulate.")
        self._entry(holder, self.fields["end_time"])
        self._unit(holder, "s")

        holder = self._row("Maximum speed",
                           "The network speed limit. Choosing a junction sets "
                           "its surveyed value.")
        self._entry(holder, self.fields["max_speed"])
        self._unit(holder, "km/h")

        holder = self._row("Random seed",
                           "The same number twice gives the same run twice.")
        self._entry(holder, self.fields["seed"])
        self._button(holder, "Randomise",
                     lambda: self.fields["seed"].set("-1")).pack(
            side="left", padx=(10, 0))

        holder = self._row("Accidents",
                           "Close encounters before one becomes an accident. "
                           "Lower means more.")
        self._entry(holder, self.fields["accident"])

        holder = self._row("Trace replay",
                           "On replays trace.txt instead of simulating a new "
                           "run.")
        self._seg(holder, ["Off", "On"], self.trace_var).pack(anchor="w")

        # ================= right column ===================================
        self._use(1)
        self._band("Control", "  what the signals do")
        holder = self._row("Signal control",
                           "Fixed time holds each leg green for the seconds "
                           "set below, VISSIM-style; a leg's red is the other "
                           "legs' greens. The multi-objective modes re-plan "
                           "each cycle and set their own timing.")
        # The timer shares this row rather than taking a captioned row of its
        # own (34 px the 1080p roomy budget did not have); packing it beside
        # the strip was tried instead and widened the column enough to stack
        # the form at 1280 px.  Below the strip costs one entry line, paid
        # for by the slimmer roomy row padding in _DENSITIES.
        self._seg(holder, list(self.SIGNAL_CHOICES), self.signal_var,
                  columns=2).pack(anchor="w")
        timing = tk.Frame(holder, background=_UI["panel"])
        timing.pack(anchor="w", pady=(3, 0))
        self._entry(timing, self.fields["signal_green"])
        self._unit(timing, "s green per approach")

        holder = self._row("Real geometry",
                           "The surveyed layout: solid medians and real "
                           "roundabouts. Off is Java parity.")
        self._seg(holder, ["Off", "On"], self.geometry_var).pack(anchor="w")

        if show_pedestrians or show_friction:
            # One captioned row for both street-level nuisances, the strips
            # side by side: a second captioned row here would cost the ~60 px
            # the roomy density does not have at 1080p, while extra width is
            # free -- the junction tiles already hold this column at 440 px.
            holder = self._row(
                "Side friction",
                "Crossing pedestrians, and parked vehicles on the kerbside.")
            if show_pedestrians:
                self._seg(holder, ["Off", "On"],
                          self.pedestrian_var).pack(side="left")
                self._unit(holder, "peds")
            if show_friction:
                self._seg(holder, ["Off", "On"], self.friction_var).pack(
                    side="left", padx=((10, 0) if show_pedestrians else 0))
                self._unit(holder, "parked")

        self._band("Road model", "  the strips a vehicle slides between")
        holder = self._row("Strip width",
                           "There are no lanes here. A carriageway is a row of "
                           "strips this wide.")
        self._seg(holder, ["0.25", "0.5", "1.0"],
                  self.fields["strip"]).pack(side="left")
        self._unit(holder, "m")

        holder = self._row("Footpath strip width",
                           "The same width again, for the footpath.")
        self._seg(holder, ["0.25", "0.5", "1.0"],
                  self.fields["footpath"]).pack(side="left")
        self._unit(holder, "m")

        self._band("Display", "  playback only, the results do not change")
        holder = self._row("3D view",
                           "A perspective scene instead of the plan view. "
                           "Line art is the lighter of the two.")
        self._seg(holder, list(self.VIEW_CHOICES),
                  self.render3d_var).pack(anchor="w")

        holder = self._row("Scale",
                           "How many screen pixels a metre of road takes up.")
        self._seg(holder, [5, 10, 15, 20, 30],
                  self.fields["ppm"]).pack(side="left")
        self._unit(holder, "px/m")

        holder = self._row("Frame delay",
                           "Pause between frames. Playback only -- the results "
                           "are the same either way.")
        self._seg(holder, [1, 5, 20, 50, 100, 500],
                  self.fields["speed"]).pack(side="left")
        self._unit(holder, "ms")

        for index, column in enumerate(self._columns):
            self._rows[index] += 1
            tk.Frame(column, background=_UI["panel"],
                     height=level["tail"]).grid(
                row=self._rows[index], column=0, columnspan=2, sticky="ew")

        # ---- the footer, which does not scroll ---------------------------
        inner = tk.Frame(self._footer, background=_UI["band"])
        inner.pack(fill="x", padx=level["pad"][0] * 2, pady=level["foot_pad"])
        self._button(inner, "Reset to defaults",
                     self.dhaka_sim_frame.show_options).pack(side="left")
        start = self._button(inner, "Start simulation", self.start_simulation,
                             primary=True)
        start.pack(side="right")
        start.focus_set()
        if not level["caption"]:
            # The captions had to come off the rows to fit the screen; this
            # is where they went.  One line for all seventeen of them.
            self._hint = tk.Label(
                inner, text="", font=("Segoe UI", level["group"] + 1),
                background=_UI["band"], foreground=_UI["muted"], anchor="w")
            self._hint.pack(side="left", fill="x", expand=True, padx=16)

    def _on_network_change(self):
        applied = Utilities.apply_network_defaults(self.network_var.get())
        if "MaximumSpeed" in applied:
            self.fields["max_speed"].set(
                jstr(round(Parameters.maximum_speed * 3.6, 2)))
        else:
            # back to the shared default when the network states none
            Utilities.apply_setting("MaximumSpeed", str(self._base_max_speed))
            self.fields["max_speed"].set(jstr(round(self._base_max_speed, 2)))
        # The other settings a place may pin.  Anything the newly selected
        # network's defaults.txt does not state goes back to parameter.txt's
        # value, or a previous network's "Off" would silently follow the
        # reader from Miami to Dhaka.
        if "AcrossPedestrianMode" not in applied:
            Parameters.across_pedestrian_mode = (
                Parameters.BASE_ACROSS_PEDESTRIAN_MODE)
        if "AlongPedestrianMode" not in applied:
            Parameters.along_pedestrian_mode = (
                Parameters.BASE_ALONG_PEDESTRIAN_MODE)
        if "ObjectMode" not in applied:
            Parameters.OBJECT_MODE = Parameters.BASE_OBJECT_MODE
        if "SignalChangeDuration" not in applied:
            Parameters.SIGNAL_CHANGE_DURATION = (
                Parameters.BASE_SIGNAL_CHANGE_DURATION)
        # The timing field mirrors the parameter the same way the speed
        # limit does: the place's own green appears when the junction
        # changes, and an edit made after that beats it.
        self.fields["signal_green"].set(str(Parameters.SIGNAL_CHANGE_DURATION))
        if "ShowLabels" not in applied:
            Parameters.SHOW_LABELS = Parameters.BASE_SHOW_LABELS
        # The Pedestrians switch mirrors the parameter, so it has to follow
        # -- start_simulation() writes the var back into Parameters, and a
        # stale "On" would undo the network's own Off.
        self.pedestrian_var.set(
            "On" if Parameters.across_pedestrian_mode else "Off")
        self.friction_var.set("On" if Parameters.OBJECT_MODE else "Off")
        self._pinned = frozenset(applied)
        # Settings the selected place cannot use leave the form: an hourly
        # demand profile it does not have, a pedestrians switch its
        # defaults.txt has decided.  Deferred because this runs from the
        # network variable's own trace, and rebuilding the form destroys the
        # very widget that fired it.
        if self._shown_rows is not None and self._trimmed_rows() != self._shown_rows:
            self.frame.after_idle(self._refit_trimmed)

    def _trimmed_rows(self):
        """(time-of-day, pedestrians, side friction) shown for the network."""
        network = self.network_var.get().strip()
        hourly = os.path.exists(
            os.path.join("input", network, "demand_by_hour.txt"))
        return (hourly, "AcrossPedestrianMode" not in self._pinned,
                "ObjectMode" not in self._pinned)

    def _refit_trimmed(self) -> None:
        if self._trimmed_rows() == self._shown_rows:
            return
        self._build_form()
        # The trimmed form is a different height, so the density search has
        # to run again from the top.
        self._viewport = None
        self._fit_to_window(force=True)

    def start_simulation(self) -> None:
        Parameters.NETWORK_DIR = self.network_var.get().strip()
        choice = self.time_var.get()
        Parameters.TIME_OF_DAY = (self.TIME_CHOICES.index(choice) - 1
                                  if choice in self.TIME_CHOICES else -1)
        print("Network: " + (Parameters.NETWORK_DIR or "input/"))
        Parameters.simulation_speed = int(self.fields["speed"].get())
        Parameters.simulation_end_time = int(self.fields["end_time"].get())
        Parameters.seed = int(self.fields["seed"].get())
        print("Seed: " + str(Parameters.seed))
        print("CF Model: " + str(Parameters.car_following_model.name))
        Parameters.random = (JavaRandom() if Parameters.seed < 0
                             else JavaRandom(Parameters.seed))
        Parameters.TRACE_MODE = self.trace_var.get() == "On"
        Parameters.SIGNAL_MODE = self.SIGNAL_CHOICES.get(
            self.signal_var.get(), "fixed")
        print("Signal control: " + Parameters.SIGNAL_MODE)
        try:
            green = int(float(self.fields["signal_green"].get()))
        except ValueError:
            green = Parameters.SIGNAL_CHANGE_DURATION
        Parameters.SIGNAL_CHANGE_DURATION = max(1, green)
        Parameters.GEOMETRY_MODE = self.geometry_var.get() == "On"
        print("Real geometry: " + ("On" if Parameters.GEOMETRY_MODE else "Off"))
        Parameters.RENDER_3D, Parameters.RENDER_3D_STYLE = (
            self.VIEW_CHOICES.get(self.render3d_var.get(), (False, "line")))
        Parameters.pixel_per_meter = float(self.fields["ppm"].get())
        value = float(self.fields["accident"].get())
        if value < 1:
            value = 100 / value
        else:
            value = 100 - value + 1
        Parameters.encounter_per_accident = value
        Parameters.strip_width = float(self.fields["strip"].get())
        Parameters.footpath_strip_width = float(self.fields["footpath"].get())
        Parameters.maximum_speed = float(self.fields["max_speed"].get()) * 1000 / 3600  # m/s
        Parameters.maximum_speed = Utilities.precision2(Parameters.maximum_speed)
        Parameters.across_pedestrian_mode = self.pedestrian_var.get() == "On"
        Parameters.OBJECT_MODE = self.friction_var.get() == "On"
        Parameters.pixel_per_footpath_strip = (Parameters.pixel_per_meter
                                              * Parameters.footpath_strip_width)
        Parameters.pixel_per_strip = Parameters.pixel_per_meter * Parameters.strip_width

        self.dhaka_sim_frame.show_simulation()


def _place_name(network: str) -> str:
    """What to call a network on screen.

    ``place.txt`` holds the name the extract was cut around, which is what a
    reader recognises where the folder name is an identifier.  Only the part
    before the comma, or every Dhaka network reads ", Dhaka", and without the
    parenthetical, which is a note rather than a name.

    A place name that says less than the folder does is not a name: the
    synthetic network's file says "Dhaka", which is true of four of the
    others too, so it falls back to "Demo Backup".
    """
    folder = network.replace("_", " ").title()
    try:
        with open(os.path.join("input", network, "place.txt"),
                  "r", encoding="utf-8") as handle:
            place = handle.read().strip()
    except OSError:
        return folder
    place = re.sub(r"\s*\([^)]*\)", "", place).split(",")[0].strip()
    if not place or (len(place.split()) < len(folder.split())):
        return folder
    return place


class DhakaSimFrame:
    """Port of ``thesisfinal.DhakaSimFrame``."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DhakaSim")
        self.root.geometry("1250x700")
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass
        # The ground colour under every screen, so the gaps between the run
        # view's panels read as the same application as the start screen.
        self.container = tk.Frame(self.root, background=_UI["ground"])
        self.container.pack(fill="both", expand=True)
        # The configuration as loaded from parameter.txt, before any run has
        # had a chance to mutate it.  show_options() puts this back, so the
        # form always opens on the same values it opened on the first time.
        self._baseline = Parameters.snapshot()
        self.option_panel = None
        self.panel = None
        self._report_button = None
        self._view_button = None
        self._pause_button = None
        self._status = None
        self.show_options()

    # Legend rows: label -> the vehicle type indices it covers.
    LEGEND_CATEGORIES = (
        ("Bicycle", (0,)), ("Rickshaw", (1,)), ("Van / Cart", (2,)),
        ("Motorbike", (3,)), ("Car", (4, 5, 6)), ("CNG / Auto", (7,)),
        ("Bus", (8, 9)), ("Truck", (10, 11)), ("Pedestrian", (12,)),
    )

    # Roadside obstructions, which have no counts because they do not travel.
    FRICTION_CATEGORIES = (
        ("Standing pedestrian", Constants.STANDING_PEDESTRIAN_COLOR),
        ("Parked car", Constants.PARKED_CAR_COLOR),
        ("Parked rickshaw", Constants.PARKED_RICKSHAW_COLOR),
        ("Parked CNG", Constants.PARKED_CNG_COLOR),
    )

    #: The one row of Run settings that is a control rather than a reading.
    #: Named so the renderer can pick it out without matching a bare string.
    DELAY_LABEL = "Frame delay"

    #: Delays the spinner steps through, in milliseconds.  Roughly 1-2-5 per
    #: decade: playback speed is judged by eye, so equal ratios are the useful
    #: steps rather than equal differences.
    DELAY_CHOICES = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)

    @staticmethod
    def _run_settings():
        """The configuration this run was started with, as label/value pairs.

        Read once, when the legend is built, rather than bound live to
        ``Parameters``.  Two reasons: these are a record of what was chosen on
        the setup form, so they should not drift if something later writes to
        the same fields, and the run's own step counter lives in there too.

        The frame delay is the exception, and is rendered as a control rather
        than as text: it only sets how long the picture pauses between frames,
        so it can be changed mid-run without making the run mean anything
        different.  Everything else here would.

        Speeds are held internally in metres per second; the form states them
        in km/h, and this shows what was typed.
        """
        hour = Parameters.TIME_OF_DAY
        # Split across the value and unit columns, which is the only way an
        # hour range fits beside the numeric settings without being clipped.
        when, when_unit = ((f"{hour:02d}:00", f"-{(hour + 1) % 24:02d}:00")
                           if 0 <= hour <= 23 else ("Peak", "hour"))
        on_off = lambda flag: "On" if flag else "Off"
        # Value and unit are separate so the numbers line up in their own
        # column instead of being pushed about by the length of the unit.
        return [
            ("Time of day", when, when_unit),
            ("End time", str(Parameters.simulation_end_time), "s"),
            (DhakaSimFrame.DELAY_LABEL,
             str(Parameters.simulation_speed), "ms"),
            ("Random seed", str(Parameters.seed), ""),
            ("Max speed", f"{Parameters.maximum_speed * 3.6:.0f}", "km/h"),
            ("Strip width", f"{Parameters.strip_width:g}", "m"),
            ("Footpath strip", f"{Parameters.footpath_strip_width:g}", "m"),
            ("Pixels per metre", f"{Parameters.pixel_per_meter:g}", ""),
            ("Pedestrians", on_off(Parameters.across_pedestrian_mode), ""),
            ("Side friction", on_off(Parameters.OBJECT_MODE), ""),
            ("Real geometry", on_off(Parameters.GEOMETRY_MODE), ""),
            ("Trace replay", on_off(Parameters.TRACE_MODE), ""),
        ]

    def _build_legend(self, parent):
        """A collapsible panel: colour key plus a live count per type.

        Dressed in the start screen's palette (``_UI``), because the run view
        and the form it came from are one application and should read as one.
        Plain ``tk`` widgets throughout for the same reason the start screen
        uses them: ttk takes its colours from the Windows theme and would
        punch a light grey panel through the dark chrome.
        """
        bg = _UI["panel"]
        legend = tk.Frame(parent, bg=bg, padx=10, pady=8,
                          highlightthickness=1,
                          highlightbackground=_UI["edge"])

        def dark_button(parent_, text, command, width=None, size=10):
            button = tk.Button(
                parent_, text=text, command=command, cursor="hand2",
                font=("Segoe UI Semibold", size), bd=0, relief="flat",
                background=_UI["seg_off"], foreground=_UI["text"],
                activebackground=_UI["seg_hover"],
                activeforeground=_UI["text"],
                highlightthickness=1, highlightbackground=_UI["edge"])
            if width is not None:
                button.configure(width=width)
            return button

        if Parameters.PLACE_NAME:
            tk.Label(legend, text=Parameters.PLACE_NAME,
                     font=("Segoe UI", 11, "bold"), bg=bg, fg=_UI["text"],
                     anchor="w", justify="left", wraplength=170).pack(
                         anchor="w", fill="x", pady=(0, 6))

        zoom_row = tk.Frame(legend, bg=bg)
        zoom_row.pack(anchor="w", fill="x", pady=(0, 8))
        tk.Label(zoom_row, text="Zoom", font=("Segoe UI", 9), bg=bg,
                 fg=_UI["muted"]).pack(side="left", padx=(0, 6))

        # A slider, not +/- buttons: with imagery on, the zoom snaps to the
        # ladder of scales the picture can be drawn at exactly, and a fixed
        # button step smaller than the next rung snapped straight back --
        # the buttons read as broken.  A drag states the destination
        # absolutely, so it always lands somewhere new.  The mapping is
        # logarithmic (see _slider_to_scale): zoom is a ratio, and a linear
        # slider crams every whole-network framing into its bottom few pixels.
        self._scale_slider = tk.Scale(
            zoom_row, from_=0, to=100, orient="horizontal", showvalue=False,
            borderwidth=0, highlightthickness=0, sliderrelief="flat",
            sliderlength=18, width=10, length=110, background=_UI["seg_off"],
            troughcolor=_UI["band"], activebackground=_UI["accent"],
            command=self._on_zoom_slider)
        self._scale_slider.pack(side="left", padx=(0, 6))
        # With imagery on, set_scale snaps to the picture's ladder and stops
        # at its top rung; settle the handle on what the view actually did,
        # so a drag past the ceiling ends with the handle at the ceiling
        # rather than promising a zoom that never happened.
        self._scale_slider.bind(
            "<ButtonRelease-1>",
            lambda _e: self.panel is not None
            and self.sync_zoom_slider(self.panel.scale))
        dark_button(zoom_row, "Reset", self.reset_zoom,
                    size=8).pack(side="left")

        # Only offered where imagery was actually fetched, so the control
        # never promises something the network cannot show.
        if self.panel is not None and self.panel._basemap is not None:
            self._basemap_var = tk.BooleanVar(value=self.panel.show_basemap)

            def flip():
                self.panel.toggle_basemap()
                self._basemap_var.set(self.panel.show_basemap)
                self.panel.repaint()

            # "Background map", not "Satellite map": every network ships with
            # the OpenStreetMap street rendering now, and only says satellite
            # if someone re-fetches with --provider esri.
            tk.Checkbutton(legend, text="Background map", bg=bg,
                           fg=_UI["text"], font=("Segoe UI", 9), anchor="w",
                           variable=self._basemap_var, command=flip,
                           cursor="hand2", activebackground=bg,
                           activeforeground=_UI["text"],
                           selectcolor=_UI["seg_off"],
                           highlightthickness=0).pack(anchor="w", pady=(0, 6))
            tk.Label(legend, text="Zoom steps are fixed while the map is on, "
                                  "so the imagery stays lined up with the "
                                  "roads.",
                     bg=bg, fg=_UI["faint"], font=("Segoe UI", 8), anchor="w",
                     justify="left", wraplength=170).pack(anchor="w",
                                                          pady=(0, 6))

        header = tk.Frame(legend, bg=bg)
        header.pack(anchor="w", fill="x")
        body = tk.Frame(legend, bg=bg)
        state = {"open": True}

        def toggle():
            state["open"] = not state["open"]
            if state["open"]:
                # after=: re-packing lands the body straight under its own
                # header; without it Tk appends it below whatever sections
                # were built later.
                body.pack(anchor="w", fill="x", pady=(6, 0), after=header)
                toggle_btn.configure(text="−")
            else:
                body.forget()
                toggle_btn.configure(text="+")

        tk.Label(header, text="Vehicles on network",
                 font=("Segoe UI Semibold", 10), bg=bg,
                 fg=_UI["accent_text"]).pack(side="left")
        toggle_btn = tk.Button(header, text="−", width=2, relief="flat",
                               bg=bg, fg=_UI["muted"], activebackground=bg,
                               activeforeground=_UI["text"],
                               font=("Segoe UI", 10, "bold"), bd=0,
                               command=toggle, cursor="hand2")
        toggle_btn.pack(side="right")

        self.legend_counts = {}
        for name, idxs in self.LEGEND_CATEGORIES:
            r, g, b = Constants.VEHICLE_TYPE_COLORS[idxs[0]]
            row = tk.Frame(body, bg=bg)
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text="  ", bg="#%02x%02x%02x" % (r, g, b), width=2,
                     relief="solid", borderwidth=1).pack(side="left")
            tk.Label(row, text="  " + name, bg=bg, fg=_UI["muted"], width=12,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")
            var = tk.StringVar(value="0")
            tk.Label(row, textvariable=var, bg=bg, fg=_UI["text"], width=5,
                     anchor="e", font=("Consolas", 9, "bold")).pack(side="left")
            self.legend_counts[name] = var

        total_row = tk.Frame(body, bg=bg)
        total_row.pack(anchor="w", fill="x", pady=(6, 0))
        tk.Label(total_row, text="Total", bg=bg, fg=_UI["text"], width=15,
                 anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.legend_total = tk.StringVar(value="0")
        tk.Label(total_row, textvariable=self.legend_total, bg=bg,
                 fg=_UI["accent_text"], width=5, anchor="e",
                 font=("Consolas", 9, "bold")).pack(side="left")

        body.pack(anchor="w", fill="x", pady=(6, 0))

        # Side friction is drawn on the same canvas but never appears in the
        # counts above, because these are obstructions rather than trips.
        # Without a key of its own a parked rickshaw is just an unexplained
        # blob, which is how it came to be mistaken for a moving one.  Its
        # own section with its own minimise button, and built only when the
        # run actually generates the objects -- a key describing things that
        # are not in the picture would be a lie on Miami and Riyadh, whose
        # defaults pin ObjectMode Off.
        if Parameters.OBJECT_MODE:
            friction_header = tk.Frame(legend, bg=bg)
            friction_header.pack(anchor="w", fill="x", pady=(12, 0))
            friction_body = tk.Frame(legend, bg=bg)
            friction_state = {"open": True}

            def toggle_friction():
                friction_state["open"] = not friction_state["open"]
                if friction_state["open"]:
                    friction_body.pack(anchor="w", fill="x", pady=(4, 0),
                                       after=friction_header)
                    friction_btn.configure(text="−")
                else:
                    friction_body.forget()
                    friction_btn.configure(text="+")

            tk.Label(friction_header, text="Side friction",
                     font=("Segoe UI Semibold", 10), bg=bg,
                     fg=_UI["accent_text"]).pack(side="left")
            friction_btn = tk.Button(friction_header, text="−", width=2,
                                     relief="flat", bg=bg, fg=_UI["muted"],
                                     activebackground=bg,
                                     activeforeground=_UI["text"],
                                     font=("Segoe UI", 10, "bold"), bd=0,
                                     command=toggle_friction, cursor="hand2")
            friction_btn.pack(side="right")

            for name, colour in self.FRICTION_CATEGORIES:
                row = tk.Frame(friction_body, bg=bg)
                row.pack(anchor="w", fill="x", pady=1)
                tk.Label(row, text="  ", bg=colour.to_hex(), width=2,
                         relief="solid", borderwidth=1).pack(side="left")
                tk.Label(row, text="  " + name, bg=bg, fg=_UI["muted"],
                         width=20, anchor="w",
                         font=("Segoe UI", 9)).pack(side="left")
            friction_body.pack(anchor="w", fill="x", pady=(4, 0))

        # What the run was set up with, kept in view for the whole run.  The
        # setup form is gone by now, and every one of these changes what the
        # numbers on screen mean, so reading a result without them beside it
        # invites comparing two runs that were never comparable.
        settings_header = tk.Frame(legend, bg=bg)
        settings_header.pack(anchor="w", fill="x", pady=(12, 0))
        settings_body = tk.Frame(legend, bg=bg)
        settings_state = {"open": True}

        def toggle_settings():
            settings_state["open"] = not settings_state["open"]
            if settings_state["open"]:
                settings_body.pack(anchor="w", fill="x", pady=(4, 0),
                                   after=settings_header)
                settings_btn.configure(text="\u2212")
            else:
                settings_body.forget()
                settings_btn.configure(text="+")

        tk.Label(settings_header, text="Run settings",
                 font=("Segoe UI Semibold", 10), bg=bg,
                 fg=_UI["accent_text"]).pack(side="left")
        settings_btn = tk.Button(settings_header, text="\u2212", width=2,
                                 relief="flat", bg=bg, fg=_UI["muted"],
                                 activebackground=bg,
                                 activeforeground=_UI["text"],
                                 font=("Segoe UI", 10, "bold"), bd=0,
                                 command=toggle_settings, cursor="hand2")
        settings_btn.pack(side="right")

        for label, value, unit in self._run_settings():
            row = tk.Frame(settings_body, bg=bg)
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text=label, bg=bg, width=14, anchor="w",
                     fg=_UI["muted"], font=("Segoe UI", 9)).pack(side="left")
            if label == self.DELAY_LABEL:
                self._build_delay_control(row, value, unit, bg)
                continue
            tk.Label(row, text=value, bg=bg, fg=_UI["text"], width=6,
                     anchor="e", font=("Consolas", 9, "bold")).pack(side="left")
            tk.Label(row, text=" " + unit, bg=bg, width=6, anchor="w",
                     fg=_UI["faint"], font=("Segoe UI", 8)).pack(side="left")

        settings_body.pack(anchor="w", fill="x", pady=(4, 0))
        return legend

    def _build_delay_control(self, row, value, unit, bg) -> None:
        """A spinner for the animation delay, live for the whole run.

        Nothing has to be restarted or rearmed: the panel's timer reads
        ``Parameters.simulation_speed`` each time it schedules the next frame,
        so a new value is picked up on the following tick.  While paused the
        timer is still turning over, so a change made then takes effect the
        moment the run resumes.

        Typed as well as stepped, since the useful range spans three orders of
        magnitude and clicking from 500 down to 1 would be tedious.
        """
        self._delay_var = tk.StringVar(value=value)

        def apply(normalise):
            """Push the box's value at the run, and optionally tidy the box.

            *normalise* is off while typing.  Rewriting the text on every
            keystroke fights the typist: a "0" on the way to "500" would be
            clamped to "1" under their fingers.  So mid-edit the value is
            applied if it parses and the text is left alone; it is only
            written back once the edit is committed.
            """
            try:
                wanted = int(float(self._delay_var.get()))
            except (TypeError, ValueError):
                return                      # mid-edit, or nonsense
            wanted = max(1, min(60000, wanted))
            Parameters.simulation_speed = wanted
            if normalise and str(wanted) != self._delay_var.get():
                self._delay_var.set(str(wanted))

        # tk.Spinbox rather than ttk: the Windows theme paints ttk fields
        # white whatever colours they are given, and this row sits on the
        # dark legend panel.
        spin = tk.Spinbox(row, textvariable=self._delay_var, width=6,
                          values=self.DELAY_CHOICES,
                          command=lambda: apply(True),
                          font=("Consolas", 9), justify="right",
                          relief="flat", bd=0,
                          background=_UI["seg_off"], foreground=_UI["text"],
                          insertbackground=_UI["accent"],
                          buttonbackground=_UI["seg_off"],
                          readonlybackground=_UI["seg_off"],
                          highlightthickness=1,
                          highlightbackground=_UI["edge"],
                          highlightcolor=_UI["accent"])
        spin.pack(side="left")
        # command= only fires for the arrows, so typing needs its own hooks.
        spin.bind("<Return>", lambda _e: apply(True))
        spin.bind("<FocusOut>", lambda _e: apply(True))
        self._delay_var.trace_add("write", lambda *_a: apply(False))
        # Kept as an attribute so the commit path can be exercised directly
        # rather than through a synthetic key event, which needs focus to be
        # where the test thinks it is.
        self._commit_delay = lambda: apply(True)
        tk.Label(row, text=" " + unit, bg=bg, width=4, anchor="w",
                 fg=_UI["faint"], font=("Segoe UI", 8)).pack(side="left")

    def update_legend_counts(self, vehicle_list) -> None:
        """Refresh the live per-type counts shown beside the colour key."""
        if not getattr(self, "legend_counts", None):
            return
        tally = {}
        for vehicle in vehicle_list:
            tally[vehicle.get_type()] = tally.get(vehicle.get_type(), 0) + 1
        total = 0
        for name, idxs in self.LEGEND_CATEGORIES:
            count = sum(tally.get(i, 0) for i in idxs)
            total += count
            try:
                self.legend_counts[name].set(str(count))
            except tk.TclError:
                return
        try:
            self.legend_total.set(str(total))
        except tk.TclError:
            pass

    def show_options(self) -> None:
        """Return to the start form, ready for another run."""
        self._teardown()
        # Undo everything the finished run changed, so the form shows the
        # values it was loaded with rather than that run's leftovers.
        Parameters.restore(self._baseline)
        Parameters.simulation_step = 1
        self.option_panel = OptionPanel(self, self.container)
        self.option_panel.frame.pack(fill="both", expand=True)

    def new_simulation(self) -> None:
        """Go back to the form, checking first if a run is still going."""
        running = self.panel is not None and not self.panel._finished
        if running and not messagebox.askyesno(
                "New simulation",
                "The current run has not finished.\n\n"
                "Discard it and go back to the setup screen?"):
            return
        self.show_options()

    def _teardown(self) -> None:
        if self.panel is not None:
            self.panel.dispose()
            self.panel = None
        self.option_panel = None
        self._report_button = None
        self._view_button = None
        self._pause_button = None
        self._status = None
        # Belong to the legend that is about to be destroyed, and the next
        # run may not have imagery at all.
        self._basemap_var = None
        self._delay_var = None
        self._commit_delay = None
        self._scale_slider = None
        Parameters.show_progress_slider = None
        for child in self.container.winfo_children():
            child.destroy()

    def show_simulation(self) -> None:
        self._teardown()

        panel = DhakaSimPanel(self, self.container)
        self.panel = panel

        # A toolbar that stays available for the whole run, so getting back to
        # the setup screen never means restarting the program.  Dressed as a
        # band of the start screen -- same fills, same glass hairlines -- so
        # pressing Start does not change which application you are in.
        top_bar = tk.Frame(self.container, background=_UI["band"],
                           padx=8, pady=6)
        _ToolButton(top_bar, text="◀  New simulation",
                    command=self.new_simulation).pack(side="left")
        self._report_button = _ToolButton(top_bar, text="Open report",
                                          command=self.open_report)
        self._report_button.configure(state="disabled")
        self._report_button.pack(side="left", padx=(8, 0))

        self._pause_button = _ToolButton(top_bar, command=self.toggle_pause)
        self._pause_button.pack(side="left", padx=(8, 0))
        self._sync_pause_button()

        # The view can be flipped at any point in a run: it changes only how
        # the frame is drawn, never what is simulated.
        self._view_button = _ToolButton(top_bar, command=self.toggle_view)
        self._view_button.pack(side="left", padx=(8, 0))
        self._sync_view_button()
        self.root.bind("<KeyPress-v>", lambda _e: self.toggle_view())
        self.root.bind("<KeyPress-space>", lambda _e: self.toggle_pause())
        self.root.bind("<KeyPress-m>", lambda _e: self.toggle_basemap())

        self._status = tk.Label(top_bar, text="", background=_UI["band"],
                                foreground=_UI["accent_text"],
                                font=("Segoe UI", 10))
        self._status.pack(side="left", padx=(12, 0))
        top_bar.pack(side="top", fill="x")
        _glass(top_bar)

        show_progress_slider = ProgressSlider(self.container, 1,
                                             Parameters.simulation_end_time, 1)
        change_trace_slider = ProgressSlider(self.container, 1,
                                            Parameters.simulation_end_time, 1)
        Parameters.show_progress_slider = show_progress_slider

        def on_change_trace(_value):
            if Parameters.TRACE_MODE:
                Parameters.simulation_step = change_trace_slider.get_value()
                trace_reader = panel.get_trace_reader()
                try:
                    trace_reader.close()
                    trace_reader = open("trace.txt", "r")
                    panel.set_trace_reader(trace_reader)
                    line_no = Parameters.simulation_step_line_nos[
                        change_trace_slider.get_value() - 1]
                    for _ in range(1, line_no):
                        trace_reader.readline()
                except OSError as e:
                    print(e)

        change_trace_slider.widget.configure(command=on_change_trace)

        # The zoom slider lives in the legend panel (built here).
        self._build_legend(self.container).pack(side="right", fill="y")
        show_progress_slider.widget.pack(side="bottom", fill="x")
        if Parameters.TRACE_MODE:
            change_trace_slider.widget.pack(side="top", fill="x")
        panel.canvas.pack(side="left", fill="both", expand=True)
        self.root.update_idletasks()
        # Open on the whole network.  A surveyed junction lands around a
        # 100 m scale bar; the BUET-DU-DMC demo lands on its full extent.
        # Starting from a fixed close zoom made every first act of a run a
        # hunt for where the traffic was.
        panel.home_scale = panel.fit_scale(panel.canvas.winfo_width(),
                                           panel.canvas.winfo_height())
        self.sync_zoom_slider(panel.home_scale)
        panel.set_scale(panel.home_scale)
        panel.start()

    def toggle_pause(self) -> None:
        """Hold or resume the run, from the toolbar or the space bar."""
        if self.panel is None or self.panel._finished:
            return
        self.panel.set_paused(not self.panel.paused)
        self._sync_pause_button()

    def _sync_pause_button(self) -> None:
        if self._pause_button is None or self.panel is None:
            return
        # The button says what it will do, not what the run is doing.
        paused = self.panel.paused
        self._pause_button.configure(text="\u25b6  Play" if paused
                                     else "\u23f8  Pause")
        if self._status is not None:
            self._status.configure(text="Paused." if paused else "")

    # Guards the slider's command while the handle is being moved to match a
    # zoom made elsewhere, so a wheel zoom does not re-enter set_scale.
    _zoom_syncing = False

    def _slider_to_scale(self, value) -> float:
        """Slider position (0-100) -> view scale, logarithmically.

        Zoom is a ratio, so equal slider movements should multiply the scale
        by equal factors; mapped linearly, every whole-network framing sat in
        the slider's bottom few pixels.
        """
        lo, hi = DhakaSimPanel.ZOOM_MIN, DhakaSimPanel.ZOOM_MAX
        return lo * (hi / lo) ** (float(value) / 100.0)

    def _scale_to_slider(self, scale: float) -> float:
        lo, hi = DhakaSimPanel.ZOOM_MIN, DhakaSimPanel.ZOOM_MAX
        scale = min(hi, max(lo, scale))
        return 100.0 * math.log(scale / lo) / math.log(hi / lo)

    def _on_zoom_slider(self, value) -> None:
        if self.panel is None or self._zoom_syncing:
            return
        self.panel.set_scale(self._slider_to_scale(value))

    def sync_zoom_slider(self, scale: float) -> None:
        """Move the handle to match a zoom made elsewhere (wheel, fit)."""
        slider = getattr(self, "_scale_slider", None)
        if slider is None:
            return
        self._zoom_syncing = True
        try:
            slider.set(self._scale_to_slider(scale))
        finally:
            self._zoom_syncing = False

    def reset_zoom(self) -> None:
        """Whole network back in frame: home pan, home scale, handle synced."""
        if self.panel is None:
            return
        self.panel.reset_view()
        self.sync_zoom_slider(self.panel.scale)

    def toggle_basemap(self) -> None:
        """Show or hide the map imagery, keeping the legend's box in step."""
        if self.panel is None:
            return
        self.panel.toggle_basemap()
        var = getattr(self, "_basemap_var", None)
        if var is not None:
            var.set(self.panel.show_basemap)
        self.panel.repaint()

    def toggle_view(self) -> None:
        """Swap between the 2D plan view and the 3D perspective view."""
        if self.panel is None:
            return
        self.panel.set_view_3d(not self.panel.view_3d)
        self._sync_view_button()

    def _sync_view_button(self) -> None:
        if self._view_button is None or self.panel is None:
            return
        # The button says where it takes you, not where you are.
        self._view_button.configure(
            text="Plan view (2D)" if self.panel.view_3d else "3D view")

    def repaint(self) -> None:
        if self.panel is not None:
            self.panel.repaint()

    def open_report(self) -> None:
        """Open the most recent HTML report in the default browser."""
        from . import report
        path = report.LAST_REPORT_PATH
        if not path:
            messagebox.showinfo("Report", "No report was written for this run.")
            return
        webbrowser.open(f"file://{path}")

    def on_simulation_finished(self) -> None:
        # Java swaps in an inert slider once the run ends; just stop repainting.
        # In addition, offer the auto-generated HTML report to the user.
        try:
            if self._pause_button is not None:
                self._pause_button.configure(state="disabled")
            if self._status is not None:
                self._status.configure(text="Run finished.")
            from . import report
            path = report.LAST_REPORT_PATH
            if path:
                if self._report_button is not None:
                    self._report_button.configure(state="normal")
                if messagebox.askyesno(
                        "Simulation finished",
                        "The run has finished.\n\nA report explaining every "
                        "result and term has been saved to:\n"
                        f"{path}\n\nOpen it now?\n\n"
                        "(Use ◀ New simulation, top left, to set up "
                        "another run without restarting.)"):
                    webbrowser.open(f"file://{path}")
        except Exception as exc:  # never let the popup break shutdown
            print(f"could not open report: {exc}")

    def run(self) -> None:
        self.root.mainloop()
