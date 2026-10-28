"""Read-only chapter-4 snapshot for BTC and ETH on the attached MT5 terminal.

Prints one JSON object and returns. Does not send orders, does not read
the account password, and does not change the rotation or the swing engine.

    python murphy_read.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from trading.application.murphy_snapshot import snapshot_from_bars
from trading.domain.demo_gate import TRADE_MODE_REAL
from trading.domain.murphy_universe import MURPHY_ROOTS, resolve_universe

DAILY_BARS = 200
TRADE_MODE_FULL = 4


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(json.dumps({"event": "MURPHY_READ", "envia_ordenes": False, "error": "MetaTrader5 no esta instalado"}))
        return 2
    if not mt5.initialize():
        err = mt5.last_error()
        print(json.dumps({
            "event": "MURPHY_READ", "envia_ordenes": False,
            "error": "initialize_failed", "mt5_error": [err[0], str(err[1])],
        }))
        return 2
    try:
        payload = _read(mt5)
    finally:
        mt5.shutdown()
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if not payload.get("error") else 2


def _read(mt5: Any) -> dict[str, Any]:
    acc = mt5.account_info()
    if acc is None:
        return {"event": "MURPHY_READ", "fase": 1, "envia_ordenes": False, "error": "sin_cuenta"}
    trade_mode = int(getattr(acc, "trade_mode", -1))
    base = {
        "event": "MURPHY_READ",
        "fase": 1,
        "envia_ordenes": False,
        "server": str(getattr(acc, "server", "") or ""),
        "company": str(getattr(acc, "company", "") or ""),
        "trade_mode": trade_mode,
        "timeframe": "D1",
        "barras_pedidas": DAILY_BARS,
    }
    if trade_mode == TRADE_MODE_REAL:
        base["error"] = "cuenta_real"
        return base
    listed = _tradable_names(mt5)
    resolved = resolve_universe(listed, tradable={name: True for name in listed})
    base["simbolos"] = {}
    missing = [root for root in MURPHY_ROOTS if not resolved.get(root)]
    if missing:
        base["error"] = "simbolo_ausente"
        base["ausentes"] = missing
        base["resueltos"] = resolved
        return base
    for root in MURPHY_ROOTS:
        broker = str(resolved[root])
        bars = _daily(mt5, broker)
        if bars is None:
            base["simbolos"][root] = {"broker": broker, "error": "sin_velas"}
            base["error"] = "sin_velas"
            continue
        highs, lows, closes, dates = bars
        reading = snapshot_from_bars(highs, lows, closes, dates)
        reading["broker"] = broker
        reading["envia_ordenes"] = False
        base["simbolos"][root] = reading
    return base


def _tradable_names(mt5: Any) -> list[str]:
    names: list[str] = []
    for row in mt5.symbols_get() or ():
        name = str(getattr(row, "name", "") or "").strip()
        if name and int(getattr(row, "trade_mode", 0)) == TRADE_MODE_FULL:
            names.append(name)
    return names


def _daily(mt5: Any, symbol: str) -> Optional[tuple[list[float], list[float], list[float], list[str]]]:
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, DAILY_BARS)
    if rates is None or len(rates) == 0:
        return None
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    dates: list[str] = []
    for row in rates:
        highs.append(float(row["high"]))
        lows.append(float(row["low"]))
        closes.append(float(row["close"]))
        dates.append(datetime.fromtimestamp(int(row["time"]), tz=timezone.utc).date().isoformat())
    return highs, lows, closes, dates


if __name__ == "__main__":
    sys.exit(main())
