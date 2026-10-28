"""MAE / MFE on closed bars after a directional signal. Pure: no I/O.

Bar ``time`` is the candle **open** (MT5 convention). The bar is treated as
closed at ``time + period_seconds``. A bar that closes after ``signal_ts +
window_seconds`` is lookahead and must not enter the path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
_SIDES = frozenset({SIDE_BUY, SIDE_SELL})
DEFAULT_M1_PERIOD = 60.0


@dataclass(frozen=True)
class MaeBar:
    time: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class MaeWindow:
    missing: bool
    reason: str
    n_bars: int
    ref: Optional[float]
    mae: Optional[float]
    mfe: Optional[float]
    mae_usd: Optional[float]
    mfe_usd: Optional[float]
    mae_gt_mfe: Optional[bool]
    ambiguous: bool


def original_decision_side(*, side_decision: object = None, side: object = None) -> str:
    """Prefer ``side_decision`` (pre-invert vote). Ignore executed fill side."""
    for raw in (side_decision, side):
        label = str(raw or "").strip().upper()
        if label in _SIDES:
            return label
    return ""


def usd_per_price_from_ticket(
    *,
    risk_usd: object = None,
    stop_dist: object = None,
    lot: object = None,
    tick_value: object = None,
    tick_size: object = None,
) -> float:
    """USD per 1.0 price unit. Prefer contract tick; else Kelly risk / stop dist."""
    try:
        tv = float(tick_value) if tick_value is not None else 0.0
        ts = float(tick_size) if tick_size is not None else 0.0
        vol = float(lot) if lot is not None else 0.0
    except (TypeError, ValueError):
        tv = ts = vol = 0.0
    if tv > 0.0 and ts > 0.0 and vol > 0.0:
        return (tv / ts) * vol
    try:
        risk = float(risk_usd) if risk_usd is not None else 0.0
        dist = float(stop_dist) if stop_dist is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if risk > 0.0 and dist > 0.0:
        return risk / dist
    return 0.0


def _f(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bar_close_ts(bar: MaeBar, period_seconds: float = DEFAULT_M1_PERIOD) -> float:
    return float(bar.time) + max(0.0, float(period_seconds))


def last_closed_close(
    bars: Sequence[MaeBar],
    signal_ts: float,
    *,
    period_seconds: float = DEFAULT_M1_PERIOD,
) -> Optional[float]:
    """Close of the last bar that is fully closed at ``signal_ts``."""
    last: Optional[float] = None
    cut = float(signal_ts)
    period = max(0.0, float(period_seconds))
    for bar in bars:
        if bar_close_ts(bar, period) <= cut + 1e-12:
            last = float(bar.close)
        elif float(bar.time) > cut:
            break
    return last


def _in_window(
    bar: MaeBar,
    *,
    signal_ts: float,
    window_seconds: float,
    period_seconds: float,
) -> bool:
    close_ts = bar_close_ts(bar, period_seconds)
    start = float(signal_ts)
    end = start + max(0.0, float(window_seconds))
    return close_ts > start + 1e-12 and close_ts <= end + 1e-12


def excursion_after_signal(
    bars: Sequence[MaeBar],
    *,
    side: str,
    signal_ts: float,
    window_seconds: float,
    period_seconds: float = DEFAULT_M1_PERIOD,
    ref: Optional[float] = None,
    usd_per_price: float = 0.0,
) -> MaeWindow:
    """MAE (against) and MFE (with) the original decision, price units then optional USD."""
    label = original_decision_side(side=side)
    if label not in _SIDES:
        return MaeWindow(True, "bad_side", 0, None, None, None, None, None, None, False)
    if float(window_seconds) <= 0.0:
        return MaeWindow(True, "bad_window", 0, None, None, None, None, None, None, False)

    period = max(0.0, float(period_seconds))
    ordered = sorted(bars, key=lambda b: float(b.time))
    ref_px = float(ref) if ref is not None and float(ref) > 0.0 else last_closed_close(
        ordered, float(signal_ts), period_seconds=period
    )
    if ref_px is None or ref_px <= 0.0:
        return MaeWindow(True, "missing_ref", 0, None, None, None, None, None, None, False)

    window_bars = [
        b
        for b in ordered
        if _in_window(
            b,
            signal_ts=float(signal_ts),
            window_seconds=float(window_seconds),
            period_seconds=period,
        )
    ]
    if not window_bars:
        return MaeWindow(True, "missing_bars", 0, ref_px, None, None, None, None, None, False)

    worst_low = min(float(b.low) for b in window_bars)
    best_high = max(float(b.high) for b in window_bars)
    if label == SIDE_BUY:
        mae = max(0.0, ref_px - worst_low)
        mfe = max(0.0, best_high - ref_px)
    else:
        mae = max(0.0, best_high - ref_px)
        mfe = max(0.0, ref_px - worst_low)

    ambiguous = False
    for b in window_bars:
        hi = float(b.high)
        lo = float(b.low)
        if hi > ref_px + 1e-12 and lo < ref_px - 1e-12:
            ambiguous = True
            break

    usd = float(usd_per_price) if usd_per_price and float(usd_per_price) > 0.0 else 0.0
    mae_usd = round(mae * usd, 6) if usd > 0.0 else None
    mfe_usd = round(mfe * usd, 6) if usd > 0.0 else None
    return MaeWindow(
        missing=False,
        reason="",
        n_bars=len(window_bars),
        ref=ref_px,
        mae=round(mae, 10),
        mfe=round(mfe, 10),
        mae_usd=mae_usd,
        mfe_usd=mfe_usd,
        mae_gt_mfe=bool(mae > mfe + 1e-12),
        ambiguous=ambiguous,
    )
