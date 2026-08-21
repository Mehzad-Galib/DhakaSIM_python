"""Port of ``thesisfinal.Utilities``."""

from __future__ import annotations

import math

from .constants import Constants
from .javacompat import (Color, GammaDistribution, JavaRandom, jbool, jexp, jint,
                         jmin, jround, lines_intersect)
from .parameters import (CAR_FOLLOWING_MODEL, DLC_MODEL, Parameters,
                         VEHICLE_GENERATION_RATE, scratch_random)
from .point2d import Point2D

#: ``Utilities.gb`` -- static, so it is built while ``Parameters.seed`` is
#: still 0, before ``initialize()`` randomises it.  Seeding with 0 reproduces
#: that.
gb = GammaDistribution(1.68256, 0.04169, seed=Parameters.seed)


def return_x3(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = -v
    v = temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return x1 + d * u


def return_y3(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = -v
    v = temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return y1 + d * v


def return_x4(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = -v
    v = temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return x2 + d * u


def return_y4(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = -v
    v = temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return y2 + d * v


def return_x5(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = v
    v = -temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return x1 + d * u


def return_y5(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = v
    v = -temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return y1 + d * v


def return_x6(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = v
    v = -temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return x2 + d * u


def return_y6(x1, y1, x2, y2, d):
    u = x2 - x1
    v = y2 - y1
    temp = u
    u = v
    v = -temp
    denom = math.sqrt(u * u + v * v)
    u = u / denom
    v = v / denom
    return y2 + d * v


def get_distance(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))


def gaussian_pdf(x, mean, std_dev):
    return ((1 / (std_dev * math.sqrt(2 * math.pi)))
            * jexp(-0.5 * math.pow((x - mean) / std_dev, 2)))


def cumulative_distribution_function(x, mean, std_dev):
    """CDF for a Gaussian distribution."""
    return 0.5 * (1 + erf((x - mean) / (std_dev * math.sqrt(2))))


def erf(z):
    """Numerical approximation of the error function used by the CDF."""
    t = 1.0 / (1.0 + 0.5 * abs(z))
    tau = t * jexp(-z * z - 1.26551223 + t * (1.00002368
                   + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806
                   + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587
                   + t * (-0.82215223 + t * 0.17087277)))))))))
    return 1 - tau if z >= 0 else tau - 1


def do_intersect(x1, y1, x2, y2, x3, y3, x4, y4) -> bool:
    temp1 = Point2D(x1, y1)
    temp2 = Point2D(x2, y2)
    temp3 = Point2D(x3, y3)
    temp4 = Point2D(x4, y4)
    intersects = lines_intersect(temp1.x, temp1.y, temp2.x, temp2.y,
                                 temp3.x, temp3.y, temp4.x, temp4.y)
    shares_any_point = _share_any_point(temp1, temp2, temp3, temp4)
    return intersects and not shares_any_point


def _share_any_point(a, b, c, d) -> bool:
    if _is_point_on_the_line(a, b, c):
        return True
    elif _is_point_on_the_line(a, b, d):
        return True
    elif _is_point_on_the_line(c, d, a):
        return True
    else:
        return _is_point_on_the_line(c, d, b)


def _is_point_on_the_line(a, b, p) -> bool:
    if b.x == a.x:
        # Java computes an infinite slope here
        return abs(a.x - p.x) < 0.001
    m = (b.y - a.y) / (b.x - a.x)
    if math.isinf(m):
        return abs(a.x - p.x) < 0.001
    return abs((p.y - a.y) - m * (p.x - a.x)) < 0.001


def get_weight(leader, follower, side_strips_to_consider: int) -> float:
    leader_start_strip = leader.get_strip_index()
    leader_number_strips = leader.get_number_of_strips()
    leader_end_strip = leader_start_strip + leader_number_strips - 1

    follower_start_strip = follower.get_start_strip_for_my_model(side_strips_to_consider)
    follower_end_strip = follower.get_end_strip_for_my_model(side_strips_to_consider)
    follower_number_strips = follower_end_strip - follower_start_strip + 1

    start_overlap = max(leader_start_strip, follower_start_strip)
    end_overlap = min(leader_end_strip, follower_end_strip)
    total_overlap = end_overlap - start_overlap + 1

    temp1 = (start_overlap + end_overlap) / 2.0
    temp2 = (temp1 - follower_start_strip + 0.5)
    height = temp2 / follower_number_strips

    normalize_height = jmin(height, 1 - height)

    # TODO Why 0.2???
    # weight = total_overlap * normalize_height if total_overlap > 0 else 0.2

    return total_overlap * normalize_height


