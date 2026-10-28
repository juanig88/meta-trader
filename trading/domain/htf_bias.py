"""BTCUSD spread cap on top of per-symbol max_spread_points. Pure: no I/O."""

from __future__ import annotations

from trading.domain.xbtfx import root_symbol as _root_symbol


def effective_max_spread_points(
    symbol: str,
    symbol_max: float,
    *,
    btcusd_cap: float,
) -> float:
    """Hard cap for BTCUSD on top of per-symbol max_spread_points. 0 cap = off."""
    cap = float(symbol_max)
    extra = float(btcusd_cap)
    if extra <= 0.0:
        return cap
    if _root_symbol(symbol) != "BTCUSD":
        return cap
    return min(cap, extra)
