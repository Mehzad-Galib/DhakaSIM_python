# DhakaSim (Python) — working notes

`README.md` documents what the simulator does, every parameter, the input and
output files, and a list of long-standing behavioural quirks under **Behaviour
worth knowing about**. Read it first; this file only covers what the README
does not.

## Where you are

Two sibling folders, easy to confuse:

| Path | What it is |
| --- | --- |
| `E:\Projects\DhakaSIM` | the **Java original**. `src/`, `build.xml`, `Dhakasim.jar`. Not a git repo. |
| `E:\Projects\DhakaSIM_python` | **this project**, the Python port. The git repo. |

Sessions are sometimes started in the Java folder by mistake — if the shell
starts there, `cd` here first. Run everything from this directory: the
simulator resolves `input/` and `statistics/` relative to the current
directory, not to the package.

## Hard constraints

**No third-party dependencies.** Standard library only; the GUI is `tkinter`,
the report is hand-built SVG. Python 3.10+. This is why the report animations
are pure-CSS SVG flip-books rather than real GIFs, and why there is no
matplotlib/numpy anywhere. Do not add a dependency to solve a problem — the
existing code shows the stdlib-only way to do it.

**Java parity is pinned by tests.** `dhakasim/javacompat.py` reproduces Java's
arithmetic where Python differs: NaN-propagating `min`/`max`, half-up rounding,
truncating int casts, `Infinity`/`NaN` instead of exceptions, sign-of-dividend
`%`. These look like bugs and are not.

```bash
python tests/test_javacompat.py
```

If that fails, the numerics have drifted — fix the drift, don't update the
expected values. `GeometryMode Off` must stay byte-identical to the Java
reference.

## The drawing-surface protocol

The single most important design fact. `Vehicle.draw_vehicle`,
`RoadsideObject.draw_object`, `Pedestrian.draw_mobile_pedestrian` and
`road_geometry.paint` all draw through a *surface* object rather than to a
specific output. Three implementations exist:

| Surface | Module | Output |
| --- | --- | --- |
| `CanvasGraphics` | `gui.py` | the live 2D plan view, on a `tkinter.Canvas` |
| `_SVGGraphics` | `visualize.py` | the report's 2D animation, as SVG |
| `Scene3D` | `render3d.py` | the 3D view — both the window and the report |

**A new view is a new surface, never a change to the simulation code.** All
three take the same calls (`set_color`, `set_stroke`, `set_font`, `draw_line`,
`fill_polygon`, `fill_oval`, `draw_oval`, `draw_string`, `begin_prop`,
`end_prop`). Add a method to one and you must add at least a no-op to the
others, or trace replay and the report will break in ways the tests do not
catch.

`begin_prop(kind, type_index)` / `end_prop()` tell a surface that the next
footprint is a solid object rather than road markings. The calls live in the
three places that drive a paint — `gui.py`'s paint loop, `utilities.draw_trace`
for replay, and `RunRecorder._capture_3d` for the report — deliberately *not*
inside `vehicle.py`, so the simulation classes stay untouched. 2D surfaces
no-op them; `Scene3D` uses the type to pick a 3D model. `type_index=None` means
"infer from the footprint", which is what trace replay needs, since `trace.txt`
records corners and a colour but not the vehicle type.

## Scene3D

Reads a vehicle's four footprint corners as a frame — `corner0` rear-left,
`corner0→corner1` along the body, `corner0→corner3` across it — and stands a
per-type model on it. Models live in `VEHICLE_MODELS` / `OBJECT_MODELS` in
`render3d.py`; each is a list of boxes in normalised body coordinates. Edit
those to change how a vehicle looks.

Correctness rests on three assumptions. Breaking any of them causes rendering
artefacts that no test will catch:

- **Model parts are declared bottom-up.** That ordering *is* the paint order,
  and it is correct for any camera above the road. Only whole vehicles are
  depth-sorted against each other.
- **Every part is a convex box**, so its visible faces never overlap each other
  on screen and need no sorting among themselves.
- **The camera never rolls.** The ground's horizon is therefore exactly a
  horizontal line, which is why the ground is filled as a screen-space
  rectangle rather than a projected quad.

Two pieces of arithmetic worth knowing before touching the camera:

- The horizon lands at `height/2 - focal*tan(pitch)`, and `focal` is
  `(height/2)/tan(fov/2)`. So **sky is visible only while pitch < fov/2**. At
  exactly half the field of view the horizon sits on the top edge and the
  ground fills the frame.
- Projected coordinates are clamped (`Scene3D.COORD_LIMIT`). A vertex just past
  the near plane projects arbitrarily far out, and a canvas cannot hold a
  coordinate of a few million — it wraps and the polygon tears.

## Junction geometry

`road_geometry.build` returns **six** lists: quads, junction hulls, islands,
turn connectors, lane markings, carriageway ribbons. Adding a seventh breaks
`paint`, `gui.py`'s cache and `visualize.py`'s unpack at once, so change all
three together.

**The carriageway is painted from the ribbons, not the quads.** A link is a
chain and only one of its two edges is shared: neighbours meet exactly at their
stated ends, but each offsets its far edge along its own normal, so a bend of
theta leaves the two far corners `2 w sin(theta/2)` apart. The fitted networks
average a twenty-degree bend per joint, which on a fourteen-metre road is a
five-metre wedge missing at every one. `carriageways` offsets the chain as a
whole and mitres the interior corners; `MITRE_LIMIT` cuts the corner square
before a near-hairpin sends the mitre to infinity. The quads are still built
and still returned, because `junction_hulls` probes against them and a quad is
convex where a ribbon is not.

