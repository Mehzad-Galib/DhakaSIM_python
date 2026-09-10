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

## The network files

`dhakasim/network_files.py` is the one home of the input-file grammar:
`link.txt`, `node.txt`, `geometry.txt` and the name files, every reader
UTF-8. It exists because the grammar used to live in four independent
readers and seven partial `geometry.txt` parsers, and three real defects
(cp1252 on Arabic street names, a BOM, a zero-length segment) were each
fixed in one reader while staying latent in the others. **Do not open a
network file directly; go through this module**, and put any new directive
or comment-borne fact in its `read_geometry`, where every consumer will
see it.

Two things about it are deliberate. The processor still builds its heavy
`Segment`/`Node` objects itself from the rows -- a `Segment` measures its
sensor and builds its strips in its constructor, which needs the run's
configuration and has no place in a file reader; parity was verified by
byte-identical seeded runs, not by eye. And the recorded anchor
(`# Centre <lat>,<lon> at <x>,<y>`) is honoured only on a line that
*starts* with `Centre`, because an unanchored match let prose in a
provenance note hijack the georeference. `tests/test_network_files.py`
pins both.

The writers live here too (`write_link_rows` / `write_node_rows`), shared
by `make_network` and `fit_roads`, and the zero-length defence travels
with them: consecutive points that round to the same whole metre are
collapsed, and a link that collapses entirely is refused -- the producer
must merge its end nodes (`make_network`'s collapse-merge loop is the
model). Byte-parity of both producers through the shared writers was
verified against their previous output, not assumed.

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
  horizontal line, which is why the ground is filled as screen-space
  rectangles rather than a projected quad — a stack of graded bands now, sky
  above the horizon and hazed ground below it, but still screen-space and
  still in the static layer.

**The frame is split into a static and a dynamic layer, by canvas tag.**
Everything drawn before `begin_dynamic()` — sky, ground, roads, markings,
node names — carries `Scene3D.STATIC_TAG`; everything after carries
`DYNAMIC_TAG`. While the camera, the window and the road geometry are
unchanged, `gui.paint_component` deletes only the dynamic tag and passes
`keep_static=True` to `begin_frame`, so the several hundred static items
survive on the canvas and only vehicles are re-created — the same economy the
report has always used in `RunRecorder._capture_3d`. Three rules keep it
honest: `begin_dynamic` flushes the kerb chain first (a chain flushed later
would carry the dynamic tag and vanish next frame); labels also carry
`LABEL_TAG` so a reused frame can `tag_raise` the static node names back
above the new vehicles; and the map furniture is tagged `furniture`, deleted
and redrawn every reused frame, or vehicles would stack above the compass.
The reuse decision is `_static3d_key` in `gui.py` — anything new that draws
per-frame-changing content into the *static* phase must either move after
`begin_dynamic` or join that key. A 2D frame clears the key, because its
untagged items must never be mistaken for a 3D static layer.

**Colour is two functions, and the split is deliberate.** `_shaded` is
brightness only and paints everything flat on the road, so the 3D roads stay
the exact 2D palette. `_lit` is for model faces: warm-tinted in sun,
cool-tinted in shade, and faded towards `HAZE_RGB` with distance (quantised
sixteenths, keyed into the cache). The haze factor is relative to
`camera.distance`, so it reads as depth at every zoom instead of kicking in
at a fixed range.

**Three LOD tiers, coarse to fine: speck, block, parts.** A prop whose
footprint projects under `SPECK_PIXELS` is one flat quad — never dropped,
because at a whole-network framing *most* props are specks and several
hundred dots are exactly how a wide view shows where the traffic is. Under
`LOD_PIXELS` it is one block. At full detail, any single part smaller than
`PART_MIN_PIXELS` (wheels, mostly) is skipped. `tests/test_render3d.py` pins
all of this, the tag contract included.

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

**The drawn bends are smoothed, the model's are not.** `_smooth_segs` rounds a
link's interior corners (capped corner-cutting, `SMOOTH_CUT_METRES`, two
passes) before the ribbon is offset and before the lane dashes are laid — both
must go through it or the dashes kink across the very bends the kerbs now
round. Endpoints never move, so junction mouths, stop lines and signal bars
stay exactly where the model puts them, and `segment_quads` stays unsmoothed
because the hull probing and stop-line rays reason against the model's own
segments. The cap is what keeps a vehicle on its straight segment from visibly
leaving the relaxed ribbon; verified drawing-only by a clean `hash_run.py`.

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

