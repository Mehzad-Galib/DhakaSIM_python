"""Pin :mod:`dhakasim.javacompat` to values produced by a real JVM.

Every expected value below was printed by Java 21 (Temurin 21.0.11) and pasted
in verbatim.  If one of these ever fails, the Python simulation has drifted away
from the Java build and the statistics will no longer match.

Run with ``python -m pytest python/tests`` or ``python python/tests/test_javacompat.py``.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim.javacompat import (  # noqa: E402
    DOUBLE_MIN_VALUE, INF, JavaRandom, NaN, jbool_str, jdiv, jformat, jint, jmax, jmin,
    jmod, jround, jsqrt, jstr,
)


def test_random_stream_matches_java():
    """``new Random(42)`` drawn in this exact order."""
    r = JavaRandom(42)
    assert [r.next_int() for _ in range(5)] == [
        -1170105035, 234785527, -1360544799, 205897768, 1325939940]
    assert [r.next_int_bound(101) for _ in range(5)] == [56, 3, 24, 75, 30]
    assert [jstr(r.next_double()) for _ in range(5)] == [
        "0.9033722646721782", "0.36878291341130565", "0.2757480694417024",
        "0.46365357580915334", "0.7829017787900358"]
    # nextFloat: Java prints the shortest decimal that round-trips as a
    # *float*, so compare the single-precision bit patterns
    as_float32 = lambda x: struct.pack("<f", x)  # noqa: E731
    assert [as_float32(r.next_float()) for _ in range(5)] == [
        as_float32(v) for v in (0.91932774, 0.15195823, 0.43649095, 0.4397998,
                                0.7499061)]
    assert [jstr(r.next_gaussian()) for _ in range(5)] == [
        "-0.9277387081593533", "-0.4292657236001543", "-1.3253064444527038",
        "-0.7167068402216212", "-0.7463182282869228"]
    assert [jbool_str(r.next_boolean()) for _ in range(5)] == [
        "true", "true", "false", "true", "false"]
    assert [jstr(r.next_double_range(10, 60)) for _ in range(5)] == [
        "47.62549742953255", "11.57091194132904", "27.895995973856433",
        "50.889846541781964", "30.884377337645937"]


def test_format_matches_java_formatter():
    """``String.format("%.3f|%.2f", d)`` for each value."""
    # a list, not a dict: 0.0 and -0.0 compare equal as dict keys
    expected = [
        (1.005, "1.005", "1.01"),
        (0.0005, "0.001", "0.00"),
        (-2.5005, "-2.501", "-2.50"),
        (1e20, "100000000000000000000.000", "100000000000000000000.00"),
        (0.0, "0.000", "0.00"),
        (-0.0, "-0.000", "-0.00"),
        (3.14159, "3.142", "3.14"),
        (2.675, "2.675", "2.68"),
        (1234.5675, "1234.568", "1234.57"),
        (1e-9, "0.000", "0.00"),
        (7.54651920529018, "7.547", "7.55"),
    ]
    for value, three, two in expected:
        assert jformat(value, 3) == three, value
        assert jformat(value, 2) == two, value
    # the statistics CSVs contain literal NaN entries
    assert jformat(NaN, 3) == "NaN"
    assert jformat(INF, 3) == "Infinity"
    assert jformat(-INF, 3) == "-Infinity"


def test_double_to_string_matches_java():
    """String concatenation of a double, as used by the ``speed: …`` output."""
    assert jstr(7.54651920529018) == "7.54651920529018"
    assert jstr(1.0) == "1.0"
    assert jstr(1.0e-4) == "1.0E-4"
    assert jstr(0.001) == "0.001"
    assert jstr(1.0e7) == "1.0E7"
    assert jstr(123456789.0) == "1.23456789E8"
    assert jstr(1.0e-7) == "1.0E-7"
    assert jstr(NaN) == "NaN"


def test_rounding_and_casts():
    assert jround(-2.5) == -2      # Math.round: floor(d + 0.5)
    assert jround(2.5) == 3
    assert jint(-2.7) == -2       # (int) truncates towards zero
    assert jint(NaN) == 0
    assert jmod(-7, 3) == -1      # Java % takes the sign of the dividend


def test_nan_propagating_min_max():
    """Math.min/Math.max propagate NaN; Python's min/max do not."""
    assert jmin(5.0, NaN) != jmin(5.0, NaN)   # i.e. NaN
    assert jmin(NaN, 5.0) != jmin(NaN, 5.0)
    assert jmax(0.0, NaN) != jmax(0.0, NaN)
    assert jmin(3.0, 4.0) == 3.0
    assert jmax(3.0, 4.0) == 4.0
    # the actual Gipps path: Java yields NaN, precision2 then makes it 0
    from dhakasim.utilities import precision2
    assert precision2(jmax(0.0, jmin(12.5, NaN))) == 0.0


def test_ieee_edge_cases_do_not_raise():
    assert jdiv(1.0, 0.0) == INF
    assert jdiv(-1.0, 0.0) == -INF
    assert jdiv(0.0, 0.0) != jdiv(0.0, 0.0)   # NaN
    assert jsqrt(-1.0) != jsqrt(-1.0)         # NaN, not an exception


def test_double_min_value_is_the_positive_denormal():
    """Node.isColliding seeds a maximum accumulator with Double.MIN_VALUE."""
    assert DOUBLE_MIN_VALUE > 0.0
    assert DOUBLE_MIN_VALUE / 2 == 0.0


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
