#!/usr/bin/env python3
"""Launcher for DhakaSim.

The simulator reads ``input/`` and writes ``statistics/`` relative to the
current directory, so run it from this folder::

    python run_dhakasim.py            # honours GUIMode in input/parameter.txt
    python run_dhakasim.py --headless # force the no-GUI run
    python run_dhakasim.py --gui      # force the GUI

Also accepted: ``--network <name>``, ``--hour <0-23>``, ``--seed <n>`` and
``--set Name=Value`` (repeatable, for any setting ``parameter.txt`` understands).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dhakasim.dhaka_sim import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