`statistics/report_*.html` is one self-contained file per run — stamped to
the **second** with a `_2` counter on collision, because a minute-resolution
name let a 60-step run overwrite the report of the run before it (found by
the 11 Sep QA sweep: seven runs, one file) — carrying **two
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
both animations. The 3D track is deliberately framed closer than the network
fit (`RunRecorder.CLOSE_3D`), trading the network's edges for vehicle models
big enough to read.

**`Statistics.flow` is not the flow.** The Java original's counter watches one
hardcoded sensor — link 0, 950 m in, reverse direction — which only the
retired synthetic network had, so on every shipped network `flow.csv` is
zeros and must stay that way (it is hashed). The report's dashboard reads
`Statistics.flow_series`, a report-only counter fed by every segment sensor
(the `update_information` call site in `_move_vehicle_at_segment_middle`),
rolled per minute alongside the parity one. Do not "fix" flow.csv from it.

The report's dashboard charts (donut, vertical bars, line, scatter) are
hand-built SVG in `report.py` with `<title>` children as the dependency-free
hover tooltips; the whole page is centred, and the per-link series reach the
report through the `series=` argument of `write_html_report`, filled in
`Processor._generate_statistics` where the link arrays already exist.

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

## Junction discipline and signal bars

`KeepClearMode` (default On, Off = byte-identical Java parity, pinned by
running `hash_run.py` with it Off before its baseline was re-recorded) is the
VISSIM-style junction behaviour, in three pieces:

- **Stop lines.** `Processor._set_stop_lines` stores a per-end setback on
  each mouth segment of every ≥3-arm non-roundabout node, read off **the
  drawn junction patch itself**: `road_geometry.stop_line_setbacks` casts
  rays from the mouth corners along each arm through the very outline
  `junction_hulls` paints (`junction_outline` is the shared piece), fillets
  included, plus `STOP_LINE_CLEARANCE_METRES` (6 m — one metre put the first
  vehicle's nose on the paint and the owner read it as queuing inside the
  box). A circle of crossing half-widths was tried first
  and landed every queue *inside* the patch — the patch reaches a full
  arm-width past the mouths before the fillets even start; on the big Dhaka
  junctions the line lands 20–35 m up the arm. **A box deeper than a short
  mouth segment walks its stop line back into earlier segments**: the
  distribution lives in `_set_stop_lines` (a segment wholly inside the box
  carries its full length as its setback, the first with room to spare
  carries the line; clamped to 0.85 of the *link* so it stays enterable),
  the phantom appears wherever the line is via
  `Vehicle._segment_carries_a_stop_line` (red only — mid-link green traffic
  must not brake for a rolling phantom it would never meet), `signal_bars`
  walks inward to draw the bar on the carrier segment, and the keep-clear
  box test reads the *whole* reach from `Segment.stop_total_at_*`. Banani
  23's east leg is the reason: a 15.6 m mouth against a 21 m box put the
  bar and the line in the middle of the drawn junction, and no cap inside
  the mouth segment could fix that.
  `Vehicle._create_dummy_vehicle_at_link_end` moves the red light's phantom
  leader back to that line, so queues brake to it instead of to the point
  where the surveyed arms converge — that convergence, not box occupancy,
  was most of the "clogged junction" look. A vehicle the phase change
  caught *past* the line is a **red-runner**: its phantom rolls (the green
  behaviour) and the second arm of the bundle-active test in
  `_move_vehicle_at_segment_end` lets it cross on red and clear the box,
  still subject to the keep-clear test — parking it in the mouth (the first
  design) read as a queue standing on the junction, and a phantom at the
  line behind a caught vehicle is a negative gap that freezes the approach.
  The red-runner arm is guarded on the leaving segment carrying a real
  setback, or an approach whose rays found no outline would run its red
  freely.
