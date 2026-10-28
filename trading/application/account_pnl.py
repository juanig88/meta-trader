"""Pure account-day PnL summaries for the Flask dashboard.

No I/O: callers pass already-fetched MT5 deals/positions as dicts.
Balance/credit rows are excluded from trading PnL.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

# First session of the current bot+lab regime (local midnight).
LAB_EPOCH_DATE = date(2026, 9, 24)
# history_deals_get treats naive datetimes as broker server time.
# ICMarkets is typically UTC+2/+3; local ART is UTC-3. Pad the end so
# closes already on the server are not cut off by datetime.now().
MT5_HISTORY_END_PAD_HOURS = 12


def mt5_history_end(now: datetime | None = None, *, pad_hours: float = MT5_HISTORY_END_PAD_HOURS) -> datetime:
    wall = now if now is not None else datetime.now()
    return wall + timedelta(hours=float(pad_hours))

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2
DEAL_TYPE_CREDIT = 3

_CLOSE_ENTRIES = frozenset({DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY})
_NON_TRADE_TYPES = frozenset({DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT})


def lab_epoch_start(raw: str | None = None) -> datetime:
    """Local midnight of the lab/account zero moment. Override with BOT_LAB_SINCE=YYYY-MM-DD."""
    text = (raw if raw is not None else os.getenv("BOT_LAB_SINCE") or "").strip()
    if text:
        try:
            parsed = date.fromisoformat(text[:10])
            return datetime(parsed.year, parsed.month, parsed.day)
        except ValueError:
            pass
    return datetime(LAB_EPOCH_DATE.year, LAB_EPOCH_DATE.month, LAB_EPOCH_DATE.day)


# First day of the live crypto rotation engine (not the H4/XAU lab).
ROTATION_EPOCH_DATE = date(2026, 9, 29)


def rotation_epoch_start(raw: str | None = None) -> datetime:
    """Local midnight when rotation went live. Override with BOT_ROTATION_SINCE=YYYY-MM-DD."""
    text = (raw if raw is not None else os.getenv("BOT_ROTATION_SINCE") or "").strip()
    if text:
        try:
            parsed = date.fromisoformat(text[:10])
            return datetime(parsed.year, parsed.month, parsed.day)
        except ValueError:
            pass
    return datetime(ROTATION_EPOCH_DATE.year, ROTATION_EPOCH_DATE.month, ROTATION_EPOCH_DATE.day)


def open_magic_by_position(deals: Sequence[Mapping[str, Any]]) -> dict[int, int]:
    """ENTRY_IN magic per position_id. Closing deals often arrive with magic 0."""
    found: dict[int, int] = {}
    for deal in deals:
        if is_balance_deal(deal):
            continue
        if _i(deal.get("entry")) != DEAL_ENTRY_IN:
            continue
        pid = _i(deal.get("position_id"), 0)
        mag = _i(deal.get("magic"), 0)
        if pid > 0 and mag > 0:
            found.setdefault(pid, mag)
    return found


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def deal_net(deal: Mapping[str, Any]) -> float:
    return _f(deal.get("profit")) + _f(deal.get("swap")) + _f(deal.get("commission"))


def position_net(pos: Mapping[str, Any]) -> float:
    return _f(pos.get("profit")) + _f(pos.get("swap"))


def is_balance_deal(deal: Mapping[str, Any]) -> bool:
    return _i(deal.get("type")) in _NON_TRADE_TYPES


def is_close_deal(deal: Mapping[str, Any]) -> bool:
    return _i(deal.get("entry")) in _CLOSE_ENTRIES and not is_balance_deal(deal)


def close_side(deal: Mapping[str, Any]) -> str:
    """Position side from a closing deal (opposite of the deal type)."""
    dtype = _i(deal.get("type"))
    if dtype == DEAL_TYPE_BUY:
        return "SELL"
    if dtype == DEAL_TYPE_SELL:
        return "BUY"
    return str(deal.get("side") or "").strip().upper() or "?"


def origin_label(magic: int, bot_magics: Sequence[int]) -> str:
    magics = {int(m) for m in bot_magics if int(m) > 0}
    if magic in magics:
        return "bot"
    if magic == 0:
        return "manual"
    return f"magic_{magic}"


def close_row(
    deal: Mapping[str, Any],
    *,
    bot_magics: Sequence[int] = (),
    magic_override: int | None = None,
) -> dict[str, Any]:
    net = deal_net(deal)
    magic = _i(deal.get("magic"), 0)
    if magic <= 0 and magic_override is not None and int(magic_override) > 0:
        magic = int(magic_override)
    return {
        "time": deal.get("time"),
        "symbol": str(deal.get("symbol") or "").upper() or "?",
        "side": close_side(deal),
        "volume": _f(deal.get("volume")),
        "price": _f(deal.get("price")),
        "profit": round(_f(deal.get("profit")), 2),
        "swap": round(_f(deal.get("swap")), 2),
        "commission": round(_f(deal.get("commission")), 2),
        "net": round(net, 4),
        "magic": magic,
        "origin": origin_label(magic, bot_magics),
        "comment": str(deal.get("comment") or "")[:80],
        "deal": _i(deal.get("deal") or deal.get("ticket"), 0),
        "position_id": _i(deal.get("position_id"), 0),
        "reason": _i(deal.get("reason"), -1),
        "reason_label": str(deal.get("reason_label") or deal.get("comment") or "—")[:80],
    }


def summarize_account_pnl(
    deals: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]] = (),
    *,
    bot_magics: Sequence[int] = (),
) -> dict[str, Any]:
    """Aggregate realized + floating PnL and list closing deals.

    `deals` should already be scoped to the window (today, 48h, …).
    Realized net includes IN-deal commissions (they hit the account today).
    """
    realized_profit = 0.0
    realized_swap = 0.0
    realized_commission = 0.0
    bot_realized_net = 0.0
    manual_realized_net = 0.0
    closed: list[dict[str, Any]] = []
    magics = {int(m) for m in bot_magics if int(m) > 0}
    by_symbol: dict[str, dict[str, Any]] = {}
    inherited = open_magic_by_position(deals)

    for deal in deals:
        if is_balance_deal(deal):
            continue
        profit = _f(deal.get("profit"))
        swap = _f(deal.get("swap"))
        comm = _f(deal.get("commission"))
        net = profit + swap + comm
        realized_profit += profit
        realized_swap += swap
        realized_commission += comm
        magic = _i(deal.get("magic"), 0)
        if magic in magics:
            bot_realized_net += net
        else:
            manual_realized_net += net
        symbol = str(deal.get("symbol") or "").upper() or "?"
        bucket = by_symbol.setdefault(
            symbol, {"symbol": symbol, "net": 0.0, "n_wins": 0, "n_losses": 0, "n_closed": 0}
        )
        bucket["net"] = round(bucket["net"] + net, 2)
        if is_close_deal(deal):
            pid = _i(deal.get("position_id"), 0)
            row = close_row(deal, bot_magics=bot_magics, magic_override=inherited.get(pid))
            closed.append(row)
            bucket["n_closed"] += 1
            if row["net"] > 0:
                bucket["n_wins"] += 1
            elif row["net"] < 0:
                bucket["n_losses"] += 1

    closed.sort(key=lambda r: str(r.get("time") or ""), reverse=True)
    wins = [r for r in closed if r["net"] > 0]
    losses = [r for r in closed if r["net"] < 0]

    floating_net = 0.0
    floating_rows: list[dict[str, Any]] = []
    for pos in positions:
        net = position_net(pos)
        floating_net += net
        magic = _i(pos.get("magic"), 0)
        floating_rows.append(
            {
                "symbol": str(pos.get("symbol") or "").upper() or "?",
                "side": str(pos.get("side") or "").upper() or "?",
                "net": round(net, 2),
                "magic": magic,
                "origin": origin_label(magic, bot_magics),
            }
        )

    realized_net = realized_profit + realized_swap + realized_commission
    return {
        "realized_profit": round(realized_profit, 2),
        "realized_swap": round(realized_swap, 2),
        "realized_commission": round(realized_commission, 2),
        "realized_net": round(realized_net, 2),
        "bot_realized_net": round(bot_realized_net, 2),
        "manual_realized_net": round(manual_realized_net, 2),
        "floating_net": round(floating_net, 2),
        "account_net": round(realized_net + floating_net, 2),
        "n_closed": len(closed),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "floating": floating_rows,
        "by_symbol": sorted(by_symbol.values(), key=lambda r: str(r["symbol"])),
    }
