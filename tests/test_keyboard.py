"""Both screens by keyboard alone, and what the run's sidebar reports.

The October heuristic evaluation judged the screens against Shneiderman's
golden rules, Norman's principles, Gerhardt-Powals' cognitive engineering
principles, ISO 9241-110 and WCAG 2.2, and the pointer turned out to be
assumed everywhere: no button or choice ever showed the keyboard focus
(Tk on Windows does not ring a label), the pictures and explanations
beside the form came only on <Enter>, every choice cell was its own Tab
stop, Space on a focused Pause button paused and resumed at once, and the
run toolbar's two sliders were not on the Tab order at all.  Nothing here
was caught by a test because nothing here was ever pressed by one.  See
``docs/dhakasim-heuristics-beyond-nielsen.html``.

Whether a ring is *drawn* was measured from the window's own pixels when
these were found; the tests check the configuration that draws it, which
is what can regress.

The sidebar's flow readout (mean speed, vehicles standing) is here too:
it is the one Gerhardt-Powals finding that needed new feedback rather
than a repair.

Needs a display.  Run from the project root with
``python tests/test_keyboard.py``.  The window has to be able to take the
focus, so do not drive the mouse while it runs.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk

# The same pins as the start-screen test: real pixels, 96 DPI type, and the
# shipped networks only -- what the density table was measured against.
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:                       # not Windows, or already set
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import gui  # noqa: E402
from dhakasim import map_import  # noqa: E402
from dhakasim import utilities as Utilities  # noqa: E402
from dhakasim.constants import Constants  # noqa: E402

_REAL_NETWORKS = gui.Processor.available_networks
gui.Processor.available_networks = staticmethod(
    lambda: [n for n in _REAL_NETWORKS() if not map_import.is_imported(n)])

SCALING = 1.3333
_REAL_TK_INIT = tk.Tk.__init__


def _pinned(self, *args, **kwargs):
    _REAL_TK_INIT(self, *args, **kwargs)
    self.tk.call("tk", "scaling", SCALING)


def start_screen(width=1920, height=1000):
    """A laid-out start screen, as ``test_start_screen.panel`` builds one."""
    Utilities.initialize()
    Constants.initialize()
    tk.Tk.__init__ = _pinned
    try:
        frame = gui.DhakaSimFrame()
    finally:
        tk.Tk.__init__ = _REAL_TK_INIT
    root = frame.root
    root.state("normal")
    root.geometry("%dx%d+0+0" % (width, height))
    for _ in range(30):
        root.update_idletasks()
        root.update()
    frame.option_panel._fit_to_window(force=True)
    root.update_idletasks()
    return frame


def run_screen():
    """A run a few steps in, its own timer taken away (see CLAUDE.md).

    ``_finished`` is left false, unlike the run-dhakasim driver: pausing
    refuses a finished run, and a test of the Pause button needs it live.
    """
    frame = start_screen()
    option = frame.option_panel
    option.fields["end_time"].set("60")
    option.fields["seed"].set("1")
    option.start_simulation()
    panel = frame.panel
    if panel._timer is not None:
        panel.canvas.after_cancel(panel._timer)
        panel._timer = None
    for _ in range(3):
        panel.action_performed()
    frame.root.update()
    return frame


def strips(widget):
    """Every choice strip under ``widget``, in creation order."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, gui._Segmented):
            found.append(child)
        found.extend(strips(child))
    return found


def tab_cycle(root):
    """The widgets Tab visits, once round."""
    stops, widget = [], root
    for _ in range(400):
        widget = widget.tk_focusNext()
        if widget is None or widget in stops:
            break
        stops.append(widget)
    return stops


def focus(widget):
    widget.focus_force()
    widget.update()


def press(widget, key):
    """A key sent to ``widget``, which must hold the focus to receive it."""
    widget.event_generate("<%s>" % key)
    widget.update()


def shows_focus(widget):
    """Whether ``widget``, holding the focus, is configured to show it.

    Labels and checkboxes are ringed by hand (``gui._ring``): Tk on Windows
    never draws their highlight, so the border colour itself has to change.
    Entries and scales draw their own ring, which only needs the colour and
    the width.  The playback slider draws a ring round its handle.
    """
    focus_colour = gui._UI["focus"].lower()
    kind = widget.winfo_class()
    if kind == "Canvas" and isinstance(widget.master, gui._SpeedSlider):
        return any(widget.type(item) == "oval"
                   and widget.itemcget(item, "outline").lower() == focus_colour
                   for item in widget.find_all())
    if kind in ("Entry", "Scale"):
        return (widget.cget("highlightcolor").lower() == focus_colour
                and int(widget.cget("highlightthickness")) >= 2)
    if kind == "Checkbutton":
        widget = widget.master
    if kind == "Button":
        return True                     # Windows draws its own focus box
    return (widget.cget("highlightbackground").lower() == focus_colour
            and int(widget.cget("highlightthickness")) >= 1)


