"""Port of ``thesisfinal.Path``."""

from __future__ import annotations


class Path:
    __slots__ = ("_source", "_destination", "_link_list")

    def __init__(self, source: int, destination: int):
        self._source = source
        self._destination = destination
        self._link_list = []

    def get_source(self) -> int:
        return self._source

    def set_source(self, source: int) -> None:
        self._source = source

    def get_destination(self) -> int:
        return self._destination

    def set_destination(self, destination: int) -> None:
        self._destination = destination

    def get_link(self, index: int) -> int:
        return self._link_list[index]

    def add_link(self, link: int) -> None:
        self._link_list.append(link)

    def get_number_of_links(self) -> int:
        return len(self._link_list)
