"""Port of ``thesisfinal.Link``."""

from __future__ import annotations


class Link:
    __slots__ = ("_index", "_id", "_up_node", "_down_node", "_segment_list")

    def __init__(self, index: int, id_: int, up_node: int, down_node: int):
        self._index = index
        self._id = id_
        self._up_node = up_node
        self._down_node = down_node
        self._segment_list = []

    def get_index(self) -> int:
        return self._index

    def set_index(self, index: int) -> None:
        self._index = index

    def get_id(self) -> int:
        return self._id

    def set_id(self, id_: int) -> None:
        self._id = id_

    def get_up_node(self) -> int:
        return self._up_node

    def set_up_node(self, up_node: int) -> None:
        self._up_node = up_node

    def get_down_node(self) -> int:
        return self._down_node

    def set_down_node(self, down_node: int) -> None:
        self._down_node = down_node

    def get_segment(self, index: int):
        return self._segment_list[index]

    def get_first_segment(self):
        return self._segment_list[0]

    def get_last_segment(self):
        return self._segment_list[-1]

    def add_segment(self, segment) -> None:
        self._segment_list.append(segment)

    def get_number_of_segments(self) -> int:
        return len(self._segment_list)

    def draw(self, canvas) -> None:
        for i in range(self.get_number_of_segments()):
            self.get_segment(i).draw(canvas)
