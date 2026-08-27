"""Port of ``thesisfinal.Vehicle``.

Holds every car-following and lane-changing model in the simulator.  The
static ``getXxxAcceleration`` helpers are :func:`staticmethod` here, exactly as
in Java, because :mod:`dhakasim.strip` calls some of them directly.
"""

from __future__ import annotations

import math
import atexit
import os
from collections import deque

from .constants import Constants
from .javacompat import (Color, DOUBLE_MAX_VALUE, NaN, jdiv, jexp, jformat, jint, jmax,
                         jmin, jpow, jround, jsqrt, jtanh, jto_radians)
from .parameters import CAR_FOLLOWING_MODEL, DLC_MODEL, Parameters
from .point2d import Point2D
from .signal import SIGNAL
from .statistics import Statistics
from .vehicle_stats import VehicleStats
from . import utilities as Utilities

PEDESTRIANS_ALONG_THE_ROAD_TYPE = Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE


_accident_log_handle = None
_accident_log_path = None


def accident_log():
    """The accident log, opened once and left open.

    A near crash is a common event -- a few thousand a minute at Khamarbari --
    and this used to make a directory, open the file, append one line and
    close it again for every one of them.  That was about eight per cent of a
    simulation step spent on file handles rather than on traffic.  The file is
    still opened for append and never truncated, which is what the rest of
    ``statistics/csv`` promises.
    """
    global _accident_log_handle, _accident_log_path
    path = os.path.join(Parameters.STATS_DIR, "csv", "accident_log.csv")
    if path != _accident_log_path:
        # STATS_DIR can change between runs in one process, and the old handle
        # is then pointing at the wrong file.
        if _accident_log_handle is not None:
            _accident_log_handle.close()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _accident_log_handle = open(path, "a")
        _accident_log_path = path
    return _accident_log_handle


@atexit.register
def _close_accident_log():
    if _accident_log_handle is not None:
        _accident_log_handle.close()


