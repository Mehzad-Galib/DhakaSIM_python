# Machine learning on DhakaSim's outputs

What the simulator already writes, what can be learned from it today, and what
has to change before the deep-learning methods are worth reaching for.

Written against the outputs as they stand: 72 completed sweep runs in
`experiments/results_paper/`, 158 accumulated rows in `statistics/csv/`, and an
889,262-row accident log.

---

## 1. What is on disk

Four assets, and they are very different animals.

### The sweep results — `experiments/results_paper/`

The only place where a configuration and its outcome sit in the same row.

```
results.csv   7,632 rows, long format
  run_id, network, lane_mode, strip_width, demand, mix, pedestrians, seed,
  metric, index_kind, index, value
summary.csv     144 rows, one per (network, lane_mode, demand, mix, peds, metric)
runs/            72 folders, the raw statistics/ of each run
```

Metrics carried: `avg_speed_vehicle`, `link_avg_speed`, `link_avg_waiting`,
`link_flow`, `route_avg_tt_car`, `route_avg_tt_motorbike`. Index kind is either
a vehicle type (0–12) or a link id.

Pivoted, this is **72 observations over six controlled factors**. That is a
designed experiment, not a large sample.

### The per-run CSVs — `statistics/csv/`

Around 60 files, one row appended per run, 13 columns per vehicle type
(`avg_speed_vehicle.csv`, `waiting_percentage_vehicle.csv`, `agg_avg_tt.csv`,
`fuel*.csv`, …) or one column per link (`link_avg_speed.csv`,
`link_avg_waiting.csv`, `link_flow.csv`).

**These rows carry no configuration.** Row 88 of `avg_speed_vehicle.csv` does
not record which network, demand or strip width produced it, and the folder is
appended to and never truncated. The 158 rows are a pile of unlabelled outcomes
from months of unrelated runs. Treat this directory as a per-run scratchpad, not
as a dataset. The sweep harness exists precisely because this one cannot be
joined back to its inputs.

### The accident log — `statistics/csv/accident_log.csv`

889,262 rows, and by a wide margin the largest real dataset the project has.

```
sim_step, vehicle_id, type, speed, acceleration,
leader_type, leader_speed, leader_acceleration
```

Every row is a conflict event **with both parties described**: the follower's
speed and acceleration, the leader's type, speed and acceleration. That is the
shape a surrogate safety model wants. Two caveats: it is appended across every
run ever executed with no run identifier, so it is a mixture of conditions; and
`leader_type = -1` with NaN speeds means no leader was found.

### The trace — `trace.txt`

One frame per simulation step, rewritten each run. Per vehicle it records the
four footprint corners and a colour. This is a complete microscopic record of
where every vehicle was, every second.

It is also the one asset with a structural hole: **no vehicle id and no type**.
Corners and colour only. Without ids there are no trajectories, only point
clouds per frame.

---

## 2. The fact that decides everything

Almost every number the simulator writes is an **end-of-run aggregate**. One
row, one run, one mean speed. The single exception is `flow.csv`, which buckets
network flow per minute of simulated time.

So the honest position is this. Sequence models — LSTM, TCN, Transformer,
spatio-temporal GNN — are the methods a traffic thesis is expected to use, and
they need a time axis. The simulator has a time axis internally and throws it
away at the end of the run. Two of the three tiers below are about getting it
back.

---

## 3. Tier A — works on today's data, no code change

Applies to the 72-run sweep. Small-n, six factors, controlled seeds.

### Gradient-boosted trees as a simulation surrogate

Fit `value ~ network + lane_mode + strip_width + demand + mix + pedestrians`
per metric. A run costs about 54 seconds; a fitted surrogate answers in
microseconds, which turns a 72-run sweep into a searchable response surface.

Use it for what tree ensembles are actually good at here, which is not
prediction accuracy but **structure**: SHAP interaction values will tell you
whether `lane_mode × demand` is a real interaction or whether lane discipline
helps uniformly. That question is the RoadBird paper's central claim and the
one your sweep reproduced only partially, so quantifying the interaction is
directly publishable.