- **Keep-clear entry.** `Processor._exit_has_room`: the exit must offer clear
  road for this vehicle plus everyone already inside bound for overlapping
  strips, capped at the exit segment's own length, via
  `get_strip_index_in_entering_segment` with a `required_length` override —
  the *lateral search* is load-bearing: testing only the intended strips
  halved banani's completed trips. On top of that, the box-exit test asks
  VISSIM's question — would this vehicle have to *stop* inside the box? —
  by looking for a **standing** (< 0.5 m/s) vehicle within the exit's own
  stop-line setback plus clearance. It must not count moving vehicles:
  reserving that stretch as empty road throttled a green to one vehicle per
  box-crossing. Roundabouts and two-arm nodes are exempt, both measured,
  not argued (holding against ring space backs the corridor up; a chain
  joint has no cross traffic to keep the box clear for).
- **Signal timing.** The Dhaka networks' `defaults.txt` now pins
  `SignalChangeDuration 20` (miami/riyadh already had 30). The Java survey
  default of 1 s cycles the signal every step, which only moved traffic
  while queues packed the box; against real stop lines it discharges
  nobody. The measured price of the whole discipline at matched 20 s
  greens: banani −20% completed trips, saturated kakrail −40% — and 1 s
  box-packing chaos out-throughputs disciplined signals everywhere, which
  is a finding about oversaturated junctions, not a bug. Moving the stop
  lines a car length behind the drawn box (owner request, 27 Aug) deepened
  the price again — banani 111 → 81 completions in the 400-step harness —
  because the big Dhaka boxes put the line 20–35 m up the approach and
  every green pays the travel-up time. The green is also settable per run
  from the start screen ("s green per approach", under Signal control),
  following the network's defaults.txt on a junction change exactly as the
  speed limit does. Full Java parity
  needs `KeepClearMode Off` *and* `--set SignalChangeDuration=1` on the
  Dhaka networks; that combination was verified byte-identical against the
  pre-change baselines before `run_hashes.txt` was re-recorded.
- **Signal bars.** `road_geometry.signal_bars` draws one cased red/green bar
  per approach across the *approaching half* of the carriageway, at the stop
  line. It is dynamic content — signal state moves every few steps — so the
  GUI calls it after `begin_dynamic` and both report captures call it per
  frame; putting it in the cached road layer freezes the lights. The
  near-black casing is what stops a red bar reading as a car and a green one
  as a CNG. One-way links get no bar at their entry end; roundabouts and
  light `network_files` nodes (no bundles → `Node.is_signalised()` False)
  get none at all. `tests/test_signals.py` pins all of this.

## Node labels and the text halo

`draw_node_id` places a junction's name by probing candidate directions (the
midpoints of the angular gaps between the arms, widest first) against **the
drawn road itself** — the cached geometry's segment quads and junction
patches, via `_point_clear_of_roads`, testing the label point and the reach
of the text to either side. Probing idealised arm *rays* was tried first and
failed: a fitted arm bends right after its mouth, so a ray from the mouth's
bearing says nothing about where the road actually goes, and Banani 27's
name landed straight on its west approach. The answer depends only on the
network, so it is cached (`_label_dirs`, cleared when the geometry
rebuilds). Text halos are eight white copies (`_HALO_OFFSETS`, diagonals
included) — four offsets left pinholes at the corners over imagery.

