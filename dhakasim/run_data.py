"""Machine-readable run records: which run, under what settings, and what
came out of it, minute by minute.

The CSVs inherited from the Java original (``avg_speed_vehicle.csv`` and the
rest) append one bare row per run.  Nothing in a row says which run, which
network or which settings produced it, so a folder that has collected a few
months of runs cannot be told apart row by row -- ``statistics/csv`` held 137
such rows from half a dozen networks, and the per-link files had drifted to
412 rows against the others' 137.  Those files are part of the Java-parity
contract (``hash_run.py`` hashes every one of them), so they stay exactly as
they are.  This module writes four files beside them instead, each with a
header and each keyed by ``run_id``:

``runs.csv``
    One row per run: ``run_id`` and every setting that shapes the run.
``run_results.csv``
    Long format, ``run_id, metric, index_kind, index, value``: every number
    the inherited files carry for this run, plus the network-wide flow per
    minute that ``flow.csv`` was meant to hold.
``link_minute.csv``
    Every link, every simulated minute: vehicles on it, density, mean speed,
    how many stand still, and the flow past its sensors in that minute.  The
    inherited files only ever give a whole-run average, which is no use to a
    model that has to learn how congestion builds.
``near_crash_episodes.csv``
    The accident log grouped into episodes.  The log writes one row per
    vehicle per step for as long as it overlaps another vehicle, so its rows
    are not events: 727,000 rows in the 72-run paper sweep were 47,000
    vehicles.  An episode is one vehicle's unbroken run of rows.

``run_id`` counts the runs in this ``StatsDir``: one more than the rows
already in ``runs.csv``.  It is deliberately not a wall-clock stamp --
``hash_run.py`` hashes every CSV, and a fresh directory has to produce the
same bytes on every run.  A batch that gives each run its own ``StatsDir``
(``experiments/``) gets ``run_id`` 1 in each and keys runs by folder, as it
always has.

Nothing here draws from ``Parameters.random`` or writes to a simulation
object; it only reads.  A run that writes these files is the same run, step
for step, as one that does not.
"""

from __future__ import annotations

import math
import os

from .constants import Constants
from .parameters import Parameters

#: Below this speed (m/s) a vehicle counts as standing still: the keep-clear
#: rule's own threshold, and the one the run screen's sidebar uses, so the
#: data and the window mean the same thing by a queue.
STANDING_SPEED = 0.5

RUNS_HEADER = [
    "run_id", "network", "seed", "end_time_s", "time_of_day",
    "strip_width_m", "footpath_strip_width_m", "max_speed",
    "cf_model", "dlc_model", "generation_model",
    "signal_mode", "signal_change_s", "across_ped", "along_ped",
    "object_mode", "geometry_mode", "keep_clear", "turn_discipline",
    "demand_scale", "demand_override", "demand_offset", "nmv_share",
    "across_ped_per_hour", "along_ped_per_hour",
    "demand_vph", "links", "nodes", "od_pairs",
]
RESULTS_HEADER = ["run_id", "metric", "index_kind", "index", "value"]
MINUTE_HEADER = ["run_id", "minute", "link", "vehicles", "density_per_km",
                 "mean_speed_kmh", "standing", "flow_vph"]
EPISODE_HEADER = ["run_id", "episode_id", "vehicle_id", "type", "leader_type",
                  "start_step", "end_step", "steps", "mean_speed_ms",
                  "min_speed_ms", "hardest_braking_ms2",
                  "mean_closing_speed_ms", "max_closing_speed_ms"]

#: Inherited files that are not per-run results: the sampled trajectories
#: (one file per sampled vehicle) and the parity flow counter, which watches
#: a sensor only the retired synthetic network had and reads zero on every
#: shipped network.  ``network_flow`` takes its place in run_results.csv.
_NOT_RESULTS = ("speeds_", "x_coords_", "y_coords_", "flow.csv")

#: Columns of agg_total_collision.csv, in the order the processor writes them.
_TOTALS = ("collisions", "accidents", "vehicles_generated",
           "pedestrians_generated")

_active: "RunRecord | None" = None


def _csv_dir() -> str:
    return os.path.join(Parameters.STATS_DIR, "csv")


def _num(value) -> str:
    """A fixed three decimals, ``NaN`` for a missing value.  Plain ``%f``
    formatting is stable across Python builds, which the hash needs."""
    if value is None:
        return "NaN"
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return "NaN"
    return f"{value:.3f}"


