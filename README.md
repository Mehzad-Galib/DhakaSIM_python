# DhakaSim (Python)

DhakaSim is an event-based microscopic traffic simulator for the non-lane-based,
heterogeneous traffic typical of cities like Dhaka. Vehicles occupy multiple
narrow **strips** rather than single lanes, and the model includes the
side-friction elements that dominate congestion there: parked cars, rickshaws
and CNGs encroaching on the carriageway, standing pedestrians, pedestrians
walking along the road, and heavy road-crossing pedestrian flow.

Self-contained Python implementation — no third-party dependencies, the GUI uses
`tkinter` from the standard library. Python 3.10 or newer.

![The 3D view: the Kakrail Church roundabout under load, with the queue on
Kakrail Mor hazing into the distance](docs/images/view3d_kakrail_corridor.png)

## Features at a glance

**The traffic model**

- Strip-based, non-lane movement: a carriageway is a row of half-metre strips
  and a vehicle occupies as many as its width needs, which is how a rickshaw,
  a bus and a motorbike genuinely share one road.
- 13 vehicle types, from bicycle and rickshaw to bus and truck, each with its
  own size, speed and behaviour; the mix is per-network (`vehicle_mix.txt`).
- 13 car-following models (`CF_model`, including Gipps and a modified
  Newtonian model unique to this build) and 4 lane-changing models.
- Side friction: parked cars, rickshaws and CNGs on the carriageway, standing
  pedestrians, pedestrians walking along the road, and road-crossing flows.
- Near-crash and accident modelling with a per-event log.
- Traffic signals: fixed-time, biased-random, and two multi-objective
  optimising controllers (Rahaman et al., IEEE Access 2025).
- Time-of-day demand: networks with an hourly profile
  (`demand_by_hour.txt`) can be run at any hour or at the peak.

**Networks**

- Seven shipped networks: four surveyed Dhaka junctions (Banani 23,
  Banani 27, Bijoy Sarani, and the two-signal Kakrail corridor with its
  roundabout) and three OSM-derived multi-intersection networks (Mohakhali,
  Miami, Riyadh).
- Real-geometry mode: medians, roundabouts, one-way links and turn lanes from
  each network's `geometry.txt`; `GeometryMode Off` is the Java-parity mode.
- Build your own network from OpenStreetMap with `make_network.py`, fetch map
  imagery for it with `fetch_basemap.py`, and bend its links onto the real
  roads with `fit_roads.py` — see the sections below.

**Seeing a run**

- A settings screen for picking the network, time of day, signal controller,
  models and display options — no file editing needed for a normal run.
- 2D plan view over real map imagery (OpenStreetMap rendering or aerial),
  with street and junction names, pan/zoom, and a live legend.
- A VISSIM-style 3D perspective view of the same run — orbit, pan and zoom —
  in two styles: shaded solid, or line art. Switchable mid-run without
  disturbing the simulation.
- Pause/resume, trace recording and replay (`trace.txt`), and a live
  vehicles-on-network panel.
- A self-contained HTML report per run, carrying animated 2D and 3D replays,
  per-type statistics and the run's settings — one file, openable anywhere.

**Numbers out**

- Per-link speed, waiting and flow CSVs; per-type speed, waiting, trip,
  fuel, collision and accident CSVs; sampled per-vehicle trajectories — all
  under `statistics/csv/`, appended per run.
- An experiment-sweep harness (`experiments/`) and a paper-comparison report.

**Trustworthiness**

- Ported class-for-class from the Java original and validated against it:
  matched seeds produce byte-identical output (see *Provenance*).
- Thirteen test suites pin the numerics, the file grammar, the geometry and
  the renderers; `hash_run.py` hashes a seeded run of every network
  end-to-end so any behavioural drift is caught as one command (see *Tests*).

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
geometry before starting:

![The settings screen: junction, time of day, signal control, road model and
display options](docs/images/start_screen.png)

Once running: drag to pan, and zoom with the mouse
wheel, the right-hand slider, or the **+** and **&minus;** buttons in the
legend panel. **Reset** there returns the zoom to its starting level. The
bottom slider shows progress.

![The 2D plan view: the Kakrail corridor drawn over the OpenStreetMap
rendering, with the live legend on the right](docs/images/plan_kakrail_corridor.png)

The legend also names the place being simulated, taken from the network's
`place.txt`, and street names are drawn along the roads for networks that
ship a `link_names.txt`.

Changing the **Intersection** moves the speed limit with it, because a
limit is a property of the roads rather than of the run. Miami and Riyadh state
100 km/h in their own `defaults.txt`; every Dhaka network uses the 60 km/h in
`parameter.txt`. Typing a different number in the field still wins.

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

The scene is lit and graded rather than flat: the sky shades from blue down
to a pale horizon, the ground hazes out towards the distance, sunlit faces
lean warm while shaded faces lean cool, and bodies fade towards the haze the
further into the scene they sit — which is also what keeps a wide view of a
busy network readable, because a bus half a kilometre away no longer shouts
as loudly as one by the camera. Distant vehicles are still always drawn: past
the point where a model would be smaller than a few pixels it becomes a
single block, then a single dot, but it never disappears, so a wide framing
still shows where the traffic is.

There are two styles, chosen on the start screen (**Display → 3D view**) or
with `Render3DStyle solid|line` in `parameter.txt`. Solid is the shaded look
above; **line art** draws every vehicle as an ink outline over a pale wash of
its own colour, which stays legible at the highest densities:

![The same roundabout in line art: outlines fade with distance instead of
piling into a thicket](docs/images/view3d_line_kakrail_corridor.png)

`Render3D On` in `parameter.txt` (or the **3D View** buttons on the start
screen) opens straight into it. The view can be switched at any point in a
run, without disturbing it: it is only a matter of how each frame is drawn, so
the results, the report and `trace.txt` are identical either way. A recorded
run replayed with `TraceMode On` renders in 3D as well — the trace stores no
vehicle types, so the model is inferred from each footprint, which recovers
all 13 correctly.

