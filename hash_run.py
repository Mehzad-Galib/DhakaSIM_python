#!/usr/bin/env python3
"""Seeded-run hash harness -- the end-to-end check the test suite does not do.

The suites pin geometry, the file grammar and the GUI layout; none of them
runs the traffic model.  The check that has always guarded the inner loop is
a seeded headless run compared bit for bit against a known-good one -- which
until now meant hand-running the simulator before and after a change and
diffing whatever was captured.  This script is that check as one command:

    python hash_run.py --record            # run every network, store hashes
    python hash_run.py                     # run and compare; non-zero on drift
    python hash_run.py kakrail_corridor    # one network only (either mode)
    python hash_run.py --list              # show what the baseline holds

Per network it runs ``run_dhakasim.py --headless`` with a pinned seed and end
time, the statistics redirected to a throwaway directory, and hashes two
things: the console summary, and every CSV the run writes (the per-link and
per-type statistics, the sampled per-vehicle trajectories, the accident log).
Those are every number the run produces, so a hash miss means the traffic
model changed -- there is nothing else in them that could move.

The hashes are stored per component, not as one digest per network, so a
drift report says *which* output moved: a change that only touches
``accident_log.csv`` points somewhere very different from one that moves
``link_avg_speed.csv``.

The HTML report is deliberately not hashed.  Its filename and body carry the
wall-clock time of the run, so it can never be stable; everything it shows is
computed from the CSVs that are.

Recording is a statement that the current behaviour is correct, exactly like
updating the expected values in ``test_javacompat.py`` would be -- so
``--record`` is for after a change is *verified* (or on a fresh machine),
never a way to make a red check green.  The baseline lives in
``run_hashes.txt`` beside this script.  Hashes are stable across runs on one
machine; a different Python build may format a float differently, so the
recorded interpreter version is checked and a mismatch is reported as a
warning rather than a drift.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(REPO, "run_hashes.txt")

#: The run every hash describes.  Changing either of these invalidates the
#: whole baseline, which is why they are recorded in its header and checked.
SEED = 7
END_TIME = 90


def networks() -> list[str]:
    """Every folder under ``input/`` that is a shipped network.

    Networks made by the GUI's map-import dialog are excluded -- they carry
    an ``osm_extract.geojson`` -- because they are user artifacts, not part
    of the parity contract: the OSM data behind them changes with every
    fetch, so no baseline for one could ever be stable.
    """
    root = os.path.join(REPO, "input")
    return sorted(
        name for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name, "link.txt"))
        and not os.path.isfile(
            os.path.join(root, name, "osm_extract.geojson")))


def _run(network: str) -> tuple[dict[str, str], str]:
    """One seeded headless run; ``({component: sha256}, error)``."""
    stats_dir = tempfile.mkdtemp(prefix=f"dhakasim_hash_{network}_")
    command = [
        sys.executable, os.path.join(REPO, "run_dhakasim.py"), "--headless",
        "--network", network, "--seed", str(SEED),
        "--set", f"SimulationEndTime={END_TIME}",
        "--set", f"StatsDir={stats_dir}",
        # The animation samples frames through the drawing pipeline; the
        # numbers under test do not pass through it, so it is pure runtime.
        "--set", "ReportAnimationFrames=0",
    ]
    result = subprocess.run(command, cwd=REPO, capture_output=True)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return {}, "run failed: " + (tail[-1] if tail else "no stderr")

    def digest(payload: bytes) -> str:
        # Line endings are normalised so a hash means "the same numbers",
        # not "the same platform's newline convention".
        return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()

    components = {"stdout": digest(result.stdout)}
    csv_dir = os.path.join(stats_dir, "csv")
    if os.path.isdir(csv_dir):
        for name in sorted(os.listdir(csv_dir)):
            with open(os.path.join(csv_dir, name), "rb") as handle:
                components[f"csv/{name}"] = digest(handle.read())
    return components, ""


# --------------------------------------------------------------------------
# the baseline file
# --------------------------------------------------------------------------

def _read_baseline() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """``({network: {component: hash}}, header facts)``."""
    recorded: dict[str, dict[str, str]] = {}
    facts: dict[str, str] = {}
    if not os.path.isfile(BASELINE):
        return recorded, facts
    with open(BASELINE, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("#"):
                name, sep, value = line[1:].strip().partition(": ")
                if sep:
                    facts[name] = value
                continue
            if not line:
                continue
            network, component, value = line.split(" ", 2)
            recorded.setdefault(network, {})[component] = value
    return recorded, facts


def _write_baseline(recorded: dict[str, dict[str, str]]) -> None:
    with open(BASELINE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# Seeded-run hashes recorded by hash_run.py; compare with\n"
            "# `python hash_run.py`, re-record with `python hash_run.py"
            " --record`.\n"
            f"# seed: {SEED}\n"
            f"# end_time: {END_TIME}\n"
            f"# python: {sys.version.split()[0]}\n")
        for network in sorted(recorded):
            for component in sorted(recorded[network]):
                handle.write(
                    f"{network} {component} {recorded[network][component]}\n")


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def _record(wanted: list[str]) -> int:
    recorded, _facts = _read_baseline()
    for network in wanted:
        started = time.time()
        components, error = _run(network)
        if error:
            print(f"FAIL  {network}: {error}")
            return 1
        recorded[network] = components
        print(f"recorded  {network}  ({len(components)} components,"
              f" {time.time() - started:.0f}s)")
    _write_baseline(recorded)
    print(f"baseline written to {os.path.relpath(BASELINE, REPO)}")
    return 0


def _check(wanted: list[str]) -> int:
    recorded, facts = _read_baseline()
    if not recorded:
        print("no baseline recorded yet -- run `python hash_run.py --record`")
        return 1
    for name, expected in (("seed", str(SEED)), ("end_time", str(END_TIME))):
        if facts.get(name, expected) != expected:
            print(f"baseline was recorded with {name}={facts[name]}, this "
                  f"script uses {expected} -- re-record before comparing")
            return 1
    this_python = sys.version.split()[0]
    if facts.get("python", this_python) != this_python:
        print(f"note: baseline recorded under Python {facts['python']}, "
              f"running {this_python} -- a drift may be float formatting")

    drift = False
    for network in wanted:
        if network not in recorded:
            print(f"skip  {network}: not in the baseline (record it first)")
            continue
        started = time.time()
        components, error = _run(network)
        if error:
            print(f"FAIL  {network}: {error}")
            drift = True
            continue
        expected = recorded[network]
        moved = sorted(component for component in expected
                       if components.get(component) != expected[component])
        # A component the baseline has never seen is drift too: a run that
        # starts writing a new CSV has changed, even if every old one matches.
        new = sorted(set(components) - set(expected))
        if moved or new:
            drift = True
            print(f"DRIFT {network}  ({time.time() - started:.0f}s)")
            for component in moved:
                what = "missing" if component not in components else "changed"
                print(f"      {what}: {component}")
            for component in new:
                print(f"      new output: {component}")
        else:
            print(f"ok    {network}  ({time.time() - started:.0f}s)")
    if drift:
        print("\nseeded runs have drifted.  If the change is unintended, fix"
              " it; if it is intended and verified, `python hash_run.py"
              " --record`.")
    return 1 if drift else 0


def main(argv: list[str]) -> int:
    record = "--record" in argv
    if "--list" in argv:
        recorded, facts = _read_baseline()
        for name, value in facts.items():
            print(f"{name}: {value}")
        for network in sorted(recorded):
            print(f"{network}: {len(recorded[network])} components")
        return 0
    wanted = [arg for arg in argv if not arg.startswith("--")]
    known = networks()
    for name in wanted:
        if name not in known:
            print(f"unknown network {name!r}; have: {', '.join(known)}")
            return 2
    if not wanted:
        wanted = known
    return _record(wanted) if record else _check(wanted)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
