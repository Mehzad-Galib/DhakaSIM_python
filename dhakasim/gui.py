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
import random
import re
import threading
import time
import atexit
import contextlib
import io
import shutil
import tempfile
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

from .constants import Constants
from .javacompat import Color, JavaRandom, jbool, jbool_str, jint, jround, jstr
from .parameters import Parameters
from .processor import Processor
from . import basemap as basemap_module
from . import map_import
from . import render3d
from . import road_geometry
from . import utilities as Utilities


#: Canvas tags for the plan view's two layers.  The static layer (imagery,
#: carriageways, junction patches, labels) survives from frame to frame
#: while the view is still; the dynamic layer (signal bars, vehicles,
#: pedestrians, props, trajectories) is deleted and redrawn every step.
#: The same economy the 3D view has always had (``Scene3D.STATIC_TAG``).
STATIC_2D_TAG = "static2d"
DYNAMIC_2D_TAG = "dynamic2d"


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
        # The tags every item created from now on carries; the paint loop
        # switches it between the static and the dynamic layer.
        self.tags = ()

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
                                fill=self._color.to_hex(), tags=self.tags,
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
                                   offset="#0,0", tags=self.tags)

    def fill_oval(self, x, y, w, h) -> None:
        x1 = self._tx(x)
        y1 = self._ty(y)
        self.canvas.create_oval(x1, y1, x1 + w * self.scale, y1 + h * self.scale,
                                fill=self._color.to_hex(), outline="",
                                stipple=self._stipple, offset="#0,0",
                                tags=self.tags)

    def draw_oval(self, x, y, w, h) -> None:
        x1 = self._tx(x)
        y1 = self._ty(y)
        self.canvas.create_oval(x1, y1, x1 + w * self.scale, y1 + h * self.scale,
                                fill="", outline=self._color.to_hex(),
                                width=max(1, self.scale), tags=self.tags)

    def draw_string(self, text, x, y, anchor="sw") -> None:
        size = max(1, int(self._font[1] * self.scale))
        self.canvas.create_text(self._tx(x), self._ty(y), text=text, anchor=anchor,
                                fill=self._color.to_hex(), font=(self._font[0], size),
                                tags=self.tags)

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


class _NullWriter:
    """Where the preview's trace goes: nowhere.  The paint loop writes a
    trace line per vehicle per frame unconditionally, and the start screen's
    preview must not touch the run's trace.txt."""

    def write(self, _text) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class DhakaSimPanel:
    """Port of ``thesisfinal.DhakaSimPanel``.

    ``processor`` and ``trace`` exist for the start screen's preview
    (:class:`_PreviewPanel`): it is this same class, drawing a processor
    it was handed, with the trace file left alone -- so the preview cannot
    disagree with the run screen about what anything looks like.
    """

    # The 2D zoom range every control shares -- slider, wheel and Reset.  Wide
    # enough for the BUET-DU-DMC demo at one end (whole network ~0.037) and
    # kerb-level inspection at the other.
    ZOOM_MIN = 0.02
    ZOOM_MAX = 3.0
    #: The 3D view's corner line of controls; the preview turns it off.
    show_help = True
    #: Node and link names are sized in pixel space, for a window-sized
    #: canvas; the preview draws them at half size.
    label_scale = 1.0
    #: The compass dial, and the shortest scale bar worth drawing: both
    #: sized for a window, both cut down for the preview's small pictures.
    show_compass = True
    scale_bar_min_px = 90

    def __init__(self, frame, parent, processor=None, trace=True):
        self.frame = frame
        self.trace_reader = None
        self.trace_writer = None
        self.draw_roads = True
        self.draw_trajectories = False
        # Off, the picture is the roads alone: no vehicles, pedestrians,
        # parked objects or signal bars.  The start screen's map pictures.
        self.draw_traffic = True
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
        self._static2d_key = None
        self._timer = None
        self._finished = False
        self._basemap = None
        self._basemap_photo = None
        self.show_basemap = True
        self.paused = False
        # Feedback (Nielsen's visibility of system status): a short message
        # drawn on the canvas for a moment after a view change, and the
        # camera tween that carries the plan view into the 3D one.
        self._toast = None
        self._toast_until = 0.0
        self._tween = None

        try:
            if not trace:
                self.trace_writer = _NullWriter()
            elif Parameters.TRACE_MODE:
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

        self.processor = processor if processor is not None else Processor()

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
            # Relative to the whole-network fit of each view (home_scale and
            # the camera's home distance), so the plan view's fit tilts into
            # the 3D view's fit and 2x closer in one is 2x closer in the
            # other.  A fixed 0.30 pivot was tried first and put the camera
            # 2.4x too far out on a junction whose fit scale is 0.12.
            base = self.scene3d.camera.home_distance
            self.scene3d.camera.distance = base * (
                max(self.home_scale, 1e-9) / max(scale, 0.004))
        self.repaint()

    def set_view_3d(self, enabled: bool, animate: bool = True) -> None:
        """Swap between the plan view and the 3D view -- visibly.

        A straight cut between the two pictures left the reader to work out
        that the second was the first seen from a low angle.  Now the camera
        starts looking straight down at the point the plan view was centred
        on, at the matching zoom, and tilts to the 3D view's angle over a
        third of a second; going back it tilts up to vertical first and the
        plan view lands on the same point at the same zoom.  Only how the
        frame is drawn changes -- the run steps on underneath.
        """
        enabled = bool(enabled)
        if enabled == self.view_3d or self._tween is not None:
            return
        camera = self.scene3d.camera
        width = self.canvas.winfo_width() or 1
        height = self.canvas.winfo_height() or 1
        if enabled:
            self.view_3d = True
            self.set_scale(self.scale)   # re-derive the camera distance
            centre = [width / 2.0 - self.translate_x,
                      height / 2.0 - self.translate_y]
            end = (centre, camera.distance, 0.0, camera.pitch)
            if animate:
                start = (list(centre), camera.distance, 0.0, camera.MAX_PITCH)
                self._run_tween(start, end, frames=10)
            else:
                camera.target = centre
                self.repaint()
            self.toast("3D view -- drag to orbit, right-drag to pan, wheel "
                       "to zoom, double-click to reset; V goes back")
            self.frame.update_status()
            return

        def land():
            self.view_3d = False
            # The plan view lands where the camera was looking, at the zoom
            # its distance stood for (the inverse of set_scale's mapping).
            self.translate_x = width / 2.0 - camera.target[0]
            self.translate_y = height / 2.0 - camera.target[1]
            scale = (self.home_scale * camera.home_distance
                     / max(camera.distance, 1.0))
            self.set_scale(min(self.ZOOM_MAX, max(self.ZOOM_MIN, scale)))
            self.frame.sync_zoom_slider(self.scale)
            self.toast("Plan view -- drag to pan, wheel to zoom; "
                       "V returns to 3D")
            self.frame.update_status()

        if not animate:
            land()
            return
        start = (list(camera.target), camera.distance, camera.yaw, camera.pitch)
        # Unwind the yaw the short way round, so north ends up at the top.
        yaw_home = 0.0 if camera.yaw <= math.pi else 2 * math.pi
        end = (list(camera.target), camera.distance, yaw_home, camera.MAX_PITCH)
        self._run_tween(start, end, frames=8, then=land)

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
            # A 3D frame's static items are not the plan view's.
            self._static2d_key = None
            if reuse_static:
                canvas.delete(render3d.Scene3D.DYNAMIC_TAG)
                canvas.delete("furniture")
            else:
                canvas.delete("all")
            g2d.begin_frame(width, height, Parameters.pixel_per_meter,
                            keep_static=reuse_static)
        else:
            # A plan-view frame's items are not the 3D view's static layer.
            self._static3d_key = None
            g2d = self.graphics
            g2d.set_transform(width, height, self.scale,
                              self.translate_x, self.translate_y)
            # The plan view used to clear the whole canvas and rebuild it
            # every step -- imagery, several hundred road items, every
            # label -- which is both the slowest thing a frame did and the
            # reason the picture flashed while a run played: Tk repaints
            # the cleared window before the new items land.  Everything
            # that depends only on the view and the network now stays on
            # the canvas while none of that changes, and a step deletes
            # and redraws only the moving things.  Same design as the 3D
            # view above; the key lists everything the static layer was
            # drawn from, so any change to it forces a full redraw.
            if self.draw_roads:
                self._ensure_road_geometry()
            static_key = ("2d", width, height, round(self.scale, 9),
                          round(self.translate_x, 6),
                          round(self.translate_y, 6),
                          Parameters.pixel_per_meter, self.draw_roads,
                          self.basemap_active(), id(self._road_geometry),
                          Parameters.SHOW_LABELS,
                          Constants.background_color.to_hex())
            reuse_static = static_key == self._static2d_key
            if reuse_static:
                canvas.delete(DYNAMIC_2D_TAG)
                canvas.delete("furniture")
            else:
                canvas.delete("all")
                canvas.configure(bg=Constants.background_color.to_hex())
            g2d.tags = (STATIC_2D_TAG,)

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
        if self.basemap_active() and not reuse_static:
            self._draw_basemap(g2d)

        if self.draw_roads and not reuse_static:
            self.draw_road_network(g2d)

        if self.view_3d:
            # The static half of the 3D frame ends here; vehicles and
            # trajectories from now on are tagged for per-frame deletion.
            g2d.begin_dynamic()
            self._static3d_key = static_key
        else:
            # Likewise for the plan view: from here on, dynamic items.
            g2d.tags = (DYNAMIC_2D_TAG,)
            self._static2d_key = static_key

        # Signal state changes every few steps, so the bars are dynamic
        # content -- drawn each frame, under the vehicles that cross them.
        # A trace replay has no live signal state to show.
        if self.draw_roads and self.draw_traffic and not Parameters.TRACE_MODE:
            road_geometry.signal_bars(g2d, self.link_list, self.node_list,
                                      Parameters.pixel_per_meter)

        if self.draw_trajectories:
            self.draw_all_trajectories(g2d)

        if Parameters.TRACE_MODE:
            Utilities.draw_trace(self.trace_reader, g2d)
        elif self.draw_traffic:
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
        self._draw_toast(width)

        # --- imagery credit, bottom right ---
        # The ODbL asks for the credit wherever the tiles are shown, so it is
        # drawn with the furniture rather than left to the documentation.
        if self.basemap_active():
            canvas.create_text(width - 8, height - 6, anchor="se",
                               text=self._basemap.attribution,
                               fill="#33465c", font=("Segoe UI", 8),
                               tags="furniture")

        # --- north arrow, top right ---
        if self.show_compass:
            cx, cy = width - 46, 46
            bearing = -self.scene3d.camera.yaw if self.view_3d else 0.0
            cos_b, sin_b = math.cos(bearing), math.sin(bearing)

            def needle(x, y):
                """Rotate an offset from the dial centre by the camera
                bearing."""
                return cx + x * cos_b - y * sin_b, cy + x * sin_b + y * cos_b

            canvas.create_oval(cx - 26, cy - 26, cx + 26, cy + 26,
                               fill=paper, outline="#c7ccd3", tags="furniture")
            canvas.create_polygon(*needle(0, -19), *needle(-8, 11),
                                  *needle(0, 5), *needle(8, 11), fill=ink,
                                  outline="", tags="furniture")
            canvas.create_text(*needle(0, 17), text="N", fill=ink,
                               font=("Segoe UI", 10, "bold"), tags="furniture")

        if self.view_3d and not self.show_help:
            return
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
            if candidate * px_per_m >= self.scale_bar_min_px:
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
        g2d.set_font("Serif", int(100 * self.label_scale))
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

        g2d.set_font("Serif", int(78 * self.label_scale))
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
        try:
            placed = base.scaled_region(left, top,
                                        self.canvas.winfo_width() or 1,
                                        self.canvas.winfo_height() or 1,
                                        self.scale, ppm)
        except tk.TclError as exc:
            # Tk can refuse to create a photo (seen as a bare TclError in a
            # process that has made and dropped many interpreters and
            # images).  Imagery must never stop a paint: this frame goes
            # without, and the next one tries again.
            print(f"basemap unavailable this frame: {exc or 'TclError'}")
            return
        if placed is None:
            return
        photo, x, y = placed
        # Tk drops a photo the moment nothing references it, and the canvas
        # item does not count as a reference.
        self._basemap_photo = photo
        self.canvas.create_image(x, y, image=photo, anchor="nw",
                                 tags=g2d.tags)

    def set_paused(self, paused: bool) -> None:
        """Hold the run where it is, or let it carry on.

        Only the stepping stops.  The view still repaints, so panning, zooming
        and switching to the 3D camera all keep working on the held frame,
        which is most of the point of being able to stop.
        """
        self.paused = bool(paused)
        self.toast("Paused -- the view still pans, zooms and tilts; "
                   "Space resumes" if self.paused else "Running")
        self.repaint()
        self.frame.update_status()

    # ---- feedback ----------------------------------------------------------

    TOAST_SECONDS = 2.8

    def toast(self, text: str, seconds: float = TOAST_SECONDS) -> None:
        """Say what just happened, on the picture, for a moment.

        Nielsen's first heuristic: a control that changes the view should
        say so where the reader is looking.  Drawn with the map furniture
        (so it survives the static-layer reuse and is deleted with the rest
        of the furniture each frame) and dropped by a timer; a newer
        message simply replaces an older one.
        """
        self._toast = text
        self._toast_until = time.monotonic() + seconds
        try:
            self.canvas.after(int(seconds * 1000) + 20, self._expire_toast)
        except tk.TclError:
            pass

    def _expire_toast(self) -> None:
        if self._toast is not None and time.monotonic() >= self._toast_until:
            self._toast = None
            try:
                self.repaint()
            except tk.TclError:
                pass

    def _draw_toast(self, width: int) -> None:
        if self._toast is None or time.monotonic() >= self._toast_until:
            return
        canvas = self.canvas
        text = canvas.create_text(width / 2.0, 24, text=self._toast,
                                  fill="#F4F1EE", anchor="n",
                                  font=("Segoe UI Semibold", 10),
                                  tags="furniture")
        x0, y0, x1, y1 = canvas.bbox(text)
        canvas.create_rectangle(x0 - 16, y0 - 7, x1 + 16, y1 + 7,
                                fill=_UI["panel"], outline=_UI["accent"],
                                width=1, tags="furniture")
        canvas.tag_raise(text)

    def _run_tween(self, start, end, frames: int, then=None) -> None:
        """Carry the 3D camera from ``start`` to ``end`` over ``frames``
        repaints (each a ``(target, distance, yaw, pitch)`` tuple), then call
        ``then``.  The frames are scheduled with ``after`` so the run keeps
        stepping underneath; a second transition asked for mid-tween is
        refused by ``set_view_3d`` rather than queued."""
        camera = self.scene3d.camera

        def ease(t):                    # smooth in and out
            return t * t * (3.0 - 2.0 * t)

        def step(i):
            if not self.canvas.winfo_exists():
                self._tween = None
                return
            t = ease(i / float(frames))
            camera.target = [start[0][0] + (end[0][0] - start[0][0]) * t,
                             start[0][1] + (end[0][1] - start[0][1]) * t]
            camera.distance = start[1] + (end[1] - start[1]) * t
            camera.yaw = start[2] + (end[2] - start[2]) * t
            camera.pitch = start[3] + (end[3] - start[3]) * t
            self.repaint()
            if i < frames:
                self._tween = self.canvas.after(28, step, i + 1)
            else:
                self._tween = None
                if then is not None:
                    then()

        step(0)

    def toggle_basemap(self) -> None:
        """Show or hide the imagery.  Does nothing if none was fetched."""
        if self._basemap is None:
            return
        self.show_basemap = not self.show_basemap
        self.toast("Background map shown -- the zoom now snaps to the "
                   "map's own steps" if self.show_basemap
                   else "Background map hidden -- the zoom is free again")
        # Turning it back on re-snaps the zoom; turning it off frees it again.
        self.set_scale(self.scale)

    def _ensure_road_geometry(self) -> None:
        """Build the cached road geometry if the current view needs a new one.

        It depends only on the network and pixelPerMeter, and rebuilding it
        every frame is wasteful.  Two shapes, not one: over imagery the
        carriageway is drawn wider, so the cache is keyed by which of the two
        is wanted.  Toggling the map rebuilds; it does not silently reuse the
        other one's geometry.  Called before the plan view decides whether
        its static layer can be reused, because the geometry's identity is
        part of that decision.
        """
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

    def draw_road_network(self, g2d) -> None:
        # The road surface is painted by road_geometry.paint, which the report's
        # animation also calls, so the two pictures cannot diverge.
        self._ensure_road_geometry()
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
        self.frame.update_status()

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
        self.toast("Camera reset -- the whole network, from the north")
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
        self.toast("View reset -- the whole network in frame")

    def mouse_wheel_moved(self, event) -> None:
        notches = -1 if event.delta > 0 else 1
        if self.view_3d:
            self.scene3d.camera.zoom(notches)
            camera = self.scene3d.camera
            self.toast("Camera %s -- x%.1f of the whole-network distance"
                       % ("closer" if notches < 0 else "further",
                          camera.home_distance / max(camera.distance, 1.0)))
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
        self.toast("Zoom x%.2g of the whole-network framing"
                   % (self.scale / max(self.home_scale, 1e-9)))


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
#: Cool greys with a blue accent (11 Oct 2026: the owner asked for
#: "blueish, not greenish").  The report's CSS carries the same values,
#: copied -- change the two together.
_UI = {
    "ground": "#0F1317",
    "panel": "#1C2229",
    "band": "#262E37",
    "seg_off": "#313A44",
    "seg_hover": "#3F4955",
    "edge": "#3A4450",
    "glass_hi": "#4A5563",
    "glass_lo": "#080B0E",
    "glow": "#1B2B40",
    "accent": "#2F80D6",
    "accent_hover": "#3B8FE6",
    "accent_text": "#7DBDFF",
    "on_accent": "#06111D",
    "text": "#F1F4F8",
    "muted": "#BAC5D1",
    "faint": "#8C99A8",
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
    """Give a surface the hairlines that read as a pane of glass.

    Glass is not a flat fill: it catches the light along its top edge and
    drops a shadow along its bottom one.  Tk cannot composite, so the
    edges *are* the effect -- the top one fades over three lines, which
    is as much of a frosted highlight as a widget without alpha can
    carry.  They go on with ``place``, which does not disturb whichever
    geometry manager the surface's real children use -- that is the whole
    reason this can be applied to a finished widget.
    """
    try:
        base = surface.cget("background")
    except tk.TclError:
        base = _UI["panel"]
    high = top or _UI["glass_hi"]
    for index, amount in enumerate((1.0, 0.45, 0.2)):
        tk.Frame(surface, background=_mix(base, high, amount), height=1,
                 borderwidth=0, highlightthickness=0).place(
            x=0, y=index, relwidth=1)
    tk.Frame(surface, background=bottom or _UI["glass_lo"], height=1,
             borderwidth=0, highlightthickness=0).place(x=0, y=-1, rely=1.0,
                                                        relwidth=1)


def _round_corners(surface, radius, outside=None, edge=None, top_fill=None):
    """Round a rectangular widget's corners at no layout cost.

    Four small canvases placed on the corners paint the colour *behind*
    the widget outside a quarter circle and the widget's edge line along
    it.  Nothing about the widget's size or geometry changes -- which is
    what lets a column that the density search has measured to the pixel
    be rounded without being measured again -- and the widget's own
    one-pixel highlight border still runs the straight edges.
    """
    outside = outside or _UI["ground"]
    edge = edge or _UI["edge"]
    try:
        fill = surface.cget("background")
    except tk.TclError:
        fill = _UI["panel"]
    r = radius
    for relx, rely in ((0, 0), (1, 0), (0, 1), (1, 1)):
        corner = tk.Canvas(surface, width=r, height=r, highlightthickness=0,
                           bd=0, background=outside)
        bx = 0 if relx == 0 else -r
        by = 0 if rely == 0 else -r
        # The top corners sit on the first band, so they take its colour.
        corner.create_oval(bx, by, bx + 2 * r, by + 2 * r,
                           fill=(top_fill or fill) if rely == 0 else fill,
                           outline=edge, width=1)
        corner.place(relx=relx, rely=rely, x=-r if relx else 0,
                     y=-r if rely else 0)


class _Icon(tk.Canvas):
    """A line-drawn glyph for a setting, drawn on a small canvas.

    Drawn rather than taken from a glyph font: the icon fonts Windows
    ships are not on every machine, Tk draws emoji without colour where
    it draws them at all, and a line icon in the accent colour sits with
    the rest of the screen.  Each glyph is laid out on a 20-unit grid and
    scaled to ``size``, so it follows the density ladder's type.
    """

    KINDS = ("network", "time", "end_time", "max_speed", "seed", "accident",
             "trace", "signal", "geometry", "pedestrians", "friction",
             "strip", "footpath", "render3d", "speed", "traffic", "ppm",
             "settings")

    def __init__(self, parent, kind, size, colour=None, background=None):
        super().__init__(parent, width=size, height=size,
                         highlightthickness=0, bd=0,
                         background=background or _UI["panel"])
        self._draw(kind, float(size), colour or _UI["accent_text"])

    def _draw(self, kind, s, c):
        u = s / 20.0
        w = max(1.5, s / 11.0)
        line = dict(fill=c, width=w, capstyle="round", joinstyle="round")
        edge = dict(outline=c, width=w)
        back = self.cget("background")

        def L(*pts):
            self.create_line(*[v * u for v in pts], **line)

        def O(x0, y0, x1, y1, filled=False, colour=None):
            self.create_oval(x0 * u, y0 * u, x1 * u, y1 * u,
                             fill=(colour or c) if filled else back,
                             outline=colour or c, width=w)

        def P(*pts, filled=False):
            self.create_polygon(*[v * u for v in pts],
                                fill=c if filled else back, **edge)

        if kind == "network":          # a crossroads
            L(2, 10, 18, 10)
            L(10, 2, 10, 18)
            self.create_rectangle(7 * u, 7 * u, 13 * u, 13 * u, fill=back,
                                  **edge)
        elif kind == "time":           # a clock
            O(3, 3, 17, 17)
            L(10, 10, 10, 5.5)
            L(10, 10, 13.5, 12)
        elif kind == "end_time":       # an hourglass
            P(4, 3, 16, 3, 10, 10)
            P(4, 17, 16, 17, 10, 10)
        elif kind == "max_speed":      # a gauge
            self.create_arc(3 * u, 4 * u, 17 * u, 18 * u, start=0,
                            extent=180, style="arc", **edge)
            L(10, 11, 14.5, 6.5)
            O(9, 10, 11, 12, filled=True)
        elif kind == "seed":           # a die
            self.create_rectangle(3 * u, 3 * u, 17 * u, 17 * u, fill=back,
                                  **edge)
            for x, y in ((6.5, 6.5), (10, 10), (13.5, 13.5)):
                O(x - 1.3, y - 1.3, x + 1.3, y + 1.3, filled=True)
        elif kind == "accident":       # a burst
            pts = []
            for k in range(16):
                import math as _m
                radius = 8.0 if k % 2 == 0 else 4.0
                angle = _m.pi * 2 * k / 16.0
                pts += [10 + radius * _m.cos(angle), 10 + radius * _m.sin(angle)]
            P(*pts)
        elif kind == "trace":          # a strip of film
            self.create_rectangle(2 * u, 5 * u, 18 * u, 15 * u, fill=back,
                                  **edge)
            for x in (4.5, 9, 13.5):
                self.create_rectangle(x * u, 6.5 * u, (x + 2) * u, 8 * u,
                                      fill=c, outline="")
                self.create_rectangle(x * u, 12 * u, (x + 2) * u, 13.5 * u,
                                      fill=c, outline="")
        elif kind == "signal":         # a traffic light
            self.create_rectangle(6 * u, 1.5 * u, 14 * u, 18.5 * u,
                                  fill=back, **edge)
            for y, colour in ((4.5, "#E5484D"), (10, "#F5C542"),
                              (15.5, "#3FBF6A")):
                O(8, y - 2, 12, y + 2, filled=True, colour=colour)
        elif kind == "geometry":       # a roundabout
            O(5, 5, 15, 15)
            O(8, 8, 12, 12, filled=True)
            L(10, 1.5, 10, 5)
            L(10, 15, 10, 18.5)
            L(1.5, 10, 5, 10)
            L(15, 10, 18.5, 10)
        elif kind == "pedestrians":    # a walker
            O(8.5, 1.5, 11.5, 4.5, filled=True)
            L(10, 5, 10, 11)
            L(5.5, 8, 14.5, 8)
            L(10, 11, 6.5, 18)
            L(10, 11, 13.5, 18)
        elif kind == "friction":       # a parked car
            P(2, 13, 4.5, 8, 7.5, 5.5, 12.5, 5.5, 15.5, 8, 18, 13)
            L(2, 13, 18, 13)
            O(4, 12, 8, 16, filled=True)
            O(12, 12, 16, 16, filled=True)
        elif kind == "strip":          # the strips of a carriageway
            for y in (4.5, 10, 15.5):
                L(2.5, y, 17.5, y)
        elif kind == "footpath":       # a kerb and the footpath beside it
            L(2.5, 7, 17.5, 7)
            self.create_line(2.5 * u, 13 * u, 17.5 * u, 13 * u, fill=c,
                             width=w, dash=(3, 3))
        elif kind == "render3d":       # a cube
            P(10, 2, 17, 6, 17, 14, 10, 18, 3, 14, 3, 6)
            L(3, 6, 10, 10)
            L(17, 6, 10, 10)
            L(10, 10, 10, 18)
        elif kind == "speed":          # play
            P(5, 3, 17, 10, 5, 17, filled=True)
        elif kind == "traffic":        # a queue of vehicles
            for x in (2, 8, 14):
                self.create_rectangle(x * u, 7 * u, (x + 4.5) * u, 13 * u,
                                      fill=c, outline="")
        elif kind == "ppm":            # a ruler
            self.create_rectangle(2.5 * u, 7 * u, 17.5 * u, 13 * u,
                                  fill=back, **edge)
            for x in (6, 9.5, 13):
                L(x, 7, x, 10)
        elif kind == "settings":       # three sliders
            for y, x in ((5, 7), (10, 13), (15, 9)):
                L(2.5, y, 17.5, y)
                O(x - 1.8, y - 1.8, x + 1.8, y + 1.8, filled=True)


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
                 keep_unknown=True, hover=None):
        super().__init__(parent, background=_UI["panel"])
        # Called with a cell's value while the pointer is on it and with
        # None when it leaves -- how the preview shows a choice before it
        # is made.  Rebound with the cells, like the wheel.
        self._hover = hover
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
                if self._hover is not None:
                    cell.bind("<Enter>", lambda _e, v=value: self._hover(v),
                              add="+")
                    cell.bind("<Leave>", lambda _e: self._hover(None),
                              add="+")
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