The renderer is `dhakasim/render3d.py`, drawing with plain `tkinter` polygons:
no OpenGL, no GPU and, in keeping with the rest of the simulator, no
third-party package. While the camera is still — the usual case, watching a
run — the sky, ground, roads and street names are kept on the canvas and only
the vehicles are redrawn each frame, which roughly halves what a frame costs;
orbiting, panning or zooming repaints everything and costs the other half
back for those frames. Measured numbers are under **Performance** below.

### While a run is going

The toolbar carries **Pause** / **Play**, and the space bar does the same. Only
the stepping stops: the view still repaints, so panning, zooming and switching to
the 3D camera all keep working on the held frame. `V` switches between the plan
and 3D views, `M` toggles the map imagery.

The panel on the right shows the place, a live count per vehicle type, the
side-friction key, and **Run settings**: the configuration the run was started
with, since the setup form is gone by then and every one of those values changes
what the numbers on screen mean. It is read from `Parameters` once when the run
begins rather than bound live, so it stays a record of what was chosen. Both the
vehicle counts and the settings collapse with the button beside their heading,
which is worth knowing on a short screen, where the panel does not scroll.

**Frame delay** is the exception, and is a spinner rather than a reading. It
only sets how long the picture pauses between frames, so changing it mid-run
cannot make the run mean anything different; every other setting there would.
Nothing is restarted when it changes: the panel's timer reads the value each
time it schedules the next frame, so a new delay takes effect on the following
tick, and one set while paused applies the moment the run resumes. The arrows
step 1-2-5 per decade, because playback speed is judged by eye and equal ratios
are the useful steps; any value from 1 to 60000 ms can be typed. Typing is not
interrupted by clamping, which only happens once the edit is committed.

## Layout

```
input/            parameter.txt plus one sub-folder per intersection/network
statistics/       HTML reports at the root; raw CSVs in statistics/csv/ (appended)
trace.txt         created by the GUI; one frame per simulation step
dhakasim/         the simulator package
run_dhakasim.py   launcher
run_sim.py        route/demand generator (only needed if the network geometry changes)
make_network.py   builds a new network from OpenStreetMap data (optional)
make_grid.py      builds an idealised grid network (optional)
fetch_basemap.py  downloads the map imagery a network is drawn over (optional)
fit_roads.py      bends a network's links onto the real roads (optional)
hash_run.py       end-to-end drift check: hashes a seeded run of every network
experiments/      the sweep harness and paper-comparison report
tests/            regression tests for the numeric layer
docs/images/      the screenshots in this README
```

Inside `dhakasim/`, three modules render the same picture through the same
drawing calls, so they cannot drift apart: `gui.py` draws the 2D window,
`render3d.py` the 3D one, and `visualize.py` the report's SVG. `basemap.py`
sits under all of that, placing map imagery in the same metres everything else
is drawn in.

### Junction rendering

`road_geometry.py` builds six things: the carriageway quads, a filled patch per
junction, the roundabout islands, a curved ribbon for every turning movement,
the painted lane dividers, and one continuous ribbon per link.  `paint()` draws
them through the shared surface protocol, so the window, the report and the 3D
view cannot disagree about the shape of an intersection.

The carriageway is painted from the ribbons, one polygon per link, not from the
quads.  On a straight link the two are the same thing; on a link that bends
they are not, and the difference is the whole reason the ribbons exist.  Two
neighbouring segments meet exactly at their stated ends, but each offsets its
far edge along its own normal, so at a bend of theta their far corners stand
`2 w sin(theta/2)` apart -- five metres on a fourteen-metre road turning twenty
degrees.  Every links-fitted-to-real-roads network bends by about that much at
every joint, so quads left the road looking like a row of planks with a wedge
missing between each pair.  Offsetting the chain as a whole and mitring the
interior corners closes the wedges and lets each kerb run the length of the
link as one line.  A bend tight enough to send the mitre off towards infinity
is cut square instead, at `MITRE_LIMIT` times the width.

The quads are still built, because `junction_hulls` probes against them to
decide which edges of a junction are mouths, and a quad is convex where a
ribbon is not.

Junction corners are rounded rather than chamfered. The radius comes from the
narrowest arm meeting there, since a side road cannot carry a wider kerb radius
than its own carriageway, and the cut-back is clamped to half the shorter
adjacent edge so neighbouring corners can never overrun each other.

A roundabout is **swept, not assembled**. Its paved area is the ring plus every
arm's throat, and `roundabout_outline` produces the two as one closed polygon
rather than drawing a circle and then patching the gaps around it.

Patching was tried at length and does not work. A stated line is one kerb edge
with the carriageway a full width to its side, so cutting it on the circle
leaves the mouth lying across the ring at whatever angle the arm happens to
arrive at. At Khamarbari nine of the twelve kerbs cross the ring and three sail
past it, one of them by fourteen metres — Manik Mia Avenue is thirty-two metres
wide arriving at a forty-eight metre circle, and its outer half has nothing to
join on to. Every patch that closes such a wedge has a corner in it, and that
corner belongs to the *union* of the patch and the arm's own ribbon, so
reshaping either one alone cannot round it: shrink the patch and a crescent of
bare ground opens between the two.

The sweep sidesteps all of it. The union of the ring and the arms is
star-shaped about the circle centre, so its boundary is one radius per angle.
Sample the angles, take the furthest road at each, and every arm is joined to
the ring by construction — there is no seam to close because nothing was ever
cut apart. `ROUNDABOUT_REACH` caps how far out the outline goes; past it the
arm's own ribbon takes over, and since the outline is inside the ribbon there,
the join does not show.

What is left is the notches, and those are rounded by a morphological
**closing** — dilate the radii, then erode them, both over
`ROUNDABOUT_CORNER_DEGREES`. A closing has the one property this needs: it can
only ever add, since the closing of a set contains the set, so no ground that
was road stops being road. It is also selective. The sharp V where a mouth
meets the ring is narrow and goes; the kerb island between two neighbouring
arms is wide and stays, which is right, because it is a real island.