def get_weight_by_type(leader) -> float:
    if leader.get_type() == 12:
        return Parameters.PEDESTRIAN_WEIGHT
    return 1


# cycle, rickshaw, van, bike, car, car, car, CNG, bus, bus, truck, truck
_CAR_WIDTHS = (0.55, 1.20, 1.22, 0.6, 1.7, 1.76, 1.78, 1.4, 2.4, 2.30, 2.44, 2.46, 0.4)
_CAR_LENGTHS = (1.9, 2.4, 2.5, 1.8, 5, 4.55, 4.3, 2.65, 10.3, 9.5, 7.2, 7.5, 0.4)
_CAR_SPEEDS = (15, 8, 7, 100, 100, 60, 110, 40, 30, 40, 35, 45, 5.1)  # km/hour
# m/s^2; approximately maxSpeed/10 i.e. takes 10 seconds to reach max speed
_CAR_ACCELERATIONS = (0.42, 0.25, 0.20, 2.80, 3, 2.80, 3.34, 1.11, 0.84, 1.12, 0.96,
                      1.24, 0.1)


def get_car_width(type_: int) -> float:
    index = abs(type_) % min(Constants.TYPES_OF_CARS, len(_CAR_WIDTHS))
    return _CAR_WIDTHS[index]


def get_car_length(type_: int) -> float:
    index = abs(type_) % min(Constants.TYPES_OF_CARS, len(_CAR_LENGTHS))
    return _CAR_LENGTHS[index]


def get_car_max_speed(type_: int) -> float:
    """:return: the maximum speed of the car in m/s"""
    index = abs(type_) % min(Constants.TYPES_OF_CARS, len(_CAR_SPEEDS))
    speed = _CAR_SPEEDS[index] * 1000 / 3600  # m/s
    if type_ == Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE:
        nd = Parameters.random.next_gaussian()
        return precision2(speed + nd * speed / 5.0)
    return precision2(speed)


def get_car_acceleration(type_: int) -> float:
    """:return: the acceleration in m/s^2"""
    index = abs(type_) % min(Constants.TYPES_OF_CARS, len(_CAR_ACCELERATIONS))

    if type_ == Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE:
        nd = Parameters.random.next_gaussian()
        return precision2(_CAR_ACCELERATIONS[index]
                          + nd * _CAR_ACCELERATIONS[index] / 5.0)  # m/s^2
    return _CAR_ACCELERATIONS[index]


def get_dlc_model(type_: int):
    if type_ == Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE:
        return DLC_MODEL.NAIVE_MODEL
        # return Parameters.lane_changing_model
    return Parameters.lane_changing_model


def number_of_strips(type_: int) -> int:
    return jint(math.ceil(get_car_width(type_) / Parameters.strip_width))


# ---------------------------------------------------------------------------
# roadside objects / side friction elements
# ---------------------------------------------------------------------------

def pedestrian_blockage_strip(distance_from_footpath: float) -> int:
    return jint(math.ceil(distance_from_footpath / Parameters.strip_width))


def _get_probability_density_of_object_blockage(distributions, x: float) -> float:
    y = 0
    for distribution in distributions:
        y += distribution.get_probability_density_with_factor(x)
    return y


#: Cache of the deterministic ``(pdf values, running sum)`` per object type.
#: The Java code recomputes both on every call; the values only depend on the
#: object type, and caching them keeps the exact same floating point
#: accumulation order (and therefore the same results) while making object
#: generation affordable in Python.
_blockage_cache: dict[int, tuple[list[float], float]] = {}


