"""Pin the per-network ``defaults.txt`` mechanism.

A speed limit belongs to a place, not to a run, so it lives in the network
folder. The order it is applied in is the part worth protecting: parameter.txt
supplies a default, the network overrides it, and an explicit ``--set`` beats
both. Getting that backwards would silently simulate Dhaka at Riyadh's speed
limit, which nothing else would catch.

Run from the project root with ``python tests/test_network_defaults.py``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import utilities as Utilities  # noqa: E402
from dhakasim.parameters import Parameters  # noqa: E402

KMH = 3.6


def kmh():
    return round(Parameters.maximum_speed * KMH, 2)


def fresh():
    Utilities.initialize()


def test_parameter_file_supplies_the_base_limit():
    fresh()
    assert kmh() == 60.0, kmh()
    assert round(Parameters.BASE_MAXIMUM_SPEED * KMH, 2) == 60.0


def test_wide_road_networks_raise_it():
    # Miami's trunk arterials carry 100; Al Malaz's urban primary/secondary
    # streets 80.  Each is the place's own statement, not a shared constant.
    for network, limit in (("miami", 100.0), ("riyadh", 80.0)):
        fresh()
        applied = Utilities.apply_network_defaults(network)
        assert applied.get("MaximumSpeed"), f"{network} set nothing"
        assert kmh() == limit, f"{network}: {kmh()}"


def test_dhaka_networks_keep_the_base_limit():
    for network in ("buet_du_dmc", "kakrail_corridor", "banani_23",
                    "bijoy_sarani"):
        fresh()
        applied = Utilities.apply_network_defaults(network)
        assert "MaximumSpeed" not in applied, f"{network} overrode the limit"
        assert kmh() == 60.0, f"{network}: {kmh()}"


def test_the_demo_turns_its_labels_off_and_nothing_else_does():
    # The BUET-DU-DMC demo's imagery already carries every street name, so its
    # defaults.txt suppresses the drawn labels; a re-initialisation or another
    # network must get them back.
    fresh()
    assert Parameters.SHOW_LABELS is True
    applied = Utilities.apply_network_defaults("buet_du_dmc")
    assert applied.get("ShowLabels"), "buet_du_dmc no longer pins ShowLabels"
    assert Parameters.SHOW_LABELS is False
    fresh()
    assert Parameters.SHOW_LABELS is True, "initialize() must reset ShowLabels"
    Utilities.apply_network_defaults("kakrail_corridor")
    assert Parameters.SHOW_LABELS is True


def test_base_limit_survives_a_network_override():
    """What the GUI falls back to when the dropdown leaves Miami."""
    fresh()
    Utilities.apply_network_defaults("miami")
    assert kmh() == 100.0
    assert round(Parameters.BASE_MAXIMUM_SPEED * KMH, 2) == 60.0


def test_an_explicit_setting_beats_the_network():
    fresh()
    Utilities.apply_network_defaults("miami")
    Utilities.apply_setting("MaximumSpeed", "45")
    assert kmh() == 45.0


def test_unknown_network_changes_nothing():
    fresh()
    assert Utilities.apply_network_defaults("no_such_place") == {}
    assert kmh() == 60.0
    assert Utilities.apply_network_defaults("") == {}
    assert kmh() == 60.0


def test_comments_and_blank_lines_are_ignored():
    fresh()
    path = os.path.join("input", "miami", "defaults.txt")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "#" in text, "the shipped file should carry its explanation"
    assert Utilities.apply_network_defaults("miami")["MaximumSpeed"] == "100.0"


def test_default_end_time_is_the_short_one():
    fresh()
    assert Parameters.simulation_end_time == 480, Parameters.simulation_end_time


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