Two details. Where the radius jumps between samples the boundary is a corner,
not a slope, and it is squared off rather than cut across with a chord —
`ROUNDABOUT_CORNER_JUMP` is where that line is drawn. And the outline is
sampled at least `ROUNDABOUT_MIN_STEPS` times whatever the zoom, because unlike
a plain circle it carries real detail: six mouths and six islands.

There are no turn connectors at a roundabout, because every movement there is
the same one-way ring. The island is opaque even over imagery, which is the one
place the map is
deliberately hidden: it is the one part of the picture you cannot drive on.

Turn connectors are cubic Beziers entering along one arm's tangent and leaving
along the other's, swept to the narrower of the two widths. One ribbon is drawn
per unordered pair of arms, because the path from A to B is the same shape as
the one from B to A, and a pair is skipped when one-way restrictions make the
movement impossible in both directions. They are filled in the road colour, so
the junction silhouette becomes the union of the patch and the turns that swing
outside it.

Carriageways carry painted lane dividers: dashed grey lines at a nominal
3.5 m lane spacing, 3 m dash and 3 m gap. They are markings only. The
simulation has no lanes, a vehicle straddles as many 0.5 m strips as it likes,
and drivers ignoring the paint is precisely the behaviour being modelled. A
road narrow enough for one lane gets no divider. The dividers stop at the
junction rather than being ruled across it, matching how an approach's markings
end at an intersection.

### Colours

Every vehicle type has a fixed colour, shared by the window, the 3D view and the
report legend, so a rickshaw is the same orange everywhere. Cars, buses and
trucks each span a few shades of one hue.

Side friction is separate. Parked cars, parked rickshaws, parked CNGs and
standing pedestrians are obstructions rather than traffic, and they carry a
yellow family no moving vehicle uses, varying by lightness within it. They never
appear in the per-type counts, because they do not travel, so both the GUI
legend and the report give them a key of their own.

The road itself is white. On a blank canvas that is a hair off pure white --
the background is white too, and at report scale the kerb is a sub-pixel line,
so a carriageway with no tint at all would have nothing left to be seen by. The
markings are grey for the same reason: pale paint reads on dark asphalt, and
not at all on a white road. Over imagery the road is pure white and painted
solid, with a near-black kerb; that combination holds against both the drawn
street map and aerial photography.

The junction draws its own kerb round each corner, leaving a gap at every arm
so traffic has somewhere to drive. Each edge of the outline is classified by
stepping a little way outwards from the patch centre: land on road surface and
it is a mouth, land on nothing and it is kerb. This is what makes the arms'
kerbs meet the junction flush instead of stopping in mid-air.

Constants that tune the result, all in `road_geometry.py`:
`CORNER_RADIUS_FACTOR` (lower gives sharper corners), `CONNECTOR_TENSION`
(0.39 is a true quarter circle for a right-angle turn; higher swings the turn
wider), `LANE_WIDTH_METRES` (divider spacing), `MARKING_DASH_METRES` and
`MARKING_GAP_METRES` (dash rhythm), `KERB_WIDTH_METRES` (edge thickness) and
`MITRE_LIMIT` (how far a kerb corner may reach out of a bend before it is cut
square).

One convention to know before touching any of this: a segment's stated x and y
in `link.txt` is **one kerb edge**, not the centreline, and the carriageway lies
to one side of it. An arm's mouth centre is therefore offset half a width from
the coordinates in the file.

### Choosing an intersection

Each intersection lives in its own folder under `input/`, holding that
network's roads, routes, demand and vehicle mix:

```
input/parameter.txt          shared settings
input/kakrail_corridor/      Kakrail Church + Kakrail Mosque (2 junctions)
input/bijoy_sarani/          Bijoy Sarani
input/banani_23/             Banani 23 Super Market
input/banani_27/             Banani 27 Kacha Bazar
input/demo_backup/           the original demonstration network
```

Pick one under **Junction** on the GUI's start screen, or set
`Network <folder>` in `parameter.txt`, or pass `--network <folder>`:

```bash
python run_dhakasim.py --headless --network bijoy_sarani
```

To add your own network, create a new folder with the same files.

### Choosing the time of day

The surveys cover a full 24 hours, so every network carries hourly demand and a
hourly vehicle mix. Choose the hour from the **Time of day** strip, or set
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
| `link_names.txt` | optional `id name` per line; street names drawn on the roads. Links without a name are labelled with their id, which is what the per-link CSV columns are indexed by |
| `place.txt` | optional single line naming the place, shown in the GUI legend and the report heading. Falls back to the folder name |
| `defaults.txt` | optional `Name Value` per line; settings that belong to the place rather than the run. Beats `parameter.txt`, loses to `--set` |
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

`GeometryMode` is **On** by default and reads the selected network's
`geometry.txt`, one directive per line (`#` starts a comment). Every surveyed
network ships one, and every one of them is wrong without it: a roundabout with
no ring, a dual carriageway with no median, a one-way arm reserving half its
road for traffic that never comes. Turn it **Off** to get the Java-parity mode,
where none of this runs and the simulator reproduces the reference exactly. A
network with no `geometry.txt` is unaffected either way.

```
median 2 2.0            # a 2 m central reservation on link 2
roundabout 6 14.0 17.0  # node 6: a 14 m island inside a 17 m ring
```

A **median** consumes `ceil(width / StripWidth)` strips at the centre of the
carriageway and pushes the per-direction limits outward, so it takes road space
away from traffic rather than being a free dividing line.  It is also read by
`fit_roads.py`, which is the network's own way of saying that this one link
stands for two separate carriageways on the map and belongs on the gap between
them; see "Fitting the links to the real roads" below. A **roundabout**
replaces the junction's signal phases with give-way priority — circulating
traffic keeps moving and an approach enters only when nothing is about to cross
it — bends every turn path into a clockwise arc around the island, and sets the
circulating speed to `1.7 × sqrt(radius)` m/s.

