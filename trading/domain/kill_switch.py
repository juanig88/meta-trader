"""Daily-loss halt helpers. Pure: no files, no MT5.

Flatten is opt-in. A persisted halt (loaded by infrastructure) blocks entries
until a human clears it. Models never clear it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

REASON_DAILY_LOSS = "daily_loss_limit"
REASON_CORRUPT = "halt_file_unreadable"


def flatten_now(*, flatten_enabled: bool, daily_reached: bool, halt_active: bool) -> bool:
    """True only when the flag is on, the day already hit the limit, and we have not halted yet."""
    return bool(flatten_enabled) and bool(daily_reached) and not bool(halt_active)


def flatten_retry(*, flatten_enabled: bool, record: Optional[Mapping[str, Any]]) -> bool:
    """A halt whose flatten left tickets open keeps retrying while the flag is on."""
    return bool(flatten_enabled) and halt_is_active(record) and bool((record or {}).get("flatten_pending"))


def entries_blocked(*, daily_reached: bool, halt_active: bool) -> bool:
    return bool(daily_reached) or bool(halt_active)


def halt_enforced(*, armed: bool, record: Optional[Mapping[str, Any]]) -> bool:
    """Disarmed (BOT_KILL_SWITCH_ARMED=0) means a human chose to ignore the halt."""
    return bool(armed) and halt_is_active(record)


def halt_record(
    *,
    ts: str,
    pnl_today: float,
    daily_loss_limit: float,
    reason: str = REASON_DAILY_LOSS,
) -> dict[str, Any]:
    return {
        "active": True,
        "reason": str(reason or REASON_DAILY_LOSS),
        "ts": str(ts or ""),
        "pnl_today": float(pnl_today),
        "daily_loss_limit": float(daily_loss_limit),
        "flatten_pending": True,
    }


def after_flatten(record: Mapping[str, Any], *, n_closed: int, n_failed: int, ts: str) -> dict[str, Any]:
    out = dict(record)
    out["flatten_pending"] = int(n_failed) > 0
    out["n_closed"] = int(out.get("n_closed") or 0) + int(n_closed)
    out["n_failed_last"] = int(n_failed)
    out["last_flatten_ts"] = str(ts or "")
    return out


def halt_is_active(record: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(record, Mapping):
        return False
    return bool(record.get("active"))
