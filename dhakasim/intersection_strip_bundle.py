"""Port of ``thesisfinal.IntersectionStripBundle``.

@author mushfiq
"""

from __future__ import annotations

from .javacompat import jint
from .signal import SIGNAL


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
