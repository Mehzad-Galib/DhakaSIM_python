"""Port of ``thesisfinal.Node``."""

from __future__ import annotations

from .javacompat import DOUBLE_MAX_VALUE, DOUBLE_MIN_VALUE
from .intersection_strip_bundle import IntersectionStripBundle
from .parameters import Parameters
from .point2d import Point2D
from .signal import SIGNAL


class Node:
    __slots__ = ("_index", "_id", "x", "y", "_time_passed", "_link_list",
                 "intersection_strip_list", "_vehicle_list",
                 "_intersection_strip_bundles", "_active_bundle_index",
                 "_pressure_on_active_bundle")

    MIN_VEHICLES_TO_MAKE_A_SIGNAL_GREEN = 1

    def __init__(self, index: int, nod_id: int, x: float, y: float):
        self._index = index
        self._id = nod_id
        self.x = x
        self.y = y
        self._time_passed = 0
        self._active_bundle_index = 0
        self._pressure_on_active_bundle = 0

        self._link_list = []
        self.intersection_strip_list = []
        self._vehicle_list = []
        self._intersection_strip_bundles = []

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
        for x in range(2):
            vehicle = a if x == 0 else b

            corners = vehicle.get_segment_corners()
            for i1 in range(len(corners)):
                i2 = (i1 + 1) % len(corners)
                p1 = corners[i1]
                p2 = corners[i2]

                normal = Point2D(p2.y - p1.y, p1.x - p2.x)

                min_a = DOUBLE_MAX_VALUE
                # Java seeds this with Double.MIN_VALUE, the smallest positive
                # denormal rather than the most negative double.
                max_a = DOUBLE_MIN_VALUE

                for p in a.get_segment_corners():
                    projected = normal.x * p.x + normal.y * p.y

                    if projected < min_a:
                        min_a = projected
                    if projected > max_a:
                        max_a = projected

                min_b = DOUBLE_MAX_VALUE
                max_b = DOUBLE_MIN_VALUE

                for p in b.get_segment_corners():
                    projected = normal.x * p.x + normal.y * p.y

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