def get_random_from_multiple_gaussian_distribution_of_objects_blockage(object_type: int
                                                                      ) -> float:
    if object_type == 1:
        distributions_of_object = Constants.STANDING_PEDESTRIAN_DISTRIBUTIONS
        start_index = Constants.STANDING_PEDESTRIAN_MIN_BLOCKAGE
        stop_index = Constants.STANDING_PEDESTRIAN_MAX_BLOCKAGE
    elif object_type == 2:
        distributions_of_object = Constants.PARKED_CAR_DISTRIBUTIONS
        start_index = Constants.PARKED_CAR_MIN_BLOCKAGE
        stop_index = Constants.PARKED_CAR_MAX_BLOCKAGE
    elif object_type == 3:
        distributions_of_object = Constants.PARKED_RICKSHAW_DISTRIBUTIONS
        start_index = Constants.PARKED_RICKSHAW_MIN_BLOCKAGE
        stop_index = Constants.PARKED_RICKSHAW_MAX_BLOCKAGE
    elif object_type == 4:
        distributions_of_object = Constants.PARKED_CNG_DISTRIBUTIONS
        start_index = Constants.PARKED_CNG_MIN_BLOCKAGE
        stop_index = Constants.PARKED_CNG_MAX_BLOCKAGE
    elif object_type == 5:
        distributions_of_object = Constants.WALKING_PEDESTRIAN_DISTRIBUTIONS
        start_index = Constants.WALKING_PEDESTRIAN_MIN_BLOCKAGE
        stop_index = Constants.WALKING_PEDESTRIAN_MAX_BLOCKAGE
    else:
        print("Error: Distribution for objectType=" + str(object_type)
              + " does not exist.")
        return 0  # Distance from footpath will be zero.

    cached = _blockage_cache.get(object_type)
    if cached is None:
        densities = []
        random_multiplier = 0.0
        i = start_index
        while i <= stop_index:
            density = _get_probability_density_of_object_blockage(
                distributions_of_object, i)
            densities.append(density)
            random_multiplier += density
            i += 0.01
        cached = (densities, random_multiplier)
        _blockage_cache[object_type] = cached
    densities, random_multiplier = cached

    r = scratch_random()
    random_double = r.next_double() * random_multiplier

    # For each possible return value, subtract the function value for that
    # value until it drops below 0; then return the current value.
    the_random_number = start_index
    step = 0
    random_double -= densities[0] if densities else \
        _get_probability_density_of_object_blockage(distributions_of_object,
                                                   the_random_number)
    while random_double >= 0:
        the_random_number += 0.01
        step += 1
        if step < len(densities):
            random_double -= densities[step]
        else:
            random_double -= _get_probability_density_of_object_blockage(
                distributions_of_object, the_random_number)

    return the_random_number


def rand_int(minimum: int, maximum: int) -> int:
    # next_int_bound is normally exclusive of the top value,
    # so add 1 to make it inclusive
    return Parameters.random.next_int_bound((maximum - minimum) + 1) + minimum


def get_new_end_point_for_intersection_strip(vehicle, is_, new_strip_index) -> Point2D:
    entering_segment = is_.entering_segment
    w1 = (1 * Parameters.pixel_per_footpath_strip
          + (new_strip_index - 1) * Parameters.pixel_per_strip)
    x_1 = entering_segment.get_start_x() * Parameters.pixel_per_meter
    y_1 = entering_segment.get_start_y() * Parameters.pixel_per_meter
    x_2 = entering_segment.get_end_x() * Parameters.pixel_per_meter
    y_2 = entering_segment.get_end_y() * Parameters.pixel_per_meter

    x_3 = return_x3(x_1, y_1, x_2, y_2, w1)
    y_3 = return_y3(x_1, y_1, x_2, y_2, w1)
    x_4 = return_x4(x_1, y_1, x_2, y_2, w1)
    y_4 = return_y4(x_1, y_1, x_2, y_2, w1)
    veh_x = vehicle.get_seg_end_x() * Parameters.pixel_per_meter
    veh_y = vehicle.get_seg_end_y() * Parameters.pixel_per_meter
    dist1 = (x_3 - veh_x) * (x_3 - veh_x) + (y_3 - veh_y) * (y_3 - veh_y)
    dist2 = (x_4 - veh_x) * (x_4 - veh_x) + (y_4 - veh_y) * (y_4 - veh_y)
    if dist1 < dist2:  # choosing closest segment end point from vehicle
        return Point2D(x_3, y_3)
    return Point2D(x_4, y_4)


