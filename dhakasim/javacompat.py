"""Java runtime semantics that Python does not share.

DhakaSim was originally written in Java and its numerical results depend on
Java behaviour that Python does not reproduce by default:

* ``Math.round`` rounds half *up*; Python's ``round`` rounds half to *even*.
* ``(int) someDouble`` truncates towards zero and saturates at the ``int``
  range; ``Double.MIN_VALUE`` is the smallest positive denormal, not the most
  negative double (``Node.isColliding`` relies on this).
* IEEE-754 edge cases: Java yields ``NaN``/``Infinity`` for ``sqrt(-1)``,
  ``x / 0`` and ``log(0)`` where Python raises.  The simulator hits all three
  routinely (Gipps braking, empty-statistics divisions, ...), so the Java
  behaviour has to be kept or the run dies.
* ``%`` on negative integers truncates towards zero in Java, floors in Python.
* ``java.util.Random`` is a specific 48-bit LCG.  Reproducing it exactly means
  a given seed produces the same vehicle types, colours, positions and
  Gaussian noise as the Java build.
* ``String.format("%.3f", x)`` rounds HALF_UP and prints ``NaN``/``Infinity``
  for non-finite values; the reference ``statistics/*.csv`` files contain
  literal ``NaN`` entries.

Everything here exists to keep the ported simulation faithful to that.
"""

from __future__ import annotations

import math
import os
import struct
import time
from decimal import Context, Decimal, ROUND_HALF_UP

INT_MAX = 2147483647
INT_MIN = -2147483648
LONG_MAX = 9223372036854775807
LONG_MIN = -9223372036854775808

#: ``Double.MAX_VALUE``
DOUBLE_MAX_VALUE = 1.7976931348623157e308
#: ``Double.MIN_VALUE`` -- the smallest *positive* denormal, not a negative
#: number.  Java code that seeds a "maximum" accumulator with it is really
#: seeding it with a value just above zero.
DOUBLE_MIN_VALUE = 5e-324

NaN = float("nan")
INF = float("inf")


# --------------------------------------------------------------------------
# numeric casts and rounding
# --------------------------------------------------------------------------

def jround(d: float) -> int:
    """``Math.round(double)`` -> ``long``: ``floor(d + 0.5)``."""
    if math.isnan(d):
        return 0
    if d == INF:
        return LONG_MAX
    if d == -INF:
        return LONG_MIN
    r = math.floor(d + 0.5)
    if r > LONG_MAX:
        return LONG_MAX
    if r < LONG_MIN:
        return LONG_MIN
    return int(r)


def jint(d: float) -> int:
    """``(int) someDouble``: truncate towards zero, saturating at int range."""
    if math.isnan(d):
        return 0
    if d >= INT_MAX:
        return INT_MAX
    if d <= INT_MIN:
        return INT_MIN
    return int(d)  # int() already truncates towards zero


def to_int32(value: int) -> int:
    """``(int) someLong``: keep the low 32 bits, interpreted as signed."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def jround_to_int(d: float) -> int:
    """``(int) Math.round(double)`` -- the round saturates in ``long``, then
    the narrowing cast keeps only the low 32 bits."""
    return to_int32(jround(d))


def jmod(a: int, b: int) -> int:
    """Java ``%`` on integers: the result takes the sign of the dividend."""
    r = abs(a) % abs(b)
    return -r if a < 0 else r


def jabs_int(i: int) -> int:
    """``Math.abs(int)`` -- ``Integer.MIN_VALUE`` stays negative."""
    return INT_MIN if i == INT_MIN else abs(i)


# --------------------------------------------------------------------------
# IEEE-754 arithmetic that does not raise
# --------------------------------------------------------------------------

def jdiv(a: float, b: float) -> float:
    """Java ``double`` division: no exception, just Infinity/NaN."""
    if b == 0.0:
        if math.isnan(a) or a == 0.0:
            return NaN
        neg = (a < 0.0) != (math.copysign(1.0, b) < 0.0)
        return -INF if neg else INF
    return a / b


def jsqrt(x: float) -> float:
    """``Math.sqrt`` -- NaN instead of an exception for negative input."""
    if x < 0.0 or math.isnan(x):
        return NaN
    if x == INF:
        return INF
    return math.sqrt(x)


def jlog(x: float) -> float:
    """``Math.log`` -- NaN for negative input, -Infinity at zero."""
    if math.isnan(x) or x < 0.0:
        return NaN
    if x == 0.0:
        return -INF
    if x == INF:
        return INF
    return math.log(x)


def jexp(x: float) -> float:
    """``Math.exp`` -- Infinity on overflow instead of an exception."""
    try:
        return math.exp(x)
    except OverflowError:
        return INF


def jpow(a: float, b: float) -> float:
    """``Math.pow`` with Java's edge-case results rather than exceptions."""
    if b == 0.0:
        return 1.0
    if math.isnan(a) or math.isnan(b):
        return NaN
    try:
        result = math.pow(a, b)
    except (OverflowError, ValueError):
        if a == 0.0:
            # 0 ** negative
            return INF
        if abs(a) > 1.0:
            return INF if b > 0 else 0.0
        return 0.0 if b > 0 else INF
    return result


