"""Port of ``thesisfinal.Statistics``.

All fields are static in Java; constructing ``Statistics(demandSize)`` resets
them, which is exactly what :meth:`Statistics.reset` does here.
"""

from __future__ import annotations

from .constants import Constants
from .javacompat import jint
from .parameters import Parameters


class Statistics:
    avg_speed_of_vehicle = None
    no_of_vehicles = None
    waiting_time = None
    total_travel_time = None
    trip_time = None
    no_of_vehicles_completing_trip = None
    total_fuel_consumption = None
    no_of_collisions = 0
    no_of_accidents = 0
    no_collisions_per_demand = None
    no_accidents_per_demand = None
    flow = None
    flow_count = 0
    no_of_generated_vehicles = None

    vehicle_stats = None

    @classmethod
    def reset(cls, demand_size: int) -> None:
        types = Constants.TYPES_OF_CARS
        cls.avg_speed_of_vehicle = [0.0] * types
        cls.no_of_vehicles = [0] * types
        cls.no_of_generated_vehicles = [0.0] * types
        cls.waiting_time = [0] * types
        cls.total_travel_time = [0] * types
        cls.total_fuel_consumption = [[0.0] * types for _ in range(demand_size)]

        cls.no_of_vehicles_completing_trip = [[0.0] * types for _ in range(demand_size)]
        cls.no_collisions_per_demand = [[0.0] * types for _ in range(demand_size)]
        cls.no_accidents_per_demand = [[0.0] * types for _ in range(demand_size)]
        cls.trip_time = [[0] * types for _ in range(demand_size)]
        cls.vehicle_stats = []
        cls.no_of_collisions = 0
        cls.no_of_accidents = 0

        cls.flow = [0.0] * (Parameters.simulation_end_time
                            // jint(60 * Constants.TIME_STEP))
        cls.flow_count = 0

    @classmethod
    def save_vehicle_stat(cls, stats) -> None:
        vehicle_ids = (1, 2, 3)

        for id_ in vehicle_ids:
            if id_ == stats.get_vehicle_id():
                cls.vehicle_stats.append(stats)
