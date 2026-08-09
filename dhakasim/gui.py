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
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

from .constants import Constants
from .javacompat import Color, JavaRandom, jbool, jbool_str, jint, jround, jstr
from .parameters import Parameters
from .processor import Processor
from . import render3d
from . import road_geometry
from . import utilities as Utilities


class CanvasGraphics:
    """The ``Graphics2D`` surface used by the drawing methods."""

    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        self._color = Color.BLACK
        self._stroke = 1
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
        self.canvas.create_polygon(points, fill=self._color.to_hex(), outline="")

    def fill_oval(self, x, y, w, h) -> None:
        x1 = self._tx(x)
        y1 = self._ty(y)
        self.canvas.create_oval(x1, y1, x1 + w * self.scale, y1 + h * self.scale,
                                fill=self._color.to_hex(), outline="")

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


class ProgressSlider:
    """Stands in for the Swing ``JSlider`` the drawing code pokes at."""

    def __init__(self, parent, minimum, maximum, value):
        self.var = tk.DoubleVar(value=value)
        self.widget = ttk.Scale(parent, from_=minimum, to=max(maximum, minimum + 1),
                                orient="horizontal", variable=self.var)

    def set_value(self, value) -> None:
        try:
            self.var.set(value)
        except tk.TclError:
            pass

    def get_value(self):
        return int(self.var.get())


