"""Pin the machine-readable run records written by ``dhakasim/run_data.py``.

Those four files are the dataset a learning or mining study starts from, so
what is pinned here is that they agree with everything else a run writes:
every number mirrored into ``run_results.csv`` is the number in the inherited
CSV it came from, every accident-log row of a run lands in exactly one
near-crash episode, every link appears once a simulated minute, and every row
carries the ``run_id`` of the run that wrote it.  The end-to-end tests run the
real simulator headless for a couple of minutes into a throwaway directory.

That the records leave the run itself alone -- the inherited CSVs byte for
byte -- is pinned by ``python hash_run.py``, not here.

Run with ``python tests/test_run_data.py``.
"""

from __future__ import annotations

import csv
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dhakasim import run_data                     # noqa: E402
from dhakasim.parameters import Parameters        # noqa: E402

#: Two closed minutes and a bit: enough for every file to have rows.
END_TIME = 150
NETWORK = "kakrail_corridor"


def _row(step, vehicle, type_=3, speed=2.0, acc=-1.0, leader=7, lspeed=3.0):
    return [str(step), str(vehicle), str(type_), str(speed), str(acc),
            str(leader), str(lspeed), "0.0"]


class EpisodeTest(unittest.TestCase):
    def test_a_gap_starts_a_new_episode(self):
        rows = [_row(10, 5), _row(11, 5), _row(12, 5), _row(15, 5)]
        eps = run_data.episodes(1, rows)
        self.assertEqual([(e[5], e[6], e[7]) for e in eps],
                         [(10, 12, 3), (15, 15, 1)])

    def test_episodes_are_numbered_by_start_then_vehicle(self):
        rows = [_row(4, 9), _row(3, 8), _row(4, 8), _row(3, 2)]
        eps = run_data.episodes(7, rows)
        self.assertEqual([(e[0], e[1], e[2]) for e in eps],
                         [(7, 1, "2"), (7, 2, "8"), (7, 3, "9")])

    def test_braking_and_closing_speed(self):
        rows = [_row(1, 4, speed=5.0, acc=-0.5, lspeed=2.0),
                _row(2, 4, speed=3.0, acc=-4.0, lspeed=2.5)]
        (ep,) = run_data.episodes(1, rows)
        self.assertEqual(ep[8:], ["4.000", "3.000", "-4.000", "1.750", "3.000"])

    def test_no_leader_means_no_closing_speed(self):
        (ep,) = run_data.episodes(1, [_row(1, 4, leader=-1, lspeed="NaN")])
        self.assertEqual(ep[4], "-1")
        self.assertEqual(ep[11:], ["NaN", "NaN"])


class _Link:
    def __init__(self, id_):
        self._id = id_

    def get_id(self):
        return self._id


class _Processor:
    def __init__(self, link_ids):
        self.link_list = [_Link(i) for i in link_ids]
        self.node_list = []
        self.demand_list = []
        self.vehicle_list = []


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.saved = Parameters.STATS_DIR
        self.dir = tempfile.mkdtemp(prefix="dhakasim_run_data_")
        Parameters.STATS_DIR = self.dir

    def tearDown(self):
        Parameters.STATS_DIR = self.saved
        run_data._active = None
        shutil.rmtree(self.dir, ignore_errors=True)

    def _read(self, name):
        with open(os.path.join(self.dir, "csv", name), newline="") as handle:
            return list(csv.reader(handle))

    def test_each_inherited_file_gets_its_index(self):
        record = run_data.RunRecord(_Processor([4, 9]))
        run_data.record("statistics/link_flow.csv", [120.0, 80.0])
        run_data.record("statistics/route_avg_tt_car.csv", [1.5, float("nan")])
        run_data.record("statistics/agg_total_collision.csv", [0, 2, 300, 40])
        run_data.record("statistics/avg_speed_vehicle.csv", [3.25])
        self.assertEqual(record.results, [
            [1, "link_flow", "link", 4, "120.000"],
            [1, "link_flow", "link", 9, "80.000"],
            [1, "route_avg_tt_car", "route", 0, "1.500"],
            [1, "route_avg_tt_car", "route", 1, "NaN"],
            [1, "agg_total_collision", "total", "collisions", "0.000"],
            [1, "agg_total_collision", "total", "accidents", "2.000"],
            [1, "agg_total_collision", "total", "vehicles_generated", "300.000"],
            [1, "agg_total_collision", "total", "pedestrians_generated", "40.000"],
            [1, "avg_speed_vehicle", "vehicle_type", 0, "3.250"],
        ])

    def test_trajectories_and_the_parity_flow_counter_are_not_results(self):
        record = run_data.RunRecord(_Processor([0]))
        run_data.record("statistics/speeds_2.csv", [1.0, 2.0])
        run_data.record("statistics/x_coords_2.csv", [1.0])
        run_data.record("statistics/flow.csv", [0.0, 0.0])
        self.assertEqual(record.results, [])

    def test_run_ids_count_the_runs_already_in_the_folder(self):
        for _ in range(3):
            run_data.RunRecord(_Processor([0])).finish([])
        runs = self._read("runs.csv")
        self.assertEqual(runs[0], run_data.RUNS_HEADER)
        self.assertEqual([row[0] for row in runs[1:]], ["1", "2", "3"])
        # One header per file, however many runs append to it.
        self.assertEqual(self._read("run_results.csv"), [run_data.RESULTS_HEADER])

    def test_only_closed_minutes_of_network_flow_are_written(self):
        record = run_data.RunRecord(_Processor([]))
        record.minutes_sampled = 2
        record.finish([5, 7, 0])
        flow = [row for row in self._read("run_results.csv")[1:]
                if row[1] == "network_flow"]
        self.assertEqual(flow, [["1", "network_flow", "minute", "1", "5.000"],
                                ["1", "network_flow", "minute", "2", "7.000"]])


