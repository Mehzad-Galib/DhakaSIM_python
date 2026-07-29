# DhakaSim (Python)

DhakaSim is an event-based microscopic traffic simulator for the non-lane-based,
heterogeneous traffic typical of cities like Dhaka. Vehicles occupy multiple
narrow **strips** rather than single lanes, and the model includes the
side-friction elements that dominate congestion there: parked cars, rickshaws
and CNGs encroaching on the carriageway, standing pedestrians, pedestrians
walking along the road, and heavy road-crossing pedestrian flow.

Self-contained Python implementation — no third-party dependencies, the GUI uses
`tkinter` from the standard library. Python 3.10 or newer.

## Running

The simulator reads `input/` and writes `statistics/` relative to the current
directory, so run it from this folder:

```bash
python run_dhakasim.py
```

`GUIMode` in `input/parameter.txt` decides whether the animated window opens.
Two flags override it for a single run:

```bash
python run_dhakasim.py --headless
```

```bash
python run_dhakasim.py --gui
```

In the GUI the option form lets you change the seed, end time, speed and road
geometry before starting. Once running: drag to pan, mouse wheel or the
right-hand slider to zoom, the bottom slider shows progress.

## Layout

```
input/            parameter.txt plus one sub-folder per intersection/network
statistics/       HTML reports at the root; raw CSVs in statistics/csv/ (appended)
trace.txt         created by the GUI; one frame per simulation step
dhakasim/         the simulator package
run_dhakasim.py   launcher
run_sim.py        route/demand generator (only needed if the network changes)
tests/            regression tests for the numeric layer
```

### Choosing an intersection

Each intersection lives in its own folder under `input/`, holding that
network's roads, routes, demand and vehicle mix:

```
input/parameter.txt          shared settings
input/kakrail_corridor/      Kakrail Church + Kakrail Mosque (2 junctions)
input/bijoy_sarani/          Bijoy Sarani
input/khamarbari/            Khamarbari
input/banani_23/             Banani 23 Super Market
input/banani_27/             Banani 27 Kacha Bazar
input/demo_backup/           the original demonstration network
```

Pick one from the **Intersection** dropdown on the GUI's start screen, or set
`Network <folder>` in `parameter.txt`, or pass `--network <folder>`:

```bash
python run_dhakasim.py --headless --network bijoy_sarani
```

To add your own network, create a new folder with the same files.

### Choosing the time of day

The surveys cover a full 24 hours, so every network carries hourly demand and a
hourly vehicle mix. Choose the hour from the **Time of Day** dropdown, or set
`TimeOfDay <0-23>` in `parameter.txt`, or pass `--hour`:

```bash
python run_dhakasim.py --headless --network kakrail_corridor --hour 8
```

`TimeOfDay -1` (the default) uses the network's busiest hour. Traffic varies
enormously across the day — the Kakrail corridor carries about 1,500 veh/h at
04:00 against 6,300 veh/h at its 13:00 peak — and the vehicle mix shifts too
(more rickshaws by day, proportionally more trucks overnight).

### Input files

| File | Contents |
| --- | --- |
| `parameter.txt` | every simulation setting; see below (stays in `input/`) |
| `link.txt` | links, then one line per segment: `id startX startY endX endY width` (metres) |
| `node.txt` | `id centerX centerY` followed by the ids of the links meeting there |
| `path.txt` | `source dest` followed by the link ids along the route |
| `demand.txt` | `source dest vehiclesPerHour` per route |
| `demand_all.txt` | the unthinned demand `run_sim.py` produced before `DemandType` filtering |
| `demand-low/medium/high.txt` | alternative demand levels; copy one over `demand.txt` to use it |
| `node_names.txt` | optional `id name` per line; friendly node labels for the GUI and report |
| `vehicle_mix.txt` | optional `typeIndex percentage` per line; the survey-measured vehicle mix for that intersection |
| `demand_by_hour.txt` | `hour source dest vehiclesPerHour` per line; the 24-hour demand profile |
| `vehicle_mix_by_hour.txt` | `hour typeIndex percentage` per line; the vehicle mix for each hour |

`demand.txt` and `path.txt` must agree — every demand row needs at least one
matching route. `run_sim.py` regenerates both from `link.txt` + `node.txt`.

### Output files

