"""Port of ``thesisfinal.IntersectionStrip``.

@author mishkat076
"""

from __future__ import annotations

import math

from .utilities import get_distance


class IntersectionStrip:
    __slots__ = ("start_link_index", "start_strip", "end_link_index", "end_strip",
                 "start_point_x", "start_point_y", "end_point_x", "end_point_y",
                 "leaving_segment", "entering_segment",
                 "arc_cx", "arc_cy", "arc_radius", "arc_start", "arc_sweep")

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
        # Curved path around a roundabout island; arc_radius <= 0 means the
        # path is the straight chord, which is the default everywhere else.
        self.arc_cx = 0.0
        self.arc_cy = 0.0
        self.arc_radius = 0.0
        self.arc_start = 0.0
        self.arc_sweep = 0.0

    def set_arc(self, centre_x: float, centre_y: float) -> None:
        """Bend this path into a clockwise arc about a roundabout island.

        Traffic in Bangladesh circulates clockwise, so the path from the entry
        point to the exit point is taken the clockwise way round the centre.
        Screen y grows downward, which makes increasing angle clockwise, so the
        sweep is simply the positive angular difference.
        """
        r1 = get_distance(centre_x, centre_y, self.start_point_x, self.start_point_y)
        r2 = get_distance(centre_x, centre_y, self.end_point_x, self.end_point_y)
        radius = (r1 + r2) / 2.0
        if radius <= 0:
            return
        a1 = math.atan2(self.start_point_y - centre_y, self.start_point_x - centre_x)
        a2 = math.atan2(self.end_point_y - centre_y, self.end_point_x - centre_x)
        sweep = (a2 - a1) % (2.0 * math.pi)
        if sweep <= 0:
            return
        self.arc_cx = centre_x
        self.arc_cy = centre_y
        self.arc_radius = radius
        self.arc_start = a1
        self.arc_sweep = sweep

    def is_curved(self) -> bool:
        return self.arc_radius > 0

    def point_at(self, distance: float):
        """Position ``distance`` along the path, following the arc if curved."""
        if not self.is_curved():
            total = get_distance(self.start_point_x, self.start_point_y,
                                 self.end_point_x, self.end_point_y)
            if total <= 0:
                return self.start_point_x, self.start_point_y
            t = distance / total
            return (self.start_point_x + (self.end_point_x - self.start_point_x) * t,
                    self.start_point_y + (self.end_point_y - self.start_point_y) * t)
        angle = self.arc_start + distance / self.arc_radius
        return (self.arc_cx + self.arc_radius * math.cos(angle),
                self.arc_cy + self.arc_radius * math.sin(angle))

    def get_start_link_index(self) -> int:
        return self.start_link_index

    def get_length(self) -> float:
        # Going round an island is further than cutting across it, and this
        # length is what the vehicle has to cover to clear the junction.
        if self.is_curved():
            return self.arc_radius * self.arc_sweep
        return get_distance(self.start_point_x, self.start_point_y,
                            self.end_point_x, self.end_point_y)
