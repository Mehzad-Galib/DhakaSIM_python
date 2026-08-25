"""Drive the DhakaSim GUI programmatically: options screen -> start ->
step the simulation -> flip to 3D -> screenshot every stage.

Run it from anywhere; it finds the project from its own location and
chdirs there (the simulator resolves input/ and statistics/ from cwd).
Screenshots land in <project>/debug/ (gitignored).

Follows the notes in CLAUDE.md ("Development environment gotchas"):
- claim DPI awareness before the Tk root exists, so screenshots are in
  real pixels and the window is not ghosted at a virtual resolution;
- never let the panel's own after() timer run: cancel it, set
  _finished, and call action_performed() ourselves -- at low frame
  delays the timer starves an external driver looping on update().
"""
import ctypes
import os
import sys
import time

ctypes.windll.shcore.SetProcessDpiAwareness(2)

# .claude/skills/run-dhakasim/driver.py -> three levels up is the project
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
OUT = os.path.join(PROJECT, "debug")
os.makedirs(OUT, exist_ok=True)
os.chdir(PROJECT)          # input/ and statistics/ resolve from cwd
sys.path.insert(0, PROJECT)

from PIL import ImageGrab

from dhakasim import utilities as Utilities
from dhakasim.constants import Constants
from dhakasim.parameters import Parameters

# Mirror dhaka_sim.main() up to the GUI branch.
Utilities.initialize()
# Optional argv override: `python driver.py banani_23` drives that
# network instead of the one parameter.txt names.  The options screen
# defaults its network tile from Parameters.NETWORK_DIR, so setting it
# here is enough.
if len(sys.argv) > 1:
    Parameters.NETWORK_DIR = sys.argv[1]
Utilities.apply_network_defaults(Parameters.NETWORK_DIR)
Utilities.finalise_settings()
Constants.initialize()
Utilities.index_trace_file()

from dhakasim.gui import DhakaSimFrame

frame = DhakaSimFrame()
root = frame.root


def pump(seconds=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def shot(name):
    if len(sys.argv) > 1:           # keep per-network runs distinct
        name = f"{sys.argv[1]}_{name}"
    root.update_idletasks()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    path = os.path.join(OUT, name)
    img.save(path)
    print(f"saved debug/{name} ({w}x{h})")


# --- stage 1: the options / start screen ---------------------------------
# lift() will not raise us above whatever app currently has focus
# (Windows foreground rules), and ImageGrab photographs the screen, so
# an obscured window screenshots as the thing covering it.  -topmost is
# the reliable way to be photographed.
root.attributes("-topmost", True)
root.lift()
pump(1.5)   # let _fit_to_window's after_idle passes settle
assert frame.option_panel is not None, "options screen did not build"
print("options screen up, network =", frame.option_panel.network_var.get())
shot("1_options.png")

# --- stage 2: start the simulation ---------------------------------------
frame.option_panel.fields["end_time"].set("120")
frame.option_panel.fields["seed"].set("1")
frame.option_panel.start_simulation()   # what the Start button calls
panel = frame.panel
assert panel is not None, "simulation panel did not build"

# Take the timer away and drive the steps ourselves (CLAUDE.md).
if panel._timer is not None:
    panel.canvas.after_cancel(panel._timer)
    panel._timer = None
panel._finished = True

for step in range(80):
    panel.action_performed()
    root.update()
print("stepped to SimulationStep", Parameters.simulation_step)
pump(0.3)
shot("2_simulation.png")

# --- stage 3: flip to the 3D view ----------------------------------------
frame.toggle_view()
panel.action_performed()
root.update()
pump(0.3)
shot("3_view3d.png")

panel.dispose()
root.destroy()
print("clean exit")