def draw_trace(trace_reader, g) -> None:
    """Replay one simulation step from ``trace.txt`` onto *g*."""
    s = None
    while True:
        s = trace_reader.readline()
        if not s:
            s = None
            break
        s = s.rstrip("\n")
        if s.startswith("Simulation"):
            break
    # here first line Current pedestrians
    # then the pedestrians list
    # then 1 line Current vehicles
    # then the vehicles list
    if s is not None and s.startswith("Simulation"):
        tokenizer = s.split()
        sim_step = int(tokenizer[1])
        if Parameters.show_progress_slider is not None:
            Parameters.show_progress_slider.set_value(sim_step)
    trace_reader.readline()

    if Parameters.across_pedestrian_mode:
        while True:
            line = trace_reader.readline()
            if not line or line.startswith("Current"):
                break
            tokenizer = line.split()
            x = int(tokenizer[0])
            y = int(tokenizer[1])
            in_accident = jbool(tokenizer[2])
            g.begin_prop("pedestrian")
            if in_accident:
                g.set_color(Color.RED)
                g.fill_oval(x, y, 10, 10)
            else:
                g.set_color(Constants.pedestrian_color)
                g.fill_oval(x, y, 7, 7)
            g.end_prop()

    # Lines after current vehicles
    while True:
        line = trace_reader.readline()
        # "Current Objects" ends the vehicle block just as "End Step" does: the
        # writer emits the objects header between the two, and stopping only on
        # "End" walks straight into it and fails to parse it as a vehicle.
        # (The objects themselves are never written -- drawing one does not
        # touch the trace -- so the header is immediately followed by the end
        # of the step and nothing is lost by stopping here.)
        if not line or line.startswith("End") or line.startswith("Current"):
            break
        tokenizer = line.split()
        xs = [0] * 4
        ys = [0] * 4
        xs[0] = int(tokenizer[0])
        xs[1] = int(tokenizer[1])
        xs[3] = int(tokenizer[2])
        xs[2] = int(tokenizer[3])

        ys[0] = int(tokenizer[4])
        ys[1] = int(tokenizer[5])
        ys[3] = int(tokenizer[6])
        ys[2] = int(tokenizer[7])

        red = int(tokenizer[8])
        green = int(tokenizer[9])
        blue = int(tokenizer[10])

        g.set_color(Color(red, green, blue))
        # The trace records corners and a colour but not the vehicle type, so
        # the 3D view is left to infer the model from the footprint; in 2D the
        # pair of calls does nothing.
        g.begin_prop("vehicle", None)
        g.fill_polygon(xs, ys, 4)
        g.end_prop()


def precision2(d: float) -> float:
    return jint(d * 10000) / 10000.0


def truncated_gaussian(sigma: float, lb: float, ub: float) -> float:
    x = 0
    for _ in range(5):
        x = Parameters.random.next_gaussian() * sigma
        if lb <= x <= ub:
            return x
    if x <= lb:
        return lb
    elif x >= ub:
        return ub
    else:
        return 0


def get_collision_penalty() -> float:
    """:return: a time penalty for collision in minutes following a gamma
    random variable"""
    return gb.sample()


def initialize() -> None:
    """Read ``input/parameter.txt`` into :class:`Parameters`."""
    try:
        with open("input/parameter.txt", "r") as bufferedReader:
            for data_line in bufferedReader:
                data_line = data_line.rstrip("\n")
                string_tokenizer = data_line.split()
                if not string_tokenizer:
                    # Java's StringTokenizer would throw here; the shipped
                    # files have no blank lines.
                    raise IndexError("empty line in input/parameter.txt")
                apply_setting(string_tokenizer[0], string_tokenizer[1])

        # Not in finalise_settings(): this transform feeds on its own output,
        # so running it twice would give a different answer.
        value = Parameters.encounter_per_accident
        if value < 1:
            value = 100 / value
        else:
            value = 100 - value + 1
        Parameters.encounter_per_accident = value

        finalise_settings()
    except OSError as ex:
        print(f"SEVERE: {ex}")