def unringed(frame):
    """The Tab stops that hold the focus without showing it."""
    root = frame.root
    missing = []
    for widget in tab_cycle(root):
        focus(widget)
        if not shows_focus(widget):
            try:
                missing.append("%s %r" % (widget.winfo_class(),
                                          widget.cget("text")))
            except tk.TclError:
                missing.append(widget.winfo_class())
    return missing


def traffic_strip(option):
    return next(s for s in strips(option.frame)
                if s._var is option.fields["traffic"])


def cells(strip):
    return [cell for _v, _l, cell in strip._cells]


# ---- the start screen -------------------------------------------------------

def test_every_stop_on_the_start_screen_shows_the_focus():
    """Before, 53 of the 58 stops on the owner's screen showed nothing when
    they held the focus: every button, every choice, the dropdown and the
    playback slider.  Only the text entries did."""
    frame = start_screen()
    missing = unringed(frame)
    left = frame.root.focus_get()
    focus(frame.option_panel._hint)     # anywhere else: the ring must go
    still = shows_focus(left) if left is not None else False
    frame.root.destroy()
    assert not missing, "no focus shown on: %s" % missing
    assert not still or left.winfo_class() in ("Entry", "Scale", "Button"), (
        "the ring stayed on %s after the focus left it" % left)


def test_focus_explains_a_row_as_the_pointer_does():
    """At the captionless densities the footer is the only explanation a
    row has, and focus never reached it."""
    frame = start_screen()
    option = frame.option_panel
    cell = cells(traffic_strip(option))[0]
    idle = option._hint.cget("text")
    cell.event_generate("<Enter>")
    cell.update()
    by_pointer = option._hint.cget("text")
    cell.event_generate("<Leave>")
    focus(cell)
    by_focus = option._hint.cget("text")
    frame.root.destroy()
    assert by_pointer != idle, "the pointer no longer explains the row"
    assert by_focus == by_pointer, (by_focus, by_pointer)


def test_focus_previews_the_focused_choice():
    frame = start_screen()
    option = frame.option_panel
    first = cells(traffic_strip(option))[0]
    focus(first)
    peek = option._preview._peek
    frame.root.destroy()
    assert peek == ("traffic", "0.5"), peek


def test_one_tab_stop_per_strip_and_no_pictures_on_the_way():
    """Thirty-nine cells were stops of their own, and binding focus events
    down a row once put every icon and picture on the Tab order too."""
    frame = start_screen()
    option = frame.option_panel
    per_strip = {str(s): sum(bool(int(str(c.cget("takefocus")) or 0))
                             for c in cells(s))
                 for s in strips(option.frame) if s._cells}
    stops = tab_cycle(frame.root)
    canvases = [w for w in stops if w.winfo_class() == "Canvas"
                and not isinstance(w.master, gui._SpeedSlider)]
    frame.root.destroy()
    assert all(n == 1 for n in per_strip.values()), per_strip
    assert not canvases, "pictures on the Tab order: %s" % canvases


def test_arrows_walk_a_strip_without_choosing():
    """Choosing as the focus moves would load every junction walked past;
    the arrows only move, and Space takes the focused cell."""
    frame = start_screen()
    option = frame.option_panel
    strip = traffic_strip(option)
    row = cells(strip)
    variable = option.fields["traffic"]
    before = variable.get()
    start = row[[v for v, _l, _c in strip._cells].index(before)]
    focus(start)
    press(start, "Right")
    moved = frame.root.focus_get()
    unchanged = variable.get()
    press(moved, "space")
    chosen = variable.get()
    focus(row[0])
    press(row[0], "Left")
    stayed = frame.root.focus_get()
    frame.root.destroy()
    assert moved is row[row.index(start) + 1], moved
    assert unchanged == before, "an arrow chose %s" % unchanged
    assert chosen == "1.5", chosen
    assert stayed is row[0], "Left ran off the start of the strip"


def test_tab_leaves_a_strip_from_any_cell():
    """Tab and Shift+Tab step over the rest of the strip, from whichever
    cell the arrows left the focus on."""
    frame = start_screen()
    option = frame.option_panel
    row = cells(traffic_strip(option))
    after, before = row[-1].tk_focusNext(), row[0].tk_focusPrev()
    focus(row[1])
    press(row[1], "Tab")
    forwards = frame.root.focus_get()
    focus(row[3])
    press(row[3], "Shift-Tab")
    backwards = frame.root.focus_get()
    frame.root.destroy()
    assert forwards is after, (forwards, after)
    assert backwards is before, (backwards, before)


