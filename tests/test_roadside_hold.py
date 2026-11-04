"""Pin what a vehicle does when a roadside object holds it up.

``move_vehicle_in_segment`` asks first whether an object is in proximity and,
if so, only tries a strip change -- the forward move, the one place speed is
recomputed, is skipped.  A vehicle that found no strip to change to used to
stand still while still reporting last step's speed.  Its follower
car-followed that phantom speed into its tail, froze beside the same object,
and the pair stayed overlapped for minutes: at Banani 27 that was 11,797 of
the run's near-crash log rows against 57 with objects off.

The unit tests drive ``move_vehicle_in_segment`` on a stand-in vehicle so each
branch is exercised alone; the last test runs Banani 27 with objects on and
checks the minute-long overlaps are gone.

Run with ``python tests/test_roadside_hold.py``.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import deque

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dhakasim.vehicle import Vehicle  # noqa: E402


class _Stand:
    """Just enough of a vehicle for ``move_vehicle_in_segment``: whether an
    object is near, whether a strip change succeeds, and a record of whether
    the forward move was asked for."""

    _type = 3  # a motorbike; anything but the walking-pedestrian type

    move_vehicle_in_segment = Vehicle.move_vehicle_in_segment
    _hold_behind_object = Vehicle._hold_behind_object
    _store_prev_speeds = Vehicle._store_prev_speeds

    def __init__(self, object_near, strip_change_works):
        self.object_near = object_near
        self.strip_change_works = strip_change_works
        self.moved_forward = False
        self._strip_index = 4
        self._speed = 5.358
        self._acceleration = 0.7
        self._prev_speeds = deque()
        self._waiting_time = 0
        self._waiting_time_in_segment = 0

    def is_object_in_proximity(self):
        return self.object_near

    def try_lane_change(self, priority_towards_middle):
        if self.strip_change_works:
            self._strip_index -= 1

    def _move_forward_in_segment(self):
        self.moved_forward = True
        return True

    def _is_slower_vehicle_in_proximity(self):
        return False


class HeldVehicleTest(unittest.TestCase):
    def test_a_held_vehicle_stops_and_waits(self):
        v = _Stand(object_near=True, strip_change_works=False)
        v.move_vehicle_in_segment()
        self.assertFalse(v.moved_forward)
        self.assertEqual((v._speed, v._acceleration), (0, 0))
        self.assertEqual((v._waiting_time, v._waiting_time_in_segment), (1, 1))
        # the speed it had is history, as the forward move would have kept it
        self.assertEqual(list(v._prev_speeds), [5.358])

    def test_a_vehicle_that_changes_strip_is_not_held(self):
        v = _Stand(object_near=True, strip_change_works=True)
        v.move_vehicle_in_segment()
        self.assertEqual(v._strip_index, 3)
        self.assertEqual(v._speed, 5.358)
        self.assertEqual(v._waiting_time, 0)

    def test_no_object_means_the_ordinary_forward_move(self):
        v = _Stand(object_near=False, strip_change_works=False)
        v.move_vehicle_in_segment()
        self.assertTrue(v.moved_forward)
        self.assertEqual(v._speed, 5.358)
        self.assertEqual(v._waiting_time, 0)


class NoPersistentOverlapsTest(unittest.TestCase):
    """Banani 27 at nine in the morning, objects on, five minutes.  Before
    the fix the same run already had overlaps of a minute and more."""

    LONGEST_ALLOWED = 30  # steps

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dhakasim_roadside_hold_")
        subprocess.run(
            [sys.executable, os.path.join(REPO, "run_dhakasim.py"),
             "--headless", "--network", "banani_27", "--seed", "3",
             "--set", "SimulationEndTime=300", "--set", "TimeOfDay=9",
             "--set", "ObjectMode=On", "--set", f"StatsDir={cls.dir}",
             "--set", "ReportAnimationFrames=0"],
            cwd=REPO, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_no_overlap_lasts_half_a_minute(self):
        path = os.path.join(self.dir, "csv", "near_crash_episodes.csv")
        with open(path, newline="") as f:
            steps = [int(row["steps"]) for row in csv.DictReader(f)]
        self.assertTrue(steps, "the run logged no near crashes at all")
        self.assertLess(max(steps), self.LONGEST_ALLOWED)


if __name__ == "__main__":
    unittest.main()
