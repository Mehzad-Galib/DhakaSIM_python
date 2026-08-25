"""Pin :mod:`experiments.stats` to closed forms.

There is no SciPy here to check against, so every expected value below is
either hand-arithmetic or a closed form that the general function must
reproduce at particular parameters -- ``I_x(1,1) = x``, the Cauchy and df=2
t-distributions, and so on.  Those hold exactly, so an implementation that
drifts fails rather than merely disagreeing with another library.

Run with ``python tests/test_stats.py``.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.stats import (  # noqa: E402
    betainc, error_summary, kolmogorov_sf, ks_test_2samp, mean_absolute_error,
    mean_absolute_percentage_error, mean_error, root_mean_square_error,
    root_mean_square_percentage_error, student_t_sf, t_test_ind,
)

TOL = 1e-10


def close(got, want, tol=TOL):
    assert abs(got - want) <= tol, f"got {got!r}, want {want!r}"


# --------------------------------------------------------------------------
# incomplete beta
# --------------------------------------------------------------------------

def test_betainc_degenerate_parameters_are_polynomials():
    """I_x(1,1) = x, I_x(2,1) = x^2, I_x(1,2) = 1 - (1-x)^2."""
    for x in (0.01, 0.2, 0.5, 0.75, 0.999):
        close(betainc(1.0, 1.0, x), x)
        close(betainc(2.0, 1.0, x), x * x)
        close(betainc(1.0, 2.0, x), 1.0 - (1.0 - x) ** 2)


def test_betainc_half_half_is_the_arcsine_law():
    """I_x(1/2, 1/2) = (2/pi) * asin(sqrt(x))."""
    for x in (0.05, 0.3, 0.5, 0.8, 0.95):
        close(betainc(0.5, 0.5, x), (2.0 / math.pi) * math.asin(math.sqrt(x)))


def test_betainc_endpoints_and_symmetry():
    close(betainc(2.5, 3.5, 0.0), 0.0)
    close(betainc(2.5, 3.5, 1.0), 1.0)
    # I_x(a,b) + I_{1-x}(b,a) = 1, which also exercises both branches of the
    # continued fraction against each other
    for a, b, x in ((2.0, 3.0, 0.3), (0.5, 7.0, 0.9), (11.0, 2.0, 0.15)):
        close(betainc(a, b, x) + betainc(b, a, 1.0 - x), 1.0)


# --------------------------------------------------------------------------
# t distribution
# --------------------------------------------------------------------------

def test_student_t_one_degree_of_freedom_is_cauchy():
    """df=1: two-sided p = 1 - (2/pi) * atan(|t|)."""
    for t in (0.1, 0.5, 1.0, 2.5, 12.0):
        close(student_t_sf(t, 1), 1.0 - (2.0 / math.pi) * math.atan(abs(t)))


def test_student_t_two_degrees_of_freedom_closed_form():
    """df=2: two-sided p = 1 - |t| / sqrt(t^2 + 2)."""
    for t in (0.25, 1.0, 3.5355339059327378, 9.0):
        close(student_t_sf(t, 2), 1.0 - abs(t) / math.sqrt(t * t + 2.0))


def test_student_t_is_symmetric_and_bounded():
    for t in (0.3, 2.0, 6.0):
        close(student_t_sf(t, 7), student_t_sf(-t, 7))
    close(student_t_sf(0.0, 5), 1.0)
    assert student_t_sf(50.0, 5) < 1e-6


# --------------------------------------------------------------------------
# t-test
# --------------------------------------------------------------------------

def test_t_test_hand_computed():
    """a=[1,3], b=[6,8]: means 2 and 7, both sample variances 2, so the pooled
    variance is 2 and t = -5/sqrt(2) on 2 degrees of freedom."""
    t, p, df = t_test_ind([1, 3], [6, 8])
    close(t, -5.0 / math.sqrt(2.0))
    close(df, 2)
    close(p, 1.0 - abs(t) / math.sqrt(t * t + 2.0))


def test_t_test_argument_order_only_flips_the_sign():
    t1, p1, _ = t_test_ind([1, 3, 5, 7], [2, 3, 9, 11])
    t2, p2, _ = t_test_ind([2, 3, 9, 11], [1, 3, 5, 7])
    close(t1, -t2)
    close(p1, p2)


def test_t_test_identical_samples_find_nothing():
    t, p, _ = t_test_ind([4, 5, 6], [4, 5, 6])
    close(t, 0.0)
    close(p, 1.0)


def test_t_test_constant_identical_samples_do_not_divide_by_zero():
    t, p, _ = t_test_ind([3, 3, 3], [3, 3, 3])
    close(t, 0.0)
    close(p, 1.0)


def test_welch_matches_pooled_for_equal_sizes_and_variances():
    """The two tests coincide exactly when n1 = n2 and the variances match."""
    a, b = [1, 3], [6, 8]
    pooled = t_test_ind(a, b, equal_var=True)
    welch = t_test_ind(a, b, equal_var=False)
    close(pooled[0], welch[0])
    close(pooled[1], welch[1])
    close(pooled[2], welch[2])


def test_welch_differs_when_variances_differ():
    a = [1, 2, 3, 4, 5]
    b = [10, 40, -20, 35, -5]
    assert t_test_ind(a, b, equal_var=True)[2] != t_test_ind(a, b, equal_var=False)[2]


def test_t_test_rejects_single_value_samples():
    try:
        t_test_ind([1], [2, 3])
    except ValueError:
        return
    raise AssertionError("expected ValueError for a one-value sample")


# --------------------------------------------------------------------------
# Kolmogorov-Smirnov
# --------------------------------------------------------------------------

def test_kolmogorov_sf_limits():
    close(kolmogorov_sf(0.0), 1.0)
    close(kolmogorov_sf(-1.0), 1.0)
    close(kolmogorov_sf(10.0), 0.0)
    # Q(1) = 2*(e^-2 - e^-8 + e^-18 - ...)
    want = 2.0 * (math.exp(-2.0) - math.exp(-8.0) + math.exp(-18.0)
                  - math.exp(-32.0))
    close(kolmogorov_sf(1.0), want, tol=1e-12)


def test_kolmogorov_sf_is_decreasing():
    values = [kolmogorov_sf(lam) for lam in (0.5, 0.8, 1.2, 2.0, 3.0)]
    assert values == sorted(values, reverse=True)


def test_ks_disjoint_samples_separate_completely():
    d, p = ks_test_2samp([1, 2, 3, 4], [5, 6, 7, 8])
    close(d, 1.0)
    assert 0.0 <= p <= 1.0


def test_ks_identical_samples():
    d, p = ks_test_2samp([1, 2, 3, 4], [1, 2, 3, 4])
    close(d, 0.0)
    close(p, 1.0)


def test_ks_interleaved_samples():
    """a=[1,2] against b=[1.5,2.5] steps 1, 1.5, 2, 2.5; the CDFs are furthest
    apart after the first step, at 1/2 against 0."""
    d, _ = ks_test_2samp([1, 2], [1.5, 2.5])
    close(d, 0.5)


def test_ks_handles_ties_across_samples():
    """A value in both samples must advance both CDFs before they are compared.

    a=[1,1,2], b=[1,2,2]: at x=1 the CDFs are 2/3 and 1/3, at x=2 both are 1,
    so D is 1/3.  Advancing only one sample first would report 2/3.
    """
    d, _ = ks_test_2samp([1, 1, 2], [1, 2, 2])
    close(d, 1.0 / 3.0)


def test_ks_is_symmetric_in_its_arguments():
    a = [1, 4, 4, 9, 12, 13]
    b = [2, 3, 4, 10, 11, 20]
    close(ks_test_2samp(a, b)[0], ks_test_2samp(b, a)[0])
    close(ks_test_2samp(a, b)[1], ks_test_2samp(b, a)[1])


# --------------------------------------------------------------------------
# error measures
# --------------------------------------------------------------------------

# simulated - observed = [+1, 0, -3]; observed = [1, 4, 9]
SIM = [2.0, 4.0, 6.0]
OBS = [1.0, 4.0, 9.0]


def test_mean_error_keeps_the_sign_of_the_bias():
    close(mean_error(SIM, OBS), -2.0 / 3.0)
    close(mean_error(OBS, SIM), 2.0 / 3.0)


def test_absolute_and_square_errors():
    close(mean_absolute_error(SIM, OBS), 4.0 / 3.0)
    close(root_mean_square_error(SIM, OBS), math.sqrt(10.0 / 3.0))


def test_percentage_errors():
    close(mean_absolute_percentage_error(SIM, OBS),
          100.0 * (1.0 + 0.0 + 1.0 / 3.0) / 3.0)
    close(root_mean_square_percentage_error(SIM, OBS),
          100.0 * math.sqrt((1.0 + 0.0 + 1.0 / 9.0) / 3.0))


def test_percentage_errors_refuse_a_zero_observation():
    for fn in (mean_absolute_percentage_error, root_mean_square_percentage_error):
        try:
            fn([1.0, 2.0], [0.0, 2.0])
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__} should reject a zero observation")


def test_perfect_prediction_is_zero_everywhere():
    for name, value in error_summary(OBS, OBS).items():
        close(value, 0.0)


def test_error_summary_reports_all_five():
    assert set(error_summary(SIM, OBS)) == {"ME", "MAE", "RMSE", "MAPE", "RMSPE"}


def test_mismatched_lengths_are_rejected():
    try:
        mean_error([1.0, 2.0], [1.0])
    except ValueError:
        return
    raise AssertionError("expected ValueError for unequal lengths")


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
