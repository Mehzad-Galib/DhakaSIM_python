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

`ReportAnimationFrames` is the main control over report size — every frame is
stored in full, so a 23-frame run is roughly 3.5 MB. Set it to `0` to disable
both animations.

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