Scikit-learn's `HistGradientBoostingRegressor` handles the categoricals and the
NaNs natively. Note that this breaks the project's no-dependency rule — keep
analysis code outside `dhakasim/`, in `experiments/`, as the sweep harness
already does.

### Variance-based sensitivity analysis

With a full factorial you can compute first-order and total-effect Sobol indices
directly, no surrogate needed. This is the defensible way to say "strip width
accounts for X% of the variance in average speed, demand for Y%". Reviewers
trust it more than feature importances, and it is pure arithmetic over the
design.

### Clustering links into regimes

`link_avg_speed`, `link_avg_waiting` and `link_flow` give every link a
three-number signature per run. K-means or agglomerative clustering over the
23 links of `demo_backup` separates free-flowing arterials from the approaches
that starve. Do this **per demand level** and watch which links change cluster
as demand rises: those are the links that fail first, which is exactly the set
your diversion study wants to target.

This is the single cheapest win in this document. It needs no new data, no new
dependency, and it produces a figure.

### Feature engineering on the existing columns

The raw columns are weak on their own. These derived features are not:

| Feature | From | Why it carries signal |
| --- | --- | --- |
| Speed dispersion across types | `avg_speed_vehicle` (13 cols) | Heterogeneity is the thing being modelled. The spread between the motorbike and the rickshaw is more informative than either alone. |
| Motorised / non-motorised speed ratio | same | The paper's sub-10 km/h claim turns out to be about which fleet you average. This makes that explicit instead of hidden. |
| Waiting share weighted by type frequency | `waiting_percentage_vehicle` × `generated_vehicles` | An unweighted mean over-counts rare types. |
| Link speed coefficient of variation | `link_avg_speed` | Distinguishes "uniformly slow" from "one blocked approach", which a network mean cannot. |
| Flow / speed ratio per link | `link_flow`, `link_avg_speed` | A density proxy, and the axis of the fundamental diagram. |
| Gini coefficient over link flows | `link_flow` | One number for how unevenly the network is loaded. Moves sharply under diversion, which is the effect you want to measure. |
| Approach imbalance at a junction | `link_flow` over a node's arms | Direct measure of what intersection blocking does. |
| Deviation from the seed-mean | any metric, grouped by config | Separates stochastic noise from treatment effect, and gives an honest error bar. |

The last row matters more than it looks. With ten seeds per cell you can report
effect sizes with confidence intervals rather than point differences, which is
the difference between a thesis chapter and a table of numbers.

---

## 4. Tier B — one small logging change unlocks the sequence models

### The change

`Processor` already buckets flow per minute of simulated time. The same place
can write a per-link row on the same schedule. That turns a run from one row
into a **panel of (link × minute)** observations: 23 links × 8 minutes = 184
rows per run instead of 1, and 13,248 across the 72-run sweep.

This is roughly a twenty-line change in one method. It is the highest
value-per-line work available on this project, and everything below depends on
it.

### What it then supports

**LSTM / TCN / Transformer for short-horizon prediction.** Predict each link's
speed and queue for the next few minutes from the last several. Standard,
expected in a traffic thesis, and impossible today.

**Spatio-temporal GNN — the strongest fit.** A road network *is* a graph, and
the links are its nodes: `link.txt` gives adjacency for free through shared
node ids. A DCRNN or Graph WaveNet over that graph learns how congestion
propagates from one approach to the next. For a thesis on flow diversion this
is the natural model, because diversion is precisely a question about where
load moves when a link is removed, and a graph model answers it structurally
rather than by re-running the simulator for every scenario.

**Change-point detection on the per-minute series.** When does an approach tip
from flowing to queueing? Identifying the tipping point, and how demand shifts
it, is a result in itself.

**Learned diversion policy.** With the panel as state, the intersection-blocking
scenario becomes a sequential decision problem. Reinforcement learning here is
ambitious for one MSc and I would not lead with it, but the same panel supports
a supervised alternative: learn which diversion an oracle search picked, from
the network state that preceded it. That is a tractable thesis contribution
with a clear baseline.

