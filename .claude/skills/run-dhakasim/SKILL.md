---
name: run-dhakasim
description: Build, run, and drive DhakaSim. Use when asked to start or run the simulator, launch the tkinter GUI, take a screenshot of the app, run a headless simulation, or verify a change works in the running app.
---

DhakaSim is a stdlib-only Python traffic microsimulator with a tkinter
GUI (settings screen → 2D plan view → 3D view). Drive the GUI via
`.claude/skills/run-dhakasim/driver.py` — it launches the real window,
steps the simulation itself, and screenshots every stage. Headless runs
need no driver at all.

All commands run from the project root: the simulator resolves
`input/` and `statistics/` from the current directory.

## Prerequisites

- Windows with a real display (the GUI driver grabs the screen; it is
  not headless-capable). Verified on Windows 11, Python 3.14.7.
- The project itself needs **no third-party packages** — that is a hard
  project constraint. The driver additionally needs Pillow for
  screenshots (`pip install Pillow`; 12.3.0 verified).

No setup or build step: clone/extract and run.

## Run (agent path) — GUI

```bash
python .claude/skills/run-dhakasim/driver.py
```

Takes ~15 s. It launches the window, then walks three stages, printing
progress and asserting at each:

| stage | what it does | screenshot |
|---|---|---|
| 1 | settings screen builds, network read from `network_var` | `debug/1_options.png` |
| 2 | `start_simulation()` (what the Start button calls), 80 steps driven manually | `debug/2_simulation.png` |
| 3 | `toggle_view()` to the 3D renderer, one more step | `debug/3_view3d.png` |

Expected output ends with `stepped to SimulationStep 81` and
`clean exit`. **Open the screenshots and look at them** — stage 2
should show vehicles on the Kakrail roundabout with a live legend
(≈60 vehicles at seed 1), stage 3 the corridor in line-art perspective.
`debug/` is gitignored.

To drive a different network, pass its `input/` folder name — the
screenshots get that name as a prefix:

```bash
python .claude/skills/run-dhakasim/driver.py banani_23
```

For other scenario changes (end time, seed), edit the two
`fields[...]` lines before `start_simulation()` in the driver.

## Run (agent path) — headless

For engine changes that don't touch drawing, skip the GUI entirely:

```bash
python run_dhakasim.py --headless --seed 1 --set SimulationEndTime=60 --set ReportAnimationFrames=0
```

~10 s. Prints the geometry lines (`medians on 2 link(s)`,
`roundabout at node(s) 4`) then per-class speed/waiting statistics and
exits 0. `--network <name>` picks any folder under `input/`;
`--set Name=Value` overrides anything `input/parameter.txt` understands.

## Run (human path)

```bash
python run_dhakasim.py
```

Opens the settings window (GUIMode On in `parameter.txt`); click
Start simulation. Close the window to stop.

## Test

```bash
python tests/test_javacompat.py
```

`all passed` — this is the Java-parity tripwire; if it fails, numerics
have drifted and nothing else can be trusted. The full suite is the 12
`tests/test_*.py` files run the same way, one process each (no pytest
needed); all 12 pass, ~1 min total.

## Gotchas

- **A zip/export copy has no basemap imagery.** `input/*/basemap.png`
  and `basemap.txt` are gitignored, and without them the 2D view has
  a blank background and no "Background map" checkbox. Rebuild with
  `python fetch_basemap.py --all --provider osm` (~1 min; use
  `--provider osm`, the street rendering — the script's esri default
  is photography, which the project deliberately moved away from).
  `buet_du_dmc` (the BUET-DU-DMC demo, formerly `demo_backup`) carries an
  owner-supplied screenshot as its basemap, which the fetch cannot
  rebuild — restore its `basemap.png`/`basemap.txt` from a backup copy.
- **`root.lift()` does not put the window above the focused app**, and
  `ImageGrab` photographs the screen — so a covered window screenshots
  as whatever covers it (verified: got a picture of the chat client).
  The driver sets `root.attributes("-topmost", True)` before shooting.

- **The GUI's own timer starves external drivers.** `DhakaSimPanel`
  re-arms `after(SimulationSpeed)`; at small delays `root.update()`
  may never return to your loop, and Windows eventually ghosts the
  window (black screenshots, `state=iconic`). The driver therefore
  cancels `panel._timer`, sets `panel._finished = True`, and calls
  `panel.action_performed()` itself. Any new automation must do the
  same — never loop on `update()` with the timer live.
- **Claim DPI awareness before the Tk root exists**
  (`ctypes.windll.shcore.SetProcessDpiAwareness(2)`). Without it a
  scaled display gives the process a virtual desktop: window
  coordinates and screen grabs are both in the wrong pixel space.
- **The settings screen sizes itself asynchronously.** `_fit_to_window`
  defers through `after_idle` until the canvas has a width; screenshot
  it before ~1.5 s of event pumping and you capture a half-arranged
  form. The driver's `pump(1.5)` after `DhakaSimFrame()` is that wait.
- **Runs have side effects**: `trace.txt` at the root and rows appended
  under `statistics/` (both gitignored, and `statistics/csv/` is
  append-only by design — delete the folder before a real experiment).

## Troubleshooting

- **`AssertionError: options screen did not build`** — a stale
  `parameter.txt` with `GUIMode Off` doesn't cause this (the driver
  builds the frame directly), but a missing `input/` does: you are not
  running from the project root, or the network named in
  `parameter.txt` (`Network kakrail_corridor`) has no folder under
  `input/`.
- **Screenshots show another application** — the simulator window was
  behind it. Make sure the `-topmost` attribute is set (the driver
  does this) before grabbing.
- **Blank/black screenshots** — the window was ghosted: the GUI's own
  timer starved the event loop (first gotcha in the section above).
