"""Close rotation positions that sat too long.

Default live clock is 5 minutes (fill MTM 2026-10-03: pre-invert EV least-bad
at +5m vs +15/+30/+60). Pure: no I/O.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_MIN_AGE_SECONDS = 300.0
DEFAULT_LOSS_USD = -0.0001
DEFAULT_TAKE_USD = 0.01


def stale_rotation_close_reason(
    *,
    age_seconds: float,
    profit: float,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    tiny_loss_usd: float = DEFAULT_LOSS_USD,
    take_usd: float = DEFAULT_TAKE_USD,
    close_all_after_age: bool = False,
) -> Optional[str]:
    """Return a close reason or None to leave the position.

    After ``min_age_seconds``:
    - loss: profit below ``tiny_loss_usd`` (default below -0.0001)
    - take: profit above ``take_usd`` (default above 0.01)
    - time: if ``close_all_after_age``, the band in between also closes
    Without ``close_all_after_age``, the closed interval
    ``[tiny_loss_usd, take_usd]`` stays.
    """
    min_age = float(min_age_seconds)
    if min_age <= 0.0:
        return None
    if float(age_seconds) + 1e-9 < min_age:
        return None
    pnl = float(profit)
    take = float(take_usd)
    if take > 0.0 and pnl > take + 1e-12:
        return "stale_take"
    floor = float(tiny_loss_usd)
    if pnl < floor - 1e-12:
        return "stale_loss"
    if bool(close_all_after_age):
        return "stale_time"
    return None