def apply_setting(name: str, value: str) -> bool:
    """Apply one ``Name Value`` pair to :class:`Parameters`.

    Split out of :func:`initialize` so that the same dispatch serves both
    ``input/parameter.txt`` and a command-line ``--set Name=Value``; there is
    one definition of what a setting means, not two.

    The Java ``switch`` in ``Utilities.initialize`` is missing ``break``
    statements after ``DLC_model``, ``CF_model`` and ``VehicleGenerationRate``,
    so those lines also execute the following case(s).  That fall-through is
    reproduced below because it changes the configuration the simulation runs
    with (a ``DLC_model`` line, for instance, also sets the car-following
    model from the same value).

    :return: whether the name was recognised.  Unknown names are ignored, so
        ``parameter.txt`` can carry notes; the caller decides whether to warn.
    """
    if name == "SimulationEndTime":
        Parameters.simulation_end_time = int(value)
    elif name == "PixelPerMeter":
        Parameters.pixel_per_meter = int(value)
    elif name == "SimulationSpeed":
        Parameters.simulation_speed = int(value)
    elif name == "EncounterPerAccident":
        Parameters.encounter_per_accident = float(value)
    elif name == "StripWidth":
        Parameters.strip_width = float(value)
    elif name == "FootpathStripWidth":
        Parameters.footpath_strip_width = float(value)
    elif name == "MaximumSpeed":
        # Written in km/h -- the unit a speed limit is actually quoted in, and
        # the unit the GUI field is labelled with -- and held internally in m/s
        # like every other speed.  The Java original read this field as m/s,
        # which made the shipped 100 mean 360 km/h: no limit at all, since the
        # fastest vehicle type manages 110 km/h.  Set 360 here to reproduce that.
        Parameters.maximum_speed = precision2(float(value) * 1000 / 3600)
    elif name == "AcrossPedestrianMode":
        Parameters.across_pedestrian_mode = value.lower() == "on"
    elif name == "AlongPedestrianMode":
        Parameters.along_pedestrian_mode = value.lower() == "on"
    elif name == "DebugMode":
        Parameters.DEBUG_MODE = value.lower() == "on"
    elif name == "ObjectMode":
        Parameters.OBJECT_MODE = value.lower() == "on"
    elif name == "TraceMode":
        Parameters.TRACE_MODE = value.lower() == "on"
    elif name == "RandomSeed":
        # the value in the file is deliberately ignored -- see `Seed` below
        Parameters.seed = JavaRandom().next_int_bound(101)
    elif name == "Seed":
        # Unlike RandomSeed, this one is honoured, which is what makes a run
        # repeatable and a seeded experiment sweep possible.
        Parameters.seed = int(value)
    elif name == "VehicleMixOverride":
        Parameters.VEHICLE_MIX_OVERRIDE = value.lower() == "on"
    elif name == "StatsDir":
        Parameters.STATS_DIR = value.strip()
    elif name == "DemandOverride":
        Parameters.DEMAND_OVERRIDE = float(value)
    elif name == "DemandOffset":
        Parameters.DEMAND_OFFSET = int(value)
    elif name == "NetworkRoadLength":
        # "auto" (or any negative value) measures it from the network
        Parameters.NETWORK_ROAD_LENGTH = (
            -1.0 if value.lower() == "auto" else float(value))
    elif name == "SignalChangeDuration":
        Parameters.SIGNAL_CHANGE_DURATION = int(value)
        Parameters.SIGNAL_CHANGE_DURATION = jint(jround(
            Parameters.SIGNAL_CHANGE_DURATION / Constants.TIME_STEP))
    elif name == "DefaultTranslateX":
        Parameters.DEFAULT_TRANSLATE_X = float(value)
    elif name == "DefaultTranslateY":
        Parameters.DEFAULT_TRANSLATE_Y = float(value)
    elif name == "CenteredView":
        Parameters.CENTERED_VIEW = value.lower() == "on"
    elif name == "DLC_model":
        _apply_dlc_model(value)
        _apply_cf_model(value)       # Java switch falls through
        _apply_slow_vehicle(value)   # Java switch falls through
    elif name == "CF_model":
        _apply_cf_model(value)
        _apply_slow_vehicle(value)   # Java switch falls through
    elif name == "SlowVehicle":
        _apply_slow_vehicle(value)
    elif name == "MediumVehicle":
        Parameters.medium_vehicle_percentage = float(value)
    elif name == "FastVehicle":
        Parameters.fast_vehicle_percentage = float(value)
    elif name == "TTC_Threshold":
        Parameters.TTC_THRESHOLD = float(value)
    elif name == "VehicleGenerationRate":
        _apply_vehicle_generation_rate(value)
        _apply_error_mode(value)     # Java switch falls through
    elif name == "ErrorMode":
        _apply_error_mode(value)
    elif name == "FTMethod":
        Parameters.FT_METHOD = int(value)
    elif name == "NoOfReadings":
        Parameters.NO_OF_READINGS = int(value)
    elif name == "MFactor":
        Parameters.M_FACTOR = float(value)
    elif name == "GUIMode":
        Parameters.GUI_MODE = value.lower() == "on"
    elif name == "ALPHA":
        Parameters.ALPHA = float(value)
    elif name == "BETA":
        Parameters.BETA = float(value)
    elif name == "ETA":
        Parameters.ETA = float(value)
    elif name == "AcrossPedestrianLimit":
        Parameters.ACROSS_PEDESTRIAN_LIMIT = int(value)
    elif name == "AcrossPedestrianPerHour":
        Parameters.ACROSS_PEDESTRIAN_PER_HOUR = float(value)
    elif name == "AlongPedestrianPerHour":
        Parameters.ALONG_PEDESTRIAN_PER_HOUR = float(value)
    elif name == "AlongPedestrianPercentage":
        Parameters.ALONG_PEDESTRIAN_PERCENTAGE = int(value)
    elif name == "DensityPercentage":
        Parameters.DENSITY_PERCENTAGE = int(value)
    elif name == "PedestrianWeight":
        Parameters.PEDESTRIAN_WEIGHT = float(value)
    elif name == "PedestrianRandomLaneChangePercentage":
        Parameters.PEDESTRIAN_RANDOM_LANE_CHANGE_PERCENTAGE = int(value)
    elif name == "PedestrianLeftBiasPercentage":
        Parameters.PEDESTRIAN_LEFT_BIAS_PERCENTAGE = int(value)
    elif name == "PenaltyWait":
        Parameters.PENALTY_WAIT = value.lower() == "on"
    elif name == "ConsiderMinimum":
        Parameters.CONSIDER_MINIMUM = value.lower() == "on"
    elif name == "NoOfRoutes":
        Parameters.NO_OF_ROUTES_FOR_STAT = int(value)
    elif name == "ReportAnimationFrames":
        Parameters.REPORT_ANIMATION_FRAMES = int(value)
    elif name == "Network":
        Parameters.NETWORK_DIR = value.strip()
    elif name == "GeometryMode":
        Parameters.GEOMETRY_MODE = value.lower() == "on"
    elif name == "Render3D":
        Parameters.RENDER_3D = value.lower() == "on"
    elif name == "TimeOfDay":
        try:
            Parameters.TIME_OF_DAY = int(value)
        except ValueError:
            Parameters.TIME_OF_DAY = -1
    elif name == "BrakeHard":
        Parameters.BRAKE_HARD = value.lower() == "on"
    elif name == "AcrossPedestrianPercentage":
        print("Should change the parameter name: AcrossPedestrianPerHour")
    else:
        return False
    return True


