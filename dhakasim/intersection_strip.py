"""Port of ``thesisfinal.IntersectionStrip``.

@author mishkat076
"""

from __future__ import annotations

from .utilities import get_distance


class IntersectionStrip:
    __slots__ = ("start_link_index", "start_strip", "end_link_index", "end_strip",
                 "start_point_x", "start_point_y", "end_point_x", "end_point_y",
                 "leaving_segment", "entering_segment")

    def __init__(self, start_link_index, start_strip, end_link_index, end_strip,
                 start_point_x, start_point_y, end_point_x, end_point_y,
                 leaving_segment, entering_segment):
        self.start_link_index = start_link_index
        self.start_strip = start_strip
        self.end_link_index = end_link_index
        self.end_strip = end_strip
        self.start_point_x = start_point_x
        self.start_point_y = start_point_y
        self.end_point_x = end_point_x
        self.end_point_y = end_point_y
        self.leaving_segment = leaving_segment
        self.entering_segment = entering_segment

    def get_start_link_index(self) -> int:
        return self.start_link_index

    def get_length(self) -> float:
        return get_distance(self.start_point_x, self.start_point_y,
                            self.end_point_x, self.end_point_y)