Lane dividers are paint, not model. The simulator has no lanes; they are drawn
at `LANE_WIDTH_METRES` spacing because 0.5 m strip boundaries would be
hatching. Do not let anything in `vehicle.py` consult them.

Two functions in this module take a segment's kerb corners, and **both** must
test segment length before calling `Utilities.return_x3`, which divides by zero
on a zero-length segment: `_arm_at_node` and `lane_markings`. Both were caught
by tests rather than by review.

A segment's stated x/y in `link.txt` is one **kerb edge**, not the centreline;
the carriageway lies to one side. An arm's mouth centre is half a width off the
coordinates in the file. This trips up anything that reasons about where a road
actually is, and `tests/test_road_geometry.py` pins it.

Corner fillets clamp their cut-back to half the shorter adjacent edge, so a
large `CORNER_RADIUS_FACTOR` degrades gracefully instead of letting one corner
eat its neighbour.

Each hull tuple carries **six** fields, the last being the stretches of its
outline to draw as kerb. `junction_kerb` decides that by stepping outwards from
the patch centre at each edge's midpoint: landing on a road quad means the edge
is a mouth and must stay open, landing on nothing means it is kerb. Without
that stroke the arms' kerbs stop in mid-air and the junction reads as
unfinished, which is the whole reason it exists.

Turn connectors are drawn but are mostly *inside* the hull, because a Bezier
lies within the convex hull of its control points and those are the arm mouths
pulled inward. They change the silhouette only where a turn swings wide. Do not
expect them to smooth a junction on their own. `_arm_at_node` must test segment length *before* asking for
kerb corners: a zero-length segment divides by zero inside
`Utilities.return_x3`.

## When a network will not sit on its imagery

Check the georeference before blaming the fit. The test that found Banani 23's
44 m error in one line: **a junction node should be ~0 m from the nearest OSM
road** (a roundabout node reads as its outer radius instead, which is why
Khamarbari shows 24 m and Kakrail 15.5 m). Banani 23 read 34.4 m.

Do **not** fix it by sliding the network to the best-fitting offset. On a grid
that lands one block over and scores better; the same search wants to move
`banani_27` and `bijoy_sarani` by 24 m, and both are already right. Match the
junction by the *names and bearings* of its arms against `node_names.txt`
instead — `scratchpad`-style, but the rule is: names first, bearings second,
distance last.

Banani's imagery is off-nadir enough to show building sides, so towers lean
across the streets and a correctly placed road still looks wrong. That is why
`banani_23` carries the OSM street rendering instead of photography.

## The basemap

`basemap.py` puts real map imagery under the 2D plan view. Four facts about it
are not visible from the code.

**Georeference off the files, never off a live network.** `BaseMap.load` used
to take the `link_list` its caller handed it, and every caller — `gui.py`,
`visualize.py` — hands it the *processor's*, by which time
`Processor._open_the_circle` has pulled every arm at a roundabout back to the
edge of the ring. The anchor is the mean of a node's arm endpoints, so it
became the centroid of six points scattered round a circle: **7.1 m out at
Khamarbari and 5.2 m at Kakrail**, half a carriageway, and zero everywhere
else — which is exactly why it looked like a Khamarbari problem for months
and produced the phantom "the OSM roundabout is 6.3 m off" measurement. Where
the ground truth sits is a property of the survey data and nothing a run does
to it may move it. `test_a_moved_network_does_not_drag_the_imagery_with_it`
pins it, and any *diagnostic* script must call
`basemap.georeference(net, *basemap.read_network(net))` rather than reuse a
`Processor`'s lists, or it will measure the same ghost.

**The georeference rests on one control point, and it is load-bearing.**
`make_network.py` projects lon/lat to metres with **y increasing southwards**,
which is also how a web-map tile row runs, so the two grids differ by a
translation and a scale with no rotation. That is why a single anchor suffices.
`basemap.Projector` is a *copy* of `make_network.Projector`; the package must
not import a top-level script, so `tests/test_basemap.py` pins the two against
each other. If they drift, every basemap slides and nothing raises.

The anchor is the roundabout `geometry.txt` declares, else the uniquely busiest
node, else the bounding-box centre. All four surveyed Dhaka junctions land on
(500, 500) under this rule, which is the evidence that it is the rule they were
built with rather than one that merely happens to work.

**The zoom snaps, and it has to.** `tkinter.PhotoImage` rescales by whole
numbers only. `BaseMap.snap` rounds the view scale to something the image can
hit exactly, because a 1% miss over a few thousand pixels is tens of metres of
drift. `scaled_region` crops to the viewport *before* scaling — scaling the
whole image would need a hundreds-of-megabytes intermediate — and snaps the crop
outwards to whole subsample blocks, because Tk starts sampling at the crop's own
origin and a crop beginning mid-block makes the imagery creep while panning.

**`_ratio` optimises crispness, not accuracy, and the difference is visible.**
Tk subsamples then zooms, so the zoom numerator *is* the on-screen block size in
pixels. Searching for the ratio nearest the requested factor finds things like
16/13 — a third of a percent out, and sixteen-pixel blocks. Because `snap` moves
the view onto whatever ratio comes back, distance from the request costs
nothing, so the search ascends by zoom and takes the first ratio inside
`RATIO_TOLERANCE`.

