"""Port of ``thesisfinal.Segment``."""

from __future__ import annotations

import math

from .constants import Constants
from .javacompat import jint, jround
from .parameters import Parameters
from .strip import Strip
from .utilities import get_distance, return_x3, return_x4, return_y3, return_y4


class Segment:
    __slots__ = ("_link_index", "_index", "_id", "_start_x", "_start_y", "_end_x",
                 "_end_y", "_segment_width", "_last_segment", "_first_segment",
                 "_sensor", "_parent_link_id", "_strip_count", "_sensor_vehicle_count",
                 "_entering_vehicle_count", "_leaving_vehicle_count",
                 "_avg_speed_in_segment", "_near_crash_count",
                 "_sensor_vehicle_avg_speed", "_accident_count", "_total_waiting_time",
                 "_forward_vehicle_count", "_reverse_vehicle_count",
                 "middle_low_strip_index", "middle_high_strip_index",
                 "last_vehicle_strip_index", "_strip_list")

    def __init__(self, link_index, index, id_, start_x, start_y, end_x, end_y,
                 segment_width, last_segment, first_segment, parent_link_id):
        self._link_index = link_index
        self._index = index
        self._id = id_
        self._start_x = start_x
        self._start_y = start_y
        self._end_x = end_x
        self._end_y = end_y
        self._segment_width = segment_width
        self._last_segment = last_segment
        self._first_segment = first_segment
        self._parent_link_id = parent_link_id
        self._sensor = 0.5 * get_distance(start_x, start_y, end_x, end_y)
        self._sensor_vehicle_count = 0
        self._sensor_vehicle_avg_speed = 0.0
        self._accident_count = 0
        self._entering_vehicle_count = 0
        self._leaving_vehicle_count = 0
        self._avg_speed_in_segment = 0.0
        self._near_crash_count = 0
        self._total_waiting_time = 0
        self._forward_vehicle_count = 0
        self._reverse_vehicle_count = 0
        # when a vehicle leaves the segment it increases totalWaitingTime by
        # its waiting time on this segment
        self._strip_list = []
        self.middle_low_strip_index = 0
        self.middle_high_strip_index = 0  # first strip of the 2nd side of the road
        self.last_vehicle_strip_index = 0
        self._strip_count = 0
        self._initialize()

    def get_forward_vehicle_count(self) -> int:
        return self._forward_vehicle_count

    def get_reverse_vehicle_count(self) -> int:
        return self._reverse_vehicle_count

    def increase_forward_vehicle_count(self) -> None:
        self._forward_vehicle_count += 1

    def increase_reverse_vehicle_count(self) -> None:
        self._reverse_vehicle_count += 1

    def decrease_forward_vehicle_count(self) -> None:
        self._forward_vehicle_count -= 1

    def decrease_reverse_vehicle_count(self) -> None:
        self._reverse_vehicle_count -= 1

    def increase_entering_vehicle_count(self) -> None:
        self._entering_vehicle_count += 1

    def _increase_leaving_vehicle_count(self) -> None:
        self._leaving_vehicle_count += 1

    def increase_total_waiting_time(self, amount: int) -> None:
        self._total_waiting_time += amount

    def get_total_waiting_time(self) -> int:
        return self._total_waiting_time

    def update_avg_speed_in_segment(self, speed: float) -> None:
        self._increase_leaving_vehicle_count()
        self._avg_speed_in_segment = (
            (self._avg_speed_in_segment * (self._leaving_vehicle_count - 1) + speed)
            / self._leaving_vehicle_count)

    def get_avg_speed_in_segment(self) -> float:
        return self._avg_speed_in_segment

    def get_entering_vehicle_count(self) -> int:
        return self._entering_vehicle_count

    def get_leaving_vehicle_count(self) -> int:
        return self._leaving_vehicle_count

    def get_near_crash_count(self) -> int:
        return self._near_crash_count

    def increase_near_crash_count(self) -> None:
        self._near_crash_count += 1

    def get_strip_count(self) -> int:
        return self._strip_count

    def _initialize(self) -> None:
        self._strip_count = 2 + jint(math.floor(
            (self._segment_width - 2 * Parameters.footpath_strip_width)
            / Parameters.strip_width))
        for i in range(self._strip_count):
            if i == 0:
                self._strip_list.append(Strip(self._index, i, True, self._parent_link_id))
            elif i == self._strip_count - 1:
                self._strip_list.append(Strip(self._index, i, True, self._parent_link_id))
            else:
                self._strip_list.append(Strip(self._index, i, False, self._parent_link_id))

        self.middle_high_strip_index = jint(math.ceil(self._strip_count / 2.0))
        if self._strip_count % 2 == 0:
            self.middle_low_strip_index = self.middle_high_strip_index - 1
        else:
            self.middle_low_strip_index = self.middle_high_strip_index - 2
        self.last_vehicle_strip_index = self._strip_count - 2

    def get_link_index(self) -> int:
        return self._link_index

    def set_link_index(self, link_index: int) -> None:
        self._link_index = link_index

    def get_index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index

    def get_id(self) -> int:
        return self._id

    def set_id(self, id_: int) -> None:
        self._id = id_

    def get_start_x(self) -> float:
        return self._start_x

    def set_start_x(self, start_x: float) -> None:
        self._start_x = start_x

    def get_start_y(self) -> float:
        return self._start_y

    def set_start_y(self, start_y: float) -> None:
        self._start_y = start_y

    def get_end_x(self) -> float:
        return self._end_x

    def set_end_x(self, end_x: float) -> None:
        self._end_x = end_x

    def get_end_y(self) -> float:
        return self._end_y

    def set_end_y(self, end_y: float) -> None:
        self._end_y = end_y

    def get_segment_width(self) -> float:
        return self._segment_width

    def set_segment_width(self, segment_width: float) -> None:
        self._segment_width = segment_width

    def is_last_segment(self) -> bool:
        return self._last_segment

    def set_last_segment(self, last_segment: bool) -> None:
        self._last_segment = last_segment

    def is_first_segment(self) -> bool:
        return self._first_segment

    def set_first_segment(self, first_segment: bool) -> None:
        self._first_segment = first_segment

    def get_sensor(self) -> float:
        return self._sensor

    def set_sensor(self, sensor: float) -> None:
        self._sensor = sensor

    def get_vehicle_count_at_sensor(self) -> int:
        return self._sensor_vehicle_count

    def set_vehicle_count_at_sensor(self, vehicle_count_at_sensor: int) -> None:
        self._sensor_vehicle_count = vehicle_count_at_sensor

    def get_average_speed_at_sensor(self) -> float:
        return self._sensor_vehicle_avg_speed

    def set_average_speed_at_sensor(self, average_speed_at_sensor: float) -> None:
        self._sensor_vehicle_avg_speed = average_speed_at_sensor

    def set_accident_count(self, accident_count: int) -> None:
        self._accident_count = accident_count

    def get_strip(self, index: int) -> Strip:
        # Java throws IndexOutOfBoundsException for an out of range index;
        # Python would silently wrap around for negatives, so guard here.
        if index < 0 or index >= len(self._strip_list):
            raise IndexError(f"strip index {index} out of range "
                             f"[0, {len(self._strip_list)})")
        return self._strip_list[index]

    def number_of_strips(self) -> int:
        return len(self._strip_list)

    def get_length(self) -> float:
        return get_distance(self._start_x, self._start_y, self._end_x, self._end_y)

    def update_information(self, speed: float) -> None:
        self._sensor_vehicle_count += 1
        self._sensor_vehicle_avg_speed = (
            (self._sensor_vehicle_avg_speed * (self._sensor_vehicle_count - 1) + speed)
            / self._sensor_vehicle_count)

    def get_strip_index_in_entering_segment(self, vehicle, intended_entering_strip_index
                                           ) -> int:
        """:return: the strip index where the vehicle can be added; -1 if
        there is no strip available"""
        reverse = intended_entering_strip_index >= self.middle_high_strip_index

        if not reverse:  # straight
            begin_limit = 1
            end_limit = self.middle_low_strip_index - (vehicle.get_number_of_strips() - 1)
        else:
            begin_limit = self.middle_high_strip_index
            end_limit = (self.last_vehicle_strip_index
                         - (vehicle.get_number_of_strips() - 1))
        for i in range(begin_limit, end_limit + 1):
            flag = True
            for j in range(vehicle.get_number_of_strips()):
                if not self.get_strip(i + j).has_gap_for_adding_vehicle(
                        vehicle.get_length()):
                    flag = False
                    break
            if flag:
                return i
        return -1

    def draw(self, g) -> None:
        x1 = jint(jround(self._start_x * Parameters.pixel_per_meter))
        y1 = jint(jround(self._start_y * Parameters.pixel_per_meter))
        x2 = jint(jround(self._end_x * Parameters.pixel_per_meter))
        y2 = jint(jround(self._end_y * Parameters.pixel_per_meter))
        w = self._segment_width * Parameters.pixel_per_meter
        x3 = jint(jround(return_x3(x1, y1, x2, y2, w)))
        y3 = jint(jround(return_y3(x1, y1, x2, y2, w)))
        x4 = jint(jround(return_x4(x1, y1, x2, y2, w)))
        y4 = jint(jround(return_y4(x1, y1, x2, y2, w)))
        g.set_color(Constants.road_border_color)
        g.set_stroke(15)
        g.draw_line(x1, y1, x2, y2)
        g.draw_line(x3, y3, x4, y4)

    def update_sensor_info(self, new_speed: float) -> None:
        """Counts vehicles passing the sensor and updates the average speed."""
        self._sensor_vehicle_count += 1
        self._sensor_vehicle_avg_speed = (
            self._sensor_vehicle_avg_speed / self._sensor_vehicle_count
            * (self._sensor_vehicle_count - 1)
            + new_speed / self._sensor_vehicle_count)

    def get_sensor_vehicle_count(self) -> int:
        return self._sensor_vehicle_count

    def get_sensor_vehicle_avg_speed(self) -> float:
        return self._sensor_vehicle_avg_speed

    def update_accidentcount(self) -> None:
        self._accident_count += 1

    def get_accident_count(self) -> int:
        # Java: ``return accidentCount++;`` -- the getter increments the field
        # as a side effect.  Kept because it changes the reported numbers.
        value = self._accident_count
        self._accident_count += 1
        return value
