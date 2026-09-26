"""TurnDisciplineMode: the turning-speed cap and the settings that drive it.

The behaviour inside the box is pinned by ``hash_run.py`` (the baseline was
re-recorded with the mode On after a run with it Off matched the previous
baseline byte for byte); these tests cover the arithmetic the mode rests on
and the plumbing that switches it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import utilities as Utilities  # noqa: E402
from dhakasim.parameters import Parameters  # noqa: E402
from dhakasim.vehicle import Vehicle  # noqa: E402


class _Seg:
    def __init__(self, sx, sy, ex, ey):
        self._p = (sx, sy, ex, ey)

    def get_start_x(self):
        return self._p[0]

    def get_start_y(self):
        return self._p[1]

    def get_end_x(self):
        return self._p[2]

    def get_end_y(self):
        return self._p[3]


class TurnAngleTest(unittest.TestCase):
    def test_straight_across_is_zero(self):
        a = _Seg(0, 0, 100, 0)
        b = _Seg(120, 0, 220, 0)
        self.assertAlmostEqual(Vehicle.turn_angle(a, False, b, False), 0.0)

    def test_a_right_angle_is_ninety(self):
        a = _Seg(0, 0, 100, 0)
        b = _Seg(120, 10, 120, 110)
        self.assertAlmostEqual(Vehicle.turn_angle(a, False, b, False), 90.0)

    def test_reverse_flags_flip_the_direction_of_travel(self):
        # The vehicle travels a end -> start and b end -> start: both flipped
        # is the same straight run as neither flipped.
        a = _Seg(100, 0, 0, 0)
        b = _Seg(220, 0, 120, 0)
        self.assertAlmostEqual(Vehicle.turn_angle(a, True, b, True), 0.0)
        # One flipped is a U-turn.
        self.assertAlmostEqual(Vehicle.turn_angle(a, True, b, False), 180.0)

    def test_the_angle_is_unsigned(self):
        a = _Seg(0, 0, 100, 0)
        left = _Seg(120, -10, 120, -110)
        right = _Seg(120, 10, 120, 110)
        self.assertAlmostEqual(Vehicle.turn_angle(a, False, left, False),
                               Vehicle.turn_angle(a, False, right, False))


class TurningSpeedTest(unittest.TestCase):
    def setUp(self):
        self._saved = Parameters.TURN_SPEED
        Parameters.TURN_SPEED = 5.0

    def tearDown(self):
        Parameters.TURN_SPEED = self._saved

    def test_a_slight_bend_is_not_capped(self):
        self.assertEqual(Vehicle.turning_speed(10.0, 16.7), 0.0)

    def test_a_right_angle_gets_the_turn_speed(self):
        self.assertAlmostEqual(Vehicle.turning_speed(90.0, 16.7), 5.0)
        self.assertAlmostEqual(Vehicle.turning_speed(150.0, 16.7), 5.0)

    def test_the_cap_falls_linearly_between(self):
        # Halfway from 15 to 90 degrees: halfway from full speed to the floor.
        self.assertAlmostEqual(Vehicle.turning_speed(52.5, 15.0), 10.0)

    def test_a_slow_vehicle_type_is_never_sped_up(self):
        # A rickshaw at 4 m/s turning a right angle keeps its 4 m/s.
        self.assertAlmostEqual(Vehicle.turning_speed(90.0, 4.0), 4.0)
        self.assertAlmostEqual(Vehicle.turning_speed(40.0, 4.0), 4.0)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self._mode = Parameters.TURN_DISCIPLINE_MODE
        self._speed = Parameters.TURN_SPEED

    def tearDown(self):
        Parameters.TURN_DISCIPLINE_MODE = self._mode
        Parameters.TURN_SPEED = self._speed

    def test_the_mode_is_on_by_default(self):
        self.assertTrue(Parameters.TURN_DISCIPLINE_MODE)

    def test_the_mode_can_be_switched_off(self):
        Utilities.apply_setting("TurnDisciplineMode", "Off")
        self.assertFalse(Parameters.TURN_DISCIPLINE_MODE)
        Utilities.apply_setting("TurnDisciplineMode", "On")
        self.assertTrue(Parameters.TURN_DISCIPLINE_MODE)

    def test_turn_speed_is_written_in_km_per_hour(self):
        Utilities.apply_setting("TurnSpeed", "36")
        self.assertAlmostEqual(Parameters.TURN_SPEED, 10.0)


if __name__ == "__main__":
    unittest.main()
