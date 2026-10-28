from __future__ import annotations

from trading.domain.expected_value import (
    breakeven_p,
    expected_value,
    kelly_fraction,
    quarter_kelly_risk_pct,
)


def test_expected_value_table():
    cases = [
        ("fair coin 1:1", 0.5, 1.0, 1.0, 0.0),
        ("benter overlay", 0.5, 1.2, 1.0, 0.1),
        ("high wr bad rr", 0.8, 1.0, 5.0, -0.2),
        ("always win", 1.0, 2.0, 10.0, 2.0),
        ("always lose", 0.0, 2.0, 3.0, -3.0),
    ]
    for name, p, w, loss, want in cases:
        got = expected_value(p, w, loss)
        assert abs(got - want) < 1e-12, name


def test_expected_value_rejects_bad_p():
    cases = [(-0.01, 1.0, 1.0), (1.01, 1.0, 1.0)]
    for p, w, loss in cases:
        try:
            expected_value(p, w, loss)
            assert False, p
        except ValueError:
            pass


def test_breakeven_p_table():
    cases = [
        ("1:1", 1.0, 1.0, 0.5),
        ("2:1", 2.0, 1.0, 1.0 / 3.0),
        ("no win", 0.0, 2.0, 1.0),
        ("no loss", 2.0, 0.0, 0.0),
    ]
    for name, w, loss, want in cases:
        assert abs(breakeven_p(w, loss) - want) < 1e-12, name


def test_kelly_table():
    cases = [
        ("fair 1:1", 0.5, 1.0, 0.0),
        ("edge 1:1", 0.6, 1.0, 0.2),
        ("no odds", 0.9, 0.0, 0.0),
    ]
    for name, p, b, want in cases:
        assert abs(kelly_fraction(p, b) - want) < 1e-12, name
    q = quarter_kelly_risk_pct(0.6, 1.0, 1.0, fraction=0.25)
    assert abs(q - 5.0) < 1e-12  # 0.25 * 0.2 * 100
