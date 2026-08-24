"""Port of ``thesisfinal.Node``."""

from __future__ import annotations

from .constants import Constants
from .javacompat import DOUBLE_MAX_VALUE, DOUBLE_MIN_VALUE
from .intersection_strip_bundle import IntersectionStripBundle
from .parameters import Parameters
from .point2d import Point2D
from .signal import SIGNAL
from . import signal_schedule


class Node:
    __slots__ = ("_index", "_id", "x", "y", "_time_passed", "_link_list",
                 "intersection_strip_list", "_vehicle_list",
                 "_intersection_strip_bundles", "_active_bundle_index",
                 "_pressure_on_active_bundle", "_roundabout_radius",
                 "_circulatory_width",
                 "_circulation_order", "_centre_x", "_centre_y",
                 "_green_times", "_signal_rng")

    MIN_VEHICLES_TO_MAKE_A_SIGNAL_GREEN = 1

    def __init__(self, index: int, nod_id: int, x: float, y: float):
        self._index = index
        self._id = nod_id
        self.x = x
        self.y = y
        self._time_passed = 0
        self._active_bundle_index = 0
        self._pressure_on_active_bundle = 0
        # > 0 marks this node as a roundabout of that radius in metres
        # (GeometryMode only). See ``roundabout_signal_change``.
        self._roundabout_radius = 0.0
        # Only meaningful once set_roundabout has run.
        self._circulatory_width = Constants.ROUNDABOUT_CIRCULATORY_WIDTH
        # incident link indices in circulation order (see set_circulation_order)
        self._circulation_order = []
        # true centre of a junction, in metres; junction nodes are stored at
        # (0, 0) so this is recovered from the incident link geometry
        self._centre_x = 0.0
        self._centre_y = 0.0

        # Green duration per bundle, in simulation steps, as decided by the
        # Traffic Signal Scheduling Module.  Empty until the first cycle is
        # planned, and unused unless Parameters.SIGNAL_MODE asks for it.
        self._green_times = []
        # The optimiser's own random stream.  Seeded per node so that the
        # schedule an intersection gets does not depend on how many other
        # intersections were planned before it, and kept well away from
        # Parameters.random, which is what makes a seeded run reproduce the
        # Java original exactly.
        self._signal_rng = None

        self._link_list = []
        self.intersection_strip_list = []
        self._vehicle_list = []
        self._intersection_strip_bundles = []

    def set_centre(self, x: float, y: float) -> None:
        self._centre_x = x
        self._centre_y = y

    def get_centre(self):
        return self._centre_x, self._centre_y

    def create_bundles(self) -> None:
        """Completes the bundle creation task."""
        for link_index in self._link_list:
            self._intersection_strip_bundles.append(IntersectionStripBundle(link_index))
        self._change_signal_of_active_bundle_into(SIGNAL.GREEN)

    def _add_intersection_strip_to_bundle(self, intersection_strip) -> bool:
        for bundle in self._intersection_strip_bundles:
            if bundle.get_intersection_bundle_index() == intersection_strip.start_link_index:
                bundle.add_intersection_strip(intersection_strip)
                return True
        return False

    def _get_bundle(self, start_link_index: int):
        """:return: the bundle whose startLinkIndex matches, otherwise None"""
        for pb in self._intersection_strip_bundles:
            if pb.get_intersection_bundle_index() == start_link_index:
                return pb
        return None

    def is_bundle_active(self, start_link_index: int) -> bool:
        pb = self._get_bundle(start_link_index)
        if pb is not None:
            return pb.get_signal() == SIGNAL.GREEN
        return False

    def _change_signal_of_active_bundle_into(self, signal: SIGNAL) -> None:
        self._intersection_strip_bundles[self._active_bundle_index].set_signal(signal)

    def _get_next_active_bundle(self) -> int:
        return (self._active_bundle_index + 1) % len(self._intersection_strip_bundles)

    def adaptive_signal_change(self, simulation_time: int) -> None:
        """A somewhat adaptive signalling scheme.

        :param simulation_time: current time in simulation
        """
        if (simulation_time - self._time_passed
                >= min(Parameters.SIGNAL_CHANGE_DURATION
                       + self._pressure_on_active_bundle, 120)):
            if (self._intersection_strip_bundles[self._active_bundle_index].get_signal()
                    == SIGNAL.GREEN):
                self._change_signal_of_active_bundle_into(SIGNAL.YELLOW)
            else:
                current_active_bundle = self._active_bundle_index
                while True:
                    self._switch_signal()
                    if self._active_bundle_index == current_active_bundle:
                        break
                    if not (self._pressure_on_active_bundle
                            < self.MIN_VEHICLES_TO_MAKE_A_SIGNAL_GREEN):
                        break
                self._time_passed = simulation_time
        else:
            try:
                isb = self._intersection_strip_bundles[self._active_bundle_index]
                if isb.get_pressure_on_bundle() == 0:
                    self._change_signal_of_active_bundle_into(SIGNAL.YELLOW)
            except AttributeError:
                pass  # Java catches NullPointerException here

    def manual_signaling(self) -> None:
        """Debug this function"""
        if self._is_node_clear():
            current_active_bundle = self._active_bundle_index
            while True:
                self._switch_signal()
                if self._active_bundle_index == current_active_bundle:
                    break
                if not (self._pressure_on_active_bundle
                        < self.MIN_VEHICLES_TO_MAKE_A_SIGNAL_GREEN):
                    break
            self._time_passed = Parameters.simulation_step

    def set_roundabout(self, radius: float, circulatory_width: float = 0.0) -> None:
        self._roundabout_radius = radius
        self._circulatory_width = (circulatory_width if circulatory_width > 0
                                   else Constants.ROUNDABOUT_CIRCULATORY_WIDTH)

    def is_roundabout(self) -> bool:
        return self._roundabout_radius > 0

    def get_roundabout_radius(self) -> float:
        return self._roundabout_radius

    def get_circulatory_width(self) -> float:
        return self._circulatory_width

    def get_outer_radius(self) -> float:
        """Kerb line of the circulatory carriageway, in metres.

        The arms are pulled back to this circle when the network is read, so
        it is where a roundabout begins for a driver and where the ring is
        drawn for a reader.  Nothing should be inside it but the island.
        """
        return self._roundabout_radius + self._circulatory_width

    def set_circulation_order(self, link_indices) -> None:
        """Incident links ordered the way traffic circulates.

        Bangladesh drives on the left, so roundabout traffic circulates
        **clockwise** and an entering driver gives way to the right. The
        processor supplies the incident links sorted by compass bearing, which
        is exactly that clockwise order.
        """
        self._circulation_order = list(link_indices)

    def _approaches_passed(self, from_link: int, to_link: int):
        """Entries a circulating vehicle will cross going from *from* to *to*.

        Walking clockwise from the entry arm to the exit arm, every arm strictly
        in between has its entry blocked by that vehicle. The entry arm is
        behind it and the exit arm is where it leaves, so neither conflicts.
        """
        order = self._circulation_order
        if from_link not in order or to_link not in order:
            return []
        start = order.index(from_link)
        end = order.index(to_link)
        passed = []
        i = (start + 1) % len(order)
        while i != end:
            passed.append(order[i])
            i = (i + 1) % len(order)
        return passed

    def roundabout_signal_change(self) -> None:
        """Priority rule for a roundabout: give way to circulating traffic.

        A roundabout has no phases. Traffic already on the circulatory
        carriageway keeps moving, and an approach may enter only when no
        circulating vehicle is about to cross that entry. Each approach is
        judged separately, so entries that do not conflict with the vehicles
        currently going round may proceed at the same time -- which is what
        gives a roundabout its capacity advantage over a signal.

        Falls back to blocking every approach whenever the circulation order is
        unknown, which is the safe (capacity-conservative) reading.
        """
        if not self._circulation_order:
            signal = SIGNAL.GREEN if self._is_node_clear() else SIGNAL.RED
            for bundle in self._intersection_strip_bundles:
                bundle.set_signal(signal)
            return

        blocked = set()
        for vehicle in self._vehicle_list:
            try:
                strip = self.get_intersection_strip(
                    vehicle.get_intersection_strip_index())
            except (IndexError, AttributeError, TypeError):
                continue
            blocked.update(self._approaches_passed(strip.start_link_index,
                                                   strip.end_link_index))
        for bundle in self._intersection_strip_bundles:
            link_index = bundle.get_intersection_bundle_index()
            bundle.set_signal(SIGNAL.RED if link_index in blocked
                              else SIGNAL.GREEN)

    def scheduled_signal_change(self, simulation_time: int) -> None:
        """Signalling driven by the Traffic Signal Scheduling Module.

        Algorithm 1 of Rahaman et al., 2025, from this end: hold each approach
        green for the duration the module gave it, and when the cycle has come
        all the way round, ask for a new schedule against the traffic that is
        there *now*.  Re-planning every cycle rather than every step is the
        paper's own design and it is also what makes the cost bearable -- a
        full NSGA-II run per intersection per simulation step would dominate
        everything else the simulator does.
        """
        if not self._intersection_strip_bundles:
            return
        if not self._green_times:
            self._plan_cycle()
            self._time_passed = simulation_time
            return
        index = self._active_bundle_index
        if simulation_time - self._time_passed < self._green_times[index]:
            return
        self._switch_signal()
        self._time_passed = simulation_time
        if self._active_bundle_index == 0:
            self._plan_cycle()

    def _plan_cycle(self) -> None:
        """Ask the scheduling module for one green duration per approach."""
        if self._signal_rng is None:
            # Distinct per node, derived from the run's seed, so the whole
            # thing stays reproducible without sharing state between nodes.
            self._signal_rng = signal_schedule.seed_from(
                (0 if Parameters.seed is None or Parameters.seed < 0
                 else Parameters.seed) + self._id * 7919)
        demands = [signal_schedule.Demand(*bundle.get_demand_on_bundle())
                   for bundle in self._intersection_strip_bundles]
        seconds = signal_schedule.schedule(
            demands, Parameters.SIGNAL_MODE,
            rng=self._signal_rng,
            # SignalChangeDuration is the fixed-time green, and it is the
            # only place that number lives.  It is held in steps, and the
            # module works in seconds.
            fixed_green=Parameters.SIGNAL_CHANGE_DURATION * Constants.TIME_STEP,
            motorised_weight=Parameters.SIGNAL_MOTORISED_WEIGHT,
            objective_weight=Parameters.SIGNAL_OBJECTIVE_WEIGHT,
            green_min=Parameters.SIGNAL_GREEN_MIN,
            green_max=Parameters.SIGNAL_GREEN_MAX,
            population=Parameters.SIGNAL_POPULATION,
            evaluations=Parameters.SIGNAL_EVALUATIONS,
            random_low=Parameters.SIGNAL_RANDOM_LOW,
            random_high=Parameters.SIGNAL_RANDOM_HIGH)
        # Seconds out of the module, simulation steps in here.  At least one
        # step, or an approach whose green rounds to nothing never opens and
        # the traffic on it waits for the whole run.
        self._green_times = [max(1, int(round(green / Constants.TIME_STEP)))
                             for green in seconds]

    def get_green_times(self):
        """The current cycle, in simulation steps.  For the report and tests."""
        return list(self._green_times)

    def constant_signal_change(self, simulation_time: int) -> None:
        """Non adaptive signal changing scheme; does not consider load on a link."""
        if simulation_time - self._time_passed >= Parameters.SIGNAL_CHANGE_DURATION:
            self._switch_signal()
            self._time_passed = simulation_time

    def _switch_signal(self) -> None:
        """Turns the current signal red and the next one green."""
        self._change_signal_of_active_bundle_into(SIGNAL.RED)
        self._active_bundle_index = self._get_next_active_bundle()
        self._change_signal_of_active_bundle_into(SIGNAL.GREEN)
        try:
            isb = self._intersection_strip_bundles[self._active_bundle_index]
            self._pressure_on_active_bundle = isb.get_pressure_on_bundle()
        except AttributeError:
            self._pressure_on_active_bundle = 0

    def intersection_strip_exists(self, start_link, start_strip, end_link, end_strip
                                 ) -> bool:
        for intersection_strip in self.intersection_strip_list:
            if (intersection_strip.start_link_index == start_link
                    and intersection_strip.start_strip == start_strip
                    and intersection_strip.end_link_index == end_link
                    and intersection_strip.end_strip == end_strip):
                return True
        return False

    def get_my_intersection_strip(self, start_link, start_strip, end_link, end_strip
                                 ) -> int:
        for i, strip in enumerate(self.intersection_strip_list):
            if (strip.start_link_index == start_link
                    and strip.start_strip == start_strip
                    and strip.end_link_index == end_link
                    and strip.end_strip == end_strip):
                return i
        return -1

    @staticmethod
    def _is_colliding(a, b) -> bool:
        """Separating-axis test on the two vehicles' footprints.

        Both corner lists are fetched once and reduced to plain coordinate
        pairs: this runs over eight edges for every pair of vehicles in every
        junction on every step, and asking each vehicle for its corners again
        inside the inner loop -- and boxing every edge normal in a Point2D --
        cost more than the arithmetic did.
        """
        corners_a = [(p.x, p.y) for p in a.get_segment_corners()]
        corners_b = [(p.x, p.y) for p in b.get_segment_corners()]

        for corners in (corners_a, corners_b):
            count = len(corners)
            for i1 in range(count):
                x1, y1 = corners[i1]
                x2, y2 = corners[(i1 + 1) % count]
                nx, ny = y2 - y1, x1 - x2

                min_a = DOUBLE_MAX_VALUE
                # Java seeds this with Double.MIN_VALUE, the smallest positive
                # denormal rather than the most negative double.
                max_a = DOUBLE_MIN_VALUE

                for px, py in corners_a:
                    projected = nx * px + ny * py

                    if projected < min_a:
                        min_a = projected
                    if projected > max_a:
                        max_a = projected

                min_b = DOUBLE_MAX_VALUE
                max_b = DOUBLE_MIN_VALUE

                for px, py in corners_b:
                    projected = nx * px + ny * py

                    if projected < min_b:
                        min_b = projected
                    if projected > max_b:
                        max_b = projected

                if max_a < min_b or max_b < min_a:
                    return False

        return True

    def get_overlapping_vehicle(self, v):
        v.calculate_corner_points()
        for vehicle in self._vehicle_list:
            if vehicle is not v:
                vehicle.calculate_corner_points()
                if self._is_colliding(vehicle, v):
                    return vehicle
        return None

    def do_overlap(self, v) -> bool:
        v.calculate_corner_points()
        for vehicle in self._vehicle_list:
            if vehicle is not v:
                vehicle.calculate_corner_points()
                if self._is_colliding(vehicle, v):
                    return True
        return False

    def remove_vehicle(self, v) -> None:
        for i, existing in enumerate(self._vehicle_list):
            if existing is v:
                del self._vehicle_list[i]
                return

    def _is_node_clear(self) -> bool:
        return not self._vehicle_list

    def get_signal_on_link(self, link_index: int) -> SIGNAL:
        for isb in self._intersection_strip_bundles:
            if isb.get_intersection_bundle_index() == link_index:
                return isb.get_signal()
        return SIGNAL.RED

    def get_index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index

    def get_id(self) -> int:
        return self._id

    def set_id(self, id_: int) -> None:
        self._id = id_

    def get_x(self) -> float:
        return self.x

    def set_x(self, x: float) -> None:
        self.x = x

    def get_y(self) -> float:
        return self.y

    def set_y(self, y: float) -> None:
        self.y = y

    def get_time_passed(self) -> int:
        return self._time_passed

    def set_time_passed(self, time_passed: int) -> None:
        self._time_passed = time_passed

    def get_link(self, index: int) -> int:
        return self._link_list[index]

    def add_link(self, link: int) -> None:
        self._link_list.append(link)

    def number_of_links(self) -> int:
        return len(self._link_list)

    def get_intersection_strip(self, index: int):
        return self.intersection_strip_list[index]

    def add_intersection_strip(self, intersection_strip) -> None:
        self.intersection_strip_list.append(intersection_strip)
        if not self._add_intersection_strip_to_bundle(intersection_strip):
            print("Problem")

    def number_of_intersection_strips(self) -> int:
        return len(self.intersection_strip_list)

    def get_vehicle(self, index: int):
        return self._vehicle_list[index]

    def add_vehicle(self, vehicle) -> None:
        self._vehicle_list.append(vehicle)

    def number_of_vehicles(self) -> int:
        return len(self._vehicle_list)