Link names (usually the bare numeric ids) come off the carriageway the same
way: `road_geometry.link_label_offset` walks outwards perpendicular to the
link's middle segment, **both sides**, and takes the first spot where the
text — its horizontal reach *and* the row one text height up, since the
anchor is the baseline — lands on no quad and no hull. Both sides, because
`link.txt` states a kerb edge: one side clears in a step, the other must
cross the whole carriageway, and over imagery the widened geometry moves
both. The window (`_link_label_pts`, cleared with `_label_dirs`) and the
report (`visualize.py`'s `link_labels`) share the function, so the two
pictures agree. Fallback when nothing within reach is clear is the old
on-road midpoint.

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
the suites do not run the traffic model. That check is now one command:
`python hash_run.py` runs every network headless (seed 7, 90 steps) and
compares the console summary and every CSV against `run_hashes.txt`,
per component, so a drift report names the output that moved. Re-record with
`--record` only after a change is *verified* — recording is the same statement
as updating `test_javacompat.py`'s expected values. The HTML report is
deliberately not hashed; it carries wall-clock timestamps.

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
(`fill_polygon`, `fill_oval`, `draw_oval`, a ribbon, an arrow, `flush`,
`begin_dynamic`). Paint order in this renderer is call order, so a kerb still
sitting in the buffer while a junction patch is filled comes back out on top
of it.

**Measured again 26 Aug 2026**, after the static layer, the speck tier and
the part cull went in (see Scene3D above). One frame of the demo network
(the folder now named buet_du_dmc, then demo_backup, and at measurement
time still holding the Mohakhali network, before Shahbag replaced it) at
1920x991, 191 vehicles plus ~500 side-friction props, `paint_component` +
`update_idletasks`, median of 24: solid was 99 ms at 2371 items; a full frame
is now 56-58 ms at ~960 items and a reused-static frame — the steady state
while the camera is still — 38-43 ms, at close zoom as well as wide. The
remaining cost splits roughly evenly between Python (projection and
`_stand_model`) and Tk's own rasterising, so the next saving, if one is ever
needed, is again fewer or simpler items, not faster arithmetic. The
benchmark harness is `benchmark_3d.py` in the session scratchpad.

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

## The map-import dialog

`dhakasim/map_import.py` is the logic — location parsing, Nominatim
geocoding, the Overpass fetch with its mirror list, and `ImportJob`, which
drives the chain — and `_ImportDialog` in `gui.py` is the window over it,
opened by **Import map…** in the start screen's *footer* (a footer button
costs no height, and the density ladder has none to give). Five facts:

- **The scripts are run as subprocesses, not imported.** The chain is the
  canonical one — `make_network.py`, `run_sim.py`, `fetch_basemap.py`,
  `fit_roads.py` — and the package must not import a top-level script (the
  `basemap.Projector` rule). `PYTHONIOENCODING=utf-8` is forced on every
  child, or the first Bengali street name kills it on a cp1252 console.
- **The worker thread never touches Tk.** Inputs are read from the
  `StringVar`s on the Tk thread before the thread starts; everything back
  travels through a queue the dialog polls with `after`.
- **An imported network is not part of the parity contract.**
  `hash_run.py` skips any `input/` folder containing `osm_extract.geojson`
  — the import's provenance file — because a fresh fetch of the same place
  returns different OSM data. Do not record baselines for imports.
- **A failed import removes its half-built folder** (a folder with a
  `node.txt` becomes a start-screen tile, and a broken tile crashes the
  run), but a failed basemap fetch or road fit only warns — the network is
  runnable without them.
- **The default name is the user's typed text, not the geocoder's display
  name** — Nominatim answers in the local language, and a Bengali display
  name slugifies to an empty folder name (found the hard way: the first
  Mirpur import landed in `input/imported`).
- **Imported maps are their own start-screen groups, and only they are
  removable.** The dialog's Kind choice is written to `imported.txt`
  (`kind single|multi`; missing file reads multi, which is what the
  pre-question imports were grouped as) and decides which "Imported
  (…)" group the tile joins. The "remove selected…" link sits inline
  with those groups' captions — a row of its own would cost height —
  and deletion is double-gated: the affordance only exists on imported
  groups, and `map_import.remove` refuses any folder without the
  `osm_extract.geojson` marker, so survey data cannot be deleted from
  the GUI at all. `test_start_screen.py` measures its density rungs
  with imports filtered out — a user's imports legitimately grow the
  form and step it down a rung, which is the ladder working, not a
  regression.
