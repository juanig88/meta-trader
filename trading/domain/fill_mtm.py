"""Mark-to-market of a fill on later closed bars. Pure: no I/O.

Used to choose a scalp time-stop. Does not invent prices: missing bars stay missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from trading.domain.mae_excursion import (
    DEFAULT_M1_PERIOD,
    SIDE_BUY,
    MaeBar,
    bar_close_ts,
    original_decision_side,
)

SIDE_SELL = "SELL"


@dataclass(frozen=True)
class FillMtm:
    missing: bool
    reason: str
    n_bars: int
    entry: Optional[float]
    mark: Optional[float]
    pnl_usd: Optional[float]


def signed_pnl_usd(
    *,
    side: str,
    entry: float,
    price: float,
    usd_per_price: float,
) -> Optional[float]:
    """USD PnL of a position from entry to mark. None if inputs are unusable."""
    label = original_decision_side(side=side)
    try:
        px0 = float(entry)
        px1 = float(price)
        usd = float(usd_per_price)
    except (TypeError, ValueError):
        return None
    if label not in (SIDE_BUY, SIDE_SELL) or px0 <= 0.0 or px1 <= 0.0 or usd <= 0.0:
        return None
    if label == SIDE_BUY:
        return round((px1 - px0) * usd, 6)
    return round((px0 - px1) * usd, 6)


def mtm_at_horizon(
    bars: Sequence[MaeBar],
    *,
    side: str,
    entry: float,
    fill_ts: float,
    horizon_seconds: float,
    usd_per_price: float,
    period_seconds: float = DEFAULT_M1_PERIOD,
) -> FillMtm:
    """Last closed M1 close in (fill_ts, fill_ts + horizon]. No lookahead."""
    label = original_decision_side(side=side)
    if label not in (SIDE_BUY, SIDE_SELL):
        return FillMtm(True, "bad_side", 0, None, None, None)
    try:
        px0 = float(entry)
        usd = float(usd_per_price)
        start = float(fill_ts)
        horizon = float(horizon_seconds)
    except (TypeError, ValueError):
        return FillMtm(True, "bad_inputs", 0, None, None, None)
    if px0 <= 0.0:
        return FillMtm(True, "missing_entry", 0, None, None, None)
    if usd <= 0.0:
        return FillMtm(True, "missing_usd", 0, px0, None, None)
    if horizon <= 0.0:
        return FillMtm(True, "bad_horizon", 0, px0, None, None)

    period = max(0.0, float(period_seconds))
    end = start + horizon
    mark: Optional[float] = None
    n_bars = 0
    for bar in sorted(bars, key=lambda b: float(b.time)):
        close_ts = bar_close_ts(bar, period)
        if close_ts <= start + 1e-12:
            continue
        if close_ts > end + 1e-12:
            break
        mark = float(bar.close)
        n_bars += 1
    if mark is None or mark <= 0.0:
        return FillMtm(True, "missing_bars", n_bars, px0, None, None)
    pnl = signed_pnl_usd(side=label, entry=px0, price=mark, usd_per_price=usd)
    if pnl is None:
        return FillMtm(True, "bad_pnl", n_bars, px0, mark, None)
    return FillMtm(False, "", n_bars, px0, mark, pnl)
