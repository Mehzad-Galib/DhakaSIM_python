"""Pin the start screen's fit on a range of monitors.

The settings are taller than a short window, so the screen carries a ladder of
densities and picks the roomiest one the window can show whole.  Three things
about that are easy to break and expensive to notice:

* the *search* itself.  It measures, and it has to measure a laid-out canvas
  -- an earlier version ran inside the panel's own ``<Configure>``, before Tk
  had given the canvas a width, judged the roomiest layout against a one-pixel
  canvas and stepped down a rung it never needed.
* the *values*.  Changing density rebuilds every widget, so anything the
  reader has typed has to live in a variable that outlives the rebuild.
* the *traces*.  Every choice strip watches its variable; a strip destroyed by
  a rebuild has to stop watching, or the next write reaches a dead widget.

Font sizes are given in points, so what fits depends on the display's DPI.
These tests pin ``tk scaling`` at 1.3333 -- 96 DPI, a monitor at 100% -- which
is what the numbers in the density table were chosen against.

Needs a display.  Run from the project root with
``python tests/test_start_screen.py``.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk

# Ask Windows for the real pixels rather than a scaled-down desktop, so a
# 1920-wide window can actually be opened here.  Together with the pinned
# scaling below this is exactly a 1080p monitor at 100%, which is what the
# density table was measured against.  On a display already at 100% it
# changes nothing.
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:                       # not Windows, or already set
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import gui  # noqa: E402
from dhakasim import map_import  # noqa: E402
from dhakasim import utilities as Utilities  # noqa: E402

# The density rungs below were measured against the shipped networks alone.
# A user's imported maps legitimately grow the Intersection block and step
# the form down a rung -- that is the ladder doing its job, not a
# regression -- so the measurements here exclude them.
_REAL_NETWORKS = gui.Processor.available_networks


def _shipped_only():
    return [n for n in _REAL_NETWORKS() if not map_import.is_imported(n)]


gui.Processor.available_networks = staticmethod(_shipped_only)

#: Pixels per point at 96 DPI.  Pinned before any widget exists, because the
#: first layout is measured at whatever is in force when it is built.
SCALING = 1.3333

#: A maximised window is the screen less its title bar and its taskbar.  These
#: are the client areas that leaves, which is what the form actually gets.
MONITORS = (
    ("1080p", 1920, 1000),
    ("1600x900", 1600, 820),
    ("1536x864", 1536, 784),
    ("768p", 1366, 688),
    ("720p", 1280, 640),
    ("720p, deep taskbar", 1280, 580),
)


_REAL_TK_INIT = tk.Tk.__init__


def _pinned(self, *args, **kwargs):
    """A root whose font scale is fixed before anything is drawn on it."""
    _REAL_TK_INIT(self, *args, **kwargs)
    self.tk.call("tk", "scaling", SCALING)


def panel(width, height):
    """A start screen in a window of the given size, laid out and settled."""
    Utilities.initialize()
    # As the program launches: the preview steps a real run, and a run
    # needs the parameter-dependent constants (pedestrian arrivals) built.
    from dhakasim.constants import Constants
    Constants.initialize()
    tk.Tk.__init__ = _pinned
    try:
        frame = gui.DhakaSimFrame()
    finally:
        tk.Tk.__init__ = _REAL_TK_INIT
    root = frame.root
    root.state("normal")                # it opens maximised
    root.geometry("%dx%d+0+0" % (width, height))
    for _ in range(30):
        root.update_idletasks()
        root.update()
    frame.option_panel._fit_to_window(force=True)
    root.update_idletasks()
    return frame


def _desktop():
    root = tk.Tk()
    size = (root.winfo_screenwidth(), root.winfo_screenheight())
    root.destroy()
    return size


def test_every_monitor_shows_the_whole_form():
    """The pictures' column is always there, and the settings fit whole
    beside it down to 1366 px.  At 1280 px the settings become one column
    beside the pictures -- a game's list-and-picture layout -- and that
    column may scroll; that is the one width where it may."""
    desktop = _desktop()
    for name, width, height in MONITORS:
        if width > desktop[0] or height > desktop[1]:
            print("     (skipped %s: this desktop is %dx%d)"
                  % (name, desktop[0], desktop[1]))
            continue
        frame = panel(width, height)
        option = frame.option_panel
        wanted = option._tray.winfo_reqheight()
        room = option._canvas.winfo_height()
        bar = option._bar.winfo_ismapped()
        pane = option._preview.width
        shown = option._preview.frame is not None and option._preview.frame.winfo_ismapped()
        frame.root.destroy()
        assert shown and pane >= 260, "%s: no pictures beside the settings" % name
        if width < 1366:
            continue
        assert wanted <= room, (
            "%s: %s wants %d px and has %d"
            % (name, option._d["name"], wanted, room))
        assert not bar, "%s: scrollbar showing at %s" % (name, option._d["name"])


def test_a_big_screen_gets_the_roomiest_layout():
    """The ladder is walked from the top, so a screen with room to spare must
    land on the first rung and keep its captions."""
    desktop = _desktop()
    if desktop[0] < 1920 or desktop[1] < 1000:
        print("     (skipped: this desktop is %dx%d)" % desktop)
        return
    frame = panel(1920, 1000)
    option = frame.option_panel
    name = option._d["name"]
    captions = option._d["caption"]
    pane = option._preview.width
    stacked = option._stacked
    frame.root.destroy()
    # With a 460 px picture column beside them the two columns stand side
    # by side at compact (the tiles two abreast); roomy is 1738 px wide
    # and cannot.  Captions stay on the rows.
    assert pane >= 400, "a big screen must give the pictures a wide column"
    assert name in ("roomy", "compact"), name
    assert captions, "a big screen must keep its captions"
    assert stacked is False, "a big screen shows two columns of settings"


def test_a_short_screen_keeps_the_explanations_somewhere():
    """The captions come off the rows to fit 720p -- 17 of them, two lines
    each, is more height than that screen has.  They move to the footer."""
    frame = panel(1280, 640)
    option = frame.option_panel
    assert not option._d["caption"], option._d["name"]
    assert option._hint is not None, "no hint line to put the captions in"
    assert option._hint_rows, "no row registered its explanation"
    widgets, text = option._hint_rows[0]
    widgets[0].event_generate("<Enter>")
    option.frame.update_idletasks()
    shown = option._hint.cget("text")
    frame.root.destroy()
    assert shown == text, (shown, text)


def test_a_narrow_screen_gives_the_pictures_their_room():
    """At 1280 px two columns of settings cannot stand beside a picture
    column, so the settings become one column (scrolling if it must) and
    the pictures keep their width: the list-and-picture layout of a
    game's settings menu."""
    frame = panel(1280, 640)
    option = frame.option_panel
    stacked = option._stacked
    pane = option._preview.width
    frame.root.destroy()
    assert stacked is True, "1280 px: one column of settings beside the pictures"
    assert pane >= 400, "the pictures keep their column"


