"""Read-only MT5 history for the manual-trade miner.

This module never calls order_send, order_check, or any trade request.
Credentials stay in the environment and are passed only to mt5.initialize.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional


def fetch_deals_and_positions(
    start: datetime,
    end: datetime,
) -> tuple[list[dict], list[dict], str]:
    """Return (deals, open_positions, error). error is empty on success."""
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        return [], [], f"MetaTrader5 is not installed: {exc}"

    err = _ensure_terminal(mt5)
    if err:
        return [], [], err

    try:
        deals = mt5.history_deals_get(start, end) or []
        positions = mt5.positions_get() or []
    except Exception as exc:
        return [], [], f"mt5 history read failed: {exc}"

    return [_deal_dict(d) for d in deals], [_position_dict(p) for p in positions], ""


def _ensure_terminal(mt5: Any) -> str:
    try:
        connected = mt5.terminal_info() is not None
    except Exception as exc:
        return f"mt5.terminal_info failed: {exc}"

    if not connected:
        login = os.getenv("MT5_LOGIN")
        password = os.getenv("MT5_PASSWORD")
        server = os.getenv("MT5_SERVER")
        path = os.getenv("MT5_PATH") or os.getenv("MT5_TERMINAL_PATH")
        kwargs: dict = {}
        if path:
            kwargs["path"] = path
        if login and password and server:
            try:
                kwargs["login"] = int(login)
            except ValueError:
                return "MT5_LOGIN must be an integer"
            kwargs["password"] = password
            kwargs["server"] = server
        try:
            ok = bool(mt5.initialize(**kwargs))
        except Exception as exc:
            return f"mt5.initialize failed: {exc}"
        if not ok:
            try:
                detail = mt5.last_error()
            except Exception:
                detail = "unknown"
            return f"mt5.initialize returned False: {detail}"

    from trading.infrastructure.xbtfx_session import rejection_reason

    reason = rejection_reason(mt5)
    if reason:
        if not connected:
            try:
                mt5.shutdown()
            except Exception:
                pass
        return reason
    return ""


def _int_attr(obj: object, name: str, default: int = 0) -> int:
    raw = getattr(obj, name, default)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _deal_dict(deal: object) -> dict:
    secs = _int_attr(deal, "time", 0)
    when: Optional[str] = None
    if secs > 0:
        when = datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    return {
        "time": when,
        "deal": _int_attr(deal, "ticket", 0),
        "ticket": _int_attr(deal, "ticket", 0),
        "position_id": _int_attr(deal, "position_id", 0),
        "symbol": str(getattr(deal, "symbol", "") or ""),
        "magic": _int_attr(deal, "magic", 0),
        "entry": _int_attr(deal, "entry", -1),
        "type": _int_attr(deal, "type", -1),
        "volume": float(getattr(deal, "volume", 0.0) or 0.0),
        "price": float(getattr(deal, "price", 0.0) or 0.0),
        "profit": float(getattr(deal, "profit", 0.0) or 0.0),
        "swap": float(getattr(deal, "swap", 0.0) or 0.0),
        "commission": float(getattr(deal, "commission", 0.0) or 0.0),
        "comment": str(getattr(deal, "comment", "") or "")[:120],
    }


def _position_dict(pos: object) -> dict:
    return {
        "symbol": str(getattr(pos, "symbol", "") or ""),
        "magic": _int_attr(pos, "magic", 0),
        "ticket": _int_attr(pos, "ticket", 0),
        "type": _int_attr(pos, "type", -1),
    }
