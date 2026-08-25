"""Pin the multi-objective signal scheduler.

An optimiser fails quietly.  Hand it a broken objective and it still returns a
schedule, the simulation still runs, and the only symptom is that the traffic
is a bit slower than it should be -- which is indistinguishable from the
traffic simply being slow.  So the arithmetic of both objective functions is
computed by hand here, and the pathological cases that actually bit during
development each have a test of their own.

Run with ``python tests/test_signal_schedule.py``.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import signal_schedule as ss       # noqa: E402
from dhakasim.parameters import Parameters       # noqa: E402


def close(got, want, tol=1e-9):
    assert abs(got - want) <= tol, f"got {got!r}, want {want!r}"


# --------------------------------------------------------------------------
# vehicle classes
# --------------------------------------------------------------------------

def test_the_three_human_powered_types_are_non_motorised():
    """Bicycle, rickshaw and van/cart, matching Processor's own split.

    If these two ever disagree the optimiser tunes for one definition of
    heterogeneity while the report measures another, and nothing complains.
    """
    for slow in (0, 1, 2):
        assert not ss.is_motorised(slow)
    for fast in (3, 4, 5, 6, 7, 8, 9, 10, 11):
        assert ss.is_motorised(fast)


def test_a_pedestrian_is_neither():
    assert not ss.is_motorised(ss.PEDESTRIAN_TYPE)


# --------------------------------------------------------------------------
# the numerical simulator
# --------------------------------------------------------------------------

def test_a_green_clears_each_class_at_its_own_rate():
    """The whole reason a heterogeneous objective is worth anything."""
    (left,), (red,) = ss.run_cycle([ss.Demand(50, 50)], [10.0])
    close(left.motorised, 50 - 0.9 * 10)
    close(left.non_motorised, 50 - 0.4 * 10)
    close(red, 0.0)                       # one approach, so never red


def test_a_queue_cannot_go_negative():
    (left,), _ = ss.run_cycle([ss.Demand(2, 1)], [600.0])
    close(left.motorised, 0.0)
    close(left.non_motorised, 0.0)


def test_red_is_the_rest_of_the_cycle():
    """Exactly one approach is green at a time, so this is the whole of it."""
    demands = [ss.Demand(1, 0) for _ in range(4)]
    _, reds = ss.run_cycle(demands, [10.0, 20.0, 30.0, 40.0])
    assert reds == [90.0, 80.0, 70.0, 60.0]


# --------------------------------------------------------------------------
# the objective functions
# --------------------------------------------------------------------------

def test_version_1_counts_every_vehicle_and_every_second_alike():
    demands = [ss.Demand(20, 10), ss.Demand(0, 0)]
    f1, f2 = ss.objectives_v1(demands, [10.0, 10.0])
    # 20 - 9 = 11 motorised and 10 - 4 = 6 non-motorised left on the first
    close(f1, 11.0 + 6.0)
    close(f2, 10.0 + 10.0)                # each approach red for the other's green


def test_version_2_prices_the_two_classes_apart():
    demands = [ss.Demand(20, 10), ss.Demand(0, 0)]
    f1, _ = ss.objectives_v2(demands, [10.0, 10.0], 0.25)
    close(f1, 0.25 * 11.0 + 0.75 * 6.0)


def test_version_2_weighs_red_by_the_traffic_sitting_through_it():
    """Equation 4: sixty seconds of red on an empty approach costs nothing."""
    demands = [ss.Demand(30, 0), ss.Demand(0, 0)]
    _, f2 = ss.objectives_v2(demands, [10.0, 50.0], 0.5)
    # only the first approach carries anything, and it is red for 50 s
    close(f2, (30.0 * 50.0) / 30.0)


def test_an_empty_intersection_has_no_average_delay():
    """And, more to the point, does not divide by zero on a quiet hour."""
    demands = [ss.Demand(0, 0), ss.Demand(0, 0)]
    f1, f2 = ss.objectives_v2(demands, [10.0, 10.0], 0.5)
    close(f1, 0.0)
    close(f2, 0.0)


# --------------------------------------------------------------------------
# NSGA-II's machinery
# --------------------------------------------------------------------------

def _individual(f1, f2):
    one = ss._Individual([1.0])
    one.f1, one.f2 = f1, f2
    return one


def test_dominance_needs_to_be_better_somewhere():
    assert _individual(1.0, 1.0).dominates(_individual(2.0, 2.0))
    assert _individual(1.0, 2.0).dominates(_individual(1.0, 3.0))
    assert not _individual(1.0, 1.0).dominates(_individual(1.0, 1.0))
    assert not _individual(1.0, 3.0).dominates(_individual(3.0, 1.0))


def test_the_sort_separates_the_front_from_the_rest():
    population = [_individual(1.0, 5.0), _individual(5.0, 1.0),
                  _individual(3.0, 3.0), _individual(9.0, 9.0)]
    fronts = ss._non_dominated_sort(population)
    assert len(fronts[0]) == 3, "the three mutually non-dominated ones"
    assert fronts[-1][0].f1 == 9.0


def test_the_ends_of_a_front_are_never_crowded_out():
    front = [_individual(1.0, 5.0), _individual(3.0, 3.0), _individual(5.0, 1.0)]
    ss._crowding_distance(front)
    middle = [one for one in front if one.f1 == 3.0][0]
    assert middle.crowding < float("inf")
    for end in (1.0, 5.0):
        assert [one for one in front if one.f1 == end][0].crowding == float("inf")


def test_crossover_and_mutation_stay_inside_the_bounds():
    rng = random.Random(3)
    for _ in range(200):
        a = [rng.uniform(5.0, 600.0) for _ in range(4)]
        b = [rng.uniform(5.0, 600.0) for _ in range(4)]
        for child in ss._sbx(a, b, 5.0, 600.0, 1.0, rng):
            for gene in ss._polynomial_mutation(child, 5.0, 600.0, 1.0, rng):
                assert 5.0 <= gene <= 600.0, gene


# --------------------------------------------------------------------------
# picking one schedule off the front
# --------------------------------------------------------------------------

def test_the_shorter_cycle_breaks_a_tie():
    """The bug that made an empty junction hold one approach green for ten
    minutes.  With nothing waiting, every schedule scores zero on both
    objectives and the front is the whole population; without this the answer
    was whichever random individual came first."""
    long_cycle = ss._Individual([300.0, 300.0])
    short_cycle = ss._Individual([5.0, 5.0])
    for one in (long_cycle, short_cycle):
        one.f1 = one.f2 = 0.0
    assert ss._pick_from_front([long_cycle, short_cycle], 0.5) is short_cycle
    assert ss._pick_from_front([short_cycle, long_cycle], 0.5) is short_cycle


def test_an_empty_approach_gets_the_minimum_green():
    demands = [ss.Demand(40, 5), ss.Demand(0, 0), ss.Demand(10, 0)]
    greens = ss.optimise(demands, rng=random.Random(1), evaluations=400,
                         green_min=5.0, green_max=600.0)
    close(greens[1], 5.0)


def test_an_empty_intersection_gets_the_shortest_cycle_there_is():
    demands = [ss.Demand(0, 0) for _ in range(4)]
    greens = ss.optimise(demands, rng=random.Random(1), evaluations=400,
                         green_min=5.0, green_max=600.0)
    assert greens == [5.0] * 4, greens


# --------------------------------------------------------------------------
# the optimiser as a whole
# --------------------------------------------------------------------------

def test_the_busiest_approach_gets_the_longest_green():
    demands = [ss.Demand(2, 0), ss.Demand(80, 0), ss.Demand(3, 0)]
    greens = ss.optimise(demands, rng=random.Random(5), evaluations=2000)
    assert greens[1] == max(greens), greens


def test_a_queue_of_rickshaws_earns_more_green_than_the_same_queue_of_cars():
    """Equation 3's whole purpose: a rickshaw takes more than twice as long to
    clear the stop line, so the same number of them needs more green."""
    motors = ss.optimise([ss.Demand(40, 0), ss.Demand(5, 0)],
                         rng=random.Random(11), evaluations=3000)
    slow = ss.optimise([ss.Demand(0, 40), ss.Demand(5, 0)],
                       rng=random.Random(11), evaluations=3000)
    assert slow[0] > motors[0], (motors, slow)


def test_every_green_respects_its_bounds():
    demands = [ss.Demand(30, 30) for _ in range(5)]
    greens = ss.optimise(demands, rng=random.Random(2), evaluations=1000,
                         green_min=7.0, green_max=45.0)
    for green in greens:
        assert 7.0 <= green <= 45.0, green


def test_the_same_seed_gives_the_same_schedule():
    demands = [ss.Demand(30, 10), ss.Demand(9, 40), ss.Demand(1, 1)]
    first = ss.optimise(demands, rng=random.Random(99), evaluations=800)
    second = ss.optimise(demands, rng=random.Random(99), evaluations=800)
    assert first == second


def test_one_approach_is_not_a_scheduling_problem():
    assert ss.optimise([ss.Demand(5, 5)], green_max=600.0) == [600.0]
    assert ss.optimise([]) == []


# --------------------------------------------------------------------------
# the baselines
# --------------------------------------------------------------------------

def test_fixed_gives_every_approach_the_same():
    assert ss.fixed_schedule(4, 10.0) == [10.0] * 4


def test_biased_random_follows_the_share_of_the_traffic():
    """Not a control strategy -- an approximation of a traffic policeman."""
    demands = [ss.Demand(90, 0), ss.Demand(10, 0)]
    rng = random.Random(4)
    busier = 0
    for _ in range(50):
        greens = ss.biased_random_schedule(demands, low=100.0, high=100.0,
                                           rng=rng)
        if greens[0] > greens[1]:
            busier += 1
    assert busier == 50, "the busier approach should always win at a fixed draw"


def test_biased_random_never_returns_a_green_of_nothing():
    """A zero green is an approach that never opens."""
    demands = [ss.Demand(1000, 0), ss.Demand(1, 0)]
    greens = ss.biased_random_schedule(demands, rng=random.Random(6),
                                       green_min=5.0)
    for green in greens:
        assert green >= 5.0, green


# --------------------------------------------------------------------------
# the module the simulator talks to
# --------------------------------------------------------------------------

def test_every_mode_returns_one_green_per_approach():
    demands = [ss.Demand(20, 5), ss.Demand(4, 30), ss.Demand(0, 0)]
    for mode in ss.MODES:
        greens = ss.schedule(demands, mode, rng=random.Random(8),
                             evaluations=400)
        assert len(greens) == len(demands), mode
        for green in greens:
            assert green > 0.0, (mode, greens)


def test_an_unknown_mode_is_refused_rather_than_guessed_at():
    try:
        ss.schedule([ss.Demand(1, 1)], "adaptive", rng=random.Random(0))
    except ValueError:
        return
    raise AssertionError("an unknown signal mode should raise")


def test_the_optimiser_leaves_the_simulation_random_stream_alone():
    """The one thing in this module that could break Java parity.

    ``Parameters.random`` decides which vehicles get generated and when.  Take
    a single draw from it here and every vehicle after it moves.
    """
    class _Tripwire:
        def __getattr__(self, name):
            raise AssertionError(
                f"the scheduler reached for Parameters.random.{name}")

    saved = Parameters.random
    Parameters.random = _Tripwire()
    try:
        demands = [ss.Demand(30, 10), ss.Demand(5, 5), ss.Demand(0, 12)]
        for mode in ss.MODES:
            ss.schedule(demands, mode, rng=ss.seed_from(41), evaluations=400)
    finally:
        Parameters.random = saved


def test_a_negative_seed_still_gives_a_usable_stream():
    """-1 means "different every run" for the simulation; the optimiser still
    has to produce a schedule."""
    assert isinstance(ss.seed_from(-1), random.Random)
    assert isinstance(ss.seed_from(None), random.Random)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            else:
                print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
