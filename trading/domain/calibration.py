"""Calibration math for shadow flags vs realized outcomes. Pure: no I/O.

A flag is only worth a gate if it beats the base rate on our own fills. These
helpers measure that; none of them decides anything live.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

Pair = tuple[float, float]


def brier(pairs: Sequence[Pair]) -> Optional[float]:
    """Mean squared error of probability p against outcome y in {0, 1}."""
    if not pairs:
        return None
    return sum((float(p) - float(y)) ** 2 for p, y in pairs) / float(len(pairs))


def base_rate(pairs: Sequence[Pair]) -> Optional[float]:
    if not pairs:
        return None
    return sum(float(y) for _p, y in pairs) / float(len(pairs))


def brier_skill(pairs: Sequence[Pair]) -> Optional[float]:
    """1 - Brier / Brier(base rate). >0 beats always predicting the base rate."""
    b = brier(pairs)
    rate = base_rate(pairs)
    if b is None or rate is None:
        return None
    ref = rate * (1.0 - rate)
    if ref <= 0.0:
        return None
    return 1.0 - b / ref


def reliability(pairs: Sequence[Pair], *, buckets: int = 5) -> list[dict[str, Any]]:
    """Equal-width buckets on p. Last bucket is closed so p == 1.0 lands in it."""
    nb = max(1, int(buckets))
    out: list[dict[str, Any]] = []
    for i in range(nb):
        lo = i / float(nb)
        hi = (i + 1) / float(nb)
        rows = [
            (float(p), float(y))
            for p, y in pairs
            if (lo <= float(p) < hi) or (i == nb - 1 and float(p) == hi)
        ]
        n = len(rows)
        out.append(
            {
                "lo": round(lo, 4),
                "hi": round(hi, 4),
                "n": n,
                "mean_p": None if n == 0 else round(sum(p for p, _ in rows) / n, 4),
                "mean_y": None if n == 0 else round(sum(y for _, y in rows) / n, 4),
            }
        )
    return out


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> Optional[float]:
    """Pooled z for p1 - p2. None when either side is empty or variance is zero."""
    if n1 <= 0 or n2 <= 0:
        return None
    p1 = k1 / float(n1)
    p2 = k2 / float(n2)
    pooled = (k1 + k2) / float(n1 + n2)
    var = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)
    if var <= 0.0:
        return None
    return (p1 - p2) / math.sqrt(var)


def normal_two_sided_p(z: Optional[float]) -> Optional[float]:
    if z is None:
        return None
    return math.erfc(abs(float(z)) / math.sqrt(2.0))


def laplace(k: int, n: int) -> Optional[float]:
    """Smoothed rate (k+1)/(n+2). Keeps a tiny bucket off 0 and 1."""
    if n < 0:
        return None
    return (k + 1.0) / (n + 2.0)


def flag_lift(rows: Sequence[tuple[bool, float]]) -> dict[str, Any]:
    """Outcome split by a binary flag. rows = (flag, net_usd).

    loss is net < 0. Reports loss rate, mean net, and the USD a hard skip on
    flag=True would have removed from the book (negative = skip would have helped).
    """
    on = [float(net) for flag, net in rows if flag]
    off = [float(net) for flag, net in rows if not flag]

    def _side(vals: list[float]) -> dict[str, Any]:
        n = len(vals)
        losses = sum(1 for v in vals if v < 0.0)
        return {
            "n": n,
            "losses": losses,
            "loss_rate": None if n == 0 else round(losses / float(n), 4),
            "loss_rate_smoothed": None if n == 0 else round(float(laplace(losses, n)), 4),
            "mean_net": None if n == 0 else round(sum(vals) / n, 6),
            "sum_net": round(sum(vals), 4),
        }

    a = _side(on)
    b = _side(off)
    z = two_proportion_z(a["losses"], a["n"], b["losses"], b["n"])
    worse = (
        a["n"] > 0
        and b["n"] > 0
        and a["loss_rate"] > b["loss_rate"]
        and a["mean_net"] < b["mean_net"]
    )
    return {
        "flag_on_worse": bool(worse),
        "flag_on": a,
        "flag_off": b,
        "loss_rate_diff": None
        if a["loss_rate"] is None or b["loss_rate"] is None
        else round(a["loss_rate"] - b["loss_rate"], 4),
        "z": None if z is None else round(z, 3),
        "p_value": None if z is None else round(float(normal_two_sided_p(z)), 5),
        "skip_on_flag_would_remove_usd": a["sum_net"],
    }


def empirical_recalibration(pairs: Sequence[Pair]) -> dict[str, Any]:
    """Map each distinct p to the smoothed observed rate. Advisory only."""
    groups: dict[float, list[float]] = {}
    for p, y in pairs:
        groups.setdefault(round(float(p), 4), []).append(float(y))
    out: dict[str, Any] = {}
    for p in sorted(groups):
        ys = groups[p]
        k = int(sum(1 for y in ys if y >= 0.5))
        out[f"{p:.4f}"] = {"n": len(ys), "observed": round(float(laplace(k, len(ys))), 4)}
    return out


def quantile_buckets(rows: Sequence[tuple[float, float]], *, buckets: int = 5) -> list[dict[str, Any]]:
    """Win rate and mean net by quantile of a continuous score. rows = (score, net)."""
    clean = sorted((float(s), float(n)) for s, n in rows if s is not None and s == s)
    if not clean:
        return []
    nb = max(1, min(int(buckets), len(clean)))
    out: list[dict[str, Any]] = []
    size = len(clean) / float(nb)
    for i in range(nb):
        lo_i = int(round(i * size))
        hi_i = int(round((i + 1) * size))
        part = clean[lo_i:hi_i]
        if not part:
            continue
        wins = sum(1 for _s, n in part if n > 0.0)
        out.append(
            {
                "score_lo": round(part[0][0], 4),
                "score_hi": round(part[-1][0], 4),
                "n": len(part),
                "win_rate": round(wins / float(len(part)), 4),
                "mean_net": round(sum(n for _s, n in part) / len(part), 6),
            }
        )
    return out