It also **pulls the arms back**, which is the part that makes the rest of it
mean anything. The survey draws a junction as a point: every arm at a node ends
on the same coordinate, which at a roundabout is the middle of the island. Left
alone, that is what gets modelled -- traffic drives across the island and stops
on it -- and no amount of drawing fixes it, because there is no ring for anyone
to be on. So each arm is cut where it crosses the outer kerb of the circulatory
carriageway, and everything inside becomes the ring. A roundabout is not a
junction with an ornament in the middle; it is a one-way circular road with a
solid island inside it.

The first number is the **island**, the second the circulatory carriageway
around it, and their sum is the outer kerb. Omit the second and the ring is two
lanes, `Constants.ROUNDABOUT_CIRCULATORY_WIDTH`. It is deliberately not the
width of the widest arm: an approach arriving on seven lanes still circulates on
two or three.

Both surveyed circles were measured off the map rather than taken from
OpenStreetMap's own figure, because that figure is neither dimension. A
`junction=roundabout` way is a loop drawn somewhere on the running surface: at
Khamarbari it fits a circle of radius 27.3 m, and its widest point, 28.8 m, was
what `geometry.txt` used to record. Read as the island that made the whole
circle about half again too big on screen.

What the map does draw honestly is the **island kerb**, as a feature of its
own. Walking outwards from the circle centre on every bearing, Khamarbari's
reads 12.2–15.5 m and a circle fitted to it comes back at 14.2 m, centred a
tenth of a metre from where the survey puts the junction; the outer edge of the
painted running surface reads about 31 m between the arms. So Khamarbari is a
14 m island in a 17 m ring, and Kakrail a 6.5 m island in a 9 m ring.

Khamarbari was 12 + 12 before that measurement, which is the more interesting
mistake of the two: defensible, taken off aerial photography, and it put the
whole ring **inside** the island the map draws. Traffic circulated between 12
and 24 m while the map paints the roundabout from 14 out to 31, so every
vehicle on the circle appeared to be driving across the island.

Because the arms are shorter, the network is shorter — Khamarbari drops from
1.61 km to 1.43 km — so a `GeometryMode On` run is not comparable with a
`GeometryMode Off` one. That was already true of medians; it is just larger
here.

`kakrail_corridor` ships a roundabout; every surveyed network except it
ships medians. Left off, none of this runs and the simulator
reproduces the Java reference exactly.

### How the 3D view draws

`Render3DStyle` picks between two ways of putting a vehicle on the screen.

**`line`** (the default) draws each part of a model once, as its silhouette:
an outline in the vehicle's own colour over a pale wash of the same colour. A
box shows the camera up to three faces, and the silhouette is the convex hull
of their corners, so this is one canvas item where the other style needs three.
The outline is darkened to a *luminance ceiling* rather than by a fixed
fraction, because a white truck outlined in near-white on a near-white road is
not there at all.

**`solid`** is the original: every camera-facing face filled in its own colour,
shaded by how much sun it catches — warm-tinted in the sun, cool-tinted in
the shade, and faded towards the horizon haze with distance. The haze scales
with the camera's own orbit distance, so it reads as depth at every zoom.
Roads, kerbs and markings deliberately get none of this: they keep the exact
2D palette, so the two views stay recognisably the same picture.

Level of detail runs in three tiers. A vehicle whose body is too small on
screen for its cabin to read (`LOD_PIXELS`) becomes a single block; smaller
than a few pixels (`SPECK_PIXELS`) it becomes a single flat dot in its own
colour — never dropped, because at a whole-network framing most vehicles are
specks, and several hundred dots are exactly how a wide view shows where the
traffic is. At full detail, parts that project under a couple of pixels
(wheels, mostly) are skipped.

Two other economies apply to both styles, and they are about the roads rather
than the vehicles. A kerb arrives as a run of separate line calls, each
starting where the last ended, and is collected into a single polyline -- one
item for a whole kerb instead of one per point pair. And a line that ends up
*alone* rather than in such a run, and projects to less than
`Scene3D.MIN_LINE_PIXELS`, is dropped: that is the lane markings, which on a
surveyed junction outnumber every kerb item four to one at a median of three
pixels each. A short line inside a run is never dropped, so a kerb can never
come out gapped.

Finally, the frame itself is split into layers. Sky, ground, roads and street
names only change when the camera or the window does, so between camera moves
they stay on the canvas untouched and each frame deletes and redraws the
vehicles alone — the same trick the HTML report uses for its 3D animation.
Current measured costs are under **Performance** below.

## Traffic signal scheduling

By default every junction runs a **fixed-time** signal: each approach takes a
turn on green for `SignalChangeDuration` seconds, round and round, whatever the
traffic is doing. That is what most of the few working signals in Dhaka do, and
it is the behaviour the Java original had.

`SignalMode` swaps that for one of three alternatives, implementing

> M. Rahaman, A. M. S. Rumi, M. S. Islam, T. R. Toha, M. M. Mushfiq, M. S.
> Rahman, M. A. Nayeem, N. A. Al-Nabhan and A. B. M. A. Al Islam, *Toward
> Devising A Multi-Objective Traffic Signal Scheduling Approach for
> Non-Lane-Based Heterogeneous Traffic*, IEEE Access vol. 13, 2025, 172598.

```bash
python run_dhakasim.py --headless --network banani_23 --set SignalMode=moo-v1
```

| `SignalMode` | what it does |
| --- | --- |
| `fixed` | the same green for every approach; the default and the baseline |
| `biased-random` | a random green scaled by each approach's share of the traffic |
| `moo-v1` | NSGA-II on Equations 1 and 2, which count every vehicle alike |
| `moo-v2` | NSGA-II on Equations 3 and 4, which price the vehicle classes apart |

### How it works

Only one approach at a junction can hold a green, so giving one more green
makes every other one wait: congestion and delay are in direct conflict. Rather
than collapse that into a single score, the schedule -- one green duration per
approach -- is searched by NSGA-II against both objectives at once, which
returns a Pareto front, and only at the end is one schedule picked off it.

