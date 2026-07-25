"""Port of ``DhakaSimFrame`` / ``OptionPanel`` / ``DhakaSimPanel`` to tkinter.

Swing's ``Graphics2D`` is replaced by :class:`CanvasGraphics`, a thin adapter
that applies the same affine transform Java set up
(``translate(w/2,h/2) · scale(s) · translate(-w/2,-h/2) · translate(tx,ty)``)
and draws onto a ``tkinter.Canvas``.  Everything the drawing code writes to
``trace.txt`` is produced from untransformed world coordinates, exactly as in
the Java version, so traces stay interchangeable between the two builds.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

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

    @staticmethod
    def draw_node_id(g2d, node) -> None:
        g2d.set_font("Serif", 256)
        g2d.set_color(Color.BLACK)
        g2d.draw_string(str(node.get_id()), jint(node.x * Parameters.pixel_per_meter),
                        jint(node.y * Parameters.pixel_per_meter))

    def draw_road_network(self, g2d) -> None:
        g2d.set_color(Color.WHITE)
        for link in self.link_list:
            link.draw(g2d)
        for node in self.node_list:
            g2d.set_color(Color.BLACK)
            self.draw_node_id(g2d, node)

        # The intersection outline depends only on the (static) network
        # geometry and pixelPerMeter, and the O(n^2) intersection test that
        # filters it is expensive, so compute it once and reuse it.
        if self._road_geometry is None:
            self._road_geometry = self._compute_road_geometry()
        g2d.set_color(Color.BLACK)
        for x1, y1, x2, y2 in self._road_geometry:
            g2d.draw_line(x1, y1, x2, y2)

    def _compute_road_geometry(self):
        pixel_per_meter = Parameters.pixel_per_meter
        line_list = []
        for node in self.node_list:
            for j in range(node.number_of_links()):
                link = self.link_list[node.get_link(j)]
                x1, y1, x3, y3 = self._link_end_at_node(link, node, pixel_per_meter)
                for k in range(node.number_of_links()):
                    if j == k:
                        continue
                    link_prime = self.link_list[node.get_link(k)]
                    x1p, y1p, x3p, y3p = self._link_end_at_node(link_prime, node,
                                                               pixel_per_meter)
                    line_list.append((x1, y1, x1p, y1p))
                    line_list.append((x1, y1, x3p, y3p))
                    line_list.append((x3, y3, x1p, y1p))
                    line_list.append((x3, y3, x3p, y3p))

        result = []
        for i in range(len(line_list)):
            do_intersect = False
            for j in range(len(line_list)):
                if i != j and Utilities.do_intersect(*line_list[i], *line_list[j]):
                    do_intersect = True
                    break
            if not do_intersect:
                a, b, c, d = line_list[i]
                result.append((jint(jround(a)), jint(jround(b)),
                               jint(jround(c)), jint(jround(d))))

        for link in self.link_list:
            segment = link.get_first_segment()
            for j in range(1, link.get_number_of_segments()):
                x1 = segment.get_start_x() * pixel_per_meter
                y1 = segment.get_start_y() * pixel_per_meter
                x2 = segment.get_end_x() * pixel_per_meter
                y2 = segment.get_end_y() * pixel_per_meter
                w = segment.get_segment_width() * pixel_per_meter
                x4 = Utilities.return_x4(x1, y1, x2, y2, w)
                y4 = Utilities.return_y4(x1, y1, x2, y2, w)

                nxt = link.get_segment(j)
                x1p = nxt.get_start_x() * pixel_per_meter
                y1p = nxt.get_start_y() * pixel_per_meter
                x2p = nxt.get_end_x() * pixel_per_meter
                y2p = nxt.get_end_y() * pixel_per_meter
                wp = nxt.get_segment_width() * pixel_per_meter
                x3p = Utilities.return_x3(x1p, y1p, x2p, y2p, wp)
                y3p = Utilities.return_y3(x1p, y1p, x2p, y2p, wp)

                result.append((jint(jround(x2)), jint(jround(y2)),
                               jint(jround(x1p)), jint(jround(y1p))))
                result.append((jint(jround(x4)), jint(jround(y4)),
                               jint(jround(x3p)), jint(jround(y3p))))

                segment = nxt
        return result

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
            row=0, column=0, columnspan=2, pady=(40, 24))

        self.fields = {}
        rows = (
            ("Random Seed:", "seed", str(Parameters.seed)),
            ("Simulation End Time:", "end_time", str(Parameters.simulation_end_time)),
            ("Simulation Speed:", "speed", str(Parameters.simulation_speed)),
            ("Trace Mode:", "trace", jbool_str(Parameters.TRACE_MODE)),
            ("Pixel Per Meter:", "ppm", jstr(float(Parameters.pixel_per_meter))),
            ("Probability of Accident:", "accident",
             jstr(Parameters.encounter_per_accident)),
            ("Strip Width:", "strip", jstr(Parameters.strip_width)),
            ("Footpath Strip Width:", "footpath",
             jstr(Parameters.footpath_strip_width)),
            ("MaximumSpeed:", "max_speed", jstr(Parameters.maximum_speed)),
        )
        for i, (label, key, value) in enumerate(rows, start=1):
            ttk.Label(frame, text=label, font=("Consolas", 10)).grid(
                row=i, column=0, sticky="w", padx=(0, 18), pady=2)
            var = tk.StringVar(value=value)
            entry = ttk.Entry(frame, textvariable=var, width=20, font=("Consolas", 10))
            entry.grid(row=i, column=1, sticky="w", pady=2)
            self.fields[key] = var

        ttk.Label(frame, text="Pedestrian:", font=("Consolas", 10)).grid(
            row=10, column=0, sticky="w", pady=2)
        self.pedestrian_var = tk.StringVar(
            value="On" if Parameters.across_pedestrian_mode else "Off")
        radios = ttk.Frame(frame)
        radios.grid(row=10, column=1, sticky="w")
        ttk.Radiobutton(radios, text="On", value="On",
                        variable=self.pedestrian_var).pack(side="left")
        ttk.Radiobutton(radios, text="Off", value="Off",
                        variable=self.pedestrian_var).pack(side="left")

        start = ttk.Button(frame, text="Start Simulation",
                           command=self.start_simulation)
        start.grid(row=11, column=0, columnspan=2, pady=(20, 0))
        start.focus_set()
        parent_toplevel = frame.winfo_toplevel()
        parent_toplevel.bind("<Return>", lambda _e: self.start_simulation())

    def start_simulation(self) -> None:
        Parameters.simulation_speed = int(self.fields["speed"].get())
        Parameters.simulation_end_time = int(self.fields["end_time"].get())
        Parameters.seed = int(self.fields["seed"].get())
        print("Seed: " + str(Parameters.seed))
        print("CF Model: " + str(Parameters.car_following_model.name))
        Parameters.random = (JavaRandom() if Parameters.seed < 0
                             else JavaRandom(Parameters.seed))
        Parameters.TRACE_MODE = jbool(self.fields["trace"].get())
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
        pass

    def run(self) -> None:
        self.root.mainloop()
