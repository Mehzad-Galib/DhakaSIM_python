"""Port of ``thesisfinal.VehicleStats``."""

from __future__ import annotations


class VehicleStats:
    __slots__ = ("_vehicle_id", "_accelerations", "_speeds", "_trajectory")

    def __init__(self, vehicle_id: int, simulation_end_time: int):
        self._vehicle_id = vehicle_id
        self._accelerations = [0.0] * simulation_end_time
        self._speeds = [0.0] * simulation_end_time
        self._trajectory = [None] * simulation_end_time

    def add_acceleration(self, acceleration: float, simulation_step: int) -> None:
        self._accelerations[simulation_step] = acceleration

    def add_speed(self, speed: float, simulation_step: int) -> None:
        self._speeds[simulation_step] = speed

    def add_point_on_trajectory(self, point, simulation_step: int) -> None:
        self._trajectory[simulation_step] = point

    def get_vehicle_id(self) -> int:
        return self._vehicle_id

    def get_speeds(self):
        return self._speeds

    def get_trajectory(self):
        return self._trajectory

    def get_x_trajectory(self):
        return [0.0 if p is None else p.x for p in self._trajectory]

    def get_y_trajectory(self):
        return [0.0 if p is None else p.y for p in self._trajectory]