Candidate schedules are scored on a **numerical simulator**, not on DhakaSim.
The optimiser tries tens of thousands of them per decision, so each has to be
priced in microseconds: an isolated junction, with each approach's queue
discharging at 0.9 motorised vehicles a second and 0.4 non-motorised ones,
concurrently, for as long as it holds its green. DhakaSim then plays the chosen
schedule out for real. That division is the paper's, and it is why the module
is cheap enough to re-run every cycle at every junction.

Each junction re-optimises when its cycle comes round, against the traffic
queued at that moment. Roundabouts never schedule -- they have no phases at
all, only a give-way rule.

It costs about 1.8x the run time on a one-junction network and 2.2x on
Riyadh's eight, at the default budget of 2000 evaluations per decision;
`SignalEvaluations` is the knob if that matters, and NSGA-II's cost is
quadratic in `SignalPopulation` rather than in the traffic, so a busy network
is no more expensive to schedule than a quiet one.

Two weights. `SignalMotorizedWeight` (*w*, Equation 3) prices a motorised
vehicle against a non-motorised one; the paper finds lower is better, because a
rickshaw takes more than twice as long to clear a stop line as a car and
counting them alike under-serves the approaches full of rickshaws.
`SignalObjectiveWeight` (*W*, Equation 6) balances the two objectives when one
schedule has to be chosen off the front; the paper finds it barely matters, and
so do the runs here.

### What it measures on the shipped networks

Average speed in km/h, three to five seeds, 480 s of traffic. "best fixed" is
the best of the fixed-time greens the paper sweeps (5, 10, 15 and 60 s), which
is a harder baseline than any single one of them:

| network | non-motorised | best fixed | biased-random | `moo-v1` | `moo-v2` |
| --- | --- | --- | --- | --- | --- |
| banani_23 | 9.6% | 7.20 | 4.94 | **7.59** | 6.53 |
| banani_27 | 7.2% | 6.12 | 4.41 | **6.62** | 5.57 |
| kakrail_corridor | 4.8% | **11.45** | 7.93 | 10.14 | 10.52 |
| bijoy_sarani | 1.3% | **10.15** | 6.62 | 9.96 | 7.14 |

Two things fall out of that, and neither is what the paper reports.

`moo-v1` beats the best fixed-time schedule on the two networks with the most
mixed traffic and loses on the two with the least, which at least points the
same way the paper does -- the harder the traffic is to serve with one rule for
everybody, the more there is for a scheduler to win. `biased-random` is worst
everywhere, as it is in the paper.

But `moo-v2`, which the paper reports as clearly its stronger version, is worse
than `moo-v1` on three of the four. The reason
is in Equation 4 and is worth writing down, because it is not an implementation
slip -- three readings of that equation were implemented and measured, and this
is the best of them.

Equation 4 divides the queue-weighted red time by the total queue. Write it out
with `R(i) = cycle − g(i)` and it reduces to `cycle − Σ C(i)·g(i) / Σ C(i)`.
For a cycle of fixed length that is smallest when the *whole* cycle goes to
whichever approach is busiest, so the objective actively pushes towards
starving the quieter approaches -- and a starved approach at a five-second
green is very expensive in a network simulation, where the paper's numerical
simulator only sees a queue that failed to clear. Equation 2, `moo-v1`'s
version, is just the cycle length and expresses no preference about the split,
which leaves the split to Equation 1 -- and Equation 1 wants each approach to
get exactly enough green to clear. That is a balanced schedule, and it is the
one that wins here.

The other two readings, for the record: taking `C(i)` as the vehicles
*remaining* after the cycle rather than those that arrived is degenerate, since
a schedule that clears every approach then scores zero on both objectives and
the whole population ties; and taking `R(i)` as the red *before* an approach's
green rather than its total red is worse again on both networks tested.

Two caveats on all of the above. These are single-junction networks and 480 s
runs, against the paper's whole-city network and 30-minute runs at up to four
times the traffic density, and the paper's gains are largest exactly where the
density is highest. And the numerical simulator credits a five-second green
with 4.5 vehicles cleared, which no real approach manages from a standing
start; the paper says as much, noting that driver reaction time would have to
be added to every allocated green in a real deployment.

## Output files

Each run writes a self-contained HTML report to `statistics/`, named
`report_<YYYYMMDD_HHMMSS>.html`, summarising the run with two animations of the
run -- the same captured frames in plan view and through the 3D camera -- metric
cards, a configuration table, a per-type results table, colour-coded bar charts,
a vehicle-colour legend and a glossary. It is one file with everything inlined,
so it can be mailed or archived and still opens anywhere:

