"""Read-only MT5 history for calibration backfill. Never sends orders, never shuts down.

The live bot may share this terminal, so this module only attaches.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trading.infrastructure.mt5_deals import _ensure_terminal

_TF = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "H4": "TIMEFRAME_H4"}


def fetch_rotation_deals(start: datetime, end: datetime) -> tuple[list[dict[str, Any]], str]:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        return [], f"MetaTrader5 is not installed: {exc}"
    err = _ensure_terminal(mt5)
    if err:
        return [], err
    try:
        raw = mt5.history_deals_get(start, end) or []
    except Exception as exc:
        return [], f"history_deals_get failed: {exc}"
    out: list[dict[str, Any]] = []
    for d in raw:
        out.append(
            {
                "ticket": int(getattr(d, "ticket", 0) or 0),
                "position_id": int(getattr(d, "position_id", 0) or 0),
                "time_secs": int(getattr(d, "time", 0) or 0),
                "symbol": str(getattr(d, "symbol", "") or ""),
                "magic": int(getattr(d, "magic", 0) or 0),
                "entry": int(getattr(d, "entry", -1)),
                "type": int(getattr(d, "type", -1)),
                "volume": float(getattr(d, "volume", 0.0) or 0.0),
                "price": float(getattr(d, "price", 0.0) or 0.0),
                "profit": float(getattr(d, "profit", 0.0) or 0.0),
                "swap": float(getattr(d, "swap", 0.0) or 0.0),
                "commission": float(getattr(d, "commission", 0.0) or 0.0),
                "comment": str(getattr(d, "comment", "") or "")[:120],
            }
        )
    return out, ""


def fetch_bars(symbol: str, timeframe: str, count: int) -> list[tuple[int, float, float, float, float, float]]:
    """Newest ``count`` bars (forming bar included; the caller cuts by close time)."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return []
    tf = getattr(mt5, _TF.get(str(timeframe).upper(), ""), None)
    if tf is None:
        return []
    try:
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, int(count))
    except Exception:
        return []
    if rates is None:
        return []
    out: list[tuple[int, float, float, float, float, float]] = []
    for r in rates:
        try:
            out.append(
                (
                    int(r["time"]),
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r["tick_volume"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out
