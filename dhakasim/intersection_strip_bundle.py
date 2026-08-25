"""Port of ``thesisfinal.IntersectionStripBundle``.

@author mushfiq
"""

from __future__ import annotations

from .javacompat import jint
from .signal import SIGNAL
from .signal_schedule import PEDESTRIAN_TYPE, is_motorised


class IntersectionStripBundle:
    __slots__ = ("_intersection_strips", "_intersection_bundle_index",
                 "_leaving_segment", "_is_reverse_segment", "_signal")

    def __init__(self, intersection_bundle_index: int):
        self._intersection_strips = []
        # index of the link from where a vehicle enters the intersection
        self._intersection_bundle_index = intersection_bundle_index
        self._signal = SIGNAL.RED
        self._leaving_segment = None
        self._is_reverse_segment = False

    def add_intersection_strip(self, intersection_strip) -> None:
        self._intersection_strips.append(intersection_strip)
        if self._leaving_segment is None:
            self._set_segment_properties()

    def get_pressure_on_bundle(self) -> int:
        if self._is_reverse_segment:
            vs = self._leaving_segment.get_reverse_vehicle_count()
        else:
            vs = self._leaving_segment.get_forward_vehicle_count()
        area_of_segment = (self._leaving_segment.get_segment_width()
                           * self._leaving_segment.get_length())
        # pb is computed but the Java code returns vs
        pb = jint(5000 * vs / area_of_segment)  # noqa: F841
        return vs

    def get_demand_on_bundle(self):
        """(motorised, non-motorised) vehicles waiting on this approach.

        The count the signal scheduler needs: ``C_m(i)`` and ``C_nm(i)`` of
        Rahaman et al., 2025.  ``get_pressure_on_bundle`` already returns the
        total, but the whole point of that paper's objective functions is that
        the two classes clear an intersection at very different rates, so the
        total on its own cannot price an approach.

        Counted by walking the approach segment's strips rather than by keeping
        a running tally.  A tally would mean touching the vehicle movement
        loop, which is the one place in this simulator where a change has to be
        proved bit-identical; this runs once per signal cycle, off that path
        entirely.  A vehicle spans several strips, hence the de-duplication.
        """
        motorised = 0
        non_motorised = 0
        if self._leaving_segment is None:
            return motorised, non_motorised
        seen = set()
        for i in range(self._leaving_segment.get_strip_count()):
            for vehicle in self._leaving_segment.get_strip(i).get_vehicle_list():
                if vehicle.is_reverse_segment() != self._is_reverse_segment:
                    continue                  # the other carriageway
                if vehicle.get_type() == PEDESTRIAN_TYPE:
                    continue                  # walking, not queueing
                key = vehicle.get_vehicle_id()
                if key in seen:
                    continue
                seen.add(key)
                if is_motorised(vehicle.get_type()):
                    motorised += 1
                else:
                    non_motorised += 1
        return motorised, non_motorised

    def _set_segment_properties(self) -> None:
        self._leaving_segment = self._intersection_strips[0].leaving_segment
        self._is_reverse_segment = (self._intersection_strips[0].start_strip
                                   >= self._leaving_segment.middle_high_strip_index)

    def clear_bundle(self) -> None:
        self._intersection_strips.clear()

    def get_intersection_bundle_index(self) -> int:
        return self._intersection_bundle_index

    def set_intersection_bundle_index(self, intersection_bundle_index: int) -> None:
        self._intersection_bundle_index = intersection_bundle_index

    def get_signal(self) -> SIGNAL:
        return self._signal

    def set_signal(self, signal: SIGNAL) -> None:
        self._signal = signal
