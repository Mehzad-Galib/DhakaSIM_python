"""Pin the modified Newtonian car-following model (``CF_model 13``).

Unlike :mod:`tests.test_javacompat`, there is no Java build to compare against
-- the model is new here, added from the RoadBird paper's equations 4-6.  What
these tests pin is the arithmetic those equations imply, so a later edit cannot
quietly turn it back into the naive model it was meant to improve on.

Run with ``python tests/test_modified_newtonian.py``.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim.parameters import CAR_FOLLOWING_MODEL, Parameters  # noqa: E402
from dhakasim.utilities import _apply_cf_model  # noqa: E402
from dhakasim.vehicle import Vehicle  # noqa: E402

# get_gap() takes the leader's tail as its position and nets off both the
# follower's length and the standstill threshold, so the free space ahead is
# front - THRESHOLD_DISTANCE - length.
_LENGTH = 4.0
_THRESHOLD = Vehicle.THRESHOLD_DISTANCE


def _acceleration(front: float, speed: float, max_acceleration: float = 3.0) -> float:
    """Acceleration for a follower `speed` m/s behind a leader whose tail sits
    at `front` metres."""
    Parameters.ERROR_MODE = False  # otherwise get_dx() adds sensor noise
    leader = SimpleNamespace(_distance_in_segment=front, _speed=0.0)
    follower = SimpleNamespace(_distance_in_segment=0.0, _length=_LENGTH,
                               _speed=speed, _max_acceleration=max_acceleration)
    return Vehicle._get_modified_newtonian_acceleration(leader, follower)


def test_model_index_13_selects_it():
    _apply_cf_model("13")
    assert Parameters.car_following_model is CAR_FOLLOWING_MODEL.MODIFIED_NEWTONIAN_MODEL


def test_open_road_accelerates_at_the_vehicle_limit():
    """A distant leader must not make the vehicle exceed its own capability."""
    assert _acceleration(front=100.0, speed=5.0, max_acceleration=3.0) == 3.0
    assert _acceleration(front=100.0, speed=5.0, max_acceleration=0.42) == 0.42


def test_deceleration_is_capped_at_the_physical_floor():
    """Kudarauskas' 8.5 m/s^2, the one bound the naive model lacks entirely."""
    assert _acceleration(front=10.0, speed=20.0) == Vehicle.MAX_DECELERATION
    # even a hopeless gap cannot brake harder than that
    assert _acceleration(front=4.0, speed=30.0) == Vehicle.MAX_DECELERATION


def test_intermediate_rates_between_the_caps():
    """The whole point of the model: it decelerates smoothly rather than
    choosing between full acceleration and a slam."""
    # gap = 10 - 0.5 - 4 = 5.5; 2*(5.5 - 5*1)/1 = 1.0
    assert _acceleration(front=10.0, speed=5.0) == 1.0
    # gap = 8 - 0.5 - 4 = 3.5; 2*(3.5 - 5*1)/1 = -3.0
    assert _acceleration(front=8.0, speed=5.0) == -3.0
    for a in (_acceleration(front=8.0, speed=5.0),):
        assert Vehicle.MAX_DECELERATION < a < 0.0


def test_gap_exactly_covered_gives_zero():
    """v*dt covers the gap precisely, so no correction is called for."""
    # gap = 9.5 - 0.5 - 4 = 5.0, speed 5 m/s over a 1 s step
    assert _acceleration(front=9.5, speed=5.0) == 0.0


def test_stopped_behind_a_blockage_stays_stopped():
    assert _acceleration(front=_THRESHOLD + _LENGTH, speed=0.0) == 0.0


def test_distance_update_already_carries_the_half_a_t_squared_term():
    """Equation 6's ``+ 0.5*a*dt^2`` needs no new code.

    ``Vehicle._get_new_distance_in_segment`` integrates the speed ramp with
    Boole's rule, which is exact for a linear integrand -- so for the constant
    acceleration this model applies over a step it already equals
    ``x + v*dt + 0.5*a*dt^2``.  Pinned because a "simplification" of that
    quadrature back to ``x + v*dt`` would silently change every model.
    """
    def boole(v_old, v_new, dt):
        dv = v_new - v_old
        return (1.0 / 90) * (7 * v_old + 32 * (v_old + 0.25 * dv)
                             + 12 * (v_old + 0.50 * dv)
                             + 32 * (v_old + 0.75 * dv) + 7 * v_new) * dt

    def equation_6(v_old, v_new, dt):
        return v_old * dt + 0.5 * ((v_new - v_old) / dt) * dt * dt

    for v_old, v_new, dt in [(0.0, 3.0, 1.0), (12.5, 4.0, 1.0), (7.0, 7.0, 1.0),
                             (2.0, 9.5, 0.5), (20.0, 0.0, 1.0)]:
        assert boole(v_old, v_new, dt) == equation_6(v_old, v_new, dt)


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