def test_a_rebuild_keeps_what_the_reader_typed():
    frame = panel(1920, 1000)
    option = frame.option_panel
    option.fields["end_time"].set("777")
    option.fields["seed"].set("4242")
    option.network_var.set("banani_27")
    for level in range(len(gui._DENSITIES)):
        option._busy = True
        option._level = level
        option._build_form()
        option._busy = False
        option.frame.update_idletasks()
    end, seed, network = (option.fields["end_time"].get(),
                          option.fields["seed"].get(),
                          option.network_var.get())
    frame.root.destroy()
    assert (end, seed, network) == ("777", "4242", "banani_27"), (
        end, seed, network)


def test_a_rebuilt_strip_stops_watching_its_variable():
    """A choice strip renders from its variable, so it holds a trace.  Left
    behind by a rebuild, that trace fires into a destroyed widget the next
    time anything writes the variable."""
    frame = panel(1920, 1000)
    option = frame.option_panel
    # The strip's own trace plus the form's one feedback watcher (made once,
    # with the variables -- see _make_vars); a rebuild must add nothing.
    before = len(option.fields["strip"].trace_info())
    for level in (1, 2, 3, 0):
        option._busy = True
        option._level = level
        option._build_form()
        option._busy = False
        option.frame.update_idletasks()
    traces = len(option.fields["strip"].trace_info())
    error = None
    try:
        for value in ("1.0", "0.25", "0.5"):
            option.fields["strip"].set(value)
        option.frame.update_idletasks()
    except tk.TclError as problem:      # a dead widget answering a trace
        error = problem
    frame.root.destroy()
    assert error is None, error
    assert traces == before, "%d traces on one variable, %d before" % (traces, before)