Each run writes a self-contained HTML report to `statistics/`, named
`report_<YYYYMMDD_HHMMSS>.html`, summarising the run with metric cards, a
configuration table, a per-type results table, colour-coded bar charts, a
vehicle-colour legend and a glossary. The raw numeric CSVs below are written
into `statistics/csv/` (paths in the table are relative to that folder), all
**appended**, so repeated runs accumulate one row per run:

| File | Contents |
| --- | --- |
| `avg_speed_vehicle.csv` | mean speed per vehicle type (km/h) |
| `waiting_percentage_vehicle.csv` | share of travel time spent stopped, per type |
| `generated_vehicles.csv` | vehicles generated per type |
| `agg_total_trip_complete.csv` | completed trips per type |
| `agg_avg_tt.csv` | mean trip time per type (minutes) |
| `agg_avg_fuel.csv` | mean fuel consumption per type (litres) |
| `agg_avg_collision.csv`, `agg_avg_accident.csv` | collisions / accidents per type |
| `agg_total_collision.csv` | `collisions, accidents, vehiclesGenerated, pedestriansGenerated` |
| `avg_tt<i>.csv`, `fuel<i>.csv`, `trip_complete<i>.csv`, `collisions<i>.csv` | the same, per route `i` (first `NoOfRoutes` routes) |
| `flow.csv` | vehicles past the sensor per minute |
| `accident_log.csv` | `simStep, vehicleId, type, speed, acceleration, leaderType, leaderSpeed, leaderAcc` |

Every per-type file has 13 columns indexed by vehicle type: 0 bicycle,
1 rickshaw, 2 van/cart, 3 motorbike, 4–6 car, 7 CNG, 8–9 bus, 10–11 truck,
12 along-road pedestrian. Empty cells appear as `NaN` — a type with no completed
trips divides by zero. Per-type geometry and performance live in
`dhakasim/utilities.py` (`_CAR_WIDTHS`, `_CAR_LENGTHS`, `_CAR_SPEEDS`,
`_CAR_ACCELERATIONS`).

The run also prints six summary metrics to stdout: overall, motorized and
non-motorized mean speed (km/h) and waiting time (seconds).

## Parameters

`input/parameter.txt` is one `Name Value` pair per line. Unrecognised names are
ignored, so you can leave notes in the file. The settings you are most likely to
touch:

| Name | Meaning |
| --- | --- |
| `SimulationEndTime` | simulated seconds to run (1800 = 30 min) |
| `GUIMode` | `On` opens the animation, `Off` runs headless |
| `SimulationSpeed` | GUI frame delay in milliseconds |
| `CF_model` | car-following model, 0–12 (see below) |
| `DLC_model` | lane-changing model: 0 naive, 1 GHR, 2 Gipps, 3 MOBIL |
| `StripWidth`, `FootpathStripWidth` | strip granularity in metres |
| `MaximumSpeed` | network speed limit (see the unit caveat below) |
| `DemandType` | 0 low, 1 medium, 2 high — used by `run_sim.py` |
| `LowRate`, `MediumRate`, `HighRate` | vehicles/hour for each `DemandType` |
| `ObjectMode` | `On` generates parked cars/rickshaws/CNGs and standing pedestrians |
| `AcrossPedestrianMode` | `On` generates road-crossing pedestrians |
| `AlongPedestrianMode` | `On` generates pedestrians walking along the road |
| `AcrossPedestrianPerHour`, `AlongPedestrianPerHour` | pedestrian arrival rates |
| `SignalChangeDuration` | seconds between signal phase changes |
| `NoOfRoutes` | how many routes get their own per-route CSV files |
| `ReportAnimationFrames` | frames captured for the report's embedded SVG animation (`0` disables it) |
| `Network` | which `input/` sub-folder to simulate (e.g. `bijoy_sarani`); the GUI dropdown and `--network` override it |
| `TimeOfDay` | hour of the surveyed day to simulate, `0`-`23`; `-1` uses the busiest hour. The GUI dropdown and `--hour` override it |
| `TraceMode` | `On` replays a recorded `trace.txt` instead of simulating |
| `DebugMode` | `On` writes per-vehicle traces into `debug/` |

`CF_model`: 0 hybrid (default), 1 naive, 2 Gipps, 3 Krauss, 4 GFM, 5 IDM,
6 RVF, 7 VFIAC, 8 OVCM, 9 KFTM, 10 HDM, 11 SBM, 12 strip-based weighted model.