![The top of a run report: metric cards, then the animated plan view over the
network's imagery](docs/images/report.png)

The raw numeric CSVs below are written
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
| `Render3DStyle` | `line` draws outlined silhouettes, `solid` shades every face |
| `SignalMode` | `fixed`, `biased-random`, `moo-v1` or `moo-v2` (see above) |
| `SignalChangeDuration` | green per approach in `fixed` mode, seconds |
| `SignalMotorizedWeight` | *w* in Equation 3, 0-1; lower favours non-motorised traffic |
| `SignalObjectiveWeight` | *W* in Equation 6, 0-1; congestion against delay |
| `SignalGreenMin`, `SignalGreenMax` | bounds on one approach's green, seconds |
| `SignalPopulation`, `SignalEvaluations` | NSGA-II's population and budget per decision |
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
| `Network` | which `input/` sub-folder to simulate (e.g. `bijoy_sarani`); the GUI's Junction picker and `--network` override it |
| `TimeOfDay` | hour of the surveyed day to simulate, `0`-`23`; `-1` uses the busiest hour. The GUI's Time of day strip and `--hour` override it |
| `GeometryMode` | `On` (default) applies the network's `geometry.txt`: physical medians, one-way arms and roundabouts. `Off` keeps byte-identical parity with the Java reference |
| `Seed` | pins the RNG to this value, unlike `RandomSeed` (below), which discards it. `--seed` sets the same thing |
| `NetworkRoadLength` | km of road used to size the roadside-object population. `auto` (the default) measures it from the loaded network; a number pins it, and `1.01` reproduces the Java original |
| `StatsDir` | where this run writes its CSVs and report (default `statistics`). Give each run of a batch its own, since every CSV is appended to |
| `PlaceName` | what to call the place in the GUI legend and the report heading; overrides the network's `place.txt` |
| `MaximumSpeed` | network speed limit in km/h. 60 by default, raised to 100 by Miami's and Riyadh's `defaults.txt` |
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

## Map imagery behind a network

The plan view can be drawn over the real map, the way a VISSIM model sits on an
aerial photograph. The imagery is not shipped and is never fetched by a run:
one command downloads it, and the GUI picks it up if it is there.

```bash
python fetch_basemap.py --list
```

```bash
python fetch_basemap.py --network kakrail_corridor --zoom 19
```

That writes `basemap.png` and `basemap.txt` into the network's folder. Start the
GUI and a **Background map** box appears in the legend; `M` toggles it. Both files
are gitignored, being megabytes apiece and rebuildable from the same command.

`--provider osm` gives the OpenStreetMap street rendering, © OpenStreetMap
contributors under the ODbL, and is what every shipped network carries.
`--provider esri` is the flag's default and gives aerial photography instead,
credited to Esri, Maxar and Earthstar Geographics. Either way the credit is
drawn in the corner of the view and printed under the report's animation.

`--provider google` uses the Google Static Maps API, with `--maptype terrain`
by default and `satellite`, `hybrid` and `roadmap` also available. It needs your
own key, read from `GOOGLE_MAPS_API_KEY` in the environment; the fetcher never
asks for one and never stores one. Know what it costs before choosing it: the
Static Maps API returns at most 640 pixels per request and stamps a Google logo
into every image, so a junction comes back as a grid of images each carrying its
own logo. That is a licence condition, not a bug to route around. The tile
servers Google's own site uses are not an option, since their terms cover access
through the APIs rather than those.

Photography looks like the better choice and is not, which is why every
network was moved off it. It loses on two counts. Dhaka's imagery is off-nadir
enough to show the *sides* of the towers, so the buildings lean across the
streets and a correctly placed road still looks like it runs over them. And a
photograph gives a reader nothing to check the fit against: a drawn map names
its streets, so a link sitting on the wrong one is obvious at a glance. What
photography does better -- showing where the running surface actually is,
rather than a generalised centreline -- is worth having while measuring a
roundabout or judging a fit, and one command puts it back.

`--tile-url` will use any other XYZ service you hold a licence for.

Esri is asked through its ArcGIS `export` endpoint rather than its tile path,
for two reasons: it takes a bounding box, so a junction is four requests instead
of a hundred, and it returns PNG. That second one is not a preference. Tk decodes
PNG, GIF and PPM and nothing else, so the JPEG the tile path serves could not be
read here at all.

Zoom 18 is about 0.55 m per pixel in Dhaka; zoom 19 is twice as sharp and is the
better choice for the four Dhaka junctions at the zoom the GUI opens on. Miami
and Riyadh are large enough that zoom 18 is the practical ceiling.

### Where the imagery gets placed

`make_network.py` projects longitude and latitude onto a local grid of metres
with **y increasing southwards**, the same sense as a web-map tile row. A
network's grid and a slippy map therefore differ by a translation and a scale,
with no rotation and no flip, so placing the imagery needs exactly one control
point. Each network's `geometry.txt` records the lat/lon its extract was built
around, and `dhakasim/basemap.py` matches that to a point in the network: the
declared roundabout if there is one, else the single busiest node, else the
middle of the bounding box.

All four surveyed Dhaka junctions come out anchored at (500, 500), which is a
useful accident — they were each drawn around that point, and the three rules
agreeing on it independently is what says the derivation is the one the networks
were built with. `tests/test_basemap.py` pins it.

**The anchor has to be read off the files, never off a running network.** It is
the mean of a node's arm endpoints, and by the time anything is on screen the
simulator has pulled every arm at a roundabout back to the edge of the ring, so
that mean is the centroid of six points scattered round a circle rather than
the junction it was measured at. `BaseMap.load` used to take the network its
caller handed it and therefore did exactly this, displacing the imagery by
7.1 m at Khamarbari and 5.2 m at Kakrail — half a carriageway, on precisely the
two networks with a roundabout and on no others. It now re-reads the survey
files for the anchor, and a test moves a network under it to check that the
imagery stays put.

One network cannot be placed and gets no basemap: `demo_backup` is the synthetic
network from the paper and is nowhere in particular. Pass `--centre lat,lon` to
supply a coordinate by hand for anything else, naming the junction the network
was drawn around.

**Banani 23's centre was 44 m out**, which drew every arm across the rooftops
and put the junction 34 m from the nearest road. The junction is the one
crossing in the OSM extract whose arms carry the names `node_names.txt` gives
ours — Road No. 23 to the north, Enamul Haque Chowdhury Road (Road 10) to the
south, and Kamal Ataturk Avenue both ways, towards Kakoli and towards Gulshan 2
— and its bearings match ours to twelve degrees. Corrected, the junction sits on
a road to within a centimetre and all four links fit at 100%, against two of
four before. A plain best-fit slide would not have found it: Banani is a regular
grid, so sliding the network onto the next block along scores just as well, and
the same search wrongly wants to move `banani_27` and `bijoy_sarani` by 24 m
each.

Banani is also where the case against photography was made. Its imagery is
markedly off-nadir — you can see the sides of the towers, not just their roofs
— so the buildings lean across the streets and a correctly placed road still
looks like it runs over them. A drawn map has no buildings to hide behind, and
all seven networks now carry one. `python fetch_basemap.py --network <name>
--provider esri --zoom 19` puts the photography back for one of them.

`banani_27` had no centre recorded either until its junction was worked out from
OpenStreetMap: its `node_names.txt` names the arms as Kamal Ataturk Avenue,
Banani Road 27, Kakoli and Gulshan 2, and Road 27 ends 6 m from both of the
avenue's one-way carriageways, so the midpoint of those two is the junction. It
lands on (500, 500) like the other four surveyed networks, which is the check
that it is right.

**Miami will not line up.** It is an idealised grid rather than surveyed
topology (see above), and ten of its twenty-two links have no real road under
them at all, so fitting half of them would look worse than fitting none. The
imagery is placed correctly; it is the network that is a stand-in.

Riyadh started the same way and has been fitted: nineteen of its twenty-two
links found a real arterial to sit on, so its lattice now follows the district
it was drawn from. `link.straight.txt` still holds the grid it was.

### What changes on screen

Over imagery the carriageway is filled solid white, the same areas as on a
blank canvas. It used to be washed at `Constants.OVERLAY_FILL_ALPHA`, on the
argument that a reader wants to see what is under the road. They do not. A
wash comes out as a dithered grey that reads as neither road nor map, and the
arms, the junction patch and the connectors all overlap at a junction, so
every overlap lands on a different shade and the junction ends up blotched
exactly where the picture matters most. Solid, the road is a road and the map
is context around it. `OVERLAY_FILL_ALPHA` is still the knob, and setting it
below 1 brings the wash back.

The outline changes colour too, to a near-black slate with mid-grey lane
dividers, and is drawn wider, since a quarter-metre kerb is a single pixel at
ordinary zoom.

Zoom snaps to fixed steps while the map is on. `tkinter.PhotoImage` rescales an
image by whole numbers only, and a fraction of a percent of error over a few
thousand pixels puts the map tens of metres off the roads, so the view is
rounded to a scale the image can hit exactly.

Which whole numbers get picked matters more than it sounds. Tk subsamples and
then zooms, so one surviving source pixel is painted as a square block whose
side is the zoom factor. Chasing the ratio nearest the requested scale finds
things like 16/13 — a third of a percent out, and sixteen-pixel blocks, which
looks like the imagery has been smashed. Since the view is moved onto whichever
ratio comes back, being near the requested scale is nearly free and block size
is not.

So `MAX_ZOOM` is 2, which is to say: essentially never magnify. Ask to zoom past
the imagery's own resolution and the view snaps back to it rather than enlarging
it, exactly as a slippy map stops at its last tile level. The cost is a coarser
ladder near native resolution — above it there is nowhere to go but doubling —
and that is the trade being made deliberately. Fetch at a finer `--zoom` to see
more, rather than asking Tk to invent it.

The report carries the imagery too, under its plan-view animation. The
animation is a flip-book that repeats the whole static layer in every frame, so
the picture is defined once in a `<defs>` block and each frame references it
with `<use>`; embedding it in the layer directly would multiply several
megabytes by the frame count. It is also shrunk to the size the report actually
draws it at, which keeps the addition to about a megabyte on a seven-megabyte
file. The 3D animation has no imagery: that would mean texturing the ground
plane, which is a different job.

## Fitting the links to the real roads

The surveyed Dhaka networks were transcribed by hand as one straight segment per
link, which is invisible on a blank canvas and obvious over imagery: an arm that
curves in reality is drawn as a chord across the curve, leaving the road and
coming back. `fit_roads.py` replaces each link's straight line with a chain of
segments following the road it stands for.

```bash
python fit_roads.py --network kakrail_corridor --dry-run
```

```bash
python fit_roads.py --network kakrail_corridor
```

**This changes the model, not just the picture.** A curve is longer than its
chord, so distances, journey times and every speed statistic move with it. The
four Dhaka networks gained between 1.5% and 4.5% of road length. Results from
before a fit and after it are not comparable, and the paper comparison in
`experiments/` was run against the straight networks. The original `link.txt` is
copied to `link.straight.txt` before anything is written, and `--restore` puts it
back.

What does **not** change: link ids, node ids, which nodes a link joins, and
carriageway widths. Those are what `demand.txt`, `path.txt` and `vehicle_mix.txt`
are keyed by, and they carry survey data, so nothing else in the folder needs
regenerating. The endpoints do move, along with the `node.txt` coordinates that
name them -- see below -- but only their positions, never their identities.

### How a link is matched

Not by routing. The obvious approach, a shortest path between the link's two
ends over the OSM graph, answers the wrong question: a link's ends are arbitrary
cut points that need not lie on a road at all, and where several routes join
them the shortest is often round the block. It turned a 260 m arm of Khamarbari
into a 382 m detour.

Instead the line is sampled every 8 m and each sample is pulled to the nearest
road **running roughly the same way**, within 35° by default. That bearing test
is what stops an arm being dragged sideways onto the street it crosses. The
pulls are smoothed so the result is a curve rather than a rattle, tapered to
nothing at a pinned end so it is approached without a kink, and capped so a
distant road cannot yank the link across.

A link is only reshaped if enough of it found a road (`--min-match`, 55% by
default) and the result is not much longer than what it replaces
(`--max-detour`). Anything else keeps its straight line and says so. One arm of
Khamarbari does: only 45% of it lies near a mapped road, so it stays a chord.
Lowering the threshold to fit it anyway was tried and made the network worse,
because the half with no road under it wanders.

### Dual carriageways

"Nearest road" is the wrong target for a link that stands for two of them. A
dual carriageway is two separate one-way ways in OSM with a gap between, and
one thirty-two metre link covers both -- so landing squarely on either half
scores as a perfect fit while the other half of the band sits on open ground.
Khamarbari's Manik Mia Avenue did exactly that, eleven metres off centre, and
read as the worst misalignment on the network while measuring as one of the
best.

A link whose `geometry.txt` declares a `median` therefore aims at the
*transverse midpoint* of everything parallel within reach, rather than at the
nearest of it. Extremes rather than an average: the two halves are digitised
with different numbers of vertices, and an average follows whichever is busier.

The pull is also **chased** rather than taken in one step. The target moves
with the line -- stand on one carriageway and the middle of the pair is half a
gap away, and having gone there the answer has changed -- and at Khamarbari the
far half starts outside the search radius altogether, so the first step finds
only one road and the second finds both.

Judge the result by the right question, too. Distance from the fitted
centreline to the nearest road is the obvious score and it rewards the very
mistake above; what a reader actually sees is what share of the *drawn band*,
sampled across its width, lands on a road the map paints. Centring Manik Mia
Avenue moved Khamarbari's median distance from 1.2 m to 3.1 m and its
painted-road share from 41% to 51%.

### When the chord is the right answer

Not every arm should be bent. The fit pulls each sample to the nearest road
running roughly its own way, and where a station forecourt, a slip road, a
plot access or the next street over runs alongside the real one at nearly its
bearing, that pull is real and wrong. Bijoy Sarani's western arm came out
wandering twenty-nine metres off its own chord between the Novo Theatre
forecourt and the metro approach; its northern arm did the same between
Agargaon Link Road and the Tejgaon ramps; Banani Road 27 picked its way
between Road 28 and a line of plot entrances into a curve the street has not
got.

A ``straight <link id>`` line in ``geometry.txt`` says so. The link keeps the
chord the survey drew, still follows its nodes wherever the fit moves them,
and the simulator ignores the directive entirely -- its reader skips
everything it does not recognise, so the same file serves both.

Straightening only helps if the junction is in the right place, which is worth
checking first: Bijoy Sarani's node sat 13 m south of the crossing, and no
straight line from the wrong point lands on the right street.

### Where the endpoints go

The interior of a link is the easy part: every sample moves on its own. The
ends are not, because all the arms at a junction share one point and a boundary
node repeats its arm's endpoint in `node.txt`.

Pinning them was the first answer and it was visibly wrong. The middles landed
on the road to within a metre or two while the ends stayed ten to forty-five
metres out, so each arm ran off its street exactly where a reader looks first:
Khamarbari's western arm left Manik Mia Avenue and finished inside a block of
flats.

So the fit runs twice. The first pass lets both ends go and only reads off
where each link would like them; the arms at a node then vote and the node
moves to their mean, capped at `--max-offset`. The second pass is the pinned
fit again, with the stated line's ends already on the node's new position,
which keeps the taper and the smoothness of the original approach. A link the
fit declines to bend still gets its endpoints moved, or it would tear open the
junction its neighbours just relocated.

One node never moves: the one `basemap.anchor_point` picks. The georeference
hangs off it -- it is the single control point tying network metres to the
Earth -- so moving it would slide the imagery along with the roads and gain
nothing whatsoever. The evidence that this is the right one to hold still is
that it barely wants to move anyway: across the five Dhaka networks every
junction node asks for less than five metres, while the boundary nodes ask for
up to thirty.

`node.txt` is rewritten alongside `link.txt`, for the nodes that state their
own coordinates, and `node.straight.txt` is the original. `--restore` puts both
back.

**A roundabout's arms are the exception.** They are not pinned to the node at
all. Because a stated line is a kerb, an arm's *mouth* -- the middle of its
carriageway where it meets the junction -- is half a width off to one side, so
six arms all ending on one coordinate leave six mouths pinwheeling around it,
by sixteen metres in Manik Mia Avenue's case. Each arm's end is instead slid
**along its own line** until its mouth is as near the circle as that line
allows. Sliding along costs nothing, because the simulator cuts every arm back
to the ring anyway and discards the tail; and the slack is spent keeping the
mean of the six ends exactly on the anchor, so the imagery does not move a
millimetre. Ten of Khamarbari's twelve kerb lines now reach the ring, against
nine before.

### The kerb-edge trap

A segment's stated x/y is one **kerb edge**, not the centreline, and the
carriageway lies to the left of the direction of travel. So the fit shifts the
stated line onto the centreline, bends that onto the road, and shifts it back.
Snapping the stated line straight onto a road centreline would leave every
carriageway half a width out, which on Manik Mia Avenue is seven metres.

Road data comes from Overpass and is cached as `osm_roads.json` in the network
folder, so a re-run costs nothing and the fit stays reproducible against the data
it was made from.

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

Roughly 5–6 minutes for the shipped 1800-step Dhaka network on a desktop CPU.
Runtime grows with the number of concurrent vehicles, so it is worse than linear
in `SimulationEndTime` — lower that while iterating. The GUI keeps up at the
default `SimulationSpeed 1`.

Two things dominate a step, and one of them was not traffic at all. The
near-crash log used to make a directory, open `accident_log.csv`, append one
line and close it again for every event — ten thousand times in four hundred
steps. It is now opened once and closed at exit; the file is still appended to
and never truncated. That, and asking each vehicle for its position once per
comparison instead of two or three times, took a step at Khamarbari from 27.3 ms
to 19.3 ms with 750 vehicles on the network. None of it changes a result: the
same seed produces a byte-identical end state.

The 3D view's cost is canvas items — Tk rasterises every polygon in software,
so the levers are fewer items and reusing the ones that do not change. Both
are pulled: while the camera is still, the sky, ground, roads and street
names stay on the canvas and only the vehicles are deleted and redrawn; a
vehicle too small to resolve is drawn as one block and, smaller still, as a
single dot rather than a modelled body; and sub-pixel details (wheels,
mostly) are skipped at mid distance. Measured on the Mohakhali network at
1920x991 with 191 vehicles and ~500 side-friction props: a solid-style frame
was 99 ms before, a full repaint is now 56–58 ms, and the steady-state frame
while watching a run is 38–43 ms at every zoom. A GPU cannot help: a Tk
canvas has no path to one.

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

```bash
python tests/test_signal_schedule.py
```

```bash
python tests/test_render3d.py
```

```bash
python tests/test_road_geometry.py
```

```bash
python tests/test_network_defaults.py
```

```bash
python tests/test_basemap.py
```

```bash
python tests/test_fit_roads.py
```

```bash
python tests/test_network_files.py
```

```bash
python tests/test_roundabout.py
```

```bash
python tests/test_start_screen.py
```

None of the suites runs the traffic model itself — that check is a seeded
headless run compared bit for bit against a known-good one, and it is one
command:

```bash
python hash_run.py
```

It runs every network headless with a pinned seed, hashes the console summary
and every CSV the run writes, and compares them per component against
`run_hashes.txt`, so a drift report names exactly which output moved. After a
change that is *supposed* to alter results has been verified, re-record the
baseline with `python hash_run.py --record`.

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