def finalise_settings() -> None:
    """Recompute everything derived from the raw settings.

    Split out of :func:`initialize` so an override applied after the file has
    been read -- ``--seed 7``, ``--set StripWidth=2.5`` -- still gets the
    derived values it implies: a new strip width changes the pixels per strip,
    a new seed means a new RNG.  Safe to call repeatedly, which is why the
    ``EncounterPerAccident`` transform stays behind in :func:`initialize`.
    """
    Parameters.pixel_per_footpath_strip = (Parameters.pixel_per_meter
                                          * Parameters.footpath_strip_width)
    Parameters.pixel_per_strip = Parameters.pixel_per_meter * Parameters.strip_width
    Parameters.random = (JavaRandom() if Parameters.seed < 0
                         else JavaRandom(Parameters.seed))
    if Parameters.VEHICLE_MIX_OVERRIDE:
        # assert round(slow + medium + fast) == 100.0 -- assertions are
        # disabled in the Java build, and the values are overwritten anyway
        Parameters.slow_vehicle_percentage = 25
        Parameters.medium_vehicle_percentage = 25
        Parameters.fast_vehicle_percentage = 50


def _apply_dlc_model(value: str) -> None:
    o = int(value)
    if o == 0:
        Parameters.lane_changing_model = DLC_MODEL.NAIVE_MODEL
    elif o == 1:
        Parameters.lane_changing_model = DLC_MODEL.GHR_MODEL
    elif o == 2:
        Parameters.lane_changing_model = DLC_MODEL.GIPPS_MODEL
    elif o == 3:
        Parameters.lane_changing_model = DLC_MODEL.MOBIL_MODEL
    else:
        Parameters.lane_changing_model = DLC_MODEL.GIPPS_MODEL