- **Every import pauses for the junction picker.**
  `ImportJob` is two stages — `fetch_and_build` (Overpass + make_network)
  and `finish` (run_sim, metadata, basemap, fit) — with `run` calling
  both for a headless caller; the GUI never uses `run` any more, both
  kinds pick. Between them the dialog draws the staged
  network over the preview tiles (`map_import.read_network_shape`
  inverts the `# Centre lat,lon at x,y` anchor to lay network metres on
  web-mercator tiles; a junction's drawable position is the mean of its
  arms' endpoints, since the file stores junctions at (0,0)) and the
  user clicks a junction, then toggles legs.
  **A leg is a walked road, not a link.** The staged network is OSM's
  graph cut at every crossing, so the links touching the chosen node
  are its first fragments — at Palashi, 6 m and 18 m, because a
  dual-carriageway crossing is a square of OSM nodes and fusing leaves
  stubs between them. The first picker kept those fragments as the
  legs, and the import came out as one 330 m arm plus two stubs with a
  sliver of basemap under it. `map_import.junction_legs` now merges
  every crossing-node within `JUNCTION_CLUSTER_M` of the chosen one
  into the junction, starts a leg on every link leaving that cluster,
  and walks it outward through side-street junctions along the
  continuation that turns least (under `LEG_MAX_TURN_DEG`), stopping
  at a boundary node, a sharper turn, a revisited node or a link
  another leg claimed; legs come back clockwise by leaving bearing.
  **A multi import is the same picker with a set of junctions.**
  `network_legs(links, nodes, junctions)` clusters each chosen node in
  turn (a node already inside an earlier cluster *is* that junction and
  gets no entry), and a walk that reaches another chosen cluster stops
  there with `end_junction` set — the leg joins the two, and the walk
  back from the other side finds its links claimed, so a corridor is
  the junctions along it clicked in turn. `check_legs` is the gate:
  every junction two legs, and the junctions one connected piece via
  kept joining legs — `run_sim` routes between boundary nodes and an
  island nothing reaches is a route it cannot build. The dialog calls
  it *before* spawning the finish thread, because a refusal from the
  worker discards the staged folder and the user's pick with it;
  `prune_to_junctions` calls it again as its own defence. The
  roundabout strip shows for a single junction only.
  `prune_to_junctions(folder, legs, centres, roundabouts)` then writes
  **one link per leg**, junction nodes `0..J-1` at (0,0) in the order
  chosen, boundary nodes after, a joining leg running centre to centre,
  each leg's points starting at the cluster centroid (vertices inside `MOUTH_CLEAR_M`
  dropped, so the arms converge on a point the way the survey
  networks state a junction — `_open_the_circle` and the junction
  patch both rely on that), width length-weighted and median the max
  over the walked links (make_network's own chain rules), one-way
  only if every walked link is and all run the same way, written in
  the direction of travel. `geometry.txt` keeps its leading comment
  block — the georeference anchor — and regenerates the directives.
  Pruning must run *before* `finish`, which regenerates routes and
  demand from the final link set. A single-kind import also fetches
  its basemap with `--margin 200`, because the arms end at the circle
  and a 60 m margin left half the window bare.
  Three more things the single path does, each found at Shahbag.
  **Slip roads are dropped** (`fetch_and_build` strips every `*_link`
  class for a single junction): the model has nothing to do with a
  slip road, and at Shahbag they fused into a hairpin and a loop, left
  the west arm dangling 10 m short of the crossing, and the junction
  came out with two legs — the same extract without them is four
  crossing-nodes and four arms. **Boundary nodes within the cluster
  radius join the cluster**, because a dangling arm end that close is
  a reconnection the build missed, not a road that ends there. And
  **choosing Single sets the roads preset to "+ local streets"** along
  with the 300 m radius: a junction's legs are often tertiary (Shahbag's
  north arm), a 300 m extract stays small whatever it includes, and
  with only the main roads the crossing offers two or three legs. The
  picker pre-selects, among the dots within 80 m of the crosshair, the
  one whose walk yields the most legs (nearest on a tie) — the nearest
  dot alone can be a fork a few metres from the crossing that was aimed
  at — and the status line turns red with a nudge when a chosen
  junction has fewer than three. Abandoning the pick
  (Close) discards the staged folder — it has a `node.txt` and no
  demand, i.e. a broken start-screen tile. Links are drawn with a dark
  casing and junctions as amber dots because Farmgate proved a bare
  line blends into the map and OSM's red hospital icons read as
  markers. The kind strip also drives the radius default (300 m single
  / 1000 m multi) — the owner's complaint was a "single intersection"
  drawn from a kilometre of city at 200 m scale. The picker pre-selects
  the junction nearest the import point (waiting for a first click read
  as broken), takes its clicks on *release* so a slipped click never
  reads as a pan, and thickens the leg under the pointer. A "This
  junction is" strip (signalised / roundabout) appears only while
  picking: OSM maps Palashi as a plain crossing, and Roundabout makes
  `prune_to_junction` append a `roundabout <node> 8 7` directive — the
  same converging-arms-plus-directive form the surveyed kakrail uses,
  so `_open_the_circle` builds the ring. Dragging the preview pans it
  (tiles move live; on release the centre point becomes the import
  point and the Where box is set to its coordinates — the box is the
  single source of truth for what Import uses). `run_sim.py` exits with
  a sentence instead of a ZeroDivisionError when one-way legs leave
  fewer than two usable boundary nodes — found by keeping three one-way
  legs at the BUET fork. Tile fetches try tile.openstreetmap.de after
  .org (the main host throttled a day's worth of previews into connect
  timeouts), each tile failure degrades to a dark square, and
  `preview_view` computes the projection without any tiles at all, so
  an outage never blocks the pan or the picker.
