"""Port of ``thesisfinal.DhakaSim`` -- the program entry point."""

from __future__ import annotations

import sys

from .constants import Constants
from .parameters import Parameters
from .processor import Processor
from . import utilities as Utilities


def main(argv=None) -> int:
    # print("Started at " + datetime.now().isoformat())
    Utilities.initialize()
    # Java initialises the Constants interface lazily, which happens after the
    # parameters have been read; do the same here.
    Constants.initialize()
    Utilities.index_trace_file()

    argv = sys.argv[1:] if argv is None else list(argv)
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
