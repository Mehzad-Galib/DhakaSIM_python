"""A network with a single route has to finish its run and write its report.

``drmc`` is the one network in ``input/`` with a single route.  The end of a
run used to read route 1 unconditionally, which raised ``IndexError`` there
after the whole run had finished, so the user lost the report.  ``hash_run.py``
never saw it, because it skips imported networks, and drmc is an imported one.

The check runs the real thing in a subprocess, since the failure sat in the
last stretch of ``Processor._generate_statistics`` and a stub would have to
rebuild most of a run to reach it.

Run with ``python tests/test_one_route_network.py``.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK = "drmc"


def route_count(network):
    with open(os.path.join(REPO, "input", network, "path.txt"),
              encoding="utf-8") as handle:
        return int(handle.readline().split()[0])


class OneRouteNetworkFinishes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dhakasim_one_route_")
        cls.result = subprocess.run(
            [sys.executable, os.path.join(REPO, "run_dhakasim.py"),
             "--headless", "--network", NETWORK, "--seed", "1",
             "--set", "SimulationEndTime=20",
             "--set", f"StatsDir={cls.dir}",
             "--set", "ReportAnimationFrames=0"],
            cwd=REPO, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_fixture_still_has_one_route(self):
        # If drmc gains a route this file stops testing anything, so say so.
        self.assertEqual(route_count(NETWORK), 1)

    def test_the_run_exits_cleanly(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_the_run_writes_its_report(self):
        self.assertTrue(glob.glob(os.path.join(self.dir, "report_*.html")),
                        "no report written; stderr: " + self.result.stderr)


if __name__ == "__main__":
    unittest.main()