`MAX_ZOOM` is 2: essentially never magnify. That is the single knob controlling
how the imagery looks, and raising it brings the blocks straight back. It also
sets the zoom ladder's coarseness near native resolution — above factor 1 there
is nothing but doubling — so the two are one trade, not two. `test_basemap.py`
pins both halves.

The first loop in `_ratio`, returning an exact hit, is not an optimisation:
`snap` feeds its own output back in on every repaint and every toggle of the
imagery, and without it the answer for an exactly-reachable factor differs from
the answer that produced it, walking the zoom along a step at a time.

**Every network carries the OpenStreetMap street rendering**, not aerial
photography. Photography was the default and is still one command away
(`fetch_basemap.py --network <n> --provider esri --zoom 19`), but it loses on
two counts: Dhaka's imagery is off-nadir enough to show building *sides*, so
towers lean across the streets and a correctly placed road looks wrong, and a
photograph gives the reader no names to check the fit against. A drawn map has
labelled streets, and a link that is on the wrong one is obvious at a glance.

**Over imagery the road is drawn wider than the model.**
`Constants.OVERLAY_WIDEN_METRES` (6 m) is added to every carriageway by
`road_geometry.widened`, which `build(..., widen=)` wraps the link list in.
A rendered road width is a cartographic choice -- OSM draws a trunk road about
eleven metres wide whatever it measures -- so a surveyed carriageway laid over
it leaves casing sticking out along both kerbs, and that reads as the model
being misplaced when it is not. The simulation never sees it: strips, capacity
and every statistic come from the real width. It lifts the share of painted
road we cover at Khamarbari from 56.5% to 61.6%. `gui.py` keys its geometry
cache on the value, because toggling the map changes the shape.

**Over imagery the road is painted solid**, via `road_geometry.paint(fill=False)`
— the flag now only swaps in `overlay_fill_color`, `overlay_border_color` and
`overlay_marking_color` and widens the kerb. It used to drop the opacity to
`Constants.OVERLAY_FILL_ALPHA` as well. Do not put that back without reading
why it went: junction patches, connectors and arms all overlap, so a wash lands
on three different shades within one junction and blotches it, and Tk's stipple
dither turns the road into hatching at report scale.

`set_alpha` stays in the drawing-surface protocol — `CanvasGraphics` dithers
through a Tk stipple, `_SVGGraphics` emits real `fill-opacity`, `Scene3D`
ignores it — and at 1.0 the canvas takes the fast path and never builds a
stipple at all.

Miami is an idealised grid, so its map is placed correctly and its roads still
will not line up with it. That is the network, not the basemap.

## Two scores, and they disagree on purpose

`scratchpad`-style diagnostics measure alignment two ways and the difference
matters:

- **on painted road** -- of the drawn band, sampled across its width, what
  share stands on road the map paints. Falls when the band is widened, and
  cannot exceed roughly `2 x casing / width` on a dual carriageway because the
  central reservation is not painted.
- **painted road covered** -- of the paint near the network, what share we
  cover. This is the one a reader is judging when they say "there is orange
  sticking out beside my road", and the one widening improves.

A diagnostic that classifies map pixels must know OSM Carto's whole palette:
white minor, salmon trunk, light-orange primary, **pale yellow secondary**,
pink motorway, grey casing. Leaving secondary out scored Kemal Ataturk Avenue
at 0.0% when it was sitting squarely on it, which sent an afternoon chasing a
fit that was already right.

## "The roads do not line up with the map"

Measure before believing it, and measure the right line. `link.txt` states one
**kerb**; the carriageway is drawn a full width to one side of it, offset by
`(-dy, dx)/|d| * w` exactly as `Utilities.return_x3` does. So the line to
compare against the map is the stated line plus **half** that -- compare the
stated line itself and every network reads four to six metres out when it is
not.

**Measure it with the right question.** Distance from the fitted centreline to
the nearest OSM road is the obvious score and it is the wrong one, because a
band sitting squarely on one half of a dual carriageway reads as a perfect
fit while half of it lies on open ground. The honest question is what share
of the *drawn band* -- sampled across its width, not just along its middle --
falls on a road the map actually paints. The two disagree in the direction
that matters: centring Manik Mia Avenue on its median moved Khamarbari's
median distance from 1.2 m to 3.1 m and its painted-road share from 41% to
51%.

| network | on painted road | nearest road, median |
| --- | --- | --- |
| kakrail_corridor | 64.0% | 1.2 m |
| bijoy_sarani | 48.0% | 0.6 m |
| banani_23 | 26.8% | 4.9 m |
| banani_27 | 23.7% | 1.7 m |
| riyadh | 17.9% | 5.0 m |

Miami reads 19.2 m and always will: ten of its twenty-two links have no real
road under them, which is why it is deliberately not fitted. **banani_23,
banani_27 and bijoy_sarani all declare `median` links and have not been
refitted since the dual-carriageway rule went in**, so their scores are the
old ones; refitting them would change every statistic they produce, which is
not a decision to take casually.

Khamarbari's fit was once swept across five settings -- wider bearing, looser
match, tighter simplification -- and every one came out worse. That sweep is
still valid for those knobs. It is not an argument against the two things that
did move the number: the dual-carriageway rule and the basemap fix below.