def jtanh(x: float) -> float:
    if math.isnan(x):
        return NaN
    return math.tanh(x)


def jmin(a: float, b: float) -> float:
    """``Math.min(double, double)``.

    Unlike Python's :func:`min`, Java propagates NaN.  This matters a lot: the
    Gipps braking speed is NaN whenever the discriminant goes negative, and
    ``max(0, min(v_a, NaN))`` is NaN in Java (which ``precision2`` then turns
    into 0) but ``v_a`` in Python.
    """
    if math.isnan(a) or math.isnan(b):
        return NaN
    if a == 0.0 and b == 0.0 and math.copysign(1.0, b) < 0:
        return b  # -0.0 is smaller than 0.0
    return a if a <= b else b


def jmax(a: float, b: float) -> float:
    """``Math.max(double, double)`` -- propagates NaN, prefers +0.0 over -0.0."""
    if math.isnan(a) or math.isnan(b):
        return NaN
    if a == 0.0 and b == 0.0 and math.copysign(1.0, a) < 0:
        return b
    return a if a >= b else b


def jto_radians(angdeg: float) -> float:
    """``Math.toRadians`` -- ``angdeg / 180.0 * PI``, not ``angdeg * (PI/180)``;
    the two round differently in the last bit."""
    return angdeg / 180.0 * math.pi


def jhypot(x: float, y: float) -> float:
    return math.hypot(x, y)


# --------------------------------------------------------------------------
# text output
# --------------------------------------------------------------------------

_FORMAT_CONTEXT = Context(prec=400, rounding=ROUND_HALF_UP)


def jformat(value: float, decimals: int) -> str:
    """``String.format("%.<decimals>f", value)``.

    Java's ``Formatter`` does not round the exact binary value: it first takes
    the shortest decimal that round-trips (``FormattedFloatingDecimal``) and
    rounds *that* HALF_UP.  ``%.2f`` of ``1.005`` is therefore ``1.01`` in
    Java even though the stored double is ``1.00499999...``.  Non-finite
    values are spelled ``NaN`` / ``Infinity`` / ``-Infinity``.
    """
    if math.isnan(value):
        return "NaN"
    if value == INF:
        return "Infinity"
    if value == -INF:
        return "-Infinity"
    quantum = Decimal(1).scaleb(-decimals)
    shortest = Decimal(repr(value))
    return str(shortest.quantize(quantum, rounding=ROUND_HALF_UP,
                                 context=_FORMAT_CONTEXT))


def jstr(d: float) -> str:
    """``Double.toString(double)`` / string concatenation of a double.

    Java uses the shortest decimal that round-trips (as does :func:`repr`
    since CPython 3.1) but formats the exponent differently and always keeps a
    fractional digit.
    """
    if math.isnan(d):
        return "NaN"
    if d == INF:
        return "Infinity"
    if d == -INF:
        return "-Infinity"
    if d == 0.0:
        return "-0.0" if math.copysign(1.0, d) < 0 else "0.0"

    sign = "-" if d < 0 else ""
    magnitude = abs(d)
    shortest = repr(magnitude)
    if "e" in shortest:
        digits, _, exponent = shortest.partition("e")
        exp = int(exponent)
    else:
        digits, exp = shortest, 0
    # normalise ``digits`` to a plain d.dddd mantissa with its own exponent
    if "." in digits:
        int_part, frac_part = digits.split(".")
    else:
        int_part, frac_part = digits, ""
    all_digits = (int_part + frac_part).lstrip("0")
    leading_zeros = len(int_part + frac_part) - len((int_part + frac_part).lstrip("0"))
    # decimal exponent of the first significant digit
    point_pos = len(int_part) - leading_zeros + exp
    all_digits = all_digits.rstrip("0") or "0"

    if 1e-3 <= magnitude < 1e7:
        if point_pos <= 0:
            return sign + "0." + "0" * (-point_pos) + all_digits
        if point_pos >= len(all_digits):
            return sign + all_digits + "0" * (point_pos - len(all_digits)) + ".0"
        return sign + all_digits[:point_pos] + "." + all_digits[point_pos:]

    mantissa = all_digits[0] + "." + (all_digits[1:] or "0")
    return f"{sign}{mantissa}E{point_pos - 1}"


