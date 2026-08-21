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

`--seed <n>` pins the random number generator so a run can be repeated, and
`--set Name=Value` overrides any setting `parameter.txt` understands, repeatably
— together these let a batch of runs vary one factor at a time without editing
the file between them:

```bash
python run_dhakasim.py --headless --seed 4 --set StripWidth=2.5 --set CF_model=2
```

In the GUI the option form lets you change the seed, end time, speed and road
geometry before starting. Once running: drag to pan, mouse wheel or the
right-hand slider to zoom, the bottom slider shows progress.

**◀ New simulation** (top left) takes you back to the setup form at any time, so
one session can run as many scenarios as you like without restarting the
program. It asks for confirmation if the current run has not finished. Every
setting goes back to what `parameter.txt` loaded, so the form opens on the same
values each time rather than on the previous run's leftovers, and each run starts
with an empty road. **Open report** next to it reopens the newest HTML report.

### The 3D view

Next to those is a button that swaps the plan view for a **3D perspective view**
of the same run, in the manner of VISSIM's 3D mode: the road laid out in
perspective with modelled vehicles driving on it, lit and casting shadows. Each
of the 13 vehicle types has its own model — a bus with a window band and a roof
rack, a truck with a cab and a cargo body, a CNG with its canopy, a rickshaw with
its hood and its driver, a rider on the motorbikes and bicycles — and every one
keeps the colour its type has in the 2D view and the report legend. Pedestrians,
parked cars and the other roadside objects are modelled too.

| | |
| --- | --- |
| orbit | drag |
| pan | right-drag, or Shift+drag |
| zoom | mouse wheel, or the right-hand slider |
| reset the camera | double-click |
| switch views | the toolbar button, or the `V` key |

`Render3D On` in `parameter.txt` (or the **3D View** radio on the start screen)
opens straight into it. The view can be switched at any point in a run, without
disturbing it: it is only a matter of how each frame is drawn, so the results,
the report and `trace.txt` are identical either way. A recorded run replayed
with `TraceMode On` renders in 3D as well — the trace stores no vehicle types,
so the model is inferred from each footprint, which recovers all 13 correctly.

The renderer is `dhakasim/render3d.py`, drawing with plain `tkinter` polygons:
no OpenGL, no GPU and, in keeping with the rest of the simulator, no third-party
package. It costs roughly 3–4x a 2D frame at typical densities, so a busy
network animates a little slower than in plan view.

## Layout

```
input/            parameter.txt plus one sub-folder per intersection/network
statistics/       HTML reports at the root; raw CSVs in statistics/csv/ (appended)
trace.txt         created by the GUI; one frame per simulation step
dhakasim/         the simulator package
run_dhakasim.py   launcher
run_sim.py        route/demand generator (only needed if the network geometry changes)
tests/            regression tests for the numeric layer
```

Inside `dhakasim/`, three modules render the same picture through the same
drawing calls, so they cannot drift apart: `gui.py` draws the 2D window,
`render3d.py` the 3D one, and `visualize.py` the report's SVG.

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
matching route. `run_sim.py` regenerates both from `link.txt` + `node.txt`,
writing into the selected network's folder:

```bash
python run_sim.py --network demo_backup
```

The survey networks derive `demand.txt` from measured traffic counts, so it is
input data the generator cannot recompute; those are refused unless you pass
`--force`. Use `--paths-only` to regenerate the routes after a geometry edit and
leave the counts alone:

```bash
python run_sim.py --paths-only
```

### Real geometry: medians and roundabouts

`GeometryMode On` reads the selected network's `geometry.txt`, one directive per
line (`#` starts a comment):

```
median 2 2.0        # a 2 m central reservation on link 2
roundabout 6 28.8   # node 6 is a roundabout with a 28.8 m radius
```

A **median** consumes `ceil(width / StripWidth)` strips at the centre of the
carriageway and pushes the per-direction limits outward, so it takes road space
away from traffic rather than being a free dividing line. A **roundabout**
replaces the junction's signal phases with give-way priority — circulating
traffic keeps moving and an approach enters only when nothing is about to cross
it — bends every turn path into a clockwise arc around the island, and sets the
circulating speed to `1.7 × sqrt(radius)` m/s.

`khamarbari` and `kakrail_corridor` ship roundabouts; `khamarbari` and
`banani_27` ship medians. Left off, none of this runs and the simulator
reproduces the Java reference exactly.

## Output files

