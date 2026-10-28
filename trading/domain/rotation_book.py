"""Floating-book snapshot for the rotation magic. Pure: no I/O."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

DEFAULT_AGED_SECONDS = 3600.0
DEFAULT_HARVEST_USD = 0.50
DEFAULT_HARVEST_MIN_AGE_SECONDS = 600.0
DEFAULT_HARVEST_MIN_OPEN = 2


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def update_book_watermarks(
    floating_net: float,
    prev: Optional[Mapping[str, Any]] = None,
    *,
    ts: str = "",
) -> dict[str, Any]:
    """High-water and subsequent trough of the floating book.

    A new high resets the trough to that same print. Restarting the process
    starts a new watermark pair; the logged ``floating_net`` series is the
    durable peak-to-trough record.
    """
    net = round(_f(floating_net), 6)
    prev = dict(prev or {})
    prev_hwm = prev.get("hwm")
    if prev_hwm is None or net >= _f(prev_hwm) - 1e-12:
        return {
            "hwm": net,
            "hwm_ts": ts,
            "trough": net,
            "trough_ts": ts,
        }
    prev_trough = prev.get("trough")
    if prev_trough is None or net <= _f(prev_trough) + 1e-12:
        return {
            "hwm": round(_f(prev_hwm), 6),
            "hwm_ts": prev.get("hwm_ts"),
            "trough": net,
            "trough_ts": ts,
        }
    return {
        "hwm": round(_f(prev_hwm), 6),
        "hwm_ts": prev.get("hwm_ts"),
        "trough": round(_f(prev_trough), 6),
        "trough_ts": prev.get("trough_ts"),
    }


def summarize_rotation_book(
    legs: Sequence[Mapping[str, Any]] = (),
    *,
    watermarks: Optional[Mapping[str, Any]] = None,
    ts: str = "",
    aged_seconds: float = DEFAULT_AGED_SECONDS,
    magic: int = 260414,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate open rotation tickets into a ``ROTATION_BOOK`` payload.

    ``legs`` items need ``net`` and ``age_seconds``. Returns
    ``(payload, next_watermarks)`` — do not log the watermarks dict itself.
    """
    nets = [_f(leg.get("net")) for leg in legs]
    ages = [_f(leg.get("age_seconds")) for leg in legs]
    aged_cut = max(0.0, _f(aged_seconds))
    n_open = len(legs)
    floating_net = round(sum(nets), 6)
    n_green = sum(1 for n in nets if n > 1e-12)
    n_red = sum(1 for n in nets if n < -1e-12)
    young_idx = [i for i, age in enumerate(ages) if age + 1e-9 < aged_cut]
    aged_idx = [i for i in range(n_open) if i not in set(young_idx)]
    young_net = round(sum(nets[i] for i in young_idx), 6)
    aged_net = round(sum(nets[i] for i in aged_idx), 6)
    next_wm = update_book_watermarks(floating_net, watermarks, ts=ts)
    hwm = _f(next_wm.get("hwm"))
    trough = _f(next_wm.get("trough"))
    payload = {
        "event": "ROTATION_BOOK",
        "ts": ts,
        "engine": "rotation",
        "magic": int(magic),
        "n_open": n_open,
        "n_green": n_green,
        "n_red": n_red,
        "n_scratch": n_open - n_green - n_red,
        "n_young": len(young_idx),
        "n_aged": len(aged_idx),
        "floating_net": round(floating_net, 4),
        "young_net": round(young_net, 4),
        "aged_net": round(aged_net, 4),
        "best_net": round(max(nets), 4) if nets else 0.0,
        "worst_net": round(min(nets), 4) if nets else 0.0,
        "min_age_seconds": int(min(ages)) if ages else None,
        "max_age_seconds": int(max(ages)) if ages else None,
        "hwm": round(hwm, 4),
        "hwm_ts": next_wm.get("hwm_ts"),
        "trough": round(trough, 4),
        "trough_ts": next_wm.get("trough_ts"),
        "drawdown_from_hwm": round(hwm - floating_net, 4),
    }
    return payload, next_wm


def book_harvest_should_flatten(
    *,
    floating_net: float,
    n_open: int,
    min_age_seconds: Optional[float],
    harvest_usd: float = DEFAULT_HARVEST_USD,
    min_age_seconds_required: float = DEFAULT_HARVEST_MIN_AGE_SECONDS,
    min_open: int = DEFAULT_HARVEST_MIN_OPEN,
) -> bool:
    """True when the whole rotation book should be flattened.

    Locks ~1× the per-trade Kelly cap on the basket so long-lived correlated
    legs can recycle into a fresh 4/5. ``harvest_usd <= 0`` disables.
    """
    cap = _f(harvest_usd)
    if cap <= 0.0:
        return False
    if int(n_open) < max(1, int(min_open)):
        return False
    if min_age_seconds is None:
        return False
    if _f(min_age_seconds) + 1e-9 < _f(min_age_seconds_required):
        return False
    return _f(floating_net) + 1e-12 >= cap
