"""Port of ``thesisfinal.Strip``."""

from __future__ import annotations

from .constants import Constants
from .javacompat import DOUBLE_MAX_VALUE, jdiv, jexp, jint, jmax, jmod
from .parameters import DLC_MODEL, Parameters


class Strip:
    __slots__ = ("_segment_index", "_strip_index", "_is_foot_path_strip",
                 "_parent_link_id", "_vehicle_list", "_pedestrian_list", "_object_list")

    #: ``private static final Random rand = Parameters.random`` -- captured
    #: once, when the class is initialised (i.e. at the first Strip creation,
    #: which happens after the parameters have been read).
    _rand = None

    def __init__(self, seg_index: int, str_index: int, is_foot_path_strip: bool,
                 parent_link_id: int):
        if Strip._rand is None:
            Strip._rand = Parameters.random
        self._segment_index = seg_index
        self._strip_index = str_index
        self._is_foot_path_strip = is_foot_path_strip
        self._parent_link_id = parent_link_id

        # we maintain lists for vehicles, pedestrians and roadside objects
        # present in the strip
        self._vehicle_list = []
        self._pedestrian_list = []
        self._object_list = []

    def is_fp(self) -> bool:
        return self._is_foot_path_strip

    def get_strip_index(self) -> int:
        return self._strip_index

    def add_vehicle(self, v) -> None:
        """Adds a vehicle when it comes over the strip."""
        if self._parent_link_id != v.get_link().get_id():
            print("Very big problem")
        self._vehicle_list.append(v)

    def del_vehicle(self, v) -> None:
        """Removes a vehicle when it leaves the strip."""
        for i, existing in enumerate(self._vehicle_list):
            if existing is v:
                del self._vehicle_list[i]
                return

    def add_pedestrian(self, p) -> None:
        self._pedestrian_list.append(p)

    def del_pedestrian(self, p) -> None:
        for i, existing in enumerate(self._pedestrian_list):
            if existing is p:
                del self._pedestrian_list[i]
                return

    # ---- roadside objects -------------------------------------------------

    def add_object(self, obj) -> None:
        self._object_list.append(obj)

    def del_object(self, obj) -> None:
        for i, existing in enumerate(self._object_list):
            if existing is obj:
                del self._object_list[i]
                return

    def has_gap_for_object(self, p) -> bool:
        threshold_distance = 0.08

        accident = jmod(Strip._rand.next_int(),
                        jint(Parameters.encounter_per_accident)) == 0
        for v in self._vehicle_list:
            upper_limit = v.get_distance_in_segment() + v.get_length() + threshold_distance
            lower_limit = v.get_distance_in_segment()

            if v.is_reverse_segment():
                lower = lower_limit
                upper = upper_limit
                segment_length = v.get_link().get_segment(
                    v.get_segment_index()).get_length()
                lower_limit = segment_length - upper
                upper_limit = segment_length - lower

            if lower_limit < p.get_init_pos() < upper_limit:
                if not accident:
                    return False

                p.get_segment().set_accident_count(p.get_segment().get_accident_count() + 1)
                # System.out.println("Type 2: the pedestrian caused the hit")
                p.in_accident = True
                self.del_object(p)
        return True

    def has_gap_for_move_along_positive(self, p) -> bool:
        dist_in_segment = p.get_distance_in_segment()

        new_dist_in_segment = dist_in_segment + p.get_speed()

        for vehicle in self._vehicle_list:
            v_start = vehicle.get_distance_in_segment()
            v_end = v_start + vehicle.get_length() + vehicle.get_threshold_distance()
            if v_start < new_dist_in_segment < v_end:
                return False

        # checking for objects also
        for obj in self._object_list:
            v_start = obj.get_distance_in_segment()
            v_end = v_start + obj.get_object_length() + Constants.THRESHOLD_DISTANCE
            if v_start < new_dist_in_segment < v_end:
                return False

        return True

    def has_gap_for_move_along_negative(self, p) -> bool:
        dist_in_segment = p.get_distance_in_segment()

        new_dist_in_segment = dist_in_segment - p.get_speed()

        for vehicle in self._vehicle_list:
            v_start = vehicle.get_distance_in_segment()
            v_end = v_start + vehicle.get_length() + vehicle.get_threshold_distance()
            if v_start < new_dist_in_segment < v_end:
                return False

        # checking for objects also
        for obj in self._object_list:
            v_start = obj.get_distance_in_segment()
            v_end = v_start + obj.get_object_length() + Constants.THRESHOLD_DISTANCE
            if v_start < new_dist_in_segment < v_end:
                return False

        return True

    def get_probable_leader_for_my_model(self, follower):
        ped_leader = self.probable_leader_for_pedestrian(follower)
        veh_leader = self.probable_leader(follower)

        if ped_leader is None:
            return veh_leader
        if veh_leader is None:
            return ped_leader

        return (ped_leader
                if ped_leader.get_distance_in_segment()
                < veh_leader.get_distance_in_segment()
                else veh_leader)

    def probable_leader_for_pedestrian(self, follower):
        minimum = DOUBLE_MAX_VALUE
        ped = None
        # as accidents can occur so getLength is omitted
        distance = follower.get_distance_in_segment() + follower.get_length()
        for leader in self._pedestrian_list:
            if leader.get_distance_in_segment() > distance:
                compare = leader.get_distance_in_segment() - distance
                if compare < minimum:
                    minimum = compare
                    ped = leader
        res = None
        if ped is not None:
            res = follower.create_dummy_vehicle_at_pedestrian_position_for_my_model(ped)
        return res

    def get_vehicles_in_range(self, starting_distance, ending_distance):
        v_ids = []

        for vehicle in self._vehicle_list:
            lower_limit = vehicle.get_distance_in_segment()
            upper_limit = vehicle.get_distance_in_segment() + vehicle.get_length()
            if ((lower_limit <= starting_distance <= upper_limit
                 or lower_limit <= ending_distance <= upper_limit)
                    or (starting_distance <= lower_limit <= ending_distance
                        or (ending_distance >= starting_distance
                            and upper_limit <= ending_distance))):
                v_ids.append(vehicle.get_vehicle_id())
        return v_ids

    def get_pedestrians_in_range(self, starting_distance, ending_distance):
        p_ids = []

        for pedestrian in self._pedestrian_list:
            dist_in_segment = pedestrian.get_distance_in_segment()
            if starting_distance < dist_in_segment < ending_distance:
                p_ids.append(pedestrian.get_pedestrian_id())
        return p_ids

    def has_collision_occurred(self, v) -> bool:
        lower_limit = v.get_distance_in_segment()
        upper_limit = lower_limit + v.get_length()

        for vehicle in self._vehicle_list:
            if vehicle is v:
                continue

            if (v.get_segment() is not vehicle.get_segment()
                    or v.is_reverse_segment() != vehicle.is_reverse_segment()):
                continue

            distance = vehicle.get_distance_in_segment()
            if lower_limit < distance < upper_limit:
                return True

        return False

    def get_accident_vehicle(self, v):
        lower_limit = v.get_distance_in_segment()
        upper_limit = lower_limit + v.get_length()

        for vehicle in self._vehicle_list:
            if vehicle is v:
                continue

            distance = vehicle.get_distance_in_segment()
            if lower_limit < distance < upper_limit:
                return vehicle

        return None

    def probable_leader(self, follower):
        """For a vehicle on this strip, finds another vehicle on the same strip
        with the minimum distance ahead."""
        minimum = DOUBLE_MAX_VALUE
        ret = None
        # as accidents can occur so getLength is omitted
        distance = follower.get_distance_in_segment() + follower.get_length()
        for leader in self._vehicle_list:
            if leader.get_distance_in_segment() > distance:
                compare = leader.get_distance_in_segment() - distance
                if compare < minimum:
                    minimum = compare
                    ret = leader
        return ret

    def probable_object_leader(self, follower):
        minimum = DOUBLE_MAX_VALUE
        ret = None
        # as accidents can occur so getLength is omitted
        distance = follower.get_distance_in_segment() + follower.get_length()
        for leader in self._object_list:
            if leader.get_distance_in_segment() > distance:
                compare = leader.get_distance_in_segment() - distance
                if compare < minimum:
                    minimum = compare
                    ret = leader
        return ret

    def probable_follower(self, leader):
        minimum = DOUBLE_MAX_VALUE
        ret = None
        for follower in self._vehicle_list:
            distance = follower.get_distance_in_segment() + follower.get_length()
            if leader.get_distance_in_segment() > distance:
                compare = leader.get_distance_in_segment() - distance
                if compare < minimum:
                    minimum = compare
                    ret = follower
        return ret

    def get_gap_for_forward_movement(self, v) -> float:
        """Checks whether there is space for a vehicle to move forward without
        a collision and keeping a threshold distance."""
        from .vehicle import Vehicle

        threshold_distance = v.get_threshold_distance()

        leader = self.get_probable_leader_for_my_model(v)
        if leader is None:
            forward_gap = (v.get_link().get_segment(v.get_segment_index()).get_length()
                           - v.get_distance_in_segment() - v.get_length())
        else:
            forward_gap = Vehicle.get_gap(leader, v)

        if forward_gap <= 0:
            return 0

        if v.is_reverse_segment():
            lower_limit = (v.get_distance_in_segment() + v.get_length()
                           + v.get_speed() * Constants.TIME_STEP + threshold_distance)
            upper_limit = v.get_distance_in_segment() + v.get_length()
        else:
            upper_limit = (v.get_distance_in_segment() + v.get_length()
                           + v.get_speed() * Constants.TIME_STEP + threshold_distance)
            lower_limit = v.get_distance_in_segment() + v.get_length()

        if v.is_reverse_segment():
            lower = lower_limit
            upper = upper_limit
            segment_length = v.get_link().get_segment(v.get_segment_index()).get_length()
            lower_limit = segment_length - upper
            upper_limit = segment_length - lower

        for pedestrian in self._pedestrian_list:
            pedestrian_pos = pedestrian.get_init_pos()
            if lower_limit < pedestrian_pos < upper_limit:
                forward_gap = 0
        return jmax(forward_gap, 0)

    def check_for_accident(self, v) -> bool:
        for p in list(self._pedestrian_list):
            dist_in_segment = p.get_distance_in_segment()
            if (v.get_distance_in_segment() < dist_in_segment
                    < v.get_distance_in_segment() + v.get_length()):
                print("Accident: SimStep: %d Vehicle %d Pedestrian %d"
                      % (Parameters.simulation_step, v.get_vehicle_id(),
                         p.get_pedestrian_id()))
                p.in_accident = True
                p.set_to_remove(True)
                self.del_pedestrian(p)
                return True
        return False

    def has_gap_for_pedestrian(self, p) -> bool:
        threshold_distance = 0.08

        for v in self._vehicle_list:
            upper_limit = v.get_distance_in_segment() + v.get_length() + threshold_distance
            lower_limit = v.get_distance_in_segment()
            if v.is_reverse_segment():
                lower = lower_limit
                upper = upper_limit
                segment_length = v.get_link().get_segment(
                    v.get_segment_index()).get_length()
                lower_limit = segment_length - upper
                upper_limit = segment_length - lower
            if lower_limit < p.get_init_pos() < upper_limit:
                return False

        # checking gap with respect to objects
        for obj in self._object_list:
            upper_limit = (obj.get_distance_in_segment() + obj.get_object_length()
                           + threshold_distance)
            lower_limit = obj.get_distance_in_segment()
            if obj.is_reverse_segment():
                lower = lower_limit
                upper = upper_limit
                lower_limit = obj.get_segment().get_length() - upper
                upper_limit = obj.get_segment().get_length() - lower
            if lower_limit < p.get_init_pos() < upper_limit:
                return False

        return True

    def has_gap_for_adding_vehicle(self, vehicle_length: float) -> bool:
        """Checks whether there is adequate space for adding a new vehicle.

        A new vehicle enters if it has at least THRESHOLD_DISTANCE gap with the
        leader vehicle after entering.
        """
        lower_limit = 0.08
        upper_limit = Constants.THRESHOLD_DISTANCE + 0.08 + vehicle_length
        for vehicle in self._vehicle_list:
            if lower_limit < vehicle.get_distance_in_segment() < upper_limit:
                return False

        for obj in self._object_list:
            if lower_limit < obj.get_distance_in_segment() < upper_limit:
                return False

        for pedestrian in self._pedestrian_list:
            objpos = pedestrian.get_init_pos()
            if lower_limit < objpos < upper_limit:
                return False
        return True

    def has_gap_for_adding_object(self, object_length: float, initpos: float) -> bool:
        threshold_distance = 0.5

        for vehicle in self._vehicle_list:
            if (vehicle.get_distance_in_segment() + vehicle.get_length()
                    + threshold_distance > initpos
                    and vehicle.get_distance_in_segment()
                    < initpos + object_length + threshold_distance):
                return False

        for obj in self._object_list:
            if (obj.get_distance_in_segment() + obj.get_object_length()
                    + threshold_distance > initpos
                    and obj.get_distance_in_segment()
                    < initpos + object_length + threshold_distance):
                return False

        return True

    def has_gap_for_strip_change(self, subject_vehicle, leader, follower) -> bool:
        """Similar to :meth:`get_gap_for_forward_movement` but ignores vehicle
        speed; checks whether there is enough space for the given vehicle's
        forward movement."""
        from .vehicle import Vehicle

        threshold_distance = subject_vehicle.get_threshold_distance()
        lower_limit1 = subject_vehicle.get_distance_in_segment() - threshold_distance
        upper_limit1 = (subject_vehicle.get_distance_in_segment()
                        + subject_vehicle.get_length() + threshold_distance)

        for vehicle in self._vehicle_list:
            if subject_vehicle is vehicle:
                continue
            lower_limit2 = vehicle.get_distance_in_segment() - threshold_distance
            upper_limit2 = (vehicle.get_distance_in_segment() + vehicle.get_length()
                            + threshold_distance)
            if ((lower_limit2 <= lower_limit1 <= upper_limit2
                 or lower_limit2 <= upper_limit1 <= upper_limit2)
                    or (lower_limit1 <= lower_limit2 <= upper_limit1
                        or lower_limit1 <= upper_limit2 <= upper_limit1)):
                return False

        if subject_vehicle.is_reverse_segment():
            lower = lower_limit1
            upper = upper_limit1
            segment_length = subject_vehicle.get_link().get_segment(
                subject_vehicle.get_segment_index()).get_length()
            lower_limit1 = segment_length - upper
            upper_limit1 = segment_length - lower

        for pedestrian in self._pedestrian_list:
            objpos = pedestrian.get_init_pos()
            if lower_limit1 < objpos < upper_limit1:
                return False

        if subject_vehicle.get_dlc_model() == DLC_MODEL.NAIVE_MODEL:
            return True
        if subject_vehicle.get_dlc_model() in (DLC_MODEL.GIPPS_MODEL,
                                               DLC_MODEL.GHR_MODEL):
            # checking for feasibility of changing lane
            target_leader = subject_vehicle.get_probable_leader()
            target_follower = subject_vehicle.get_probable_follower()
            # is_not_feasible: True means not feasible; Gipps' method computes
            # feasibility using velocity.
            # probabilty: gap acceptance probability, see
            # https://www.civil.iitb.ac.in/tvm/nptel/534_LaneChange/web/web.html#x1-50002.2
            if target_leader is not None:
                # lane changing feasibility calculation
                if subject_vehicle.get_dlc_model() == DLC_MODEL.GIPPS_MODEL:
                    speed_wrt_leader = Vehicle.get_speed_for_braking(target_leader,
                                                                     subject_vehicle)
                    deceleration_wrt_leader = ((speed_wrt_leader
                                                - subject_vehicle.get_speed())
                                               / Vehicle.TIME_STEP)
                    is_not_feasible = (deceleration_wrt_leader
                                       < subject_vehicle.get_max_braking())
                elif subject_vehicle.get_dlc_model() == DLC_MODEL.GHR_MODEL:
                    is_not_feasible = (Vehicle.get_acceleration_ghr_model(
                        target_leader, subject_vehicle)
                        < subject_vehicle.get_max_braking())
                else:
                    # dummy
                    print("should not come here")
                    is_not_feasible = False

                # gap acceptance probability calculation
                lead_time_gap = jdiv(Vehicle.get_gap(target_leader, subject_vehicle),
                                     subject_vehicle.get_speed())
                if lead_time_gap > Vehicle.SAFE_TIME_GAP:
                    probabilty = 1 - jexp(-Vehicle.LAMBDA
                                          * (lead_time_gap - Vehicle.SAFE_TIME_GAP))
                else:
                    probabilty = 0  # acceleration model is also incorporated
            else:
                is_not_feasible = False
                probabilty = 1

            if target_follower is not None:
                # lane changing feasibility calculation
                lag_gap = Vehicle.get_gap(subject_vehicle, target_follower)
                modified_lag_gap = (lag_gap + subject_vehicle.get_speed()
                                    - target_follower.get_speed())
                speed_of_follower = Vehicle.get_speed_for_braking(
                    subject_vehicle, target_follower, modified_lag_gap)

                if subject_vehicle.get_dlc_model() == DLC_MODEL.GIPPS_MODEL:
                    deceleration_of_follower = ((speed_of_follower
                                                 - target_follower.get_speed())
                                                / Vehicle.TIME_STEP)
                    is_not_feasible = is_not_feasible or (
                        deceleration_of_follower < target_follower.get_max_braking())
                elif subject_vehicle.get_dlc_model() == DLC_MODEL.GHR_MODEL:
                    is_not_feasible = is_not_feasible or (
                        Vehicle.get_acceleration_ghr_model(subject_vehicle,
                                                           target_follower)
                        < target_follower.get_max_braking())

                # gap acceptance probability calculation
                lag_time_gap = jdiv(modified_lag_gap, target_follower.get_speed())
                if lag_time_gap > Vehicle.SAFE_TIME_GAP:
                    probabilty *= 1 - jexp(-Vehicle.LAMBDA
                                           * (lag_time_gap - Vehicle.SAFE_TIME_GAP))
                else:
                    probabilty = 0  # acceleration model is also incorporated

            if is_not_feasible:
                # lane changing not feasible
                return False
            # feasibility checking ended; after this lane changing is feasible

            # check for gap acceptance (probabilistic method)
            r = Strip._rand.next_double()
            return r < probabilty

        if subject_vehicle.get_dlc_model() == DLC_MODEL.MOBIL_MODEL:
            target_follower = subject_vehicle.get_probable_follower()
            # this was previously the leader of vehicle_n
            target_leader = subject_vehicle.get_probable_leader()

            b_safe = -4  # m/s^2
            p = 1
            a_th = 0.1  # m/s^2

            a_s = Vehicle.get_idm_acceleration(leader, subject_vehicle)
            a_s_1 = Vehicle.get_idm_acceleration(subject_vehicle, follower)
            a_n = Vehicle.get_idm_acceleration(target_leader, target_follower)

            a_s_prime = Vehicle.get_idm_acceleration(target_leader, subject_vehicle)
            a_s_1_prime = Vehicle.get_idm_acceleration(leader, follower)
            a_n_prime = Vehicle.get_idm_acceleration(subject_vehicle, target_follower)

            condition1 = a_n_prime >= b_safe
            condition2 = ((a_s_prime - a_s)
                          + p * (a_n_prime - a_n + a_s_1_prime - a_s_1)) > a_th

            return condition1 and condition2

        return True