Each run writes a self-contained HTML report to `statistics/`, named
`report_<YYYYMMDD_HHMMSS>.html`, summarising the run with two animations of the
run -- the same captured frames in plan view and through the 3D camera -- metric
cards, a configuration table, a per-type results table, colour-coded bar charts,
a vehicle-colour legend and a glossary. The raw numeric CSVs below are written
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
| `link_avg_speed.csv` | mean speed on each link (km/h); one column per link |
| `link_avg_waiting.csv` | mean waiting time on each link (seconds per vehicle leaving it) |
| `link_flow.csv` | flow rate on each link (vehicles/hour) |
| `route_avg_tt_car.csv`, `route_avg_tt_motorbike.csv` | mean trip time per route (minutes) for cars and for motorbikes, over the first `NoOfRoutes` routes |
| `accident_log.csv` | `simStep, vehicleId, type, speed, acceleration, leaderType, leaderSpeed, leaderAcc` |

The three `link_*.csv` files are indexed by link, not by vehicle type. Cars span
three type indices, so `route_avg_tt_car.csv` pools their trip times and
completion counts rather than averaging the three `avg_tt<i>.csv` columns, which
would weight a type that completed two trips the same as one that completed two
hundred.

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
| `Render3D` | `On` opens the GUI in the 3D perspective view instead of the 2D plan view; drawing only, and switchable during a run |
| `SimulationSpeed` | GUI frame delay in milliseconds |
| `CF_model` | car-following model, 0–12 (see below) |
| `DLC_model` | lane-changing model: 0 naive, 1 GHR, 2 Gipps, 3 MOBIL |
| `StripWidth`, `FootpathStripWidth` | strip granularity in metres |
| `MaximumSpeed` | network speed limit in **km/h** (shipped: 100) |
| `DemandType` | 0 low, 1 medium, 2 high — used by `run_sim.py` |
| `LowRate`, `MediumRate`, `HighRate` | vehicles/hour for each `DemandType` |
| `ObjectMode` | `On` generates parked cars/rickshaws/CNGs and standing pedestrians |
| `AcrossPedestrianMode` | `On` generates road-crossing pedestrians |
| `AlongPedestrianMode` | `On` generates pedestrians walking along the road |
| `AcrossPedestrianPerHour`, `AlongPedestrianPerHour` | pedestrian arrival rates |
| `SignalChangeDuration` | seconds between signal phase changes |
| `NoOfRoutes` | how many routes get their own per-route CSV files |
| `ReportAnimationFrames` | frames captured for the report's two embedded SVG animations, 2D and 3D (`0` disables both). Each frame is stored in full, so this is the main control over report size |
| `Network` | which `input/` sub-folder to simulate (e.g. `bijoy_sarani`); the GUI dropdown and `--network` override it |
| `TimeOfDay` | hour of the surveyed day to simulate, `0`-`23`; `-1` uses the busiest hour. The GUI dropdown and `--hour` override it |
| `GeometryMode` | `On` applies the network's `geometry.txt`: physical medians and roundabouts. `Off` (default) keeps byte-identical parity with the Java reference |
| `Seed` | pins the RNG to this value, unlike `RandomSeed` (below), which discards it. `--seed` sets the same thing |
| `NetworkRoadLength` | km of road used to size the roadside-object population. `auto` (the default) measures it from the loaded network; a number pins it, and `1.01` reproduces the Java original |
| `StatsDir` | where this run writes its CSVs and report (default `statistics`). Give each run of a batch its own, since every CSV is appended to |
| `DemandOverride` | vehicles/hour for every OD pair, replacing `demand.txt`. Negative (the default) uses the file |
| `DemandOffset` | added to every demand row (default `30`, the Java constant). Set `0` so a requested rate is the rate |
| `VehicleMixOverride` | `On` (default) keeps the Java behaviour of forcing the speed-class split to 25/25/50; `Off` lets `SlowVehicle`/`MediumVehicle`/`FastVehicle` take effect |
| `TraceMode` | `On` replays a recorded `trace.txt` instead of simulating |
| `DebugMode` | `On` writes per-vehicle traces into `debug/` |

`CF_model`: 0 hybrid (default), 1 naive, 2 Gipps, 3 Krauss, 4 GFM, 5 IDM,
6 RVF, 7 VFIAC, 8 OVCM, 9 KFTM, 10 HDM, 11 SBM, 12 strip-based weighted model,
13 modified Newtonian.

Model 1 is Newtonian motion with explicit braking: full acceleration, or
whatever deceleration closes the remaining gap in one step. Model 13 replaces
both branches with a single continuous acceleration — the rate that would leave
the vehicle exactly at its leader's tail — bounded below at 8.5 m/s², a
vehicle's physical braking floor. It therefore decelerates smoothly where the
naive model slams. Run it with `BrakeHard Off` (the shipped default): `BrakeHard
On` reimposes the explicit-braking branch afterwards and cancels the difference.