class _SpeedSlider(tk.Frame):
    """A slider over the playback speeds, the way a volume control is one.

    Five detents, one per ``SPEED_CHOICES`` entry, slowest on the left;
    the words under the ticks and the milliseconds behind the handle in
    the readout beside it.  The owner asked for this in place of the
    five-cell strip (12 Oct 2026): a pace is one quantity on a scale,
    not five unrelated choices.

    Rendered *from* its ``StringVar`` like ``_Segmented``, and giving its
    trace back on ``<Destroy>`` for the same reason.  A value off the
    ladder -- 250 ms from a defaults.txt, say -- parks the handle at the
    nearest detent and the readout shows its own milliseconds; the
    variable is written only when the handle is moved, so a run keeps
    what it was given until the reader changes it.
    """

    def __init__(self, parent, variable, font=("Segoe UI Semibold", 11),
                 small=("Segoe UI", 9), length=130, words=True,
                 background=None):
        bg = background or _UI["panel"]
        super().__init__(parent, background=bg)
        self._var = variable
        self._choices = SPEED_CHOICES
        self._words = words
        self._length = length
        self._small = small
        self._handle = max(5, int(font[1] * 0.55))
        # Room either side of the trough for the end words, which are
        # centred on the end ticks, else for the handle alone.
        if words:
            widest = max(tkfont.Font(font=small).measure(label)
                         for _v, label in self._choices)
            self._pad = widest // 2 + 3
            # Neighbouring words must not touch: the ticks are at least
            # the widest word and a gap apart, whatever length was asked.
            self._length = length = max(
                length, (len(self._choices) - 1) * (widest + 8))
        else:
            self._pad = self._handle + 2
        self._trough_y = self._handle + 3
        word_h = (int(small[1] * 1.6) + 4) if words else 0
        height = self._trough_y + self._handle + 2 + word_h
        self._canvas = tk.Canvas(self, width=length + 2 * self._pad,
                                 height=height, background=bg,
                                 highlightthickness=0, bd=0, cursor="hand2",
                                 takefocus=True)
        self._canvas.pack(side="left")
        self._readout = tk.Label(self, font=font, background=bg,
                                 foreground=_UI["text"], anchor="w")
        self._readout.pack(side="left", padx=(8, 0))
        for sequence in ("<Button-1>", "<B1-Motion>"):
            self._canvas.bind(sequence, self._drag)
        self._canvas.bind("<Left>", lambda _e: self._step(-1))
        self._canvas.bind("<Right>", lambda _e: self._step(1))
        self._canvas.bind("<FocusIn>", lambda _e: self._render())
        self._canvas.bind("<FocusOut>", lambda _e: self._render())
        self._render()
        self._trace = variable.trace_add("write", lambda *_a: self._render())
        self.bind("<Destroy>", self._forget)

    def _forget(self, event):
        if event.widget is self and self._trace is not None:
            self._var.trace_remove("write", self._trace)
            self._trace = None

    def _x(self, index):
        return self._pad + index * self._length / (len(self._choices) - 1)

    def nearest(self):
        """(the detent nearest the variable's value, whether it is on it)."""
        try:
            value = float(self._var.get())
        except (TypeError, ValueError):
            return len(self._choices) // 2, False
        value = max(value, 0.5)
        # Nearest by ratio: the detents are 1-20-50-100-500 ms, and 250
        # is a Slow, not a Step.
        best = min(range(len(self._choices)),
                   key=lambda i: abs(math.log(value)
                                     - math.log(float(self._choices[i][0]))))
        return best, _Segmented._same(self._choices[best][0], value)

    def _drag(self, event):
        n = len(self._choices)
        index = int(round((event.x - self._pad) / self._length * (n - 1)))
        self._choose(max(0, min(n - 1, index)))
        self._canvas.focus_set()

    def _step(self, delta):
        index, _on = self.nearest()
        self._choose(max(0, min(len(self._choices) - 1, index + delta)))

    def _choose(self, index):
        value = self._choices[index][0]
        if not _Segmented._same(value, self._var.get()):
            self._var.set(value)

    def _render(self):
        canvas = self._canvas
        try:
            canvas.delete("all")
        except tk.TclError:
            return
        index, on = self.nearest()
        n, y, r = len(self._choices), self._trough_y, self._handle
        x0, x1, xh = self._x(0), self._x(n - 1), self._x(index)
        try:
            focused = canvas.focus_get() is canvas
        except (tk.TclError, KeyError):
            focused = False
        lit = _UI["accent_hover"] if focused else _UI["accent"]
        canvas.create_line(x0, y, x1, y, fill=_UI["seg_off"], width=4,
                           capstyle="round")
        # The filled stretch up to the handle, as a volume slider shows
        # how much of the scale is on.
        canvas.create_line(x0, y, xh, y, fill=lit, width=4, capstyle="round")
        for k in range(n):
            x = self._x(k)
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, outline="",
                               fill=_UI["edge"] if x > xh
                               else _mix(lit, "#FFFFFF", 0.4))
            if self._words:
                canvas.create_text(x, y + r + 3, text=self._choices[k][1],
                                   anchor="n", font=self._small,
                                   fill=_UI["accent_text"] if k == index
                                   else _UI["muted"])
        canvas.create_oval(xh - r, y - r, xh + r, y + r, fill=lit,
                           outline=_mix(lit, "#FFFFFF", 0.35), width=1)
        shown = self._var.get()
        try:
            ms = f"{int(float(shown))} ms"
        except (TypeError, ValueError):
            ms = str(shown)
        if self._words:
            text = ms
        else:
            text = f"{self._choices[index][1]} \u00b7 {ms}" if on else ms
        self._readout.configure(text=text)


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
         band=12, note=11, label=14, caption=11, group=11,
         cell=(13, 12), cell_pad=((16, 8), (13, 6)),
         value=13, unit=12, button=14, button_pad=(28, 12),
         field=330, wrap=300, row_pad=4, band_gap=6, pad=(18, 12),
         foot_pad=12, tail=10, tiles=None),
    dict(name="compact",
         header=94, title=26, tagline=11,
         band=11, note=10, label=13, caption=10, group=10,
         cell=(12, 11), cell_pad=((13, 7), (11, 5)),
         value=12, unit=11, button=13, button_pad=(22, 10),
         field=286, wrap=258, row_pad=5, band_gap=8, pad=(15, 18),
         foot_pad=10, tail=10, tiles=None),
    dict(name="dense",
         header=80, title=22, tagline=10,
         band=10, note=9, label=12, caption=10, group=9,
         cell=(11, 10), cell_pad=((11, 6), (9, 4)),
         value=11, unit=10, button=12, button_pad=(18, 8),
         field=246, wrap=222, row_pad=4, band_gap=6, pad=(12, 14),
         foot_pad=8, tail=8, tiles=None),
    # Captions off like minimal, type as large as compact: the rung this
    # laptop (1536x793) lands on.  Dense is 814 px tall balanced -- its
    # captions wrap to three lines at the larger type -- and minimal was
    # 472 of the 688 on offer, a third of the window blank (owner, 11 Oct
    # 2026).  The captions are in the footer, as on minimal.
    # header 68 and foot_pad 6, not 76 and 9: the search judges a rung
    # against the canvas its own header and footer leave, and at 76/9 the
    # rung was 8 px too tall for this laptop.
    dict(name="cosy",
         header=68, title=22, tagline=10,
         band=11, note=10, label=13, caption=None, group=10,
         cell=(12, 11), cell_pad=((10, 2), (8, 3)),
         value=12, unit=11, button=13, button_pad=(22, 9),
         # The label column is its widest name plus the glyph, not
         # ``field``; the cell padding a pixel under compact's and the
         # row padding 11 are what let two cosy columns stand beside a
         # 260 px pane on a 1536 px desktop (2 px short at (12, 12)).
         # The vertical paddings are what let thirteen tiles two abreast
         # and the fourteen rows fit that desktop's 664 px under this
         # rung's own header and footer (19 px over at row_pad 4,
         # band_gap 8, tail 8 and tiles (11, 6); 8 over with tiles (11, 5)
         # and band_gap 6).
         # field 150: the widest name with its glyph is 146 px at this
         # type, and every pixel of the name column comes off the pane.
         # pad 10, tiles (10, 2), cells (8, 3), row_pad 2, band_gap 4:
         # with thirteen tiles two abreast the form was 8 px too wide and
         # 35 px too tall to stand beside a third of this laptop's
         # 1536 px.  The tiles block is 477 px of the 664 on its own at
         # (10, 4), and the best split of the four bands around it still
         # topped 698.  The type is untouched; only the air came out.
         field=150, wrap=None, row_pad=2, band_gap=3, pad=(10, 12),
         foot_pad=6, tail=2, tiles=3),
    # tiles=3, not 2: nine networks at two abreast wrap to five tile rows,
    # which is ~25 px more than a 720p viewport behind a deep taskbar has.
    dict(name="minimal",
         header=60, title=19, tagline=9,
         band=9, note=None, label=11, caption=None, group=8,
         # cell_pad a pixel tighter since the row icons (11 Oct 2026):
         # the glyphs widened the label column past what two columns
         # beside a 260 px pane have at 1366, by 19 px.
         cell=(10, 9), cell_pad=((8, 4), (6, 3)),
         value=10, unit=9, button=10, button_pad=(16, 5),
         # field 170, not 196: the captions are off at this rung, so the
         # label column only holds names, and the 26 px it gives back per
         # column is what lets two columns stand beside a 460 px picture
         # pane on a 1536 px desktop and beside 260 px on 1366.
         field=170, wrap=None, row_pad=2, band_gap=4, pad=(11, 12),
         foot_pad=5, tail=4, tiles=3),
)


#: What each start-screen setting is called, its unit, and the sentence the
#: footer shows the moment it changes -- Nielsen's visibility of system
#: status and recognition over recall, applied to a form whose captions
#: vanish at the densest layout.  Keyed by the name the variable is held
#: under (``fields[...]`` or the ``*_var`` attribute, see ``_watched``).
SETTING_INFO = {
    "network": ("Intersection", "", "the junction to simulate; its speed "
                "limit, signal timing and demand come with it"),
    "time": ("Time of day", "", "the hour of the surveyed day whose demand "
             "is used"),
    "end_time": ("End time", "s", "how many seconds of traffic to simulate"),
    "traffic": ("Traffic level", "x", "every route's arrival rate, as a "
                "multiple of the surveyed demand; also adjustable while the "
                "run plays"),
    "max_speed": ("Speed limit", "km/h", "the speed limit on every link"),
    "seed": ("Random seed", "", "the same seed gives the same run; -1 draws "
             "a fresh one"),
    "accident": ("Accidents", "", "close encounters per accident; lower "
                 "means more accidents"),
    "trace": ("Trace replay", "", "On replays trace.txt instead of "
              "simulating"),
    "signal": ("Signal control", "", "how the junction's greens are decided"),
    "signal_green": ("Green per approach", "s", "how long each leg stays "
                     "green under fixed time"),
    "geometry": ("Real geometry", "", "the surveyed medians and roundabouts; "
                 "Off is the plain roads of the Java original"),
    "pedestrians": ("Crossing pedestrians", "", "pedestrians crossing the "
                    "carriageway"),
    "friction": ("Parked vehicles", "", "parked cars, rickshaws and CNGs on "
                 "the kerbside strips"),
    "strip": ("Strip width", "m", "the lateral unit a vehicle slides by; "
              "there are no lanes"),
    "footpath": ("Footpath strip width", "m", "the same, for the footpath"),
    "render3d": ("3D view", "", "how the run is drawn; the results do not "
                 "change"),
    "ppm": ("Scale", "px/m", "screen pixels per metre of road; playback "
            "only, set in defaults.txt"),
    "speed": ("Playback speed", "ms", "the pause between frames; playback "
              "only"),
}

#: The playback strips show words, not milliseconds: a reader choosing how
#: fast a run plays does not think in frame delays.  The values behind
#: them are what ``Parameters`` takes, slowest first so the strip reads
#: as a speed scale.
SPEED_CHOICES = (("500", "Step"), ("100", "Slow"), ("50", "Normal"),
                 ("20", "Fast"), ("1", "Max"))


def choice_label(key: str, value: str) -> str:
    """The word the playback strip shows for ``value``, else the value."""
    table = {"speed": SPEED_CHOICES}.get(key)
    if table:
        for v, label in table:
            if _Segmented._same(v, value):
                return label
    return value

#: Which settings each form row stands for, by its label, so a row can
#: carry a "changed" mark without every call site naming its keys.
ROW_KEYS = {
    "Intersection": ("network",), "Time of day": ("time",),
    "End time": ("end_time",), "Traffic level": ("traffic",),
    "Speed limit": ("max_speed",), "Random seed": ("seed",),
    "Accidents": ("accident",), "Trace replay": ("trace",),
    "Signal control": ("signal", "signal_green"),
    "Real geometry": ("geometry",), "Pedestrians": ("pedestrians",),
    "Parked vehicles": ("friction",),
    "Strip width": ("strip",), "Footpath strips": ("footpath",),
    "3D view": ("render3d",), "Playback speed": ("speed",),
}


def setting_problem(key: str, value: str):
    """Why ``value`` cannot be used for the setting ``key``, as a sentence,
    or ``None`` when it can.  Nielsen's error prevention: said in the footer
    as it is typed, and again when Start is pressed, instead of a traceback
    from ``int()`` on the way into the run."""
    text = value.strip()
    try:
        if key == "end_time":
            if int(text) < 1:
                raise ValueError
        elif key == "seed":
            int(text)
        elif key == "signal_green":
            if float(text) < 1:
                raise ValueError
        elif key in ("max_speed", "accident", "traffic", "ppm", "strip",
                     "footpath"):
            if float(text) <= 0:
                raise ValueError
        elif key == "speed":
            if int(float(text)) < 1:
                raise ValueError
    except ValueError:
        label = SETTING_INFO[key][0]
        if key in ("end_time", "speed"):
            return f"{label} must be a whole number of 1 or more."
        if key == "seed":
            return f"{label} must be a whole number (-1 draws a fresh one)."
        if key == "signal_green":
            return f"{label} must be at least 1 second."
        return f"{label} must be a number above zero."
    return None


#: The order the help page lists the settings in: the form's own.
HELP_SETTING_ORDER = ("network", "time", "end_time", "traffic", "max_speed",
                      "seed", "accident", "trace", "signal", "signal_green",
                      "geometry", "pedestrians", "friction", "strip",
                      "footpath", "render3d", "speed")

#: A sentence more than SETTING_INFO's meaning for the settings a reader
#: asks about; the help page prints it after the meaning.
HELP_SETTING_MORE = {
    "network": "A surveyed junction carries 24-hour turning counts, an "
               "hourly demand profile and an hourly vehicle mix; an "
               "imported one carries synthetic demand at a uniform rate.",
    "time": "Only a surveyed junction has an hourly profile, so the row "
            "appears for those alone.",
    "seed": "Type a number to repeat a run exactly, objects and "
            "pedestrians included; Randomise draws a fresh one each click; "
            "-1 draws one at Start and never shows it.",
    "trace": "A run records trace.txt as it plays; On replays that file "
             "instead of simulating, and the recording must be of this "
             "place.",
    "signal": "Fixed time gives each approach the same green in turn. "
              "Biased random weights random greens by each approach's "
              "share of the traffic. The two Planned controllers plan "
              "every cycle with NSGA-II (the multi-objective v1 and v2 of "
              "Rahaman et al., 2025): all alike counts every vehicle the "
              "same, by class prices motorised and non-motorised apart.",
    "geometry": "Off reproduces the original Java program's results "
                "exactly.",
    "strip": "The model has no lanes: a carriageway is a row of strips "
             "and a vehicle takes as many as its width needs. 0.5 m is "
             "the surveyed non-lane traffic; a strip wider than any "
             "vehicle, 2.5 m say, is a lane.",
    "render3d": "Line art draws one silhouette per part and is the "
                "lighter of the two; the view can be switched while the "
                "run plays.",
    "speed": "Step, Slow, Normal, Fast and Max are 500, 100, 50, 20 and "
             "1 ms between frames; the run-screen sidebar has the same "
             "slider, live.",
}


def help_sections():
    """The help page, as (heading, paragraphs) pairs.  Built from the
    same table the footer reads (SETTING_INFO), so a setting cannot be
    on the form and missing from the help."""
    settings = []
    for key in HELP_SETTING_ORDER:
        label, unit, meaning = SETTING_INFO[key]
        line = f"{label}: {meaning[0].upper()}{meaning[1:]}"
        if unit and unit != "x":
            line += f" ({unit})"
        line += "."
        more = HELP_SETTING_MORE.get(key)
        if more:
            line += " " + more
        settings.append(line)
    return [
        ("What DhakaSim does",
         ["DhakaSim is a microscopic simulator for traffic that keeps to "
          "no lanes: a carriageway is a row of half-metre strips, and a "
          "rickshaw, a bus and a motorbike share it by taking the strips "
          "their width needs. A run generates vehicles from surveyed "
          "demand, moves them with a car-following model, holds them at "
          "signals, and writes per-link and per-type statistics with an "
          "HTML report at the end.",
          "This Python build is a port of the Java original and gives "
          "the same numbers under a matched seed. Everything added since "
          "-- real junction geometry, stop lines, the keep-clear rule, "
          "turning discipline -- carries an Off setting that restores "
          "the Java behaviour."]),
        ("The start screen",
         ["Choose a junction, set the run, press Start simulation. The "
          "footer line says what Start will run and counts the settings "
          "that differ from the place's own defaults; a changed row "
          "carries a dot before its name. A value that cannot be used is "
          "named in red as it is typed, and Start refuses it with the "
          "same sentence.",
          "Undo: Ctrl+Z takes back the last change, one change at a time; "
          "clicking a row's name takes back that setting's last change. "
          "Reset to defaults reloads every setting from the place's own "
          "values. Choosing a junction loads that place's speed limit, "
          "green time and switches.",
          "The pictures beside the settings show the setting under the "
          "pointer, one picture per choice: the four signal controllers "
          "side by side, the two geometry modes, the strip grid at three "
          "widths, a map of each junction. Each is a seeded run a few "
          "dozen steps in, drawn by the run screen's own panel; drag to "
          "pan and wheel to zoom in any of them."]),
        ("The settings", settings),
        ("The run screen",
         ["Space pauses and resumes. V switches between the plan view and "
          "the 3D view, with the camera tilting between them; M hides and "
          "shows the background map; F1 opens this page. Drag to pan and "
          "wheel to zoom in the plan view; in 3D, drag orbits, right-drag "
          "or Shift+drag pans, and a double-click resets the camera.",
          "The toolbar's Zoom slider and Reset frame the network; the "
          "Traffic slider multiplies every route's arrivals from the next "
          "arrival on, so a junction can be loaded up while the run plays; "
          "the Background map toggle hides the imagery. While the map is "
          "shown the zoom snaps to the steps the imagery can be drawn at.",
          "The sidebar names the place, counts the vehicles on the "
          "network by type, keys the side-friction colours, and keeps the "
          "run's settings in view. Playback speed is the one setting that "
          "can be changed mid-run; it changes nothing but the pace. New "
          "simulation returns to the start screen, asking first if the "
          "run is unfinished; Open report opens the finished run's "
          "report."]),
        ("Importing a map",
         ["Import map... on the start screen takes a place name, a "
          "\"lat, lon\" pair or a pasted Google Maps or OpenStreetMap "
          "link, fetches the roads, and shows them over the map. Click "
          "the junction to simulate, choose which legs to keep, say "
          "whether it is signalised or a roundabout, and Import builds a "
          "runnable network with its imagery and its links bent onto the "
          "real streets. The demand is synthetic until real counts are "
          "supplied. An imported junction appears as a tile under "
          "Imported, and only those tiles can be removed."]),
        ("Results and files",
         ["Every run appends per-link speed, waiting and flow and per-type "
          "speed, waiting, trip time, fuel and accident rows under "
          "statistics/csv/, and writes a self-contained HTML report with "
          "animated plan and 3D replays under statistics/. A seed typed "
          "on the start screen reproduces a run to the byte.",
          "Each network's defaults.txt pins the settings the place is "
          "studied with; a run without the window is "
          "run_dhakasim.py --headless --network <name>, with "
          "--set NAME=VALUE for any parameter and --seed for a "
          "repeatable one. README.md beside the program documents every "
          "parameter, file and quirk."]),
    ]


def setting_message(key: str, value: str, default=None) -> str:
    """The footer's sentence for a change: what moved, to what, what that
    means, and what it was -- so the reader never has to recall it."""
    label, unit, meaning = SETTING_INFO[key]
    shown = value if not unit else (f"{value} {unit}" if unit != "x"
                                    else f"x{value}")
    text = f"{label} \u2192 {shown}: {meaning}."
    if default is not None and default != value:
        was = default if not unit else (f"{default} {unit}" if unit != "x"
                                        else f"x{default}")
        text += f"  Was {was}."
    elif default is not None:
        text += "  Back to the place's default."
    return text


#: What the preview picture shows for a setting, by key, as a sentence
#: that may name the value (``{v}``) and the place (``{place}``) -- the
#: caption under the picture.  Nielsen's "match between system and the
#: real world": the setting is explained by what it does to the scene.
PREVIEW_NOTES = {
    "network": "This is {place}: its roads over the map, as the run "
               "screen draws them. Drag to pan, wheel to zoom.",
    "time": "Demand for {v}: the arrivals a run generates.",
    "end_time": "The run stops after {v} simulated seconds.",
    "traffic": "Arrivals at x{v} the surveyed rate: the road a few dozen "
               "seconds into a run.",
    "max_speed": "Vehicles now travel at up to {v} km/h on every link.",
    "seed": "Seed {v}: a different, repeatable sequence of arrivals.",
    "accident": "{v} close encounters per accident; nothing to see until "
                "one happens.",
    "trace": "On replays trace.txt instead of simulating: the recorded "
             "vehicles on the same roads.",
    "signal": "{v} decides the greens; the red and green bars at the stop "
              "lines show the state.",
    "signal_green": "Each approach stays green for {v} s before the next "
                    "takes over.",
    "geometry": {"On": "Surveyed medians and roundabouts, drawn and driven: "
                       "a divided road is two carriageways with a solid "
                       "median, and a roundabout is a ring the traffic "
                       "circulates on.",
                 "Off": "Plain roads, as the original Java DhakaSim models "
                        "them: no medians, and a roundabout's arms meet at "
                        "a point.  Choose it to reproduce the Java program's "
                        "results exactly."},
    "pedestrians": {"On": "Pedestrians cross the carriageway -- the black "
                          "dots on the road.",
                    "Off": "Nobody crosses the carriageway."},
    "friction": {"On": "Parked cars, rickshaws and CNGs and standing "
                       "pedestrians block the kerbside strips.",
                 "Off": "The kerbside strips are clear."},
    "strip": "A carriageway is a row of {v} m strips (the grey lines) "
             "that vehicles slide between; there are no lanes.",
    "footpath": "The footpath along each kerb (inside the dark line) is "
                "divided into {v} m strips the same way.",
    "render3d": {"Off": "Plan view: straight down, north up.",
                 "Line art": "The same scene from a low camera, each "
                             "vehicle a line-art model.",
                 "Solid": "The same scene from a low camera, each vehicle "
                          "a solid, lit model."},
    "ppm": "{v} screen pixels per metre is the model's own resolution; the "
           "picture is zoomed to fit, so only the rounding of positions "
           "changes.",
    "speed": "{v} ms between frames: how fast a run plays. The results are "
             "the same at every speed.",
}


def preview_note(key: str, value: str, place: str = "") -> str:
    """The caption the preview shows for ``key`` at ``value``."""
    note = PREVIEW_NOTES.get(key, "")
    if isinstance(note, dict):
        note = note.get(value, "")
    if key == "seed" and value.strip() == "-1":
        return ("A fresh random run each time; the pictures are seeded "
                "so they are the same every time.")
    return note.format(v=value, place=place)


#: The choices each strip offers, in the strip's order: a row with n of
#: them previews n pictures, one per choice.  ``test_feedback`` pins these
#: against the strips' own lists.
PREVIEW_CHOICES = {
    "traffic": ("0.5", "1", "1.5", "2", "3"),
    "trace": ("Off", "On"),
    "signal": ("Fixed time", "Biased random", "Planned, all alike",
               "Planned, by class"),
    "geometry": ("Off", "On"),
    "pedestrians": ("Off", "On"),
    "friction": ("Off", "On"),
    "strip": ("0.25", "0.5", "1.0"),
    "footpath": ("0.25", "0.5", "1.0"),
    "render3d": ("Off", "Line art", "Solid"),
}

