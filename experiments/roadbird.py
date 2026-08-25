#!/usr/bin/env python3
"""Run the lane / non-lane comparison as a batch of seeded simulations.

Sweeps the factors the RoadBird study varies -- strip width (lane against
non-lane), demand, vehicle mix and pedestrians -- over a range of seeds, then
collects the per-link and per-type CSVs into one tidy table.

Each run is a separate process with its own output directory.  Both matter:
``Parameters`` is process-wide mutable state, so looping in one process would
leak settings between runs, and every CSV is appended to rather than
truncated, so runs sharing a directory produce rows that cannot be told apart.

Run it from the project root::

    python experiments/roadbird.py --dry-run          # list the runs
    python experiments/roadbird.py --seeds 3 --jobs 4
    python experiments/roadbird.py --end-time 300     # a quick shakedown

Interrupted sweeps resume: a run whose output directory holds a ``done`` marker
is skipped, so re-running the same command fills in what is missing.

Outputs, under ``--out`` (default ``experiments/results``)::

    runs/<run id>/csv/*.csv   each run's own statistics, untouched
    results.csv               one row per scenario x seed x metric x index
    summary.csv               results.csv averaged over links/types and seeds

No third-party dependencies, in keeping with the rest of the project -- which
is why the summary is arithmetic here rather than a dataframe groupby.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import itertools
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# the factor grid
# --------------------------------------------------------------------------

# Strip width decides the whole lane question: a strip wider than the widest
# vehicle (2.46 m) holds one vehicle, which is a lane; a narrow one lets
# vehicles straddle several, which is not.
LANE_MODES = {"non_lane": 0.5, "lane": 2.5}

# Vehicles/hour per OD pair.
DEMANDS = {"low": 100, "medium": 400, "high": 800}

# Speed-class split, slow/medium/fast.  Heterogeneous is Dhaka's, dominated by
# rickshaws; homogeneous is the motorised mix of a developed city.  These reach
# the simulator only with VehicleMixOverride Off -- see the README.
MIXES = {"heterogeneous": (55, 40, 5), "homogeneous": (9, 75, 16)}

PEDESTRIANS = {"peds": True, "no_peds": False}

# metric file -> what its columns are indexed by
METRICS = {
    "link_avg_speed": "link",
    "link_avg_waiting": "link",
    "link_flow": "link",
    "avg_speed_vehicle": "vehicle_type",
    "route_avg_tt_car": "route",
    "route_avg_tt_motorbike": "route",
}

FIELDS = ["run_id", "network", "lane_mode", "strip_width", "demand", "mix",
          "pedestrians", "seed", "metric", "index_kind", "index", "value"]


class Scenario:
    """One cell of the grid, plus the seed that makes it a run."""

    __slots__ = ("network", "lane_mode", "demand", "mix", "pedestrians", "seed")

    def __init__(self, network, lane_mode, demand, mix, pedestrians, seed):
        self.network = network
        self.lane_mode = lane_mode
        self.demand = demand
        self.mix = mix
        self.pedestrians = pedestrians
        self.seed = seed

    @property
    def run_id(self) -> str:
        return (f"{self.network}__{self.lane_mode}__{self.demand}"
                f"__{self.mix}__{self.pedestrians}__seed{self.seed:03d}")

    def as_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "network": self.network,
            "lane_mode": self.lane_mode,
            "strip_width": LANE_MODES[self.lane_mode],
            "demand": DEMANDS[self.demand],
            "mix": self.mix,
            "pedestrians": self.pedestrians,
            "seed": self.seed,
        }

    def settings(self, stats_dir: str, end_time: int, cf_model: int,
                 dlc_model: int, demand_offset: int) -> list:
        slow, medium, fast = MIXES[self.mix]
        peds = "On" if PEDESTRIANS[self.pedestrians] else "Off"
        # Order matters. A DLC_model line also assigns the car-following model
        # and the slow-vehicle percentage, and a CF_model line also assigns the
        # slow-vehicle percentage -- the parser reproduces a fall-through in the
        # Java original. So the mix must be set after both, or it is silently
        # overwritten with the model index.
        return [
            ("StatsDir", stats_dir),
            ("SimulationEndTime", end_time),
            ("ReportAnimationFrames", 0),   # a sweep does not want 3.5 MB a run
            ("TraceMode", "Off"),
            ("GUIMode", "Off"),
            ("DLC_model", dlc_model),
            ("CF_model", cf_model),
            ("VehicleMixOverride", "Off"),
            ("SlowVehicle", slow),
            ("MediumVehicle", medium),
            ("FastVehicle", fast),
            ("StripWidth", LANE_MODES[self.lane_mode]),
            ("DemandOverride", DEMANDS[self.demand]),
            ("DemandOffset", demand_offset),
            ("AcrossPedestrianMode", peds),
            ("AlongPedestrianMode", peds),
        ]


def build_grid(networks, demands, mixes, lane_modes, pedestrians, seeds):
    combos = itertools.product(networks, lane_modes, demands, mixes,
                               pedestrians, seeds)
    return [Scenario(*combo) for combo in combos]


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

def run_one(scenario, runs_dir, end_time, cf_model, dlc_model, demand_offset,
            timeout):
    """Run one simulation to completion.  Returns (scenario, ok, note)."""
    stats_dir = os.path.join(runs_dir, scenario.run_id)
    marker = os.path.join(stats_dir, "done")
    if os.path.exists(marker):
        return scenario, True, "skipped (already done)"

    argv = [sys.executable, os.path.join(ROOT, "run_dhakasim.py"), "--headless",
            "--network", scenario.network, "--seed", str(scenario.seed)]
    for name, value in scenario.settings(stats_dir, end_time, cf_model,
                                         dlc_model, demand_offset):
        argv += ["--set", f"{name}={value}"]

    os.makedirs(stats_dir, exist_ok=True)
    started = time.time()
    try:
        completed = subprocess.run(argv, cwd=ROOT, timeout=timeout,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
    except subprocess.TimeoutExpired:
        return scenario, False, f"timed out after {timeout}s"
    elapsed = time.time() - started

    with open(os.path.join(stats_dir, "run.log"), "w", encoding="utf-8") as log:
        log.write(" ".join(argv) + "\n\n" + (completed.stdout or ""))
    if completed.returncode != 0:
        return scenario, False, f"exit {completed.returncode}, see run.log"

    # Written last, so an interrupted run is never mistaken for a finished one.
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"{elapsed:.1f}\n")
    return scenario, True, f"{elapsed:.0f}s"


# --------------------------------------------------------------------------
# collecting
# --------------------------------------------------------------------------

def read_metric(path):
    """Last row of one of the simulator's CSVs, as floats.

    Every run appends one row, and each run here has its own directory, so the
    last row is this run's.  Empty cells come from the trailing comma the
    simulator writes; NaN is a real value it emits for an undefined average.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        rows = [line for line in f.read().splitlines() if line.strip()]
    if not rows:
        return None
    values = []
    for cell in rows[-1].split(","):
        cell = cell.strip()
        if not cell:
            continue
        try:
            values.append(float(cell))
        except ValueError:
            values.append(float("nan"))
    return values


