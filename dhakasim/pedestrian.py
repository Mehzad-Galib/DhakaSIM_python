"""Port of ``thesisfinal.Pedestrian``.

@author USER
"""

from __future__ import annotations

import os

from .constants import Constants
from .javacompat import Color, jbool_str, jformat, jint, jround
from .parameters import Parameters
from .utilities import return_x3, return_y3


class Pedestrian:
    __slots__ = ("_pedestrian_id", "_start_time", "to_remove", "_segment", "_init_pos",
                 "_distance", "_speed", "_strip", "in_accident", "_reverse", "clock",
                 "index")

    def __init__(self, pedestrian_id: int, seg, strip: int, initpos: float, sp: float):
        self._pedestrian_id = pedestrian_id
        self.to_remove = False
        self.in_accident = False
        self.clock = Parameters.simulation_step
        self.index = 0
        if strip != 0:
            self._reverse = True
            self._strip = seg.get_strip(seg.number_of_strips() - 1)
        else:
            self._reverse = False
            self._strip = seg.get_strip(0)
        self._segment = seg
        self._init_pos = initpos
        self._distance = 0
        self._speed = sp
        self._strip.add_pedestrian(self)
        self._start_time = Parameters.simulation_step

    def clean_up(self) -> None:
        self._strip.del_pedestrian(self)
        self.to_remove = True

    def is_stuck(self) -> bool:
        return (Parameters.simulation_step - self._start_time
                >= ((self._segment.get_segment_width() / self._speed)
                    * Constants.TIME_STEP))

    def __str__(self) -> str:
        return ("Pedestrian{"
                + "pedestrianId=" + str(self._pedestrian_id)
                + ", initPos=" + str(self._init_pos)
                + ", distance=" + str(self._distance)
                + ", speed=" + str(self._speed)
                + ", strip=" + str(self._strip.get_strip_index())
                + ", distanceInSegment= " + str(self.get_distance_in_segment())
                + "}")

    def print_pedestrian_details(self) -> None:
        if Parameters.DEBUG_MODE:
            if self._pedestrian_id in (5899, -711):
                pathname = "debug/p_debug" + str(self._pedestrian_id) + ".txt"
                os.makedirs("debug", exist_ok=True)
                with open(pathname, "a") as writer:
                    writer.write("Sim step: " + str(Parameters.simulation_step) + "\n")
                    writer.write("Pedestrian ID: " + str(self._pedestrian_id) + "\n")
                    writer.write("Init Pos: " + str(self._init_pos) + "\n")
                    writer.write("Speed: " + jformat(self._speed, 2) + "\n")
                    writer.write("Distance in Segment: "
                                 + jformat(self.get_distance_in_segment(), 2) + "\n")
                    writer.write("Strip Index: "
                                 + str(self._strip.get_strip_index()) + "\n")
                    writer.write("\n")

    def get_pedestrian_id(self) -> int:
        return self._pedestrian_id

    def _get_distance(self) -> float:
        return self._distance

    def get_speed(self) -> float:
        return self._speed

    def get_init_pos(self) -> float:
        return self._init_pos

    def get_reverse_segment(self) -> bool:
        return self._strip.get_strip_index() > self._segment.middle_low_strip_index

    def get_distance_in_segment(self) -> float:
        reverse_segment = self.get_reverse_segment()

        if reverse_segment:
            return self._segment.get_length() - self._init_pos
        return self._init_pos

    def get_segment(self):
        return self._segment

    # TODO ????
    def move_length_wise(self) -> bool:
        reverse_segment = self.get_reverse_segment()
        if reverse_segment:
            if self._strip.has_gap_for_move_along_positive(self):
                self._init_pos += self._speed
                return True
        else:
            if self._strip.has_gap_for_move_along_negative(self):
                self._init_pos -= self._speed
                return True
        return False

    def move_forward(self) -> bool:
        if not self._reverse:
            if self._distance + self._speed < Parameters.footpath_strip_width:
                self._distance += self._speed
                return True
            x = 1 + jint((self._distance - Parameters.footpath_strip_width + self._speed)
                         / Parameters.strip_width)
            if x < self._segment.number_of_strips():
                if self._segment.get_strip(x).has_gap_for_pedestrian(self):
                    self._distance = self._distance + self._speed
                    self._strip.del_pedestrian(self)
                    self._set_strip(self._segment.get_strip(x))
                    self._strip.add_pedestrian(self)
                    return True
                return False
            else:
                self._distance = self._distance + self._speed
                self._strip.del_pedestrian(self)
                return True
        else:
            w = (1 * Parameters.footpath_strip_width
                 + (self._segment.number_of_strips() - 1) * Parameters.strip_width)
            x = 1 + jint((w - self._distance - self._speed
                          - Parameters.footpath_strip_width) / Parameters.strip_width)
            if x > 1:
                if self._segment.get_strip(x).has_gap_for_pedestrian(self):
                    self._distance = self._distance + self._speed
                    self._strip.del_pedestrian(self)
                    self._set_strip(self._segment.get_strip(x))
                    self._strip.add_pedestrian(self)
                    return True
                return False
            else:
                self._distance = self._distance + self._speed
                self._strip.del_pedestrian(self)
                return True

    def get_strip(self):
        return self._strip

    def set_segment(self, segment) -> None:
        self._segment = segment

    def _set_strip(self, strip) -> None:
        self._strip = strip

    def has_crossed_road(self) -> bool:
        return self._distance >= self._segment.get_segment_width()

    def draw_mobile_pedestrian(self, trace_writer, g, strip_pixel_count, mp_ratio,
                              fp_strip_pixel_count) -> None:
        seg = self.get_segment()
        segment_length = seg.get_length()
        length = 1
        # Using the internal section (ratio) formula, find the coordinates
        # along which the pedestrian is
        xp = ((self.get_init_pos() * seg.get_end_x()
               + (segment_length - self.get_init_pos()) * seg.get_start_x())
              / segment_length * mp_ratio)
        yp = ((self.get_init_pos() * seg.get_end_y()
               + (segment_length - self.get_init_pos()) * seg.get_start_y())
              / segment_length * mp_ratio)
        xq = (((self.get_init_pos() + length) * seg.get_end_x()
               + (segment_length - (self.get_init_pos() + length)) * seg.get_start_x())
              / segment_length * mp_ratio)
        yq = (((self.get_init_pos() + length) * seg.get_end_y()
               + (segment_length - (self.get_init_pos() + length)) * seg.get_start_y())
              / segment_length * mp_ratio)

        if not self._reverse:
            offset = ((self._get_distance() / Parameters.footpath_strip_width)
                      * fp_strip_pixel_count)
            accident_size = 1.5
        else:
            w = (1 * Parameters.footpath_strip_width
                 + (self._segment.number_of_strips() - 1) * Parameters.strip_width)
            wi = jint(w - self._get_distance())
            offset = (wi / Parameters.footpath_strip_width) * fp_strip_pixel_count
            accident_size = 1.2

        x1 = jint(jround(return_x3(xp, yp, xq, yq, offset)))
        y1 = jint(jround(return_y3(xp, yp, xq, yq, offset)))
        if self.in_accident:
            g.set_color(Color.RED)
            g.fill_oval(x1, y1, jint(accident_size * mp_ratio), jint(accident_size * mp_ratio))
        else:
            g.set_color(Constants.pedestrian_color)
            g.fill_oval(x1, y1, jint(0.4 * mp_ratio), jint(0.4 * mp_ratio))

        if Parameters.DEBUG_MODE:
            g.set_font("Serif", 64)
            g.draw_string(str(self._pedestrian_id), x1, y1)
        if trace_writer is not None:
            trace_writer.write(f"{x1} {y1} {jbool_str(self.in_accident)}\n")

    def print_object(self) -> None:
        print(f"{self.index} {self._init_pos} {self._distance} {self._speed}")

    def is_to_remove(self) -> bool:
        return self.to_remove

    def is_in_accident(self) -> bool:
        return self.in_accident

    def set_to_remove(self, b: bool) -> None:
        self.to_remove = b