## Behaviour worth knowing about

Long-standing quirks of the simulator. They are intentional here — each one is
commented at the point in the code where it happens — but they will surprise you
otherwise.

- **`RandomSeed` in the file is ignored.** The simulator picks a random seed in
  `[0, 101)` at startup, so a plain run is not reproducible. Use `--seed <n>`
  (or a `Seed` line) for a repeatable one. The GUI's seed field applies the
  value you type to the main generator, but not to the object and pedestrian
  generators below, so a GUI run is only partly reproducible.
- **Object and pedestrian generation used its own unseeded RNG.** Roadside
  object placement, parking times, blockage draws and pedestrian arrivals each
  built a fresh clock-seeded generator per call, following the Java original,
  so the seed never controlled them — and with `ObjectMode On` those objects are
  most of what the traffic negotiates. `--seed` now routes them through the
  seeded generator (`parameters.scratch_random`); without it they behave as
  before. Only the distributions were ever reproducible here, never the stream.
- **Every demand row gets `+30` vehicles/hour.** Negligible on an 8-row
  junction, but `demo_backup` has 110 OD pairs, so it adds 3,300 veh/h.
  `DemandOffset 0` removes it.
- **`DLC_model 1` used to crash with `ObjectMode On`.** The GHR branch of
  `is_object_in_proximity` evaluated the GHR acceleration against the cached
  `leader` field, which is null there, so the combination threw instead of
  running — invisible because the shipped `DLC_model 0` never reaches it. GHR
  now falls through to the same object-gap test the other models use, which is
  what the equivalent method for vehicles already did. GHR has no sensible
  reading against a roadside object anyway: an object never moves, so the
  closing speed is the follower's own and the acceleration is negative at any
  distance, which would read as "obstructed" always.
- **Roadside-object density used to ignore network size.** The object counts are
  a density — 19.77 parked cars, 15.61 rickshaws, 15.61 standing pedestrians and
  4.67 CNGs per kilometre — but they were baked against a fixed 1.01 km, and
  they are compared against one network-wide counter. So every network got the
  same absolute number of objects: on the 3.83 km `demo_backup` that spread
  roughly a kilometre's worth of side friction over four. The length is now
  measured from the loaded network, so all six are correctly provisioned;
  `NetworkRoadLength 1.01` restores the old fixed value.
- **Parameter parsing falls through.** A `DLC_model` line also sets the
  car-following model from the same value and then assigns `SlowVehicle`; a
  `CF_model` line also assigns `SlowVehicle`. Because `parameter.txt` lists
  `DLC_model` before `CF_model`, the `CF_model` line wins — keep that order.
  It also means anything setting `SlowVehicle` must come *after* `CF_model`, or
  the `CF_model` value silently becomes the slow-vehicle percentage.
  `SlowVehicle`/`MediumVehicle`/`FastVehicle` are then overwritten with 25/25/50
  at the end of parsing, so all three settings have never done anything; set
  `VehicleMixOverride Off` to let them through.
- **`MaximumSpeed` is in km/h**, the unit the GUI field is labelled with and
  the unit a speed limit is actually quoted in. It is held internally in m/s.
  The shipped `100.0` is a realistic 100 km/h network limit. *The Java original
  read this field as m/s*, which made the same `100.0` mean 360 km/h — no limit
  at all, since the fastest vehicle type manages 110 km/h. Set `MaximumSpeed 360`
  to reproduce that reference behaviour.
- **`Probability of Accident` is transformed twice in the GUI.** Startup turns
  `EncounterPerAccident 0.01` into 10000; the option form shows that 10000 and
  transforms it again, giving −9899. Nothing currently reads the result.
- **Accidents never reach the counters.** The accident check consumes the
  pedestrian as a side effect of logging, so `agg_total_collision.csv` reports 0
  collisions and 0 accidents even while `Accident: SimStep: …` lines print to
  stdout. Count those lines if you need the total.
- **Every demand row gets +30 veh/h added**, including the surveyed hourly
  counts. On a 621 veh/h movement that is a 5% inflation; on a 60 veh/h one it is
  50%. Account for it — or remove it in `Processor._read_demand` — before
  calibrating against the survey data.
- **`statistics/csv/` is appended, never truncated.** Delete that folder between
  experiments, or each CSV grows a row per run. (HTML reports at the
  `statistics/` root are per-run, time-stamped files and are not overwritten.)