**What a reader sees as misalignment is usually one of three other things.**
The OSM rendering draws a trunk road as a wide coloured casing whose width is a
cartographic choice, so it peeks out alongside a correctly placed carriageway.
Several streets on the map -- flyovers, slip roads, service roads -- are simply
not in the network. And `link 4` at Khamarbari is left straight on purpose:
only 45% of it lies near a road, under `--min-match`. Fitting it anyway at
`--min-match 0.40` was tried and made the network *worse* (50.3% against
50.8%), because the half of it with no road under it wanders.

**The OSM roundabout loop agrees with us, and an older note here said it did
not.** Ignore any figure of 6.3 m: it came from a georeference computed off a
*live* network, which is the bug described under "The basemap" below, and the
displacement it was measuring was ours, not OSM's. Measured properly, the
`junction=roundabout` loop at Khamarbari fits a circle of radius 27.3 m to
0.06 m rms, centred **1.4 m** from ours, and a circle fitted to the island
kerb the map draws separately comes back at 14.2 m centred **0.1 m** from
ours. Two independent features, both agreeing the centre is right.

## Fitting links to real roads

`fit_roads.py` reshapes `link.txt` so links follow the roads instead of cutting
across them. Six things about it are load-bearing.

**It changes the model.** Curves are longer than chords, so every statistic
moves. `link.straight.txt` is the original and `--restore` brings it back. The
`experiments/` paper comparison was run on the straight networks.

**It preserves everything the survey data is keyed by** — link ids, node ids,
widths — so `demand.txt`, `path.txt` and `vehicle_mix.txt` stay valid and
nothing needs regenerating. Endpoints move, and `node.txt` moves with them; see
"Where a fitted link's endpoints go" below for why that is not optional.

**Matching is projection, not routing.** Shortest path over the OSM graph was
tried first and is wrong: link ends are arbitrary cut points that need not touch
a road, and the shortest route between two of them frequently goes round the
block (a 260 m Khamarbari arm became 382 m). The line is sampled, each sample
pulled to the nearest road *within 35° of the link's own bearing*, and the pulls
smoothed and tapered. The bearing filter is the part that matters: without it a
sample near a junction snaps to the crossing street.

**It works on the centreline, not the stated line.** `link.txt` states one kerb
edge with the carriageway to the left of travel, so the fit offsets by +w/2,
bends, then offsets by -w/2. Skip that and every road lands half a width out.
`offset_polyline` is not an exact inverse at a bend, but the error is
centimetres against a fourteen-metre carriageway.

**A link with a `median` aims at the middle of a pair, not the nearer of
them.** `geometry.txt`'s `median` line is the network's own statement that the
link stands for a dual carriageway — two OSM ways with a gap — and
`spanning_road_point` takes the transverse midpoint of everything parallel
within `w/2 + SPAN_MARGIN`. Without it Manik Mia Avenue sat squarely on its
southern carriageway with the northern one eleven metres outside the band, and
scored as a perfect fit the whole time. Extremes, not a mean: the two halves
are digitised with different numbers of vertices and a mean follows whichever
is busier.

**The pull is chased, and the fit is not.** The target moves with the line —
stand on one carriageway and the middle is half a gap away; go there and the
answer has changed — so `snap_to_roads` re-aims two or three times before
returning. Repeating the *whole* fit instead was tried and is worse: it does
not converge, and each repeat lets links drift onto whatever is nearest by
then, so Khamarbari grew from +4.5% of network length after one round to +8.9%
after six while the painted-road share barely moved (50.8% to 53.3%). Chasing
inside `snap_to_roads` gets the same placement for +4.5%, because the
smoothing and the simplification still happen exactly once.

## The report

`statistics/report_*.html` is one self-contained file per run, carrying **two
animations of the same captured frames** — plan view and 3D — and no static
snapshot. Both are CSS flip-books; `RunRecorder._film` gives each a distinct
class prefix so two on one page do not collide.

The 3D track renders through `Scene3D` pointed at `_SVGCanvas`, a stand-in for
`tkinter.Canvas` that records SVG. That is why the report cannot disagree with
the window about the camera or about what a bus looks like. Sky, ground, roads
and node names are rendered once into a static layer; only vehicles are
re-rendered per frame.

The plan-view animation carries the basemap when the network has one. It is
defined once as `<defs><image id="dsbasemap">` and referenced from each frame
with `<use>`, because `_film` repeats the whole static layer per frame and
embedding the picture there multiplied it by the frame count -- 7 MB became
26 MB before that was caught. `BaseMap.png_bytes` shrinks it to the size the
report draws it at first.

`ReportAnimationFrames` is the main control over report size — every frame is
stored in full, so a 23-frame run is roughly 3.5 MB. Set it to `0` to disable
both animations.

## Where a fitted link's endpoints go

`fit_roads.py` fits in **two passes**, and the reason is not obvious from the
code. Pass one lets both ends of every link go free and throws the result away;
all it wants is where each link would put its own ends. The arms at a node then
vote, the node moves to their mean, and pass two is the ordinary pinned fit
onto the new node positions. Pinning the ends to the survey coordinates -- the
first version -- fitted the middles to a metre or two and left the ends ten to
forty-five metres off the road, which is what a reader actually sees.