class Vehicle:
    MARGIN = 1.0                                   # margin from segment end in meter
    TIME_STEP = Constants.TIME_STEP                # second
    SAFE_TIME_GAP = 0.0                            # second
    LAMBDA = 0.78                                  # lane changing gap acceptance parameter
    TIME_WINDOW = 4
    GAP_WINDOW = 3
    THRESHOLD_DISTANCE = Constants.THRESHOLD_DISTANCE  # default gap in meter between two standstill vehicles
    REACTION_TIME = Constants.TIME_STEP            # second
    ALPHA = 15.0                                   # sensitivity co-efficient for GHR/General Motors DLC model
    M = 1.0                                        # speed exponent of GHR/General Motors DLC model
    L = 2.0                                        # speed exponent of GHR/General Motors DLC model
    # Emergency-braking limit for the modified Newtonian model only, from
    # Kudarauskas' measurements.  Deliberately not `_max_braking` (-6, per
    # vehicle): that one is the comfortable deceleration the other twelve
    # models negotiate around, whereas this is the physical floor a vehicle
    # cannot brake harder than.
    MAX_DECELERATION = -8.5                        # m/s^2

    __slots__ = ("_vehicle_id", "_start_time", "_type", "_length", "_width",
                 "_number_of_strips", "_speed", "_acceleration", "_strip_index",
                 "_distance_in_segment", "_maximum_speed_capable", "_current_max_speed",
                 "_max_acceleration", "_max_braking", "_color", "_vehicle_corners",
                 "_demand_index", "_path_index", "_link_index_on_path",
                 "_segment_corners", "_segment_start_point", "_segment_end_point",
                 "_is_in_intersection", "_reverse_link", "_reverse_segment",
                 "_passed_sensor", "_to_remove", "_link", "_segment_index", "_node",
                 "_intersection_strip_index", "_distance_in_intersection", "_leader",
                 "_end_time", "_segment_enter_time", "_segment_leave_time",
                 "_waiting_time", "_waiting_time_in_segment", "_distance_traveled",
                 "_signal_on_link", "_prev_speeds", "_prev_gaps", "_dlc_model",
                 "_has_collided", "_collision_time", "_collision_penalty",
                 "_no_force_move", "_stuck_in_intersection", "_fuel_consumption",
                 "_penalty_for_collision", "_vehicle_stats")

    @classmethod
    def _dummy(cls) -> "Vehicle":
        """The private no-argument Java constructor, used for dummy vehicles."""
        v = cls.__new__(cls)
        v._vehicle_id = -5
        v._start_time = -1
        # Java default field values
        v._type = 0
        v._length = 0.0
        v._width = 0.0
        v._number_of_strips = 0
        v._speed = 0.0
        v._acceleration = 0.0
        v._strip_index = 0
        v._distance_in_segment = 0.0
        v._maximum_speed_capable = 0.0
        v._current_max_speed = 0.0
        v._max_acceleration = 0.0
        v._max_braking = 0.0
        v._color = None
        v._vehicle_corners = None
        v._demand_index = 0
        v._path_index = 0
        v._link_index_on_path = 0
        v._segment_corners = None
        v._segment_start_point = None
        v._segment_end_point = None
        v._is_in_intersection = False
        v._reverse_link = False
        v._reverse_segment = False
        v._passed_sensor = False
        v._to_remove = False
        v._link = None
        v._segment_index = 0
        v._node = None
        v._intersection_strip_index = 0
        v._distance_in_intersection = 0.0
        v._leader = None
        v._end_time = 0
        v._segment_enter_time = 0
        v._segment_leave_time = 0
        v._waiting_time = 0
        v._waiting_time_in_segment = 0
        v._distance_traveled = 0.0
        v._signal_on_link = None
        v._prev_speeds = None
        v._prev_gaps = None
        v._dlc_model = None
        v._has_collided = False
        v._collision_time = 0
        v._collision_penalty = 0
        v._no_force_move = False
        v._stuck_in_intersection = 0
        v._fuel_consumption = 0.0
        v._penalty_for_collision = 0.0
        v._vehicle_stats = None
        return v

    def __init__(self, vehicle_id, start_time, type_, color, demand_index, path_index,
                 link_index_on_path, seg_start_x, seg_start_y, seg_end_x, seg_end_y,
                 reverse_link, reverse_segment, link, segment_index, strip_index):
        self._vehicle_id = vehicle_id
        self._start_time = start_time
        self._segment_enter_time = start_time
        self._distance_traveled = 0.0
        self._type = type_

        self._length = Utilities.get_car_length(type_)
        self._width = Utilities.get_car_width(type_)
        self._maximum_speed_capable = Utilities.get_car_max_speed(type_)
        self._current_max_speed = jmin(Parameters.maximum_speed,
                                      self._maximum_speed_capable)
        self._number_of_strips = Utilities.number_of_strips(type_)

        self._speed = 0  # Utilities.get_car_max_speed(type_)
        self._max_acceleration = Utilities.get_car_acceleration(type_)
        self._acceleration = self._max_acceleration
        self._max_braking = -6  # -1.5 * max_acceleration
        self._color = color
        self._demand_index = demand_index
        self._path_index = path_index
        self._link_index_on_path = link_index_on_path
        self._segment_start_point = Point2D(0, 0)
        self._segment_end_point = Point2D(0, 0)
        self._segment_start_point.x = seg_start_x
        self._segment_start_point.y = seg_start_y
        self._segment_end_point.x = seg_end_x
        self._segment_end_point.y = seg_end_y

        self._is_in_intersection = False

        self._reverse_link = reverse_link
        self._reverse_segment = reverse_segment

        self._passed_sensor = False
        self._to_remove = False

        self._has_collided = False
        self._collision_time = -1
        self._collision_penalty = 0

        self._no_force_move = False
        self._stuck_in_intersection = 0

        self._link = link
        self._segment_index = segment_index
        self._strip_index = strip_index
        self._distance_in_segment = 0.1
        self._vehicle_corners = [None] * 4

        self._segment_corners = [None] * 4

        self._prev_speeds = deque()
        self._prev_gaps = deque()
        self._fuel_consumption = 0.0
        self._penalty_for_collision = 0.0
        self._dlc_model = Utilities.get_dlc_model(type_)

        self._node = None
        self._leader = None
        self._signal_on_link = None
        self._intersection_strip_index = 0
        self._distance_in_intersection = 0.0
        self._end_time = 0
        self._segment_leave_time = 0
        self._waiting_time = 0
        self._waiting_time_in_segment = 0

        self._vehicle_stats = VehicleStats(self._vehicle_id,
                                           Parameters.simulation_end_time)

        self._occupy_strips()
        self._increase_vehicle_count_on_segment()
        self.get_segment().increase_entering_vehicle_count()

    def get_dlc_model(self):
        return self._dlc_model

    def get_vehicle_stats(self):
        return self._vehicle_stats

    # ------------------------------------------------------------------
    # car following models
    # ------------------------------------------------------------------

    @staticmethod
    def get_speed_for_braking(leader, follower, dx=None) -> float:
        if dx is None:
            dx = Vehicle.get_dx(leader, follower, 1)
        d_n_mx = follower._max_braking
        d_T = d_n_mx * Vehicle.REACTION_TIME
        v_n_t = follower._speed
        v_n_1_t = leader._speed
        d_n_1 = Vehicle._get_leaders_perceived_deceleration(leader, follower)
        return d_T + jsqrt(d_T * d_T - d_n_mx
                           * (2 * dx - v_n_t * Vehicle.REACTION_TIME
                              - jdiv(v_n_1_t * v_n_1_t, d_n_1)))

    @staticmethod
    def _get_gipps_acceleration(leader, follower) -> float:
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_gap(leader, follower)
        dt = Vehicle.TIME_STEP
        v_len = follower._length

        a = follower._max_acceleration
        b = follower._max_braking
        bcap = -3  # according to TA sir
        s = v_len + Vehicle.THRESHOLD_DISTANCE
        Vn = follower._current_max_speed

        term1 = Vb + 2.5 * a * dt * (1 - jdiv(Vb, Vn)) * jsqrt(0.025 + jdiv(Vb, Vn))
        term2 = ((b * b) * (dt * dt)
                 - b * (2 * (gap + v_len - s) - Vb * dt - jdiv(Vf * Vf, bcap)))
        if term2 >= 0:
            term2 = b * dt + jsqrt(term2)
            vb_new = jmin(term1, term2)
            acc = (vb_new - Vb) / dt
        else:
            acc = b

        return acc

    @staticmethod
    def _get_modified_newtonian_acceleration(leader, follower) -> float:
        """Acceleration for the modified Newtonian model.

        The plain Newtonian model moves at full acceleration or brakes by
        whatever it takes to close the remaining gap in one step -- two
        branches, and the braking one is unbounded.  This model replaces both
        with a single continuous acceleration, which is what lets it decelerate
        smoothly, and bounds it below at :data:`Vehicle.MAX_DECELERATION`.

        The value comes from asking what constant acceleration would leave the
        vehicle exactly at the leader's tail after one step: solving
        ``v*dt + 0.5*a*dt^2 = dx`` for ``a``.  ``get_dx`` already nets off the
        leader's effective length and the standstill threshold, so ``dx`` is
        the free space ahead.
        """
        dt = Vehicle.TIME_STEP
        dx = Vehicle.get_dx(leader, follower, 1)
        acc = jdiv(2 * (dx - follower._speed * dt), dt * dt)
        # Never accelerate harder than the vehicle can, nor brake harder than
        # physics allows; between those it is free to pick any rate, unlike the
        # naive model's all-or-nothing.
        return jmax(Vehicle.MAX_DECELERATION, jmin(follower._max_acceleration, acc))

    @staticmethod
    def _get_krauss_acceleration(leader, follower) -> float:
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, 1)
        dt = Vehicle.TIME_STEP

        a = follower._max_acceleration
        b = -follower._max_braking  # according to TA sir's code b is positive
        s0 = Vehicle.THRESHOLD_DISTANCE  # noqa: F841 - unused in Java too
        Vn = follower._current_max_speed
        # tau = 1.0
        tau = 0.3

        gap_des = Vf * tau
        v_safe = Vf + jdiv(gap - gap_des, jdiv(Vb + Vf, 2 * b) + tau)
        v_des = jmin(Vb + a * dt, jmin(v_safe, Vn))
        vb_new = jmax(0, v_des)  # this is the krauss speed
        return (vb_new - Vb) / dt

    @staticmethod
    def _get_gfm_acceleration(leader, follower) -> float:
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, 1)

        s0 = Vehicle.THRESHOLD_DISTANCE  # safe distance = THRESHOLD_DISTANCE for me
        Vmax = follower._current_max_speed
        T = 1.5
        toud = 1.5
        toua = 9
        Ra = 15
        Rd = 80

        safe_dist = s0 + Vb * T
        vdelx = Vmax * (1 - jexp(-(gap - safe_dist) / Ra))

        acc = (vdelx - Vb) / toua
        if Vb > Vf:
            acc = acc - (Vb - Vf) * jexp(-(gap - safe_dist) / Rd) / toud
        return acc

    @staticmethod
    def get_idm_acceleration(leader, follower) -> float:
        if follower is None:
            return 0
        elif leader is None:
            return follower._max_acceleration
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, 1)

        a = 3
        b = 4
        s0 = Vehicle.THRESHOLD_DISTANCE
        Vmax = follower._current_max_speed
        T = 1.5
        c = 2 * math.sqrt(a * b)

        s_star = s0 + jmax(0, Vb * T + jdiv(Vb * (Vb - Vf), c))
        acc = a * (1 - jpow(jdiv(Vb, Vmax), 4) - jpow(jdiv(s_star, gap), 2))
        return jmax(-6, acc)

    @staticmethod
    def _get_rvf_acceleration(leader, follower) -> float:
        Vbs = [0.0] * Vehicle.TIME_WINDOW
        Vfs = [0.0] * Vehicle.TIME_WINDOW
        for i, d in enumerate(follower._prev_speeds):
            Vbs[i] = d
        for i, d in enumerate(leader._prev_speeds):
            Vfs[i] = d

        gap = Vehicle.get_dx(leader, follower, 1)
        v_len = follower._length  # noqa: F841 - unused in Java too
        dt = Vehicle.TIME_STEP

        cappa = 2.0
        V1 = 6.75
        V2 = 7.91
        C1 = 0.13
        C2 = 1.57
        lambda_ = 0.5
        gamma = 0.2
        L = Vehicle.TIME_WINDOW  # sir used 4 but here we used 5

        vdelx = V1 + V2 * jtanh(jto_radians(C1 * gap - C2))
        # Vbs[0] is the previous speed so the current speed is used here
        acc = cappa * (vdelx - follower._speed)
        vf_avg = 0.0

        for i in range(Vehicle.TIME_WINDOW):
            vf_avg += Vfs[i] - Vbs[i]

        vf_avg /= L
        acc = (acc + lambda_ * (leader._speed - follower._speed)
               + gamma * (leader._speed - follower._speed - vf_avg))
        v_new = jmax(0, follower._speed + acc * dt)
        return (v_new - follower._speed) / dt

    @staticmethod
    def _get_vfiac_acceleration(leader, follower) -> float:
        Vb = follower._speed
        Vfs = [0.0] * Vehicle.TIME_WINDOW
        for i, d in enumerate(leader._prev_speeds):
            Vfs[i] = d
        gap = Vehicle.get_dx(leader, follower, 1)
        dt = Vehicle.TIME_STEP

        cappa = 0.41
        V1 = 6.75
        V2 = 7.91
        C1 = 0.13
        C2 = 1.57
        lambda_ = 0.5
        gamma = 0.03
        m = Vehicle.TIME_WINDOW

        vdelx = V1 + V2 * jtanh(jto_radians(C1 * gap - C2))
        acc = cappa * (vdelx - Vb)
        vf_avg = 0.0

        for d in Vfs:
            vf_avg += d
        vf_avg /= m
        acc += lambda_ * (leader._speed - Vb) + gamma * (leader._speed - vf_avg)
        v_new = jmax(0, Vb + acc * dt)
        return (v_new - Vb) / dt

    @staticmethod
    def _get_ovcm_acceleration(leader, follower) -> float:
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, 1)
        gaps = list(follower._prev_gaps)
        dt = Vehicle.TIME_STEP

        Vmax = follower._current_max_speed
        a = 2.3
        lc = follower._length
        hc = 4
        lambda_ = 0.1
        mem_time_step = 2  # TODO dt = 1 then what???
        step = jint(mem_time_step / dt)
        gamma = 0.1

        vdelx = Vmax / 2 * (jtanh(jto_radians(gap + lc - hc)) + jtanh(jto_radians(hc)))
        vdelx2 = vdelx
        if len(gaps) > step:
            vdelx2 = (Vmax / 2 * (jtanh(jto_radians(gaps[step - 1] + lc - hc))
                                  + jtanh(jto_radians(hc))))
        return a * (vdelx - Vb) + lambda_ * (Vf - Vb) + gamma * (vdelx - vdelx2)

    @staticmethod
    def _get_kftm_acc_single_leader(leader, follower, multfactor, leader_no) -> float:
        ALPHA = Parameters.ALPHA  # 0.92, 0.93, ..., 1.09, 1.10
        BETA = Parameters.BETA    # 0.1, 0.2, 0.3, ..., 1.9, 2.0
        ETA = Parameters.ETA      # 0, 1, 2, ..., 9, 10

        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, leader_no)
        v_len = follower._length
        dt = Vehicle.TIME_STEP

        a = 3
        b = -4
        bcap = -4
        s = v_len + 2
        s0 = Vehicle.THRESHOLD_DISTANCE
        Vn = follower._current_max_speed
        c = 2 * math.sqrt(a * abs(b))
        T = 0.9

        s_star = (s0 + ETA + jmax(0, Vb * T + jdiv(Vb * (Vb - Vf), c))) * BETA

        va_nm = Vb + a * dt * (1 - jpow(jdiv(Vb, Vn), 4)
                               - jpow(jdiv(multfactor * s_star, gap) * jdiv(Vb, Vn), 2))

        part1 = (b * b) * (dt * dt)
        part2 = 2 * (gap + v_len - s) - Vb * dt - jdiv(Vf * Vf, bcap)
        term2 = part1 - b * part2

        if term2 >= 0:
            vb_nm = b * dt + jsqrt(term2)
        else:
            vb_nm = b * dt + Vb
        vb_nm = ALPHA * vb_nm

        vb_new = jmax(0, jmin(va_nm, vb_nm))

        return (vb_new - Vb) / dt  # acceleration

    @staticmethod
    def _get_kftm_acceleration(leader, follower) -> float:
        look_ahead_dist = 300

        l = leader
        min_acc = Vehicle._get_kftm_acc_single_leader(leader, follower, 1, 1)

        for i in range(1, 3):
            if (l is not None
                    and l._distance_in_segment - follower._distance_in_segment
                    <= look_ahead_dist):
                temp_acc = Vehicle._get_kftm_acc_single_leader(l, follower, i + 1, i + 1)
                min_acc = jmin(temp_acc, min_acc)

                l = l.get_probable_leader()
            else:
                break
        return min_acc

    # TODO
    @staticmethod
    def _get_hdm_acceleration(leader, follower) -> float:
        num_leader_to_consider = 3

        # follower's leader, then his leader, his leader, ...
        leaders = [leader]
        count = 1
        l = leader
        while count <= num_leader_to_consider:
            l = l._get_a_leader_as_necessary()
            if l is None:
                break
            leaders.append(l)
            count += 1

        num_of_actual_leaders = len(leaders)

        # in index 0 -> my vehicle (follower)
        #    index 1 -> leader of 0
        #    index 2 -> leader of 1
        #    .....
        # for both speeds and gap (different from Tanveer sir)
        speeds = [0.0] * (num_of_actual_leaders + 1)
        gaps = [0.0] * (num_of_actual_leaders + 1)

        speeds[0] = follower._speed
        for i in range(1, num_of_actual_leaders + 1):
            speeds[i] = leaders[i - 1]._speed

        gaps[0] = Vehicle.get_dx(leader, follower, 1)
        for i in range(1, num_of_actual_leaders):
            l1 = leaders[i]
            f1 = leaders[i - 1]
            gaps[i] = Vehicle.get_dx(l1, f1, i + 1)  # gap between f1 and l1

        farthest_car = leaders[num_of_actual_leaders - 1]
        leader_of_the_farthest_car = farthest_car._get_a_leader_as_necessary()
        if leader_of_the_farthest_car is None:
            gaps[num_of_actual_leaders] = 1000
        else:
            gaps[num_of_actual_leaders] = Vehicle.get_dx(
                leader_of_the_farthest_car, farthest_car, num_of_actual_leaders)

        return Vehicle.get_hdm_acc_helper(follower, num_of_actual_leaders,
                                         speeds, gaps, Vehicle.TIME_STEP)

    @staticmethod
    def get_hdm_acc_helper(follower, num_leaders, speeds, gaps, dt) -> float:
        acc = follower._acceleration

        a = follower._max_acceleration
        b = follower._max_braking
        s0 = Vehicle.THRESHOLD_DISTANCE

        v_max = follower._current_max_speed
        T = 1.5
        c = 2 * math.sqrt(a * abs(b))

        fs = follower._speed

        acc_interaction = [0.0] * num_leaders

        v_next = fs + acc * dt
        v_next = jmax(0, jmin(v_next, v_max))

        for i in range(1, num_leaders + 1):
            del_v = fs - speeds[i]
            s_star = s0 + jmax(0, v_next * T + jdiv(v_next * del_v, c))

            gap = gaps[0] + dt * (fs - speeds[1])
            for j in range(1, i):
                gap += gaps[j]

            acc_interaction[i - 1] = jdiv(s_star, gap) * jdiv(s_star, gap)

        total = 0.0
        for d in acc_interaction:
            total += d

        res_acc = a * (1 - jpow(jdiv(v_next, v_max), 4)) - a * total
        return jmax(follower._max_braking, res_acc)

    @staticmethod
    def get_sbm_acceleration(leader, follower) -> float:
        Vf = leader._speed  # Vf = leader speed
        Vb = follower._speed  # preceding car
        gap = Vehicle.get_dx(leader, follower, 1)
        v_len = follower._length
        delta_t = Vehicle.TIME_STEP

        v_max = follower._current_max_speed
        gamma = 2.0
        acc_max_n = 3.0
        d_jam = 2.0

        d_rep = (jdiv(Vb, 2.5 + 0.1 * Vb) * v_len + d_jam
                 + (0.0 + Parameters.random.next_gaussian() * 0.05))
        d_par = gamma * d_rep

        if (Vb - Vf) > jdiv(gap + v_len, 2 * Vb):
            phi = 1
        else:
            phi = 2.4

        if (gap + v_len) < d_rep and Vf != 0:
            vb_new = (Vb + jdiv(gap + v_len - d_rep, phi * delta_t)
                      + (0.0 + Parameters.random.next_gaussian() * 0.05))
            acc = (vb_new - Vb) / delta_t
        elif d_rep < (gap + v_len) < d_par:
            val = (0.1 + Parameters.random.next_gaussian() * jdiv(Vf, v_max))
            vb_new = Vf * val
            acc = (vb_new - Vb) / delta_t
        elif (gap + v_len) > d_par:
            v_feasible = Vb + acc_max_n * delta_t
            vb_new = jmin(v_max, v_feasible)
            vb_new = jmin(vb_new, jdiv(Vf * (gap + v_len), v_len))
            acc = (vb_new - Vb) / delta_t
        else:
            acc = 2

        return jmax(-6, acc)

    @staticmethod
    def get_dx(leader, follower, leader_no) -> float:
        """:param leader: vehicle in front (1st, 2nd, 3rd, 4th vehicle in front)
        :param follower: current vehicle (0th vehicle)
        :param leader_no: 1 means immediate leader; 2 means leader of leader; ...
        :return: gap between leader and follower
        """
        if Parameters.ERROR_MODE:
            if Parameters.FT_METHOD == 1:
                return Vehicle.get_dx_with_error(leader, follower, leader_no,
                                                 Parameters.NO_OF_READINGS, 0)
            elif Parameters.FT_METHOD == 2:
                return Vehicle.get_dx_with_error(leader, follower, leader_no, 1,
                                                 Parameters.M_FACTOR)
            elif Parameters.FT_METHOD == 3:
                return Vehicle.get_dx_with_error(leader, follower, leader_no,
                                                 Parameters.NO_OF_READINGS,
                                                 Parameters.M_FACTOR)
            else:
                return 0  # should never come here. FT_METHOD should be 1, 2, or 3
        return Vehicle.get_gap(leader, follower)

    @staticmethod
    def get_gap(leader, follower) -> float:
        x_n_1 = leader._distance_in_segment
        x_n = follower._distance_in_segment + follower._length
        # TODO look here
        return x_n_1 - Vehicle.THRESHOLD_DISTANCE - x_n

    @staticmethod
    def get_object_gap(leader, follower) -> float:
        x_n_1 = leader.get_distance_in_segment()
        x_n = follower._distance_in_segment + follower._length
        return x_n_1 - Vehicle.THRESHOLD_DISTANCE - x_n

    @staticmethod
    def get_dx_with_error(leader, follower, leader_no, no_of_readings, m_factor) -> float:
        if leader_no == 1:
            # immediate leader so Radar error
            return Vehicle.get_dx_with_radar_error(leader, follower,
                                                   Parameters.NO_OF_READINGS,
                                                   Parameters.M_FACTOR)
        # not immediate leader, so we need to get position through GPS
        # and get gap through inter vehicle communication
        return Vehicle.get_dx_with_position_error(leader, follower,
                                                 Parameters.NO_OF_READINGS,
                                                 Parameters.M_FACTOR)

    @staticmethod
    def get_dx_with_radar_error(leader, follower, no_of_readings, m_factor) -> float:
        # TODO bound parameterization
        sigma_radar = 2
        bound_radar = 0.38
        # we want the gap to be smallest; error can be negative; so we need the
        # smallest value
        max_error = DOUBLE_MAX_VALUE
        for _ in range(no_of_readings):
            temp = Utilities.truncated_gaussian(sigma_radar, -bound_radar, bound_radar)
            if temp < max_error:
                max_error = temp
        return Vehicle.get_gap(leader, follower) + max_error - bound_radar * m_factor

    @staticmethod
    def get_dx_with_position_error(leader, follower, no_of_readings, m_factor) -> float:
        # TODO bound parameterization
        from .javacompat import DOUBLE_MIN_VALUE
        sigma_pos = 10
        bound_pos = 10
        max_leader_pos = DOUBLE_MAX_VALUE
        max_follower_pos = DOUBLE_MIN_VALUE
        for _ in range(no_of_readings):
            temp = Utilities.truncated_gaussian(sigma_pos, -bound_pos, bound_pos)
            if temp < max_leader_pos:
                max_leader_pos = temp
            temp = Utilities.truncated_gaussian(sigma_pos, -bound_pos, bound_pos)
            if max_follower_pos < temp:
                max_follower_pos = temp
        x_n_1 = leader._distance_in_segment + max_leader_pos - bound_pos * m_factor
        x_n = (follower._distance_in_segment + max_follower_pos + follower._length
               + bound_pos * m_factor)
        return x_n_1 - x_n

    @staticmethod
    def _get_leaders_perceived_deceleration(leader, follower) -> float:
        return (leader._max_braking + follower._max_braking) / 2.0

    @staticmethod
    def _get_distance_for_desired_speed(leader, follower) -> float:
        """:return: the minimum distance between leader and follower to achieve
        the desired speed of the follower"""
        Vd = follower._current_max_speed
        T = Vehicle.TIME_STEP
        d_n_mx = follower._max_braking
        v_n = follower._speed
        v_n_1 = leader._speed
        d_n_1 = Vehicle._get_leaders_perceived_deceleration(leader, follower)
        return Vd * T + (v_n * T + jdiv(v_n_1 * v_n_1, d_n_1)
                         - jdiv(Vd * Vd, d_n_mx)) / 2.0

    @staticmethod
    def _get_object_distance_for_desired_speed(follower) -> float:
        Vd = follower._current_max_speed
        T = Vehicle.TIME_STEP
        d_n_mx = follower._max_braking
        v_n = follower._speed
        return Vd * T + (v_n * T - jdiv(Vd * Vd, d_n_mx)) / 2.0

    @staticmethod
    def get_acceleration_ghr_model(leader, follower) -> float:
        v = follower._speed
        del_v = leader._speed - follower._speed
        del_x = Vehicle.get_gap(leader, follower)
        return Vehicle.ALPHA * jpow(v, Vehicle.M) * jdiv(del_v, jpow(del_x, Vehicle.L))

    # ------------------------------------------------------------------
    # strip bookkeeping
    # ------------------------------------------------------------------

    def _occupy_strips(self) -> None:
        segment = self._link.get_segment(self._segment_index)
        for i in range(self._number_of_strips):
            segment.get_strip(self._strip_index + i).add_vehicle(self)

    def _increase_vehicle_count_on_segment(self) -> None:
        segment = self._link.get_segment(self._segment_index)
        if self.is_reverse_segment():
            segment.increase_reverse_vehicle_count()
        else:
            segment.increase_forward_vehicle_count()

    def decrease_vehicle_count_on_segment(self) -> None:
        segment = self._link.get_segment(self._segment_index)
        if self.is_reverse_segment():
            segment.decrease_reverse_vehicle_count()
        else:
            segment.decrease_forward_vehicle_count()

    def free_strips(self) -> None:
        segment = self._link.get_segment(self._segment_index)
        for i in range(self._strip_index, self._strip_index + self._number_of_strips):
            segment.get_strip(i).del_vehicle(self)

    def _store_prev_speeds(self) -> None:
        self._prev_speeds.appendleft(self._speed)
        if len(self._prev_speeds) > Vehicle.TIME_WINDOW:
            self._prev_speeds.pop()

    def _store_prev_gaps(self) -> None:
        leader = self.get_probable_leader()
        gap = 100000
        if leader is not None:
            gap = Vehicle.get_dx(leader, self, 1)
        self._prev_gaps.appendleft(gap)
        if len(self._prev_gaps) > Vehicle.GAP_WINDOW:
            self._prev_gaps.pop()

    def _get_n_leaders_gaps(self, num_lead: int):
        """:param num_lead: number of leaders to be considered
        :return: gaps between me and the num_lead leaders"""
        c = 0  # # of actual leaders
        leader = self.get_probable_leader()
        while leader is not None:
            c += 1
            leader = leader.get_probable_leader()
        N = min(c, num_lead)  # actual leaders to be considered
        gaps = [0.0] * N
        follower = self
        leader = follower.get_probable_leader()
        for i in range(N):
            gaps[i] = Vehicle.get_gap(leader, follower)
            follower = leader
            leader = follower.get_probable_leader()
        return gaps

    # ------------------------------------------------------------------
    # lane changing
    # ------------------------------------------------------------------

    def _move_to_higher_index_lane(self) -> bool:
        segment = self._link.get_segment(self._segment_index)

        limit = (segment.last_vehicle_strip_index if self.is_reverse_segment()
                 else segment.middle_low_strip_index)

        stored_strip_index = self._strip_index

        leader = self.get_probable_leader()
        follower = self.get_probable_follower()

        i = self._strip_index
        while i + self._number_of_strips <= limit:
            self._change_strip(i, i + self._number_of_strips, 1)
            if segment.get_strip(i + self._number_of_strips).has_gap_for_strip_change(
                    self, leader, follower):
                # if an object is present, having a gap is enough; no need to
                # check the other conditions
                if self.is_object_in_proximity():
                    return True
                elif (self._is_move_forward_possible()
                      and not self._is_slower_vehicle_in_proximity()):
                    self._move_forward_in_segment()
                    return True
            else:
                break
            i += 1

        self.free_strips()
        self._strip_index = stored_strip_index
        self._occupy_strips()
        return False

    def _move_to_lower_index_lane(self) -> bool:
        segment = self._link.get_segment(self._segment_index)

        limit = segment.middle_high_strip_index if self.is_reverse_segment() else 1

        stored_strip_index = self._strip_index

        leader = self.get_probable_leader()
        follower = self.get_probable_follower()

        i = self._strip_index
        while i > limit:
            self._change_strip(i + self._number_of_strips - 1, i - 1, -1)
            if segment.get_strip(i - 1).has_gap_for_strip_change(self, leader, follower):
                # if an object is present, having a gap is enough; no need to
                # check the other conditions
                if self.is_object_in_proximity():
                    return True
                elif (self._is_move_forward_possible()
                      and not self._is_slower_vehicle_in_proximity()):
                    self._move_forward_in_segment()
                    return True
            else:
                break
            i -= 1

        self.free_strips()
        self._strip_index = stored_strip_index
        self._occupy_strips()
        return False

    def _change_strip(self, remove_from: int, add_to: int, delta_index: int) -> None:
        """Helper to simplify changing strip.

        :param remove_from: vehicle is removed from this strip
        :param add_to: vehicle is added to this strip
        :param delta_index: change of index (+/-1)
        """
        segment = self._link.get_segment(self._segment_index)

        segment.get_strip(remove_from).del_vehicle(self)
        segment.get_strip(add_to).add_vehicle(self)
        self._strip_index = self._strip_index + delta_index

    # Butcher's Method
    def _get_new_distance_in_segment(self) -> float:
        v_new = self._speed
        v_old = self._prev_speeds[0]
        del_v = v_new - v_old

        k1 = v_old
        k3 = v_old + 0.25 * del_v
        k4 = v_old + 0.50 * del_v
        k5 = v_old + 0.75 * del_v
        k6 = v_new

        return (self._distance_in_segment
                + (1.0 / 90) * (7 * k1 + 32 * k3 + 12 * k4 + 32 * k5 + 7 * k6)
                * Vehicle.TIME_STEP)
        # return self._distance_in_segment + self._speed * Vehicle.TIME_STEP

    def _is_brake_required_for_leader(self) -> bool:
        # v^2 = u^2 - 2as; we get s from this eqn
        s = jdiv(self._current_max_speed * self._current_max_speed,
                 2 * -self._max_braking)
        dist_from_seg_end = self._get_distance_from_segment_end()
        return s <= dist_from_seg_end

    def _get_speed_vehicle_if_leader(self) -> float:
        if self._is_brake_required_for_leader():
            v = jsqrt(self._speed * self._speed
                      + 2 * self._max_braking * self._get_distance_from_segment_end())
        else:
            v = self._speed + self._max_acceleration * Vehicle.TIME_STEP
        self._acceleration = (v - self._speed) / Vehicle.TIME_STEP
        return v

    def _create_dummy_vehicle_at_link_end(self, signal) -> "Vehicle":
        """:return: a virtual stopped car at segment end

        (Optimise this code so that we need not generate a new dummy every time.)
        """
        v = Vehicle._dummy()
        if signal == SIGNAL.GREEN:
            v._speed = 5
        elif signal == SIGNAL.RED:
            v._speed = 0
        v._acceleration = 0
        v._max_acceleration = self._max_acceleration
        v._max_braking = self._max_braking
        v._type = self._type
        v._length = 0.1
        v._maximum_speed_capable = self._maximum_speed_capable
        v._current_max_speed = self._current_max_speed
        segment = self.get_link().get_segment(self.get_segment_index())
        v._distance_in_segment = segment.get_length()
        if Parameters.KEEP_CLEAR_MODE and signal != SIGNAL.GREEN:
            # VISSIM-style stop line: a red light stands its phantom car at
            # the edge of the junction box, not at the point where the arms
            # geometrically converge -- so the queue forms outside the drawn
            # junction instead of on top of it.  On green the phantom stays
            # at the segment end, rolling, or nobody would ever enter.
            # A vehicle the change of phase caught *past* the line keeps the
            # phantom at the segment end instead: a leader standing behind
            # its follower is a negative gap, and the car-following models
            # answer that by freezing the approach solid.
            line = (segment.get_length()
                    - (segment.stop_setback_at_start
                       if self.is_reverse_segment()
                       else segment.stop_setback_at_end))
            if self._distance_in_segment + self._length <= line:
                v._distance_in_segment = line
            else:
                # Caught past the line: the phantom rolls (the green
                # behaviour), so the vehicle carries on to the node, where
                # the red-runner rule in _move_vehicle_at_segment_end lets
                # it clear the box rather than park in the mouth.
                v._speed = 5
        v._prev_speeds = deque()
        v._prev_gaps = deque()
        for _ in range(Vehicle.TIME_WINDOW):
            v._prev_speeds.appendleft(0.0)
        for _ in range(Vehicle.GAP_WINDOW):
            v._prev_gaps.appendleft(DOUBLE_MAX_VALUE)
        v._reverse_link = self._reverse_link
        v._link = self._link
        v._segment_index = self._segment_index
        return v

    def _create_dummy_vehicle_at_infinity(self) -> "Vehicle":
        v = Vehicle._dummy()
        v._speed = self._current_max_speed
        v._acceleration = self._max_acceleration
        v._max_acceleration = self._max_acceleration
        v._max_braking = self._max_braking
        v._type = self._type
        v._length = 0.1
        v._maximum_speed_capable = self._maximum_speed_capable
        v._current_max_speed = self._current_max_speed
        v._distance_in_segment = 100000000
        v._prev_speeds = deque()
        v._prev_gaps = deque()
        for _ in range(Vehicle.TIME_WINDOW):
            v._prev_speeds.appendleft(self._current_max_speed)
        for _ in range(Vehicle.GAP_WINDOW):
            v._prev_gaps.appendleft(DOUBLE_MAX_VALUE)
        v._reverse_link = self._reverse_link
        v._link = self._link
        v._segment_index = self._segment_index
        return v

    def create_dummy_vehicle_at_pedestrian_position_for_my_model(self, pedestrian
                                                               ) -> "Vehicle":
        v = Vehicle._dummy()
        v._speed = 0
        v._acceleration = 0
        v._max_acceleration = self._max_acceleration
        v._max_braking = self._max_braking
        v._type = 12
        v._length = 0.4
        v._maximum_speed_capable = pedestrian.get_speed()
        v._current_max_speed = pedestrian.get_speed()
        v._prev_speeds = deque()
        v._prev_gaps = deque()
        for _ in range(Vehicle.TIME_WINDOW):
            v._prev_speeds.appendleft(0.0)
        for _ in range(Vehicle.GAP_WINDOW):
            v._prev_gaps.appendleft(DOUBLE_MAX_VALUE)
        v._strip_index = pedestrian.get_strip().get_strip_index()
        v._number_of_strips = Utilities.number_of_strips(v._type)
        v._reverse_link = self._reverse_link
        v._reverse_segment = self._reverse_segment
        v._link = self._link
        v._segment_index = self._segment_index
        v._distance_in_segment = pedestrian.get_distance_in_segment()
        return v

    def _get_a_leader_as_necessary(self):
        """Gets the leader.

        If this is the first car in the last segment with a red light it
        creates and returns a dummy vehicle as leader; otherwise it returns the
        probable leader.
        """
        leader = self.get_probable_leader()
        last_segment_for_this_in_current_link = (self._link.get_first_segment()
                                                 if self.is_reverse_link()
                                                 else self._link.get_last_segment())

        if leader is None:
            if (last_segment_for_this_in_current_link is self.get_segment()
                    or self._segment_carries_a_stop_line()):
                leader = self._create_dummy_vehicle_at_link_end(self._signal_on_link)
            else:
                leader = self._create_dummy_vehicle_at_infinity()
        return leader

    def _segment_carries_a_stop_line(self) -> bool:
        """Whether this (mid-link) segment holds the approach's stop line.

        A junction box deeper than a short mouth segment walks its stop line
        back into earlier segments (Processor._set_stop_lines), and the red
        phantom has to appear wherever the line actually is, not only in the
        link's last segment.  Green keeps the old behaviour -- mid-link
        traffic must not brake for a rolling phantom it would never meet.
        """
        if not Parameters.KEEP_CLEAR_MODE or self._signal_on_link == SIGNAL.GREEN:
            return False
        segment = self.get_segment()
        return (segment.stop_setback_at_start if self.is_reverse_segment()
                else segment.stop_setback_at_end) > 0.0

    def _get_a_leader_as_necessary_for_my_model(self, side_strips_to_consider):
        leader = self.get_my_probable_leader(side_strips_to_consider)
        last_segment_for_this_in_current_link = (self._link.get_first_segment()
                                                 if self.is_reverse_link()
                                                 else self._link.get_last_segment())

        if leader is None:
            if (last_segment_for_this_in_current_link is self.get_segment()
                    or self._segment_carries_a_stop_line()):
                leader = self._create_dummy_vehicle_at_link_end(self._signal_on_link)
            else:
                leader = self._create_dummy_vehicle_at_infinity()
        return leader

    def print_stat(self) -> None:
        if self._vehicle_id == 20:
            print("T: %d  .  S: %s  .   A: %s"
                  % (Parameters.simulation_step, jformat(self._speed, 2),
                     jformat(self._acceleration, 2)))

    def add_stats(self) -> None:
        self._vehicle_stats.add_speed(self._speed, Parameters.simulation_step)
        self._vehicle_stats.add_point_on_trajectory(self._vehicle_corners[0],
                                                    Parameters.simulation_step)

    def _get_speed_for_acceleration(self) -> float:
        v_n_t = self._speed
        a_n_mx = self._max_acceleration
        v_n_desired = self._current_max_speed
        # reaction time equals time step
        return (v_n_t + 2.5 * a_n_mx * Vehicle.REACTION_TIME
                * (1 - jdiv(v_n_t, v_n_desired))
                * jsqrt(0.025 + jdiv(v_n_t, v_n_desired)))

    def _get_local_density(self) -> float:
        start_strip_local = self.get_start_strip_for_my_model(self._number_of_strips)
        end_strip_local = self.get_end_strip_for_my_model(self._number_of_strips)
        starting_distance = self._distance_in_segment
        ending_distance = self._distance_in_segment + self._length * 2.0

        neighbor_vehicles = set()
        neighbor_pedestrians = set()

        segment = self.get_segment()
        for i in range(start_strip_local, end_strip_local + 1):
            s = segment.get_strip(i)
            neighbor_vehicles.update(s.get_vehicles_in_range(starting_distance,
                                                             ending_distance))
            neighbor_pedestrians.update(s.get_pedestrians_in_range(starting_distance,
                                                                   ending_distance))
        total_neighbors = len(neighbor_vehicles) + len(neighbor_pedestrians)
        return total_neighbors / 9

    def _get_my_model_free_speed(self) -> float:
        return self._speed + self._max_acceleration * Vehicle.TIME_STEP

    def _get_my_model_congested_speed(self, side_strips_to_consider: int) -> float:
        closest_leader = self._get_a_leader_as_necessary_for_my_model(
            side_strips_to_consider)
        speed_closest = Vehicle.get_speed_for_braking(closest_leader, self)

        leaders = self.get_all_leaders_for_my_model(side_strips_to_consider)

        if not leaders:
            return speed_closest

        speeds = []
        weights = []
        if Parameters.CONSIDER_MINIMUM:
            min_speed = speed_closest
            for leader in leaders:
                speed_wrt_leader = Vehicle.get_speed_for_braking(leader, self)
                min_speed = jmin(min_speed, speed_wrt_leader)
            return min_speed
        else:
            total_weight = 0
            for leader in leaders:
                speed_wrt_leader = Vehicle.get_speed_for_braking(leader, self)
                weight_wrt_leader = Utilities.get_weight(leader, self,
                                                         side_strips_to_consider)
                weight_by_type = Utilities.get_weight_by_type(leader)
                weight = weight_wrt_leader * weight_by_type

                total_weight += weight

                speeds.append(speed_wrt_leader)
                weights.append(weight)
            speed = 0
            for i in range(len(leaders)):
                speed += jdiv(weights[i], total_weight) * speeds[i]
            return jmin(speed, speed_closest)

    def _get_new_speed_my_model(self) -> float:
        v_a = jmin(self._current_max_speed, self._get_my_model_free_speed())
        # v_a = self._get_speed_for_acceleration()
        v_b = v_a

        side_strips_to_consider = Parameters.SIDE_STRIPS_TO_CONSIDER

        if self._type != PEDESTRIANS_ALONG_THE_ROAD_TYPE:
            density = self._get_local_density()

            if density / 10.0 < (1.0 * Parameters.DENSITY_PERCENTAGE / 100.0):
                side_strips_to_consider = 0

        leader = self._get_a_leader_as_necessary_for_my_model(side_strips_to_consider)

        if leader is not None:
            v_b = self._get_my_model_congested_speed(side_strips_to_consider)

        v = Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))
        self._acceleration = (v - self._speed) / Vehicle.TIME_STEP
        return v

    def _get_new_speed_gipps_model(self) -> float:
        v_a = self._get_speed_for_acceleration()
        v_b = v_a

        leader = self._get_a_leader_as_necessary()

        if leader is not None:
            v_b = Vehicle.get_speed_for_braking(leader, self)

        v = Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))
        self._acceleration = (v - self._speed) / Vehicle.TIME_STEP
        return v

    def _get_new_speed_krauss_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_krauss_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_gfm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_gfm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_idm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle.get_idm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        v = Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))
        self._acceleration = (v - self._speed) / Vehicle.TIME_STEP
        return v

    def _get_new_speed_rvf_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_rvf_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_vfiac_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_vfiac_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_ovcm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_ovcm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_kftm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_kftm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_hdm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_hdm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_sbm_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle.get_sbm_acceleration(leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def _get_new_speed_modified_newtonian_model(self) -> float:
        v_a = self._speed + self._max_acceleration * Vehicle.TIME_STEP

        leader = self._get_a_leader_as_necessary()

        if leader is None:
            v_b = v_a
        else:
            self._acceleration = Vehicle._get_modified_newtonian_acceleration(
                leader, self)
            v_b = self._speed + self._acceleration * Vehicle.TIME_STEP

        return Utilities.precision2(jmax(0.0, jmin(v_a, v_b)))

    def get_new_speed(self) -> float:
        """:return: new speed according to the configured car-following model"""
        model = Parameters.car_following_model
        if model == CAR_FOLLOWING_MODEL.NAIVE_MODEL:
            return self._speed + self._max_acceleration * Vehicle.TIME_STEP
        if model == CAR_FOLLOWING_MODEL.MODIFIED_NEWTONIAN_MODEL:
            return self._get_new_speed_modified_newtonian_model()
        if model in (CAR_FOLLOWING_MODEL.GIPPS_MODEL, CAR_FOLLOWING_MODEL.HYBRID_MODEL):
            return self._get_new_speed_gipps_model()
        if model == CAR_FOLLOWING_MODEL.KRAUSS_MODEL:
            return self._get_new_speed_krauss_model()
        if model == CAR_FOLLOWING_MODEL.GFM_MODEL:
            return self._get_new_speed_gfm_model()
        if model == CAR_FOLLOWING_MODEL.IDM_MODEL:
            return self._get_new_speed_idm_model()
        if model == CAR_FOLLOWING_MODEL.RVF_MODEL:
            return self._get_new_speed_rvf_model()
        if model == CAR_FOLLOWING_MODEL.VFIAC_MODEL:
            return self._get_new_speed_vfiac_model()
        if model == CAR_FOLLOWING_MODEL.OVCM_MODEL:
            return self._get_new_speed_ovcm_model()
        if model == CAR_FOLLOWING_MODEL.KFTM_MODEL:
            return self._get_new_speed_kftm_model()
        if model == CAR_FOLLOWING_MODEL.HDM_MODEL:
            return self._get_new_speed_hdm_model()
        if model == CAR_FOLLOWING_MODEL.SBM_MODEL:
            return self._get_new_speed_sbm_model()
        if model == CAR_FOLLOWING_MODEL.MY_MODEL:
            return self._get_new_speed_my_model()
        return self._get_new_speed_gipps_model()

    def try_lane_change(self, priority_towards_middle: bool) -> None:
        towards_middle = priority_towards_middle == self.is_reverse_segment()
        # For a vehicle, it doesn't try to go towards the roadside when an
        # object is present.  Pedestrians can try.
        if towards_middle:
            if not self._move_to_lower_index_lane():
                if (self._type == PEDESTRIANS_ALONG_THE_ROAD_TYPE
                        or not self.is_object_in_proximity()):
                    self._move_to_higher_index_lane()
        else:
            if not self._move_to_higher_index_lane():
                if (self._type == PEDESTRIANS_ALONG_THE_ROAD_TYPE
                        or not self.is_object_in_proximity()):
                    self._move_to_lower_index_lane()

    # TODO optimize
    def move_vehicle_in_segment(self) -> None:
        if self._type != PEDESTRIANS_ALONG_THE_ROAD_TYPE:
            # the order of the three conditions is important and must not be changed
            if (self.is_object_in_proximity() or not self._move_forward_in_segment()
                    or self._is_slower_vehicle_in_proximity()):
                self.try_lane_change(True)
        else:
            if (Utilities.rand_int(0, 101)
                    < Parameters.PEDESTRIAN_RANDOM_LANE_CHANGE_PERCENTAGE):
                probability = Parameters.PEDESTRIAN_LEFT_BIAS_PERCENTAGE
                rand_int = Utilities.rand_int(0, 101)
                if rand_int < probability:
                    if not self.is_reverse_segment():
                        if not self._move_to_lower_index_lane():
                            if not self._move_to_higher_index_lane():
                                if self.is_object_in_proximity():
                                    self.try_lane_change(False)
                                self._move_forward_in_segment()
                    else:
                        if not self._move_to_higher_index_lane():
                            if not self._move_to_lower_index_lane():
                                if self.is_object_in_proximity():
                                    self.try_lane_change(False)
                                self._move_forward_in_segment()
            else:
                if self.is_object_in_proximity():
                    self.try_lane_change(False)
                if not self._move_forward_in_segment():
                    self.try_lane_change(False)

    def has_collided(self) -> bool:
        if self._is_in_intersection:
            return False
        return self.get_segment().get_strip(self._strip_index).has_collision_occurred(self)

    def has_caused_accident(self) -> bool:
        if self._is_in_intersection:
            return False
        strip = self._link.get_segment(self._segment_index).get_strip(self._strip_index)
        return strip.check_for_accident(self)

    def reset_position(self) -> None:
        leader = self.get_segment().get_strip(
            self.get_strip_index()).get_accident_vehicle(self)

        leader_dist = leader.get_distance_in_segment()
        self._distance_in_segment = leader_dist - Vehicle.THRESHOLD_DISTANCE - self._length
        self._speed = 0
        self._acceleration = 0

    def after_collision_to_do(self) -> None:
        if not self._has_collided:
            self._color = Color.RED
            self._has_collided = True
            self._collision_time = Parameters.simulation_step
            self._collision_penalty = jint(Utilities.get_collision_penalty() * 300.0)
            leader_collided = self.get_segment().get_strip(
                self.get_strip_index()).get_accident_vehicle(self)
            # leader_collided can be None when this function is called from the
            # following collider
            if leader_collided is not None:
                leader_collided.after_collision_to_do()
                Statistics.no_of_collisions += 1
                Statistics.no_collisions_per_demand[self._demand_index][self._type] += 1
            self._speed = 0
            self._acceleration = 0
            self._penalty_for_collision += Utilities.get_collision_penalty() * 60.0

    def after_accident_to_do(self) -> None:
        if not self._has_collided:
            self._color = Color.RED
            self._has_collided = True
            self._collision_time = Parameters.simulation_step
            self._collision_penalty = jint(Utilities.get_collision_penalty() * 300.0)
            self._speed = 0
            self._acceleration = 0
            self._penalty_for_collision += Utilities.get_collision_penalty() * 60.0

            Statistics.no_of_accidents += 1
            Statistics.no_accidents_per_demand[self._demand_index][self._type] += 1

    def print_accident_log(self) -> None:
        if not self._has_collided:
            leader = self.get_segment().get_strip(
                self.get_strip_index()).get_accident_vehicle(self)
            leader_type = -1 if leader is None else leader.get_type()
            leader_speed = NaN if leader is None else leader.get_speed()
            leader_acc = NaN if leader is None else leader.get_acceleration()
            # sim_step, vehicle_id, type, speed, leader_type, leader_speed,
            # acceleration, collision_penalty
            accident_log().write(
                "%d, %d, %d, %s, %s, %d, %s, %s\n"
                % (Parameters.simulation_step, self.get_vehicle_id(),
                   self.get_type(), jformat(self.get_speed(), 3),
                   jformat(self.get_acceleration(), 3), leader_type,
                   jformat(leader_speed, 3), jformat(leader_acc, 3)))

    def _control_speed_in_segment(self) -> bool:
        self._store_prev_speeds()
        self._speed = self.get_new_speed()
        if self._speed > self._current_max_speed:
            self._speed = self._current_max_speed
        if Parameters.BRAKE_HARD:
            gap = (self._speed * Vehicle.TIME_STEP
                   + 0.5 * self._max_acceleration * Vehicle.TIME_STEP * Vehicle.TIME_STEP)
            for i in range(self._strip_index,
                           self._strip_index + self._number_of_strips):
                segment = self._link.get_segment(self._segment_index)
                strip = segment.get_strip(i)
                strip_gap = strip.get_gap_for_forward_movement(self)
                if strip_gap == 0:
                    self._speed = 0
                    return False
                else:
                    gap = jmin(gap, strip_gap)
            self._speed = gap / Vehicle.TIME_STEP
            return True
        else:
            return self._speed > 0

    def _is_move_forward_possible(self) -> bool:
        store_speed = self._speed
        self._speed = self.get_new_speed()
        if self._speed > self._current_max_speed:
            self._speed = self._current_max_speed
        if Parameters.BRAKE_HARD:
            for i in range(self._strip_index,
                           self._strip_index + self._number_of_strips):
                segment = self._link.get_segment(self._segment_index)
                strip = segment.get_strip(i)
                strip_gap = strip.get_gap_for_forward_movement(self)
                if strip_gap == 0:
                    self._speed = store_speed
                    return False
            self._speed = store_speed
            return True
        else:
            res = self._speed > 0
            self._speed = store_speed
            return res

    def print_vehicle_details(self) -> None:
        if Parameters.DEBUG_MODE:
            if self._vehicle_id in (1767, 109, -10, -110):
                pathname = "debug/debug" + str(self._vehicle_id) + ".txt"
                os.makedirs("debug", exist_ok=True)
                with open(pathname, "a") as writer:
                    writer.write("Sim step: " + str(Parameters.simulation_step) + "\n")
                    writer.write("Vehicle ID: " + str(self._vehicle_id) + "\n")
                    writer.write("Type: " + str(self._type) + "\n")
                    writer.write("Speed: " + jformat(self._speed, 2) + "\n")
                    writer.write("Acceleration: " + jformat(self._acceleration, 2) + "\n")
                    writer.write("Length: " + jformat(self._length, 2) + "\n")
                    writer.write("Accident Time: " + str(self._collision_time) + "\n")
                    if self._is_in_intersection:
                        writer.write("Intersection Mode: true\n")
                        writer.write("Intersection ID: " + str(self._node.get_id()) + "\n")
                        writer.write("Distance in Intersection: "
                                     + jformat(self._distance_in_intersection, 2) + "\n")
                        writer.write("Intersection Length: " + jformat(
                            self._node.get_intersection_strip(
                                self._intersection_strip_index).get_length()
                            / Parameters.pixel_per_meter, 2) + "\n")
                    else:
                        writer.write("Link ID: " + str(self._link.get_id()) + "\n")
                        writer.write("Segment Index: " + str(self._segment_index) + "\n")
                        writer.write("Strip Index: " + str(self._strip_index) + "\n")
                        p_leader = self.get_probable_leader()
                        p_follower = self.get_probable_follower()
                        leader_id = -1 if p_leader is None else p_leader._vehicle_id
                        follower_id = -1 if p_follower is None else p_follower._vehicle_id
                        writer.write("Leader: " + str(leader_id) + "\n")
                        writer.write("Follower: " + str(follower_id) + "\n")
                        writer.write("Distance in Segment: "
                                     + jformat(self._distance_in_segment, 2) + "\n")
                        writer.write("Segment Length: " + jformat(
                            self._link.get_segment(self._segment_index).get_length(), 2)
                            + "\n")
                        writer.write("Segment Width: " + jformat(
                            self._link.get_segment(
                                self._segment_index).get_segment_width(), 2) + "\n")
                        writer.write("Reverse Link: "
                                     + ("true" if self._reverse_link else "false") + "\n")
                        writer.write("Reverse Segment: "
                                     + ("true" if self._reverse_segment else "false")
                                     + "\n")
                    writer.write("\n")

    def get_start_strip_for_my_model(self, delta_index: int) -> int:
        start_index = self._strip_index
        if self._type > -12:
            if self._reverse_segment:
                start_index = max(start_index - delta_index,
                                  self.get_segment().middle_high_strip_index)
            else:
                start_index = max(1, start_index - delta_index)
        return start_index

    def get_end_strip_for_my_model(self, delta_index: int) -> int:
        end_index = self._strip_index + self._number_of_strips - 1
        if self._type != PEDESTRIANS_ALONG_THE_ROAD_TYPE:
            if self._reverse_segment:
                end_index = min(end_index + delta_index,
                                self.get_segment().last_vehicle_strip_index)
            else:
                end_index = min(end_index + delta_index,
                                self.get_segment().middle_low_strip_index)
        return end_index

    def get_my_probable_leader(self, side_strips_to_consider: int):
        leader = None
        start_index = self.get_start_strip_for_my_model(side_strips_to_consider)
        end_index = self.get_end_strip_for_my_model(side_strips_to_consider)
        segment = self._link.get_segment(self._segment_index)
        for i in range(start_index, end_index + 1):
            strip = segment.get_strip(i)
            v = strip.get_probable_leader_for_my_model(self)
            if v is not None:
                if leader is None:
                    leader = v
                else:
                    if leader.get_distance_in_segment() > v.get_distance_in_segment():
                        leader = v
        return leader

    def get_probable_leader(self):
        """:return: the vehicle closest and in front of this among all the
        strips (None if none is in front of it)"""
        leader = None
        nearest = 0.0
        segment = self._link.get_segment(self._segment_index)
        for i in range(self._strip_index, self._strip_index + self._number_of_strips):
            v = segment.get_strip(i).get_probable_leader_for_my_model(self)
            if v is None:
                continue
            distance = v.get_distance_in_segment()
            if leader is None or nearest > distance:
                leader, nearest = v, distance
        return leader

    def get_probable_object_leader(self):
        leader = None
        segment = self._link.get_segment(self._segment_index)
        for i in range(self._strip_index, self._strip_index + self._number_of_strips):
            strip = segment.get_strip(i)
            o = strip.probable_object_leader(self)
            if o is not None:
                if leader is None:
                    leader = o
                else:
                    if leader.get_distance_in_segment() > o.get_distance_in_segment():
                        leader = o
        return leader

    def get_probable_follower(self):
        follower = None
        segment = self._link.get_segment(self._segment_index)
        for i in range(self._strip_index, self._strip_index + self._number_of_strips):
            strip = segment.get_strip(i)
            v = strip.probable_follower(self)
            if v is not None:
                if follower is None:
                    follower = v
                else:
                    if (follower.get_distance_in_segment() + follower._length
                            < v.get_distance_in_segment() + v._length):
                        follower = v
        return follower

    def get_all_leaders_for_my_model(self, side_strips_to_consider: int):
        leaders = []
        segment = self._link.get_segment(self._segment_index)
        for i in range(self.get_start_strip_for_my_model(side_strips_to_consider),
                       self.get_end_strip_for_my_model(side_strips_to_consider) + 1):
            strip = segment.get_strip(i)
            v = strip.get_probable_leader_for_my_model(self)
            if v is not None:
                leaders.append(v)
        return leaders

    def print_all_leaders(self) -> None:
        leaders = self.get_all_leaders_for_my_model(Parameters.SIDE_STRIPS_TO_CONSIDER)
        if self._vehicle_id == 4:
            parts = []
            for leader in leaders:
                w = Utilities.get_weight(leader, self, Parameters.SIDE_STRIPS_TO_CONSIDER)
                parts.append("%d %s\t" % (leader._vehicle_id, jformat(w, 2)))
            print("This: %d; Leaders: %s" % (self._vehicle_id, "".join(parts)))

    def _is_slower_vehicle_in_proximity(self) -> bool:
        leader = self.get_probable_leader()
        if leader is not None:
            if self._type == PEDESTRIANS_ALONG_THE_ROAD_TYPE:
                return (leader._current_max_speed < self._current_max_speed
                        and leader.get_distance_in_segment()
                        < self.get_distance_in_segment() + self._length
                        + self._current_max_speed)
            if self._dlc_model == DLC_MODEL.NAIVE_MODEL:
                return (leader._current_max_speed < self._current_max_speed
                        and leader.get_distance_in_segment()
                        < self.get_distance_in_segment() + self._length
                        + self._current_max_speed)
            # case GHR_MODEL -> get_acceleration_ghr_model(leader, this) < 0
            return (Vehicle.get_gap(leader, self)
                    < Vehicle._get_distance_for_desired_speed(leader, self))
        # no leader
        return False

    def is_object_in_proximity(self) -> bool:
        object_leader = self.get_probable_object_leader()
        threshold_distance = 2

        if object_leader is not None:
            if self._type == PEDESTRIANS_ALONG_THE_ROAD_TYPE:
                return (object_leader.get_distance_in_segment()
                        < self.get_distance_in_segment() + self._length
                        + self._current_max_speed + threshold_distance)
            # have to fix for other models, currently uses naive model
            if self._dlc_model == DLC_MODEL.NAIVE_MODEL:
                return (object_leader.get_distance_in_segment()
                        < self.get_distance_in_segment() + self._length
                        + self._current_max_speed + threshold_distance)
            # case GHR_MODEL -> falls through to the gap test below.
            #
            # Java evaluates the GHR acceleration here against the cached
            # `leader` field, which is null at this point, so `DLC_model 1`
            # with `ObjectMode On` throws rather than running -- a combination
            # the shipped `DLC_model 0` never reaches.  There is no behaviour
            # to reproduce, and GHR has no meaningful reading here anyway: a
            # roadside object never moves, so its closing speed is the
            # follower's own and the acceleration comes out negative at any
            # distance, which would mean "obstructed" always.  The sibling
            # method above drops GHR to the same gap test for the same reason.
            return (Vehicle.get_object_gap(object_leader, self)
                    < Vehicle._get_object_distance_for_desired_speed(self))
        return False

    def _control_speed_in_intersection(self) -> bool:
        """Does not change the distance in intersection, just changes the speed
        if necessary.

        :return: whether movement is possible or not
        """
        store_distance_in_intersection = self._distance_in_intersection
        self._speed += self._max_acceleration * Vehicle.TIME_STEP

        # this block is for making a small jump while deadlocked in intersection
        if not self._no_force_move:
            self._speed += self._length * 1.5

        if self._speed > self._current_max_speed:
            self._speed = self._current_max_speed
        store_speed = self._speed

        temp_speed = 0
        temp_speed += self._max_acceleration * Vehicle.TIME_STEP
        movement_possible = False

        while temp_speed <= store_speed:
            self._speed = temp_speed
            self._distance_in_intersection += temp_speed * Vehicle.TIME_STEP
            if self._node.do_overlap(self) and self._no_force_move:
                self._distance_in_intersection = store_distance_in_intersection
                temp_speed -= self._max_acceleration * Vehicle.TIME_STEP
                self._speed = temp_speed
                return movement_possible
            movement_possible = True
            temp_speed += self._max_acceleration * Vehicle.TIME_STEP
            self._distance_in_intersection = store_distance_in_intersection
        self._distance_in_intersection = store_distance_in_intersection
        return True

    def _move_forward_in_segment(self) -> bool:
        segment = self._link.get_segment(self._segment_index)
        if self._control_speed_in_segment():
            # self._modify_speed_and_acc()
            self._store_prev_gaps()
            self._distance_in_segment = self._get_new_distance_in_segment()
            # distance in segment is the tail of the vehicle so the vehicle
            # length is subtracted from the segment length
            if self._distance_in_segment > segment.get_length() - self._length:
                # here MARGIN is the margin, slightly less than the margin in
                # is_at_segment_end
                self._distance_in_segment = (segment.get_length() - self._length
                                             - (Vehicle.MARGIN - 0.1))
            if not self._passed_sensor:
                if self._distance_in_segment > segment.get_sensor():
                    self._passed_sensor = True
            return True
        else:
            self._waiting_time += 1
            self._waiting_time_in_segment += 1
            return False

    def _modify_speed_and_acc(self) -> None:
        store_dist_in_segment = self._distance_in_segment
        new_dist_in_segment = self._get_new_distance_in_segment()
        if (new_dist_in_segment
                > self._link.get_segment(self._segment_index).get_length() - self._length):
            new_dist_in_segment = (
                self._link.get_segment(self._segment_index).get_length()
                - self._length - (Vehicle.MARGIN - 0.1))
            delta_distance = new_dist_in_segment - store_dist_in_segment
            modified_speed = delta_distance / Vehicle.TIME_STEP
            self._acceleration = (modified_speed - self._speed) / Vehicle.TIME_STEP
            self._speed = modified_speed

    def _print_end_points(self, index: int, p) -> None:
        if self._vehicle_id in (86, 186):
            pathname = "debug_ep" + str(self._vehicle_id) + ".txt"
            with open(pathname, "a") as writer:
                writer.write("Index: " + str(index) + " End Point: "
                             + str(p.x) + " " + str(p.y) + "\n")

    def _is_change_direction_in_intersection_fruitful(self, new_strip_index: int) -> bool:
        """Try to move forward by changing the end point i.e. direction of the
        intersection strip.

        :param new_strip_index: calculate the end point from this index on the
            entering segment of the intersection strip
        :return: whether we can move forward in the new direction
        """
        is_ = self.get_current_intersection_strip()
        store_new_strip_index = is_.end_strip
        store_end_point_x = is_.end_point_x
        store_end_point_y = is_.end_point_y

        new_end_point = Utilities.get_new_end_point_for_intersection_strip(
            self, is_, new_strip_index)
        is_.end_point_x = new_end_point.x
        is_.end_point_y = new_end_point.y
        is_.end_strip = new_strip_index
        if self._is_move_forward_possible_in_intersection():
            return True
        else:
            is_.end_strip = store_new_strip_index
            is_.end_point_x = store_end_point_x
            is_.end_point_y = store_end_point_y
            return False

    def _is_move_forward_possible_in_intersection(self) -> bool:
        """Changes nothing.  Just checks if moving forward is possible.
        Similar to :meth:`_control_speed_in_intersection`."""
        store_distance_in_intersection = self._distance_in_intersection
        store_initial_speed = self._speed

        self._speed += self._max_acceleration * Vehicle.TIME_STEP
        if self._speed > self._current_max_speed:
            self._speed = self._current_max_speed
        store_speed = self._speed

        temp_speed = 0
        temp_speed += self._max_acceleration
        movement_possible = False

        while temp_speed <= store_speed:
            self._speed = temp_speed
            self._distance_in_intersection += temp_speed
            if self._node.do_overlap(self):
                self._distance_in_intersection = store_distance_in_intersection
                self._speed = store_initial_speed
                return movement_possible
            movement_possible = True
            temp_speed += self._max_acceleration
            self._distance_in_intersection = store_distance_in_intersection
        self._distance_in_intersection = store_distance_in_intersection
        self._speed = store_initial_speed
        return True

    def _try_changing_direction_in_intersection(self) -> bool:
        """Try all the strips of the entering segment to enter when stuck in
        the current direction; returns whether it can move forward after
        changing direction."""
        is_ = self.get_current_intersection_strip()
        if is_.end_strip <= is_.entering_segment.middle_low_strip_index:
            begin_limit = 1
            end_limit = (is_.entering_segment.middle_low_strip_index
                         - (self._number_of_strips - 1))
        else:
            begin_limit = is_.entering_segment.middle_high_strip_index
            end_limit = (is_.entering_segment.last_vehicle_strip_index
                         - (self._number_of_strips - 1))
        for i in range(begin_limit, end_limit + 1):
            if self._is_change_direction_in_intersection_fruitful(i):
                self._stuck_in_intersection = 0
                return True
        return False

    def _is_slower_vehicle_in_proximity_in_intersection(self) -> bool:
        store_distance_in_intersection = self._distance_in_intersection
        self._distance_in_intersection += self._speed * Vehicle.TIME_STEP
        if self._node.do_overlap(self):
            # overlaps when moving forward at full speed; so vehicle in proximity
            obstructing_vehicle = self._node.get_overlapping_vehicle(self)
            if obstructing_vehicle._speed < self._speed:
                # obstructing vehicle is slower
                self._distance_in_intersection = store_distance_in_intersection
                return True
            # obstructing vehicle is not slow
        # no vehicle in proximity
        self._distance_in_intersection = store_distance_in_intersection
        return False

    def move_vehicle_in_intersection(self) -> None:
        if (not self._move_forward_in_intersection()
                or self._is_slower_vehicle_in_proximity_in_intersection()):
            self._try_changing_direction_in_intersection()

    def _move_forward_in_intersection(self) -> bool:
        if self._control_speed_in_intersection():
            self._distance_in_intersection += self._speed * Vehicle.TIME_STEP
            pseudo_length = (self._node.get_intersection_strip(
                self._intersection_strip_index).get_length()
                / Parameters.pixel_per_meter)
            if self._distance_in_intersection > pseudo_length - self._length:
                self._distance_in_intersection = (pseudo_length - self._length
                                                  - (Vehicle.MARGIN - 0.1))
            self._stuck_in_intersection = 0
            self._no_force_move = False
            return True
        else:
            self._stuck_in_intersection += 1
            self._no_force_move = self._stuck_in_intersection <= 40
            self._waiting_time += 1
            return False

    def is_at_segment_end(self) -> bool:
        segment = self._link.get_segment(self._segment_index)
        return (self._distance_in_segment + self._length + Vehicle.MARGIN
                >= segment.get_length())

    def is_at_intersection_end(self) -> bool:
        intersection_strip = self._node.get_intersection_strip(
            self._intersection_strip_index)
        return ((self._distance_in_intersection + self._length + Vehicle.MARGIN)
                * Parameters.pixel_per_meter >= intersection_strip.get_length())

    def get_new_strip_index(self, leaving_segment, entering_segment, is_reverse) -> int:
        """oldSegmentWidth != newSegmentWidth, so we need to calculate a new
        strip index.

        :param leaving_segment: current/leaving segment
        :param entering_segment: entering segment
        :param is_reverse: whether the entering segment is reverse or not
        :return: strip index in the new link
        """
        if self._strip_index >= leaving_segment.middle_high_strip_index:
            strip_index_for_vehicle = (self._strip_index
                                       - leaving_segment.middle_high_strip_index + 1)
            old_limit = (leaving_segment.last_vehicle_strip_index
                         - leaving_segment.middle_high_strip_index + 1)
        else:
            strip_index_for_vehicle = self._strip_index
            old_limit = leaving_segment.middle_low_strip_index

        if is_reverse:
            new_limit = (entering_segment.last_vehicle_strip_index
                         - entering_segment.middle_high_strip_index + 1)
            new_strip_index = jint(jround(jdiv(1.0 * strip_index_for_vehicle, old_limit)
                                          * new_limit))

            if new_strip_index == 0:
                new_strip_index += 1

            if not self.is_reverse_segment():
                new_strip_index = new_limit - new_strip_index + 1

            new_strip_index = (entering_segment.middle_high_strip_index
                               + new_strip_index - 1)

            if (entering_segment.last_vehicle_strip_index - new_strip_index + 1
                    < self._number_of_strips):
                new_strip_index = (entering_segment.last_vehicle_strip_index
                                   - self._number_of_strips + 1)

            if not (new_strip_index >= entering_segment.middle_high_strip_index
                    and (new_strip_index + self._number_of_strips - 1)
                    <= entering_segment.last_vehicle_strip_index):
                # if the road is not wide enough then it can be here
                print("Reverse: >>>>>>>>>>>>>>>>>" + str(self._vehicle_id))
        else:
            new_limit = entering_segment.middle_low_strip_index
            new_strip_index = jint(jround(jdiv(1.0 * strip_index_for_vehicle, old_limit)
                                          * new_limit))

            if new_strip_index == 0:
                new_strip_index += 1

            if self.is_reverse_segment():
                new_strip_index = new_limit - new_strip_index + 1

            if (entering_segment.middle_low_strip_index - new_strip_index + 1
                    < self._number_of_strips):
                new_strip_index = (entering_segment.middle_low_strip_index
                                   - self._number_of_strips + 1)

            if not (new_strip_index >= 1
                    and (new_strip_index + self._number_of_strips - 1)
                    <= entering_segment.middle_low_strip_index):
                # if the road is not wide enough then it can be here
                print("Straight: >>>>>>>>>>>>>" + str(self._vehicle_id))

        return new_strip_index

    def has_penalty_time_passed(self) -> bool:
        return ((Parameters.simulation_step - self._collision_time)
                >= self._penalty_for_collision)

    def remove_from_simulation(self, has_completed_trip: bool) -> None:
        """Called when the trip is completed or the vehicle has caused an
        accident."""
        if has_completed_trip is False:
            print("mara")
        self.set_to_remove(True)
        self.free_strips()
        self.decrease_vehicle_count_on_segment()
        if has_completed_trip:
            self.update_segment_leaving_data()

    def is_has_already_collided(self) -> bool:
        return self._has_collided

    def get_current_intersection_strip(self):
        return self._node.get_intersection_strip(self._intersection_strip_index)

    def get_threshold_distance(self) -> float:
        # here 1.8 is the desired time gap
        return Vehicle.THRESHOLD_DISTANCE + Vehicle.SAFE_TIME_GAP * self._speed

    def get_max_braking(self) -> float:
        return self._max_braking

    def get_signal_on_link(self):
        return self._signal_on_link

    def set_signal_on_link(self, signal_on_link) -> None:
        self._signal_on_link = signal_on_link

    def get_vehicle_id(self) -> int:
        return self._vehicle_id

    def get_type(self) -> int:
        return self._type

    def set_type(self, type_: int) -> None:
        self._type = type_

    def get_length(self) -> float:
        return self._length

    def set_length(self, length: float) -> None:
        self._length = length

    def get_width(self) -> float:
        return self._width

    def set_width(self, width: float) -> None:
        self._width = width

    def get_number_of_strips(self) -> int:
        return self._number_of_strips

    def set_number_of_strips(self, number_of_strips: int) -> None:
        self._number_of_strips = number_of_strips

    def get_speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        self._speed = speed

    def get_acceleration(self) -> float:
        return self._acceleration

    def set_acceleration(self, acceleration: float) -> None:
        self._acceleration = acceleration

    def get_color(self):
        return self._color

    def set_color(self, color) -> None:
        self._color = color

    def get_demand_index(self) -> int:
        return self._demand_index

    def set_demand_index(self, demand_index: int) -> None:
        self._demand_index = demand_index

    def get_path_index(self) -> int:
        return self._path_index

    def set_path_index(self, path_index: int) -> None:
        self._path_index = path_index

    def get_link_index_on_path(self) -> int:
        return self._link_index_on_path

    def set_link_index_on_path(self, link_index_on_path: int) -> None:
        self._link_index_on_path = link_index_on_path

    def get_seg_start_x(self) -> float:
        return self._segment_start_point.x

    def set_seg_start_x(self, seg_start_x: float) -> None:
        self._segment_start_point.x = seg_start_x

    def get_seg_start_y(self) -> float:
        return self._segment_start_point.y

    def set_seg_start_y(self, seg_start_y: float) -> None:
        self._segment_start_point.y = seg_start_y

    def get_seg_end_x(self) -> float:
        return self._segment_end_point.x

    def set_seg_end_x(self, seg_end_x: float) -> None:
        self._segment_end_point.x = seg_end_x

    def get_seg_end_y(self) -> float:
        return self._segment_end_point.y

    def set_seg_end_y(self, seg_end_y: float) -> None:
        self._segment_end_point.y = seg_end_y

    def is_in_intersection(self) -> bool:
        return self._is_in_intersection

    def set_in_intersection(self, in_intersection: bool) -> None:
        self._is_in_intersection = in_intersection

    def is_reverse_link(self) -> bool:
        return self._reverse_link

    def set_reverse_link(self, reverse_link: bool) -> None:
        self._reverse_link = reverse_link

    def is_reverse_segment(self) -> bool:
        return self._reverse_segment

    def set_reverse_segment(self, reverse_segment: bool) -> None:
        self._reverse_segment = reverse_segment

    def is_passed_sensor(self) -> bool:
        return self._passed_sensor

    def set_passed_sensor(self, passed_sensor: bool) -> None:
        self._passed_sensor = passed_sensor

    def is_to_remove(self) -> bool:
        return self._to_remove

    def set_to_remove(self, to_remove: bool) -> None:
        self._to_remove = to_remove

    def get_link(self):
        return self._link

    def set_link(self, link) -> None:
        self._link = link

    def get_segment_index(self) -> int:
        return self._segment_index

    def set_segment_index(self, segment_index: int) -> None:
        self._segment_index = segment_index

    def get_strip_index(self) -> int:
        return self._strip_index

    def get_distance_in_segment(self) -> float:
        return self._distance_in_segment

    def set_distance_in_segment(self, distance_in_segment: float) -> None:
        self._distance_in_segment = distance_in_segment

    def _get_distance_from_segment_end(self) -> float:
        return (self.get_segment().get_length() - self._distance_in_segment
                - self._length - Vehicle.MARGIN)

    def get_node(self):
        return self._node

    def set_node(self, node) -> None:
        self._node = node

    def get_intersection_strip_index(self) -> int:
        return self._intersection_strip_index

    def set_intersection_strip_index(self, intersection_strip_index: int) -> None:
        self._intersection_strip_index = intersection_strip_index

    def get_distance_in_intersection(self) -> float:
        return self._distance_in_intersection

    def set_distance_in_intersection(self, distance_in_intersection: float) -> None:
        self._distance_in_intersection = distance_in_intersection

    def get_leader(self):
        return self._leader

    def set_leader(self, leader) -> None:
        self._leader = leader

    def get_segment_corners(self):
        return self._segment_corners

    def calculate_corner_points(self) -> None:
        strip = self._node.get_intersection_strip(self._intersection_strip_index)
        x_1 = strip.start_point_x
        x_2 = strip.end_point_x
        y_1 = strip.start_point_y
        y_2 = strip.end_point_y
        p_s_l = math.hypot(x_1 - x_2, y_1 - y_2)
        l = self.get_length() * Parameters.pixel_per_meter
        d_i_j = self._distance_in_intersection * Parameters.pixel_per_meter
        t1 = jdiv(d_i_j, p_s_l)
        x1 = (1 - t1) * x_1 + t1 * x_2
        y1 = (1 - t1) * y_1 + t1 * y_2
        t2 = jdiv(d_i_j + l, p_s_l)
        x2 = (1 - t2) * x_1 + t2 * x_2
        y2 = (1 - t2) * y_1 + t2 * y_2
        w = self.get_width() * Parameters.pixel_per_meter
        x3 = Utilities.return_x3(x1, y1, x2, y2, w)
        y3 = Utilities.return_y3(x1, y1, x2, y2, w)
        x4 = Utilities.return_x4(x1, y1, x2, y2, w)
        y4 = Utilities.return_y4(x1, y1, x2, y2, w)
        self._segment_corners[0] = Point2D(x1, y1)
        self._segment_corners[1] = Point2D(x2, y2)
        self._segment_corners[2] = Point2D(x4, y4)
        self._segment_corners[3] = Point2D(x3, y3)

    def update_segment_leaving_data(self) -> None:
        """Updates the required statistical info before leaving a segment."""
        self._set_segment_leave_time(Parameters.simulation_step)
        self.increase_traveled_distance(self.get_segment().get_length())
        self.get_segment().update_avg_speed_in_segment(self._get_avg_speed_in_segment())
        self.get_segment().increase_total_waiting_time(
            jint(self._waiting_time_in_segment * Constants.TIME_STEP))

    def update_segment_entering_data(self) -> None:
        """Updates the required statistical info before entering a segment."""
        self._waiting_time_in_segment = 0
        self._set_segment_enter_time(Parameters.simulation_step)
        self.get_segment().increase_entering_vehicle_count()
        self.set_speed(self.get_new_speed())

    def _get_avg_speed_in_segment(self) -> float:
        return jdiv(self.get_segment().get_length(),
                    (self._segment_leave_time - self._segment_enter_time)
                    * Constants.TIME_STEP)

    def _set_segment_enter_time(self, segment_enter_time: int) -> None:
        self._segment_enter_time = segment_enter_time

    def _set_segment_leave_time(self, segment_leave_time: int) -> None:
        self._segment_leave_time = segment_leave_time

    def get_waiting_time(self) -> int:
        return jint(self._waiting_time * Constants.TIME_STEP)

    def get_travel_time(self) -> int:
        if self._end_time == 0:
            self._end_time = Parameters.simulation_step
        return jint((self._end_time - self._start_time) * Constants.TIME_STEP)

    def get_distance_traveled(self) -> float:
        return self._distance_traveled

    def compute_fuel_consumption(self) -> float:
        """Computes the fuel consumed in each TIME_STEP.

        :return: the consumed fuel in the current SIMULATION_STEP in
            litre/TIME_STEP
        """
        mass = 1325.0  # kg
        t = Vehicle.TIME_STEP
        d_petrol = 0.73722  # gm/cc at 60 F
        f_idle = 0.299  # gm/s

        alpha = 0.365
        beta = 0.00114
        delta = 9.65 / 10000000
        xita = 0.0943
        A = 0.1326  # for tires.
        B = 0.0027384  # 274.4; % 205.8; % 823.2;
        C = 0.0010843
        v = self._speed * 3600 / 1000  # km/hr
        Rt = (A * v + B * (v * v) + C * (v * v * v) + mass * self._acceleration * v)
        av = self._acceleration * self._speed

        if Rt > 0:
            del_f = alpha + beta * v + delta * (v * v * v) + xita * av
        else:
            del_f = f_idle

        return del_f / d_petrol * t / 1000  # litre/TIME_STEP

    def increment_fuel_consumption(self) -> None:
        self._fuel_consumption += self.compute_fuel_consumption()

    def update_trip_time_statistics(self) -> None:
        Statistics.no_of_vehicles_completing_trip[self._demand_index][self._type] += 1
        Statistics.trip_time[self._demand_index][self._type] += self.get_travel_time()
        # tripTime is int[][] in Java, so `+= penaltyForCollision` (a double)
        # implicitly narrows back to int, truncating towards zero
        Statistics.trip_time[self._demand_index][self._type] = jint(
            Statistics.trip_time[self._demand_index][self._type]
            + self._penalty_for_collision)
        Statistics.total_fuel_consumption[self._demand_index][self._type] += \
            self._fuel_consumption

    def calculate_statistics_at_end(self) -> None:
        """Calculates and updates various statistical results when a car
        finishes its trip or the whole simulation finishes."""
        self._end_time = Parameters.simulation_step
        travel_time = self._end_time - self._start_time
        avg_speed = jdiv(self._distance_traveled, travel_time)
        Statistics.no_of_vehicles[self._type] += 1
        Statistics.avg_speed_of_vehicle[self._type] += avg_speed
        Statistics.waiting_time[self._type] += self._waiting_time
        Statistics.total_travel_time[self._type] += travel_time

    def increase_traveled_distance(self, amount: float) -> None:
        self._distance_traveled += amount

    def _check_crash_condition(self, current_speed: float) -> None:
        leader = self.get_probable_leader()
        if leader is not None:
            gap = (leader._distance_in_segment - self._distance_in_segment - self._length)
            leader_speed = (leader.get_speed() if leader._vehicle_id < self._vehicle_id
                            else leader.get_new_speed())
            if current_speed > leader_speed:
                ttc = jdiv(gap, current_speed - leader_speed)

                if ttc <= Parameters.TTC_THRESHOLD:
                    self.get_segment().increase_near_crash_count()

    def get_segment(self):
        return self.get_link().get_segment(self._segment_index)

    def segment_change(self, segment_index: int, strip_index: int) -> None:
        self._segment_index = segment_index
        self._strip_index = strip_index

        self._distance_in_segment = 0.1
        self._passed_sensor = False
        self._occupy_strips()
        self._increase_vehicle_count_on_segment()

    def link_change(self, link_index_on_path: int, link, segment_index: int,
                    strip_index: int) -> None:
        self._link_index_on_path = link_index_on_path
        self._link = link
        self.segment_change(segment_index, strip_index)

    def draw_vehicle(self, trace_writer, g, pixel_per_strip, pixel_per_meter,
                     pixel_per_foot_path_strip) -> None:
        if self._is_in_intersection:
            intersection_strip = self._node.get_intersection_strip(
                self._intersection_strip_index)
            intersection_strip_length = intersection_strip.get_length()

            length = jint(self.get_length() * pixel_per_meter)

            dis = self._distance_in_intersection * pixel_per_meter
            if intersection_strip.is_curved():
                # Inside a roundabout the path bends round the island, so the
                # body is placed along the arc instead of along the chord.
                xp, yp = intersection_strip.point_at(dis)
                xq, yq = intersection_strip.point_at(dis + length)
            else:
                # Using the internal section (ratio) formula, find the coordinates
                # along which vehicles are
                xp = jdiv(dis * intersection_strip.end_point_x
                          + (intersection_strip_length - dis) * intersection_strip.start_point_x,
                          intersection_strip_length)
                yp = jdiv(dis * intersection_strip.end_point_y
                          + (intersection_strip_length - dis) * intersection_strip.start_point_y,
                          intersection_strip_length)
                xq = jdiv((dis + length) * intersection_strip.end_point_x
                          + (intersection_strip_length - (dis + length))
                          * intersection_strip.start_point_x, intersection_strip_length)
                yq = jdiv((dis + length) * intersection_strip.end_point_y
                          + (intersection_strip_length - (dis + length))
                          * intersection_strip.start_point_y, intersection_strip_length)

            x1 = jint(jround(xp))
            y1 = jint(jround(yp))
            x2 = jint(jround(xq))
            y2 = jint(jround(yq))

            width = jint(jround(self._width * pixel_per_meter))

            # find the coordinates of the perpendicularly opposite lower(right) points
            if self._reverse_segment:
                x3 = jint(jround(Utilities.return_x5(x1, y1, x2, y2, width)))
                y3 = jint(jround(Utilities.return_y5(x1, y1, x2, y2, width)))
                x4 = jint(jround(Utilities.return_x6(x1, y1, x2, y2, width)))
                y4 = jint(jround(Utilities.return_y6(x1, y1, x2, y2, width)))
            else:
                x3 = jint(jround(Utilities.return_x3(x1, y1, x2, y2, width)))
                y3 = jint(jround(Utilities.return_y3(x1, y1, x2, y2, width)))
                x4 = jint(jround(Utilities.return_x4(x1, y1, x2, y2, width)))
                y4 = jint(jround(Utilities.return_y4(x1, y1, x2, y2, width)))
            xs = [x1, x2, x4, x3]
            ys = [y1, y2, y4, y3]
        else:
            segment = self._link.get_segment(self._segment_index)
            segment_length = segment.get_length()
            margin_strip = ((self.get_number_of_strips() * Parameters.strip_width
                             - self._width) / 2.0)

            # ---> strategy: if it is the source then change it to int right away
            # Using the internal section (ratio) formula, find the coordinates
            # along which vehicles are
            xp = ((self.get_distance_in_segment() * self._segment_end_point.x
                   + (segment_length - self.get_distance_in_segment())
                   * self._segment_start_point.x) / segment_length * pixel_per_meter)
            yp = ((self.get_distance_in_segment() * self._segment_end_point.y
                   + (segment_length - self.get_distance_in_segment())
                   * self._segment_start_point.y) / segment_length * pixel_per_meter)
            xq = (((self.get_distance_in_segment() + self._length)
                   * self._segment_end_point.x
                   + (segment_length - (self.get_distance_in_segment() + self._length))
                   * self._segment_start_point.x) / segment_length * pixel_per_meter)
            yq = (((self.get_distance_in_segment() + self._length)
                   * self._segment_end_point.y
                   + (segment_length - (self.get_distance_in_segment() + self._length))
                   * self._segment_start_point.y) / segment_length * pixel_per_meter)

            w = (1 * pixel_per_foot_path_strip + (self._strip_index - 1) * pixel_per_strip
                 + margin_strip * pixel_per_meter)
            # find the coordinates of the vehicle's starting and ending upper
            # points depending on which strip their upper(left) portion is on
            if self._reverse_segment:
                x1 = jint(jround(Utilities.return_x5(xp, yp, xq, yq, w)))
                y1 = jint(jround(Utilities.return_y5(xp, yp, xq, yq, w)))
                x2 = jint(jround(Utilities.return_x6(xp, yp, xq, yq, w)))
                y2 = jint(jround(Utilities.return_y6(xp, yp, xq, yq, w)))
            else:
                x1 = jint(jround(Utilities.return_x3(xp, yp, xq, yq, w)))
                y1 = jint(jround(Utilities.return_y3(xp, yp, xq, yq, w)))
                x2 = jint(jround(Utilities.return_x4(xp, yp, xq, yq, w)))
                y2 = jint(jround(Utilities.return_y4(xp, yp, xq, yq, w)))
            width = jint(jround(self._width * pixel_per_meter))
            # find the coordinates of the perpendicularly opposite lower(right) points
            if self._reverse_segment:
                x3 = jint(jround(Utilities.return_x5(xp, yp, xq, yq, w + width)))
                y3 = jint(jround(Utilities.return_y5(xp, yp, xq, yq, w + width)))
                x4 = jint(jround(Utilities.return_x6(xp, yp, xq, yq, w + width)))
                y4 = jint(jround(Utilities.return_y6(xp, yp, xq, yq, w + width)))
            else:
                x3 = jint(jround(Utilities.return_x3(xp, yp, xq, yq, w + width)))
                y3 = jint(jround(Utilities.return_y3(xp, yp, xq, yq, w + width)))
                x4 = jint(jround(Utilities.return_x4(xp, yp, xq, yq, w + width)))
                y4 = jint(jround(Utilities.return_y4(xp, yp, xq, yq, w + width)))
            xs = [x1, x2, x4, x3]
            ys = [y1, y2, y4, y3]

        g.set_color(self._color)
        g.fill_polygon(xs, ys, 4)
        self._vehicle_corners[0] = Point2D(xs[0], ys[0])
        self._vehicle_corners[1] = Point2D(xs[1], ys[1])
        self._vehicle_corners[2] = Point2D(xs[2], ys[2])
        self._vehicle_corners[3] = Point2D(xs[3], ys[3])
        if Parameters.DEBUG_MODE:
            g.set_font("Serif", 64)
            g.draw_string(str(self._vehicle_id), x1, y1)
        if trace_writer is not None:
            trace_writer.write(f"{x1} {x2} {x3} {x4} {y1} {y2} {y3} {y4} ")
            trace_writer.write(f"{self._color.get_red()} {self._color.get_blue()} "
                               f"{self._color.get_green()}\n")