class DhakaSimPanel:
    """Port of ``thesisfinal.DhakaSimPanel``."""

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
        self._timer = None
        self._finished = False

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

        # Frame the whole network for the 3D camera, and remember it as the
        # view a double-click goes back to.
        extent = render3d.network_extent(self.link_list, Parameters.pixel_per_meter)
        self.scene3d.set_ground_extent(*extent)
        self.scene3d.camera.frame(*extent)

    def start(self) -> None:
        self._timer = self.canvas.after(max(1, Parameters.simulation_speed),
                                       self._on_timer)

    def set_scale(self, scale: float) -> None:
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
        canvas.delete("all")
        width = canvas.winfo_width() or 1
        height = canvas.winfo_height() or 1
        if self.view_3d:
            g2d = self.scene3d
            g2d.begin_frame(width, height, Parameters.pixel_per_meter)
        else:
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

        if self.draw_roads:
            self.draw_road_network(g2d)

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
        """
        canvas = self.canvas
        ink, paper = "#12263a", "#ffffff"

        # --- north arrow, top right ---
        cx, cy = width - 46, 46
        bearing = -self.scene3d.camera.yaw if self.view_3d else 0.0
        cos_b, sin_b = math.cos(bearing), math.sin(bearing)

        def needle(x, y):
            """Rotate an offset from the dial centre by the camera bearing."""
            return cx + x * cos_b - y * sin_b, cy + x * sin_b + y * cos_b

        canvas.create_oval(cx - 26, cy - 26, cx + 26, cy + 26,
                           fill=paper, outline="#c7ccd3")
        canvas.create_polygon(*needle(0, -19), *needle(-8, 11), *needle(0, 5),
                              *needle(8, 11), fill=ink, outline="")
        canvas.create_text(*needle(0, 17), text="N", fill=ink,
                           font=("Segoe UI", 10, "bold"))

        if self.view_3d:
            # A scale bar means nothing under perspective -- the metres a
            # pixel covers change from the top of the frame to the bottom --
            # so the corner carries the controls instead.
            canvas.create_text(
                16, height - 16, anchor="sw", fill="#33465c",
                font=("Segoe UI", 9),
                text="3D view — drag to orbit · right-drag or Shift+drag to "
                     "pan · wheel to zoom · double-click to reset")
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
                                fill=paper, outline="#c7ccd3")
        canvas.create_line(x0, y0, x0 + bar, y0, fill=ink, width=3)
        for x in (x0, x0 + bar):
            canvas.create_line(x, y0 - 6, x, y0 + 4, fill=ink, width=2)
        label = f"{nice} m" if nice < 1000 else f"{nice // 1000} km"
        canvas.create_text(x0 + bar / 2, y0 - 13, text=label, fill=ink,
                           font=("Segoe UI", 10, "bold"))

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
            clearance = widest * 1.6 + 16.0
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
            # midpoint of the largest angular gap between consecutive arms
            best, best_gap = 0.0, -1.0
            bearings.sort()
            for k in range(len(bearings)):
                a = bearings[k]
                b = bearings[(k + 1) % len(bearings)]
                gap = (b - a) % (2 * math.pi)
                if gap > best_gap:
                    best_gap, best = gap, (a + gap / 2.0) % (2 * math.pi)
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

        # Halo: the same text in white just behind the label, so names stay
        # legible over the carriageway and over each other on tight networks.
        halo = max(1.0, 1.2 / max(self.scale, 0.0001))
        g2d.set_color(Color.WHITE)
        for ox, oy in ((-halo, 0), (halo, 0), (0, -halo), (0, halo)):
            g2d.draw_string(name, jint(lx + ox), jint(ly + oy), anchor)
        g2d.set_color(Color.BLACK)
        g2d.draw_string(name, jint(lx), jint(ly), anchor)

    def draw_road_network(self, g2d) -> None:
        # The road surface is painted by road_geometry.paint, which the report's
        # animation also calls, so the two pictures cannot diverge.  Cache the
        # geometry: it depends only on the network and pixelPerMeter, and
        # rebuilding it every frame is wasteful.
        if self._road_geometry is None:
            self._road_geometry = road_geometry.build(
                self.link_list, self.node_list, Parameters.pixel_per_meter)
        road_geometry.paint(g2d, self.link_list, self.node_list,
                            Parameters.pixel_per_meter, self._road_geometry)

        for node in self.node_list:
            g2d.set_color(Color.BLACK)
            self.draw_node_id(g2d, node)

    # ---- timer / events --------------------------------------------------

    def _on_timer(self) -> None:
        try:
            self.action_performed()
        finally:
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

    def mouse_wheel_moved(self, event) -> None:
        notches = -1 if event.delta > 0 else 1
        if self.view_3d:
            self.scene3d.camera.zoom(notches)
            self.repaint()
            return
        new_scale_value = self.scale - notches * 0.004
        self.scale = min(1.0, max(0.001, new_scale_value))
        self.repaint()


class OptionPanel:
    """Port of ``thesisfinal.OptionPanel``."""

    def __init__(self, dhaka_sim_frame, parent):
        self.dhaka_sim_frame = dhaka_sim_frame
        self.frame = ttk.Frame(parent, padding=24)
        self._init_components()

    def _init_components(self) -> None:
        frame = self.frame
        ttk.Label(frame, text="DhakaSim", font=("Segoe UI", 32)).grid(
            row=0, column=0, columnspan=3, pady=(30, 2))
        ttk.Label(frame, text="Set up the run — each setting is explained on the "
                             "right. A report opens automatically when the run ends.",
                  font=("Segoe UI", 10), foreground="#5b6b7b").grid(
            row=1, column=0, columnspan=3, pady=(0, 18))

        # --- intersection / network selector -----------------------------
        networks = Processor.available_networks()
        ttk.Label(frame, text="Intersection:", font=("Consolas", 10)).grid(
            row=2, column=0, sticky="w", padx=(0, 18), pady=4)
        default = (Parameters.NETWORK_DIR if Parameters.NETWORK_DIR in networks
                   else (networks[0] if networks else ""))
        self.network_var = tk.StringVar(value=default)
        if networks:
            selector = ttk.Combobox(frame, textvariable=self.network_var,
                                    values=networks, state="readonly", width=24,
                                    font=("Consolas", 10))
        else:
            selector = ttk.Entry(frame, textvariable=self.network_var, width=24,
                                 font=("Consolas", 10))
        selector.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="Which intersection/network to simulate. Each "
                             "option is a folder under input/ with its own "
                             "roads, demand and vehicle mix.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=2, column=2, sticky="w", padx=(16, 0), pady=4)

        # --- time of day -------------------------------------------------
        ttk.Label(frame, text="Time of Day:", font=("Consolas", 10)).grid(
            row=3, column=0, sticky="w", padx=(0, 18), pady=4)
        self.TIME_CHOICES = ["Peak hour (busiest)"] + [
            f"{h:02d}:00 - {(h + 1) % 24:02d}:00" for h in range(24)]
        current = Parameters.TIME_OF_DAY
        self.time_var = tk.StringVar(
            value=self.TIME_CHOICES[current + 1] if 0 <= current <= 23
            else self.TIME_CHOICES[0])
        ttk.Combobox(frame, textvariable=self.time_var, values=self.TIME_CHOICES,
                     state="readonly", width=24, font=("Consolas", 10)).grid(
            row=3, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="Which hour of the surveyed day to simulate. The "
                             "counts cover a full 24 hours, so traffic is much "
                             "lighter at night than at the peak.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=3, column=2, sticky="w", padx=(16, 0), pady=4)

        self.fields = {}
        # (label with unit, key, initial value, plain-language explanation)
        rows = (
            ("Random Seed:", "seed", str(Parameters.seed),
             "Fixes randomness so a run can be repeated. Same number → "
             "identical run; -1 = a new random run each time."),
            ("Simulation End Time (seconds):", "end_time",
             str(Parameters.simulation_end_time),
             "How long to simulate, in SECONDS of traffic (1800 = 30 minutes)."),
            ("Simulation Speed (ms/frame):", "speed",
             str(Parameters.simulation_speed),
             "Animation delay in MILLISECONDS. Lower = faster playback; does "
             "not affect the results."),
            ("Pixel Per Meter:", "ppm", jstr(float(Parameters.pixel_per_meter)),
             "Display zoom only: screen pixels drawn per metre of road."),
            ("Probability of Accident:", "accident",
             jstr(Parameters.encounter_per_accident),
             "Accident-frequency control (encounters per accident). A higher "
             "value means accidents happen less often."),
            ("Strip Width (metres):", "strip", jstr(Parameters.strip_width),
             "Width of each lateral 'strip' in METRES. A vehicle occupies "
             "several strips and may move to any free one — this is how "
             "non-lane traffic is modelled."),
            ("Footpath Strip Width (metres):", "footpath",
             jstr(Parameters.footpath_strip_width),
             "Strip granularity on the footpath, in METRES."),
            # Speeds are held in m/s; this field, like MaximumSpeed in
            # parameter.txt, is km/h.  Rounding to 2 dp undoes the 4-digit
            # truncation precision2 applied on the way in, so the value shown
            # is the value the file states and start_simulation converts it
            # back to exactly the same m/s -- the round trip is an identity.
            ("Maximum Speed (km/h):", "max_speed",
             jstr(round(Parameters.maximum_speed * 3.6, 2)),
             "Network speed limit, in KILOMETRES PER HOUR, as set by "
             "MaximumSpeed in parameter.txt. The fastest vehicle type manages "
             "110 km/h, so anything above that is no limit at all."),
        )
        for i, (label, key, value, desc) in enumerate(rows, start=4):
            ttk.Label(frame, text=label, font=("Consolas", 10)).grid(
                row=i, column=0, sticky="w", padx=(0, 18), pady=4)
            var = tk.StringVar(value=value)
            entry = ttk.Entry(frame, textvariable=var, width=16, font=("Consolas", 10))
            entry.grid(row=i, column=1, sticky="w", pady=4)
            ttk.Label(frame, text=desc, font=("Segoe UI", 9), foreground="#5b6b7b",
                      wraplength=430, justify="left").grid(
                row=i, column=2, sticky="w", padx=(16, 0), pady=4)
            self.fields[key] = var

        ttk.Label(frame, text="Trace Mode:", font=("Consolas", 10)).grid(
            row=12, column=0, sticky="w", pady=4)
        self.trace_var = tk.StringVar(
            value="On" if Parameters.TRACE_MODE else "Off")
        trace_radios = ttk.Frame(frame)
        trace_radios.grid(row=12, column=1, sticky="w")
        ttk.Radiobutton(trace_radios, text="On", value="On",
                        variable=self.trace_var).pack(side="left")
        ttk.Radiobutton(trace_radios, text="Off", value="Off",
                        variable=self.trace_var).pack(side="left")
        ttk.Label(frame, text="On = replay a previously recorded run from "
                             "trace.txt instead of simulating a fresh one.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=12, column=2, sticky="w", padx=(16, 0), pady=4)

        ttk.Label(frame, text="Pedestrian:", font=("Consolas", 10)).grid(
            row=13, column=0, sticky="w", pady=4)
        self.pedestrian_var = tk.StringVar(
            value="On" if Parameters.across_pedestrian_mode else "Off")
        radios = ttk.Frame(frame)
        radios.grid(row=13, column=1, sticky="w")
        ttk.Radiobutton(radios, text="On", value="On",
                        variable=self.pedestrian_var).pack(side="left")
        ttk.Radiobutton(radios, text="Off", value="Off",
                        variable=self.pedestrian_var).pack(side="left")
        ttk.Label(frame, text="On = generate road-crossing pedestrians, a major "
                             "source of congestion in Dhaka.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=13, column=2, sticky="w", padx=(16, 0), pady=4)

        ttk.Label(frame, text="Real Geometry:", font=("Consolas", 10)).grid(
            row=14, column=0, sticky="w", pady=4)
        self.geometry_var = tk.StringVar(
            value="On" if Parameters.GEOMETRY_MODE else "Off")
        geom_radios = ttk.Frame(frame)
        geom_radios.grid(row=14, column=1, sticky="w")
        ttk.Radiobutton(geom_radios, text="On", value="On",
                        variable=self.geometry_var).pack(side="left")
        ttk.Radiobutton(geom_radios, text="Off", value="Off",
                        variable=self.geometry_var).pack(side="left")
        ttk.Label(frame, text="On = use the surveyed road layout: physical "
                             "medians, and roundabouts with a central island, "
                             "give-way priority and deflection. Off reproduces "
                             "the original simulator exactly.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=14, column=2, sticky="w", padx=(16, 0), pady=4)

        ttk.Label(frame, text="3D View:", font=("Consolas", 10)).grid(
            row=15, column=0, sticky="w", pady=4)
        self.render3d_var = tk.StringVar(
            value="On" if Parameters.RENDER_3D else "Off")
        view_radios = ttk.Frame(frame)
        view_radios.grid(row=15, column=1, sticky="w")
        ttk.Radiobutton(view_radios, text="On", value="On",
                        variable=self.render3d_var).pack(side="left")
        ttk.Radiobutton(view_radios, text="Off", value="Off",
                        variable=self.render3d_var).pack(side="left")
        ttk.Label(frame, text="On = watch the run as a 3D perspective scene "
                             "with modelled vehicles, as VISSIM does; Off = "
                             "the 2D plan view. Drawing only — the results are "
                             "the same either way, and the button in the "
                             "toolbar (or the V key) switches at any time.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=15, column=2, sticky="w", padx=(16, 0), pady=4)

        start = ttk.Button(frame, text="Start Simulation",
                           command=self.start_simulation)
        start.grid(row=16, column=0, columnspan=3, pady=(22, 0))
        start.focus_set()
        parent_toplevel = frame.winfo_toplevel()
        parent_toplevel.bind("<Return>", lambda _e: self.start_simulation())

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
        Parameters.GEOMETRY_MODE = self.geometry_var.get() == "On"
        print("Real geometry: " + ("On" if Parameters.GEOMETRY_MODE else "Off"))
        Parameters.RENDER_3D = self.render3d_var.get() == "On"
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
        Parameters.pixel_per_footpath_strip = (Parameters.pixel_per_meter
                                              * Parameters.footpath_strip_width)
        Parameters.pixel_per_strip = Parameters.pixel_per_meter * Parameters.strip_width

        self.dhaka_sim_frame.show_simulation()


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
        self.container = ttk.Frame(self.root)
        self.container.pack(fill="both", expand=True)
        # The configuration as loaded from parameter.txt, before any run has
        # had a chance to mutate it.  show_options() puts this back, so the
        # form always opens on the same values it opened on the first time.
        self._baseline = Parameters.snapshot()
        self.option_panel = None
        self.panel = None
        self._report_button = None
        self._view_button = None
        self._status = None
        self.show_options()

    # Legend rows: label -> the vehicle type indices it covers.
    LEGEND_CATEGORIES = (
        ("Bicycle", (0,)), ("Rickshaw", (1,)), ("Van / Cart", (2,)),
        ("Motorbike", (3,)), ("Car", (4, 5, 6)), ("CNG / Auto", (7,)),
        ("Bus", (8, 9)), ("Truck", (10, 11)), ("Pedestrian", (12,)),
    )

    def _build_legend(self, parent):
        """A collapsible panel: colour key plus a live count per type."""
        bg = "#f2f4f7"
        legend = tk.Frame(parent, bg=bg, padx=10, pady=8, bd=1, relief="solid")

        header = tk.Frame(legend, bg=bg)
        header.pack(anchor="w", fill="x")
        body = tk.Frame(legend, bg=bg)
        state = {"open": True}

        def toggle():
            state["open"] = not state["open"]
            if state["open"]:
                body.pack(anchor="w", fill="x", pady=(6, 0))
                toggle_btn.configure(text="−")
            else:
                body.forget()
                toggle_btn.configure(text="+")

        tk.Label(header, text="Vehicles on network", font=("Segoe UI", 10, "bold"),
                 bg=bg).pack(side="left")
        toggle_btn = tk.Button(header, text="−", width=2, relief="flat",
                               bg=bg, font=("Segoe UI", 10, "bold"), bd=0,
                               command=toggle, cursor="hand2")
        toggle_btn.pack(side="right")

        self.legend_counts = {}
        for name, idxs in self.LEGEND_CATEGORIES:
            r, g, b = Constants.VEHICLE_TYPE_COLORS[idxs[0]]
            row = tk.Frame(body, bg=bg)
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text="  ", bg="#%02x%02x%02x" % (r, g, b), width=2,
                     relief="solid", borderwidth=1).pack(side="left")
            tk.Label(row, text="  " + name, bg=bg, width=12, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            var = tk.StringVar(value="0")
            tk.Label(row, textvariable=var, bg=bg, width=5, anchor="e",
                     font=("Consolas", 9, "bold")).pack(side="left")
            self.legend_counts[name] = var

        total_row = tk.Frame(body, bg=bg)
        total_row.pack(anchor="w", fill="x", pady=(6, 0))
        tk.Label(total_row, text="Total", bg=bg, width=15, anchor="w",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.legend_total = tk.StringVar(value="0")
        tk.Label(total_row, textvariable=self.legend_total, bg=bg, width=5,
                 anchor="e", font=("Consolas", 9, "bold")).pack(side="left")

        body.pack(anchor="w", fill="x", pady=(6, 0))
        return legend

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
        self._status = None
        Parameters.show_progress_slider = None
        for child in self.container.winfo_children():
            child.destroy()

    def show_simulation(self) -> None:
        self._teardown()

        panel = DhakaSimPanel(self, self.container)
        self.panel = panel

        # A toolbar that stays available for the whole run, so getting back to
        # the setup screen never means restarting the program.
        top_bar = ttk.Frame(self.container, padding=(8, 6))
        ttk.Button(top_bar, text="◀  New simulation",
                   command=self.new_simulation).pack(side="left")
        self._report_button = ttk.Button(top_bar, text="Open report",
                                         command=self.open_report,
                                         state="disabled")
        self._report_button.pack(side="left", padx=(8, 0))

        # The view can be flipped at any point in a run: it changes only how
        # the frame is drawn, never what is simulated.
        self._view_button = ttk.Button(top_bar, command=self.toggle_view)
        self._view_button.pack(side="left", padx=(8, 0))
        self._sync_view_button()
        self.root.bind("<KeyPress-v>", lambda _e: self.toggle_view())

        self._status = ttk.Label(top_bar, text="")
        self._status.pack(side="left", padx=(12, 0))
        top_bar.pack(side="top", fill="x")

        scale_slider = ttk.Scale(self.container, from_=100, to=0, orient="vertical")
        scale_slider.set(30)
        show_progress_slider = ProgressSlider(self.container, 1,
                                             Parameters.simulation_end_time, 1)
        change_trace_slider = ProgressSlider(self.container, 1,
                                            Parameters.simulation_end_time, 1)
        Parameters.show_progress_slider = show_progress_slider

        def on_scale(_value):
            scale_value = max(0.00001, scale_slider.get() / 100.0)
            panel.set_scale(scale_value)
            if Parameters.DEBUG_MODE:
                print("Scale Value: " + str(scale_value))

        scale_slider.configure(command=on_scale)

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

        scale_slider.pack(side="right", fill="y")
        self._build_legend(self.container).pack(side="right", fill="y")
        show_progress_slider.widget.pack(side="bottom", fill="x")
        if Parameters.TRACE_MODE:
            change_trace_slider.widget.pack(side="top", fill="x")
        panel.canvas.pack(side="left", fill="both", expand=True)
        self.root.update_idletasks()
        panel.set_scale(max(0.00001, scale_slider.get() / 100.0))
        panel.start()

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