**The anchor node is pinned and must stay pinned.** `basemap.anchor_point`
picks it, `georeference` maps it to the recorded lat/lon, so moving it drags
the imagery with it by the same amount and changes nothing except that every
other fitted link goes out of alignment. That it barely wants to move is the
check that the georeference is sound: junction nodes ask for under five metres
across all five Dhaka networks.

**A roundabout's arms are not pinned to the node**, which sounds like it
breaks the rule above and does not. Their ends are placed afterwards by
`aim_arms_at_the_circle`, under the constraint that *the mean of the stated
ends still equals the anchor exactly* — the arms end in six different places
now, but their centroid is where it always was, so the imagery is untouched.
The freedom that buys this is the along-the-arm one, which costs nothing
because `_open_the_circle` throws away everything inside the ring. The
solution is the minimum-norm one: `sum(t)` subject to `sum(t * direction)`
hitting the residual, which is two equations in as many unknowns as there are
arms. After it, `link.txt`'s whole-metre rounding leaves the anchor about
0.2 m off (500, 500) — `test_surveyed_junctions_all_sit_at_500_500` allows
half a metre for exactly this and nothing more.

`node.txt` is rewritten too, for nodes whose stored coordinate equals their
arm's endpoint, which is exactly the test that tells a boundary node from a
junction stored at (0, 0). `node.straight.txt` backs it up; `--restore` puts
both files back.

Miami is deliberately **not** fitted, and `--all` will fit it: ten of its
twenty-two links have no real road under them at all, so fitting half of them
looks worse than fitting none. Riyadh *is* fitted -- 19 of 22 links matched --
so `--all` is now safe for everything except Miami. Both keep their
`link.straight.txt`, and the `experiments/` paper comparison was run on the
straight geometry.

## The report's framing

`RunRecorder` sizes the frame from two extents, not one. The carriageway alone
decides the centre; everything drawn -- node names especially -- decides the
size. Sizing from one list slid the junction sideways by however far the
longest street name overhung, which was a couple of hundred pixels at
Khamarbari. Half the `pad` goes into the translation as well as into the size,
or the picture comes back off centre by half a pad.

`_fit_3d_camera` does the same thing in the 3D track: the ground rectangle's
projected centre is what the camera is aimed at, while the width it fits is a
*reach* out from that aim rather than a plain bounding box, so a name
overhanging one side pulls the camera back instead of swinging it across. The
vertical aim stays at `H3 * 0.57` on purpose -- that is the band of sky.

## Roundabouts

`GeometryMode` is **On** by default now; Off is the Java-parity mode and with it
off there is no roundabout in the model at all.

**geometry.txt's radius is the island, and the second number is the ring.**
Khamarbari is **14 + 17**, Kakrail 6.5 + 9. Khamarbari's has been measured
three times and got it wrong twice, so read this before changing it again.

The number to trust is the **island kerb the map draws as its own feature**:
walk outwards from the circle centre on every bearing and it reads 12.2–15.5 m,
and a circle fitted to it comes back at 14.2 m centred a tenth of a metre from
ours. The outer edge of the painted running surface reads about 31 m between
the arms.

The two wrong answers, and why. The OSM `junction=roundabout` loop is 27.3 m
and its *widest* point, 28.8 m, was once recorded as the island — that made
the circle half again too big. Then 12 + 12, measured off aerial photography,
which is defensible but put the whole ring **inside** the island the map
draws: our running surface ran 12→24 m while the map paints the roundabout
from 14 out to 31, so vehicles circulated over what a reader sees as the
island. Neither error was visible without putting a fitted circle on the
rendering and looking at the numbers.

**A roundabout is swept, not assembled.** `roundabout_outline` returns the ring
and every arm's throat as one closed polygon, by sampling the union's radius
angle by angle. Do not go back to drawing a circle and patching around it —
aprons, flares, fillets, hulls have all been tried and the record is below.

Read the numbers before touching it. `scratchpad`-style, per kerb line at the
node, the perpendicular distance from the circle centre: at Khamarbari **ten of
the twelve** now cross the ring and two miss it, by 5.1 and 10.1 m. The misses
are the far kerbs of the wide arms — Manik Mia Avenue is 32 m wide arriving at
a 62 m circle. Their outer halves have nothing to join on to and no drawing
trick invents one.

**The mouths have been re-aimed, and an older note here said not to.** The
convention was the problem: every arm's *stated line* used to end exactly at
the node, and since the carriageway hangs a full width off one side, the
mouths pinwheeled round the node by half a width each — sixteen metres for
Manik Mia. The note said re-aiming them would drag the arms off their roads,
and that is true of the obvious way (swinging each arm to point radially).
`fit_roads.aim_arms_at_the_circle` does not do that. It moves the end **along
the arm's own line only**, which is free, because `_open_the_circle` discards
everything inside the ring anyway; across the arm the road still decides. The
count above, nine of twelve to ten of twelve, is that change.

The same step keeps the mean of the stated ends exactly on the anchor, so the
imagery does not move. That constraint is the reason it slides along the arms
rather than simply placing each end where it likes.

**Do not cut the arms deeper either.** `set_arc` takes its radius from the
arm's endpoint, so an arm cut deep enough to look right puts traffic back on
the island.

**The arms are pulled back, and everything else depends on it.**
`Processor._open_the_circle` cuts every arm where it crosses
`island radius + circulatory width` and lets the region inside become the ring.
Without it the survey's geometry has all the arms ending at the same point --
the middle of the island -- so `IntersectionStrip.set_arc` gets entry and exit
points a few metres from the centre, bends the path into an arc *inside* the
island, and vehicles are drawn standing on it. That is what the picture showed
before this existed.