class EndToEndTest(unittest.TestCase):
    """Two real runs appended to one folder, as a researcher's would be."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dhakasim_run_data_e2e_")
        for seed in (3, 4):
            subprocess.run(
                [sys.executable, os.path.join(REPO, "run_dhakasim.py"),
                 "--headless", "--network", NETWORK, "--seed", str(seed),
                 "--set", f"SimulationEndTime={END_TIME}",
                 "--set", f"StatsDir={cls.dir}",
                 "--set", "ReportAnimationFrames=0"],
                cwd=REPO, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _read(self, name):
        with open(os.path.join(self.dir, "csv", name), newline="") as handle:
            return list(csv.reader(handle))

    def test_every_row_names_one_of_the_two_runs(self):
        runs = self._read("runs.csv")[1:]
        self.assertEqual([(r[0], r[1], r[2]) for r in runs],
                         [("1", NETWORK, "3"), ("2", NETWORK, "4")])
        for name in ("run_results.csv", "link_minute.csv",
                     "near_crash_episodes.csv"):
            ids = {row[0] for row in self._read(name)[1:]}
            self.assertEqual(ids, {"1", "2"}, name)

    def test_mirrored_results_equal_the_inherited_files(self):
        results = self._read("run_results.csv")[1:]
        for metric in ("link_avg_speed", "link_flow", "avg_speed_vehicle",
                       "agg_avg_fuel"):
            inherited = [[cell for cell in row if cell != ""]
                         for row in self._read(metric + ".csv")]
            for run_id, row in zip(("1", "2"), inherited):
                mirrored = [r[4] for r in results
                            if r[0] == run_id and r[1] == metric]
                self.assertEqual(mirrored, row, f"{metric}, run {run_id}")

    def test_every_link_is_logged_every_closed_minute(self):
        runs = self._read("runs.csv")
        links = int(runs[1][runs[0].index("links")])
        minutes = (END_TIME - 1) // 60
        rows = self._read("link_minute.csv")[1:]
        for run_id in ("1", "2"):
            mine = [r for r in rows if r[0] == run_id]
            self.assertEqual(len(mine), links * minutes)
            self.assertEqual(sorted({int(r[1]) for r in mine}),
                             list(range(1, minutes + 1)))
            for r in mine:
                vehicles, standing = int(r[3]), int(r[6])
                self.assertLessEqual(standing, vehicles)
                if vehicles == 0:
                    self.assertEqual(r[5], "NaN")

    def test_every_accident_row_lands_in_one_episode(self):
        # The log carries no run id, so it is split where the step count
        # falls back: each run starts again from the first steps.
        with open(os.path.join(self.dir, "csv", "accident_log.csv")) as handle:
            log = [line for line in handle if line.strip()]
        steps = [int(line.split(",")[0]) for line in log]
        cut = next(i for i in range(1, len(steps)) if steps[i] < steps[i - 1])
        per_run = {"1": cut, "2": len(log) - cut}
        episodes = self._read("near_crash_episodes.csv")[1:]
        for run_id, rows in per_run.items():
            covered = sum(int(e[7]) for e in episodes if e[0] == run_id)
            self.assertEqual(covered, rows, f"run {run_id}")
            self.assertGreater(rows, 0)

    def test_network_flow_counts_vehicles_past_sensors(self):
        flow = [float(r[4]) for r in self._read("run_results.csv")[1:]
                if r[1] == "network_flow"]
        self.assertEqual(len(flow), 2 * ((END_TIME - 1) // 60))
        self.assertTrue(all(not math.isnan(v) and v >= 0 for v in flow))
        self.assertTrue(any(v > 0 for v in flow),
                        "the per-minute flow is all zeros, as flow.csv is")


if __name__ == "__main__":
    unittest.main()