def jbool(text: str) -> bool:
    """``Boolean.parseBoolean`` -- only "true" (any case) is true."""
    return text.strip().lower() == "true"


def jbool_str(value: bool) -> str:
    return "true" if value else "false"


# --------------------------------------------------------------------------
# java.awt.Color
# --------------------------------------------------------------------------

class Color:
    """The tiny slice of ``java.awt.Color`` the simulator uses."""

    __slots__ = ("r", "g", "b")

    def __init__(self, r, g, b, *, float_components: bool = False):
        if float_components or isinstance(r, float) or isinstance(g, float) or isinstance(b, float):
            r = jint(r * 255 + 0.5)
            g = jint(g * 255 + 0.5)
            b = jint(b * 255 + 0.5)
        self.r = int(r)
        self.g = int(g)
        self.b = int(b)

    def get_red(self) -> int:
        return self.r

    def get_green(self) -> int:
        return self.g

    def get_blue(self) -> int:
        return self.b

    def to_hex(self) -> str:
        return "#%02x%02x%02x" % (self.r, self.g, self.b)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Color({self.r},{self.g},{self.b})"


Color.BLACK = Color(0, 0, 0)
Color.WHITE = Color(255, 255, 255)
Color.RED = Color(255, 0, 0)
Color.GREEN = Color(0, 255, 0)
Color.BLUE = Color(0, 0, 255)
Color.CYAN = Color(0, 255, 255)
Color.MAGENTA = Color(255, 0, 255)
Color.DARK_GRAY = Color(64, 64, 64)


# --------------------------------------------------------------------------
# java.util.Random
# --------------------------------------------------------------------------

_MULTIPLIER = 0x5DEECE66D
_ADDEND = 0xB
_MASK = (1 << 48) - 1
_DOUBLE_UNIT = 1.0 / (1 << 53)

_seed_uniquifier = 8682522807148012


def _new_random_seed() -> int:
    """Stand-in for Java's ``seedUniquifier() ^ System.nanoTime()``."""
    global _seed_uniquifier
    _seed_uniquifier = (_seed_uniquifier * 1181783497276652981) & 0xFFFFFFFFFFFFFFFF
    return (_seed_uniquifier ^ time.perf_counter_ns()
            ^ int.from_bytes(os.urandom(4), "little"))