def _apply_cf_model(value: str) -> None:
    p = int(value)
    models = {
        0: CAR_FOLLOWING_MODEL.HYBRID_MODEL,
        1: CAR_FOLLOWING_MODEL.NAIVE_MODEL,
        2: CAR_FOLLOWING_MODEL.GIPPS_MODEL,   # no collision
        3: CAR_FOLLOWING_MODEL.KRAUSS_MODEL,  # 100700+ // OK
        4: CAR_FOLLOWING_MODEL.GFM_MODEL,     # no collision
        5: CAR_FOLLOWING_MODEL.IDM_MODEL,     # 61021 // OK
        6: CAR_FOLLOWING_MODEL.RVF_MODEL,     # 8284
        7: CAR_FOLLOWING_MODEL.VFIAC_MODEL,   # 4654
        8: CAR_FOLLOWING_MODEL.OVCM_MODEL,    # no collision (1s -> time step) 15577
        9: CAR_FOLLOWING_MODEL.KFTM_MODEL,    # 12
        10: CAR_FOLLOWING_MODEL.HDM_MODEL,    # 22742
        11: CAR_FOLLOWING_MODEL.SBM_MODEL,    # 8363
        12: CAR_FOLLOWING_MODEL.MY_MODEL,     # no collision
        13: CAR_FOLLOWING_MODEL.MODIFIED_NEWTONIAN_MODEL,
    }
    Parameters.car_following_model = models.get(p, CAR_FOLLOWING_MODEL.HYBRID_MODEL)


def _apply_slow_vehicle(value: str) -> None:
    Parameters.slow_vehicle_percentage = float(value)


def _apply_vehicle_generation_rate(value: str) -> None:
    q = int(value)
    if q == 0:
        Parameters.vehicle_generation_rate = VEHICLE_GENERATION_RATE.CONSTANT
    elif q == 1:
        Parameters.vehicle_generation_rate = VEHICLE_GENERATION_RATE.POISSON
    else:
        Parameters.vehicle_generation_rate = VEHICLE_GENERATION_RATE.CONSTANT


def _apply_error_mode(value: str) -> None:
    Parameters.ERROR_MODE = value.lower() == "on"


def index_trace_file() -> None:
    if Parameters.TRACE_MODE:
        Parameters.simulation_step_line_nos = []
        try:
            with open("trace.txt", "r") as trace_reader:
                for line_number, s in enumerate(trace_reader, start=1):
                    if s.startswith("Sim"):
                        Parameters.simulation_step_line_nos.append(line_number)
        except OSError as ex:
            print(f"{type(ex).__name__}: {ex}")