def collect(scenarios, runs_dir, out_dir):
    rows = []
    for scenario in scenarios:
        csv_dir = os.path.join(runs_dir, scenario.run_id, "csv")
        base = scenario.as_row()
        for metric, index_kind in METRICS.items():
            values = read_metric(os.path.join(csv_dir, f"{metric}.csv"))
            if values is None:
                continue
            for index, value in enumerate(values):
                row = dict(base)
                row.update({"metric": metric, "index_kind": index_kind,
                            "index": index, "value": value})
                rows.append(row)

    results = os.path.join(out_dir, "results.csv")
    with open(results, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows, results


def summarise(rows, out_dir):
    """Average each metric over links/types and over seeds.

    NaNs are dropped rather than propagated: a route nobody completed leaves a
    NaN, and letting one poison a scenario's mean would discard the runs that
    did produce a number.  The count of contributing values is kept so a mean
    resting on very little is visible as such.
    """
    groups = {}
    for row in rows:
        key = (row["network"], row["lane_mode"], row["demand"], row["mix"],
               row["pedestrians"], row["metric"])
        groups.setdefault(key, []).append(row["value"])

    out = os.path.join(out_dir, "summary.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["network", "lane_mode", "demand", "mix", "pedestrians",
                         "metric", "mean", "n_values", "n_dropped_nan"])
        for key in sorted(groups, key=lambda k: tuple(str(x) for x in k)):
            values = groups[key]
            finite = [v for v in values if v == v and abs(v) != float("inf")]
            mean = sum(finite) / len(finite) if finite else float("nan")
            writer.writerow(list(key) + [f"{mean:.4f}", len(finite),
                                         len(values) - len(finite)])
    return out


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=os.path.join("experiments", "results"))
    parser.add_argument("--networks", nargs="+", default=["demo_backup"])
    parser.add_argument("--seeds", type=int, default=10,
                        help="seeds 1..N (default 10)")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--end-time", type=int, default=1800,
                        help="simulated seconds per run (default 1800)")
    parser.add_argument("--cf-model", type=int, default=0, help="0 = hybrid")
    parser.add_argument("--dlc-model", type=int, default=1, help="1 = GHR")
    parser.add_argument("--demand-offset", type=int, default=0,
                        help="added to every OD row; the simulator's own "
                             "default is 30, this defaults to 0 so the "
                             "requested rate is the rate")
    parser.add_argument("--timeout", type=int, default=3600,
                        help="seconds before a single run is abandoned")
    parser.add_argument("--demands", nargs="+", default=list(DEMANDS),
                        choices=list(DEMANDS))
    parser.add_argument("--rates", nargs=3, type=int,
                        metavar=("LOW", "MEDIUM", "HIGH"),
                        help="vehicles/hour behind low/medium/high. Defaults to "
                             "100 400 800, the paper's Dhaka rates; it uses "
                             "500 1000 2000 for Miami and Riyadh, so those "
                             "cities want a separate invocation")
    parser.add_argument("--mixes", nargs="+", default=list(MIXES),
                        choices=list(MIXES))
    parser.add_argument("--lane-modes", nargs="+", default=list(LANE_MODES),
                        choices=list(LANE_MODES))
    parser.add_argument("--pedestrians", nargs="+", default=list(PEDESTRIANS),
                        choices=list(PEDESTRIANS))
    parser.add_argument("--dry-run", action="store_true",
                        help="list the runs and stop")
    parser.add_argument("--collect-only", action="store_true",
                        help="rebuild results.csv from runs already on disk")
    args = parser.parse_args(argv)

    if args.rates:
        DEMANDS.update(zip(("low", "medium", "high"), args.rates))

    scenarios = build_grid(args.networks, args.demands, args.mixes,
                           args.lane_modes, args.pedestrians,
                           range(1, args.seeds + 1))
    out_dir = os.path.join(ROOT, args.out) if not os.path.isabs(args.out) else args.out
    runs_dir = os.path.join(out_dir, "runs")
    os.makedirs(runs_dir, exist_ok=True)

    print(f"{len(scenarios)} runs "
          f"({len(scenarios) // max(args.seeds, 1)} scenarios x {args.seeds} seeds), "
          f"{args.end_time}s each, {args.jobs} at a time")
    if args.dry_run:
        for scenario in scenarios:
            print("  " + scenario.run_id)
        return 0

    if not args.collect_only:
        done = failed = 0
        started = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_one, s, runs_dir, args.end_time,
                                   args.cf_model, args.dlc_model,
                                   args.demand_offset, args.timeout)
                       for s in scenarios]
            for future in concurrent.futures.as_completed(futures):
                scenario, ok, note = future.result()
                done += 1
                if not ok:
                    failed += 1
                print(f"[{done}/{len(scenarios)}] "
                      f"{'ok  ' if ok else 'FAIL'} {scenario.run_id}  {note}",
                      flush=True)
        print(f"\n{done - failed} succeeded, {failed} failed, "
              f"{time.time() - started:.0f}s wall clock")

    rows, results = collect(scenarios, runs_dir, out_dir)
    summary = summarise(rows, out_dir)
    print(f"{len(rows)} rows -> {os.path.relpath(results, ROOT)}")
    print(f"           -> {os.path.relpath(summary, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