Segments are **rebuilt, not edited**: `Segment.__init__` works out its sensor
position and its strips from its endpoints, so moving them afterwards leaves
that stale. `Link.clear_segments` exists for this and nothing else.

The centre is recorded on the node (`set_centre`) *before* anything moves, and
`road_geometry.roundabout_centre` reads it back. `node_point` is wrong here:
once the arms end on the circle their endpoints lie on it, and the mean of
points on a circle is only its centre when they are evenly spread.

**A corner of a union cannot be rounded by shrinking one of its polygons.**
This is the single fact that killed three earlier attempts, and it is why the
smoothing is a morphological closing rather than a fillet. `fillet_polygon` on
an apron cuts material the arm's ribbon does not replace, and what comes back
is a crescent of bare ground — the very gap the apron existed to close.
`tests/test_road_geometry.py` still catches it, as
`test_closing_a_notch_only_ever_adds`.

**`_close_notches` is a closing, dilate then erode, and both halves matter.**
The closing of a set contains the set, so nothing that was road stops being
road. And it is selective by width: the sharp V where a mouth meets the ring is
narrower than `ROUNDABOUT_CORNER_DEGREES` and goes, while the kerb island
between two neighbouring arms is wider and stays. Raise that constant far and
the arms merge into one blob — which is exactly what a convex hull of the
mouths gives, tried and rejected: it paves the ring over and leaves the island
floating in the middle of it.

**The outline squares its corners off rather than cutting across them.** Where
the radius jumps between two samples the boundary is a mouth's outer end, not a
kerb turning, and joining the two with a plain chord slices up to a metre of
road off exactly the place a reader looks to see whether the arm is joined on.
`ROUNDABOUT_CORNER_JUMP` is where a slope stops and a corner starts.

**`junction_kerb` probes along the edge normal, not the radius.** On a convex
hull the two agree, because its edges run across the arms. A roundabout's
outline also runs *along* them, and there a radial probe slides down the kerb
instead of stepping off it, so the answer comes back as a coin toss and the
kerb draws speckled.

`junction_hulls` returns that outline for a roundabout with `radius=0` in the
disc field — the outline already follows the arms, so the rounding disc has
nothing left to reach. `turn_connectors` skips roundabouts entirely.
`islands` returns **four** fields now, the last being the outer radius;
`visualize.py` unpacks it.

The island is painted opaque in both modes. It is the one place the imagery is
deliberately covered, and the justification is that it is the one part of the
picture nobody can drive on.

## Traffic signal scheduling

`dhakasim/signal_schedule.py` implements Rahaman et al., IEEE Access 2025.
Four facts about it that the code cannot tell you.

**The optimiser must never draw from `Parameters.random`.** That stream decides
which vehicles are generated and when, and it is what makes a seeded run
reproduce the Java original. `seed_from` hands out a separate `random.Random`,
and `test_the_optimiser_leaves_the_simulation_random_stream_alone` puts a
tripwire on `Parameters.random` to keep it that way.

**`SignalMode fixed` is the untouched code path.** `Processor._control_signal`
still calls `constant_signal_change` for it, so Java parity needs no argument.
Everything new hangs off `scheduled_signal_change`. Note that
`_run_at_each_time_step` polls the controller every step in a scheduled mode,
because `SignalChangeDuration` is doing double duty in fixed mode -- it is both
the green and the polling interval, and leaving it as the interval would round
every optimised green up to a multiple of it.

**Two degenerate cases bit during development and both are pinned by tests.**
An empty junction scores zero on both objectives for *every* candidate, so the
front is the whole population and the pick is arbitrary -- which at step 1 of a
run handed one approach a 525-second green that then never came round to be
revised, since re-planning only happens at the end of a cycle. Fixed by
breaking ties on the shorter cycle, which can never be worse on either
objective, and by `_trim_empty_approaches`: an approach with nothing on it
discharges nothing however long it is held, so it gets the minimum green.

**Equation 4 is ambiguous and the literal reading is the best one.** Measured,
not argued: `C(i)` as the arriving count beats `C(i)` as the count remaining
after the cycle (degenerate -- clearing everything scores zero on both
objectives and the vehicle-class weight stops doing anything at all), and beats
reading `R(i)` as the red before an approach's green rather than its total red.
Do not re-litigate this without re-running the numbers; the harnesses are in
`scratchpad`-style scripts and the results are in README.

`moo-v1` beats fixed-time on the mixed-traffic networks and `moo-v2` does not,
which is the opposite of the paper's ordering. The mechanism is written up in
README under "What it measures on the shipped networks" -- briefly, Equation 4
reduces to `cycle − Σ C(i)g(i)/Σ C(i)`, which for a fixed cycle length is
minimised by giving the whole cycle to the busiest approach. That is a real
property of the objective, not a bug.

## Speed

Measured, not guessed. Two rules if you touch the inner loop.

**The accident log is opened once and kept open** (`vehicle.accident_log`,
closed by `atexit`). Reverting it to open-append-close per row costs 40% of a
simulation step: a near crash is a common event, ten thousand rows in four
hundred steps, and each one was a `makedirs` plus an open and a close. It still
appends and never truncates.