---

## 5. Tier C — trajectories, once the trace carries ids

`trace.txt` becomes trajectory data the moment each vehicle writes its id and
type alongside its corners. The corners already give position, heading and
footprint; consecutive frames give speed and acceleration; the footprints of
neighbours give the gap structure.

That unlocks the methods that are genuinely deep-learning-shaped:

- **Trajectory prediction with social pooling.** Non-lane traffic is the
  interesting case for these models, because the usual lane-following prior is
  absent. Dhaka is a better test bed for them than a motorway, and saying so is
  a contribution.
- **Surrogate safety measures.** Time-to-collision and post-encroachment time
  computed from the footprints, then modelled. The accident log already gives
  you the labelled events to validate against.
- **Lateral-placement mining.** Where in the carriageway does each type sit, and
  how does that change with demand? The strip model permits any placement; what
  emerges is an output of the model, not an input, and it is measurable from the
  trace.
- **Anomaly detection.** An autoencoder over trajectory windows flags unusual
  manoeuvres. Useful for validation: if the simulator produces motions no real
  driver makes, this finds them.

### Data mining on the accident log, which needs nothing

The 889k-row log supports real mining work today:

- **Association rule mining** over discretised (follower type, leader type,
  speed band, acceleration band). Which type pairs co-occur in conflicts more
  than chance? The answer is a table about heterogeneous interaction, which is
  the project's subject.
- **Conflict-severity clustering** on the two-vehicle state, then a look at
  which clusters change frequency under lane discipline.
- **Sequential pattern mining** on `sim_step` — do conflicts cascade, and over
  what interval?

One caution first: the file is appended across every run ever executed, so it
mixes conditions. Add a run identifier to the row, or clear the folder before
the experiment, or the mining is over an undefined population.

---

## 6. Things that will bite

- **`statistics/csv/` is appended, never truncated.** Delete the folder between
  experiments or every run adds a row to files you thought were fresh. The
  158-row files are a demonstration of this.
- **NaN is meaningful.** `NaN` in `avg_tt*.csv` means no vehicle of that type
  completed a trip, not a missing measurement. Imputing it with a mean
  fabricates trips. Encode "did any complete" as its own feature.
- **The collision counters read 0** even when accidents occur; the per-event
  detail is in `accident_log.csv` and the console. Do not model the aggregate
  columns for safety work.
- **The simulator has no lanes.** Lane dividers are paint. Any feature that
  reads like "lane occupancy" has to be derived from strip positions, not from
  the markings.
- **72 runs is not a deep-learning dataset.** Tier A on 72 runs is honest work.
  A neural network on 72 runs is not, and a reviewer will say so. The DL case
  rests on the panel and the trace, both of which have thousands to millions of
  observations per run.
- **Seeds are a factor, not noise to average away.** Report across-seed variance
  explicitly; it is the natural baseline any treatment effect must beat.

---

## 7. What I would actually do

In order, and each step is useful even if you stop there.

1. **Cluster the links into regimes** from the existing sweep. No new code, no
   new data, one figure, and it tells you which links matter for the rest.
2. **Add per-minute per-link logging** and re-run the sweep. This is the
   twenty-line change, and it converts the project from aggregate to panel.
3. **Fit a spatio-temporal GNN** on the panel to predict link speed under
   diversion scenarios. This is the thesis's methodological core: it uses the
   graph structure your problem already has, it needs the data volume you now
   have, and it answers the diversion question directly.
4. **Mine the accident log** for heterogeneous conflict patterns as a second
   chapter. Independent of the above, already has the data, and covers you if
   the GNN work runs long.
5. **Add ids to the trace** only if time remains. It is the richest asset and
   the largest project.

Steps 2 and 3 together are a coherent MSc contribution in a data-science
department: a graph neural network for congestion propagation in non-lane
heterogeneous traffic, trained on a calibrated microscopic simulator, evaluated
against held-out diversion scenarios. The novelty is the traffic regime, not the
architecture, which is the right way round for this degree.
