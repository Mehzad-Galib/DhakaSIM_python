"""Port of ``thesisfinal.Processor`` -- the simulation engine."""

from __future__ import annotations

import math
import os

from .constants import Constants
from .demand import Demand
from .intersection_strip import IntersectionStrip
from .javacompat import (Color, DOUBLE_MAX_VALUE, jabs_int, jdiv, jformat,
                         jint, jlog, jmax, jmin, jround, jround_to_int, jstr)
from .link import Link
from .link_segment_orientation import get_link_and_segment_orientation
from .node import Node
from .parameters import Parameters, VEHICLE_GENERATION_RATE, scratch_random
from .path import Path
from .pedestrian import Pedestrian
from .point2d import Point2D
from .roadside_object import Object
from .segment import Segment
from .signal import SIGNAL
from .statistics import Statistics
from .strip import Strip
from .vehicle import Vehicle
from . import utilities as Utilities


class Processor:
    # variables for roadside objects (static in Java)
    number_of_vehicles = 0
    number_of_objects = 0
    number_of_standing_pedestrians = 0
    number_of_parked_cars = 0
    number_of_parked_rickshaws = 0
    number_of_parked_cngs = 0

    def __init__(self):
        self.node_list = []
        self.link_list = []
        self.pedestrians = []
        self.vehicle_list = []
        self.object_list = []
        self.demand_list = []
        self.path_list = []
        self.next_generation_time = []
        self.number_of_vehicles_to_generate = []
        self.intersection_list = []
        self.random = Parameters.random
        self.vehicle_id = 0
        self.object_id = 0
        self._recorder = None  # lazily created run-visualisation recorder
        self.pedestrian_id = 0
        self.start_along_ped = Parameters.along_pedestrian_mode
        self.mid_point = None

        # These are class-level (Java `static`), and a fresh JVM always started
        # them at zero.  Reset them here so a second Processor in the same
        # process -- the GUI's "New simulation", or a batch loop -- begins from
        # the same state the first one did, rather than inheriting its
        # roadside-object population.
        Processor.number_of_vehicles = 0
        Processor.number_of_objects = 0
        Processor.number_of_standing_pedestrians = 0
        Processor.number_of_parked_cars = 0
        Processor.number_of_parked_rickshaws = 0
        Processor.number_of_parked_cngs = 0
        # Strip caches Parameters.random at class-initialisation time; drop it
        # so the strips of this run use this run's generator.
        Strip._rand = None

        self._read_geometry()   # must precede _read_network: strips depend on it
        self._read_network()
        self._calibrate_roadside_object_density()
        self._read_path()
        self._read_demand()
        self._add_path_to_demand()
        self._validate_oneway()

        Statistics.reset(len(self.demand_list))
        os.makedirs(Parameters.STATS_DIR, exist_ok=True)

        for demand1 in self.demand_list:
            self.next_generation_time.append(1)

            demand = demand1.get_demand()  # returns number of vehicle
            demand_ratio = jdiv(3600, demand)

            if demand_ratio > 1:
                self.number_of_vehicles_to_generate.append(1)
            else:
                self.number_of_vehicles_to_generate.append(jint(jround(jdiv(1, demand_ratio))))

    def get_mid_point(self):
        return self.mid_point

    def get_pedestrians(self):
        return self.pedestrians

    def get_vehicle_list(self):
        return self.vehicle_list

    def get_object_list(self):
        return self.object_list

    def get_node_list(self):
        return self.node_list

    def get_link_list(self):
        return self.link_list

    def auto_process(self) -> None:
        while True:
            if Parameters.simulation_step < Parameters.simulation_end_time:
                self._run_at_each_time_step()
            elif Parameters.simulation_step == Parameters.simulation_end_time:
                if Constants.PRINT_RESULT:
                    self._generate_statistics()
                break
            Parameters.simulation_step += 1

    def manual_process(self, frame) -> None:
        if Parameters.simulation_step < Parameters.simulation_end_time:
            if not Parameters.TRACE_MODE:
                self._run_at_each_time_step()
            frame.repaint()
        elif Parameters.simulation_step == Parameters.simulation_end_time:
            if Constants.PRINT_RESULT:
                self._generate_statistics()
            # Java also repaints here (which appends a final block to
            # trace.txt) and then keeps the Swing timer running forever; the
            # Python build stops animating instead.
            frame.repaint()
            frame.on_simulation_finished()
        Parameters.simulation_step += 1

    def _run_at_each_time_step(self) -> None:
        if Parameters.simulation_step % Parameters.SIGNAL_CHANGE_DURATION == 0:
            self._control_signal()

        if self.start_along_ped:
            if Parameters.simulation_step % 20 == 0:
                if Parameters.simulation_step != 0:
                    Parameters.along_pedestrian_mode = not Parameters.along_pedestrian_mode

        if Parameters.across_pedestrian_mode:
            self._remove_old_pedestrians()

            self._generate_new_pedestrians()

            self._move_pedestrians()

        self._remove_old_vehicles()

        self._generate_new_vehicles()

        if Parameters.along_pedestrian_mode:
            self._generate_new_along_pedestrians()

        # self._generate_specific_vehicles()

        self._move_vehicles()

        if Parameters.OBJECT_MODE:
            self._generate_new_objects()
            self._remove_old_objects()

        self._capture_frame()

    def _capture_frame(self) -> None:
        # Capture a handful of frames across the run for the report animation.
        if self._recorder is None:
            if Parameters.REPORT_ANIMATION_FRAMES <= 0:
                self._recorder = False  # animation disabled via parameter
            else:
                try:
                    from .visualize import RunRecorder
                    self._recorder = RunRecorder(
                        self, max_frames=Parameters.REPORT_ANIMATION_FRAMES)
                except Exception as exc:
                    # Report it rather than silently dropping the animation.
                    print(f"run animation disabled: {exc!r}")
                    self._recorder = False
        if self._recorder:
            self._recorder.maybe_capture(Parameters.simulation_step)

    def _generate_statistics(self) -> None:
        n_links = len(self.link_list)
        sensor_vehicle_count = [0.0] * n_links
        sensor_vehicle_avg_speed = [0.0] * n_links
        accident_count = [0.0] * n_links
        near_crash_count = [0.0] * n_links
        avg_speed_in_link = [0.0] * n_links
        avg_waiting_time_in_link = [0.0] * n_links  # in percentage

        index2 = 0
        for index in range(n_links):
            waiting_in_segment = 0
            leaving = 0
            link = self.link_list[index]
            for index2 in range(link.get_number_of_segments()):
                segment = link.get_segment(index2)
                sensor_vehicle_count[index] += segment.get_sensor_vehicle_count()
                sensor_vehicle_avg_speed[index] += (segment.get_sensor_vehicle_avg_speed()
                                                    * 3600.0 / 1000)
                accident_count[index] += segment.get_accident_count()
                near_crash_count[index] += segment.get_near_crash_count()
                avg_speed_in_link[index] += segment.get_avg_speed_in_segment()
                waiting_in_segment += segment.get_total_waiting_time()
                leaving += segment.get_leaving_vehicle_count()
            index2 = link.get_number_of_segments()
            # this means the flow rate on a link (vehicle/hour)
            sensor_vehicle_count[index] = jint(jround(
                jdiv(sensor_vehicle_count[index], index2) * 3600
                / Parameters.simulation_end_time))

            sensor_vehicle_avg_speed[index] = jdiv(sensor_vehicle_avg_speed[index], index2)
            avg_speed_in_link[index] = jdiv(avg_speed_in_link[index], index2)
            avg_waiting_time_in_link[index] = (jdiv(1.0 * waiting_in_segment, leaving)
                                               if leaving > 0 else 0)

        # The three link-level measures, one row per run and one column per
        # link.  They have always been computed here and then dropped on the
        # floor -- nothing downstream read them -- which left the per-vehicle
        # CSVs as the only output and made a link-by-link comparison
        # impossible.  Speed is accumulated in m/s (segment length over time to
        # cross it); the other two are already in the units they are reported
        # in, seconds per vehicle and vehicles per hour.
        self._print_data([s * 3.6 for s in avg_speed_in_link],
                         "statistics/link_avg_speed.csv")
        self._print_data(avg_waiting_time_in_link, "statistics/link_avg_waiting.csv")
        self._print_data(sensor_vehicle_count, "statistics/link_flow.csv")

        percentage_of_waiting = [0.0] * Constants.TYPES_OF_CARS
        for vehicle in self.vehicle_list:
            if vehicle.is_in_intersection():
                vehicle.increase_traveled_distance(vehicle.get_distance_in_intersection())
            else:
                vehicle.increase_traveled_distance(vehicle.get_distance_in_segment())

            Statistics.total_travel_time[vehicle.get_type()] += vehicle.get_travel_time()
            Statistics.waiting_time[vehicle.get_type()] += vehicle.get_waiting_time()
            vehicle.calculate_statistics_at_end()

        # overall statistics
        overall_avg_speed = 0.0
        overall_waiting_time = 0.0
        total_vehicle_count = 0

        # motorized statistics
        motorized_avg_speed = 0.0
        motorized_waiting_time = 0.0
        motorized_vehicle_count = 0

        # non-motorized statistics
        non_motorized_avg_speed = 0.0
        non_motorized_waiting_time = 0.0
        non_motorized_vehicle_count = 0

        for i in range(Constants.TYPES_OF_CARS):
            if Statistics.no_of_vehicles[i] != 0:
                if i != 12:
                    overall_avg_speed += Statistics.avg_speed_of_vehicle[i]
                    overall_waiting_time += Statistics.waiting_time[i]
                    total_vehicle_count += Statistics.no_of_vehicles[i]

                    if i < 3:
                        non_motorized_avg_speed += Statistics.avg_speed_of_vehicle[i]
                        non_motorized_waiting_time += Statistics.waiting_time[i]
                        non_motorized_vehicle_count += Statistics.no_of_vehicles[i]
                    else:
                        motorized_avg_speed += Statistics.avg_speed_of_vehicle[i]
                        motorized_waiting_time += Statistics.waiting_time[i]
                        motorized_vehicle_count += Statistics.no_of_vehicles[i]

                Statistics.avg_speed_of_vehicle[i] = jdiv(
                    Statistics.avg_speed_of_vehicle[i], Statistics.no_of_vehicles[i])
                Statistics.avg_speed_of_vehicle[i] *= 3.6
                percentage_of_waiting[i] = jdiv(100.0 * Statistics.waiting_time[i],
                                                Statistics.total_travel_time[i])

        overall_avg_speed = jdiv(overall_avg_speed, total_vehicle_count)
        overall_waiting_time = jdiv(overall_waiting_time, total_vehicle_count)

        motorized_avg_speed = jdiv(motorized_avg_speed, motorized_vehicle_count)
        motorized_waiting_time = jdiv(motorized_waiting_time, motorized_vehicle_count)

        non_motorized_avg_speed = jdiv(non_motorized_avg_speed,
                                       non_motorized_vehicle_count)
        non_motorized_waiting_time = jdiv(non_motorized_waiting_time,
                                          non_motorized_vehicle_count)

        shankar_palashi_car = 0.0
        palashi_shankar_car = 0.0

        # cnt is an int in Java, so accumulating the double
        # noOfVehiclesCompletingTrip entries truncates each time
        cnt = 0
        for i in range(4, 7):
            shankar_palashi_car += Statistics.trip_time[1][i]
            cnt = jint(cnt + Statistics.no_of_vehicles_completing_trip[1][i])
        shankar_palashi_car = jdiv(shankar_palashi_car, cnt)
        # only used by print statements that are commented out in the Java
        shankar_palashi_bike = jdiv(  # noqa: F841
            Statistics.trip_time[1][3],
            Statistics.no_of_vehicles_completing_trip[1][3])

        cnt = 0
        for i in range(4, 7):
            palashi_shankar_car += Statistics.trip_time[0][i]
            cnt = jint(cnt + Statistics.no_of_vehicles_completing_trip[0][i])
        palashi_shankar_car = jdiv(palashi_shankar_car, cnt)
        palashi_shankar_bike = jdiv(  # noqa: F841
            Statistics.trip_time[0][3],
            Statistics.no_of_vehicles_completing_trip[0][3])

        print("speed: " + jstr(overall_avg_speed * 3.6) + " km/h")
        print("waiting time: " + jstr(overall_waiting_time) + " s (avg per vehicle)")
        print("motorized speed: " + jstr(motorized_avg_speed * 3.6) + " km/h")
        print("motorized waiting time: " + jstr(motorized_waiting_time) + " s")
        print("non-motorized speed: " + jstr(non_motorized_avg_speed * 3.6) + " km/h")
        print("non-motorized waiting time: " + jstr(non_motorized_waiting_time) + " s")

        self._print_data(Statistics.avg_speed_of_vehicle,
                         "statistics/avg_speed_vehicle.csv")
        self._print_data(percentage_of_waiting,
                         "statistics/waiting_percentage_vehicle.csv")
        self._print_data(Statistics.no_of_generated_vehicles,
                         "statistics/generated_vehicles.csv")

        types = Constants.TYPES_OF_CARS
        aggregated_total_trip_time = [0.0] * types
        aggregated_avg_trip_time = [0.0] * types
        aggregated_total_trip_complete = [0.0] * types
        aggregated_total_fuel_consumption = [0.0] * types
        aggregated_avg_fuel_consumption = [0.0] * types
        aggregated_avg_collision = [0.0] * types
        aggregated_avg_accident = [0.0] * types
        total_number_of_collision_and_accident = [
            Statistics.no_of_collisions, Statistics.no_of_accidents,
            self.vehicle_id, self.pedestrian_id]

        for j in range(types):
            for i in range(len(self.demand_list)):
                aggregated_total_trip_complete[j] += \
                    Statistics.no_of_vehicles_completing_trip[i][j]
                aggregated_avg_collision[j] += Statistics.no_collisions_per_demand[i][j]
                aggregated_avg_accident[j] += Statistics.no_accidents_per_demand[i][j]
                aggregated_total_fuel_consumption[j] += \
                    Statistics.total_fuel_consumption[i][j]
                aggregated_total_trip_time[j] += 1.0 * Statistics.trip_time[i][j]

        for i in range(types):
            aggregated_avg_trip_time[i] = jdiv(
                jdiv(aggregated_total_trip_time[i], aggregated_total_trip_complete[i]),
                60.0 / Constants.TIME_STEP)
            aggregated_avg_fuel_consumption[i] = jdiv(
                aggregated_total_fuel_consumption[i], aggregated_total_trip_complete[i])

        self._print_data(aggregated_total_trip_complete,
                         "statistics/agg_total_trip_complete.csv")
        self._print_data(aggregated_avg_trip_time, "statistics/agg_avg_tt.csv")
        self._print_data(aggregated_avg_fuel_consumption, "statistics/agg_avg_fuel.csv")
        self._print_data(aggregated_avg_collision, "statistics/agg_avg_collision.csv")
        self._print_data(aggregated_avg_accident, "statistics/agg_avg_accident.csv")
        self._print_data(total_number_of_collision_and_accident,
                         "statistics/agg_total_collision.csv")

        n_demands = len(self.demand_list)
        avg_fuel_consumption = [[0.0] * types for _ in range(n_demands)]
        avg_trip_time = [[0.0] * types for _ in range(n_demands)]

        no_of_trip_times = min(Parameters.NO_OF_ROUTES_FOR_STAT, n_demands)
        # Mean trip time per route for the two vehicle classes a travel-time
        # validation is usually quoted in.  A car is three type indices, so the
        # mean has to be pooled over their trip times and completion counts
        # together: averaging the three avg_tt columns instead would give a
        # type that completed two trips the same weight as one that completed
        # two hundred.
        route_avg_tt_car = [0.0] * no_of_trip_times
        route_avg_tt_bike = [0.0] * no_of_trip_times
        for i in range(no_of_trip_times):
            for j in range(types):
                avg_fuel_consumption[i][j] = jdiv(
                    Statistics.total_fuel_consumption[i][j],
                    Statistics.no_of_vehicles_completing_trip[i][j])
                avg_trip_time[i][j] = jdiv(
                    jdiv(1.0 * Statistics.trip_time[i][j],
                         Statistics.no_of_vehicles_completing_trip[i][j]),
                    60.0 / Constants.TIME_STEP)
            car_time = 0.0
            car_trips = 0.0
            for j in range(4, 7):
                car_time += 1.0 * Statistics.trip_time[i][j]
                car_trips += Statistics.no_of_vehicles_completing_trip[i][j]
            route_avg_tt_car[i] = jdiv(jdiv(car_time, car_trips),
                                       60.0 / Constants.TIME_STEP)
            route_avg_tt_bike[i] = jdiv(
                jdiv(1.0 * Statistics.trip_time[i][3],
                     Statistics.no_of_vehicles_completing_trip[i][3]),
                60.0 / Constants.TIME_STEP)
            self._print_data(avg_fuel_consumption[i], f"statistics/fuel{i}.csv")
            self._print_data(avg_trip_time[i], f"statistics/avg_tt{i}.csv")
            self._print_data(Statistics.no_of_vehicles_completing_trip[i],
                             f"statistics/trip_complete{i}.csv")
            self._print_data(Statistics.no_collisions_per_demand[i],
                             f"statistics/collisions{i}.csv")

        self._print_data(route_avg_tt_car, "statistics/route_avg_tt_car.csv")
        self._print_data(route_avg_tt_bike, "statistics/route_avg_tt_motorbike.csv")

        # flow rate statistics
        self._print_data(Statistics.flow, "statistics/flow.csv")

        # human-readable HTML report (see dhakasim/report.py)
        try:
            from .report import write_html_report
            visual = None
            if self._recorder:
                try:
                    visual = self._recorder.finish()
                except Exception:
                    visual = None
            write_html_report(
                visual=visual,
                params={
                    "seed": Parameters.seed,
                    "end_time": Parameters.simulation_end_time,
                    "cf_model": getattr(Parameters.car_following_model, "name",
                                        str(Parameters.car_following_model)),
                    "dlc_model": getattr(Parameters.lane_changing_model, "name",
                                         str(Parameters.lane_changing_model)),
                    "strip_width": Parameters.strip_width,
                    "footpath_strip_width": Parameters.footpath_strip_width,
                    "maximum_speed": Parameters.maximum_speed,
                    "signal_change": Parameters.SIGNAL_CHANGE_DURATION,
                    "across_ped": Parameters.across_pedestrian_mode,
                    "along_ped": Parameters.along_pedestrian_mode,
                    "object_mode": Parameters.OBJECT_MODE,
                    "num_links": len(self.link_list),
                    "num_nodes": len(self.node_list),
                    "num_od": len(self.demand_list),
                },
                per_type={
                    "avg_speed": Statistics.avg_speed_of_vehicle,
                    "counts": Statistics.no_of_vehicles,
                    "waiting_pct": percentage_of_waiting,
                    "generated": Statistics.no_of_generated_vehicles,
                    "trips": aggregated_total_trip_complete,
                    "avg_tt": aggregated_avg_trip_time,
                    "avg_fuel": aggregated_avg_fuel_consumption,
                    "collision": aggregated_avg_collision,
                    "accident": aggregated_avg_accident,
                },
                totals=total_number_of_collision_and_accident,
            )
        except Exception as exc:  # never let reporting break a run
            print(f"report generation skipped: {exc}")

    @staticmethod
    def _print_data(data, filename: str) -> None:
        # Keep raw CSVs out of the run's root, which holds only the
        # human-readable HTML reports; write them under <StatsDir>/csv/.
        if filename.startswith("statistics/") and filename.endswith(".csv"):
            filename = os.path.join(Parameters.STATS_DIR, "csv",
                                    filename[len("statistics/"):])
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        with open(filename, "a") as writer:
            for d in data:
                writer.write(jformat(d, 3) + ",")
            writer.write("\n")

    def _remove_old_pedestrians(self) -> None:
        objects_to_remove = set()
        for pedestrian in self.pedestrians:
            if pedestrian.is_to_remove():
                objects_to_remove.add(id(pedestrian))
        if objects_to_remove:
            self.pedestrians = [p for p in self.pedestrians
                                if id(p) not in objects_to_remove]

    def _generate_new_pedestrians(self) -> None:
        random = self.random
        for link in self.link_list:
            # across_ped_percentage == number of ped per one hour
            pedestrian_count = Constants.road_crossing_ped_poisson.sample()
            for _ in range(pedestrian_count):
                random_segment_id = jabs_int(random.next_int()) % link.get_number_of_segments()
                random_segment = link.get_segment(random_segment_id)

                minimum = 9
                maximum = jint(random_segment.get_length() - 9)
                distance1 = (maximum - minimum) / 5 + minimum
                distance2 = maximum - (maximum - minimum) / 5
                if (maximum - minimum) + 1 <= 0:
                    continue

                # pedestrian generation model. Most road-crossing pedestrians
                # are generated at the two ends of the road.
                probabilities = (0.4, 0.2, 0.4)
                rand = random.next_double()
                if rand <= probabilities[0]:
                    random_pos = random.next_double_range(minimum, distance1)
                elif rand <= probabilities[0] + probabilities[1]:
                    random_pos = random.next_double_range(distance1, distance2)
                else:
                    random_pos = random.next_double_range(distance2, maximum)

                random_obj_speed = (jabs_int(random.next_int()) % 2) / 10.0 + 0.05
                bo = random.next_boolean()
                if not bo:
                    strip = random_segment.number_of_strips() - 1
                else:
                    strip = 0
                pedestrian = Pedestrian(self.pedestrian_id, random_segment, strip,
                                        random_pos, random_obj_speed)
                self.pedestrian_id += 1
                self.pedestrians.append(pedestrian)

    def _move_pedestrians(self) -> None:
        for pedestrian in self.pedestrians:
            if pedestrian.has_crossed_road():
                pedestrian.set_to_remove(True)
            else:
                if not pedestrian.move_forward():
                    if not pedestrian.move_length_wise():
                        if pedestrian.is_stuck():
                            pedestrian.clean_up()
                pedestrian.print_pedestrian_details()

    def _generate_an_object(self, link, object_type: int) -> None:
        # lengths and widths of the objects
        object_lengths = (Constants.STANDING_PEDESTRIAN_LENGTH,
                          Constants.PARKED_CAR_LENGTH,
                          Constants.PARKED_RICKSHAW_LENGTH,
                          Constants.PARKED_CNG_LENGTH)
        object_widths = (Constants.STANDING_PEDESTRIAN_WIDTH,
                         Constants.PARKED_CAR_WIDTH,
                         Constants.PARKED_RICKSHAW_WIDTH,
                         Constants.PARKED_CNG_WIDTH)

        random_segment_id = jabs_int(self.random.next_int()) % link.get_number_of_segments()
        random_segment = link.get_segment(random_segment_id)
        minimum = 9
        maximum = jint(random_segment.get_length() - 9)
        if (maximum - minimum) + 1 <= 0:
            return
        random_init_pos = self.random.next_int_bound((maximum - minimum) + 1) + minimum
        reverse_direction = self.random.next_boolean()

        # sampling blockage from Gaussian distribution
        distance_from_footpath = (
            Utilities.get_random_from_multiple_gaussian_distribution_of_objects_blockage(
                object_type) - object_widths[object_type - 1])
        width = distance_from_footpath + object_widths[object_type - 1]

        number_of_occupied_strips = jint(math.ceil(width / Parameters.strip_width))
        if not reverse_direction:
            # Started from 0, as we want to occupy the footpath strip as well.
            for i in range(number_of_occupied_strips + 1):
                if not random_segment.get_strip(i).has_gap_for_adding_object(
                        object_lengths[object_type - 1], random_init_pos):
                    return  # There's no gap for adding object
        else:
            for i in range(number_of_occupied_strips + 1):
                if not random_segment.get_strip(
                        random_segment.number_of_strips() - 1 - i
                ).has_gap_for_adding_object(object_lengths[object_type - 1],
                                            random_init_pos):
                    return  # There's no gap for adding object

        obj = Object(self.object_id, object_type, Parameters.simulation_step, link,
                     random_segment, random_segment_id, random_init_pos,
                     reverse_direction, distance_from_footpath)
        self.object_id += 1
        self.object_list.append(obj)
        Processor.number_of_objects += 1
        if object_type == 1:
            Processor.number_of_standing_pedestrians += 1
        elif object_type == 2:
            Processor.number_of_parked_cars += 1
        elif object_type == 3:
            Processor.number_of_parked_rickshaws += 1
        else:
            Processor.number_of_parked_cngs += 1

    def _generate_new_objects(self) -> None:
        for link in self.link_list:
            random = scratch_random()
            factor = 0.1

            # We generate objects using a cumulative Gaussian distribution.  As
            # the number of objects increases, the chance of generating new ones
            # decreases.

            # generating standing pedestrians
            random_gaussian = 1 - Utilities.cumulative_distribution_function(
                Processor.number_of_standing_pedestrians,
                Constants.AVG_NUMBER_OF_STANDING_PEDESTRIANS, 10)
            if random.next_double() < factor * 3 * random_gaussian:
                self._generate_an_object(link, 1)

            # generating parked cars
            random_gaussian = 1 - Utilities.cumulative_distribution_function(
                Processor.number_of_parked_cars,
                Constants.AVG_NUMBER_OF_PARKED_CARS, 10)
            if random.next_double() < factor * random_gaussian:
                self._generate_an_object(link, 2)

            # generating parked rickshaws
            random_gaussian = 1 - Utilities.cumulative_distribution_function(
                Processor.number_of_parked_rickshaws,
                Constants.AVG_NUMBER_OF_PARKED_RICKSHAWS, 10)
            if random.next_double() < factor * 4 * random_gaussian:
                self._generate_an_object(link, 3)

            # generating parked CNGs
            random_gaussian = 1 - Utilities.cumulative_distribution_function(
                Processor.number_of_parked_cngs,
                Constants.AVG_NUMBER_OF_PARKED_CNGS, 10)
            if random.next_double() < factor * 7 * random_gaussian:
                self._generate_an_object(link, 4)

    def _remove_old_objects(self) -> None:
        objects_to_remove = []
        for obj in self.object_list:
            if (Parameters.simulation_step - obj.get_parking_start_time()
                    < obj.get_parking_time()):
                continue  # parking time is not over yet

            # parking time over, the object will move on

            # for standing pedestrians
            if obj.object_type == 1:
                Processor.number_of_standing_pedestrians -= 1
                width = (obj.get_distance_from_footpath()
                         + Constants.STANDING_PEDESTRIAN_WIDTH)
                self._remove_rectangular_object(obj, width)
                objects_to_remove.append(obj)

            # for parked cars
            if obj.object_type == 2:
                Processor.number_of_parked_cars -= 1
                width = obj.get_distance_from_footpath() + Constants.PARKED_CAR_WIDTH
                self._remove_rectangular_object(obj, width)
                objects_to_remove.append(obj)

            # for parked rickshaws
            if obj.object_type == 3:
                Processor.number_of_parked_rickshaws -= 1
                width = obj.get_distance_from_footpath() + Constants.PARKED_RICKSHAW_WIDTH
                self._remove_rectangular_object(obj, width)
                objects_to_remove.append(obj)

            # for parked CNGs
            if obj.object_type == 4:
                Processor.number_of_parked_cngs -= 1
                width = obj.get_distance_from_footpath() + Constants.PARKED_CNG_WIDTH
                self._remove_rectangular_object(obj, width)
                objects_to_remove.append(obj)

        Processor.number_of_objects -= len(objects_to_remove)
        if objects_to_remove:
            removed = {id(o) for o in objects_to_remove}
            self.object_list = [o for o in self.object_list if id(o) not in removed]

    @staticmethod
    def _remove_rectangular_object(obj, width: float) -> None:
        if math.fmod(width, Parameters.strip_width) == 0:
            number_of_occupied_strips = jint(width / Parameters.strip_width)
        else:
            number_of_occupied_strips = jint(width / Parameters.strip_width) + 1
        if not obj.is_reverse_segment():
            # Started from 0, as the footpath strip should be freed as well.
            for i in range(number_of_occupied_strips + 1):
                obj.get_segment().get_strip(i).del_object(obj)
        else:
            for i in range(number_of_occupied_strips + 1):
                obj.get_segment().get_strip(
                    obj.get_segment().number_of_strips() - 1 - i).del_object(obj)

    def _random_vehicle_path(self, number_of_paths: int) -> int:
        return jabs_int(self.random.next_int()) % number_of_paths

    def _random_vehicle_speed(self) -> float:
        return self.random.next_int_bound(10) + 1  # speed in 1 to 10

    def _constant_vehicle_speed(self) -> float:
        return 0

    def _constant_vehicle_type(self) -> int:
        return 4

    def _random_vehicle_type(self) -> int:
        # first 3 are human powered
        ratio = self.random.next_int_bound(101)
        if ratio < Parameters.slow_vehicle_percentage:
            type_ = self.random.next_int_bound(3)  # first 3 are slow human powered
        elif ratio < Parameters.slow_vehicle_percentage + Parameters.medium_vehicle_percentage:
            type_ = self.random.next_int_bound(5)  # types of medium speed vehicle = 5
            type_ += 7  # last
        else:
            type_ = self.random.next_int_bound(4)  # types of high speed vehicle = 4
            type_ += 3  # offset

        return type_

    def _distributed_vehicle_type(self) -> int:
        ratio = self.random.next_int_bound(101)
        if ratio < Parameters.slow_vehicle_percentage:
            ratio_n = self.random.next_int_bound(101)
            if ratio_n < 9:
                return 0  # bicycle
            elif ratio_n < 98:
                return 1  # rickshaw
            else:
                return 2  # van/cart
        elif ratio < Parameters.slow_vehicle_percentage + Parameters.medium_vehicle_percentage:
            ratio_n = self.random.next_int_bound(101)
            if ratio_n < 83:
                return 7  # cng
            elif ratio_n < 98:
                return 8 + self.random.next_int_bound(2)  # bus
            else:
                return 10 + self.random.next_int_bound(2)  # truck
        else:
            ratio_n = self.random.next_int_bound(101)
            if ratio_n < 88:
                return 3  # bike
            else:
                return 4 + self.random.next_int_bound(3)  # car

    def _distributed_vehicle_type_miami(self) -> int:
        ratio = self.random.next_int_bound(101)
        if ratio < Parameters.slow_vehicle_percentage:
            return 0  # always bicycle
        elif ratio < Parameters.slow_vehicle_percentage + Parameters.medium_vehicle_percentage:
            return 8 + self.random.next_int_bound(2)  # always bus
        else:
            ratio_n = self.random.next_int_bound(101)
            if ratio_n < 12:
                return 3  # bike
            else:
                return 4 + self.random.next_int_bound(3)  # car

    def _distributed_vehicle_type_bd_new(self) -> int:
        ratio = self.random.next_int_bound(101)
        if ratio < 26:
            return 1  # rickshaw
        elif ratio < 26 + 23:
            return 7  # CNG
        elif ratio < 26 + 23 + 30:
            return 3
        elif ratio < 26 + 23 + 30 + 18:
            return 4 + self.random.next_int_bound(3)
        else:
            return 8 + self.random.next_int_bound(2)  # bus

    def _distributed_vehicle_type_survey(self) -> int:
        """Sample a vehicle type from the network's measured mix.

        Reads ``vehicle_mix.txt`` of the selected network (see
        ``Parameters.VEHICLE_MIX``). Types that stand for a family of indices
        -- car 4-6, bus 8-9, truck 10-11 -- are spread across that family.
        """
        r = self.random.next_int_bound(10000)
        for threshold, type_ in Parameters.VEHICLE_MIX:
            if r < threshold:
                if type_ == 4:
                    return 4 + self.random.next_int_bound(3)
                if type_ == 8:
                    return 8 + self.random.next_int_bound(2)
                if type_ == 10:
                    return 10 + self.random.next_int_bound(2)
                return type_
        return 4 + self.random.next_int_bound(3)

    def _distributed_vehicle_type_kakrail(self) -> int:
        # Survey-matched mix for the Kakrail Church + Kakrail Mosque corridor,
        # peak hour 13:00-14:00 on 29-05-2025.  The 18 classified survey classes
        # are mapped onto DhakaSim's 13 vehicle types; thresholds are per-10000
        # shares of the observed peak-hour flow (total 11,860 veh).
        #   bicycle 0.94 | rickshaw(+easybike) 5.35 | van/cart 0.61 |
        #   motorbike 18.81 | car(private/jeep/microbus/emergency) 46.53 |
        #   CNG(+autorickshaw/tempo) 22.83 | bus(std+mini) 0.83 | truck 4.09
        r = self.random.next_int_bound(10000)
        if r < 94:
            return 0  # bicycle
        elif r < 629:
            return 1  # rickshaw
        elif r < 690:
            return 2  # van / cart
        elif r < 2571:
            return 3  # motorbike
        elif r < 7224:
            return 4 + self.random.next_int_bound(3)  # car
        elif r < 9507:
            return 7  # CNG
        elif r < 9590:
            return 8 + self.random.next_int_bound(2)  # bus
        else:
            return 10 + self.random.next_int_bound(2)  # truck

    def _pedestrian_vehicle_distribution_type(self) -> int:
        if Parameters.VEHICLE_MIX:
            return self._distributed_vehicle_type_survey()
        return self._distributed_vehicle_type()

    def _new_pedestrian_vehicle_distribution_type(self) -> int:
        if Parameters.along_pedestrian_mode:
            no_of_division = 4
            duration_of_division = 25
            total_duration = no_of_division * duration_of_division
            x = Parameters.simulation_step % total_duration
            y = x // duration_of_division
            ratio = self.random.next_int_bound(total_duration + 1)
            if ratio < (y + 1) * duration_of_division:
                return 12
            return self._constant_vehicle_type()
        return self._constant_vehicle_type()

    def _create_an_along_pedestrian(self, demand_index: int) -> None:
        number_of_paths = self.demand_list[demand_index].get_number_of_paths()
        for _ in range(self.number_of_vehicles_to_generate[demand_index]):
            path_index = self._random_vehicle_path(number_of_paths)
            type_ = Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE
            path = self.demand_list[demand_index].get_path(path_index)
            source_node = self.node_list[path.get_source()]
            link = self.link_list[path.get_link(0)]

            link_segment_orientation = get_link_and_segment_orientation(
                source_node.x, source_node.y, link.get_first_segment(),
                link.get_last_segment())
            segment = (link.get_last_segment() if link_segment_orientation.reverse_link
                       else link.get_first_segment())

            # for pedestrians, we sample from the distribution for the blockage
            distance_from_footpath = (
                Utilities.get_random_from_multiple_gaussian_distribution_of_objects_blockage(5))
            strip_index = Utilities.pedestrian_blockage_strip(distance_from_footpath)

            if link_segment_orientation.reverse_segment:
                strip_index = segment.last_vehicle_strip_index - strip_index + 1

            if self._has_gap(type_, strip_index, segment):
                self._create_vehicle(type_, link_segment_orientation, link, segment,
                                     demand_index, path_index, strip_index)
            return

    def _create_a_vehicle(self, demand_index: int) -> None:
        number_of_paths = self.demand_list[demand_index].get_number_of_paths()
        for _ in range(self.number_of_vehicles_to_generate[demand_index]):
            path_index = self._random_vehicle_path(number_of_paths)
            type_ = self._pedestrian_vehicle_distribution_type()
            number_of_strips = Utilities.number_of_strips(type_)
            path = self.demand_list[demand_index].get_path(path_index)
            source_node = self.node_list[path.get_source()]
            link = self.link_list[path.get_link(0)]

            link_segment_orientation = get_link_and_segment_orientation(
                source_node.x, source_node.y, link.get_first_segment(),
                link.get_last_segment())
            segment = (link.get_last_segment() if link_segment_orientation.reverse_link
                       else link.get_first_segment())

            # for pedestrians, we sample from the distribution for the blockage
            if type_ == Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE:
                distance_from_footpath = (
                    Utilities.get_random_from_multiple_gaussian_distribution_of_objects_blockage(5))
                strip_index = Utilities.pedestrian_blockage_strip(distance_from_footpath)

                if link_segment_orientation.reverse_segment:
                    strip_index = segment.last_vehicle_strip_index - strip_index + 1

                if self._has_gap(type_, strip_index, segment):
                    self._create_vehicle(type_, link_segment_orientation, link, segment,
                                         demand_index, path_index, strip_index)
                return

            # other vehicles are created normally
            if link_segment_orientation.reverse_segment:
                start = segment.middle_high_strip_index
                end = segment.last_vehicle_strip_index
            else:
                start = 1
                end = segment.middle_low_strip_index

            random_start = Utilities.rand_int(start, end)

            k = random_start
            while k + number_of_strips - 1 <= end:
                if self._has_gap(type_, k, segment):
                    self._create_vehicle(type_, link_segment_orientation, link, segment,
                                         demand_index, path_index, k)
                    return
                k += 1

            k = end - number_of_strips + 1
            while k >= random_start:
                if self._has_gap(type_, k, segment):
                    self._create_vehicle(type_, link_segment_orientation, link, segment,
                                         demand_index, path_index, k)
                    return
                k -= 1

    def _has_gap(self, type_: int, start_index: int, segment) -> bool:
        number_of_strips = Utilities.number_of_strips(type_)
        length = Utilities.get_car_length(type_)
        has_gap = True
        for l in range(start_index, start_index + number_of_strips):
            if l < 0 or l >= segment.number_of_strips():
                has_gap = False
                continue
            if not segment.get_strip(l).has_gap_for_adding_vehicle(length):
                has_gap = False
        return has_gap

    def _create_vehicle(self, type_: int, link_segment_orientation, link, segment,
                        demand_index: int, path_index: int, strip_index: int) -> None:
        # Fixed colour per vehicle type so types are distinguishable in the
        # animation and match the report legend (Constants.VEHICLE_TYPE_COLORS).
        # The RNG draw is kept so vehicle-generation stays seed-identical.
        _ = (self.random.next_float(), self.random.next_float(),
             self.random.next_float())
        if 0 <= type_ < len(Constants.VEHICLE_TYPE_COLORS):
            color = Color(*Constants.VEHICLE_TYPE_COLORS[type_])
        else:
            color = Color.BLACK
        if type_ == Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE:
            color = Color.BLACK
        if link_segment_orientation.reverse_segment:
            start_x = segment.get_end_x()
            start_y = segment.get_end_y()
            end_x = segment.get_start_x()
            end_y = segment.get_start_y()
        else:
            start_x = segment.get_start_x()
            start_y = segment.get_start_y()
            end_x = segment.get_end_x()
            end_y = segment.get_end_y()

        self.vehicle_list.append(Vehicle(
            self.vehicle_id, Parameters.simulation_step, type_, color, demand_index,
            path_index, 0, start_x, start_y, end_x, end_y,
            link_segment_orientation.reverse_link,
            link_segment_orientation.reverse_segment, link, segment.get_index(),
            strip_index))
        self.vehicle_id += 1
        Statistics.no_of_generated_vehicles[type_] += 1

    def _create_a_specific_vehicle(self, demand_index: int, type_: int, strip_index: int,
                                   color) -> None:
        number_of_paths = self.demand_list[demand_index].get_number_of_paths()
        for _ in range(self.number_of_vehicles_to_generate[demand_index]):
            path_index = self._random_vehicle_path(number_of_paths)
            length = Utilities.get_car_length(type_)
            number_of_strips = Utilities.number_of_strips(type_)
            path = self.demand_list[demand_index].get_path(path_index)
            source_node = self.node_list[path.get_source()]
            link = self.link_list[path.get_link(0)]

            link_segment_orientation = get_link_and_segment_orientation(
                source_node.x, source_node.y, link.get_first_segment(),
                link.get_last_segment())
            segment = (link.get_last_segment() if link_segment_orientation.reverse_link
                       else link.get_first_segment())

            has_gap = True
            for l in range(strip_index, strip_index + number_of_strips):
                if not segment.get_strip(l).has_gap_for_adding_vehicle(length):
                    has_gap = False
            if has_gap:
                if link_segment_orientation.reverse_segment:
                    start_x = segment.get_end_x()
                    start_y = segment.get_end_y()
                    end_x = segment.get_start_x()
                    end_y = segment.get_start_y()
                else:
                    start_x = segment.get_start_x()
                    start_y = segment.get_start_y()
                    end_x = segment.get_end_x()
                    end_y = segment.get_end_y()

                self.vehicle_list.append(Vehicle(
                    self.vehicle_id, Parameters.simulation_step, type_, color,
                    demand_index, path_index, 0, start_x, start_y, end_x, end_y,
                    link_segment_orientation.reverse_link,
                    link_segment_orientation.reverse_segment, link,
                    segment.get_index(), strip_index))
                self.vehicle_id += 1
                return

    def _get_next_inter_arrival_gap(self, mean_headway: float) -> float:
        """:param mean_headway: the constant time gap between two consecutive
        vehicles
        :return: the time gap to be used based on various distributions"""
        if Parameters.vehicle_generation_rate == VEHICLE_GENERATION_RATE.CONSTANT:
            return mean_headway
        elif Parameters.vehicle_generation_rate == VEHICLE_GENERATION_RATE.POISSON:
            return mean_headway * -jlog(self.random.next_double())
        return 0

    def _remove_old_vehicles(self) -> None:
        vehicles_to_remove = []
        for vehicle in self.vehicle_list:
            if vehicle.is_to_remove():
                vehicle.free_strips()
                vehicles_to_remove.append(vehicle)
                vehicle.calculate_statistics_at_end()
                if self.write_speed(vehicle):
                    self._print_data(vehicle.get_vehicle_stats().get_speeds(),
                                     f"statistics/speeds_{vehicle.get_vehicle_id()}.csv")
                    self._print_data(vehicle.get_vehicle_stats().get_x_trajectory(),
                                     f"statistics/x_coords_{vehicle.get_vehicle_id()}.csv")
                    self._print_data(vehicle.get_vehicle_stats().get_y_trajectory(),
                                     f"statistics/y_coords_{vehicle.get_vehicle_id()}.csv")
                Statistics.save_vehicle_stat(vehicle.get_vehicle_stats())
                vehicle.update_trip_time_statistics()
        if vehicles_to_remove:
            removed = {id(v) for v in vehicles_to_remove}
            self.vehicle_list = [v for v in self.vehicle_list if id(v) not in removed]

    def _generate_new_along_pedestrians(self) -> None:
        for i in range(len(self.demand_list)):
            pedestrian_count = Constants.road_along_ped_poisson.sample()
            for _ in range(pedestrian_count):
                self._create_an_along_pedestrian(i)

    def _generate_new_vehicles(self) -> None:
        for i in range(len(self.demand_list)):
            if jint(self.next_generation_time[i] / Constants.TIME_STEP) == \
                    Parameters.simulation_step:
                demand = self.demand_list[i].get_demand()
                mean_headway = jdiv(3600.0, demand)
                while True:
                    self._create_a_vehicle(i)

                    x = self._get_next_inter_arrival_gap(mean_headway)

                    next_time = jround_to_int(self.next_generation_time[i] + x)
                    self.next_generation_time[i] = next_time
                    if jint(next_time / Constants.TIME_STEP) != Parameters.simulation_step:
                        break

    def _generate_specific_vehicles(self) -> None:
        time_points = (1, 1, 1, 1, 15)
        types = (0, 0, 0, 0, 5)
        # strip_indices = (6, 1, 2, 12, 3)
        strip_indices = (2, 4, 6, 10, 3)
        colors = (Color.GREEN, Color.RED, Color.CYAN, Color.MAGENTA, Color.BLUE)

        for i in range(len(time_points)):
            if Parameters.simulation_step == time_points[i]:
                self._create_a_specific_vehicle(0, types[i], strip_indices[i], colors[i])

    def write_speed(self, vehicle) -> bool:
        # return vehicle.get_vehicle_id() % 40 == 0 and vehicle.get_type() != 12
        return False

    def _move_vehicle_at_intersection_end(self, vehicle) -> None:
        demand_index = vehicle.get_demand_index()
        path_index = vehicle.get_path_index()
        link_index_on_path = vehicle.get_link_index_on_path()

        new_link_index = self.demand_list[demand_index].get_path(path_index).get_link(
            link_index_on_path + 1)
        link = self.link_list[new_link_index]

        first_segment = link.get_first_segment()
        last_segment = link.get_last_segment()

        link_segment_orientation = get_link_and_segment_orientation(
            vehicle.get_seg_end_x(), vehicle.get_seg_end_y(), first_segment, last_segment)

        entering_segment = (last_segment if link_segment_orientation.reverse_link
                            else first_segment)

        strip_index = vehicle.get_current_intersection_strip().end_strip

        flag = True
        for j in range(vehicle.get_number_of_strips()):
            if not entering_segment.get_strip(strip_index + j).has_gap_for_adding_vehicle(
                    vehicle.get_length()):
                flag = False
                break
        if not flag:
            strip_index = entering_segment.get_strip_index_in_entering_segment(
                vehicle, strip_index)
            flag = strip_index != -1

        if flag:
            # check this
            vehicle.get_node().remove_vehicle(vehicle)
            # should not call manual_signaling() if we want to compare
            # performance between different CF models
            # vehicle.get_node().manual_signaling()
            vehicle.set_node(None)
            vehicle.increase_traveled_distance(vehicle.get_distance_in_intersection()
                                               + vehicle.get_length() + Vehicle.MARGIN)
            vehicle.set_distance_in_intersection(0)

            vehicle.free_strips()

            vehicle.set_in_intersection(False)
            vehicle.set_reverse_link(link_segment_orientation.reverse_link)
            vehicle.set_reverse_segment(link_segment_orientation.reverse_segment)
            vehicle.link_change(link_index_on_path + 1, link,
                                entering_segment.get_index(), strip_index)
            vehicle.update_segment_entering_data()
            if link_segment_orientation.reverse_segment:
                vehicle.set_seg_start_x(entering_segment.get_end_x())
                vehicle.set_seg_start_y(entering_segment.get_end_y())
                vehicle.set_seg_end_x(entering_segment.get_start_x())
                vehicle.set_seg_end_y(entering_segment.get_start_y())
            else:
                vehicle.set_seg_start_x(entering_segment.get_start_x())
                vehicle.set_seg_start_y(entering_segment.get_start_y())
                vehicle.set_seg_end_x(entering_segment.get_end_x())
                vehicle.set_seg_end_y(entering_segment.get_end_y())

    def _move_vehicle_at_segment_middle(self, vehicle) -> None:
        old_dist_in_segment = vehicle.get_distance_in_segment()
        previous = vehicle.is_passed_sensor()

        vehicle.move_vehicle_in_segment()

        now = vehicle.is_passed_sensor()
        new_dist_in_segment = vehicle.get_distance_in_segment()

        if not previous and now:
            vehicle.get_link().get_segment(
                vehicle.get_segment_index()).update_information(vehicle.get_speed())

        self._update_flow(old_dist_in_segment, new_dist_in_segment, vehicle)

    @staticmethod
    def accident_log_all_vehicle_at_last(vehicle) -> None:
        if vehicle.has_collided() or vehicle.has_caused_accident():
            vehicle.print_accident_log()

    @staticmethod
    def accident_check_all_vehicles_at_last(vehicle) -> None:
        if vehicle.has_caused_accident():
            vehicle.after_accident_to_do()

    def _create_intersection_strip(self, vehicle, old_link_index: int,
                                   new_link_index: int) -> IntersectionStrip:
        new_link = self.link_list[new_link_index]
        link_segment_orientation = get_link_and_segment_orientation(
            vehicle.get_seg_end_x(), vehicle.get_seg_end_y(), new_link.get_segment(0),
            new_link.get_segment(new_link.get_number_of_segments() - 1))

        # leaving segment
        leaving_segment = vehicle.get_link().get_segment(vehicle.get_segment_index())
        # entering segment
        entering_segment = (new_link.get_last_segment()
                            if link_segment_orientation.reverse_link
                            else new_link.get_first_segment())

        old_strip_index = vehicle.get_strip_index()
        new_strip_index = vehicle.get_new_strip_index(
            leaving_segment, entering_segment, link_segment_orientation.reverse_segment)

        pixel_per_meter = Parameters.pixel_per_meter
        # single direction so 1 footpath
        w = (1 * Parameters.pixel_per_footpath_strip
             + (vehicle.get_strip_index() - 1) * Parameters.pixel_per_strip)
        x1 = leaving_segment.get_start_x() * pixel_per_meter
        y1 = leaving_segment.get_start_y() * pixel_per_meter
        x2 = leaving_segment.get_end_x() * pixel_per_meter
        y2 = leaving_segment.get_end_y() * pixel_per_meter
        x3 = Utilities.return_x3(x1, y1, x2, y2, w)
        y3 = Utilities.return_y3(x1, y1, x2, y2, w)
        x4 = Utilities.return_x4(x1, y1, x2, y2, w)
        y4 = Utilities.return_y4(x1, y1, x2, y2, w)
        veh_x = vehicle.get_seg_end_x() * pixel_per_meter
        veh_y = vehicle.get_seg_end_y() * pixel_per_meter
        dist1 = (x3 - veh_x) * (x3 - veh_x) + (y3 - veh_y) * (y3 - veh_y)
        dist2 = (x4 - veh_x) * (x4 - veh_x) + (y4 - veh_y) * (y4 - veh_y)
        if dist1 < dist2:
            start_point_x = x3
            start_point_y = y3
        else:
            start_point_x = x4
            start_point_y = y4

        x_1 = entering_segment.get_start_x() * pixel_per_meter
        y_1 = entering_segment.get_start_y() * pixel_per_meter
        x_2 = entering_segment.get_end_x() * pixel_per_meter
        y_2 = entering_segment.get_end_y() * pixel_per_meter
        if vehicle.is_reverse_segment() == link_segment_orientation.reverse_segment:
            w1 = (1 * Parameters.pixel_per_footpath_strip
                  + (vehicle.get_new_strip_index(
                      leaving_segment, entering_segment,
                      link_segment_orientation.reverse_segment) - 1)
                  * Parameters.pixel_per_strip)  # single direction so 1 footpath
        else:
            w1 = (1 * Parameters.pixel_per_footpath_strip
                  + (vehicle.get_new_strip_index(
                      leaving_segment, entering_segment,
                      link_segment_orientation.reverse_segment))
                  * Parameters.pixel_per_strip)  # single direction so 1 footpath

        x_3 = Utilities.return_x3(x_1, y_1, x_2, y_2, w1)
        y_3 = Utilities.return_y3(x_1, y_1, x_2, y_2, w1)
        x_4 = Utilities.return_x4(x_1, y_1, x_2, y_2, w1)
        y_4 = Utilities.return_y4(x_1, y_1, x_2, y_2, w1)
        dist3 = (x_3 - veh_x) * (x_3 - veh_x) + (y_3 - veh_y) * (y_3 - veh_y)
        dist4 = (x_4 - veh_x) * (x_4 - veh_x) + (y_4 - veh_y) * (y_4 - veh_y)
        if dist3 < dist4:  # choosing closest segment end point from vehicle
            end_point_x = x_3
            end_point_y = y_3
        else:
            end_point_x = x_4
            end_point_y = y_4
        return IntersectionStrip(old_link_index, old_strip_index, new_link_index,
                                 new_strip_index, start_point_x, start_point_y,
                                 end_point_x, end_point_y, leaving_segment,
                                 entering_segment)

    def _move_vehicle_at_segment_end(self, vehicle) -> None:
        if ((vehicle.is_reverse_link()
             and vehicle.get_link().get_segment(
                 vehicle.get_segment_index()).is_first_segment())
                or (not vehicle.is_reverse_link()
                    and vehicle.get_link().get_segment(
                        vehicle.get_segment_index()).is_last_segment())):
            # at link end
            demand_index = vehicle.get_demand_index()
            path_index = vehicle.get_path_index()
            path_link_index = vehicle.get_link_index_on_path()
            path = self.demand_list[demand_index].get_path(path_index)
            last_link_in_path_index = path.get_number_of_links() - 1
            if path_link_index == last_link_in_path_index:
                # at path end
                vehicle.remove_from_simulation(True)
            else:
                # at path middle
                old_link_index = path.get_link(path_link_index)
                new_link_index = path.get_link(path_link_index + 1)

                node = (self.node_list[vehicle.get_link().get_up_node()]
                        if vehicle.is_reverse_link()
                        else self.node_list[vehicle.get_link().get_down_node()])

                new_link = self.link_list[new_link_index]
                link_segment_orientation = get_link_and_segment_orientation(
                    vehicle.get_seg_end_x(), vehicle.get_seg_end_y(),
                    new_link.get_segment(0),
                    new_link.get_segment(new_link.get_number_of_segments() - 1))

                # leaving segment
                leaving_segment = vehicle.get_link().get_segment(
                    vehicle.get_segment_index())
                # entering segment
                entering_segment = (
                    new_link.get_segment(new_link.get_number_of_segments() - 1)
                    if link_segment_orientation.reverse_link
                    else new_link.get_segment(0))

                old_strip_index = vehicle.get_strip_index()
                new_strip_index = vehicle.get_new_strip_index(
                    leaving_segment, entering_segment,
                    link_segment_orientation.reverse_segment)

                if entering_segment.get_strip_index_in_entering_segment(
                        vehicle, new_strip_index) == -1:
                    vehicle.set_speed(0)
                    return

                if not node.intersection_strip_exists(old_link_index, old_strip_index,
                                                      new_link_index, new_strip_index):
                    new_strip = self._create_intersection_strip(
                        vehicle, old_link_index, new_link_index)
                    if node.is_roundabout():
                        # deflect the crossing into an arc around the island
                        cx, cy = node.get_centre()
                        new_strip.set_arc(cx * Parameters.pixel_per_meter,
                                          cy * Parameters.pixel_per_meter)
                    node.add_intersection_strip(new_strip)

                if not self._turn_lane_allows(vehicle, old_link_index,
                                              new_link_index, old_strip_index):
                    # wrong lane for this turn: hold at the stop line
                    vehicle.set_speed(0)
                elif node.is_bundle_active(old_link_index):
                    vehicle.set_intersection_strip_index(node.get_my_intersection_strip(
                        old_link_index, old_strip_index, new_link_index, new_strip_index))
                    vehicle.set_node(node)

                    node.add_vehicle(vehicle)
                    vehicle.set_distance_in_intersection(0)
                    vehicle.set_in_intersection(True)
                    if node.do_overlap(vehicle):
                        node.remove_vehicle(vehicle)
                        vehicle.set_in_intersection(False)
                        vehicle.set_speed(0)
                    else:
                        # Deflection: a roundabout bends the path around the
                        # island, so drivers slow to a circulating speed rather
                        # than crossing at approach speed. Tighter islands
                        # deflect more, hence the radius term.
                        if node.is_roundabout():
                            radius = node.get_roundabout_radius()
                            circulating = Constants.ROUNDABOUT_SPEED_FACTOR * math.sqrt(
                                max(radius, 1.0))
                            vehicle.set_speed(jmin(vehicle.get_speed(), circulating))
                        # in the next step the vehicle will start moving in the
                        # intersection; so here we update statistics
                        vehicle.update_segment_leaving_data()
                else:
                    # here the vehicle is at segment end and the signal is red
                    # so we need to make the speed 0
                    vehicle.set_speed(0)
        else:
            # at link middle
            link = vehicle.get_link()
            current_segment_index = vehicle.get_segment_index()

            entering_segment = (link.get_segment(current_segment_index - 1)
                                if vehicle.is_reverse_link()
                                else link.get_segment(current_segment_index + 1))

            link_segment_orientation = get_link_and_segment_orientation(
                vehicle.get_seg_end_x(), vehicle.get_seg_end_y(), entering_segment,
                entering_segment)

            strip_index = vehicle.get_new_strip_index(
                link.get_segment(current_segment_index), entering_segment,
                link_segment_orientation.reverse_segment)

            flag = True
            for j in range(vehicle.get_number_of_strips()):
                if not entering_segment.get_strip(
                        strip_index + j).has_gap_for_adding_vehicle(vehicle.get_length()):
                    flag = False

            if flag:
                vehicle.free_strips()
                vehicle.decrease_vehicle_count_on_segment()
                vehicle.update_segment_leaving_data()

                vehicle.set_reverse_segment(link_segment_orientation.reverse_segment)
                vehicle.segment_change(entering_segment.get_index(), strip_index)
                vehicle.update_segment_entering_data()

                if link_segment_orientation.reverse_segment:
                    vehicle.set_seg_start_x(entering_segment.get_end_x())
                    vehicle.set_seg_start_y(entering_segment.get_end_y())
                    vehicle.set_seg_end_x(entering_segment.get_start_x())
                    vehicle.set_seg_end_y(entering_segment.get_start_y())
                else:
                    vehicle.set_seg_start_x(entering_segment.get_start_x())
                    vehicle.set_seg_start_y(entering_segment.get_start_y())
                    vehicle.set_seg_end_x(entering_segment.get_end_x())
                    vehicle.set_seg_end_y(entering_segment.get_end_y())
            else:
                vehicle.set_speed(0)

    def _move_vehicles(self) -> None:
        for vehicle in self.vehicle_list:
            if Parameters.PENALTY_WAIT:
                if vehicle.is_has_already_collided():
                    if vehicle.has_penalty_time_passed():
                        vehicle.remove_from_simulation(False)
                    continue
            if vehicle.is_in_intersection():
                vehicle.free_strips()
                if vehicle.is_at_intersection_end():
                    self._move_vehicle_at_intersection_end(vehicle)
                else:
                    vehicle.move_vehicle_in_intersection()
                    if vehicle.is_at_intersection_end():
                        vehicle.print_vehicle_details()
                        self._move_vehicle_at_intersection_end(vehicle)
            else:
                s = self._get_next_signal(vehicle)
                vehicle.set_signal_on_link(s)
                if vehicle.is_at_segment_end():
                    self._move_vehicle_at_segment_end(vehicle)
                    if vehicle.is_in_intersection():
                        vehicle.free_strips()
                        vehicle.print_vehicle_details()
                        vehicle.move_vehicle_in_intersection()
                else:
                    self._move_vehicle_at_segment_middle(vehicle)

                    if vehicle.is_at_segment_end():
                        vehicle.print_vehicle_details()
                        self._move_vehicle_at_segment_end(vehicle)
                        if vehicle.is_in_intersection():
                            vehicle.free_strips()
                            vehicle.print_vehicle_details()
                            vehicle.move_vehicle_in_intersection()
            vehicle.increment_fuel_consumption()
            vehicle.print_vehicle_details()
            vehicle.add_stats()
            # vehicle.print_all_leaders()

        for vehicle in self.vehicle_list:
            self.accident_log_all_vehicle_at_last(vehicle)

        for vehicle in self.vehicle_list:
            self.accident_check_all_vehicles_at_last(vehicle)

        if Parameters.simulation_step % (60 * Constants.TIME_STEP) == 0:
            Statistics.flow[(Parameters.simulation_step
                             // jint(60 * Constants.TIME_STEP)) - 1] = Statistics.flow_count
            Statistics.flow_count = 0

    def _control_signal(self) -> None:
        for node in self.intersection_list:
            # node.adaptive_signal_change(Parameters.simulation_step)
            if node.is_roundabout():
                node.roundabout_signal_change()   # give way to circulating traffic
            else:
                node.constant_signal_change(Parameters.simulation_step)

    def _get_next_signal(self, vehicle) -> SIGNAL:
        demand_index = vehicle.get_demand_index()
        path_index = vehicle.get_path_index()
        path_link_index = vehicle.get_link_index_on_path()
        path = self.demand_list[demand_index].get_path(path_index)
        last_link_in_path_index = path.get_number_of_links() - 1
        if path_link_index == last_link_in_path_index:
            return SIGNAL.GREEN
        link_index = path.get_link(path_link_index)
        node = (self.node_list[vehicle.get_link().get_up_node()]
                if vehicle.is_reverse_link()
                else self.node_list[vehicle.get_link().get_down_node()])
        return node.get_signal_on_link(link_index)

    def _update_flow(self, old_dist_in_segment: float, new_dist_in_segment: float,
                     vehicle) -> None:
        link_id = 0
        segment_id = 0
        sensor_distance = 950
        direction = True

        condition1 = vehicle.get_link().get_id() == link_id
        condition2 = vehicle.get_segment_index() == segment_id
        condition3 = vehicle.is_reverse_link() == direction
        condition4 = old_dist_in_segment < sensor_distance
        condition5 = new_dist_in_segment > sensor_distance
        if condition1 and condition2 and condition3 and condition4 and condition5:
            Statistics.flow_count += 1

    @staticmethod
    def input_path(filename: str) -> str:
        """Resolve an input file inside the selected network folder.

        Falls back to ``input/<filename>`` when no network is selected or the
        network folder does not provide that file.
        """
        if Parameters.NETWORK_DIR:
            candidate = os.path.join("input", Parameters.NETWORK_DIR, filename)
            if os.path.exists(candidate):
                return candidate
        return os.path.join("input", filename)

    @staticmethod
    def available_networks():
        """Sub-folders of ``input/`` that contain a network definition."""
        names = []
        try:
            for entry in sorted(os.listdir("input")):
                if os.path.isfile(os.path.join("input", entry, "node.txt")):
                    names.append(entry)
        except OSError:
            pass
        return names

    def _read_geometry(self) -> None:
        """Load the network's real-world geometry description, if any.

        ``geometry.txt`` holds one directive per line; blank lines and lines
        starting with ``#`` are ignored. Currently understood::

            median     <linkId> <widthMetres>
            roundabout <nodeId> <radiusMetres>
            oneway     <linkId>

        The file is only consulted when ``GeometryMode On`` is set, so a default
        run is unaffected and remains byte-identical to the Java reference.
        """
        Parameters.MEDIAN_WIDTHS = {}
        Parameters.ROUNDABOUTS = {}
        Parameters.ONEWAY_LINKS = set()
        Parameters.TURN_LANES = {}
        self.turn_lane_held = 0
        self.turn_lane_forced = 0
        if not Parameters.GEOMETRY_MODE:
            return
        path = self.input_path("geometry.txt")
        if not os.path.exists(path):
            return
        medians = {}
        roundabouts = {}
        oneways = set()
        turn_lanes = {}
        try:
            with open(path, "r") as reader:
                for line in reader:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    tokens = line.split()
                    if len(tokens) == 3 and tokens[0].lower() == "median":
                        medians[int(tokens[1])] = float(tokens[2])
                    elif len(tokens) >= 3 and tokens[0].lower() == "roundabout":
                        roundabouts[int(tokens[1])] = float(tokens[2])
                    elif len(tokens) == 2 and tokens[0].lower() == "oneway":
                        oneways.add(int(tokens[1]))
                    elif len(tokens) == 5 and tokens[0].lower() == "turnlane":
                        turn_lanes[(int(tokens[1]), int(tokens[2]))] = (
                            int(tokens[3]), int(tokens[4]))
        except (OSError, ValueError) as ex:
            print(f"geometry.txt ignored: {ex}")
            return
        Parameters.MEDIAN_WIDTHS = medians
        Parameters.ROUNDABOUTS = roundabouts
        Parameters.ONEWAY_LINKS = oneways
        Parameters.TURN_LANES = turn_lanes
        if turn_lanes:
            print(f"Geometry: turn lanes on {len(turn_lanes)} movement(s) "
                  + ", ".join(f"{a}->{b} strips {lo}-{hi}"
                              for (a, b), (lo, hi) in sorted(turn_lanes.items())))
        if oneways:
            print(f"Geometry: one-way link(s) "
                  f"{', '.join(str(k) for k in sorted(oneways))}")
        if medians:
            print(f"Geometry: medians on {len(medians)} link(s) "
                  f"({', '.join(f'{k}={v}m' for k, v in sorted(medians.items()))})")
        if roundabouts:
            print(f"Geometry: roundabout at node(s) "
                  f"{', '.join(f'{k} (r={v}m)' for k, v in sorted(roundabouts.items()))}")

    def _read_network(self) -> None:
        try:
            with open(self.input_path("link.txt"), "r") as reader:
                num_links = int(reader.readline())
                for i in range(num_links):
                    tokens = reader.readline().split()
                    link_id = int(tokens[0])
                    node_id1 = int(tokens[1])
                    node_id2 = int(tokens[2])
                    segment_count = int(tokens[3])
                    link = Link(i, link_id, node_id1, node_id2)
                    for j in range(segment_count):
                        tokens = reader.readline().split()
                        segment_id = int(tokens[0])
                        start_x = float(tokens[1])
                        start_y = float(tokens[2])
                        end_x = float(tokens[3])
                        end_y = float(tokens[4])
                        segment_width = float(tokens[5])

                        first_segment = (j == 0)
                        last_segment = (j == segment_count - 1)

                        segment = Segment(i, j, segment_id, start_x, start_y, end_x,
                                          end_y, segment_width, last_segment,
                                          first_segment, link_id)
                        link.add_segment(segment)
                    self.link_list.append(link)

            with open(self.input_path("node.txt"), "r") as reader:
                num_nodes = int(reader.readline())
                boundary_points = []

                for i in range(num_nodes):
                    tokens = reader.readline().split()
                    node_id = int(tokens[0])
                    center_x = float(tokens[1])
                    center_y = float(tokens[2])
                    if center_x != 0 or center_y != 0:
                        boundary_points.append(Point2D(center_x, center_y))
                    node = Node(i, node_id, center_x, center_y)
                    for token in tokens[3:]:
                        node.add_link(self._get_link_index(int(token)))
                    if node.number_of_links() > 1:
                        node.create_bundles()
                        radius = Parameters.ROUNDABOUTS.get(node_id, 0.0)
                        if radius > 0:
                            node.set_roundabout(radius)
                        self.intersection_list.append(node)
                    self.node_list.append(node)

            self._validate_network()
            self._setup_roundabouts()

            left = DOUBLE_MAX_VALUE
            right = 0
            top = DOUBLE_MAX_VALUE
            down = 0

            for boundary_point in boundary_points:
                left = jmin(left, boundary_point.x)
                right = jmax(right, boundary_point.x)

                top = jmin(top, boundary_point.y)
                down = jmax(down, boundary_point.y)
            self.mid_point = Point2D((left + right) / 2, (top + down) / 2)
        except OSError as ex:
            print(f"SEVERE: {ex}")

        # Optional friendly node names: "<id> <name with spaces>" per line.
        names = {}
        try:
            with open(self.input_path("node_names.txt"), "r") as reader:
                for line in reader:
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        names[int(parts[0])] = parts[1].strip()
        except OSError:
            pass
        Parameters.NODE_NAMES = names

        # Optional survey vehicle mix: "<type index> <percentage>" per line.
        # Converted to cumulative per-10000 thresholds for sampling.
        mix = []
        try:
            shares = []
            hour = Parameters.TIME_OF_DAY
            hourly = self.input_path("vehicle_mix_by_hour.txt")
            if hour >= 0 and os.path.exists(hourly):
                # "<hour> <type index> <percentage>" per line
                with open(hourly, "r") as reader:
                    for line in reader:
                        parts = line.split()
                        if len(parts) == 3 and int(parts[0]) == hour:
                            shares.append((int(parts[1]), float(parts[2])))
            if not shares:
                with open(self.input_path("vehicle_mix.txt"), "r") as reader:
                    for line in reader:
                        parts = line.split()
                        if len(parts) == 2:
                            shares.append((int(parts[0]), float(parts[1])))
            total = sum(s for _, s in shares)
            if shares and total > 0:
                running = 0.0
                for type_, share in shares:
                    running += share / total * 10000.0
                    mix.append((int(round(running)), type_))
                mix[-1] = (10000, mix[-1][1])
        except OSError:
            pass
        Parameters.VEHICLE_MIX = mix

    def _calibrate_roadside_object_density(self) -> None:
        """Size the roadside-object population to the network just loaded.

        Object targets are compared against one network-wide counter, so a
        fixed target spread the same absolute number of parked cars and
        standing pedestrians over whatever road there was.  On the 3.83 km demo
        network that meant roughly a kilometre's worth of side friction across
        four -- a large understatement of the very thing the simulator is for.
        Measuring the length makes every network self-calibrating.
        """
        if Parameters.NETWORK_ROAD_LENGTH >= 0:
            length_km = Parameters.NETWORK_ROAD_LENGTH
        else:
            total = 0.0
            for link in self.link_list:
                for index in range(link.get_number_of_segments()):
                    total += link.get_segment(index).get_length()
            length_km = total / 1000.0
        Constants.calibrate_to_network(length_km)
        print(f"Road length: {jformat(length_km, 2)} km"
              f" ({Constants.AVG_NUMBER_OF_STANDING_PEDESTRIANS} standing pedestrians,"
              f" {Constants.AVG_NUMBER_OF_PARKED_CARS} cars,"
              f" {Constants.AVG_NUMBER_OF_PARKED_RICKSHAWS} rickshaws,"
              f" {Constants.AVG_NUMBER_OF_PARKED_CNGS} CNGs)")

    def _turn_lane_allows(self, vehicle, from_link_index, to_link_index,
                          strip_index) -> bool:
        """Is this vehicle in a lane permitted to make this turn?

        Channelisation reserves a band of strips for a movement -- a left-turn
        pocket, say -- so a driver in the wrong lane cannot take the turn and
        has to wait. DhakaSim's lane changing is not destination-aware, so a
        vehicle can arrive in the wrong band with no way to correct; to stop
        that deadlocking the whole approach, a driver held longer than
        ``TURN_LANE_PATIENCE`` forces the turn anyway. That is also the more
        honest model of Dhaka, where lane discipline is advisory at best.
        """
        if not (Parameters.GEOMETRY_MODE and Parameters.TURN_LANES):
            return True
        from_id = self.link_list[from_link_index].get_id()
        to_id = self.link_list[to_link_index].get_id()
        band = Parameters.TURN_LANES.get((from_id, to_id))
        if band is None:
            return True
        first, last = band
        if first <= strip_index <= last:
            return True
        if vehicle.get_waiting_time() >= Constants.TURN_LANE_PATIENCE:
            self.turn_lane_forced += 1
            return True
        self.turn_lane_held += 1
        return False

    def _validate_oneway(self) -> None:
        """Check that every link declared one-way really carries one direction.

        Opening the full carriageway to both directions is only safe because
        the demand runs one way. If a declared link turns out to carry traffic
        both ways, opposing streams would share the same strips, so say so
        loudly rather than quietly simulating a head-on road.
        """
        if not (Parameters.GEOMETRY_MODE and Parameters.ONEWAY_LINKS):
            return
        for link in self.link_list:
            if link.get_id() not in Parameters.ONEWAY_LINKS:
                continue
            # the terminal this arm serves: the end node with only this link
            terminal = None
            for node_id in (link.get_up_node(), link.get_down_node()):
                node = self.node_list[self._get_node_index(node_id)]
                if node.number_of_links() == 1:
                    terminal = node
            if terminal is None:
                continue        # internal link; direction is not a terminal OD
            out = sum(d.get_demand() for d in self.demand_list
                      if d.get_source() == terminal.get_index())
            into = sum(d.get_demand() for d in self.demand_list
                       if d.get_destination() == terminal.get_index())
            if out > 0 and into > 0:
                print(f"WARNING: link {link.get_id()} is declared one-way but "
                      f"carries demand both ways (out {out} / in {into}); "
                      f"opposing traffic would share the full carriageway")

    def _validate_network(self) -> None:
        """Warn about node/link wiring that does not agree.

        Every link a node claims must actually name that node as one of its
        endpoints. Getting this wrong silently attaches an arm to the wrong
        road, which is easy to miss because the simulation still runs -- it just
        models a different network than intended.
        """
        for node in self.node_list:
            for j in range(node.number_of_links()):
                link_index = node.get_link(j)
                if link_index < 0 or link_index >= len(self.link_list):
                    print(f"WARNING: node {node.get_id()} refers to unknown "
                          f"link index {link_index}")
                    continue
                link = self.link_list[link_index]
                if node.get_id() not in (link.get_up_node(), link.get_down_node()):
                    print(f"WARNING: node {node.get_id()} lists link "
                          f"{link.get_id()}, but that link runs "
                          f"{link.get_up_node()} -> {link.get_down_node()} and "
                          f"does not touch node {node.get_id()}")

    def _setup_roundabouts(self) -> None:
        """Give every roundabout node its circulation order.

        The order is the incident arms sorted by compass bearing, measured from
        the junction outwards. Bearings increase clockwise, and clockwise is the
        direction traffic circulates where driving is on the left.
        """
        for node in self.intersection_list:
            if not node.is_roundabout():
                continue
            arms = []
            for j in range(node.number_of_links()):
                link_index = node.get_link(j)
                link = self.link_list[link_index]
                if link.get_up_node() == node.get_id():
                    seg = link.get_first_segment()
                    near = (seg.get_start_x(), seg.get_start_y())
                    far = (seg.get_end_x(), seg.get_end_y())
                else:
                    seg = link.get_last_segment()
                    near = (seg.get_end_x(), seg.get_end_y())
                    far = (seg.get_start_x(), seg.get_start_y())
                arms.append((link_index, near, far))
            if not arms:
                continue
            cx = sum(a[1][0] for a in arms) / len(arms)
            cy = sum(a[1][1] for a in arms) / len(arms)
            node.set_centre(cx, cy)
            ordered = []
            for link_index, _near, far in arms:
                # screen y grows downward, so -dy points north
                bearing = math.degrees(math.atan2(far[0] - cx,
                                                  -(far[1] - cy))) % 360
                ordered.append((bearing, link_index))
            ordered.sort()
            node.set_circulation_order([li for _b, li in ordered])

    def _get_link_index(self, link_id: int) -> int:
        for link in self.link_list:
            if link.get_id() == link_id:
                return link.get_index()
        return -1

    def _get_node_index(self, node_id: int) -> int:
        for node in self.node_list:
            if node.get_id() == node_id:
                return node.get_index()
        return -1

    def _read_demand(self) -> None:
        try:
            rows = []
            hour = Parameters.TIME_OF_DAY
            hourly = self.input_path("demand_by_hour.txt")
            if hour >= 0 and os.path.exists(hourly):
                # "<hour> <source> <destination> <vehiclesPerHour>" per line
                with open(hourly, "r") as reader:
                    for line in reader:
                        tokens = line.split()
                        if len(tokens) == 4 and int(tokens[0]) == hour:
                            rows.append((int(tokens[1]), int(tokens[2]),
                                         int(tokens[3])))
                if rows:
                    print(f"Time of day: {hour:02d}:00-{(hour + 1) % 24:02d}:00 "
                          f"({sum(r[2] for r in rows)} veh/h)")
            if not rows:
                with open(self.input_path("demand.txt"), "r") as reader:
                    num_demands = int(reader.readline())
                    for _ in range(num_demands):
                        tokens = reader.readline().split()
                        rows.append((int(tokens[0]), int(tokens[1]),
                                     int(tokens[2])))

            for node_id1, node_id2, demand in rows:
                if Parameters.DEMAND_OVERRIDE >= 0:
                    demand = Parameters.DEMAND_OVERRIDE
                self.demand_list.append(Demand(self._get_node_index(node_id1),
                                               self._get_node_index(node_id2),
                                               jint(demand + Parameters.DEMAND_OFFSET)))
        except OSError as ex:
            print(f"SEVERE: {ex}")

    def _read_path(self) -> None:
        try:
            with open(self.input_path("path.txt"), "r") as reader:
                num_paths = int(reader.readline())
                for _ in range(num_paths):
                    tokens = reader.readline().split()
                    node_id1 = int(tokens[0])
                    node_id2 = int(tokens[1])
                    path = Path(self._get_node_index(node_id1),
                                self._get_node_index(node_id2))
                    for token in tokens[2:]:
                        path.add_link(self._get_link_index(int(token)))
                    self.path_list.append(path)
        except OSError as ex:
            print(f"SEVERE: {ex}")

    def _add_path_to_demand(self) -> None:
        for path in self.path_list:
            for demand in self.demand_list:
                if (demand.get_source() == path.get_source()
                        and demand.get_destination() == path.get_destination()):
                    demand.add_path(path)

    @staticmethod
    def _total_acc_count(acc) -> None:
        total = 0
        for v in acc:
            total += v