**Every change in here has to be bit-identical.** `Strip.probable_leader` and
its neighbours were rewritten to ask each vehicle for its distance once instead
of twice, which means the strict `<` that keeps the first of equal candidates
and the NaN comparisons that silently drop a vehicle both had to survive
untouched. The check is a seeded run hashed end to end, not the test suite —
the suites do not run the traffic model.

**The 3D view is Tk-bound, not Python-bound**, and the way to speed it up is
to hand the canvas fewer items. One frame of Khamarbari, 444 vehicles, is
111.7 ms in the original solid style, of which 41.9 ms is Python. Line art plus
the road-line economies below bring it to 84.7 ms and cut canvas items from
2645 to 1137; Python barely moves, because none of the saving was ever there.

An older note here quoted 73 ms a frame with 17 ms of Python. Ignore it. The
harness that produced it drew vehicles without wrapping them in
`begin_prop`/`end_prop`, so `Scene3D._stand_model` was never reached and what
it measured was the flat footprint path, not the 3D view. Any benchmark of
this renderer has to wrap its vehicles the way `gui.py`'s paint loop does.

**The two things that actually made it faster** are both about item count.
`Render3DStyle line` draws one silhouette per model part instead of three
shaded faces. And `Scene3D.draw_line` collects a run of connected lines into a
single polyline, then drops any line that ends up *alone* and projects shorter
than `MIN_LINE_PIXELS`. That second rule is aimed at the lane markings -- 979
items at Khamarbari against 204 for every kerb in the network, three pixels
each -- and the "alone" part is load-bearing: kerb segments are short too, and
dropping one on length alone gaps the outline.

The chain buffer has to be flushed before anything else reaches the canvas
(`fill_polygon`, `fill_oval`, `draw_oval`, a ribbon, an arrow, `flush`). Paint
order in this renderer is call order, so a kerb still sitting in the buffer
while a junction patch is filled comes back out on top of it.

## Development environment gotchas

Both of these cost real time to diagnose; neither affects normal interactive
use.

- **The display runs at 300% scaling.** A DPI-unaware process sees a virtual
  640x409 desktop, and both Tk geometry and any screen grab work in those
  coordinates. Call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` before
  creating the root window in any screenshot harness.
- **The animation timer starves external drivers.** `DhakaSimPanel` re-arms an
  `after(SimulationSpeed)` callback, and at `SimulationSpeed 1` `root.update()`
  may never return. A driver looping on `update()` will hang, and Windows
  eventually ghosts the window — it reports `state=iconic` at `+-32000+-32000`
  and screen grabs come back black. To drive the GUI programmatically, cancel
  `panel._timer`, set `panel._finished = True`, and call
  `panel.action_performed()` yourself.

## Conventions

- Comments explain *why*, especially where the code looks wrong but is
  deliberate (Java-parity arithmetic, the paint-order assumptions above). Match
  that density — the codebase is heavily commented by design.
- British spelling in prose and comments (`colour`, `centre`, `behaviour`).
- `statistics/csv/` is appended to, never truncated. Delete the folder between
  experiments or every CSV grows a row per run.
- Commit only when asked.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Khamarbari is gone

`input/khamarbari/` was removed on the owner's instruction (24 Aug 2026): the
survey data was inconsistent and its road geometry had been refitted. It sits
in `removed_networks/khamarbari/` rather than being deleted, because
`basemap.png`, `basemap.txt` and `osm_roads.json` are gitignored and are not
recoverable from history; the other files are. Delete that folder when you are
sure.

**The engineering notes above still name it, and that is deliberate.** They
record *why* the code is shaped as it is -- the basemap displacement, the
dual-carriageway rule, the swept roundabout outline, the mouths being re-aimed
-- and every one of those reasons was found on Khamarbari and still governs
`kakrail_corridor`, which is now the only roundabout. Deleting the network
does not make the reasoning wrong; deleting the reasoning would lose it.

`kakrail_corridor` has taken over as the fixture in `test_basemap.py`,
`test_fit_roads.py`, `test_roundabout.py` and `test_network_defaults.py`.

## The start screen

Rebuilt as a settings screen, in **two columns**, and the column count is the
whole design constraint: stacked in one column these settings run to about
1700 pixels and a maximised window on a 1080p screen has roughly 810 to give
them. `_arrange` measures rather than guesses — it puts the columns side by
side while the canvas is wider than they ask for and stacks them when it is
not, because the form has no horizontal scrollbar and a layout wider than the
window would simply lose its right half. The scrollbar shows itself only when
it is needed, and that check runs on the **tray's** resize as well as the
canvas's: the canvas stops resizing long before the settings finish building,
so a check made only there decides while the form is still short.

How much fits is decided by measurement at run time — see **Height is not a
budget any more** below.

**`_Segmented` is the vocabulary**, and it is not a borrowed game-UI flourish:
this simulator has no lanes, a carriageway is a row of half-metre strips, and
a row of cells with one filled is the shape the model is already made of.

It renders **from** its `StringVar` via `trace_add`, so setting the variable
redraws it — which is how `_on_network_change` moves the speed limit without
knowing the widget exists, and why `option_panel.network_var.set(...)` now
gets the refresh a click gets. The old combobox binding fired on neither.

Two flags on it are load-bearing. `keep_unknown` shows a value matching no
cell as an extra cell rather than snapping it to the nearest, because a
surveyed network is entitled to a speed limit nobody thought to offer — but it
must be **off** where several controls share one variable, as the two junction
groups do, or the selected network is drawn in both rows. And `wheel=`: the
cells are destroyed and rebuilt on every change, which takes their bindings
with them, so anything else bound to a cell has to be re-bound in `_render`
the same way.

**Three Tk traps are already paid for.** `self._options` is `tkinter.Misc`'s
own method — shadowing it breaks every grid call the widget makes, hence
`_choices`. ttk's scrollbar and combobox both take their colours from the OS
under the Windows theme and ignore yours, so the form uses a classic
`tk.Scrollbar` and a `_Dropdown` built from a `tk.Label` and a `tk.Menu`;
switching the app to the `clam` theme to fix that would restyle the simulation
panel too.

**Start is in a footer outside the scroller.** An earlier version put it at the
top of the form for the same reason — the settings were taller than a short
window, so a button below them was a button nobody could see. Captions moved
under their labels at the same time, which is why they wrap now: in the old
third column a wrapped caption pushed the next row down by however many lines
it took.

The junction tiles are grouped single-intersection against multi. Kakrail is a
two-junction corridor and sits with the single ones because that is how it is
studied — a pair of adjacent signals, not a grid.

**The header is a canvas, and it has to be.** The title sits on a glow, and
tkinter has no per-widget alpha, so the glow can only be *painted*: a
`tk.Label` over it would punch an opaque rectangle through the middle of the
thing it is meant to sit on, while canvas text does not. `_paint_header`
draws sixty concentric filled ovals from the outside in, the outermost one
the ground colour itself so the bloom has no edge to give it away. A dozen
steps shows rings at this contrast; sixty does not.

**Glass here is two hairlines, not transparency.** `_glass` places a
one-pixel highlight along a surface's top edge and a shadow along its bottom,
which is what translucency actually looks like and is all Tk can do. It uses
`place`, so it can be applied to a finished widget without disturbing the
geometry manager its real children use — that is the whole reason it is a
function and not a widget. Every gloss colour comes from `_mix`, the
surface's own colour lifted towards white, so a control that changes colour
(selected, hovered) keeps its highlight in step without a second table of
colours to maintain.

**The captions were unreadable and the fix was the palette.** They were drawn
in `faint` (`#6E6662`) on `panel` (`#232120`) — about 2.4:1, under any
threshold worth naming. `muted` and `faint` both moved a long way up and the
captions moved from one to the other. Section titles are `accent_text`, a
lighter tint of the same green as Start.