## Behaviour worth knowing about

Long-standing quirks of the simulator. They are intentional here — each one is
commented at the point in the code where it happens — but they will surprise you
otherwise.

- **`RandomSeed` in the file is ignored.** The simulator picks a random seed in
  `[0, 101)` at startup, so runs are not reproducible. The RNG underneath *is*
  seed-faithful, so for repeatable runs change the `RandomSeed` branch in
  `dhakasim/utilities.py:initialize` to use the file value. The GUI's seed field
  does apply the value you type.
- **Parameter parsing falls through.** A `DLC_model` line also sets the
  car-following model from the same value and then assigns `SlowVehicle`; a
  `CF_model` line also assigns `SlowVehicle`. Because `parameter.txt` lists
  `DLC_model` before `CF_model`, the `CF_model` line wins — keep that order.
  `SlowVehicle`/`MediumVehicle`/`FastVehicle` are overwritten with 25/25/50 at
  the end of parsing regardless of what the file says.
- **`MaximumSpeed` means different things in the two modes.** Headless reads it
  as m/s; the GUI option form reads it as km/h and divides by 3.6. The shipped
  `100.0` therefore caps speeds at 100 m/s headless and 27.8 m/s in the GUI.
- **`Probability of Accident` is transformed twice in the GUI.** Startup turns
  `EncounterPerAccident 0.01` into 10000; the option form shows that 10000 and
  transforms it again, giving −9899. Nothing currently reads the result.
- **Accidents never reach the counters.** The accident check consumes the
  pedestrian as a side effect of logging, so `agg_total_collision.csv` reports 0
  collisions and 0 accidents even while `Accident: SimStep: …` lines print to
  stdout. Count those lines if you need the total.
- **`statistics/csv/` is appended, never truncated.** Delete that folder between
  experiments, or each CSV grows a row per run. (HTML reports at the
  `statistics/` root are per-run, time-stamped files and are not overwritten.)
- **The GUI writes `trace.txt` every frame**, which grows quickly.

## Performance

Roughly 7–8 minutes for the shipped 1800-step Dhaka network on a desktop CPU.
Runtime grows with the number of concurrent vehicles, so it is worse than linear
in `SimulationEndTime` — lower that while iterating. The GUI keeps up at the
default `SimulationSpeed 1`.

## Tests

```bash
python tests/test_javacompat.py
```

`dhakasim/javacompat.py` reproduces the arithmetic of the original
implementation, because Python's defaults differ from it in ways that change
simulation results: `min`/`max` must propagate NaN (Gipps braking produces NaN
routinely, and mishandling it shifts every speed statistic), rounding is half-up
rather than half-to-even, int casts truncate towards zero, division by zero and
`sqrt` of a negative must yield `Infinity`/`NaN` instead of raising, `%` on
negatives keeps the dividend's sign, and `"%.3f"` rounds the shortest
round-tripping decimal half-up. The test file pins all of this to reference
values — if it fails, the numerics have drifted.

## Provenance

Translated from the Java implementation of DhakaSim, one module per original
class, and validated against it: with a matched seed both produce byte-identical
`statistics/csv/*.csv` and identical printed metrics, across all 13 car-following
models, all 4 lane-changing models, the pedestrian and roadside-object paths
including the per-step accident log, the route/demand generator output, and the
drawing geometry written to `trace.txt`. Unseeded, repeated runs agree within
statistical noise.

## Further reading

1. M. M. Mushfiq et al., "“To Lane or Not to Lane?”—Comparing On-Road
   Experiences in Developing and Developed Countries Using a New Simulator
   RoadBird," *IEEE Transactions on Intelligent Transportation Systems*,
   vol. 25, no. 8, pp. 8486–8498, Aug. 2024,
   [doi:10.1109/TITS.2024.3406731](https://ieeexplore.ieee.org/abstract/document/10552416).
2. A. M. S. Rumi et al., "A Tale of Side Friction Elements in Dhaka City: From
   Modeling Real Cases to Revealing Impacts Through Event-Based Simulation,"
   *IEEE Access*, vol. 13, pp. 37344–37360, 2025,
   [doi:10.1109/ACCESS.2025.3544511](https://ieeexplore.ieee.org/abstract/document/10898009).