- **The preview is plain OSM tiles on a `tk.Canvas`** —
  `map_import.preview_tiles` picks the deepest zoom whose radius circle
  still fits two thirds of the panel, caches tiles under
  `input/.tilecache/preview_osm/` (its own drawer, not fetch_basemap.py's —
  their cache keys differ and sharing would couple the package to a
  script), and the dialog holds the `PhotoImage`s in a list or Tk drops
  them mid-frame. A generation counter discards a slow fetch that a radius
  click has superseded, and the radius strip re-renders the preview live
  through the same queue the import uses — the dialog runs one `after`
  pump for its whole life.

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

**The run screen and the report wear the same palette now.** The run view's
toolbar, legend panel, sliders and spinner all read their colours from `_UI`,
and the ttk rule above extends to them: every widget there is plain `tk`
(`_ToolButton` is the toolbar's `ttk.Button` replacement — a label with the
start screen's fills and hover, answering `configure(text=…)` and
`configure(state=…)` so the callers that flip the pause caption or enable the
report button did not change). The HTML report's CSS carries the same values
**copied**, not imported — `report.py` cannot import the GUI — so a palette
change means editing `_UI` and the `:root` block in `report.py` together.
The one place the light look survives is inside the animation frames: the
plan/3D pictures are daylight scenes and stay so, framed by the dark cards.

**The legend's collapsible sections re-pack with `after=`.** Vehicles on
network, Side friction and Run settings each carry a minimise button; a
section expanded again is `pack`ed with `after=its own header`, because a
plain re-pack appends it below every section built later — collapse
Vehicles, expand it, and it came back underneath Run settings. The Side
friction section (its own header since 28 Aug, one hue per type — vivid
yellow / sky blue / lavender / pale green, the old single yellow family was
indistinguishable) is built only when `Parameters.OBJECT_MODE` is on, and
the report drops its side-friction key under the same test: a key for
objects the run never generated is a lie on Miami and Riyadh.

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

Measured at 96 DPI, the rungs are 812 / 751 / 681 / 421 pixels of settings
against viewports of 816 (1080p), 644 (1536x864), 535 (720p). So a 1080p
screen gets `roomy`, this machine's own desktop gets `dense`, and 720p gets
`minimal`. Roomy sits four pixels under its budget, and that is deliberate:
the signal-timing line (27 Aug) had to come out of roomy's paddings
(`row_pad` 6 → 4, `pad` bottom 22 → 12, header 102 → 96) because a full
captioned row pushed 1080p off the top rung, and packing the timer beside
the mode strip instead widened the column enough to stack the form at
1280 px. Anything else added to the form must pay for itself the same way.
The side-friction switch (28 Aug) paid by not being a row: it shares the old
Pedestrians row (now titled "Side friction", two Off/On strips side by side,
labelled `peds` / `parked`), which costs zero height — and both of its first
two drafts failed the suite before fitting: labels one word longer
(`crossing`, `parked / standing`) widened the right column enough to stack
the form at 1280 px, and a caption one wrapped line longer than the old
row's pushed roomy to 828 against its 816. Short labels and a one-line
caption are load-bearing, not style. The row is trimmed like its
neighbours: `_trimmed_rows` is a **three**-tuple (time, pedestrians, side
friction), hidden where defaults.txt pins `ObjectMode` (miami, riyadh).

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
