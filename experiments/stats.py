#!/usr/bin/env python3
"""Goodness-of-fit and hypothesis tests, standard library only.

The RoadBird study reports five error measures (ME, MAE, RMSE, MAPE, RMSPE)
plus a two-sample t-test and a two-sample Kolmogorov-Smirnov test.  The project
takes no third-party dependencies, so the special functions those need -- the
regularised incomplete beta for the t distribution, and the Kolmogorov series
for K-S -- are implemented here.

Both are the standard continued-fraction and series forms; the values they
produce are pinned in ``tests/test_stats.py`` against closed forms that hold for
particular parameters, so a drift shows up as a test failure rather than as a
quietly wrong p-value.

As a command line tool it splits the harness's ``results.csv`` on one factor and
compares the two halves::

    python experiments/stats.py experiments/results/results.csv --by lane_mode
"""

from __future__ import annotations

import argparse
import csv
import math

# --------------------------------------------------------------------------
# special functions
# --------------------------------------------------------------------------

_FPMIN = 1e-300
_EPS = 3e-16
_MAXIT = 300


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        # even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        # odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta ``I_x(a, b)``.

    The continued fraction converges quickly only on one side of the
    distribution's mean, so past that point the symmetry
    ``I_x(a,b) = 1 - I_{1-x}(b,a)`` is used instead.
    """
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"x must be in [0, 1], got {x}")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    log_beta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(log_beta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """Two-sided tail probability of Student's t: ``P(|T| >= |t|)``."""
    if df <= 0:
        return float("nan")
    if math.isinf(t):
        return 0.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def kolmogorov_sf(lam: float) -> float:
    """``Q(lam) = 2 * sum (-1)^(j-1) exp(-2 j^2 lam^2)``.

    The limiting distribution of the K-S statistic.  The series is divergent at
    the origin (Abel-summable to 1) and underflows for large arguments, so both
    tails are returned by their limits.
    """
    if lam <= 0.0:
        return 1.0
    if lam < 0.04:      # series useless here, and Q is 1 to many digits
        return 1.0
    if lam > 3.5:       # first term is already below 1e-21
        return 0.0
    total = 0.0
    for j in range(1, 200):
        term = math.exp(-2.0 * j * j * lam * lam)
        total += term if j % 2 else -term      # (-1)^(j-1)
        if term < _EPS * abs(total) or term < 1e-300:
            break
    return max(0.0, min(1.0, 2.0 * total))


# --------------------------------------------------------------------------
# error measures
# --------------------------------------------------------------------------

def _paired(simulated, observed):
    sim = list(simulated)
    obs = list(observed)
    if len(sim) != len(obs):
        raise ValueError(f"unequal lengths: {len(sim)} vs {len(obs)}")
    if not sim:
        raise ValueError("no observations")
    return sim, obs


def mean_error(simulated, observed) -> float:
    """Mean of ``simulated - observed``; sign shows the direction of bias."""
    sim, obs = _paired(simulated, observed)
    return sum(s - o for s, o in zip(sim, obs)) / len(sim)


def mean_absolute_error(simulated, observed) -> float:
    sim, obs = _paired(simulated, observed)
    return sum(abs(s - o) for s, o in zip(sim, obs)) / len(sim)


def root_mean_square_error(simulated, observed) -> float:
    sim, obs = _paired(simulated, observed)
    return math.sqrt(sum((s - o) ** 2 for s, o in zip(sim, obs)) / len(sim))


def mean_absolute_percentage_error(simulated, observed) -> float:
    """MAPE as a percentage.

    Undefined where an observation is zero -- raises rather than dropping the
    point, since silently averaging over fewer values would overstate the fit.
    """
    sim, obs = _paired(simulated, observed)
    if any(o == 0 for o in obs):
        raise ValueError("MAPE is undefined when an observed value is zero")
    return 100.0 * sum(abs((s - o) / o) for s, o in zip(sim, obs)) / len(sim)


def root_mean_square_percentage_error(simulated, observed) -> float:
    sim, obs = _paired(simulated, observed)
    if any(o == 0 for o in obs):
        raise ValueError("RMSPE is undefined when an observed value is zero")
    return 100.0 * math.sqrt(
        sum(((s - o) / o) ** 2 for s, o in zip(sim, obs)) / len(sim))


def error_summary(simulated, observed) -> dict:
    """All five measures at once, as the paper's Table IV reports them."""
    return {
        "ME": mean_error(simulated, observed),
        "MAE": mean_absolute_error(simulated, observed),
        "RMSE": root_mean_square_error(simulated, observed),
        "MAPE": mean_absolute_percentage_error(simulated, observed),
        "RMSPE": root_mean_square_percentage_error(simulated, observed),
    }


# --------------------------------------------------------------------------
# hypothesis tests
# --------------------------------------------------------------------------

def _mean_var(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)   # sample variance
    return n, mean, var