def test_a_row_out_of_sight_is_scrolled_into_view():
    """On 720p with a deep taskbar the settings are one column of about
    1000 px in a window with under 500 to show it; Tab must not walk the
    focus off the bottom of it."""
    frame = start_screen(1280, 580)
    option = frame.option_panel
    if not option._bar.winfo_ismapped():
        frame.root.destroy()
        print("     (skipped: the form fits at 1280x580 here)")
        return
    canvas, tray = option._canvas, option._tray

    def on_screen(widget):
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        return top >= 0 and top + widget.winfo_height() <= canvas.winfo_height()

    rows = sorted((s for s in strips(option.frame) if s._cells),
                  key=lambda s: s.winfo_rooty())
    lowest = next(c for c in cells(rows[-1])
                  if str(c.cget("takefocus")) in ("1", "True"))
    highest = next(c for c in cells(rows[0])
                   if str(c.cget("takefocus")) in ("1", "True"))
    hidden_at_first = not on_screen(lowest)
    focus(lowest)
    shown = on_screen(lowest)
    focus(highest)
    back = on_screen(highest)
    frame.root.destroy()
    assert hidden_at_first, "the lowest row was already in view; no test"
    assert shown, "focus went to a row the scroller hides"
    assert back, "scrolling back up did not follow the focus"


# ---- the run screen ------------------------------------------------------------

def test_space_on_the_focused_pause_button_pauses():
    """It paused and resumed in one keystroke: the button fired, and the
    window's own Space binding fired after it."""
    frame = run_screen()
    panel = frame.panel
    before = panel.paused
    focus(frame._pause_button)
    press(frame._pause_button, "space")
    after = panel.paused
    frame.root.destroy()
    assert before is False and after is True, (before, after)


def test_every_stop_on_the_run_screen_shows_the_focus():
    frame = run_screen()
    missing = unringed(frame)
    frame.root.destroy()
    assert not missing, "no focus shown on: %s" % missing


def test_space_on_a_focused_checkbox_or_reset_does_only_that():
    """Tk's own checkbox and button take Space without stopping it, so the
    window's Space-to-pause fired as well: Space on the map checkbox hid
    the map and paused the run."""
    frame = run_screen()
    panel = frame.panel

    def find(widget, kind, text):
        for child in widget.winfo_children():
            if (child.winfo_class() == kind
                    and text in str(child.cget("text"))):
                return child
            found = find(child, kind, text)
            if found is not None:
                return found
        return None

    results = {}
    for kind, text in (("Checkbutton", "Background map"), ("Button", "Reset")):
        widget = find(frame.root, kind, text)
        if widget is None:
            continue                    # no imagery on this checkout
        before = panel.paused
        focus(widget)
        press(widget, "space")
        results[text] = (before, panel.paused)
    frame.root.destroy()
    assert results, "neither control was found"
    for text, (before, after) in results.items():
        assert before == after, "Space on %s also paused the run" % text


def test_a_disabled_toolbar_button_is_not_a_tab_stop():
    frame = run_screen()
    report = frame._report_button
    disabled = report in tab_cycle(frame.root)
    report.configure(state="normal")
    enabled = report in tab_cycle(frame.root)
    frame.root.destroy()
    assert not disabled, "Tab lands on Open report while it does nothing"
    assert enabled, "an enabled Open report fell off the Tab order"


def test_the_toolbar_sliders_are_on_the_tab_order_and_visible():
    frame = run_screen()
    stops = tab_cycle(frame.root)
    sliders = (frame._scale_slider, frame._traffic_slider)
    reached = [s in stops for s in sliders]
    dress = [(s.cget("background"), s.cget("troughcolor"),
              int(s.cget("highlightthickness")), s.cget("highlightcolor"))
             for s in sliders]
    zoom = frame._scale_slider
    focus(zoom)
    was = zoom.get()
    press(zoom, "Right")
    moved = zoom.get()
    frame.root.destroy()
    assert all(reached), "a toolbar slider cannot be reached by Tab"
    for handle, trough, ring, ring_colour in dress:
        assert handle == gui._UI["muted"] and trough == gui._UI["panel"], (
            handle, trough)
        assert ring >= 2 and ring_colour == gui._UI["focus"], (ring,
                                                               ring_colour)
    assert moved > was, "the arrow key did not move the zoom slider"


class _Vehicle:
    def __init__(self, type_, speed):
        self._type, self._speed = type_, speed

    def get_type(self):
        return self._type

    def get_speed(self):
        return self._speed


def test_the_sidebar_says_how_the_network_is_coping():
    """Mean speed and vehicles standing, in the model's own terms: m/s
    shown as km/h, standing below the keep-clear rule's 0.5 m/s, and the
    people walking along the road left out of both."""
    frame = run_screen()
    car, walker = 4, Constants.PEDESTRIANS_ALONG_THE_ROAD_TYPE
    frame.update_legend_counts([_Vehicle(car, 10.0), _Vehicle(car, 0.2),
                                _Vehicle(car, 0.0), _Vehicle(walker, 1.0)])
    speed, limit = (v.get() for v in frame.legend_flow["speed"])
    standing, share = (v.get() for v in frame.legend_flow["standing"])
    frame.update_legend_counts([_Vehicle(walker, 1.0)])
    empty = frame.legend_flow["speed"][0].get()
    frame.root.destroy()
    assert speed == "12", speed           # (10 + 0.2 + 0) / 3 m/s
    assert limit.endswith("km/h") and limit.startswith("of "), limit
    assert (standing, share) == ("2", "67%"), (standing, share)
    assert empty == "–", "no vehicles should read as a dash, not 0"


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