**Height is not a budget any more, it is a search.** `_DENSITIES` holds four
ways to draw the same seventeen settings and `_fit_to_window` walks them from
the roomiest, stopping at the first one whose form fits inside the canvas.
Nothing asks how big the monitor is: a maximised window is the desktop less
its title bar and its taskbar, and the DPI setting, the taskbar's edge and
its auto-hiding all move that number. The check is
`tray.winfo_reqheight()` against `canvas.winfo_height()`.

Measured at 96 DPI, the rungs are 793 / 707 / 640 / 444 pixels of settings
against viewports of 819 (1080p), 644 (1536x864), 535 (720p). So a 1080p
screen gets `roomy`, this machine's own desktop gets `dense`, and 720p gets
`minimal`.

**`minimal` drops the captions, and it has to.** 720p leaves about 1070
pixels of settings across two columns and the roomiest layout wants 1570.
Seventeen captions at two lines each *are* that difference, and no amount of
shaving fonts and padding closes it while every row carries two lines of
explanation. They move to a hint line in the footer, shown while the pointer
is on a row — one row's height for all seventeen of them instead of two lines
each. A fifth rung with 8 pt captions was built and measured at 635 against
`dense`'s 640: the wrapped junction tiles ate everything the smaller type
saved, so it was removed rather than kept as a rung that never wins.

Three things make the search work, and all three were bugs first:

- **It must measure a laid-out canvas.** It runs from the panel's own
  `<Configure>`, which is inside the very geometry pass that would give the
  canvas its width, so the first call sees one pixel, finds the roomiest
  layout too wide, stacks it, and steps down a rung it never needed. It now
  defers to `after_idle` until the canvas has a width.
- **`_arrange` short-circuits on its last answer**, and the columns are a
  different width at every density, so `_stacked` is cleared before each
  trial. A stale "stacked" never fits and sends the search to the bottom.
- **Stacking is not a rung.** It doubles the height, so the search steps past
  it — and a denser layout is often narrow enough to come apart again, which
  is exactly what happens at 1280 wide.

**A rebuild throws away the widgets and keeps the values.** Every `StringVar`
is made once in `_make_vars`; `_init_components` only draws. That is also why
`_Segmented` holds its trace and gives it back on `<Destroy>` — a strip
destroyed by a rebuild that is still watching its variable fires into a dead
widget the next time anything writes it. `tests/test_start_screen.py` pins the
fit on six window sizes, the values surviving a rebuild, and the trace count.

**Font sizes are in points, so the layout depends on the display.** The
simulator never claims DPI awareness, so Windows hands it a 96 DPI world and
scales the result — which is why a harness that *does* claim awareness
measures a form a quarter larger than the user's and every number it prints is
fiction. Pin `tk scaling` to 1.3333 before the first widget exists when
measuring this screen.

Both panes stretch to the taller of the two (`sticky="ns"`), because one pane
stopping short of its neighbour reads as an unfinished layout rather than as
a shorter list of settings.