def test_a_place_that_cannot_use_a_setting_loses_its_row():
    """Miami's defaults pin the pedestrians and side friction off and it has
    no hourly demand profile, so those rows leave the form; moving back to a
    surveyed Dhaka junction brings them back, along with parameter.txt's
    pedestrian and object modes."""
    from dhakasim.parameters import Parameters
    frame = panel(1920, 1000)
    option = frame.option_panel
    option.network_var.set("miami")
    for _ in range(10):
        frame.root.update_idletasks()
        frame.root.update()             # the rebuild rides on after_idle
    trimmed = option._shown_rows
    pinned_off = option.pedestrian_var.get()
    friction_pinned_off = option.friction_var.get()
    option.network_var.set("kakrail_corridor")
    for _ in range(10):
        frame.root.update_idletasks()
        frame.root.update()
    restored = option._shown_rows
    restored_mode = Parameters.across_pedestrian_mode
    restored_friction = Parameters.OBJECT_MODE
    frame.root.destroy()
    assert trimmed == (False, False, False), trimmed
    assert pinned_off == "Off", pinned_off
    assert friction_pinned_off == "Off", friction_pinned_off
    assert restored == (True, True, True), restored
    assert restored_mode == Parameters.BASE_ACROSS_PEDESTRIAN_MODE
    assert restored_friction == Parameters.BASE_OBJECT_MODE


def test_the_run_screen_shows_the_frame_delay_the_start_screen_set():
    """The legend's delay box is a Spinbox over a list of choices, and Tk
    writes the first choice into the box's variable when it is built, so a
    run started at 250 ms used to open with a box reading 1 -- the run was
    stepping at 250, the legend said otherwise, and the first arrow click
    stepped from 1."""
    from tkinter import messagebox
    from dhakasim.parameters import Parameters
    frame = panel(1920, 1000)
    option = frame.option_panel
    option.network_var.set("banani_23")
    for _ in range(10):
        frame.root.update_idletasks()
        frame.root.update()
    option.fields["speed"].set("250")
    asked = messagebox.askyesno
    messagebox.askyesno = lambda *a, **k: False
    try:
        option.start_simulation()
        frame.root.update()
        run_panel = frame.panel
        if run_panel._timer is not None:
            run_panel.canvas.after_cancel(run_panel._timer)
            run_panel._timer = None
        run_panel._finished = True
        shown = frame._delay_var.get()
        stepping_at = Parameters.simulation_speed
        run_panel.dispose()
    finally:
        messagebox.askyesno = asked
        frame.root.destroy()
    assert shown == "250", shown
    assert stepping_at == 250, stepping_at


