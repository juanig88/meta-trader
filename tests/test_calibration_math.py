from __future__ import annotations

import math

from trading.domain.calibration import (
    base_rate,
    brier,
    brier_skill,
    empirical_recalibration,
    flag_lift,
    laplace,
    normal_two_sided_p,
    quantile_buckets,
    reliability,
    two_proportion_z,
)


def test_brier_and_skill_table():
    cases = [
        ("perfect", [(1.0, 1.0), (0.0, 0.0)], 0.0, 1.0),
        ("always wrong", [(1.0, 0.0), (0.0, 1.0)], 1.0, -3.0),
        ("base rate forecaster", [(0.5, 1.0), (0.5, 0.0)], 0.25, 0.0),
    ]
    for name, pairs, want_b, want_s in cases:
        assert math.isclose(brier(pairs), want_b), name
        assert math.isclose(brier_skill(pairs), want_s), name
    assert brier([]) is None
    assert base_rate([]) is None
    assert brier_skill([]) is None
    assert brier_skill([(0.3, 1.0), (0.9, 1.0)]) is None


def test_reliability_last_bucket_closed():
    rows = reliability([(1.0, 1.0), (0.0, 0.0), (0.55, 1.0)], buckets=5)
    assert len(rows) == 5
    assert rows[-1]["n"] == 1 and rows[-1]["mean_y"] == 1.0
    assert rows[0]["n"] == 1
    assert rows[2]["n"] == 1
    assert all(b["n"] == 0 and b["mean_y"] is None for b in reliability([]))


def test_two_proportion_table():
    cases = [
        ("empty", (0, 0, 1, 2), None),
        ("zero var", (0, 10, 0, 10), None),
        ("equal", (5, 10, 5, 10), 0.0),
    ]
    for name, args, want in cases:
        got = two_proportion_z(*args)
        if want is None:
            assert got is None, name
        else:
            assert math.isclose(got, want), name
    z = two_proportion_z(80, 100, 50, 100)
    assert z is not None and z > 4
    assert normal_two_sided_p(z) < 1e-4
    assert normal_two_sided_p(None) is None
    assert math.isclose(normal_two_sided_p(0.0), 1.0)


def test_laplace_edges():
    assert laplace(0, 0) == 0.5
    assert laplace(3, 3) == 0.8
    assert laplace(0, -1) is None


def test_flag_lift_counts_and_skip_usd():
    rows = [(True, -0.1), (True, -0.2), (True, 0.05), (False, 0.1), (False, -0.05)]
    got = flag_lift(rows)
    assert got["flag_on"]["n"] == 3 and got["flag_on"]["losses"] == 2
    assert got["flag_off"]["n"] == 2 and got["flag_off"]["losses"] == 1
    assert math.isclose(got["skip_on_flag_would_remove_usd"], -0.25)
    assert got["loss_rate_diff"] == round(2 / 3 - 0.5, 4)
    assert got["flag_on_worse"] is True
    empty = flag_lift([])
    assert empty["z"] is None and empty["flag_on"]["n"] == 0
    assert empty["flag_on_worse"] is False


def test_flag_on_worse_needs_rate_and_mean():
    cases = [
        ("flag loses less often", [(True, -0.1), (True, 0.1), (False, -0.1), (False, -0.1)], False),
        ("more losses but better mean", [(True, -0.01), (True, -0.01), (False, -0.5), (False, 0.1)], False),
        ("worse on both", [(True, -0.2), (True, -0.2), (False, -0.1), (False, 0.1)], True),
        ("one side empty", [(True, -0.2)], False),
    ]
    for name, rows, want in cases:
        assert flag_lift(rows)["flag_on_worse"] is want, name


def test_empirical_recalibration_groups():
    got = empirical_recalibration([(0.8, 1.0), (0.8, 0.0), (1.0, 1.0)])
    assert got["0.8000"]["n"] == 2 and got["0.8000"]["observed"] == 0.5
    assert got["1.0000"]["observed"] == round(2 / 3, 4)
    assert empirical_recalibration([]) == {}


def test_quantile_buckets_sorted_and_nan_dropped():
    rows = [(float(i), 1.0 if i % 2 else -1.0) for i in range(10)] + [(float("nan"), 5.0)]
    got = quantile_buckets(rows, buckets=5)
    assert len(got) == 5
    assert sum(b["n"] for b in got) == 10
    assert got[0]["score_lo"] == 0.0 and got[-1]["score_hi"] == 9.0
    assert quantile_buckets([]) == []
    assert len(quantile_buckets([(1.0, 1.0)], buckets=5)) == 1
