"""XBTFX session gate for processes that attach to the MetaTrader 5 terminal.

Reads the account the terminal is already logged into. Does not place orders
and does not print credentials. A rejected server is a reason string; callers
decide whether to shut down their own API connection.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from trading.domain.xbtfx import (
    ContractSnapshot,
    account_is_xbtfx,
    alias_hint,
    contract_snapshot,
    margin_band,
    parse_server_markers,
)

_FALSE = {"0", "false", "no", "n", "off"}


def enforcement_enabled(env: Optional[dict] = None) -> bool:
    src = env if env is not None else os.environ
    raw = str(src.get("MT5_XBTFX_ONLY", "1") or "1").strip().lower()
    return raw not in _FALSE


def server_markers(env: Optional[dict] = None) -> tuple[str, ...]:
    src = env if env is not None else os.environ
    return parse_server_markers(str(src.get("MT5_BROKER_SERVER_MARKERS", "") or ""))


def rejection_reason(mt5: Any, env: Optional[dict] = None) -> str:
    """Empty string when the attached account is allowed."""
    if not enforcement_enabled(env):
        return ""
    try:
        acc = mt5.account_info()
    except Exception as exc:
        return f"XBTFX gate: account_info failed: {exc}"
    if acc is None:
        return "XBTFX gate: account_info unavailable"
    server = str(getattr(acc, "server", "") or "")
    company = str(getattr(acc, "company", "") or "")
    if account_is_xbtfx(server, company, server_markers(env)):
        return ""
    return f"XBTFX gate: refusing server={server!r} company={company!r}"


def account_margin_status(mt5: Any) -> tuple[Optional[float], str]:
    try:
        acc = mt5.account_info()
    except Exception:
        return None, "unknown"
    if acc is None:
        return None, "unknown"
    equity = float(getattr(acc, "equity", 0.0) or 0.0)
    margin = float(getattr(acc, "margin", 0.0) or 0.0)
    so_call = float(getattr(acc, "margin_so_call", 0.0) or 0.0) or None
    so_so = float(getattr(acc, "margin_so_so", 0.0) or 0.0) or None
    return margin_band(equity, margin, so_call, so_so)


def listed_names(mt5: Any, symbol: str) -> list[str]:
    from trading.domain.xbtfx import candidate_names

    found: list[str] = []
    for name in candidate_names(symbol):
        try:
            info = mt5.symbol_info(name)
        except Exception:
            info = None
        if info is not None:
            found.append(name)
    return found


def missing_symbol_message(mt5: Any, symbol: str) -> str:
    hint = alias_hint(symbol, listed_names(mt5, symbol))
    return f"Symbol not found on XBTFX: {symbol}.{hint}".rstrip(".")


def snapshot_symbol(mt5: Any, symbol: str) -> Optional[ContractSnapshot]:
    try:
        info = mt5.symbol_info(symbol)
    except Exception:
        return None
    if info is None:
        return None
    payload = {
        "name": str(getattr(info, "name", "") or symbol),
        "point": getattr(info, "point", 0.0),
        "digits": getattr(info, "digits", 0),
        "trade_mode": getattr(info, "trade_mode", 0),
        "calc_mode": getattr(info, "trade_calc_mode", 0),
        "trade_exemode": getattr(info, "trade_exemode", 0),
        "spread_float": bool(getattr(info, "spread_float", False)),
        "trade_contract_size": getattr(info, "trade_contract_size", 0.0),
        "volume_min": getattr(info, "volume_min", 0.0),
        "volume_step": getattr(info, "volume_step", 0.0),
        "volume_max": getattr(info, "volume_max", 0.0),
        "filling_mode": getattr(info, "filling_mode", 0),
        "currency_base": getattr(info, "currency_base", ""),
        "currency_profit": getattr(info, "currency_profit", ""),
        "currency_margin": getattr(info, "currency_margin", ""),
        "margin_initial": getattr(info, "margin_initial", 0.0),
    }
    return contract_snapshot(payload)