def _settle(root, condition, seconds=8.0):
    """Pump Tk until ``condition`` holds or the time is up."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update_idletasks()
        root.update()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_the_preview_shows_the_place_and_follows_the_view_strip():
    """Beside the settings, on a wide window, a picture of the chosen
    junction drawn by the run screen's own panel; choosing a 3D style
    redraws it from the 3D camera over the same run, and a model setting
    makes a new run behind it."""
    from tkinter import messagebox
    frame = panel(1920, 1000)
    option = frame.option_panel
    preview = option._preview
    root = frame.root
    asked = messagebox.askyesno
    messagebox.askyesno = lambda *a, **k: False
    try:
        built = _settle(root, lambda: preview.panel is not None)
        placed = preview.mode == "dock"
        title = preview.headers[0].cget("text")
        first = preview.panel
        option.render3d_var.set("Solid")
        in_3d = _settle(root, lambda: preview.panel is not None
                        and preview.panel.view_3d
                        and preview.panel._tween is None)
        style = preview.panel.scene3d.style
        same_run = preview.panel is first
        option.friction_var.set("Off")
        rebuilt = _settle(root, lambda: preview.panel is not None
                          and preview.panel is not first)
        note = preview._note.cget("text")
    finally:
        messagebox.askyesno = asked
        root.destroy()
    assert built and placed, (built, placed)
    assert title.startswith("Kakrail Corridor"), title
    assert in_3d and style == "solid" and same_run, (in_3d, style, same_run)
    assert rebuilt, "a model setting must rebuild the run behind the picture"
    assert "kerbside" in note, note


def test_a_run_started_after_the_preview_is_the_run_without_it():
    """The preview is a real run, on the same global Parameters the
    run reads -- so it must put them back.  A seeded run started after
    previewing (and after a 3D switch and a rebuild) has to be the run a
    fresh start screen gives."""
    from tkinter import messagebox
    from dhakasim.parameters import Parameters

    def signature(frame):
        option = frame.option_panel
        option.network_var.set("kakrail_corridor")
        option.fields["end_time"].set("90")
        option.fields["seed"].set("7")
        option.fields["speed"].set("5000")
        _settle(frame.root, lambda: False, 0.5)
        # The report's animation is not part of the signature, and its
        # basemap embedding opens a second Tk interpreter to write a PNG,
        # which a process that has made and destroyed twenty Tk roots (this
        # suite) has been seen to crash in.
        Parameters.REPORT_ANIMATION_FRAMES = 0
        option.start_simulation()
        frame.root.update()
        run = frame.panel
        if run._timer is not None:
            run.canvas.after_cancel(run._timer)
            run._timer = None
        run._finished = True
        for _ in range(40):
            run.action_performed()
        vehicles = run.processor.get_vehicle_list()
        return (len(vehicles),
                round(sum(v.get_segment_index() + v.get_distance_in_segment()
                          for v in vehicles), 6),
                Parameters.simulation_step)

    asked = messagebox.askyesno
    messagebox.askyesno = lambda *a, **k: False
    frame = panel(1920, 1000)
    try:
        option = frame.option_panel
        preview = option._preview
        reference = Parameters.snapshot()
        docked = preview.mode == "dock"
        _settle(frame.root, lambda: preview.panel is not None)
        option.render3d_var.set("Line art")
        _settle(frame.root, lambda: preview.panel is not None
                and preview.panel.view_3d)
        option.fields["traffic"].set("2")
        _settle(frame.root, lambda: False, 1.0)
        option.fields["traffic"].set("1")
        _settle(frame.root, lambda: False, 1.0)
        previewed = preview.panel is not None
        preview.stop()
        after_stop = Parameters.snapshot()
        with_preview = signature(frame)
        frame.new_simulation()
        frame.root.update()
        option = frame.option_panel
        option._preview.request = lambda _k: None      # never built
        option._preview.stop()
        without_preview = signature(frame)
    finally:
        messagebox.askyesno = asked
        frame.root.destroy()
    assert docked and previewed, (docked, previewed)
    leaked = [k for k in reference if k != "random"
              and reference[k] != after_stop.get(k)]
    assert not leaked, leaked
    assert with_preview == without_preview, (with_preview, without_preview)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            else:
                print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
