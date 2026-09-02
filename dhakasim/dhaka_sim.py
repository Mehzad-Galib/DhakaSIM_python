"""Port of ``thesisfinal.DhakaSim`` -- the program entry point."""

from __future__ import annotations

import os
import sys

from .constants import Constants
from .parameters import Parameters
from .processor import Processor
from . import utilities as Utilities


def _apply_overrides(argv) -> None:
    """Apply the command-line settings overrides to :class:`Parameters`.

    Runs between reading ``parameter.txt`` and ``Constants.initialize()``,
    because the pedestrian arrival rates an override may change are what the
    Poisson tables there are built from.
    """
    changed = False
    # --network <name> overrides the Network setting in parameter.txt
    if "--network" in argv:
        i = argv.index("--network")
        if i + 1 < len(argv):
            Parameters.NETWORK_DIR = argv[i + 1]
    # A name that matches no folder must stop here.  Without this check the
    # per-file fallback to the flat ``input/`` root quietly assembles half a
    # network (root carries no demand.txt) and the run limps on until the
    # statistics crash with an IndexError -- found by running a network the
    # GUI's remove link had already deleted.
    if Parameters.NETWORK_DIR and not os.path.isdir(
            os.path.join("input", Parameters.NETWORK_DIR)):
        known = ", ".join(Processor.available_networks()) or "none"
        raise SystemExit(
            f"no such network: input/{Parameters.NETWORK_DIR} does not "
            f"exist (available: {known})")
    # --hour <0-23> overrides TimeOfDay in parameter.txt (-1 = peak hour)
    if "--hour" in argv:
        i = argv.index("--hour")
        if i + 1 < len(argv):
            try:
                Parameters.TIME_OF_DAY = int(argv[i + 1])
            except ValueError:
                pass
    # --seed <n> pins the RNG.  parameter.txt cannot do this: its RandomSeed
    # line deliberately discards the value and draws a fresh one, so without
    # this flag no two runs are comparable and no experiment can be repeated.
    if "--seed" in argv:
        i = argv.index("--seed")
        if i + 1 < len(argv):
            try:
                Parameters.seed = int(argv[i + 1])
                # Also routes the object/pedestrian generators, which the Java
                # original left unseeded, through the seeded generator --
                # otherwise the seed pins only part of the run.
                Parameters.DETERMINISTIC = True
                changed = True
            except ValueError:
                print(f"ignoring non-integer --seed {argv[i + 1]!r}")
    # The selected network's own defaults land between parameter.txt and the
    # command line: a place's speed limit should beat the global default, and
    # an explicit --set should beat both.
    if Utilities.apply_network_defaults(Parameters.NETWORK_DIR):
        changed = True

    # --set Name=Value, repeatable, for any setting parameter.txt understands.
    for i, arg in enumerate(argv):
        if arg != "--set" or i + 1 >= len(argv):
            continue
        pair = argv[i + 1]
        name, sep, value = pair.partition("=")
        if not sep:
            print(f"ignoring malformed --set {pair!r} (expected Name=Value)")
            continue
        if Utilities.apply_setting(name.strip(), value.strip()):
            changed = True
        else:
            print(f"ignoring unknown setting {name.strip()!r}")
    if changed:
        Utilities.finalise_settings()


def main(argv=None) -> int:
    # print("Started at " + datetime.now().isoformat())
    Utilities.initialize()
    argv = sys.argv[1:] if argv is None else list(argv)
    _apply_overrides(argv)
    # Java initialises the Constants interface lazily, which happens after the
    # parameters have been read; do the same here.
    Constants.initialize()
    Utilities.index_trace_file()

    gui_mode = Parameters.GUI_MODE
    if "--headless" in argv or "--no-gui" in argv:
        gui_mode = False
    elif "--gui" in argv:
        gui_mode = True

    if gui_mode:
        from .gui import DhakaSimFrame
        DhakaSimFrame().run()
    else:
        Processor().auto_process()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
