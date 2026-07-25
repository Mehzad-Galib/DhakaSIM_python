"""Port of ``thesisfinal.Object`` -- a roadside object / side friction element."""

from __future__ import annotations

import math

from .constants import Constants
from .javacompat import JavaRandom, jint, jround
from .parameters import Parameters
from .utilities import return_x3, return_x4, return_y3, return_y4


class Object:
    """Current object types are:

    * 0 for road-crossing pedestrian
    * 1 for standing pedestrian
    * 2 for parked car
    * 3 for parked rickshaw
    * 4 for parked CNG
    """

    __slots__ = ("_object_id", "object_type", "_segment", "_strip", "_init_pos",
                 "_object_length", "_object_width", "_distance_from_footpath",
                 "_distance_along_width", "_reverse_direction", "_random_double",
                 "in_accident", "to_remove", "index", "_parking_time",
                 "_parking_start_time")

    def __init__(self, object_id, object_type, parking_start_time, link, segment,
                 segment_id, init_pos, reverse_direction, distance_from_footpath):
        # Initialize parameters
        self.object_type = object_type
        self._segment = segment
        #: Equivalent to :meth:`Vehicle.get_distance_in_segment`.
        self._init_pos = init_pos
        self._reverse_direction = reverse_direction
        self._parking_start_time = parking_start_time
        self._object_id = object_id
        self.in_accident = False
        self.to_remove = False
        self.index = 0
        self._object_length = 0.0
        self._object_width = 0.0
        self._parking_time = 0.0

        random = JavaRandom()
        self._random_double = random.next_double()

        # Initialize other fields; the parking time is estimated from a uniform
        # distribution (for a standing pedestrian it is how long they stand in
        # that position blocking the road).
        if object_type == 1:
            self._object_length = Constants.STANDING_PEDESTRIAN_LENGTH
            self._object_width = Constants.STANDING_PEDESTRIAN_WIDTH
            self._parking_time = JavaRandom().next_double_range(10, 60)
        elif object_type == 2:
            self._object_length = Constants.PARKED_CAR_LENGTH
            self._object_width = Constants.PARKED_CAR_WIDTH
            self._parking_time = JavaRandom().next_double_range(100, 500)
        elif object_type == 3:
            self._object_length = Constants.PARKED_RICKSHAW_LENGTH
            self._object_width = Constants.PARKED_RICKSHAW_WIDTH
            self._parking_time = JavaRandom().next_double_range(30, 150)
        elif object_type == 4:
            self._object_length = Constants.PARKED_CNG_LENGTH
            self._object_width = Constants.PARKED_CNG_WIDTH
            self._parking_time = JavaRandom().next_double_range(60, 300)

        self._distance_from_footpath = distance_from_footpath

        self._distance_along_width = (distance_from_footpath
                                      + Parameters.footpath_strip_width)

        if reverse_direction:
            self._strip = segment.get_strip(segment.number_of_strips() - 1)
        else:
            self._strip = segment.get_strip(0)

        self._occupy_strips()

    def get_distance_in_segment(self) -> float:
        return self._init_pos

    def get_distance_along_width(self) -> float:
        return self._distance_along_width

    def get_distance_from_footpath(self) -> float:
        return self._distance_from_footpath

    def get_init_pos(self) -> float:
        return self._init_pos

    def get_init_pos_of_starting_side(self) -> float:
        if self.object_type in (0, 1):
            return self._init_pos - self._object_length / 2
        return self._init_pos

    def get_object_length(self) -> float:
        return self._object_length

    def get_segment(self):
        return self._segment

    def get_parking_start_time(self) -> float:
        return self._parking_start_time

    def get_parking_time(self) -> float:
        return self._parking_time

    def _occupy_strips(self) -> None:
        occupied_width = self._distance_from_footpath + self._object_width
        self._occupy_strips_for_rectangular_object(occupied_width)

    def _occupy_strips_for_rectangular_object(self, width: float) -> None:
        if math.fmod(width, Parameters.strip_width) == 0:
            number_of_occupied_strips = jint(width / Parameters.strip_width)
        else:
            number_of_occupied_strips = jint(width / Parameters.strip_width) + 1
        if not self._reverse_direction:
            # Started from 0, as we want to occupy the footpath strip as well.
            for i in range(number_of_occupied_strips + 1):
                self._segment.get_strip(i).add_object(self)
        else:
            for i in range(number_of_occupied_strips + 1):
                self._segment.get_strip(
                    self._segment.number_of_strips() - 1 - i).add_object(self)

    def get_strip(self):
        return self._strip

    def set_segment(self, segment) -> None:
        self._segment = segment

    def _set_strip(self, strip) -> None:
        self._strip = strip

    def has_crossed_road(self) -> bool:
        return self._distance_along_width >= self._segment.get_segment_width()

    def draw_object(self, trace_writer, g, strip_pixel_count, mp_ratio,
                    fp_strip_pixel_count) -> None:
        if self.object_type == 1:
            self._draw_rectangular_object(g, Constants.STANDING_PEDESTRIAN_LENGTH,
                                         Constants.STANDING_PEDESTRIAN_WIDTH,
                                         Constants.STANDING_PEDESTRIAN_COLOR)
        elif self.object_type == 2:
            self._draw_rectangular_object(g, Constants.PARKED_CAR_LENGTH,
                                         Constants.PARKED_CAR_WIDTH,
                                         Constants.PARKED_CAR_COLOR)
        elif self.object_type == 3:
            self._draw_rectangular_object(g, Constants.PARKED_RICKSHAW_LENGTH,
                                         Constants.PARKED_RICKSHAW_WIDTH,
                                         Constants.PARKED_RICKSHAW_COLOR)
        elif self.object_type == 4:
            self._draw_rectangular_object(g, Constants.PARKED_CNG_LENGTH,
                                         Constants.PARKED_CNG_WIDTH,
                                         Constants.PARKED_CNG_COLOR)

    def _draw_rectangular_object(self, g, length, width, color) -> None:
        pixel_per_meter = Parameters.pixel_per_meter
        segment = self.get_segment()
        segment_length = segment.get_length()
        distance_in_segment = self._init_pos

        xp = ((distance_in_segment * segment.get_end_x()
               + (segment_length - distance_in_segment) * segment.get_start_x())
              / segment_length * pixel_per_meter)
        yp = ((distance_in_segment * segment.get_end_y()
               + (segment_length - distance_in_segment) * segment.get_start_y())
              / segment_length * pixel_per_meter)
        xq = (((distance_in_segment + length) * segment.get_end_x()
               + (segment_length - (distance_in_segment + length)) * segment.get_start_x())
              / segment_length * pixel_per_meter)
        yq = (((distance_in_segment + length) * segment.get_end_y()
               + (segment_length - (distance_in_segment + length)) * segment.get_start_y())
              / segment_length * pixel_per_meter)

        object_width_in_pixel = jint(jround(width * pixel_per_meter))
        if not self._reverse_direction:
            w = self._distance_along_width * pixel_per_meter
        else:
            w = ((segment.get_segment_width() - self._distance_along_width - width)
                 * pixel_per_meter)
        x1 = jint(jround(return_x3(xp, yp, xq, yq, w)))
        y1 = jint(jround(return_y3(xp, yp, xq, yq, w)))
        x2 = jint(jround(return_x4(xp, yp, xq, yq, w)))
        y2 = jint(jround(return_y4(xp, yp, xq, yq, w)))
        x3 = jint(jround(return_x3(xp, yp, xq, yq, w + object_width_in_pixel)))
        y3 = jint(jround(return_y3(xp, yp, xq, yq, w + object_width_in_pixel)))
        x4 = jint(jround(return_x4(xp, yp, xq, yq, w + object_width_in_pixel)))
        y4 = jint(jround(return_y4(xp, yp, xq, yq, w + object_width_in_pixel)))
        xs = [x1, x2, x4, x3]
        ys = [y1, y2, y4, y3]
        g.set_color(color)
        g.fill_polygon(xs, ys, 4)

    def is_to_remove(self) -> bool:
        return self.to_remove

    def is_in_accident(self) -> bool:
        return self.in_accident

    def is_reverse_segment(self) -> bool:
        return self._reverse_direction

    def set_to_remove(self, b: bool) -> None:
        self.to_remove = b
