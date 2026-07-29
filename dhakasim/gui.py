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

    def draw_line(self, x1, y1, x2, y2) -> None:
        self.canvas.create_line(self._tx(x1), self._ty(y1), self._tx(x2), self._ty(y2),
                                fill=self._color.to_hex(),
                                width=max(1, self._stroke * self.scale))

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

    def draw_string(self, text, x, y) -> None:
        size = max(1, int(self._font[1] * self.scale))
        self.canvas.create_text(self._tx(x), self._ty(y), text=text, anchor="sw",
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
        self.canvas.bind("<ButtonPress-1>", self.mouse_pressed)
        self.canvas.bind("<B1-Motion>", self.mouse_dragged)
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

    def start(self) -> None:
        self._timer = self.canvas.after(max(1, Parameters.simulation_speed),
                                       self._on_timer)

    def set_scale(self, scale: float) -> None:
        self.scale = scale
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
        g2d = self.graphics
        g2d.set_transform(width, height, self.scale, self.translate_x, self.translate_y)
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
                    pedestrian.draw_mobile_pedestrian(
                        self.trace_writer, g2d, Parameters.pixel_per_strip,
                        Parameters.pixel_per_meter, Parameters.pixel_per_footpath_strip)
            self.trace_writer.write("Current Vehicles\n")
            for vehicle in self.vehicle_list:
                vehicle.draw_vehicle(self.trace_writer, g2d, Parameters.pixel_per_strip,
                                     Parameters.pixel_per_meter,
                                     Parameters.pixel_per_footpath_strip)

            self.trace_writer.write("Current Objects\n")
            for obj in self.object_list:
                obj.draw_object(self.trace_writer, g2d, Parameters.pixel_per_strip,
                                Parameters.pixel_per_meter,
                                Parameters.pixel_per_footpath_strip)

            self.trace_writer.write("End Step\n")
            self.trace_writer.flush()

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
        """World (metre) point at which to draw a node's label.

        Boundary nodes carry real coordinates; junction nodes are stored at
        (0, 0), so their position is recovered as the mean of the link
        endpoints that meet there.
        """
        n = node.number_of_links()
        if n == 0:
            return node.x, node.y
        sx = sy = 0.0
        for j in range(n):
            link = self.link_list[node.get_link(j)]
            if link.get_up_node() == node.get_id():
                seg = link.get_first_segment()
                sx += seg.get_start_x()
                sy += seg.get_start_y()
            else:
                seg = link.get_last_segment()
                sx += seg.get_end_x()
                sy += seg.get_end_y()
        return sx / n, sy / n

    def draw_node_id(self, g2d, node) -> None:
        # Prefer a friendly name (input/node_names.txt) over the numeric id.
        name = Parameters.NODE_NAMES.get(node.get_id(), str(node.get_id()))
        x_m, y_m = self._node_label_point(node)
        g2d.set_font("Serif", 160)
        g2d.set_color(Color.BLACK)

        # Push the label clear of the carriageway: terminals are pushed
        # outwards along their own road, junctions straight up.
        widest = 0.0
        for j in range(node.number_of_links()):
            link = self.link_list[node.get_link(j)]
            for k in range(link.get_number_of_segments()):
                widest = max(widest, link.get_segment(k).get_segment_width())
        # Junction names need more room: they sit inside the road network,
        # so lift them well clear of the widest carriageway.
        if node.number_of_links() > 1:
            clearance = widest * 1.8 + 12.0
        else:
            clearance = widest * 0.75 + 6.0

        dx, dy = 0.0, -1.0
        if node.number_of_links() == 1:
            link = self.link_list[node.get_link(0)]
            if link.get_up_node() == node.get_id():
                seg = link.get_first_segment()
                ox, oy = seg.get_end_x(), seg.get_end_y()
            else:
                seg = link.get_last_segment()
                ox, oy = seg.get_start_x(), seg.get_start_y()
            vx, vy = x_m - ox, y_m - oy          # points away from the junction
            length = math.hypot(vx, vy)
            if length > 0:
                dx, dy = vx / length, vy / length

        lx = (x_m + dx * clearance) * Parameters.pixel_per_meter
        ly = (y_m + dy * clearance) * Parameters.pixel_per_meter
        g2d.draw_string(name, jint(lx), jint(ly))

    @staticmethod
    def _convex_hull(points):
        """Monotone-chain convex hull of ``(x, y)`` points."""
        pts = sorted(set(points))
        if len(pts) <= 2:
            return pts

        def cross(o, a, b):
            return ((a[0] - o[0]) * (b[1] - o[1])
                    - (a[1] - o[1]) * (b[0] - o[0]))

        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        return lower[:-1] + upper[:-1]

    def _segment_quads(self):
        """The four corner points of every road segment, in pixel space."""
        ppm = Parameters.pixel_per_meter
        quads = []
        for link in self.link_list:
            for j in range(link.get_number_of_segments()):
                seg = link.get_segment(j)
                x1 = seg.get_start_x() * ppm
                y1 = seg.get_start_y() * ppm
                x2 = seg.get_end_x() * ppm
                y2 = seg.get_end_y() * ppm
                w = seg.get_segment_width() * ppm
                x3 = Utilities.return_x3(x1, y1, x2, y2, w)
                y3 = Utilities.return_y3(x1, y1, x2, y2, w)
                x4 = Utilities.return_x4(x1, y1, x2, y2, w)
                y4 = Utilities.return_y4(x1, y1, x2, y2, w)
                quads.append(([x1, x2, x4, x3], [y1, y2, y4, y3]))
        return quads

    def _junction_hulls(self):
        """A filled convex patch for every junction (node with >1 link).

        The patch is built from each incident link's two kerb corners at the
        node *and* the same corners carried a short way along the link, so the
        patch overlaps the road surfaces and leaves no notch at the mouth.
        """
        ppm = Parameters.pixel_per_meter
        hulls = []
        for node in self.node_list:
            if node.number_of_links() < 2:
                continue
            pts = []
            widths = []
            for j in range(node.number_of_links()):
                link = self.link_list[node.get_link(j)]
                if link.get_up_node() == node.get_id():
                    seg = link.get_first_segment()
                    ax, ay = seg.get_start_x(), seg.get_start_y()
                    bx, by = seg.get_end_x(), seg.get_end_y()
                else:
                    seg = link.get_last_segment()
                    ax, ay = seg.get_end_x(), seg.get_end_y()
                    bx, by = seg.get_start_x(), seg.get_start_y()
                widths.append(seg.get_segment_width())
                x1, y1, x3, y3 = self._link_end_at_node(link, node, ppm)
                pts.append((x1, y1))
                pts.append((x3, y3))
                # unit vector pointing from the node into the link
                dx, dy = bx - ax, by - ay
                length = math.hypot(dx, dy)
                if length > 0:
                    reach = seg.get_segment_width() * ppm
                    ux, uy = dx / length * reach, dy / length * reach
                    pts.append((x1 + ux, y1 + uy))
                    pts.append((x3 + ux, y3 + uy))
            hull = self._convex_hull(pts)
            if len(hull) >= 3:
                # A disc at the junction centre rounds off any concave mouth
                # the convex hull cannot reach.
                cx = sum(p[0] for p in hull) / len(hull)
                cy = sum(p[1] for p in hull) / len(hull)
                radius = max(widths) * ppm * 0.5
                hulls.append(([p[0] for p in hull], [p[1] for p in hull],
                              cx, cy, radius))
        return hulls

    def draw_road_network(self, g2d) -> None:
        # Cache the static geometry: it depends only on the network and
        # pixelPerMeter, and rebuilding it every frame is wasteful.
        if self._road_geometry is None:
            self._road_geometry = (self._segment_quads(), self._junction_hulls())
        quads, hulls = self._road_geometry

        # 1. road surfaces, 2. kerb outlines, 3. junction patches painted last
        # so they cover the kerb stubs and give a smooth intersection.
        g2d.set_color(Constants.road_fill_color)
        for xs, ys in quads:
            g2d.fill_polygon(xs, ys, 4)

        g2d.set_color(Constants.road_border_color)
        for link in self.link_list:
            link.draw(g2d)

        g2d.set_color(Constants.road_fill_color)
        for xs, ys, cx, cy, radius in hulls:
            g2d.fill_polygon(xs, ys, len(xs))
            g2d.fill_oval(cx - radius, cy - radius, radius * 2, radius * 2)

        for node in self.node_list:
            g2d.set_color(Color.BLACK)
            self.draw_node_id(g2d, node)


    @staticmethod
    def _link_end_at_node(link, node, pixel_per_meter):
        """The two outer corner points of *link* where it meets *node*."""
        if link.get_up_node() == node.get_id():
            segment = link.get_first_segment()
            x1 = segment.get_start_x() * pixel_per_meter
            y1 = segment.get_start_y() * pixel_per_meter
            x2 = segment.get_end_x() * pixel_per_meter
            y2 = segment.get_end_y() * pixel_per_meter
            w = segment.get_segment_width() * pixel_per_meter
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            return x1, y1, x3, y3
        segment = link.get_last_segment()
        x1 = segment.get_start_x() * pixel_per_meter
        y1 = segment.get_start_y() * pixel_per_meter
        x2 = segment.get_end_x() * pixel_per_meter
        y2 = segment.get_end_y() * pixel_per_meter
        w = segment.get_segment_width() * pixel_per_meter
        x4 = Utilities.return_x4(x1, y1, x2, y2, w)
        y4 = Utilities.return_y4(x1, y1, x2, y2, w)
        return x2, y2, x4, y4

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

    def mouse_pressed(self, event) -> None:
        self._reference_x = event.x
        self._reference_y = event.y

    def mouse_dragged(self, event) -> None:
        self.translate_x += (event.x - self._reference_x) * 30
        self.translate_y += (event.y - self._reference_y) * 30
        self._reference_x = event.x
        self._reference_y = event.y
        if not Parameters.TRACE_MODE:
            self.repaint()
        if Parameters.DEBUG_MODE:
            print(f"{self.translate_x} {self.translate_y}")

    def mouse_wheel_moved(self, event) -> None:
        notches = -1 if event.delta > 0 else 1
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
            ("Maximum Speed (km/h):", "max_speed", "60",
             "Network speed limit, in KILOMETRES PER HOUR."),
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
            row=14, column=0, sticky="w", pady=4)
        self.trace_var = tk.StringVar(
            value="On" if Parameters.TRACE_MODE else "Off")
        trace_radios = ttk.Frame(frame)
        trace_radios.grid(row=14, column=1, sticky="w")
        ttk.Radiobutton(trace_radios, text="On", value="On",
                        variable=self.trace_var).pack(side="left")
        ttk.Radiobutton(trace_radios, text="Off", value="Off",
                        variable=self.trace_var).pack(side="left")
        ttk.Label(frame, text="On = replay a previously recorded run from "
                             "trace.txt instead of simulating a fresh one.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=14, column=2, sticky="w", padx=(16, 0), pady=4)

        ttk.Label(frame, text="Pedestrian:", font=("Consolas", 10)).grid(
            row=14, column=0, sticky="w", pady=4)
        self.pedestrian_var = tk.StringVar(
            value="On" if Parameters.across_pedestrian_mode else "Off")
        radios = ttk.Frame(frame)
        radios.grid(row=14, column=1, sticky="w")
        ttk.Radiobutton(radios, text="On", value="On",
                        variable=self.pedestrian_var).pack(side="left")
        ttk.Radiobutton(radios, text="Off", value="Off",
                        variable=self.pedestrian_var).pack(side="left")
        ttk.Label(frame, text="On = generate road-crossing pedestrians, a major "
                             "source of congestion in Dhaka.",
                  font=("Segoe UI", 9), foreground="#5b6b7b",
                  wraplength=430, justify="left").grid(
            row=14, column=2, sticky="w", padx=(16, 0), pady=4)

        start = ttk.Button(frame, text="Start Simulation",
                           command=self.start_simulation)
        start.grid(row=14, column=0, columnspan=3, pady=(22, 0))
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
        self.option_panel = OptionPanel(self, self.container)
        self.option_panel.frame.pack(fill="both", expand=True)
        self.panel = None

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

    def show_simulation(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()

        panel = DhakaSimPanel(self, self.container)
        self.panel = panel

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

    def repaint(self) -> None:
        if self.panel is not None:
            self.panel.repaint()

    def on_simulation_finished(self) -> None:
        # Java swaps in an inert slider once the run ends; just stop repainting.
        # In addition, offer the auto-generated HTML report to the user.
        try:
            from . import report
            path = report.LAST_REPORT_PATH
            if path:
                if messagebox.askyesno(
                        "Simulation finished",
                        "The run has finished.\n\nA report explaining every "
                        "result and term has been saved to:\n"
                        f"{path}\n\nOpen it now?"):
                    webbrowser.open(f"file://{path}")
        except Exception as exc:  # never let the popup break shutdown
            print(f"could not open report: {exc}")

    def run(self) -> None:
        self.root.mainloop()
