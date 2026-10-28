"""Expected value and fractional-Kelly sizing.

For a unit stake that pays net W on a win and loses L (L > 0):
    EV = p·W − (1 − p)·L, so a trade is +EV iff p > L / (W + L).

Kelly uses the same p, W, L: f* = p − (1 − p)/b with b = W/L.
Fractional Kelly caps risk downward only — it never increases size.
"""

from __future__ import annotations


def expected_value(p: float, avg_win: float, avg_loss: float) -> float:
    """EV per unit trade. avg_loss is the loss magnitude (>= 0)."""
    if p < 0.0 or p > 1.0:
        raise ValueError("p must be in [0, 1]")
    if avg_win < 0.0:
        raise ValueError("avg_win must be >= 0")
    if avg_loss < 0.0:
        raise ValueError("avg_loss must be >= 0")
    return float(p) * float(avg_win) - (1.0 - float(p)) * float(avg_loss)


def breakeven_p(avg_win: float, avg_loss: float) -> float:
    """Smallest p such that EV >= 0. 1.0 if there is no win sample to offset losses."""
    w = float(avg_win)
    loss = float(avg_loss)
    if w < 0.0 or loss < 0.0:
        raise ValueError("avg_win and avg_loss must be >= 0")
    denom = w + loss
    if denom <= 0.0:
        return 1.0
    return loss / denom


def kelly_fraction(p: float, b: float) -> float:
    """Full Kelly f* = p − (1−p)/b for net odds b. Clamped to [0, 1]."""
    if p < 0.0 or p > 1.0:
        raise ValueError("p must be in [0, 1]")
    if b <= 0.0:
        return 0.0
    f = float(p) - (1.0 - float(p)) / float(b)
    return min(1.0, max(0.0, f))


def quarter_kelly_risk_pct(
    p: float,
    avg_win: float,
    avg_loss: float,
    *,
    fraction: float = 0.25,
) -> float:
    """Bankroll percent to risk at the stop, using fractional Kelly."""
    if fraction <= 0.0:
        return 0.0
    if avg_loss <= 0.0:
        if avg_win > 0.0:
            return 100.0 * float(fraction) * kelly_fraction(p, 1e9)
        return 0.0
    b = float(avg_win) / float(avg_loss)
    f = kelly_fraction(p, b)
    return 100.0 * float(fraction) * f