def _append(name: str, header: list[str], rows: list[list]) -> None:
    """Append ``rows`` to ``name`` under ``<StatsDir>/csv``, writing the header
    first when the file is new.  A header that does not match means the folder
    mixes two versions of this file; say so rather than append columns that
    mean something else."""
    path = os.path.join(_csv_dir(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.isfile(path) or os.path.getsize(path) == 0
    if not fresh:
        with open(path, "r", encoding="utf-8") as handle:
            first = handle.readline().rstrip("\r\n")
        if first != ",".join(header):
            print(f"warning: {name} has a different header; delete "
                  f"{_csv_dir()} before mixing versions of the run records")
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        if fresh:
            handle.write(",".join(header) + "\n")
        for row in rows:
            handle.write(",".join(str(cell) for cell in row) + "\n")


def _next_run_id() -> int:
    path = os.path.join(_csv_dir(), "runs.csv")
    if not os.path.isfile(path):
        return 1
    with open(path, "r", encoding="utf-8") as handle:
        rows = sum(1 for line in handle if line.strip())
    return max(rows, 1)          # the header is the first of those lines


def _accident_log_path() -> str:
    return os.path.join(_csv_dir(), "accident_log.csv")


def _flush_accident_log() -> None:
    # vehicle.py keeps the log open for speed; what it holds in its buffer
    # is not on disk until flushed, and both ends of a run measure the file.
    from . import vehicle
    handle = vehicle._accident_log_handle
    if handle is not None:
        handle.flush()


class RunRecord:
    """What one run writes to the four record files.  Made when a Processor
    is built, fed once a simulated minute, written out with the statistics."""

    def __init__(self, processor) -> None:
        global _active
        self.processor = processor
        self.run_id = _next_run_id()
        self.results: list[list] = []
        self.minutes: list[list] = []
        _flush_accident_log()
        path = _accident_log_path()
        # Where this run's rows will begin in a log that every run appends to.
        self.log_offset = os.path.getsize(path) if os.path.isfile(path) else 0
        self._sensor_seen = [0] * len(processor.link_list)
        self.minutes_sampled = 0
        _active = self

    # -- the inherited files, mirrored -------------------------------------

    def record(self, filename: str, data) -> None:
        name = os.path.basename(filename)
        if name.startswith(_NOT_RESULTS) or not name.endswith(".csv"):
            return
        metric = name[:-len(".csv")]
        values = list(data)
        if metric.startswith("link_"):
            kind = "link"
            index = [link.get_id() for link in self.processor.link_list]
        elif metric.startswith("route_"):
            kind, index = "route", list(range(len(values)))
        elif metric == "agg_total_collision":
            kind, index = "total", list(_TOTALS)
        else:
            kind, index = "vehicle_type", list(range(len(values)))
        for key, value in zip(index, values):
            self.results.append([self.run_id, metric, kind, key, _num(value)])

    # -- once a simulated minute -------------------------------------------

    def sample_minute(self, minute: int) -> None:
        """Every link's state at the end of ``minute`` (0-based)."""
        self.minutes_sampled = minute + 1
        links = self.processor.link_list
        position = {id(link): i for i, link in enumerate(links)}
        speeds: list[list[float]] = [[] for _ in links]
        walking = Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE
        for vehicle in self.processor.vehicle_list:
            # A vehicle crossing a junction belongs to no link; pedestrians
            # walking along the road are carried as a vehicle type but are
            # not traffic, as on the sidebar.
            if vehicle.get_type() == walking or vehicle.is_in_intersection():
                continue
            i = position.get(id(vehicle.get_link()))
            if i is not None:
                speeds[i].append(vehicle.get_speed())
        for i, link in enumerate(links):
            segments = link.get_number_of_segments()
            length_km = sum(link.get_segment(s).get_length()
                            for s in range(segments)) / 1000.0
            passed = sum(link.get_segment(s).get_sensor_vehicle_count()
                         for s in range(segments))
            # Every segment has a sensor, so a vehicle that drives the whole
            # link is counted once per segment; the link's flow divides by
            # the segment count, exactly as link_flow.csv does for the run.
            flow = (passed - self._sensor_seen[i]) / max(segments, 1) * 60.0
            self._sensor_seen[i] = passed
            here = speeds[i]
            self.minutes.append([
                self.run_id, minute + 1, link.get_id(), len(here),
                _num(len(here) / length_km if length_km > 0 else None),
                _num(sum(here) / len(here) * 3.6 if here else None),
                sum(1 for s in here if s < STANDING_SPEED),
                _num(flow),
            ])

    # -- the end of the run ------------------------------------------------

    def finish(self, flow_series) -> None:
        global _active
        # The step at the end time itself is never simulated, so the run's
        # last minute never closes; only the minutes that did are written,
        # or every series would end on a zero that is not a measurement.
        sampled = list(flow_series or [])[:self.minutes_sampled]
        for minute, count in enumerate(sampled):
            # Vehicles past any sensor in that minute, network-wide: what the
            # report's flow chart shows and what flow.csv cannot.
            self.results.append([self.run_id, "network_flow", "minute",
                                 minute + 1, _num(count)])
        _append("runs.csv", RUNS_HEADER, [self._settings()])
        _append("run_results.csv", RESULTS_HEADER, self.results)
        _append("link_minute.csv", MINUTE_HEADER, self.minutes)
        _flush_accident_log()
        _append("near_crash_episodes.csv", EPISODE_HEADER,
                episodes(self.run_id, self._log_rows()))
        if _active is self:
            _active = None

    def _settings(self) -> list:
        p = self.processor

        def name(model):
            return getattr(model, "name", model)

        def flag(value):
            return "on" if value else "off"

        return [
            self.run_id, Parameters.NETWORK_DIR or "default", Parameters.seed,
            Parameters.simulation_end_time, Parameters.TIME_OF_DAY,
            Parameters.strip_width, Parameters.footpath_strip_width,
            Parameters.maximum_speed, name(Parameters.car_following_model),
            name(Parameters.lane_changing_model),
            name(Parameters.vehicle_generation_rate), Parameters.SIGNAL_MODE,
            Parameters.SIGNAL_CHANGE_DURATION,
            flag(Parameters.across_pedestrian_mode),
            flag(Parameters.along_pedestrian_mode),
            flag(Parameters.OBJECT_MODE), flag(Parameters.GEOMETRY_MODE),
            flag(Parameters.KEEP_CLEAR_MODE),
            flag(Parameters.TURN_DISCIPLINE_MODE), Parameters.DEMAND_SCALE,
            Parameters.DEMAND_OVERRIDE, Parameters.DEMAND_OFFSET,
            Parameters.NMV_SHARE, Parameters.ACROSS_PEDESTRIAN_PER_HOUR,
            Parameters.ALONG_PEDESTRIAN_PER_HOUR,
            # Arrivals asked for, all OD pairs, after the traffic level: an
            # arrival that finds no room at its entry is dropped, not queued,
            # so vehicles generated against this is how much demand a
            # congested network actually took in.
            _num(sum(d.get_demand() for d in p.demand_list)
                 * Parameters.DEMAND_SCALE),
            len(p.link_list), len(p.node_list), len(p.demand_list),
        ]

    def _log_rows(self) -> list[list[str]]:
        path = _accident_log_path()
        if not os.path.isfile(path):
            return []
        with open(path, "rb") as handle:
            handle.seek(self.log_offset)
            text = handle.read().decode("utf-8", "replace")
        rows = []
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.split(",")]
            if len(cells) >= 8:
                rows.append(cells[:8])
        return rows


def _float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return math.nan


def episodes(run_id: int, rows: list[list[str]]) -> list[list]:
    """Group accident-log rows into episodes.

    A row is ``step, vehicle, type, speed, acceleration, leader type, leader
    speed, leader acceleration``.  One vehicle's rows on consecutive steps are
    one episode; a gap of a step or more starts a new one.  The leader type is
    the one at the start, since that is the vehicle the episode began behind.
    Closing speed is the follower's speed less the leader's, over the steps
    that had a leader; with none it is ``NaN``.
    """
    open_by_vehicle: dict[str, dict] = {}
    done: list[dict] = []
    for step_text, vehicle, type_, speed, acc, leader, lspeed, _ in rows:
        step = int(step_text)
        ep = open_by_vehicle.get(vehicle)
        if ep is None or step - ep["end"] > 1:
            if ep is not None:
                done.append(ep)
            ep = {"vehicle": vehicle, "type": type_, "leader": leader,
                  "start": step, "end": step, "speeds": [], "accs": [],
                  "closing": []}
            open_by_vehicle[vehicle] = ep
        ep["end"] = step
        ep["speeds"].append(_float(speed))
        ep["accs"].append(_float(acc))
        if leader != "-1":
            ep["closing"].append(_float(speed) - _float(lspeed))
    done.extend(open_by_vehicle.values())
    done.sort(key=lambda e: (e["start"], int(e["vehicle"])))

    out = []
    for number, ep in enumerate(done, start=1):
        speeds = [s for s in ep["speeds"] if not math.isnan(s)]
        accs = [a for a in ep["accs"] if not math.isnan(a)]
        closing = [c for c in ep["closing"] if not math.isnan(c)]
        out.append([
            run_id, number, ep["vehicle"], ep["type"], ep["leader"],
            ep["start"], ep["end"], ep["end"] - ep["start"] + 1,
            _num(sum(speeds) / len(speeds) if speeds else None),
            _num(min(speeds) if speeds else None),
            _num(min(accs) if accs else None),
            _num(sum(closing) / len(closing) if closing else None),
            _num(max(closing) if closing else None),
        ])
    return out


def record(filename: str, data) -> None:
    """Called for every row the processor writes to an inherited CSV."""
    if _active is not None:
        _active.record(filename, data)
