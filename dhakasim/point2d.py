"""Port of ``thesisfinal.Point2D``."""

from __future__ import annotations


class Point2D:
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Point2D({self.x}, {self.y})"