- **The GUI writes `trace.txt` every frame**, which grows quickly.

## Building a network from OpenStreetMap

The shipped networks were transcribed by hand from overpass-turbo GeoJSON
exports. `make_network.py` does it mechanically:

```bash
python make_network.py export.geojson --out input/miami --centre 25.48,-80.47 --radius 1500
```

Get the GeoJSON from [overpass-turbo](https://overpass-turbo.eu) — a
`way["highway"~"..."](bbox); (._;>;); out geom;` query, then Export → GeoJSON.
The tool projects to local metres, splits ways where they meet, collapses
degree-2 bends into multi-segment links, drops short dead ends and
disconnected fragments, and writes `node.txt`, `link.txt` and `geometry.txt`.
Run `run_sim.py --network <name>` afterwards for the routes and demand.

The part that matters most is dual carriageways. OSM maps a divided road as two
one-way ways, but this simulator's links are bidirectional with the median
expressed in `geometry.txt` — so importing both sides verbatim would give the
road twice the capacity at half the width each. Antiparallel one-way pairs are
detected and fused, taking the centreline between them, a width from kerb to
kerb, and the measured gap as the median. `--no-merge-dual` turns that off,
`--max-separation` sets how far apart two carriageways may be and still count
as one road.

Widths come from the `width` tag, else `lanes` × 3.25 m, else a default per
road class. Checked against the hand-built `banani_23`: Kemal Ataturk Avenue
converts to 17.9 m wide with a 4.9 m median, against 15.5 m and 1.5 m read off
a satellite image by hand — the same road at the same order of magnitude, which
is about as close as an automatic derivation gets.

### Miami and Riyadh

`input/miami` and `input/riyadh` exist so the lane / non-lane comparison can be
run against developed-country road layouts, but they are **idealised grids, not
surveyed topology** — each `geometry.txt` says so at the top. They come from
`make_grid.py`, which writes a GeoJSON street grid and feeds it through the same
converter real data goes through, so the fusion, width and splitting logic is
identical:

```bash
python make_grid.py riyadh --out riyadh.geojson
```

What they reproduce faithfully is the carriageway widths the paper states —
Miami 10 m and 20 m, Riyadh 18 m and 24 m, with divided arterials — because
that, not the street pattern, is what its conclusion rests on: wide long roads
reward lane discipline and narrow ones do not. Miami's area is inferred from the
paper's Figure 3(d); Riyadh's district could not be identified from Figure 3(h),
so Al Malaz stands in. Swap either for a real extract with one command.

Note that roadside objects are still Dhaka's — parked rickshaws and CNGs appear
in Miami and Riyadh too, since `ObjectMode` models one city's side friction. Run
those cities with `ObjectMode Off`, or accept it as a known difference from the
paper.

## Experiment sweeps

`experiments/roadbird.py` runs the lane / non-lane comparison as a batch,
sweeping strip width, demand, vehicle mix and pedestrians over a range of seeds:

```bash
python experiments/roadbird.py --dry-run
```

```bash
python experiments/roadbird.py --seeds 10 --jobs 8
```

The full grid is 24 scenarios — 2 lane modes × 3 demands × 2 mixes × 2
pedestrian settings — so 240 runs at ten seeds. Each is a separate process with
its own `StatsDir`, because `Parameters` is process-wide state and every CSV is
appended to rather than truncated. Interrupted sweeps resume: a run whose
directory holds a `done` marker is skipped.

Results land under `experiments/results/` — each run's own CSVs untouched, plus
`results.csv` (one row per scenario × seed × metric × link) and `summary.csv`
(averaged over links and seeds, with a count of the NaNs dropped so a mean
resting on very little is visible as one). Narrow the grid with `--demands`,
`--mixes`, `--lane-modes`, `--pedestrians`, and shorten runs with `--end-time`
while iterating.

### Demand rates are not the simulator's own

`--demands` selects a label; `--rates LOW MEDIUM HIGH` sets what those labels
mean, in **vehicles per hour per OD pair**. The harness's defaults are
100 / 400 / 800, which are *not* the rates `run_sim.py` builds a network's
`demand.txt` from. Those come from `DemandType` and divide a total across the
boundary nodes — `LOW_RATE // acceptable_node` and so on — which for
`demo_backup` (11 boundary nodes, 110 OD pairs) works out to:

| DemandType | total | per OD pair |
| --- | --- | --- |
| 0 low | 200 | 66 |
| 1 medium | 700 | 100 |
| 2 high | 1200 | 120 |

The shipped `input/demo_backup/demand.txt` is a uniform 100, i.e. the medium
one. So the harness's default "low" already equals the simulator's *medium*,
and its medium and high sit three to seven times beyond the simulator's *high*.
That is a legitimate stress test, but it is a different demand range from the
one the shipped networks are built around — pass `--rates 66 100 120` to sweep
the range the simulator itself uses.

**A change of `--rates` needs a fresh `--out`.** A run's identity is its label,
not its rate, so a `done` marker written at one rate will be silently reused for
a different one. Resumability cannot tell the two apart.

`experiments/stats.py` tests whether a difference in those results is real:

```bash
python experiments/stats.py experiments/results/results.csv --by lane_mode
```

It splits `results.csv` on any factor column and reports, per metric, both
means, a two-sample t-test and a two-sample Kolmogorov–Smirnov test. It is also
importable, offering the five error measures a validation against field data
needs — `ME`, `MAE`, `RMSE`, `MAPE`, `RMSPE` via `error_summary`, plus
`t_test_ind` (pooled or Welch) and `ks_test_2samp`.

Since there is no SciPy here, the t distribution and the Kolmogorov
distribution are implemented directly — a continued fraction for the
regularised incomplete beta, and the alternating exponential series. Both
reproduce published critical values to five decimal places, and
`tests/test_stats.py` pins them to closed forms that hold exactly at particular
parameters. One limitation is worth knowing: the K-S p-value is the asymptotic
approximation, dependable once `n1·n2/(n1+n2)` exceeds about 10 and optimistic
below that. The statistic `D` itself is exact.

## The paper comparison report

`experiments/paper_report.py` turns a sweep into one self-contained HTML page
comparing it against the RoadBird paper:

```bash
python experiments/paper_report.py experiments/results/results.csv --out report.html
```

It tests ten claims the paper makes about Dhaka — non-lane beating lane on
speed, waiting time and flow; each of those worsening as demand rises; the
speed crossover the paper predicts at high demand; the gain from removing slow
vehicles; and the pedestrian asymmetry between mixes — and marks each
*supported*, *not reproduced* or *no data*. Every claim is quoted from the
paper's prose rather than read off one of its bar charts, since a value
eyeballed from a figure is not evidence.

Charts are hand-built SVG, per-link bars in the shape of the paper's Figures
10–12 with each system's average drawn as a dashed line. Two things about how
it aggregates are worth knowing:

- The lane-versus-non-lane claims are judged on the **heterogeneous mix with
  pedestrians present** alone, which is the slice the paper's Dhaka figures
  plot. Pooling the homogeneous runs into those means would average two
  different cities' traffic together.
- Error bars are the spread **across seeds**, not across links. Links within a
  run share a traffic stream and are not independent, so the t-test and K-S
  p-values — which run over per-link values, the unit the paper's own figures
  plot — are optimistic. They indicate effect size rather than proving it.

The report says what it cannot test, too: the paper's Table IV validates
simulated travel times against observed ones from real Dhaka routes, and
without that field data the five error measures in `stats.py` have nothing to
compare to.

## Performance

Roughly 7–8 minutes for the shipped 1800-step Dhaka network on a desktop CPU.
Runtime grows with the number of concurrent vehicles, so it is worse than linear
in `SimulationEndTime` — lower that while iterating. The GUI keeps up at the
default `SimulationSpeed 1`.

## Tests

```bash
python tests/test_javacompat.py
```

```bash
python tests/test_modified_newtonian.py
```

```bash
python tests/test_stats.py
```

```bash
python tests/test_make_network.py
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

`tests/test_modified_newtonian.py` pins `CF_model 13` instead. That model has no
Java counterpart to compare against, so what it pins is the arithmetic its
defining equations imply: the acceleration cap, the 8.5 m/s² braking floor, the
intermediate rates between them, and the fact that the existing Boole-rule
distance integrator already supplies the `½a∆t²` term for every model.

## Provenance

Translated from the Java implementation of DhakaSim, one module per original
class, and validated against it: with a matched seed both produce byte-identical
`statistics/csv/*.csv` and identical printed metrics, across all 13 car-following
models, all 4 lane-changing models, the pedestrian and roadside-object paths
including the per-step accident log, the route/demand generator output, and the
drawing geometry written to `trace.txt`. Unseeded, repeated runs agree within
statistical noise.

Two later changes deviate from that reference on purpose, both listed under
**Behaviour worth knowing about** above and both revertible with a setting:
roadside-object density now scales with network size (`NetworkRoadLength 1.01`
pins it back), and `CF_model 13` is new here with no Java counterpart. Set those
two and parity is unchanged.

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