class JavaRandom:
    """Bit-exact port of ``java.util.Random``.

    Given the same seed this produces the same sequence of ``nextInt``,
    ``nextDouble``, ``nextFloat``, ``nextBoolean`` and ``nextGaussian`` values
    as the Java implementation, so a seeded Python run walks the simulation
    down exactly the same path as a seeded Java run.
    """

    __slots__ = ("_seed", "_have_next_gaussian", "_next_gaussian")

    def __init__(self, seed: int | None = None):
        self.set_seed(_new_random_seed() if seed is None else seed)

    def set_seed(self, seed: int) -> None:
        self._seed = (int(seed) ^ _MULTIPLIER) & _MASK
        self._have_next_gaussian = False
        self._next_gaussian = 0.0

    def _next(self, bits: int) -> int:
        seed = (self._seed * _MULTIPLIER + _ADDEND) & _MASK
        self._seed = seed
        return seed >> (48 - bits)

    def next_int(self) -> int:
        """``nextInt()`` -- a signed 32-bit value."""
        value = self._next(32)
        return value - 0x100000000 if value >= 0x80000000 else value

    def next_int_bound(self, bound: int) -> int:
        """``nextInt(int bound)`` -- 0 (inclusive) to *bound* (exclusive)."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        r = self._next(31)
        m = bound - 1
        if (bound & m) == 0:  # power of two
            return (bound * r) >> 31
        u = r
        r = u % bound
        while u - r + m > INT_MAX:
            u = self._next(31)
            r = u % bound
        return r

    def next_int_range(self, origin: int, bound: int) -> int:
        """``nextInt(int origin, int bound)`` (Java 17+)."""
        if origin >= bound:
            raise ValueError("bound must be greater than origin")
        return origin + self.next_int_bound(bound - origin)

    def next_long(self) -> int:
        high = self.next_int()
        low = self._next(32)
        value = ((high << 32) + (low - 0x100000000 if low >= 0x80000000 else low))
        value &= 0xFFFFFFFFFFFFFFFF
        return value - 0x10000000000000000 if value >= 0x8000000000000000 else value

    def next_boolean(self) -> bool:
        return self._next(1) != 0

    def next_float(self) -> float:
        return self._next(24) / float(1 << 24)

    def next_double(self) -> float:
        return ((self._next(26) << 27) + self._next(27)) * _DOUBLE_UNIT

    def next_double_range(self, origin: float, bound: float) -> float:
        """``nextDouble(double origin, double bound)`` (Java 17+)."""
        r = self.next_double()
        if origin < bound:
            if (bound - origin) < INF:
                r = r * (bound - origin) + origin
            else:
                r = (r * (0.5 * bound - 0.5 * origin) + 0.5 * origin) * 2.0
            if r >= bound:
                r = math.nextafter(bound, -INF)
        return r

    def next_gaussian(self) -> float:
        """``nextGaussian()`` -- Marsaglia polar method, second value cached."""
        if self._have_next_gaussian:
            self._have_next_gaussian = False
            return self._next_gaussian
        while True:
            v1 = 2 * self.next_double() - 1
            v2 = 2 * self.next_double() - 1
            s = v1 * v1 + v2 * v2
            if s < 1 and s != 0:
                break
        multiplier = math.sqrt(-2 * math.log(s) / s)
        self._next_gaussian = v2 * multiplier
        self._have_next_gaussian = True
        return v1 * multiplier


# --------------------------------------------------------------------------
# the two Apache commons-math3 distributions the simulator samples from
# --------------------------------------------------------------------------

class PoissonDistribution:
    """``org.apache.commons.math3.distribution.PoissonDistribution.sample()``.

    Same algorithm (multiplication method below the pivot, Devroye's above);
    the uniform source is a :class:`JavaRandom` rather than ``Well19937c``.
    Commons-math seeds ``Well19937c`` from the clock when no generator is
    supplied, so the Java stream is not reproducible either -- only the
    distribution matters.
    """

    __slots__ = ("mean", "_random")

    PIVOT = 40.0

    def __init__(self, mean: float, random: JavaRandom | None = None):
        if mean <= 0:
            raise ValueError("mean must be strictly positive")
        self.mean = mean
        self._random = random if random is not None else JavaRandom()

    def sample(self) -> int:
        return min(self._next_poisson(self.mean), INT_MAX)

    def _next_poisson(self, mean_poisson: float) -> int:
        if mean_poisson < self.PIVOT:
            p = jexp(-mean_poisson)
            n = 0
            r = 1.0
            limit = 1000 * mean_poisson
            while n < limit:
                r *= self._random.next_double()
                if r >= p:
                    n += 1
                else:
                    break
            return n

        # Devroye's method for large means; unused with the shipped
        # parameters but kept so the port is complete.
        lambda_ = math.floor(mean_poisson)
        lambda_fractional = mean_poisson - lambda_
        log_lambda = jlog(lambda_)
        y2 = 0 if lambda_fractional < 1.0e-12 else PoissonDistribution(
            lambda_fractional, self._random).sample()
        n = int(math.sqrt(lambda_ * jlog(1.0 / 1.0e-12)))
        delta = max(1.0, float(n))
        half_delta = delta / 2
        twolpd = 2 * lambda_ + delta
        a1 = math.sqrt(math.pi * twolpd) * jexp(1 / (8 * lambda_))
        a2 = (twolpd / delta) * jexp(-delta * (1 + delta) / twolpd)
        a_sum = a1 + a2 + 1
        p1 = a1 / a_sum
        p2 = a2 / a_sum
        c1 = 1 / (8 * lambda_)

        x = y = 0.0
        v = 0.0
        a = 0
        t = 0.0
        qr = 0.0
        qa = 0.0
        while True:
            u = self._random.next_double()
            if u <= p1:
                n_gauss = self._random.next_gaussian()
                x = n_gauss * math.sqrt(lambda_ + half_delta) - 0.5
                if x > delta or x < -lambda_:
                    continue
                y = math.floor(x) if x < 0 else math.ceil(x)
                e = _exponential_sample(self._random)
                v = -e - (n_gauss * n_gauss) / 2 + c1
            else:
                if u > p1 + p2:
                    y = lambda_
                    break
                x = delta + (twolpd / delta) * _exponential_sample(self._random)
                y = math.ceil(x)
                v = -_exponential_sample(self._random) - delta * (x + 1) / twolpd
            a = 1 if y < 0 else 0
            t = y * (y + 1) / (2 * lambda_)
            if v < -t and a == 0:
                y = lambda_ + y
                break
            qr = t * ((2 * y + 1) / (6 * lambda_) - 1)
            qa = qr - (t * t) / (3 * (lambda_ + a * (y + 1)))
            if v < qa:
                y = lambda_ + y
                break
            if v > qr:
                continue
            if v < _factorial_log_ratio(y, lambda_, log_lambda):
                y = lambda_ + y
                break
        return int(y2 + y)


def _exponential_sample(random: JavaRandom) -> float:
    return -jlog(random.next_double())


def _factorial_log_ratio(y: float, lambda_: float, log_lambda: float) -> float:
    return y * log_lambda - math.lgamma(y + 1) - (-lambda_)


class GammaDistribution:
    """``GammaDistribution.sample()`` (Marsaglia-Tsang for shape >= 1).

    The Java build constructs this with ``new Well19937c(seed)`` where *seed*
    is still 0 at class-initialisation time, so seeding the Python generator
    with 0 mirrors that.  The generator differs, the distribution does not.
    """

    __slots__ = ("shape", "scale", "_random")

    def __init__(self, shape: float, scale: float, seed: int | None = None):
        self.shape = shape
        self.scale = scale
        self._random = JavaRandom(seed)

    def sample(self) -> float:
        random = self._random
        if self.shape < 1:
            # Ahrens-Dieter GS
            while True:
                u = random.next_double()
                bGS = 1 + self.shape / math.e
                p = bGS * u
                if p <= 1:
                    x = jpow(p, 1 / self.shape)
                    u2 = random.next_double()
                    if u2 > jexp(-x):
                        continue
                    return self.scale * x
                x = -jlog((bGS - p) / self.shape)
                u2 = random.next_double()
                if u2 > jpow(x, self.shape - 1):
                    continue
                return self.scale * x

        d = self.shape - 0.333333333333333333
        c = 1 / (3 * jsqrt(d))
        while True:
            x = random.next_gaussian()
            v = (1 + c * x) ** 3
            if v <= 0:
                continue
            x2 = x * x
            u = random.next_double()
            if u < 1 - 0.0331 * x2 * x2:
                return self.scale * d * v
            if jlog(u) < 0.5 * x2 + d * (1 - v + jlog(v)):
                return self.scale * d * v


# --------------------------------------------------------------------------
# java.awt.geom.Line2D.linesIntersect
# --------------------------------------------------------------------------

def _relative_ccw(px, py, x1, y1, x2, y2) -> int:
    """``Line2D.relativeCCW``."""
    x2 -= x1
    y2 -= y1
    px -= x1
    py -= y1
    ccw = px * y2 - py * x2
    if ccw == 0.0:
        # The point is colinear; classify based on which side of the segment
        # it falls outside of.
        ccw = px * x2 + py * y2
        if ccw > 0.0:
            px -= x2
            py -= y2
            ccw = px * x2 + py * y2
            if ccw < 0.0:
                ccw = 0.0
    if ccw < 0.0:
        return -1
    if ccw > 0.0:
        return 1
    return 0


def lines_intersect(x1, y1, x2, y2, x3, y3, x4, y4) -> bool:
    """``java.awt.geom.Line2D.linesIntersect``."""
    return ((_relative_ccw(x1, y1, x2, y2, x3, y3)
             * _relative_ccw(x1, y1, x2, y2, x4, y4) <= 0)
            and (_relative_ccw(x3, y3, x4, y4, x1, y1)
                 * _relative_ccw(x3, y3, x4, y4, x2, y2) <= 0))


def double_bits(d: float) -> int:  # pragma: no cover - debugging aid
    return struct.unpack("<q", struct.pack("<d", d))[0]
