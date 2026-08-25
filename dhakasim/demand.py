"""Port of ``thesisfinal.Demand``."""

from __future__ import annotations


class Demand:
    __slots__ = ("_source", "_destination", "_demand", "_path_list")

    def __init__(self, source: int, destination: int, demand: int):
        self._source = source
        self._destination = destination
        self._demand = demand
        self._path_list = []

    def get_source(self) -> int:
        return self._source

    def set_source(self, source: int) -> None:
        self._source = source

    def get_destination(self) -> int:
        return self._destination

    def set_destination(self, destination: int) -> None:
        self._destination = destination

    def get_demand(self) -> int:
        return self._demand

    def set_demand(self, demand: int) -> None:
        self._demand = demand

    def get_path(self, index: int):
        return self._path_list[index]

    def add_path(self, path) -> None:
        self._path_list.append(path)

    def get_number_of_paths(self) -> int:
        return len(self._path_list)