#: The line under each choice's picture: one short phrase, since a cell
#: of a two-abreast grid is two hundred pixels wide.  ``{v}`` is the
#: value, ``{green}`` the form's seconds of green.
PREVIEW_CELL_NOTES = {
    "traffic": "x{v} the surveyed arrival rate",
    "trace": {"Off": "A new run is simulated",
              "On": "trace.txt replayed; nothing is simulated"},
    "signal": {"Fixed time": "Each approach green in turn, {green} s each",
               "Biased random": "Random greens, weighted by each approach's "
                                "share of the traffic",
               "Planned, all alike": "Greens planned every cycle; every "
                                     "vehicle counts alike",
               "Planned, by class": "Greens planned every cycle; the "
                                     "vehicle classes priced apart"},
    "geometry": {"Off": "Plain roads as the Java original models them: no "
                        "medians, arms meeting at a point",
                 "On": "Surveyed medians and roundabout ring, drawn and "
                       "driven"},
    "pedestrians": {"Off": "Nobody crosses the carriageway",
                    "On": "Pedestrians cross the carriageway (the black dots)"},
    "friction": {"Off": "The kerbside strips are clear",
                 "On": "Parked cars, rickshaws, CNGs and hawkers on the "
                       "kerbside strips"},
    "strip": "{v} m strips across the carriageway (the grey lines)",
    "footpath": "{v} m strips along each footpath (inside the dark line)",
    "render3d": {"Off": "Plan view, north up",
                 "Line art": "Low camera, line-art models (the fastest)",
                 "Solid": "Low camera, solid lit models"},
}


def preview_cell_note(key: str, value: str, green: str = "") -> str:
    """The short line under the picture of ``key`` at ``value``."""
    note = PREVIEW_CELL_NOTES.get(key, "")
    if isinstance(note, dict):
        note = note.get(value, "")
    return note.format(v=value, green=green)


#: Settings a change of which only changes how the preview is drawn, not
#: what is simulated -- no rebuild of the run behind it.
PREVIEW_VIEW_KEYS = ("render3d", "speed")


#: Which value the right-hand picture shows for a setting when the pointer
#: is on its row and on no particular cell: the other side of a switch, the
#: next size up, the scheduler against fixed time.  None means the setting
#: has no picture of its own (an end time, a seed) and the right slot says so.
def preview_alternative(key: str, value: str):
    value = (value or "").strip()
    if key in ("geometry", "pedestrians", "friction", "trace"):
        return "Off" if value == "On" else "On"
    if key == "render3d":
        return "Line art" if value == "Off" else "Off"
    if key in ("strip", "footpath"):
        ladder = ["0.25", "0.5", "1.0"]
        try:
            index = next(i for i, v in enumerate(ladder)
                         if abs(float(v) - float(value)) < 1e-9)
        except (StopIteration, ValueError):
            return "0.5"
        return ladder[(index + 1) % len(ladder)]
    if key == "signal":
        return "Planned, all alike" if value == "Fixed time" else "Fixed time"
    if key == "traffic":
        return "1" if value == "3" else "3"
    return None


#: Settings whose alternative is a second *run* (the model differs), as
#: opposed to the same run drawn differently (render3d) or replayed (trace).
PREVIEW_MODEL_KEYS = ("geometry", "pedestrians", "friction", "strip",
                      "footpath", "signal", "traffic")


class _PreviewHost:
    """What the preview panel sees as its frame: a run screen with no
    toolbar, no legend and no status line, so every call is a no-op."""

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class _PreviewPanel(DhakaSimPanel):
    """The run screen's panel, drawing one still of a run the start screen
    owns.

    ``overrides`` are the ``Parameters`` this picture's run differs by from
    the form's -- the lower picture of a pair -- swapped in around every
    paint (``_with``), together with the run's own step counter, because
    ``Parameters`` is process-wide and the two runs of a pair must not read
    each other's settings.  ``show`` is the whole of what a picture can be:
    a view (plan or 3D), a framing, with or without the traffic, with or
    without the strip grid.
    """

    #: How much road the junction framing shows.  The run screen opens on
    #: the whole network; at a few hundred pixels that is a few specks, and
    #: the point of the picture is what is on the road.
    SPAN_METRES = 110.0
    #: The signal framing: closer than the junction's, so the bars at the
    #: stop lines are more than a few pixels.
    SIGNAL_SPAN_METRES = 80.0
    #: The arm framing, for the strip rows: close enough that a 0.25 m
    #: strip is a few pixels wide and the grid reads as a grid.
    ARM_SPAN_METRES = 36.0
    #: How far up the arm from the junction's centre the arm framing is
    #: centred: past the box and the stop line, where the queue stands.
    ARM_OFFSET_METRES = 30.0
    #: The 3D view's corner help line is the run screen's; here the caption
    #: under the picture does that job, and at this width it was clipped.
    show_help = False
    label_scale = 0.5
    show_compass = False
    scale_bar_min_px = 36
    #: The strip grid: the carriageway's strip boundaries in grey, the two
    #: footpath bands' inner edges darker, one pixel each, on the plain
    #: white road -- the imagery is off for these pictures, both so the
    #: grid reads and because its zoom ladder tops out short of the arm
    #: framing (``BaseMap.snap``: a picture is never magnified past 2x).
    STRIP_COLOUR = Color(160, 160, 160)
    FOOTPATH_COLOUR = Color(80, 80, 80)

    def __init__(self, parent, processor, width, height):
        super().__init__(_PreviewHost(), parent, processor=processor,
                         trace=False)
        self.canvas.configure(width=width, height=height)
        self._size = (width, height)
        self.scene3d.label_max_px = 14
        self.overrides = {}
        self.sim_step = Parameters.simulation_step
        self.draw_traffic = True
        self.show_strips = False
        self._zoom = "junction"
        self._trace_start = None        # where trace.txt's first frame starts

    def toast(self, text, seconds=None) -> None:
        """No toasts: the caption under the picture says what it is."""

    def dispose(self) -> None:
        # The imagery is the pane's, shared by both pictures of a place
        # (see _SettingsPreview._share_basemap), so this picture must not
        # forget it; the pane does when it is torn down.
        self._basemap = None
        super().dispose()

    def _with(self, action):
        """Run ``action`` under this picture's own Parameters and clock."""
        saved = {k: getattr(Parameters, k) for k in self.overrides}
        saved_step = Parameters.simulation_step
        for k, v in self.overrides.items():
            setattr(Parameters, k, v)
        Parameters.simulation_step = self.sim_step
        try:
            return action()
        finally:
            self.sim_step = Parameters.simulation_step
            Parameters.simulation_step = saved_step
            for k, v in saved.items():
                setattr(Parameters, k, v)

    def paint(self) -> None:
        """Redraw, under this picture's own Parameters.  A trace picture
        is always the recording's first frame, so a redraw rewinds it."""
        def go():
            if self.trace_reader is not None and self._trace_start is not None:
                self.trace_reader.seek(self._trace_start)
            self.repaint()
        self._with(go)

    def show(self, view, zoom, traffic, strips) -> None:
        """Make this picture ``view`` ("Off", "Line art", "Solid") of the
        run, framed by ``zoom`` ("fit", "junction", "arm"), with or without
        the traffic and the strip grid, and draw it."""
        def go():
            on, style = OptionPanel.VIEW_CHOICES.get(view, (False, "line"))
            self.draw_traffic = bool(traffic)
            self.show_strips = bool(strips)
            self.show_basemap = not strips      # see STRIP_COLOUR
            self.scene3d.style = style
            # Neither flag is in the static layers' keys: a full redraw.
            self._static2d_key = self._static3d_key = None
            self.view_3d = False
            self.frame_view(zoom)
            if on:
                self.set_view_3d(True, animate=False)
            self.paint()
        self._with(go)

    def set_size(self, width, height) -> None:
        """The holder was resized: the same picture at the new size."""
        if (width, height) == self._size:
            return
        self.canvas.configure(width=width, height=height)
        self._size = (width, height)
        view = ("Line art" if self.scene3d.style == "line" else "Solid") \
            if self.view_3d else "Off"
        self.show(view, self._zoom, self.draw_traffic, self.show_strips)

    # ---- framing ------------------------------------------------------------

    def frame_view(self, zoom="junction") -> None:
        """Frame the plan view: the whole network ("fit"), the main
        junction a hundred metres or so across ("junction"), the nearest
        signalised one the same way ("signal"), or one of the main
        junction's arms a few dozen metres across ("arm").

        The junction is the roundabout if there is one, else the junction
        (three or more arms) nearest the network's centre -- not
        ``basemap.anchor_point``, whose bounding-box fallback put Miami's
        picture on a block of houses.  Home (double-click) is always the
        whole-network fit.
        """
        self._zoom = zoom
        width, height = self._size
        ppm = Parameters.pixel_per_meter
        bounds = basemap_module.network_bounds(self.link_list, margin=30.0)
        if bounds:
            x0, y0, x1, y1 = bounds
            self.translate_x = width / 2.0 - (x0 + x1) / 2.0 * ppm
            self.translate_y = height / 2.0 - (y0 + y1) / 2.0 * ppm
        self._home_translate = (self.translate_x, self.translate_y)
        self.home_scale = self.fit_scale(width, height)
        if zoom == "fit" or not bounds:
            self.set_scale(self.home_scale)
            return
        try:
            centre, node = self._junction(signalised=(zoom == "signal"))
            span = (self.SIGNAL_SPAN_METRES if zoom == "signal"
                    else self.SPAN_METRES)
            if zoom == "arm" and node is not None:
                centre = self._arm_point(centre, node)
                span = self.ARM_SPAN_METRES
        except Exception:
            centre, span = None, self.SPAN_METRES    # the network's centre
        if centre is not None:
            self.translate_x = width / 2.0 - centre[0] * ppm
            self.translate_y = height / 2.0 - centre[1] * ppm
        close = width / max(span * ppm, 1.0)
        if zoom == "arm":
            self.set_scale(min(self.ZOOM_MAX, close))
        else:
            self.set_scale(min(self.ZOOM_MAX, max(self.home_scale, close)))

    def _junction(self, signalised=False):
        """(centre in metres, node) of the junction the pictures open on:
        the roundabout, else the junction of three or more arms nearest
        the network's centre.  ``signalised`` asks for one with signals
        -- the signal pictures want bars to show -- and falls back to the
        usual when the network has none."""
        x0, y0, x1, y1 = basemap_module.network_bounds(self.link_list,
                                                       margin=30.0)
        mid = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        if signalised:
            lit = [n for n in self.node_list
                   if n.number_of_links() >= 3 and n.is_signalised()
                   and not n.is_roundabout()]
            if lit:
                node = min(lit, key=lambda n: self._far(n, mid))
                return road_geometry.node_point(self.link_list, node), node
        roundabout = basemap_module.read_roundabout(Parameters.NETWORK_DIR)
        if roundabout is not None:
            centre = basemap_module.anchor_point(
                self.link_list, self.node_list, roundabout)[:2]
            if centre is not None:
                node = min(self.node_list, key=lambda n: self._far(n, centre))
                return centre, node
        junctions = [n for n in self.node_list
                     if n.number_of_links() >= 3] or list(self.node_list)
        node = min(junctions, key=lambda n: self._far(n, mid))
        return road_geometry.node_point(self.link_list, node), node

    def _far(self, node, point):
        x, y = road_geometry.node_point(self.link_list, node)
        return (x - point[0]) ** 2 + (y - point[1]) ** 2

    def _arm_point(self, centre, node):
        """A point ``ARM_OFFSET_METRES`` up the longest arm of ``node``,
        following the arm's own line, so a bent arm is still in frame."""
        best = None
        for j in range(node.number_of_links()):
            link = self.link_list[node.get_link(j)]
            segments = [link.get_segment(i)
                        for i in range(link.get_number_of_segments())]
            if link.get_up_node() != node.get_id():
                segments.reverse()
            length = sum(s.get_length() for s in segments)
            if best is None or length > best[0]:
                best = (length, segments, link.get_up_node() == node.get_id())
        if best is None:
            return centre
        _length, segments, forward = best
        left = self.ARM_OFFSET_METRES
        for seg in segments:
            if forward:
                sx, sy, ex, ey = (seg.get_start_x(), seg.get_start_y(),
                                  seg.get_end_x(), seg.get_end_y())
            else:
                sx, sy, ex, ey = (seg.get_end_x(), seg.get_end_y(),
                                  seg.get_start_x(), seg.get_start_y())
            length = seg.get_length()
            if length >= left:
                t = left / length if length > 0 else 0.0
                return (sx + (ex - sx) * t, sy + (ey - sy) * t)
            left -= length
        last = segments[-1]
        return ((last.get_start_x(), last.get_start_y()) if not forward
                else (last.get_end_x(), last.get_end_y()))

    # ---- the strip grid -----------------------------------------------------

    def draw_road_network(self, g2d) -> None:
        super().draw_road_network(g2d)
        if self.show_strips and not self.view_3d:
            self._draw_strips(g2d)

    def _draw_strips(self, g2d) -> None:
        """The strips a carriageway is divided into, as the model divides
        it: a footpath strip along each kerb and ``strip_width`` strips
        between them, exactly as ``Segment._initialize`` counts them and
        ``Vehicle.draw_vehicle`` places a vehicle on them.  Only segments
        near the view are drawn; the grid is in the static layer."""
        ppm = Parameters.pixel_per_meter
        fp = Parameters.footpath_strip_width * ppm
        sw = Parameters.strip_width * ppm
        if sw <= 0.0:
            return
        width, height = self._size
        scale = max(self.scale, 1e-9)
        reach = max(width, height) / scale
        cx = width / 2.0 - self.translate_x
        cy = height / 2.0 - self.translate_y
        g2d.set_stroke(1)
        for link in self.link_list:
            for j in range(link.get_number_of_segments()):
                seg = link.get_segment(j)
                x1, y1 = seg.get_start_x() * ppm, seg.get_start_y() * ppm
                x2, y2 = seg.get_end_x() * ppm, seg.get_end_y() * ppm
                w = seg.get_segment_width() * ppm
                if math.hypot(x2 - x1, y2 - y1) <= 1e-9 or w <= 0.0:
                    continue
                # Skip a segment whose whole extent lies beyond the view;
                # a long one crossing it has both ends far away.
                if (min(x1, x2) > cx + reach + w or max(x1, x2) < cx - reach - w
                        or min(y1, y2) > cy + reach + w
                        or max(y1, y2) < cy - reach - w):
                    continue
                inner = seg.number_of_strips() - 2    # carriageway strips
                for k in range(inner + 1):
                    d = fp + k * sw
                    if d > w:
                        break
                    g2d.set_color(self.FOOTPATH_COLOUR
                                  if k in (0, inner) else self.STRIP_COLOUR)
                    g2d.draw_line(Utilities.return_x3(x1, y1, x2, y2, d),
                                  Utilities.return_y3(x1, y1, x2, y2, d),
                                  Utilities.return_x4(x1, y1, x2, y2, d),
                                  Utilities.return_y4(x1, y1, x2, y2, d))


