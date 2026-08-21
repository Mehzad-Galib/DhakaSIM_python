"""Port of ``thesisfinal.NormalDistribution``.

These functions generate roadside objects and walking pedestrians: the road
blockage caused by them follows a sum of Gaussian distributions according to
the field study behind the simulator.
"""

from __future__ import annotations

import math

from .javacompat import jexp
from .parameters import scratch_random


class NormalDistribution:
    __slots__ = ("factor", "mean", "standard_deviation")

    def __init__(self, factor: float, mean: float, standard_deviation: float):
        self.factor = factor
        self.mean = mean
        self.standard_deviation = standard_deviation

    def get_a_random_value(self) -> float:
        return scratch_random().next_gaussian() * self.standard_deviation + self.mean

    def get_a_random_value_with_factor(self) -> float:
        return self.factor * (scratch_random().next_gaussian() * self.standard_deviation
                              + self.mean)

    def get_probability_density(self, x: float) -> float:
        power_of_exp = (-((x - self.mean) * (x - self.mean))
                        / (2 * self.standard_deviation * self.standard_deviation))
        return jexp(power_of_exp) / (self.standard_deviation * math.sqrt(2 * math.pi))

    def get_probability_density_with_factor(self, x: float) -> float:
        power_of_exp = (-((x - self.mean) * (x - self.mean))
                        / (2 * self.standard_deviation * self.standard_deviation))
        return self.factor * (jexp(power_of_exp)
                              / (self.standard_deviation * math.sqrt(2 * math.pi)))