def t_test_ind(a, b, equal_var: bool = True):
    """Two-sample t-test.  Returns ``(t, p, df)``, p two-sided.

    ``equal_var`` picks Student's pooled test (the default, and what most
    statistics packages do unless told otherwise) or Welch's, which does not
    assume the two variances match.
    """
    a, b = list(a), list(b)
    if len(a) < 2 or len(b) < 2:
        raise ValueError("each sample needs at least two values")
    n1, m1, v1 = _mean_var(a)
    n2, m2, v2 = _mean_var(b)
    if equal_var:
        df = n1 + n2 - 2
        pooled = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
        denominator = math.sqrt(pooled * (1.0 / n1 + 1.0 / n2))
    else:
        e1, e2 = v1 / n1, v2 / n2
        denominator = math.sqrt(e1 + e2)
        df = ((e1 + e2) ** 2 / (e1 * e1 / (n1 - 1) + e2 * e2 / (n2 - 1))
              if e1 + e2 > 0 else float("nan"))
    if denominator == 0:
        # identical constant samples: no difference to detect
        return 0.0, 1.0, df
    t = (m1 - m2) / denominator
    return t, student_t_sf(t, df), df


def ks_test_2samp(a, b):
    """Two-sample Kolmogorov-Smirnov test.  Returns ``(D, p)``.

    ``D`` is exact.  ``p`` uses the asymptotic Kolmogorov distribution with the
    usual small-sample correction, so it is an approximation: reliable once
    ``n1*n2/(n1+n2)`` is above roughly 10, optimistic below that.  An exact
    p-value for small samples needs the combinatorial method, which this does
    not implement -- treat borderline results on short samples with suspicion.
    """
    a, b = sorted(a), sorted(b)
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        raise ValueError("both samples must be non-empty")
    i = j = 0
    cdf1 = cdf2 = 0.0
    d = 0.0
    while i < n1 and j < n2:
        # Step whichever sample has the smaller next value, past all of its
        # copies of it.  A value present in both must advance *both* before the
        # CDFs are compared, or the sample that moved first appears to lead by
        # the whole height of the tie -- which on ties-heavy data (link counts,
        # rounded speeds) inflates D substantially.
        if a[i] < b[j]:
            value = a[i]
            while i < n1 and a[i] == value:
                i += 1
            cdf1 = i / n1
        elif b[j] < a[i]:
            value = b[j]
            while j < n2 and b[j] == value:
                j += 1
            cdf2 = j / n2
        else:
            value = a[i]
            while i < n1 and a[i] == value:
                i += 1
            while j < n2 and b[j] == value:
                j += 1
            cdf1, cdf2 = i / n1, j / n2
        d = max(d, abs(cdf1 - cdf2))
    effective_n = math.sqrt(n1 * n2 / (n1 + n2))
    return d, kolmogorov_sf((effective_n + 0.12 + 0.11 / effective_n) * d)


# --------------------------------------------------------------------------
# command line: compare two halves of a sweep
# --------------------------------------------------------------------------

def compare_results(path: str, factor: str, alpha: float = 0.05):
    """Split the harness's results.csv on `factor` and test the two halves."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{path} has no rows")
    if factor not in rows[0]:
        raise SystemExit(f"no column {factor!r}; have {', '.join(rows[0])}")

    levels = sorted({row[factor] for row in rows})
    if len(levels) != 2:
        raise SystemExit(f"{factor} has {len(levels)} levels ({', '.join(levels)}); "
                         "this compares exactly two")
    left, right = levels

    metrics = sorted({row["metric"] for row in rows})
    print(f"{factor}: {left} vs {right}   (alpha = {alpha})\n")
    header = (f"{'metric':<24}{'mean ' + left:>16}{'mean ' + right:>16}"
              f"{'t':>9}{'p(t)':>10}{'D':>8}{'p(KS)':>10}  verdict")
    print(header)
    print("-" * len(header))
    for metric in metrics:
        x = [float(r["value"]) for r in rows
             if r["metric"] == metric and r[factor] == left
             and _finite(r["value"])]
        y = [float(r["value"]) for r in rows
             if r["metric"] == metric and r[factor] == right
             and _finite(r["value"])]
        if len(x) < 2 or len(y) < 2:
            print(f"{metric:<24}{'too few values':>16}")
            continue
        mx, my = sum(x) / len(x), sum(y) / len(y)
        t, p_t, _ = t_test_ind(x, y)
        d, p_ks = ks_test_2samp(x, y)
        verdict = "differ" if p_t < alpha else "no evidence"
        print(f"{metric:<24}{mx:>16.4f}{my:>16.4f}{t:>9.3f}{p_t:>10.4f}"
              f"{d:>8.3f}{p_ks:>10.4f}  {verdict}")


def _finite(text: str) -> bool:
    try:
        value = float(text)
    except ValueError:
        return False
    return value == value and not math.isinf(value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("results", help="results.csv from experiments/roadbird.py")
    parser.add_argument("--by", default="lane_mode",
                        help="column to split on (default lane_mode)")
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args(argv)
    compare_results(args.results, args.by, args.alpha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