class _SettingsPreview:
    """The pictures beside the settings.

    A game's graphics menu shows a setting as the scene with it each way.
    This does that with the simulator's own scene, as stills: a row with
    n choices previews n pictures in a grid, one per choice, each with a
    short line saying what that choice is, the one the form has marked.
    The junction rows show one picture, a map of the place -- its roads
    over its imagery with nothing driving on them -- the hovered tile's
    place while the pointer is on a tile.  Every other picture is a
    seeded run a few dozen steps in: the form's run for the choice the
    form has, a second run built with that one choice for the others
    (real geometry, pedestrians, parked vehicles, signal control, traffic,
    the strip widths); the 3D view row draws the form's run from each
    camera; trace replay shows the recording's vehicles.  Settings with
    no choices (end time, seed) show the form's run and a sentence.
    Every picture is drawn by :class:`DhakaSimPanel`, the run screen's own
    class, so it is what Start will show.  Nothing plays: a picture is
    drawn once and redrawn only when a setting, the pointer or the window
    moves.

    What keeps it honest.  The form's values are written into
    ``Parameters`` exactly as Start writes them (``_apply_to_parameters``),
    under a snapshot that ``stop`` puts back; each other run's own
    differences are captured as a diff of ``Parameters`` taken around its
    build and swapped in only while its picture paints
    (``_PreviewPanel``).  Statistics go to a temp folder, no run reaches
    its end time, the trace file is never written.  ``_on_network_change``
    stops the pane before writing a place's defaults, so the restore
    cannot undo them.  Every change is debounced, a value that cannot be
    used asks for nothing, and the pictures of a row are built one per
    event-loop turn (``_pump``) so the form stays responsive while a
    four-picture row builds.  Runs are cached per (setting, value) until
    the form's run is rebuilt, so returning to a row costs paints only.

    The column it lives in is always there -- ``OptionPanel._choose_layout``
    lays the settings out around it -- and the grid fills it, each cell
    sharing the height and width (``_fit_slot`` resizes the canvases with
    their holders), so the column is pictures from top to bottom.
    """

    WIDTH = 460
    MIN_WIDTH = 260
    DEBOUNCE_MS = 300
    #: How far into a run the pictures are taken: enough for the queues at
    #: the stop lines and the parked kerbs to have formed.
    WARM_UP_STEPS = 40
    #: Up to this many pictures stand in one column; more go two abreast.
    ONE_COLUMN_UP_TO = 3

    def __init__(self, option, host):
        self.option = option
        self.host = host                # the column the pictures live in
        self.mode = "hide"              # hide | dock
        self.width = None
        self.frame = None
        self._grid = None
        self.cells = []                 # one dict per picture, see _lay_cells
        self.headers = ()
        self.holders = ()
        self._note = self._counts = None
        self.panel = None               # the picture of the form's own run
        self._run = None                # the form's run (a Processor)
        self._run_step = 0
        self._runs = {}                 # (key, value) -> (run, overrides, step)
        self._layout = None             # (key, values, specs) the grid shows
        self._queue = []                # cells still to build
        self._pump_job = None
        self._job = None
        self._key = "network"           # the row last changed
        self._dirty = True              # a setting changed since the build
        self._hover_key = None          # the row under the pointer
        self._peek = None               # (key, value) of the cell under it
        self._saved = None
        self._stats_dir = None
        self._basemaps = {}             # network dir -> its BaseMap, shared

    # ---- the widgets -----------------------------------------------------------

    def _build_widgets(self, parent, width) -> None:
        self.width = width
        frame = tk.Frame(parent, background=_UI["panel"],
                         highlightthickness=1,
                         highlightbackground=_UI["edge"],
                         highlightcolor=_UI["edge"])
        self.frame = frame
        band = tk.Frame(frame, background=_UI["band"])
        band.pack(fill="x")
        _glass(band)
        tk.Label(band, text="PREVIEW", font=("Segoe UI Semibold", 11),
                 background=_UI["band"], foreground=_UI["accent_text"],
                 padx=14, pady=5).pack(side="left")
        tk.Label(band, text="  each choice, side by side",
                 font=("Segoe UI", 9), background=_UI["band"],
                 foreground=_UI["muted"]).pack(side="left")
        # Bottom first, so the caption keeps its lines whatever the height
        # and the pictures share the rest.
        self._note = tk.Label(frame, text="", anchor="nw", justify="left",
                              wraplength=width - 28, height=3,
                              font=("Segoe UI", 10),
                              background=_UI["panel"],
                              foreground=_UI["text"])
        self._note.pack(side="bottom", fill="x", padx=14, pady=(2, 10))
        self._counts = tk.Label(frame, text="", anchor="w",
                                font=("Segoe UI", 9),
                                background=_UI["panel"], foreground=_UI["muted"])
        self._counts.pack(side="bottom", fill="x", padx=14, pady=(4, 0))
        self._grid = tk.Frame(frame, background=_UI["panel"])
        self._grid.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        _glass(frame)
        _round_corners(frame, 12, top_fill=_UI["band"])
        # Torn down with the start screen (New simulation, Reset, Start):
        # the run that follows must find Parameters as the form left them.
        # Only for the pane that is current: Tk delivers <Destroy> from
        # the event loop, so a pane replaced by a re-dock reports its
        # destruction after its successor has scheduled a build, and a
        # stop here would cancel that build.
        frame.bind("<Destroy>",
                   lambda e: self.stop()
                   if e.widget is frame and self.frame is frame else None)

    def _lay_cells(self, values) -> None:
        """One cell per value in a grid: a header, a picture, a line."""
        self._drop_cells()
        for child in self._grid.winfo_children():
            child.destroy()
        n = max(1, len(values))
        cols = 1 if n <= self.ONE_COLUMN_UP_TO else 2
        rows = (n + cols - 1) // cols
        for r in range(rows):
            self._grid.grid_rowconfigure(r, weight=1, uniform="row")
        for c in range(cols):
            self._grid.grid_columnconfigure(c, weight=1, uniform="col")
        # Rows and columns the previous grid had and this one does not
        # must stop claiming space.
        for r in range(rows, 8):
            self._grid.grid_rowconfigure(r, weight=0, uniform="")
        for c in range(cols, 2):
            self._grid.grid_columnconfigure(c, weight=0, uniform="")
        gap = 8
        wrap = max(60, (self.width - 24 - (cols - 1) * gap) // cols - 4)
        cells = []
        for index, value in enumerate(values):
            r, c = divmod(index, cols)
            cell = tk.Frame(self._grid, background=_UI["panel"])
            # The same padding on every cell, so the uniform grid gives
            # every picture the same size; the outer half-gaps are the
            # grid's own margin.
            cell.grid(row=r, column=c, sticky="nsew",
                      padx=gap // 2, pady=gap // 2)
            header = tk.Label(cell, text="", anchor="w", width=1,
                              font=("Segoe UI Semibold", 9),
                              background=_UI["panel"],
                              foreground=_UI["muted"])
            header.pack(fill="x")
            holder = tk.Frame(cell, background=_UI["seg_off"],
                              highlightthickness=1,
                              highlightbackground=_UI["edge"])
            holder.pack(fill="both", expand=True, pady=(2, 2))
            holder.pack_propagate(False)
            holder.bind("<Configure>",
                        lambda _e, i=index: self._fit_slot(i))
            desc = tk.Label(cell, text="", anchor="nw", justify="left",
                            wraplength=wrap, height=2,
                            font=("Segoe UI", 8),
                            background=_UI["panel"], foreground=_UI["muted"])
            desc.pack(fill="x")
            cells.append({"value": value, "spec": None, "panel": None,
                          "frame": cell, "header": header, "holder": holder,
                          "desc": desc})
        self.cells = cells
        self.headers = tuple(c["header"] for c in cells)
        self.holders = tuple(c["holder"] for c in cells)
        self.frame.update_idletasks()

    def _fit_slot(self, index) -> None:
        """A holder changed size: its picture follows it."""
        if index >= len(self.cells):
            return
        cell = self.cells[index]
        panel = cell["panel"]
        if panel is None:
            return
        w, h = cell["holder"].winfo_width(), cell["holder"].winfo_height()
        if w > 2 and h > 2:
            try:
                panel.set_size(w, h)
            except tk.TclError:
                pass

    def _drop_cells(self) -> None:
        """Drop the pictures (not the runs behind them)."""
        self._cancel_pump()
        for cell in self.cells:
            panel = cell["panel"]
            if panel is None:
                continue
            reader = getattr(panel, "trace_reader", None)
            try:
                panel.dispose()
            except tk.TclError:
                pass
            if reader is not None:
                try:
                    reader.close()
                except OSError:
                    pass
            cell["panel"] = None
        self.panel = None

    def _discard(self) -> None:
        """Drop the runs and the widgets."""
        self.stop()
        if self.frame is not None:
            try:
                self.frame.destroy()
            except tk.TclError:
                pass
        self.frame = self._grid = None
        self.cells = []
        self.headers = self.holders = ()
        self._note = self._counts = None
        self.mode = "hide"
        for base in self._basemaps.values():
            base.forget()
        self._basemaps.clear()

    # ---- placement ----------------------------------------------------------

    def resize(self, width) -> None:
        """The pictures at ``width`` pixels, in their column.

        The column is always there -- the form is laid out around it, see
        ``OptionPanel._fit_to_window`` -- so this only rebuilds the widgets
        when the width the search chose has changed, which costs the runs
        behind the pictures and is why it is not done on every resize; a
        change of height alone resizes the canvases in place.
        """
        width = int(width)
        if self.mode == "dock" and width == self.width and self.frame is not None:
            return
        self._discard()
        self._build_widgets(self.host, width)
        self.frame.pack(fill="both", expand=True)
        self.mode = "dock"
        self.request(self._key)

    # ---- requests from the form ----------------------------------------------

    def request(self, key: str) -> None:
        """A setting changed: refresh the pictures shortly, naming it."""
        self._key = key
        self._dirty = True
        self._schedule()

    def focus(self, key) -> None:
        """The pointer is on a row (``None``: on none)."""
        if key == self._hover_key:
            return
        self._hover_key = key
        if key is None:
            self._peek = None
        self._schedule()

    def peek(self, key: str, value) -> None:
        """The pointer is on one cell of a strip (``value`` None: left it)."""
        self._peek = (key, str(value)) if value is not None else None
        if value is not None:
            self._hover_key = key
        self._schedule()

    def _schedule(self) -> None:
        if self.frame is None:
            return
        if self._job is not None:
            try:
                self.frame.after_cancel(self._job)
            except tk.TclError:
                pass
        try:
            self._job = self.frame.after(self.DEBOUNCE_MS, self._refresh)
        except tk.TclError:
            self._job = None

    # ---- what the pictures should show ----------------------------------------

    def _focused(self):
        """The row the pictures are about, the form's value for it, and
        the value under the pointer (None when on no cell)."""
        values = self.option._values()
        key = self._hover_key or self._key
        current = values.get(key, "")
        hovered = None
        if self._peek is not None and self._peek[0] == key:
            hovered = self._peek[1]
        return key, current, hovered

    def _values_for(self, key, current, hovered):
        """The values the row previews, one picture each."""
        if key == "network":
            return [hovered or current]
        choices = PREVIEW_CHOICES.get(key)
        if not choices:
            return [current]
        values = list(choices)
        if not any(_Segmented._same(v, current) for v in values):
            values.insert(0, current)   # a value the strip does not offer
        return values

    def _spec_for(self, key, value, current):
        """What the picture of ``value`` shows: ("map", network),
        ("run", view, zoom, strips) -- the form's own run --, ("view",
        view, zoom, strips) -- the form's run from that camera --,
        ("trace", zoom), ("model", key, value, zoom, strips) -- a second
        run built with that one value -- or a sentence for no picture.
        The zoom is "arm" for the strip rows, "signal" for the signal
        rows (a junction with bars to show), else "junction"."""
        if key == "network":
            return ("map", value)
        if key == "speed":
            # A pace changes nothing on the road, so a picture of the
            # road would show nothing: the speeds and their milliseconds
            # instead (owner, 12 Oct 2026).
            return ("sheet", tuple((label, f"{v} ms")
                                   for v, label in SPEED_CHOICES), value)
        strips = key in ("strip", "footpath")
        zoom = ("arm" if strips else "signal" if key in ("signal", "signal_green")
                else "junction")
        view = self.option.render3d_var.get()
        is_current = _Segmented._same(value, current)
        if key == "render3d":
            return ("view", value, zoom, strips)
        if key == "trace":
            if value != "On":
                return ("run", view, zoom, False)
            if not os.path.isfile("trace.txt"):
                return "On replays trace.txt; none has been recorded yet."
            if not self._trace_is_here():
                return ("On replays trace.txt, which holds a run of another "
                        "place; its vehicles would land off these roads.  "
                        "Run this junction once to record it.")
            return ("trace", zoom)
        if key in PREVIEW_MODEL_KEYS and not is_current:
            return ("model", key, value, zoom, strips)
        return ("run", view, zoom, strips)

    # ---- the pictures ---------------------------------------------------------

    def _alive(self) -> bool:
        return (self.mode != "hide" and self.frame is not None
                and self.frame.winfo_exists())

    def _refresh(self) -> None:
        self._job = None
        if not self._alive():
            return
        if self.option._validate():
            return                      # the footer is already saying why
        rebuilt = False
        if self._run is None or (self._dirty
                                 and self._key not in PREVIEW_VIEW_KEYS):
            # A model setting changed (or nothing is built yet): the
            # form's run is rebuilt, and every other run with it.  The
            # pointer moving over rows never rebuilds it.
            if not self._build_run():
                return
            rebuilt = True
        self._dirty = False
        self._key = self._key if self._key in SETTING_INFO else "network"
        key, current, hovered = self._focused()
        values = self._values_for(key, current, hovered)
        specs = [self._spec_for(key, v, current) for v in values]
        layout = (key, tuple(values), tuple(specs))
        if rebuilt or layout != self._layout:
            self._lay_cells(values)
            self._layout = layout
            network = self.option._values()["network"]
            for cell, spec in zip(self.cells, specs):
                cell["spec"] = spec
                cell["header"].configure(
                    text=self._cell_title(key, cell["value"], current))
                self._describe_cell(cell, key, current)
                if isinstance(spec, str):
                    self._slot_text(cell["holder"], spec)
                elif spec[0] == "sheet":
                    self._slot_sheet(cell["holder"], spec[1], spec[2])
                else:
                    self._slot_text(cell["holder"], "Building…")
            # The form's own value first, so self.panel exists soonest.
            order = sorted(range(len(self.cells)),
                           key=lambda i: not _Segmented._same(
                               self.cells[i]["value"],
                               network if key == "network" else current))
            self._queue = [i for i in order
                           if not isinstance(self.cells[i]["spec"], str)
                           and self.cells[i]["spec"][0] != "sheet"]
            self._pump()
        self._say(key, current, hovered)

    def _cell_title(self, key, value, current) -> str:
        if key == "network":
            return _place_name(value)
        label = SETTING_INFO.get(key, ("", "", ""))[0]
        if key not in PREVIEW_CHOICES:
            shown = choice_label(key, current)
            return f"{label}: {shown}" if label else shown
        unit = SETTING_INFO.get(key, ("", "", ""))[1]
        shown = (f"x{value}" if unit == "x" else f"{value} {unit}" if unit
                 else value)
        if _Segmented._same(value, current):
            return f"{shown}  ·  selected"
        return shown

    def _pump(self) -> None:
        """Build the next picture of the queue, and come back for the
        rest on the next turn of the event loop, so the form stays live
        while a four-picture row builds."""
        self._pump_job = None
        if not self._queue or not self._alive():
            return
        index = self._queue.pop(0)
        if index < len(self.cells):
            self._build_cell(index)
            key, current, hovered = self._focused()
            self._say(key, current, hovered)
        if self._queue:
            try:
                self._pump_job = self.frame.after(1, self._pump)
            except tk.TclError:
                self._pump_job = None

    def _cancel_pump(self) -> None:
        self._queue = []
        if self._pump_job is not None and self.frame is not None:
            try:
                self.frame.after_cancel(self._pump_job)
            except tk.TclError:
                pass
        self._pump_job = None

    def _build_run(self) -> bool:
        """A fresh run of the form's junction under the form's settings."""
        self.stop()                     # the previous runs, and Parameters
        if self.cells:
            self.cells[0]["header"].configure(text="Building the preview…")
            self.frame.update_idletasks()
        self._saved = Parameters.snapshot()
        try:
            self.option._apply_to_parameters(quiet=True)
            seed = Parameters.seed if Parameters.seed >= 0 else 7
            Parameters.random = JavaRandom(seed)
            Parameters.DETERMINISTIC = True
            Constants.reseed()
            Parameters.TRACE_MODE = False
            Parameters.REPORT_ANIMATION_FRAMES = 0
            Parameters.STATS_DIR = self._scratch_dir()
            Parameters.simulation_step = 1
            with contextlib.redirect_stdout(io.StringIO()):
                run = Processor()
                for _ in range(self.WARM_UP_STEPS):
                    run._run_at_each_time_step()
                    Parameters.simulation_step += 1
            self._run = run
            self._run_step = Parameters.simulation_step
            return True
        except Exception as exc:          # a broken network must not take
            self.stop()                   # the start screen down with it
            self._lay_cells([""])
            self._layout = None
            self.cells[0]["header"].configure(text="No preview for this network")
            self._slot_text(self.cells[0]["holder"], str(exc)[:120])
            self._counts.configure(text=str(exc)[:70])
            return False

    def _new_run(self, overrides, warm_up):
        """A second run built under ``overrides`` and stepped ``warm_up``
        times on a private clock: (run, the Parameters it differs by,
        its step).  The build is made under Parameters changed for it,
        and the differences the build leaves behind are captured and put
        back, to be swapped in only while its picture paints."""
        before = Parameters.snapshot()
        saved_step = Parameters.simulation_step
        try:
            for name, override in overrides.items():
                setattr(Parameters, name, override)
            Parameters.simulation_step = 1
            with contextlib.redirect_stdout(io.StringIO()):
                run = Processor()
                for _ in range(warm_up):
                    run._run_at_each_time_step()
                    Parameters.simulation_step += 1
            step = Parameters.simulation_step
            after = Parameters.snapshot()
        finally:
            Parameters.simulation_step = saved_step
            Parameters.restore(before)
        # along_pedestrian_mode is toggled every 20 steps by any run, so a
        # build catches it mid-swing; pinning it would freeze that
        # picture's hawkers on or off.
        diff = {k: v for k, v in after.items()
                if k not in ("simulation_step", "random", "show_progress_slider",
                             "along_pedestrian_mode")
                and before.get(k) != v}
        return run, diff, step

    def _model_run(self, key, value):
        """The run for ``key`` at ``value``, built once per form run."""
        found = self._runs.get((key, value))
        if found is None:
            warm_up = 0 if key == "network" else self.WARM_UP_STEPS
            found = self._new_run(self._overrides(key, value), warm_up)
            self._runs[(key, value)] = found
        return found

    def _make_panel(self, holder, run):
        """A picture in ``holder`` over ``run``.  Not drawn: ``show``
        does that."""
        for child in holder.winfo_children():
            child.destroy()
        w, h = holder.winfo_width(), holder.winfo_height()
        if w < 2 or h < 2:
            w, h = max(holder.winfo_reqwidth(), 200), max(holder.winfo_reqheight(), 150)
        panel = _PreviewPanel(holder, run, w, h)
        self._share_basemap(panel)
        panel.canvas.pack(fill="both", expand=True)
        # A still is painted once.  The run screen's paint reads the
        # canvas's own size, which is one pixel until Tk has laid it out,
        # and a picture painted at one pixel is a blank with the imagery
        # cropped to nothing; the run screen never noticed because its
        # next step repainted.  So the canvas gets its size first.
        holder.update_idletasks()
        return panel

    def _share_basemap(self, panel) -> None:
        """One decoded basemap per place for the pane's pictures.

        A panel loads its imagery lazily and decodes the PNG on its first
        paint -- a second or so for the big ones -- and every picture of
        the same place would decode it again.  The pane keeps one
        ``BaseMap`` per network and hands it to each picture of that
        place; the pictures do not forget it when they go
        (``_PreviewPanel.dispose``), the pane does (``_discard``).  Only
        the form's place and the one just built are kept, so hovering
        down the tiles does not pile up a decoded PNG per place.
        """
        network = Parameters.NETWORK_DIR
        if panel._basemap is not None:
            panel._basemap = self._basemaps.setdefault(network, panel._basemap)
        keep = {network, self.option._values().get("network")}
        for key in [k for k in self._basemaps if k not in keep]:
            self._basemaps.pop(key).forget()

    def _build_cell(self, index) -> None:
        """The picture of one cell, from its spec (see ``_spec_for``)."""
        cell = self.cells[index]
        spec, holder = cell["spec"], cell["holder"]
        key = self._layout[0] if self._layout else self._key
        if isinstance(spec, str) or spec is None or self._run is None:
            return
        try:
            kind = spec[0]
            if kind == "map":
                network = spec[1]
                if network == self.option._values()["network"]:
                    run, overrides, step = self._run, {}, self._run_step
                else:
                    run, overrides, step = self._model_run("network", network)
                panel = self._make_panel(holder, run)
                panel.overrides = dict(overrides)
                panel.sim_step = step
                panel.show("Off", "fit", False, False)
            elif kind in ("run", "view"):
                panel = self._make_panel(holder, self._run)
                panel.sim_step = self._run_step
                panel.show(spec[1], spec[2], True, spec[3])
            elif kind == "trace":
                # The same roads, with trace.txt's recorded vehicles on
                # them instead of the live run's -- what Trace replay On
                # shows on the run screen; a frame from well into it.
                reader = open("trace.txt", "r")
                reader.readline()           # the header line
                panel = self._make_panel(holder, self._run)
                panel.sim_step = self._run_step
                panel.trace_reader = reader
                panel._trace_start = self._trace_frame(reader,
                                                       self.WARM_UP_STEPS)
                panel.overrides = {"TRACE_MODE": True}
                panel.show("Off", spec[1], True, False)
            else:
                _kind, mkey, value, zoom, strips = spec
                run, overrides, step = self._model_run(mkey, value)
                panel = self._make_panel(holder, run)
                panel.overrides = dict(overrides)
                panel.sim_step = step
                panel.show(self.option.render3d_var.get(), zoom, True, strips)
        except Exception as exc:
            self._slot_text(holder, "No picture: " + str(exc)[:60])
            return
        cell["panel"] = panel
        if panel.processor is self._run and self.panel is None:
            self.panel = panel
        key, current, _hovered = self._focused()
        self._describe_cell(cell, key, current)

    def _describe_cell(self, cell, key, current) -> None:
        """The line under a picture: what the choice is, and what is on
        the road in it when the row is about the traffic."""
        if key == "network":
            cell["desc"].configure(text="")
            return
        text = preview_cell_note(key, cell["value"],
                                 self.option.fields["signal_green"].get()
                                 if "signal_green" in self.option.fields
                                 else "")
        panel = cell["panel"]
        if (panel is not None and key in PREVIEW_MODEL_KEYS
                and panel.draw_traffic):
            text += f"  ·  {len(panel.processor.get_vehicle_list())} vehicles"
        cell["desc"].configure(text=text)
        chosen = _Segmented._same(cell["value"], current)
        cell["header"].configure(
            foreground=_UI["accent_text"] if chosen else _UI["muted"])
        cell["holder"].configure(
            highlightbackground=_UI["accent"] if chosen else _UI["edge"])

    def _trace_is_here(self) -> bool:
        """Whether trace.txt was recorded on the network of the form's run.
        The file does not say; what it holds is vehicle corners in model
        pixels, so a frame from well into it is read and its vehicles are
        asked whether they lie on this network.  A recording of another
        place replays as an empty road here, and the run screen does
        exactly that -- but a picture of an empty road says nothing, so
        the slot says this instead."""
        if self._run is None:
            return True
        bounds = basemap_module.network_bounds(self._run.get_link_list(),
                                               margin=30.0)
        if not bounds:
            return True
        ppm = Parameters.pixel_per_meter
        x0, y0, x1, y1 = (v * ppm for v in bounds)
        inside = total = 0
        try:
            with open("trace.txt", "r") as reader:
                reader.readline()
                self._trace_frame(reader, self.WARM_UP_STEPS)
                vehicles = False
                while True:
                    line = reader.readline()
                    if not line or line.startswith("End"):
                        break
                    if line.startswith("Current"):
                        vehicles = line.startswith("Current Vehicles")
                        continue
                    if not vehicles:
                        continue
                    parts = line.split()
                    if len(parts) < 8:
                        continue
                    x, y = int(parts[0]), int(parts[4])
                    total += 1
                    inside += x0 <= x <= x1 and y0 <= y <= y1
        except (OSError, ValueError):
            return True
        return total == 0 or inside * 2 >= total

    @staticmethod
    def _trace_frame(reader, steps):
        """Where frame ``steps`` of the recording starts, or the last
        frame when the recording is shorter: a recording's first frames
        are an empty network, and the picture is meant to show the
        recorded vehicles.  Leaves the reader at that frame."""
        position = reader.tell()
        for _ in range(steps):
            # Read one frame; if it is incomplete, the previous one was
            # the last whole frame.
            while True:
                line = reader.readline()
                if not line:
                    reader.seek(position)
                    return position
                if line.startswith("End Step"):
                    break
            here = reader.tell()
            if not reader.readline():       # the recording ends here
                reader.seek(position)
                return position
            position = here
            reader.seek(position)
        return position

    def _overrides(self, key, value):
        """The Parameters a second run is built with for ``value``."""
        ppm = Parameters.pixel_per_meter
        if key == "network":
            return {"NETWORK_DIR": value}
        if key == "geometry":
            return {"GEOMETRY_MODE": value == "On"}
        if key == "pedestrians":
            return {"across_pedestrian_mode": value == "On"}
        if key == "friction":
            return {"OBJECT_MODE": value == "On"}
        if key == "strip":
            return {"strip_width": float(value),
                    "pixel_per_strip": ppm * float(value)}
        if key == "footpath":
            return {"footpath_strip_width": float(value),
                    "pixel_per_footpath_strip": ppm * float(value)}
        if key == "signal":
            return {"SIGNAL_MODE": OptionPanel.SIGNAL_CHOICES.get(value, "fixed")}
        if key == "traffic":
            return {"DEMAND_SCALE": float(value)}
        return {}

    def _slot_text(self, holder, text) -> None:
        for child in holder.winfo_children():
            child.destroy()
        tk.Label(holder, text=text, wraplength=max(60, holder.winfo_width() - 24),
                 justify="center", font=("Segoe UI", 9),
                 background=_UI["seg_off"], foreground=_UI["muted"]
                 ).place(relx=0.5, rely=0.5, anchor="center")

    def _slot_sheet(self, holder, rows, current) -> None:
        """A table in a picture's place: the playback speeds and the
        milliseconds behind each, the form's own in the accent colour."""
        for child in holder.winfo_children():
            child.destroy()
        bg = _UI["seg_off"]
        sheet = tk.Frame(holder, background=bg)
        sheet.place(relx=0.5, rely=0.5, anchor="center")
        rows = list(rows)
        if not any(_Segmented._same(ms.split()[0], current)
                   for _label, ms in rows):
            rows.append(("As set", f"{current} ms"))   # off the ladder
        for r, (label, ms) in enumerate(rows):
            chosen = _Segmented._same(ms.split()[0], current)
            fg = _UI["accent_text"] if chosen else _UI["muted"]
            tk.Label(sheet, text=label, font=("Segoe UI Semibold", 10),
                     background=bg, foreground=fg, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 14), pady=1)
            tk.Label(sheet, text=ms, font=("Consolas", 10, "bold"),
                     background=bg, foreground=fg, anchor="e").grid(
                row=r, column=1, sticky="e", pady=1)
            tk.Label(sheet, text="selected" if chosen else "",
                     font=("Segoe UI", 9), background=bg,
                     foreground=_UI["accent_text"], width=8, anchor="w").grid(
                row=r, column=2, sticky="w", padx=(10, 0))
        tk.Label(sheet, text="between frames; the results are the same at "
                             "every speed",
                 font=("Segoe UI", 9), background=bg, foreground=_UI["muted"],
                 wraplength=max(120, holder.winfo_width() - 40),
                 justify="center").grid(row=len(rows), column=0, columnspan=3,
                                        pady=(8, 0))

    def _say(self, key, current, hovered) -> None:
        """The caption under the pictures: what the setting shows, for
        the value under the pointer else the form's, and a line counting
        what the form's own run (or the map) holds."""
        if self._note is None:
            return
        values = self.option._values()
        place = _place_name(values["network"])
        note_key = key if key in PREVIEW_NOTES else "network"
        if key == "network":
            shown = hovered or values["network"]
            about = _place_about(shown)
            name = _place_name(shown)
            # "Palashi: Palashi crossing ..." when the sentence opens with
            # the name itself; the sentence alone then.
            if about and about.lower().startswith(name.lower()):
                text = about
            elif about:
                text = f"{name}: {about}"
            else:
                text = preview_note("network", shown, name)
            self._note.configure(text=text)
        else:
            self._note.configure(text=preview_note(note_key, hovered or current,
                                                   place))
        self._captions(key)

    def _captions(self, key) -> None:
        panel = self.cells[0]["panel"] if self.cells else None
        if panel is None:
            return
        run = panel.processor
        if key == "network":
            links = run.get_link_list()
            nodes = run.get_node_list()
            junctions = sum(1 for n in nodes if n.number_of_links() >= 3)
            metres = sum(link.get_segment(i).get_length()
                         for link in links
                         for i in range(link.get_number_of_segments()))
            parts = [f"{junctions} junction{'s' if junctions != 1 else ''}",
                     f"{len(links)} links", f"{metres / 1000.0:.1f} km of road"]
            if any(n.is_roundabout() for n in nodes):
                parts.append("roundabout")
        else:
            run = self._run
            if run is None:
                return
            parts = [f"{self._run_step} s into the run",
                     f"{len(run.get_vehicle_list())} vehicles"]
            if Parameters.across_pedestrian_mode:
                parts.append(f"{len(run.get_pedestrians())} crossing")
            if Parameters.OBJECT_MODE:
                parts.append(f"{len(run.get_object_list())} parked")
        self._counts.configure(text=" · ".join(parts))

    # ---- leaving nothing behind --------------------------------------------

    def stop(self) -> None:
        """Drop the runs and put ``Parameters`` back as they were before
        them.  Safe to call twice."""
        if self._job is not None and self.frame is not None:
            try:
                self.frame.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None
        self._drop_cells()
        self._run = None
        self._runs.clear()
        self._layout = None
        if self._saved is not None:
            Parameters.restore(self._saved)
            self._saved = None

    def _scratch_dir(self) -> str:
        if self._stats_dir is None:
            self._stats_dir = tempfile.mkdtemp(prefix="dhakasim_preview_")
            atexit.register(shutil.rmtree, self._stats_dir,
                            ignore_errors=True)
        return self._stats_dir


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
    #: takes; the labels say what each does (the paper's "multi-objective
    #: v1" and "v2" are named in the help and the report, not on the
    #: strip -- a reader could not tell them apart by version number).
    SIGNAL_CHOICES = {
        "Fixed time": "fixed",
        "Biased random": "biased-random",
        "Planned, all alike": "moo-v1",
        "Planned, by class": "moo-v2",
    }
    #: What the strip's cells say when a name is too long for a cell
    #: (two abreast set the right column's width, which comes off the
    #: picture pane).  Empty since the controllers were named by what
    #: they do; kept as the hook.
    SIGNAL_LABELS = {}

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
        self._fit_height = 0
        self._rechecks = 0
        self._hint = None
        self._hint_rows = []
        self._make_vars()
        self.form = self._build_shell()
        self._build_form()
        self.frame.bind("<Configure>", lambda _e: self._fit_to_window())
        # Undo for the whole form; the run screen unbinds it in dispose.
        self.frame.winfo_toplevel().bind("<Control-z>",
                                         lambda _e: self.undo())
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
        # The pictures' column: a fixed width the density search chooses,
        # outside the scroller so it stays put while the settings scroll,
        # and always there -- the settings menu of a game puts its picture
        # beside the list, not behind a button.
        self._pane = tk.Frame(body, background=_UI["ground"],
                              width=_SettingsPreview.WIDTH)
        self._pane.pack(side="right", fill="y", padx=(18, 12), pady=(10, 0))
        self._pane.pack_propagate(False)
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
        # How many junction tiles stand abreast: the density's own number,
        # or two when the search needs the left column narrower.
        self._tiles = None
        # (level, tiles, rows shown, networks) -> (column widths, height
        # side by side, height stacked): what a built form measures,
        # kept so the search below does not rebuild to ask twice.
        self._measure_cache = {}
        # (rung, tiles, rows shown, networks) -> {band: column}, the
        # even split of the bands found on the first build at that rung.
        self._plans = {}
        self._plan = None
        self._placed = {}
        self._band_log = []
        # The pictures beside the settings.  Built once with the shell, not
        # with the form: a density change rebuilds every row but must not
        # throw away a run that took a third of a second to make.
        self._preview = _SettingsPreview(self, self._pane)

        def fit(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            # The panel's size is what the search keys on, and it does
            # not change when the header or footer does -- but the canvas
            # between them does, and a rebuild at another rung moves
            # both.  A height the last search did not see is searched
            # again; the search judges every rung against its own header
            # (``_choose_layout``), so this settles rather than cycles.
            moved = abs(canvas.winfo_height() - self._fit_height) > 2
            self._fit_to_window(force=moved and self._fit_height > 0)

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
        height = self._canvas.winfo_height()
        try:
            level, tiles, width, stacked = self._choose_layout(size[0], height)
            if (level, tiles) != (self._level, self._tiles):
                self._level, self._tiles = level, tiles
                self._build_form()
            self._pane.configure(width=width)
            self.frame.update_idletasks()
            # The columns are a different width at every density, so
            # whether they stand side by side has to be decided again --
            # ``_arrange`` short-circuits on its last answer.
            self._stacked = None
            self._arrange(0 if stacked else self._canvas.winfo_width())
            self.frame.update_idletasks()
            self._preview.resize(width)
            self._viewport = size
        finally:
            self._busy = False
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._show_bar()
        # The canvas's height can lag the panel's: a maximise delivers the
        # panel's <Configure> before the canvas inside it has grown, so the
        # search above may have judged against the old height (on this
        # laptop it chose a 340 px pane with the tiles three abreast where
        # 460 px and two abreast fit).  Look again once Tk has settled, and
        # search again if the height moved; once per size, so it ends.
        self._fit_height = height
        if self._rechecks < 2:
            self._rechecks += 1
            self.frame.after_idle(self._recheck_fit)
        else:
            self._rechecks = 0

    def _recheck_fit(self) -> None:
        if not self.frame.winfo_exists():
            return
        if abs(self._canvas.winfo_height() - self._fit_height) > 2:
            self._fit_to_window(force=True)
        else:
            self._rechecks = 0

    #: The widths the pictures' column may take after a third of the
    #: window, widest first, when no rung fits beside the third.  The
    #: pictures stack in it at its full width, so a wider column is a
    #: bigger picture.
    PANE_WIDTHS = (460, 400, 340, 260)
    #: The share of the window the pictures get when a rung fits beside
    #: it (owner, 12 Oct 2026: "one-third of the column space").
    PANE_SHARE = 3
    #: What the pane costs beyond its own width: its padding and the
    #: scrollbar's reserve.
    PANE_MARGIN = 44

    def _measure(self, level, tiles):
        """What the form measures at a density and tile count: the two
        columns' widths, and the tray's height side by side and stacked.
        Built once per combination and cached -- a build is the expensive
        half of this search."""
        key = (level, tiles, self._trimmed_rows(),
               len(Processor.available_networks()))
        found = self._measure_cache.get(key)
        if found is not None:
            return found
        self._level, self._tiles = level, tiles
        self._build_form()
        self.frame.update_idletasks()
        columns = [c.winfo_reqwidth() for c in self._columns]
        self._stacked = None
        self._arrange(10 ** 6)
        self.frame.update_idletasks()
        side_by_side = self._tray.winfo_reqheight()
        self._stacked = None
        self._arrange(0)
        self.frame.update_idletasks()
        stacked = self._tray.winfo_reqheight()
        # The footer is rebuilt with the form (its buttons take the
        # rung's type), so its height is this rung's too.
        footer = self._footer.winfo_reqheight()
        found = (columns, side_by_side, stacked, footer)
        self._measure_cache[key] = found
        return found

    def _choose_layout(self, width, height):
        """(level, tiles, pane width, stacked) for a window this size.

        The order of preference, and why.  The pictures come first: the
        widest column in PANE_WIDTHS, because the comparison is the point
        of the start screen now and a picture that has to be fetched from
        behind a button is not one.  Then the roomiest density whose two
        columns stand beside that pane and whose height fits -- with the
        junction tiles two abreast as a second try, since the tiles are
        what make the left column wide.  Failing that, a narrower pane.
        Failing that, one column of settings beside the pictures, scrolling
        if it must: the list-and-picture layout of a game's settings menu,
        which is what a 1280 px screen gets.  Whatever width the chosen
        settings leave over goes to the pictures (``spare``), up to
        PANE_MAX; the pictures stack in the column at its full width, so
        the column is never wider than it can use.

        Measured at 96 DPI with the shipped networks: 1080p lands on
        compact with the tiles two abreast beside a 460 px pane; a
        1536 px desktop on minimal beside 400 px; 1280 px on one scrolling
        column beside 460 px.
        """
        def usable(pane):
            return width - pane - self.PANE_MARGIN

        # The canvas's height depends on the density drawn now -- each
        # rung has its own header height and footer padding -- so a
        # candidate is judged against the height the window would give
        # *it*, not against the height the current rung leaves.  Judged
        # against the current one, the launch rung's tall header hid 38
        # px from every roomier rung, and a rung that only just fits
        # could be chosen and then unchosen as its own header shrank.
        base = height + self._d["header"] + self._footer.winfo_reqheight()

        def room(level, footer):
            return base - _DENSITIES[level]["header"] - footer

        third = width // self.PANE_SHARE
        panes = tuple(dict.fromkeys(
            (third,) + tuple(p for p in self.PANE_WIDTHS if p < third)))

        def spare(pane, needed):
            # Whatever width the settings leave goes to the pictures, up
            # to the third of the window they are meant to have.
            return max(pane, min(third, width - needed - self.PANE_MARGIN))

        # Density outside, pane width inside: a roomier form (captions on,
        # larger type) beats a wider picture column, since the pictures
        # are stills that read at 260 px and the settings are what the
        # reader is here to use.  It was the other way round until 11 Oct
        # 2026, which put this laptop on the captionless rung beside a
        # 460 px pane with a third of the window left blank.
        for level in range(len(_DENSITIES)):
            for pane in panes:
                for tiles in dict.fromkeys((_DENSITIES[level]["tiles"], 2)):
                    columns, side, _stacked, footer = self._measure(level, tiles)
                    if (sum(columns) + 60 <= usable(pane)
                            and side <= room(level, footer)):
                        return (level, tiles,
                                spare(pane, sum(columns) + 60), False)
        for level in range(len(_DENSITIES)):
            for pane in panes:
                tiles = _DENSITIES[level]["tiles"]
                columns, _side, stacked, footer = self._measure(level, tiles)
                if (max(columns) + 60 <= usable(pane)
                        and stacked <= room(level, footer)):
                    return (level, tiles,
                            spare(pane, max(columns) + 60), True)
        # Nothing fits whole: the densest form in one column, scrolling,
        # beside the widest pane the window leaves room for.
        level, tiles = len(_DENSITIES) - 1, 2
        columns, _side, _stacked, _footer = self._measure(level, tiles)
        pane = max(self.PANE_WIDTHS[-1],
                   min(self.PANE_WIDTHS[0],
                       width - max(columns) - 60 - self.PANE_MARGIN))
        return level, tiles, pane, True

    def _build_form(self) -> None:
        """Draw every setting again at the current density.

        The widgets go, the variables stay.  Everything the reader has typed
        lives in a ``StringVar`` made once in ``_make_vars``, so a rebuild is
        invisible except for the sizes.
        """
        # The bands after the junction tiles are split across the two
        # columns to make them as even as possible.  Their heights are
        # not known until they are drawn, so the first build at a rung
        # draws them greedily (each to the shorter column), measures what
        # each one cost, works out the best split (``_best_split``) and,
        # when the greedy one was not it, draws once more.  The split is
        # kept per rung, so later rebuilds draw once.  Greedy alone put
        # Display on a column already level with the other and left
        # compact 910 px tall at 1080p where 847 was possible.
        key = self._plan_key()
        self._plan = self._plans.get(key)
        self._draw()
        if self._plan is None:
            heights = self._band_heights()
            plan = self._best_split(heights)
            self._plans[key] = plan
            if plan != self._placed:
                self._plan = plan
                self._draw()
        self._bind_wheel(self._tray)
        self._bind_wheel(self._canvas)
        for widgets, text in self._hint_rows:
            for widget in widgets:
                self._bind_hint(widget, text)
        # The pointer on a row shows that setting's pair of pictures.
        # Bound to every descendant, like the hint: Tk hands <Enter> to
        # the widget under the pointer, not to its row.
        for key, widgets in self._row_frames:
            for widget in widgets:
                self._bind_focus(widget, key)
        self._paint_header()
        self._refresh_changed_marks()
        self._idle_status()

    def _bind_focus(self, widget, key) -> None:
        widget.bind("<Enter>", lambda _e: self._preview.focus(key), add="+")
        widget.bind("<Leave>", lambda _e: self._preview.focus(None), add="+")
        for child in widget.winfo_children():
            self._bind_focus(child, key)

    def _bind_hint(self, widget, text) -> None:
        """Show one row's explanation in the footer while the pointer is on
        it.  Added to whatever is already bound, never in place of it: the
        cells paint their own hover state on the same event."""
        widget.bind("<Enter>",
                    lambda _e: self._show_status(text), add="+")
        widget.bind("<Leave>",
                    lambda _e: self._idle_status(), add="+")
        for child in widget.winfo_children():
            self._bind_hint(child, text)

    def _paint_header(self, _event=None) -> None:
        """Draw the title, the line under it, and the light behind them.

        The glow is concentric filled ovals worked from the outside in, and
        the outermost one is the ground colour itself, so the bloom has no
        edge to give it away.  Sixty steps put the banding below what the eye
        separates at this contrast; a dozen would show rings.  It takes the
        accent's hue, so the light behind the name is the light on Start.
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

    def _draw(self) -> None:
        """Empty both columns and the footer and draw every row again."""
        for column in self._columns:
            for child in column.winfo_children():
                child.destroy()
            # A frame whose children are all gone keeps requesting the
            # size the last of them gave it, so the column that was the
            # taller stays "taller" into the next build and the greedy
            # placement sends every band the other way.  Ask for nothing
            # until the new rows say otherwise.
            column.configure(width=1, height=1)
            column.columnconfigure(0, minsize=self._d["field"])
            _glass(column)
            _round_corners(column, 12, top_fill=_UI["band"])
        # The left column holds the tiles alone, full width: no name column
        # to reserve.  A band's rows landing here bring their own name
        # width.
        self._columns[0].columnconfigure(0, minsize=0)
        for child in self._footer.winfo_children():
            child.destroy()
        _glass(self._footer)
        self._header.configure(height=self._d["header"])
        self._rows = [0, 0]
        self._col = 0
        self._stacked = None
        self._hint = None
        self._hint_rows = []
        self._row_labels = []
        self._row_frames = []
        self._placed = {}               # band -> column it was drawn in
        self._band_log = []             # (band, column, height before it)
        self._init_components()

    def _plan_key(self):
        """What a band split depends on: the rung and tile count (the
        tiles' height), the rows shown and the number of tiles."""
        return (self._level, self._tiles, self._trimmed_rows(),
                len(Processor.available_networks()))

    def _place(self, band):
        """The column the next band goes to: the plan's, else the one
        with less in it so far (the right on a tie, so the tiles keep the
        left).  Either way the column's height before the band is noted,
        which is how the band's own height is measured afterwards."""
        self.frame.update_idletasks()
        heights = [c.winfo_reqheight() for c in self._columns]
        if self._plan is not None and band in self._plan:
            column = self._plan[band]
        else:
            column = 1 if heights[1] <= heights[0] else 0
        self._placed[band] = column
        self._band_log.append((band, column, heights[column]))
        return column

    def _band_heights(self):
        """(tiles height, {band: height}) from the build just drawn."""
        self.frame.update_idletasks()
        final = [c.winfo_reqheight() for c in self._columns]
        heights = {}
        for index, (band, column, before) in enumerate(self._band_log):
            after = final[column]
            for later, later_column, later_before in self._band_log[index + 1:]:
                if later_column == column:
                    after = later_before
                    break
            heights[band] = max(0, after - before)
        tiles = final[0] - sum(h for b, h in heights.items()
                               if self._placed[b] == 0)
        return max(0, tiles), heights

    @staticmethod
    def _best_split(measured):
        """The assignment of bands to columns that makes the taller
        column shortest, the tiles' height counting for the left; the
        left column (reading order) breaks ties."""
        tiles, heights = measured
        bands = list(heights)
        best = None
        for mask in range(1 << len(bands)):
            plan = {band: (0 if mask & (1 << i) else 1)
                    for i, band in enumerate(bands)}
            left = tiles + sum(heights[b] for b in bands if plan[b] == 0)
            right = sum(heights[b] for b in bands if plan[b] == 1)
            score = (max(left, right), -sum(1 for b in bands if plan[b] == 0))
            if best is None or score < best[0]:
                best = (score, plan)
        return best[1]

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
        head = tk.Frame(cell, background=_UI["panel"])
        head.pack(anchor="w")
        keys = ROW_KEYS.get(label)
        # The glyph before the name, sized with the type.  Inside the
        # label column's fixed width, so it costs the layout nothing.
        if keys and keys[0] in _Icon.KINDS:
            _Icon(head, keys[0], round(level["label"] * 1.4)).pack(
                side="left", padx=(0, 6))
        name = tk.Label(head, text=label, font=("Segoe UI", level["label"]),
                        background=_UI["panel"], foreground=_UI["text"])
        name.pack(side="left")
        if keys:
            self._row_labels.append((name, label, keys))
            # A click on the name takes back that setting's last change
            # (the footer says "Ctrl+Z undoes" after every change; the
            # name is the per-row way).
            name.bind("<Button-1>", lambda _e, k=keys: self.undo(k))
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
        if keys:
            self._row_frames.append((keys[0], (cell, holder)))
        return holder

    def _wide_row(self, key, caption):
        """A control that takes the column's whole width, with no name
        beside it: the junction tiles, whose band says what they are.
        The name column stood empty beside seven rows of tiles -- 180 px
        of the left column doing nothing -- which is the width the
        picture pane needed to be a third of the window."""
        level = self._d
        column = self._columns[self._col]
        self._rows[self._col] += 1
        holder = tk.Frame(column, background=_UI["panel"])
        holder.grid(row=self._rows[self._col], column=0, columnspan=2,
                    sticky="nw", padx=level["pad"],
                    pady=(level["row_pad"], 0))
        if not level["caption"]:
            self._hint_rows.append(((holder,), caption))
        else:
            tk.Label(holder, text=caption, font=("Segoe UI", level["caption"]),
                     background=_UI["panel"], foreground=_UI["muted"],
                     wraplength=max(level["wrap"] * 2, 420), justify="left"
                     ).pack(anchor="w", pady=(0, 4))
        self._row_frames.append((key, (holder,)))
        return holder

    def _unit(self, holder, text):
        tk.Label(holder, text=text, font=("Segoe UI", self._d["unit"]),
                 background=_UI["panel"], foreground=_UI["muted"]).pack(
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

    def _randomise_seed(self) -> None:
        """A fresh seed in the box on every click.

        It used to write -1 -- a seed drawn at Start and never shown --
        so a second click changed nothing and the box read -1 whatever
        had been pressed (owner, 12 Oct 2026).  A number in the box is a
        run that can be repeated by typing it again, which -1 never was.
        Drawn from the system's randomness, never from
        ``Parameters.random``: that stream is the run's own.
        """
        draw = random.SystemRandom()
        was = self.fields["seed"].get()
        seed = str(draw.randrange(1, 100000))
        while seed == was:
            seed = str(draw.randrange(1, 100000))
        self.fields["seed"].set(seed)

    def _button(self, parent, text, command, primary=False, small=False):
        """A push button; ``small`` is the size of a unit label, for a
        button that sits beside an entry rather than in the footer."""
        level = self._d
        pad = level["button_pad"]
        if small:
            pad = (max(6, pad[0] // 3), max(1, pad[1] // 4))
        button = tk.Label(
            parent, text=text,
            font=("Segoe UI Semibold", max(8, level["unit"] - 1) if small
                  else level["button"]),
            background=_UI["accent"] if primary else _UI["seg_off"],
            foreground=_UI["on_accent"] if primary else _UI["text"],
            padx=pad[0], pady=pad[1],
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
        self.fields["traffic"] = tk.StringVar(
            value=f"{Parameters.DEMAND_SCALE:g}")
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
        # The feedback state: whether a write is the place's defaults being
        # applied (not a reader's change), the values those defaults left
        # (what "changed" is measured against), the row labels that carry
        # the mark, and the timer that returns the footer to its summary.
        self._applying_defaults = False
        self._snapshot = {}
        self._row_labels = []
        self._status_job = None
        # Undo: the values each setting had before each change, newest
        # last, and the order the changes came in (Ctrl+Z takes the
        # latest).  ``_last`` is what every variable held a moment ago --
        # a trace fires after the write, so the old value has to be
        # remembered.  A run of writes to one setting within
        # UNDO_COALESCE_S is one change (typing "480" is three writes).
        self._history = {}
        self._undo_order = []
        self._last = {}
        self._undoing = False
        self._last_change = (None, 0.0)
        self.network_var.trace_add("write",
                                   lambda *_a: self._on_network_change())
        self._on_network_change()
        self._last = self._values()
        # One watcher per setting, made once with the variables -- a form
        # rebuild must not add a second (test_start_screen counts them).
        for key, var in self._watched().items():
            var.trace_add("write", lambda *_a, k=key: self._setting_changed(k))

    def _watched(self):
        """Every setting the footer reports on, by key."""
        return {"network": self.network_var, "time": self.time_var,
                "end_time": self.fields["end_time"],
                "traffic": self.fields["traffic"],
                "max_speed": self.fields["max_speed"],
                "seed": self.fields["seed"],
                "accident": self.fields["accident"],
                "trace": self.trace_var, "signal": self.signal_var,
                "signal_green": self.fields["signal_green"],
                "geometry": self.geometry_var,
                "pedestrians": self.pedestrian_var,
                "friction": self.friction_var,
                "strip": self.fields["strip"],
                "footpath": self.fields["footpath"],
                "render3d": self.render3d_var, "ppm": self.fields["ppm"],
                "speed": self.fields["speed"]}

    def _values(self):
        return {key: var.get() for key, var in self._watched().items()}

    # ---- feedback: the footer says what every change does ------------------

    #: Writes to one setting closer together than this are one change
    #: for undo: the keystrokes of a typed value, or a dragged slider.
    UNDO_COALESCE_S = 1.5

    def _remember(self, key: str, value: str) -> None:
        """Note the value a setting held before this write, so the write
        can be taken back."""
        was = self._last.get(key)
        self._last[key] = value
        if self._undoing or was is None or was == value:
            return
        now = time.monotonic()
        last_key, last_at = self._last_change
        if last_key == key and now - last_at < self.UNDO_COALESCE_S:
            # The same edit still going on: keep the value from before it.
            self._last_change = (key, now)
            if self._history.get(key) and self._history[key][-1] == value:
                self._history[key].pop()        # typed back to where it was
                for index in range(len(self._undo_order) - 1, -1, -1):
                    if self._undo_order[index] == key:
                        del self._undo_order[index]
                        break
            return
        self._history.setdefault(key, []).append(was)
        self._undo_order.append(key)
        self._last_change = (key, now)

    def can_undo(self, key=None) -> bool:
        if key is None:
            return any(self._history.get(k) for k in self._undo_order)
        return bool(self._history.get(key))

    def undo(self, key=None) -> bool:
        """Take back the last change -- of ``key``, or of any setting.
        Returns whether there was one.  The variable is written back, so
        the strip, the picture and the footer follow as for any change."""
        if key is None:
            while self._undo_order and not self._history.get(
                    self._undo_order[-1]):
                self._undo_order.pop()
            if not self._undo_order:
                return False
            key = self._undo_order[-1]
        elif isinstance(key, (tuple, list)):
            # A row with two settings: the one changed most recently.
            keys = [k for k in reversed(self._undo_order) if k in key]
            if not keys:
                return False
            key = keys[0]
        history = self._history.get(key)
        if not history:
            return False
        value = history.pop()
        for index in range(len(self._undo_order) - 1, -1, -1):
            if self._undo_order[index] == key:
                del self._undo_order[index]
                break
        self._undoing = True
        try:
            self._watched()[key].set(value)
        finally:
            self._undoing = False
        self._last[key] = value
        self._last_change = (None, 0.0)
        label, unit, _meaning = SETTING_INFO[key]
        shown = (value if not unit else f"x{value}" if unit == "x"
                 else f"{value} {unit}")
        if key == "network":
            shown = _place_name(value)
        self._show_status(f"Undone: {label} back to {shown}.")
        if self._status_job is not None:
            self.frame.after_cancel(self._status_job)
        self._status_job = self.frame.after(7000, self._idle_status)
        self._refresh_changed_marks()
        return True

    def _setting_changed(self, key: str) -> None:
        """A setting's variable was written.  Say what that means in the
        footer, mark the row if it now differs from the place's default,
        and warn at once if the value cannot be used."""
        if self._applying_defaults or key == "network":
            return                      # the network summary covers it
        value = self._watched()[key].get()
        self._remember(key, value)
        self._preview.request(key)
        if self._undoing:
            return                      # undo says its own sentence
        problem = setting_problem(key, value)
        if problem:
            self._show_status(problem, error=True)
        else:
            self._show_status(setting_message(key, value,
                                              self._snapshot.get(key))
                              + "  Ctrl+Z undoes.")
            if self._status_job is not None:
                self.frame.after_cancel(self._status_job)
            self._status_job = self.frame.after(7000, self._idle_status)
        self._refresh_changed_marks()

    def _show_status(self, text: str, error: bool = False) -> None:
        if self._hint is None:
            return
        try:
            self._hint.configure(
                text=text,
                foreground="#E86A6A" if error else _UI["accent_text"])
        except tk.TclError:
            pass

    def _idle_status(self) -> None:
        """With nothing just changed, the footer says what Start will run."""
        self._status_job = None
        if self._hint is None:
            return
        problem = self._validate()
        if problem:
            self._show_status(problem, error=True)
            return
        try:
            self._hint.configure(text=self._summary(),
                                 foreground=_UI["muted"])
        except tk.TclError:
            pass

    def _summary(self) -> str:
        values = self._values()
        view = values["render3d"]
        changed = sum(1 for k, v in values.items()
                      if k in self._snapshot and self._snapshot[k] != v)
        parts = [f"Start runs {_place_name(values['network'])}",
                 values["time"].split(" (")[0].lower(),
                 f"{values['end_time']} s", f"traffic x{values['traffic']}",
                 f"seed {values['seed']}",
                 "3D view" if view != "Off" else "plan view"]
        text = " \u00b7 ".join(parts)
        if changed:
            text += f"  \u00b7  {changed} setting{'s' if changed > 1 else ''} " \
                    "changed from the place's defaults"
        return text

    def _validate(self):
        """The first setting that cannot be used, as a sentence, or None."""
        for key, value in self._values().items():
            problem = setting_problem(key, value)
            if problem:
                return problem
        return None

    def _refresh_changed_marks(self) -> None:
        """A dot before the name of every row whose value is not the
        place's default, so the reader can see at a glance what they have
        moved (and the summary line counts them)."""
        values = self._values()
        for widget, label, keys in self._row_labels:
            changed = any(k in self._snapshot and self._snapshot[k] != values[k]
                          for k in keys)
            undoable = any(self._history.get(k) for k in keys)
            try:
                widget.configure(
                    text=("\u25cf " + label) if changed else label,
                    foreground=_UI["accent_text"] if changed else _UI["text"],
                    cursor="hand2" if undoable else "")
            except tk.TclError:
                pass

    def _network_summary(self, applied) -> str:
        values = self._values()
        parts = [f"{_place_name(values['network'])}: "
                 f"{values['max_speed']} km/h limit",
                 f"{values['signal_green']} s green per approach"]
        if "AcrossPedestrianMode" in applied:
            parts.append("crossing pedestrians "
                         + ("On" if Parameters.across_pedestrian_mode else "Off"))
        if "ObjectMode" in applied:
            parts.append("parked vehicles "
                         + ("On" if Parameters.OBJECT_MODE else "Off"))
        return " \u00b7 ".join(parts) + " -- the place's own defaults"

    def _init_components(self) -> None:
        level = self._d
        networks = Processor.available_networks()

        # ================= left column ====================================
        self._use(0)
        self._band("Junction", "  the network to simulate")
        holder = self._wide_row("network",
                                "Each is a surveyed junction with its own "
                                "demand, vehicle mix and road widths.")
        # Imported maps sit in groups of their own, under the shipped ones,
        # split by what the importer said they were.  Only imported groups
        # carry the remove affordance -- map_import.remove refuses anything
        # else, but the control should not even be offered for survey data.
        imported = [n for n in networks if map_import.is_imported(n)]
        single = [n for n in networks
                  if n in self.SINGLE_JUNCTIONS and n not in imported]
        multi = [n for n in networks
                 if n not in self.SINGLE_JUNCTIONS and n not in imported]
        groups = (
            ("Single intersection", single, False),
            ("Multi intersection", multi, False),
            ("Imported (single intersection)",
             [n for n in imported
              if map_import.imported_kind(n) == "single"], True),
            ("Imported (multi intersection)",
             [n for n in imported
              if map_import.imported_kind(n) != "single"], True),
        )
        for caption, group, removable in groups:
            if not group:
                continue
            head = tk.Frame(holder, background=_UI["panel"])
            head.pack(anchor="w", fill="x", pady=(0, 2))
            tk.Label(head, text=caption, font=("Segoe UI", level["group"]),
                     background=_UI["panel"],
                     foreground=_UI["muted"]).pack(side="left")
            if removable:
                # Inline with the caption rather than a row of its own,
                # which would cost height the density ladder does not have.
                remover = tk.Label(
                    head, text="remove selected…",
                    font=("Segoe UI", level["group"], "underline"),
                    background=_UI["panel"], foreground=_UI["muted"],
                    cursor="hand2")
                remover.pack(side="left", padx=(12, 0))
                remover.bind("<Button-1>",
                             lambda _e: self._remove_import())
                remover.bind("<Enter>", lambda _e, w=remover:
                             w.configure(foreground=_UI["muted"]))
                remover.bind("<Leave>", lambda _e, w=remover:
                             w.configure(foreground=_UI["faint"]))
            # Four tiles abreast is 440 pixels, which two columns of them
            # cannot afford on a 1280-wide screen; the densest layout wraps
            # them instead.
            self._seg(holder, [(n, _place_name(n)) for n in group],
                      self.network_var, large=True,
                      command=self._on_network_change,
                      columns=min(self._tiles or level["tiles"] or len(group),
                                  len(group)),
                      keep_unknown=False,
                      hover=lambda v: self._preview.peek("network", v)).pack(
                anchor="w", pady=(0, level["row_pad"] + 2))

        show_time, show_pedestrians, show_friction = self._trimmed_rows()
        self._shown_rows = (show_time, show_pedestrians, show_friction)

        # ================= the other bands =================================
        # Each band goes to whichever column is the shorter at that moment
        # (``_shorter``), so the two end near the same line whatever the
        # density and however many junction tiles there are.  Run always
        # stood under the tiles, which left the right column ending a
        # third of the way up the window on every screen (owner, 11 Oct
        # 2026: "a lot of unused space") -- and pinning all four bands to
        # the right instead made that column fourteen captioned rows tall,
        # too tall for 1080p at any captioned rung.  Measured, not fixed.
        self._use(self._place("Run"))
        self._band("Run", "  how much traffic, and how repeatable")
        if show_time:
            # Only a surveyed network carries an hourly demand profile; for
            # the OSM-derived ones the hour would change nothing, so the row
            # would be a lie.  Under Run, with the other demand settings,
            # since 12 Oct 2026: it is about the traffic, not the place.
            holder = self._row("Time of day",
                               "The busiest hour of the surveyed day, or one "
                               "you name.")
            _Dropdown(holder, self.TIME_CHOICES, self.time_var,
                      font=("Segoe UI", level["value"]),
                      pad=(12, max(3, level["row_pad"]))).pack(anchor="w")
        else:
            # The form adjusting itself, not the reader: no footer sentence,
            # no preview toast naming the hour over the junction just chosen.
            was = self._applying_defaults
            self._applying_defaults = True
            self.time_var.set(self.TIME_CHOICES[0])
            self._applying_defaults = was
        # The traffic level shares this row rather than taking one of its
        # own: a captioned row is ~34 px the roomy density does not have at
        # 1080p, and the strip fits beside the entry inside the column's
        # 440 px.  The caption is kept to one line for the same reason.
        # Two rows since 12 Oct 2026.  They shared one while the Run band
        # stood under the tiles and height was the scarce thing; now the
        # bands balance across the columns and *width* is, because the
        # picture pane takes a third of the window -- and an entry beside
        # a five-cell strip was the widest row on the screen.
        holder = self._row("End time", "Seconds of traffic to simulate.")
        self._entry(holder, self.fields["end_time"], width=7)
        self._unit(holder, "s")
        holder = self._row("Traffic level",
                           "x1 is the surveyed traffic; x3 is three times it.")
        self._seg(holder, ["0.5", "1", "1.5", "2", "3"],
                  self.fields["traffic"],
                  hover=lambda v: self._preview.peek("traffic", v)).pack(
            side="left")
        self._unit(holder, "x")

        holder = self._row("Speed limit",
                           "The network speed limit. Choosing a junction sets "
                           "its surveyed value.")
        self._entry(holder, self.fields["max_speed"])
        self._unit(holder, "km/h")

        holder = self._row("Random seed",
                           "The same number twice gives the same run twice.")
        self._entry(holder, self.fields["seed"])
        self._button(holder, "Randomise", self._randomise_seed,
                     small=True).pack(side="left", padx=(8, 0))

        holder = self._row("Accidents",
                           "Close encounters before one becomes an accident. "
                           "Lower means more.")
        self._entry(holder, self.fields["accident"])

        holder = self._row("Trace replay",
                           "On replays trace.txt instead of simulating a new "
                           "run.")
        self._seg(holder, ["Off", "On"], self.trace_var,
                  hover=lambda v: self._preview.peek("trace", v)).pack(
            anchor="w")

        self._use(self._place("Control"))
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
        self._seg(holder, [(name, self.SIGNAL_LABELS.get(name, name))
                           for name in self.SIGNAL_CHOICES], self.signal_var,
                  columns=2,
                  hover=lambda v: self._preview.peek("signal", v)).pack(
            anchor="w")
        timing = tk.Frame(holder, background=_UI["panel"])
        timing.pack(anchor="w", pady=(3, 0))
        self._entry(timing, self.fields["signal_green"])
        self._unit(timing, "s green per approach")

        holder = self._row("Real geometry",
                           "On drives the surveyed medians and roundabout "
                           "ring. Off is the plain roads of the original "
                           "Java program.")
        self._seg(holder, ["Off", "On"], self.geometry_var,
                  hover=lambda v: self._preview.peek("geometry", v)).pack(
            anchor="w")

        # A row each.  They shared one ("Side friction", two strips side by
        # side) to save the roomy density ~60 px at 1080p, and the owner
        # read the two strips as one overlapping control; the height is
        # paid for by the playback rows below, which went from two
        # captioned rows to two short ones.
        if show_pedestrians:
            holder = self._row("Pedestrians",
                               "Pedestrians cross the carriageway and "
                               "vehicles brake for them.")
            self._seg(holder, ["Off", "On"], self.pedestrian_var,
                      hover=lambda v: self._preview.peek("pedestrians", v)
                      ).pack(anchor="w")
        if show_friction:
            holder = self._row("Parked vehicles",
                               "Parked cars, rickshaws and CNGs and "
                               "standing hawkers on the kerbside strips.")
            self._seg(holder, ["Off", "On"], self.friction_var,
                      hover=lambda v: self._preview.peek("friction", v)
                      ).pack(anchor="w")

        self._use(self._place("Road model"))
        self._band("Road model", "  the strips a vehicle slides between")
        holder = self._row("Strip width",
                           "There are no lanes here. A carriageway is a row of "
                           "strips this wide.")
        self._seg(holder, ["0.25", "0.5", "1.0"], self.fields["strip"],
                  hover=lambda v: self._preview.peek("strip", v)).pack(
            side="left")
        self._unit(holder, "m")

        holder = self._row("Footpath strips",
                           "The same width again, for the footpath.")
        self._seg(holder, ["0.25", "0.5", "1.0"], self.fields["footpath"],
                  hover=lambda v: self._preview.peek("footpath", v)).pack(
            side="left")
        self._unit(holder, "m")

        self._use(self._place("Display"))
        self._band("Display", "  playback only, the results do not change")
        holder = self._row("3D view",
                           "A perspective scene instead of the plan view. "
                           "Line art is the lighter of the two.")
        self._seg(holder, list(self.VIEW_CHOICES), self.render3d_var,
                  hover=lambda v: self._preview.peek("render3d", v)).pack(
            anchor="w")

        # Playback speed reads as words: a frame delay in milliseconds
        # looked like a leftover from the Java form beside the rest, and
        # nobody chooses "20 ms".  The values behind the words are the
        # same (SPEED_CHOICES); the footer names the milliseconds on a
        # change.  The pixels-per-metre "Scale" row is gone from the form
        # (11 Oct 2026): a drawing resolution the picture is zoomed to fit
        # anyway meant nothing to a reader, and its row is what pays for
        # the pedestrians and parked vehicles having a row each.  It is
        # still a parameter: defaults.txt and --set PixelPerMeter.
        holder = self._row("Playback speed",
                           "Playback pace only; the results never change.")
        _SpeedSlider(holder, self.fields["speed"],
                     font=("Segoe UI Semibold", level["cell"][1]),
                     small=("Segoe UI", max(8, level["unit"] - 1)),
                     length=130).pack(anchor="w")

        for index, column in enumerate(self._columns):
            self._rows[index] += 1
            tk.Frame(column, background=_UI["panel"],
                     height=level["tail"]).grid(
                row=self._rows[index], column=0, columnspan=2, sticky="ew")

        # ---- the footer, which does not scroll ---------------------------
        inner = tk.Frame(self._footer, background=_UI["band"])
        inner.pack(fill="x", padx=level["pad"][0] * 2, pady=level["foot_pad"])
        self._button(inner, "Reset to defaults",
                     self._reset_to_defaults).pack(side="left")
        # In the footer, not the form: a footer button costs no height, and
        # the density ladder has none to give (see CLAUDE.md).
        self._button(inner, "Import map…", self._open_import).pack(
            side="left", padx=(10, 0))
        self._button(inner, "Help  (F1)", self.dhaka_sim_frame.show_help,
                     small=True).pack(side="left", padx=(10, 0))
        start = self._button(inner, "Start simulation", self.start_simulation,
                             primary=True)
        start.pack(side="right")
        start.focus_set()
        # The footer's status line, at every density: what the run will be,
        # what the last change did, or why a value cannot be used.  At the
        # densest layout it also carries the captions that came off the
        # rows, while the pointer is on one.  ``width=1`` with fill, so a
        # long sentence is clipped rather than widening the window -- the
        # form stacks its columns at 1280 px on width alone.
        self._hint = tk.Label(
            inner, text="", font=("Segoe UI", level["group"] + 1),
            background=_UI["band"], foreground=_UI["muted"], anchor="w",
            width=1)
        self._hint.pack(side="left", fill="x", expand=True, padx=16)

    def _reset_to_defaults(self) -> None:
        """Put every setting back to the place's own values -- after
        asking, when any of them has been changed.  The one control on
        the form that throws edits away, and it used to do so silently
        (heuristic evaluation, 15 Oct 2026)."""
        values = self._values()
        changed = [SETTING_INFO[k][0] for k, v in values.items()
                   if k in self._snapshot and self._snapshot[k] != v]
        if changed:
            named = ", ".join(changed[:4]) + (", ..." if len(changed) > 4
                                              else "")
            if not messagebox.askyesno(
                    "Reset to defaults",
                    f"{len(changed)} setting{'s' if len(changed) > 1 else ''} "
                    f"differ from {_place_name(values['network'])}'s own "
                    f"defaults: {named}.\n\nPut them all back?"):
                return
        self.dhaka_sim_frame.show_options()

    def _open_import(self):
        """Open the map-import dialog; a second click fronts the first one."""
        dialog = getattr(self, "_import_dialog", None)
        if dialog is not None and dialog.top.winfo_exists():
            dialog.top.lift()
            dialog.top.focus_set()
            return
        self._import_dialog = _ImportDialog(self)

    def _remove_import(self):
        """Delete the selected imported network, after asking.

        Only imports are removable: the affordance sits on the imported
        groups alone, and ``map_import.remove`` refuses anything without
        the import marker, so a shipped network is safe from both ends.
        """
        network = self.network_var.get().strip()
        if not map_import.is_imported(network):
            messagebox.showinfo(
                "Remove imported map",
                "Select an imported map's tile first. The shipped survey "
                "networks cannot be removed.")
            return
        if not messagebox.askyesno(
                "Remove imported map",
                f"Delete input/{network}?\n\nThis removes the imported "
                "network, its imagery and its demand from disk. It can be "
                "re-imported later."):
            return
        map_import.remove(network)
        self.network_var.set(self.SINGLE_JUNCTIONS[0])
        self._build_form()
        self._viewport = None
        self._fit_to_window(force=True)

    def _on_network_change(self):
        preview = getattr(self, "_preview", None)
        if preview is not None:
            # Before the place's defaults are written: the preview holds a
            # snapshot of Parameters it will put back, and a restore made
            # after this write would undo it.
            preview.stop()
        self._applying_defaults = True
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
        # What the place set is the baseline a reader's changes are marked
        # against; the footer names what it set.
        self._applying_defaults = False
        self._snapshot = self._values()
        if self._last:
            # The junction itself is a change to take back; the settings
            # its defaults rewrote lose their histories, since the values
            # in them belong to the other place.
            self._remember("network", self.network_var.get())
            for key, value in self._snapshot.items():
                if key != "network" and self._last.get(key) != value:
                    self._history.pop(key, None)
                    self._undo_order = [k for k in self._undo_order
                                        if k != key]
                    self._last[key] = value
        self._refresh_changed_marks()
        self._show_status(self._network_summary(applied))
        if self._status_job is not None:
            self.frame.after_cancel(self._status_job)
        self._status_job = self.frame.after(7000, self._idle_status)
        # Settings the selected place cannot use leave the form: an hourly
        # demand profile it does not have, a pedestrians switch its
        # defaults.txt has decided.  Deferred because this runs from the
        # network variable's own trace, and rebuilding the form destroys the
        # very widget that fired it.
        if self._shown_rows is not None and self._trimmed_rows() != self._shown_rows:
            self.frame.after_idle(self._refit_trimmed)
        if preview is not None:
            preview.request("network")

    def dispose(self) -> None:
        """Stop the preview's runs and put Parameters back, and drop the
        footer timer so it cannot fire into a destroyed form."""
        try:
            self.frame.winfo_toplevel().unbind("<Control-z>")
        except tk.TclError:
            pass
        if self._status_job is not None:
            try:
                self.frame.after_cancel(self._status_job)
            except tk.TclError:
                pass
            self._status_job = None
        self._preview._discard()

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
        problem = self._validate()
        if problem:
            self._show_status(problem, error=True)
            messagebox.showwarning("Check the settings", problem)
            return
        # The preview's run goes first, and its snapshot of Parameters is
        # put back, so the run starts from the form and nothing else.
        self._preview.stop()
        self._apply_to_parameters()
        self.dhaka_sim_frame.show_simulation()

    def _apply_to_parameters(self, quiet: bool = False) -> None:
        """Write the form into ``Parameters``, as a run reads them.

        One function for Start and for the preview, so the preview can
        never show settings the run would read differently.  ``quiet``
        skips the console lines a run announces itself with.
        """
        say = (lambda _text: None) if quiet else print
        Parameters.NETWORK_DIR = self.network_var.get().strip()
        Parameters.DEMAND_SCALE = float(self.fields["traffic"].get())
        choice = self.time_var.get()
        Parameters.TIME_OF_DAY = (self.TIME_CHOICES.index(choice) - 1
                                  if choice in self.TIME_CHOICES else -1)
        say("Network: " + (Parameters.NETWORK_DIR or "input/"))
        Parameters.simulation_speed = int(self.fields["speed"].get())
        Parameters.simulation_end_time = int(self.fields["end_time"].get())
        Parameters.seed = int(self.fields["seed"].get())
        say("Seed: " + str(Parameters.seed))
        say("CF Model: " + str(Parameters.car_following_model.name))
        Parameters.random = (JavaRandom() if Parameters.seed < 0
                             else JavaRandom(Parameters.seed))
        # A seed pins the whole run, as --seed does headless: the object,
        # pedestrian and blockage draws go through the seeded stream, and
        # the pedestrian arrival generators built at launch are rebuilt on
        # it.  -1 keeps the Java behaviour, a fresh run every time.
        Parameters.DETERMINISTIC = Parameters.seed >= 0
        Constants.reseed()
        Parameters.TRACE_MODE = self.trace_var.get() == "On"
        Parameters.SIGNAL_MODE = self.SIGNAL_CHOICES.get(
            self.signal_var.get(), "fixed")
        say("Signal control: " + Parameters.SIGNAL_MODE)
        try:
            green = int(float(self.fields["signal_green"].get()))
        except ValueError:
            green = Parameters.SIGNAL_CHANGE_DURATION
        Parameters.SIGNAL_CHANGE_DURATION = max(1, green)
        Parameters.GEOMETRY_MODE = self.geometry_var.get() == "On"
        say("Real geometry: " + ("On" if Parameters.GEOMETRY_MODE else "Off"))
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


#: The import's stages, as the first words of the worker's own log lines,
#: with the share of the whole each one starts at.  The picker sits
#: between "building the network" and "keeping": the bar is hidden while
#: the user chooses, and comes back at 0.45 when Finish import is pressed.
IMPORT_STAGES = (
    ("looking up", 0.02),
    ("asking ", 0.06),
    ("rebuilding", 0.0),
    ("roads fetched", 0.22),
    ("building the network", 0.28),
    ("keeping ", 0.45),
    ("generating routes", 0.55),
    ("fetching the background map", 0.70),
    ("bending the links", 0.86),
    ("done.", 1.0),
)


def import_progress(line, current):
    """The stage floor a worker log line moves the bar to, or ``None`` if
    the line is not a stage boundary.  Never lower than ``current`` --
    except the "rebuilding" line, which starts the import over."""
    text = line.strip()
    for prefix, floor in IMPORT_STAGES:
        if prefix == "roads fetched":
            hit = prefix in text          # "166 roads fetched."
        else:
            hit = text.startswith(prefix)
        if hit:
            return floor if prefix == "rebuilding" else max(current, floor)
    return None


def next_import_floor(floor):
    """The floor of the stage after the one at ``floor`` -- what the bar
    creeps towards while that stage runs."""
    for _prefix, next_floor in IMPORT_STAGES:
        if next_floor > floor:
            return next_floor
    return 1.0


class _HelpWindow:
    """The in-app help page, opened by F1 or a Help button on either screen.

    A read-only ``tk.Text`` in the application's palette, filled from
    ``help_sections`` so the text is one table and not two.  One window
    per application: a second request fronts the first.
    """

    def __init__(self, parent):
        top = self.top = tk.Toplevel(parent)
        top.title("DhakaSim help")
        top.configure(background=_UI["ground"])
        top.geometry("760x640")
        top.minsize(480, 320)
        panel = tk.Frame(top, background=_UI["panel"], padx=4, pady=4,
                         highlightthickness=1, highlightbackground=_UI["edge"])
        panel.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        text = self.text = tk.Text(
            panel, wrap="word", background=_UI["panel"],
            foreground=_UI["text"], insertbackground=_UI["panel"],
            font=("Segoe UI", 10), relief="flat", bd=0, padx=16, pady=12,
            highlightthickness=0, cursor="arrow",
            selectbackground=_UI["glow"], selectforeground=_UI["text"])
        bar = tk.Scrollbar(panel, orient="vertical", command=text.yview,
                           background=_UI["seg_off"],
                           activebackground=_UI["seg_hover"],
                           troughcolor=_UI["ground"], borderwidth=0,
                           highlightthickness=0, elementborderwidth=0,
                           width=12)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.tag_configure("h1", font=("Segoe UI Semibold", 15),
                           foreground=_UI["accent_text"], spacing3=6)
        text.tag_configure("h2", font=("Segoe UI Semibold", 12),
                           foreground=_UI["accent_text"], spacing1=14,
                           spacing3=4)
        text.tag_configure("p", spacing3=7, lmargin1=0, lmargin2=0)
        text.tag_configure("li", spacing3=5, lmargin1=14, lmargin2=14)
        text.insert("end", "DhakaSim\n", "h1")
        for heading, paragraphs in help_sections():
            text.insert("end", heading + "\n", "h2")
            tag = "li" if heading == "The settings" else "p"
            for paragraph in paragraphs:
                text.insert("end", paragraph + "\n", tag)
        text.configure(state="disabled")
        foot = tk.Frame(top, background=_UI["ground"])
        foot.pack(fill="x", padx=12, pady=(0, 12))
        _ToolButton(foot, text="Close  (Esc)", command=self.close).pack(
            side="right")
        top.bind("<Escape>", lambda _e: self.close())
        top.bind("<F1>", lambda _e: self.close())
        text.bind("<MouseWheel>", lambda e: text.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        top.protocol("WM_DELETE_WINDOW", self.close)
        text.focus_set()

    def content(self) -> str:
        return self.text.get("1.0", "end")

    def close(self) -> None:
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class _ImportDialog:
    """The start screen's "Import map" window.

    A thin shell over :mod:`dhakasim.map_import`, which is itself a shell
    over the command-line chain -- everything this window does, the scripts
    can do.  The fetching and building run in a worker thread; tkinter is
    only ever touched from the Tk thread, so the worker talks through a
    queue the dialog polls with ``after``.
    """

    RADIUS_CHOICES = ("200", "300", "500", "750", "1000", "1500", "2000",
                      "3000")
    PREVIEW_W = 380
    PREVIEW_H = 380

    def __init__(self, option):
        import queue as queue_module
        self.option = option
        self._queue = queue_module.Queue()
        self._job = None
        self._worker = None
        self._cancelled = False
        self._done = False

        top = self.top = tk.Toplevel(option.frame)
        top.title("Import a map")
        top.configure(background=_UI["ground"])
        top.transient(option.frame.winfo_toplevel())
        top.protocol("WM_DELETE_WINDOW", self._close)
        top.resizable(False, False)

        panel = self.panel = tk.Frame(
            top, background=_UI["panel"], padx=18, pady=14,
            highlightthickness=1, highlightbackground=_UI["edge"])
        panel.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(panel, text="Import a map from OpenStreetMap",
                 font=("Segoe UI Semibold", 13), background=_UI["panel"],
                 foreground=_UI["accent_text"]).pack(anchor="w")
        tk.Label(panel,
                 text="Fetches the real roads, builds a runnable network, "
                      "downloads the background map and bends the links "
                      "onto the streets. The demand is synthetic -- routes "
                      "at a uniform rate, not surveyed counts -- so the "
                      "result is for demonstration until real demand is "
                      "supplied.",
                 font=("Segoe UI", 9), background=_UI["panel"],
                 foreground=_UI["faint"], wraplength=500,
                 justify="left").pack(anchor="w", pady=(2, 10))

        self.where_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.radius_var = tk.StringVar(value="1000")
        self.roads_var = tk.StringVar(value=map_import.CLASS_PRESETS[0][0])
        self.dual_var = tk.StringVar(value="Fuse into one road")
        self.kind_var = tk.StringVar(value="Multi intersection")

        # Form on the left, preview map on the right -- side by side rather
        # than stacked, or the dialog with the log under it would outgrow a
        # 768-high screen.
        content = tk.Frame(panel, background=_UI["panel"])
        content.pack(anchor="w", fill="x")
        self.form = tk.Frame(content, background=_UI["panel"])
        self.form.pack(side="left", anchor="n")

        self._caption("Where",
                      "A place name, \"lat, lon\", or a pasted Google Maps "
                      "or OpenStreetMap link.")
        where_box = self._entry(self.where_var, 52)
        where_box.bind("<Return>", lambda _e: self._preview_clicked())
        self._caption("Call it",
                      "The name the start screen will show. Left blank, the "
                      "place's own name is used.")
        self._entry(self.name_var, 36)
        self._caption("Radius", "Metres of city around the point to import.")
        _Segmented(self._line(), self.RADIUS_CHOICES, self.radius_var,
                   font=("Segoe UI Semibold", 10),
                   pad=(10, 4)).pack(anchor="w")
        self._caption("Roads",
                      "Main roads keeps the network light; every extra "
                      "class multiplies the links.")
        _Segmented(self._line(),
                   [label for label, _ in map_import.CLASS_PRESETS],
                   self.roads_var, font=("Segoe UI Semibold", 10),
                   pad=(10, 4)).pack(anchor="w")
        self._caption("Divided roads",
                      "Fusing a dual carriageway into one two-way link "
                      "matches the surveyed networks.")
        _Segmented(self._line(), ["Fuse into one road", "Keep separate"],
                   self.dual_var, font=("Segoe UI Semibold", 10),
                   pad=(10, 4)).pack(anchor="w")
        self._caption("Kind",
                      "Single adds a step: pick the junction and its legs "
                      "after the fetch.")
        _Segmented(self._line(),
                   ["Single intersection", "Multi intersection"],
                   self.kind_var, font=("Segoe UI Semibold", 10),
                   pad=(10, 4)).pack(anchor="w")

        preview_box = tk.Frame(content, background=_UI["panel"])
        preview_box.pack(side="left", anchor="n", padx=(18, 0))
        self.preview = tk.Canvas(
            preview_box, width=self.PREVIEW_W, height=self.PREVIEW_H,
            background=_UI["ground"], highlightthickness=1,
            highlightbackground=_UI["edge"])
        self.preview.pack()
        self.preview.create_text(
            self.PREVIEW_W / 2, self.PREVIEW_H / 2,
            text="Press Preview to see the spot\nand the import circle",
            justify="center", font=("Segoe UI", 9), fill=_UI["faint"])
        self.preview_label = tk.Label(
            preview_box, text="", font=("Segoe UI", 8),
            background=_UI["panel"], foreground=_UI["faint"],
            wraplength=self.PREVIEW_W, justify="left")
        self.preview_label.pack(anchor="w", pady=(3, 0))
        # Shown only while the junction picker is up: Palashi's "junction"
        # is a roundabout, and the model needs to be told which it is --
        # a roundabout gets a ring and no signals.
        self.jtype_var = tk.StringVar(value="Signalised crossing")
        self._jtype_row = tk.Frame(preview_box, background=_UI["panel"])
        tk.Label(self._jtype_row, text="This junction is",
                 font=("Segoe UI", 9), background=_UI["panel"],
                 foreground=_UI["muted"]).pack(side="left", padx=(0, 8))
        _Segmented(self._jtype_row, ["Signalised crossing", "Roundabout"],
                   self.jtype_var, font=("Segoe UI Semibold", 9),
                   pad=(8, 3)).pack(side="left")

        buttons = tk.Frame(panel, background=_UI["panel"])
        buttons.pack(fill="x", pady=(12, 8))
        self._import_btn = option._button(buttons, "Import", self._start,
                                          primary=True)
        self._import_btn.pack(side="left")
        option._button(buttons, "Preview", self._preview_clicked).pack(
            side="left", padx=(10, 0))
        option._button(buttons, "Close", self._close).pack(
            side="left", padx=(10, 0))
        # Shown only while picking: the second try.  The legs a junction
        # offers are whatever the extract holds, so when the one you want
        # is missing the answer is a wider radius or more road classes --
        # change them on the form and Rebuild fetches again without
        # closing the dialog or retyping anything.
        self._rebuild_btn = option._button(buttons, "Rebuild", self._rebuild)
        self.status = tk.Label(buttons, text="", font=("Segoe UI", 9),
                               background=_UI["panel"],
                               foreground=_UI["muted"], anchor="w")
        self.status.pack(side="left", padx=(14, 0))

        # A progress bar for the stretches with nothing to read: the
        # Overpass wait, make_network on a kilometre of city, the route
        # generation and the tile fetch each take long enough that a
        # still window reads as a hung one.  Drawn on a canvas, not a
        # ttk.Progressbar -- under the Windows theme ttk takes its colours
        # from the OS.  Stage floors come from the worker's own log lines
        # (``import_progress``) and the bar creeps towards the next floor
        # between them, so it keeps moving while a stage runs.
        self._progress = tk.Canvas(panel, height=5,
                                   background=_UI["seg_off"],
                                   highlightthickness=0, bd=0)
        self._progress_at = 0.0
        self._progress_floor = 0.0
        self._progress_shown = False

        self.log = tk.Text(panel, height=10, relief="flat",
                           background=_UI["ground"], foreground=_UI["muted"],
                           insertbackground=_UI["text"], font=("Consolas", 8),
                           state="disabled", wrap="word",
                           highlightthickness=1,
                           highlightbackground=_UI["edge"])
        self.log.pack(fill="both", expand=True)

        # The preview's moving parts.  The circle follows the radius strip
        # live once a spot has been located; a generation counter lets a
        # slow tile fetch that has been superseded fall on the floor.
        self._located = None
        self._preview_gen = 0
        self._preview_photos = []
        # The junction picker's state: None until a single-intersection
        # import reaches the pick stage.
        self._pick = None
        self._picking = False
        self._leg_items = {}
        self._hover_leg = None
        # Dragging the preview pans it: the map moves live, and on release
        # the point under the crosshair becomes the import point.  The
        # click that picks a junction is therefore taken on *release*,
        # only when the pointer has not moved.
        self._view = None
        self._press = None
        self._drag_last = None
        self.preview.bind("<ButtonPress-1>", self._preview_press)
        self.preview.bind("<B1-Motion>", self._preview_drag)
        self.preview.bind("<ButtonRelease-1>", self._preview_release)
        self.preview.bind("<Motion>", self._preview_hover)
        self.radius_var.trace_add("write", self._radius_changed)
        # A single intersection wants a couple of hundred metres, not a
        # neighbourhood; the radius follows the kind so the default is
        # never a 200 m-scale junction drawn from a kilometre of city.
        # It wants the local streets too: a junction's legs are often
        # tertiary (Shahbag's north arm; Palashi's), and with only the
        # main roads the crossing comes out with two or three of them,
        # while a 300 m extract stays small whatever it includes.
        self.kind_var.trace_add("write", self._kind_changed)

        # One polling loop for the dialog's whole life: the import and the
        # preview both answer through the same queue.
        self._poll()

    # -- small builders ----------------------------------------------------

    def _caption(self, title, text):
        row = tk.Frame(self.form, background=_UI["panel"])
        row.pack(anchor="w", fill="x", pady=(6, 0))
        tk.Label(row, text=title, font=("Segoe UI Semibold", 10),
                 background=_UI["panel"], foreground=_UI["text"]).pack(
            side="left")
        tk.Label(row, text="  " + text, font=("Segoe UI", 8),
                 background=_UI["panel"], foreground=_UI["faint"]).pack(
            side="left")

    def _line(self):
        line = tk.Frame(self.form, background=_UI["panel"])
        line.pack(anchor="w", fill="x", pady=(2, 0))
        return line

    def _entry(self, variable, width):
        box = tk.Entry(self._line(), textvariable=variable, width=width,
                       font=("Consolas", 10), relief="flat",
                       background=_UI["seg_off"], foreground=_UI["text"],
                       insertbackground=_UI["accent"], highlightthickness=2,
                       highlightbackground=_UI["edge"],
                       highlightcolor=_UI["accent"])
        box.pack(side="left", ipady=3, ipadx=6)
        return box

    def _append(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text, error=False):
        self.status.configure(
            text=text,
            foreground="#E86A6A" if error else _UI["accent_text"])

    # -- the progress bar --------------------------------------------------

    def _progress_stage(self, line):
        """A worker log line arrived: move the bar to that stage's floor."""
        floor = import_progress(line, self._progress_floor)
        if floor is None:
            return
        if not self._progress_shown:
            self._progress_shown = True
            self._progress.pack(fill="x", pady=(0, 6), before=self.log)
        self._progress_floor = floor
        self._progress_at = max(self._progress_at, floor)
        self._draw_progress()

    def _hide_progress(self):
        if self._progress_shown:
            self._progress_shown = False
            self._progress.pack_forget()
        self._progress_at = 0.0
        self._progress_floor = 0.0

    def _tick_progress(self):
        """Creep towards the next stage's floor while a stage runs."""
        if not self._progress_shown:
            return
        goal = next_import_floor(self._progress_floor)
        self._progress_at += (goal - self._progress_at) * 0.03
        self._draw_progress()

    def _draw_progress(self):
        bar = self._progress
        bar.delete("all")
        width = bar.winfo_width() or 1
        height = int(bar.cget("height"))
        bar.create_rectangle(0, 0, width * min(self._progress_at, 1.0),
                             height, fill=_UI["accent"], outline="")

    # -- the work ----------------------------------------------------------

    def _start(self):
        if self._done:
            return
        if self._picking:
            # The button reads "Finish import" at this stage.
            pick = self._pick
            if pick is None or not pick["junctions"]:
                self._set_status("click a junction on the map first",
                                 error=True)
                return
            kept = [pick["arms"][i]["leg"] for i in sorted(pick["legs"])]
            # Checked here, with the pick still on screen: a refusal from
            # the worker would have thrown the staged folder away.
            problem = map_import.check_legs(kept, pick["centres"])
            if problem:
                self._set_status(problem, error=True)
                return
            self._picking = False
            self._jtype_row.pack_forget()
            self._rebuild_btn.pack_forget()
            self._import_btn.configure(text="Cancel")
            self._set_status("finishing the import...")
            # Default ring sizes for a declared roundabout; measured
            # values can be edited into geometry.txt afterwards.  The
            # strip only shows for a single junction; a multi import's
            # junctions are all crossings until geometry.txt says else.
            roundabouts = None
            if (pick["kind"] == "single"
                    and self.jtype_var.get() == "Roundabout"):
                roundabouts = {pick["junctions"][0]: (8.0, 7.0)}
            self._worker = threading.Thread(
                target=self._finish_work,
                args=(kept, pick["centres"], roundabouts), daemon=True)
            self._worker.start()
            return
        if self._worker is not None:
            # The button reads Cancel while a run is up.
            self._cancelled = True
            if self._job is not None:
                self._job.cancel()
            self._set_status("cancelling...")
            return
        where = self.where_var.get().strip()
        if not where:
            self._set_status("say where -- a name, a link, or lat, lon",
                             error=True)
            return
        # Everything the worker needs is read here, on the Tk thread;
        # tkinter variables are not safe to read from another thread.
        name = self.name_var.get().strip()
        radius = float(self.radius_var.get())
        classes = dict(map_import.CLASS_PRESETS)[self.roads_var.get()]
        merge = self.dual_var.get() == "Fuse into one road"
        kind = ("single" if self.kind_var.get().startswith("Single")
                else "multi")
        # An earlier import under this name may be replaced -- that is
        # the second try after legs did not come out -- but only with a
        # yes, and never a shipped network.  Asked here, on the Tk
        # thread, because the worker cannot put up a dialog.
        replace = False
        folder = self._folder_for(where, name)
        if folder is not None and os.path.isdir(os.path.join("input",
                                                             folder)):
            if not map_import.is_imported(folder):
                self._set_status(f"input/{folder} is a shipped network -- "
                                 "pick another name", error=True)
                return
            if not messagebox.askyesno(
                    "Replace the earlier import?",
                    f"input/{folder} is an earlier import. Replace it with "
                    "this one?\n\nThe old one is deleted as soon as the "
                    "new fetch starts.", parent=self.top):
                self._set_status("kept the earlier import -- pick another "
                                 "name to import alongside it")
                return
            replace = True
        self._cancelled = False
        self._import_btn.configure(text="Cancel")
        self._set_status("working -- this takes a minute or two")
        self._worker = threading.Thread(
            target=self._work,
            args=(where, name, radius, classes, merge, kind, replace),
            daemon=True)
        self._worker.start()

    @staticmethod
    def _folder_for(where, name):
        """The folder an import would land in, or None until geocoded.

        Mirrors the worker's naming: the typed name, else the typed
        coordinates, else the place text itself (the geocoder's display
        name is never used -- see _work).
        """
        if name:
            return map_import.slugify(name)
        point = map_import.parse_location(where)
        if point is not None:
            return map_import.slugify(f"Imported {point[0]:.4f}, "
                                      f"{point[1]:.4f}")
        return map_import.slugify(where)

    def _rebuild(self):
        """Throw the staged network away and fetch again with the form
        as it now stands -- the second try, from inside the picker."""
        if not self._picking or self._worker is not None:
            return
        if self._job is not None:
            self._job.discard()
        self._job = None
        self._picking = False
        self._pick = None
        self._jtype_row.pack_forget()
        self._rebuild_btn.pack_forget()
        self.preview.delete("all")
        self._preview_photos = []
        self.preview_label.configure(text="")
        self._append("rebuilding with the settings as they now stand...")
        self._start()

    def _work(self, where, name, radius, classes, merge, kind,
              replace=False):
        post = self._queue.put

        def log(line):
            post(("log", line))

        try:
            point = map_import.parse_location(where)
            if point is None:
                log(f'looking up "{where}"...')
                hit = map_import.geocode(where)
                if hit is None:
                    raise RuntimeError(f'nowhere called "{where}" was found')
                lat, lon, display = hit
                log(f"found {display}")
                if not name:
                    # The user's own words, not the geocoder's display name:
                    # for Dhaka that comes back in Bengali, which the ASCII
                    # folder slug reduces to nothing.
                    name = where
            else:
                lat, lon = point
                if not name:
                    name = f"Imported {lat:.4f}, {lon:.4f}"
            folder = map_import.slugify(name)
            self._job = map_import.ImportJob(
                name, folder, lat, lon, radius, classes, merge, kind,
                replace=replace)
            # Stop after the build and hand the network to the junction
            # picker; the rest runs from _finish_work once the user has
            # chosen the junction(s) and their legs.  Both kinds pick:
            # a multi import is several junctions chosen from the same
            # staged network, each with its legs, joined where the same
            # road runs between them.
            out_dir = self._job.fetch_and_build(log, lambda: self._cancelled)
            try:
                shape = map_import.read_network_shape(out_dir)
                if not map_import.junction_candidates(*shape[:2]):
                    raise RuntimeError(
                        "no junction with three or more legs in the "
                        "extract -- try a slightly larger radius")
                tiles = map_import.preview_tiles(
                    lat, lon, radius, self.PREVIEW_W, self.PREVIEW_H)
            except BaseException:
                self._job.discard()
                raise
            post(("pick", radius, tiles, shape, kind))
        except Exception as exc:
            post(("error", str(exc)))

    def _finish_work(self, keep, centres, roundabouts=None):
        post = self._queue.put

        def log(line):
            post(("log", line))

        job = self._job
        try:
            log(f"keeping {len(centres)} junction(s) with {len(keep)} "
                "legs..." + (" (as a roundabout)" if roundabouts else ""))
            map_import.prune_to_junctions(job.out_dir, keep, centres,
                                          roundabouts=roundabouts)
            folder = job.finish(log, lambda: self._cancelled)
            post(("done", folder, tuple(job.warnings)))
        except Exception as exc:
            job.discard()
            post(("error", str(exc)))

    def _poll(self):
        """The dialog's one message pump, running for its whole life."""
        if not self.top.winfo_exists():
            return
        self._tick_progress()
        while not self._queue.empty():
            message = self._queue.get_nowait()
            kind = message[0]
            if kind == "log":
                self._append(message[1])
                self._progress_stage(message[1])
            elif kind == "error":
                self._worker = None
                self._job = None
                self._hide_progress()
                self._rebuild_btn.pack_forget()
                self._import_btn.configure(text="Import")
                self._set_status(message[1], error=True)
                self._append("FAILED: " + message[1])
            elif kind == "done":
                self._worker = None
                self._job = None
                self._done = True
                self._hide_progress()
                self._finish(message[1], message[2])
            elif kind == "preview":
                (_kind, gen, lat, lon, display, radius, mpp, placed,
                 view) = message
                if gen == self._preview_gen:
                    self._located = (lat, lon, display)
                    self._view = view
                    self._render_preview(radius, mpp, placed, display)
            elif kind == "preview_error":
                if message[1] == self._preview_gen:
                    self._set_status(message[2], error=True)
            elif kind == "pick":
                self._worker = None
                self._picking = True
                self._hide_progress()
                # Invalidate any slow preview still in flight: its render
                # arriving now would wipe the picker off the canvas.
                self._preview_gen += 1
                self._import_btn.configure(text="Finish import")
                self._rebuild_btn.pack(side="left", padx=(10, 0),
                                       before=self.status)
                if message[4] == "single":
                    self._jtype_row.pack(anchor="w", pady=(4, 0))
                self._render_picker(*message[1:])
        self.top.after(120, self._poll)

    # -- the preview map ---------------------------------------------------

    def _preview_clicked(self):
        # Not while the junction picker is up: a preview render would wipe
        # the picker's overlay off the canvas mid-selection.
        if self._worker is not None or self._done or self._picking:
            return
        where = self.where_var.get().strip()
        if not where:
            self._set_status("say where first -- then Preview", error=True)
            return
        self._preview_gen += 1
        self._set_status("fetching the preview...")
        threading.Thread(
            target=self._preview_work,
            args=(self._preview_gen, where, float(self.radius_var.get())),
            daemon=True).start()

    def _kind_changed(self, *_args):
        single = self.kind_var.get().startswith("Single")
        self.radius_var.set("300" if single else "1000")
        self.roads_var.set(map_import.CLASS_PRESETS[1 if single else 0][0])

    def _radius_changed(self, *_args):
        """Once a spot is located, the circle follows the radius strip."""
        if (self._located is None or self._worker is not None
                or self._done or self._picking):
            return
        try:
            radius = float(self.radius_var.get())
        except ValueError:
            return
        lat, lon, display = self._located
        self._preview_gen += 1
        threading.Thread(
            target=self._tiles_work,
            args=(self._preview_gen, lat, lon, display, radius),
            daemon=True).start()

    def _preview_work(self, gen, where, radius):
        try:
            point = map_import.parse_location(where)
            if point is None:
                hit = map_import.geocode(where)
                if hit is None:
                    raise RuntimeError(f'nowhere called "{where}" was found')
                lat, lon, display = hit
            else:
                lat, lon = point
                display = f"{lat:.5f}, {lon:.5f}"
        except Exception as exc:
            self._queue.put(("preview_error", gen, str(exc)))
            return
        self._tiles_work(gen, lat, lon, display, radius)

    def _tiles_work(self, gen, lat, lon, display, radius):
        try:
            mpp, placed, view = map_import.preview_tiles(
                lat, lon, radius, self.PREVIEW_W, self.PREVIEW_H)
        except Exception as exc:
            self._queue.put(("preview_error", gen,
                             f"no preview -- check the connection ({exc})"))
            return
        self._queue.put(("preview", gen, lat, lon, display, radius,
                         mpp, placed, view))

    def _render_preview(self, radius, mpp, placed, display):
        canvas = self.preview
        canvas.delete("all")
        # PhotoImages are dropped by Tk the moment Python stops referencing
        # them; the list keeps this frame's tiles alive.
        self._preview_photos = []
        for cx, cy, path in placed:
            try:
                photo = tk.PhotoImage(file=path)
            except tk.TclError:
                continue                      # one bad tile is not fatal
            self._preview_photos.append(photo)
            canvas.create_image(cx, cy, image=photo, anchor="nw")
        if not placed:
            canvas.create_text(self.PREVIEW_W / 2.0, 40,
                               text="map tiles unavailable right now",
                               font=("Segoe UI", 9), fill=_UI["faint"])
        cx, cy = self.PREVIEW_W / 2.0, self.PREVIEW_H / 2.0
        r = radius / mpp
        # A dark underline ring keeps the accent ring visible over the
        # map's pale ground.
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline="#0A130D", width=4)
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           outline=_UI["accent"], width=2)
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                           fill=_UI["accent"], outline="#0A130D")
        canvas.create_text(self.PREVIEW_W - 5, self.PREVIEW_H - 4,
                           text="(c) OpenStreetMap", anchor="se",
                           font=("Segoe UI", 7), fill="#5A5A5A")
        self.preview_label.configure(
            text=f"{display} -- circle is the {radius:.0f} m import radius")
        self._set_status("previewed -- adjust the radius, or Import")

    # -- the junction picker -----------------------------------------------

    def _render_picker(self, radius, tiles, shape, kind):
        """The staged network over its tiles, ready to be clicked on."""
        mpp, placed, (zoom, gx0, gy0) = tiles
        links, nodes, to_latlon = shape
        canvas = self.preview
        canvas.delete("all")
        self._preview_photos = []
        for cx, cy, path in placed:
            try:
                photo = tk.PhotoImage(file=path)
            except tk.TclError:
                continue
            self._preview_photos.append(photo)
            canvas.create_image(cx, cy, image=photo, anchor="nw",
                                tags=("tile",))

        def to_canvas(x_m, y_m):
            plat, plon = to_latlon(x_m, y_m)
            tx, ty = map_import.deg_to_tile(plat, plon, zoom)
            return (tx * map_import.TILE_PIXELS - gx0,
                    ty * map_import.TILE_PIXELS - gy0)

        self._pick = {
            "shape": (links, nodes), "to_canvas": to_canvas,
            "links": {lid: [to_canvas(x, y) for x, y in link["points"]]
                      for lid, link in links.items()},
            "width": {lid: link["width"] for lid, link in links.items()},
            "nodes": {nid: to_canvas(nodes[nid]["x"], nodes[nid]["y"])
                      for nid in map_import.junction_candidates(links,
                                                                 nodes)},
            "mpp": mpp, "kind": kind, "junctions": [], "arms": [],
            "legs": set(), "centres": {},
        }
        # Pre-select a junction near the import point: the legs come up
        # already highlighted, and clicking another amber dot moves the
        # selection (single) or adds it (multi).  Waiting for a first
        # click here just felt broken.  Among the dots within reach of
        # the crosshair the one offering the most legs wins -- the
        # nearest can be a slip road's fork a few metres from the
        # crossing that was actually aimed at.
        if self._pick["nodes"]:
            centre = (self.PREVIEW_W / 2.0, self.PREVIEW_H / 2.0)
            reach = 80.0 / mpp

            def distance(n):
                return math.hypot(self._pick["nodes"][n][0] - centre[0],
                                  self._pick["nodes"][n][1] - centre[1])

            near = [n for n in self._pick["nodes"] if distance(n) <= reach]
            if near:
                chosen = max(
                    near, key=lambda n: (len(map_import.junction_legs(
                        links, nodes, n)[0]), -distance(n)))
            else:
                chosen = min(self._pick["nodes"], key=distance)
            self._select_junction(chosen)
        self._redraw_pick()
        if kind == "single":
            text = ("The nearest junction is selected with every leg kept "
                    "(green) -- each leg is the whole road out to the edge "
                    "of the circle. Click another amber dot to move the "
                    "selection, click a leg to drop or restore it (at "
                    "least two stay), say what the junction is below, then "
                    "Finish import. Missing a leg? Change the radius or "
                    "roads and press Rebuild.")
        else:
            text = ("The nearest junction is selected with every leg kept "
                    "(green) -- each leg is the whole road out to the edge "
                    "of the circle or to the next chosen junction. Click "
                    "amber dots to add junctions (click again to remove), "
                    "click a leg to drop or restore it, then Finish "
                    "import. The chosen junctions must be joined by road. "
                    "Missing a leg? Change the radius or roads and press "
                    "Rebuild.")
        self.preview_label.configure(text=text)
        self._legs_status("adjust, then Finish import")

    def _legs_status(self, tail):
        """The kept counts in the status line, with a nudge when a single
        chosen junction has fewer legs than a crossing's worth."""
        pick = self._pick
        count = len(pick["legs"])
        if pick["kind"] == "single":
            if len(pick["arms"]) < 3:
                self._set_status(
                    f"only {len(pick['arms'])} legs here -- click "
                    "another dot, or widen the radius or roads and "
                    "Rebuild", error=True)
            else:
                self._set_status(f"{count} legs kept -- {tail}")
        else:
            self._set_status(
                f"{len(pick['junctions'])} junction"
                f"{'s' if len(pick['junctions']) != 1 else ''}, "
                f"{count} legs kept -- {tail}")

    def _select_junction(self, nid):
        """Choose ``nid``: the one junction (single) or toggle it in the
        set (multi), every leg of the chosen set kept.

        A leg is the road walked outward from the crossing to where it
        ends (``map_import.network_legs``), not the first OSM fragment
        touching the node -- at a dual-carriageway crossing that fragment
        is a few metres long, and an import made of those was a junction
        with stubs for arms.  Returns False when the click would have
        emptied the selection.
        """
        pick = self._pick
        if pick["kind"] == "single":
            pick["junctions"] = [nid]
        elif nid in pick["junctions"]:
            if len(pick["junctions"]) == 1:
                return False
            pick["junctions"].remove(nid)
        else:
            pick["junctions"].append(nid)
        links, nodes = pick["shape"]
        legs, centres = map_import.network_legs(links, nodes,
                                                pick["junctions"])
        # A chosen node inside an earlier junction's cluster is that
        # junction; it drops out of the list so a second click on it
        # cannot leave a phantom entry.
        pick["junctions"] = [j for j in pick["junctions"] if j in centres]
        pick["centres"] = centres
        pick["arms"] = [{"leg": leg,
                         "points": [pick["to_canvas"](x, y)
                                    for x, y in leg["points"]],
                         "width": max(links[lid]["width"]
                                      for lid in leg["links"])}
                        for leg in legs]
        pick["legs"] = set(range(len(legs)))
        return True

    def _redraw_pick(self):
        canvas = self.preview
        canvas.delete("ov")
        pick = self._pick
        chosen = set(pick["junctions"])
        in_arms = {lid for arm in pick["arms"] for lid in arm["leg"]["links"]}
        # Casing under every link, colour on top -- a bare line blends
        # into the map's own road colours (this junction sits among red
        # hospital icons that read as markers, so the network has to be
        # unmistakably drawn, not hinted).  The links a leg walked are
        # drawn once, as the leg, so the leg's colour is never striped by
        # the grey of its own fragments.
        styled = []
        for lid, pts in pick["links"].items():
            if lid in in_arms:
                continue
            width = max(4.0, pick["width"][lid] / pick["mpp"])
            styled.append((pts, "#6E7B88", width, (), None))
        for index, arm in enumerate(pick["arms"]):
            kept = index in pick["legs"]
            colour = _UI["accent"] if kept else "#E05B5B"
            dash = () if kept else (7, 5)
            width = max(5.0, arm["width"] / pick["mpp"])
            styled.append((arm["points"], colour, width, dash, index))
        flats = []
        for pts, colour, width, dash, index in styled:
            flat = [value for point in pts for value in point]
            canvas.create_line(*flat, fill="#14100E", width=width + 4,
                               capstyle="round", joinstyle="round",
                               tags=("ov",))
            flats.append((flat, colour, width, dash, index))
        # The colour pass is kept apart from the casing pass so no leg's
        # casing sits on a neighbour's colour; leg items are remembered so
        # hovering can thicken exactly one of them.
        self._leg_items = {}
        self._hover_leg = None
        for flat, colour, width, dash, index in flats:
            item = canvas.create_line(*flat, fill=colour, width=width,
                                      dash=dash, capstyle="round",
                                      joinstyle="round", tags=("ov",))
            if index is not None:
                self._leg_items[index] = (item, width)
        for nid, (cx, cy) in pick["nodes"].items():
            r = 9 if nid in chosen else 7
            fill = _UI["accent"] if nid in chosen else "#FFB02E"
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                               outline="#14100E", width=2, tags=("ov",))
        canvas.create_text(self.PREVIEW_W - 5, self.PREVIEW_H - 4,
                           text="(c) OpenStreetMap", anchor="se",
                           font=("Segoe UI", 7), fill="#5A5A5A",
                           tags=("ov",))

    def _preview_press(self, event):
        self._press = (event.x, event.y)
        self._drag_last = (event.x, event.y)

    def _preview_drag(self, event):
        # Dragging pans the preview.  Not while picking: the picker's
        # overlay coordinates are tied to the tiles it was drawn on.
        if (self._picking or self._view is None or self._press is None
                or self._worker is not None):
            return
        last_x, last_y = self._drag_last
        self.preview.move("all", event.x - last_x, event.y - last_y)
        self._drag_last = (event.x, event.y)

    def _preview_release(self, event):
        press, self._press = self._press, None
        if press is None:
            return
        moved = math.hypot(event.x - press[0], event.y - press[1])
        if self._picking:
            # Selection happens on release so a slip of the hand while
            # clicking never counts as a pan.
            if moved < 4.0:
                self._pick_click(event.x, event.y)
            return
        if moved < 4.0 or self._view is None or self._worker is not None:
            return
        # The point now under the crosshair becomes the import point --
        # the Where box follows, so what Import uses is always visible.
        zoom, gx0, gy0 = self._view
        lat, lon = map_import.tile_to_latlon(
            gx0 + self.PREVIEW_W / 2.0 + (press[0] - event.x),
            gy0 + self.PREVIEW_H / 2.0 + (press[1] - event.y), zoom)
        coords = f"{lat:.6f}, {lon:.6f}"
        self._located = (lat, lon, coords)
        self.where_var.set(coords)
        try:
            radius = float(self.radius_var.get())
        except ValueError:
            return
        self._preview_gen += 1
        self._set_status("re-aimed -- the centre follows your drag")
        threading.Thread(
            target=self._tiles_work,
            args=(self._preview_gen, lat, lon, coords, radius),
            daemon=True).start()

    def _preview_hover(self, event):
        if not self._picking or self._pick is None:
            return
        pick = self._pick
        near_node = any(math.hypot(event.x - cx, event.y - cy) < 13.0
                        for cx, cy in pick["nodes"].values())
        leg = None
        if not near_node and pick["junctions"]:
            leg = self._leg_at(event.x, event.y)
        self.preview.configure(
            cursor="hand2" if near_node or leg is not None else "")
        if leg != self._hover_leg:
            # Thicken the leg under the pointer, so what a click would
            # toggle is never a guess.
            previous = self._leg_items.get(self._hover_leg)
            if previous is not None:
                self.preview.itemconfigure(previous[0], width=previous[1])
            current = self._leg_items.get(leg)
            if current is not None:
                self.preview.itemconfigure(current[0],
                                           width=current[1] + 3)
            self._hover_leg = leg

    def _leg_at(self, x, y):
        """The chosen junction's leg under the pointer, or ``None``."""
        pick = self._pick
        target, target_d = None, 12.0
        for index, arm in enumerate(pick["arms"]):
            pts = arm["points"]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                d = _point_segment_px(x, y, ax, ay, bx, by)
                if d < target_d:
                    target, target_d = index, d
        return target

    def _pick_click(self, x, y):
        pick = self._pick
        if pick is None:
            return
        # A junction dot first -- clicking one (re)selects the junction
        # and starts with every leg kept.
        best, best_d = None, 13.0
        for nid, (cx, cy) in pick["nodes"].items():
            d = math.hypot(x - cx, y - cy)
            if d < best_d:
                best, best_d = nid, d
        if best is not None:
            if not self._select_junction(best):
                self._set_status("keep at least one junction", error=True)
                return
            self._redraw_pick()
            self._legs_status("click a leg to toggle, then Finish import")
            return
        if not pick["junctions"]:
            return
        target = self._leg_at(x, y)
        if target is None:
            return
        if target in pick["legs"]:
            if len(pick["legs"]) <= 2:
                self._set_status("keep at least two legs", error=True)
                return
            pick["legs"].discard(target)
        else:
            pick["legs"].add(target)
        self._redraw_pick()
        self._legs_status("Finish import when ready")

    def _finish(self, folder, warnings):
        for warning in warnings:
            self._append("WARNING: " + warning)
        # Select the new network and rebuild the form so its tile exists --
        # the same sequence _refit_trimmed uses.
        option = self.option
        option.network_var.set(folder)
        option._build_form()
        option._viewport = None
        option._fit_to_window(force=True)
        self._append(f"input/{folder} is ready and selected on the start "
                     "screen.")
        self._set_status("imported -- close this window and press Start")
        self._import_btn.configure(text="Imported")

    def _close(self):
        self._cancelled = True
        if self._job is not None:
            self._job.cancel()
            if self._picking:
                # An abandoned pick leaves a staged folder that has a
                # node.txt and no demand -- a broken start-screen tile.
                self._job.discard()
        self.top.destroy()


def _point_segment_px(px, py, ax, ay, bx, by):
    """Distance from a point to a segment, in canvas pixels."""
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _place_about(network: str) -> str:
    """One sentence on what surrounds a place, from ``about.txt`` in its
    folder (comment lines skipped), or "" when it has none.  Shown under
    the junction's map on the start screen: a reader placing "Palashi"
    wants to hear Dhaka University and BUET, which no map tile says."""
    try:
        with open(os.path.join("input", network, "about.txt"), "r",
                  encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return ""


def _place_name(network: str) -> str:
    """What to call a network on screen.

    ``place.txt`` holds the name the extract was cut around, which is what a
    reader recognises where the folder name is an identifier.  Only the part
    before the comma, or every Dhaka network reads ", Dhaka", and without the
    parenthetical, which is a note rather than a name.

    A place name that says less than the folder does is not a name: the
    old synthetic demo's file said "Dhaka", which was true of four of the
    others too, so it fell back to the folder.  Said-ness is counted in
    alphanumeric tokens rather than space-separated words, because a
    hyphenated name is not saying less -- "BUET-DU-DMC Area" is four
    tokens against the folder ``buet_du_dmc``'s three, and splitting on
    spaces alone read it as two and threw the real name away.
    """
    folder = network.replace("_", " ").title()
    try:
        with open(os.path.join("input", network, "place.txt"),
                  "r", encoding="utf-8") as handle:
            place = handle.read().strip()
    except OSError:
        return folder
    place = re.sub(r"\s*\([^)]*\)", "", place).split(",")[0].strip()
    if not place or (len(re.findall(r"[A-Za-z0-9]+", place))
                     < len(re.findall(r"[A-Za-z0-9]+", folder))):
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
        self._top_bar = None
        self._bottom_bars = ()
        self._legend = None
        self._legend_room = None
        self._legend_size = None
        self._legend_job = None
        self._basemap_box = None
        self._help = None
        self.root.bind("<F1>", lambda _e: self.show_help())
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
    DELAY_LABEL = "Playback speed"

    @staticmethod
    def _run_settings():
        """The configuration this run was started with, as label/value pairs.

        Read once, when the legend is built, rather than bound live to
        ``Parameters``.  Two reasons: these are a record of what was chosen on
        the setup form, so they should not drift if something later writes to
        the same fields, and the run's own step counter lives in there too.

        The playback speed is the exception, and is rendered as a control
        rather than as text: it only sets how long the picture pauses between frames,
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
            ("Speed limit", f"{Parameters.maximum_speed * 3.6:.0f}", "km/h"),
            ("Strip width", f"{Parameters.strip_width:g}", "m"),
            ("Footpath strips", f"{Parameters.footpath_strip_width:g}", "m"),
            # Pixels per metre is not here: it left the start screen on
            # 11 Oct 2026 (a drawing resolution the picture is zoomed to
            # fit anyway), so it is not a setting the run was set up with.
            ("Pedestrians", on_off(Parameters.across_pedestrian_mode), ""),
            ("Parked vehicles", on_off(Parameters.OBJECT_MODE), ""),
            ("Real geometry", on_off(Parameters.GEOMETRY_MODE), ""),
            ("Trace replay", on_off(Parameters.TRACE_MODE), ""),
        ]

    #: The legend's type sizes, largest first: the row size, with the
    #: headings a point up and the units a point down.  The panel is
    #: built at the largest size whose whole height stands beside the
    #: picture (``_fit_legend``): the owner could not see the run
    #: settings without collapsing the vehicle counts (12 Oct 2026), and
    #: the panel does not scroll.
    LEGEND_SIZES = (12, 11, 10, 9, 8)

    #: The glyph beside each run setting and heading (``_Icon.KINDS``).
    LEGEND_ICONS = {
        "Vehicles on network": "traffic", "Side friction": "friction",
        "Run settings": "settings",
        "Time of day": "time", "End time": "end_time",
        "Playback speed": "speed", "Random seed": "seed",
        "Speed limit": "max_speed", "Strip width": "strip",
        "Footpath strips": "footpath", "Pixels per metre": "ppm",
        "Pedestrians": "pedestrians", "Parked vehicles": "friction",
        "Real geometry": "geometry", "Trace replay": "trace",
    }

    def _dark_button(self, parent, text, command, width=None, size=10):
        """A plain ``tk.Button`` in the panel's dress (ttk would take the
        Windows theme's light grey)."""
        button = tk.Button(
            parent, text=text, command=command, cursor="hand2",
            font=("Segoe UI Semibold", size), bd=0, relief="flat",
            background=_UI["seg_off"], foreground=_UI["text"],
            activebackground=_UI["seg_hover"],
            activeforeground=_UI["text"],
            highlightthickness=1, highlightbackground=_UI["edge"])
        if width is not None:
            button.configure(width=width)
        return button

    def _build_view_controls(self, bar) -> None:
        """Zoom, traffic and the map toggle, in the toolbar beside the
        view button (owner, 12 Oct 2026: "move the Zoom slider, traffic
        density slider and background map toggle to the top of the
        screen, beside Plan view").  They are about the picture and the
        run as it plays, which is what the toolbar holds; the legend is
        left to what the run is made of.
        """
        bg = _UI["band"]
        tk.Frame(bar, width=1, bg=_UI["edge"]).pack(side="left", fill="y",
                                                    padx=(10, 8), pady=2)
        tk.Label(bar, text="Zoom", font=("Segoe UI", 10), bg=bg,
                 fg=_UI["muted"]).pack(side="left", padx=(0, 6))
        # A slider, not +/- buttons: with imagery on, the zoom snaps to the
        # ladder of scales the picture can be drawn at exactly, and a fixed
        # button step smaller than the next rung snapped straight back --
        # the buttons read as broken.  A drag states the destination
        # absolutely, so it always lands somewhere new.  The mapping is
        # logarithmic (see _slider_to_scale): zoom is a ratio, and a linear
        # slider crams every whole-network framing into its bottom few pixels.
        self._scale_slider = tk.Scale(
            bar, from_=0, to=100, orient="horizontal", showvalue=False,
            borderwidth=0, highlightthickness=0, sliderrelief="flat",
            sliderlength=18, width=10, length=90, background=_UI["seg_off"],
            troughcolor=_UI["panel"], activebackground=_UI["accent"],
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
        self._dark_button(bar, "Reset", self.reset_zoom, size=9).pack(
            side="left", padx=(0, 10))

        # Traffic level, live (owner request, 6 Oct 2026: "increase traffic
        # density while the simulation is running").  A multiplier on every
        # route's arrival rate, read at the moment each stream draws its next
        # headway, so a change takes hold on the next arrival of each stream
        # and the run never has to restart.  x1 is the surveyed (or
        # imported) demand; the same multiplier is set on the start screen.
        tk.Label(bar, text="Traffic", font=("Segoe UI", 10), bg=bg,
                 fg=_UI["muted"]).pack(side="left", padx=(0, 6))
        self._traffic_slider = tk.Scale(
            bar, from_=25, to=300, orient="horizontal",
            showvalue=False, borderwidth=0, highlightthickness=0,
            sliderrelief="flat", sliderlength=18, width=10, length=90,
            background=_UI["seg_off"], troughcolor=_UI["panel"],
            activebackground=_UI["accent"], command=self._on_traffic_slider)
        self._traffic_slider.set(int(round(Parameters.DEMAND_SCALE * 100)))
        self._traffic_slider.pack(side="left", padx=(0, 6))
        self._traffic_label = tk.Label(
            bar, text=f"x{Parameters.DEMAND_SCALE:.2g}",
            font=("Consolas", 10, "bold"), bg=bg, fg=_UI["text"], width=5,
            anchor="w")
        self._traffic_label.pack(side="left", padx=(0, 6))

        # The imagery controls live in a box of their own, because what
        # goes in it can change mid-run: a network with no map on disk
        # offers to fetch one, and once that lands the box is refilled with
        # the ordinary checkbox.
        self._basemap_box = tk.Frame(bar, bg=bg)
        self._basemap_box.pack(side="left")
        self._fill_basemap_box()

    def _build_legend(self, parent, size=11):
        """A collapsible panel: colour key plus a live count per type, and
        what the run was set up with.

        Dressed in the start screen's palette (``_UI``), because the run view
        and the form it came from are one application and should read as one.
        Plain ``tk`` widgets throughout for the same reason the start screen
        uses them: ttk takes its colours from the Windows theme and would
        punch a light grey panel through the dark chrome.  ``size`` is the
        row type in points; ``_fit_legend`` picks it.
        """
        bg = _UI["panel"]
        row_font = ("Segoe UI", size)
        head_font = ("Segoe UI Semibold", size + 1)
        mono = ("Consolas", size, "bold")
        unit_font = ("Segoe UI", size - 1)
        glyph = round(size * 1.5)
        legend = tk.Frame(parent, bg=bg, padx=10, pady=8,
                          highlightthickness=1,
                          highlightbackground=_UI["edge"])

        if Parameters.PLACE_NAME:
            tk.Label(legend, text=Parameters.PLACE_NAME,
                     font=("Segoe UI", size + 2, "bold"), bg=bg,
                     fg=_UI["text"], anchor="w", justify="left",
                     wraplength=240).pack(anchor="w", fill="x", pady=(0, 6))

        def section(title, first=False):
            """A heading with its glyph and minimise button, and the body
            under it.  A body expanded again is packed ``after=`` its own
            header; without that Tk appends it below whatever sections
            were built later."""
            header = tk.Frame(legend, bg=bg)
            header.pack(anchor="w", fill="x", pady=(0 if first else 8, 0))
            body = tk.Frame(legend, bg=bg)
            state = {"open": True}

            def toggle():
                state["open"] = not state["open"]
                if state["open"]:
                    body.pack(anchor="w", fill="x", pady=(4, 0), after=header)
                    button.configure(text="\u2212")
                else:
                    body.forget()
                    button.configure(text="+")

            _Icon(header, self.LEGEND_ICONS[title], glyph,
                  background=bg).pack(side="left", padx=(0, 6))
            tk.Label(header, text=title, font=head_font, bg=bg,
                     fg=_UI["accent_text"]).pack(side="left")
            button = tk.Button(header, text="\u2212", width=2, relief="flat",
                               bg=bg, fg=_UI["muted"], activebackground=bg,
                               activeforeground=_UI["text"],
                               font=("Segoe UI", size, "bold"), bd=0,
                               command=toggle, cursor="hand2")
            button.pack(side="right")
            body.pack(anchor="w", fill="x", pady=(4, 0))
            return body

        body = section("Vehicles on network", first=True)
        self.legend_counts = {}
        for name, idxs in self.LEGEND_CATEGORIES:
            r, g, b = Constants.VEHICLE_TYPE_COLORS[idxs[0]]
            row = tk.Frame(body, bg=bg)
            row.pack(anchor="w", fill="x")
            tk.Label(row, text="  ", bg="#%02x%02x%02x" % (r, g, b), width=2,
                     relief="solid", borderwidth=1).pack(side="left")
            tk.Label(row, text="  " + name, bg=bg, fg=_UI["muted"], width=12,
                     anchor="w", font=row_font).pack(side="left")
            var = tk.StringVar(value="0")
            tk.Label(row, textvariable=var, bg=bg, fg=_UI["text"], width=5,
                     anchor="e", font=mono).pack(side="left")
            self.legend_counts[name] = var

        total_row = tk.Frame(body, bg=bg)
        total_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(total_row, text="Total", bg=bg, fg=_UI["text"], width=15,
                 anchor="w", font=("Segoe UI", size, "bold")).pack(side="left")
        self.legend_total = tk.StringVar(value="0")
        tk.Label(total_row, textvariable=self.legend_total, bg=bg,
                 fg=_UI["accent_text"], width=5, anchor="e",
                 font=mono).pack(side="left")

        # Side friction is drawn on the same canvas but never appears in the
        # counts above, because these are obstructions rather than trips.
        # Without a key of its own a parked rickshaw is just an unexplained
        # blob, which is how it came to be mistaken for a moving one.  Its
        # own section with its own minimise button, and built only when the
        # run actually generates the objects -- a key describing things that
        # are not in the picture would be a lie on Miami and Riyadh, whose
        # defaults pin ObjectMode Off.
        if Parameters.OBJECT_MODE:
            friction = section("Side friction")
            # Two abreast: four rows of key were two rows of the height
            # the run settings needed to stand in the window.
            for index, (name, colour) in enumerate(self.FRICTION_CATEGORIES):
                cell = tk.Frame(friction, bg=bg)
                cell.grid(row=index // 2, column=index % 2, sticky="w",
                          padx=(0, 8))
                tk.Label(cell, text="  ", bg=colour.to_hex(), width=2,
                         relief="solid", borderwidth=1).pack(side="left")
                tk.Label(cell, text=" " + name, bg=bg, fg=_UI["muted"],
                         anchor="w", font=row_font).pack(side="left")

        # What the run was set up with, kept in view for the whole run.  The
        # setup form is gone by now, and every one of these changes what the
        # numbers on screen mean, so reading a result without them beside it
        # invites comparing two runs that were never comparable.
        settings = section("Run settings")
        for label, value, unit in self._run_settings():
            row = tk.Frame(settings, bg=bg)
            row.pack(anchor="w", fill="x")
            kind = self.LEGEND_ICONS.get(label)
            if kind:
                _Icon(row, kind, glyph, background=bg).pack(side="left",
                                                           padx=(0, 6))
            tk.Label(row, text=label, bg=bg, width=14, anchor="w",
                     fg=_UI["muted"], font=row_font).pack(side="left")
            if label == self.DELAY_LABEL:
                self._build_delay_control(settings, value, bg, size,
                                          indent=glyph + 6)
                continue
            tk.Label(row, text=value, bg=bg, fg=_UI["text"], width=6,
                     anchor="e", font=mono).pack(side="left")
            tk.Label(row, text=" " + unit, bg=bg, width=6, anchor="w",
                     fg=_UI["faint"], font=unit_font).pack(side="left")
        return legend

    def _build_delay_control(self, parent, value, bg, size, indent) -> None:
        """The playback slider, live for the whole run, under its label.

        Nothing has to be restarted or rearmed: the panel's timer reads
        ``Parameters.simulation_speed`` each time it schedules the next frame,
        so a new value is picked up on the following tick.  While paused the
        timer is still turning over, so a change made then takes effect the
        moment the run resumes.

        The same ``_SpeedSlider`` the start screen has (12 Oct 2026; it was
        a typed spinner over a 1-2-5 ladder).  A run started at a
        milliseconds the slider does not offer keeps it: the slider only
        writes its variable when the handle is moved, and the readout
        shows the run's own value until then.
        """
        self._delay_var = tk.StringVar(value=value)

        def apply(*_args):
            try:
                wanted = int(float(self._delay_var.get()))
            except (TypeError, ValueError):
                return
            Parameters.simulation_speed = max(1, min(60000, wanted))

        self._delay_var.trace_add("write", apply)
        # Kept as an attribute so the commit path can be exercised directly.
        self._commit_delay = apply
        _SpeedSlider(parent, self._delay_var,
                     font=("Segoe UI Semibold", size),
                     small=("Segoe UI", max(8, size - 1)), length=120,
                     background=bg).pack(anchor="w", padx=(indent, 0),
                                         pady=(0, 2))

    def _fit_legend(self) -> None:
        """Build the legend at the largest type whose whole height stands
        beside the picture.  Measured, not guessed: a maximised window is
        the desktop less its taskbar, and the panel does not scroll.
        Called once the window is laid out, and again when its height
        changes."""
        if self.panel is None or self._top_bar is None:
            return
        self.root.update_idletasks()
        room = (self.container.winfo_height()
                - self._top_bar.winfo_reqheight()
                - sum(bar.winfo_reqheight() for bar in self._bottom_bars))
        if room < 100:
            room = 100000               # not laid out yet: the largest
        if self._legend is not None and room == self._legend_room:
            return
        old = self._legend
        for size in self.LEGEND_SIZES:
            legend = self._build_legend(self.container, size)
            legend.update_idletasks()
            if (legend.winfo_reqheight() <= room
                    or size == self.LEGEND_SIZES[-1]):
                break
            legend.destroy()
        outer = legend
        if legend.winfo_reqheight() > room:
            legend.destroy()
            outer = self._scrolled(size, room)
        if old is not None:
            outer.pack(side="right", fill="y", before=old)
            old.destroy()
        else:
            outer.pack(side="right", fill="y")
        self._legend, self._legend_room, self._legend_size = outer, room, size
        if self.panel is not None:
            self.update_legend_counts(self.panel.vehicle_list)

    def _scrolled(self, size, room):
        """The legend in a scrolling canvas, for a window shorter than
        even its smallest type: the panel then scrolls rather than
        losing its last rows.  Built again as the canvas's own child: a
        window item whose widget is a child of an ancestor stacks under
        the canvas and never shows."""
        wrap = tk.Frame(self.container, bg=_UI["panel"])
        canvas = tk.Canvas(wrap, bg=_UI["panel"], highlightthickness=0, bd=0,
                           height=room)
        legend = self._build_legend(canvas, size)
        legend.update_idletasks()
        canvas.configure(width=legend.winfo_reqwidth())
        bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                           background=_UI["seg_off"],
                           activebackground=_UI["seg_hover"],
                           troughcolor=_UI["ground"], borderwidth=0,
                           highlightthickness=0, elementborderwidth=0,
                           width=12)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_window(0, 0, window=legend, anchor="nw")
        canvas.configure(scrollregion=(0, 0, legend.winfo_reqwidth(),
                                       legend.winfo_reqheight()))

        def wheel(widget):
            widget.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
                -1 if e.delta > 0 else 1, "units"))
            for child in widget.winfo_children():
                wheel(child)

        wheel(legend)
        return wrap

    def _on_container_resize(self, _event=None) -> None:
        """The window changed size: the legend may now have more or less
        height to stand in.  After idle, so one resize is one rebuild."""
        if self.panel is None or self._legend is None:
            return
        if self._legend_job is None:
            self._legend_job = self.root.after_idle(self._refit_legend)

    def _refit_legend(self) -> None:
        self._legend_job = None
        self._fit_legend()

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

    def _fill_basemap_box(self) -> None:
        """The legend's imagery controls, for the run as it stands.

        With imagery loaded: the Background map checkbox.  Without it, and
        with the network georeferenced (every import is), a button that
        fetches the map now -- the imagery files are gitignored, so a
        checkout elsewhere gets every import without its map, and a tile
        fetch that failed at import time only warned.  Only offered where
        the network can actually be placed, so the control never promises
        something it cannot show.
        """
        box = self._basemap_box
        if box is None:
            return
        for child in box.winfo_children():
            child.destroy()
        bg = box.cget("background")
        panel = self.panel
        if panel is None:
            return
        if panel._basemap is not None:
            self._basemap_var = tk.BooleanVar(value=panel.show_basemap)

            def flip():
                self.panel.toggle_basemap()
                self._basemap_var.set(self.panel.show_basemap)
                self.panel.repaint()

            # "Background map", not "Satellite map": every network ships with
            # the OpenStreetMap street rendering now, and only says satellite
            # if someone re-fetches with --provider esri.
            # No sentence under it (owner, 12 Oct 2026): the toast on a
            # toggle says that the zoom snaps while the map is on.
            tk.Checkbutton(box, text="Background map  (M)", bg=bg,
                           fg=_UI["text"], font=("Segoe UI", 10), anchor="w",
                           variable=self._basemap_var, command=flip,
                           cursor="hand2", activebackground=bg,
                           activeforeground=_UI["text"],
                           selectcolor=_UI["seg_off"],
                           highlightthickness=0).pack(side="left")
            return
        if basemap_module.read_centre(Parameters.NETWORK_DIR) is None:
            return                    # nowhere on Earth to fetch for
        self._basemap_var = None
        self._fetch_map_btn = self._dark_button(
            box, "Fetch background map", self._fetch_basemap, size=9)
        self._fetch_map_btn.pack(side="left")
        panel.toast("No map on disk for this network -- Fetch background "
                    "map takes a minute and needs the internet")

    def _fetch_basemap(self) -> None:
        """Fetch the imagery for the running network, then show it.

        The script runs in a thread (it is a subprocess, like every step
        of an import); the thread never touches Tk, and the result is
        collected by an ``after`` poll on the Tk side.
        """
        if self.panel is None or getattr(self, "_fetching_map", False):
            return
        network = Parameters.NETWORK_DIR
        self._fetching_map = True
        self._fetch_map_btn.configure(state="disabled",
                                      text="Fetching the map...")
        self.panel.toast("Downloading tiles from OpenStreetMap...")
        result = {}

        def work():
            lines = []
            try:
                ok = map_import.fetch_basemap(network, log=lines.append)
            except Exception as exc:          # a missing script, say
                ok, lines = False, [str(exc)]
            result["ok"], result["lines"] = ok, lines

        threading.Thread(target=work, daemon=True).start()

        def poll():
            if self.panel is None:
                self._fetching_map = False
                return                    # the run was torn down meanwhile
            if "ok" not in result:
                self.root.after(300, poll)
                return
            self._fetching_map = False
            panel = self.panel
            if result["ok"]:
                panel._basemap = basemap_module.load_for_current_network(
                    panel.link_list, panel.node_list)
            if panel._basemap is None:
                tail = result["lines"][-1] if result["lines"] else ""
                self._fetch_map_btn.configure(state="normal",
                                              text="Fetch background map")
                panel.toast("The fetch did not succeed" + (f": {tail}" if tail
                                                           else "") +
                            " -- try again later, or run fetch_basemap.py "
                            f"--network {network}")
                return
            # Imagery landed: refill the box with the checkbox, and re-snap
            # the zoom to a scale the picture can be drawn at exactly.
            self._fill_basemap_box()
            panel.set_scale(panel.scale)
            self.sync_zoom_slider(panel.scale)
            panel.repaint()

        self.root.after(300, poll)

    def show_help(self) -> None:
        """Open the help page, or front the one already open."""
        help_window = self._help
        if help_window is not None and help_window.top.winfo_exists():
            help_window.top.lift()
            help_window.top.focus_set()
            return
        self._help = _HelpWindow(self.root)

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
        if self.option_panel is not None:
            self.option_panel.dispose()
        self.option_panel = None
        self._report_button = None
        self._view_button = None
        self._pause_button = None
        self._status = None
        # Belong to the toolbar and legend about to be destroyed, and the
        # next run may not have imagery at all.
        self._basemap_var = None
        self._basemap_box = None
        self._fetching_map = False
        self._top_bar = None
        self._bottom_bars = ()
        self._legend = None
        self._legend_room = None
        if self._legend_job is not None:
            try:
                self.root.after_cancel(self._legend_job)
            except tk.TclError:
                pass
            self._legend_job = None
        self.container.unbind("<Configure>")
        self._delay_var = None
        self._commit_delay = None
        self._scale_slider = None
        self._traffic_slider = None
        self._traffic_label = None
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
        _ToolButton(top_bar, text="Help  (F1)", command=self.show_help).pack(
            side="left", padx=(8, 0))
        self.root.bind("<KeyPress-v>", lambda _e: self.toggle_view())
        self.root.bind("<KeyPress-space>", lambda _e: self.toggle_pause())
        self.root.bind("<KeyPress-m>", lambda _e: self.toggle_basemap())
        self._build_view_controls(top_bar)

        # After the controls, taking whatever width the bar has left:
        # ``width=1`` with fill, so on a narrow window the sentence is
        # clipped at its end ("Step 61 of 480 ...") instead of the
        # controls losing their places -- and the step is the part that
        # matters.
        self._status = tk.Label(top_bar, text="", background=_UI["band"],
                                foreground=_UI["accent_text"],
                                font=("Segoe UI", 10), anchor="w", width=1)
        self._status.pack(side="left", fill="x", expand=True, padx=(12, 4))
        top_bar.pack(side="top", fill="x")
        _glass(top_bar)
        self._top_bar = top_bar

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

        # The legend at the largest type that stands in the window's
        # height; it is packed before the bottom bars, so a refit packs its
        # successor ``before=`` it and keeps the order.
        self._bottom_bars = ((show_progress_slider.widget,
                              change_trace_slider.widget)
                             if Parameters.TRACE_MODE
                             else (show_progress_slider.widget,))
        self._fit_legend()
        show_progress_slider.widget.pack(side="bottom", fill="x")
        if Parameters.TRACE_MODE:
            change_trace_slider.widget.pack(side="top", fill="x")
        panel.canvas.pack(side="left", fill="both", expand=True)
        self.container.bind("<Configure>", self._on_container_resize)
        self.root.update_idletasks()
        # Open on the whole network.  A surveyed junction lands around a
        # 100 m scale bar; the BUET-DU-DMC demo lands on its full extent.
        # Starting from a fixed close zoom made every first act of a run a
        # hunt for where the traffic was.
        panel.home_scale = panel.fit_scale(panel.canvas.winfo_width(),
                                           panel.canvas.winfo_height())
        self.sync_zoom_slider(panel.home_scale)
        panel.set_scale(panel.home_scale)
        self.update_status()
        panel.toast("Running -- Space pauses, V tilts into 3D, M hides the "
                    "map, the wheel zooms")
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
        self._pause_button.configure(text="\u25b6  Play  (Space)" if paused
                                     else "\u23f8  Pause  (Space)")
        self.update_status()

    def update_status(self) -> None:
        """The toolbar's one line of system status: where the run is, and
        which view is showing.  Cheap enough to refresh every step."""
        if self._status is None or self.panel is None:
            return
        panel = self.panel
        view = "3D view" if panel.view_3d else "plan view"
        step, end = Parameters.simulation_step, Parameters.simulation_end_time
        if panel._finished and step >= end:
            text = f"Run finished at {end} s \u00b7 {view}"
        elif panel.paused:
            text = f"Paused at step {step} of {end} \u00b7 {view}"
        else:
            text = f"Step {step} of {end} \u00b7 {view}"
        try:
            self._status.configure(text=text)
        except tk.TclError:
            pass

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
        self.panel.toast("Zoom x%.2g of the whole-network framing"
                         % (self.panel.scale / max(self.panel.home_scale, 1e-9)))

    def _on_traffic_slider(self, value) -> None:
        """The legend's traffic slider: a multiplier on every route's
        arrival rate, applied from each stream's next arrival on."""
        level = int(float(value)) / 100.0
        if abs(level - Parameters.DEMAND_SCALE) < 1e-9:
            return
        Parameters.DEMAND_SCALE = level
        label = getattr(self, "_traffic_label", None)
        if label is not None:
            label.configure(text=f"x{level:.2g}")
        if self.panel is not None:
            self.panel.toast(f"Traffic x{level:.2g} -- every route's arrivals "
                             "from now on; the vehicles already out stay")

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
            text="Plan view  (V)" if self.panel.view_3d else "3D view  (V)")

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
