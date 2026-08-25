"""Port of ``thesisfinal.LinkSegmentOrientation``."""

from __future__ import annotations

from .javacompat import jmin
from .utilities import get_distance


class LinkSegmentOrientation:
    __slots__ = ("reverse_link", "reverse_segment")

    def __init__(self):
        self.reverse_link = False
        self.reverse_segment = False


def get_link_and_segment_orientation(x, y, first_segment, last_segment
                                    ) -> LinkSegmentOrientation:
    link_segment_orientation = LinkSegmentOrientation()
    distance1 = get_distance(x, y, first_segment.get_start_x(), first_segment.get_start_y())
    distance2 = get_distance(x, y, first_segment.get_end_x(), first_segment.get_end_y())
    distance3 = get_distance(x, y, last_segment.get_start_x(), last_segment.get_start_y())
    distance4 = get_distance(x, y, last_segment.get_end_x(), last_segment.get_end_y())
    minimum = jmin(distance1, jmin(distance2, jmin(distance3, distance4)))
    if first_segment is not last_segment:
        if minimum == distance1:
            link_segment_orientation.reverse_link = False
            link_segment_orientation.reverse_segment = False
        elif minimum == distance2:
            link_segment_orientation.reverse_link = False
            link_segment_orientation.reverse_segment = True
        elif minimum == distance3:
            link_segment_orientation.reverse_link = True
            link_segment_orientation.reverse_segment = False
        elif minimum == distance4:
            link_segment_orientation.reverse_link = True
            link_segment_orientation.reverse_segment = True
    else:
        if minimum == distance1:
            link_segment_orientation.reverse_link = False
            link_segment_orientation.reverse_segment = False
        else:
            link_segment_orientation.reverse_link = True
            link_segment_orientation.reverse_segment = True
    return link_segment_orientation
