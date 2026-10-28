"""Dry journal of the one trend rule on Exness BTC and ETH.

Reads closed daily bars, charges the spread, and prints the result.
Does not send orders. A higher win rate is not a reason to change the rule.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from trading.domain.demo_gate import TRADE_MODE_REAL
from trading.domain.murphy_rule import walk_journal
from trading.domain.murphy_trend import read_degrees
from trading.domain.murphy_universe import MURPHY_ROOTS, resolve_universe

HISTORY = 1500
TRADE_MODE_FULL = 4
HOLDOUT = 60


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(json.dumps({"event": "MURPHY_DRY", "envia_ordenes": False, "error": "MetaTrader5 no esta instalado"}))
        return 2
    if not mt5.initialize():
        err = mt5.last_error()
        print(json.dumps({
            "event": "MURPHY_DRY", "envia_ordenes": False,
            "error": "initialize_failed", "mt5_error": [err[0], str(err[1])],
        }))
        return 2
    try:
        payload = _run(mt5)
    finally:
        mt5.shutdown()
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if not payload.get("error") else 2


def _run(mt5: Any) -> dict[str, Any]:
    acc = mt5.account_info()
    if acc is None:
        return {"event": "MURPHY_DRY", "envia_ordenes": False, "error": "sin_cuenta"}
    trade_mode = int(getattr(acc, "trade_mode", -1))
    base: dict[str, Any] = {
        "event": "MURPHY_DRY",
        "envia_ordenes": False,
        "regla": "retroceso_menor",
        "server": str(getattr(acc, "server", "") or ""),
        "trade_mode": trade_mode,
        "nota": "Una sola regla, sin buscar la de mejor winrate. El swap se informa y no se resta.",
    }
    if trade_mode == TRADE_MODE_REAL:
        base["error"] = "cuenta_real"
        return base
    names = [
        str(getattr(row, "name", "") or "")
        for row in (mt5.symbols_get() or ())
        if int(getattr(row, "trade_mode", 0)) == TRADE_MODE_FULL and str(getattr(row, "name", "") or "")
    ]
    resolved = resolve_universe(names, tradable={name: True for name in names})
    missing = [root for root in MURPHY_ROOTS if not resolved.get(root)]
    if missing:
        base["error"] = "simbolo_ausente"
        base["ausentes"] = missing
        return base
    base["simbolos"] = {}
    for root in MURPHY_ROOTS:
        broker = str(resolved[root])
        base["simbolos"][root] = _one(mt5, broker)
    return base


def _one(mt5: Any, symbol: str) -> dict[str, Any]:
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, HISTORY)
    if info is None or rates is None or len(rates) < 182:
        return {"broker": symbol, "error": "sin_velas", "envia_ordenes": False}
    point = float(info.point)
    spread_points = int(getattr(info, "spread", 0) or 0)
    spread_price = spread_points * point
    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    opens = [float(r["open"]) for r in rates]
    volumes = [float(r["tick_volume"]) for r in rates]
    dates = [datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).date().isoformat() for r in rates]
    journal = walk_journal(highs, lows, closes, opens, spread_price=spread_price, holdout_bars=HOLDOUT)
    for trade in journal["operaciones"]:
        trade["entrada_fecha"] = dates[int(trade["entrada_i"])]
        trade["salida_fecha"] = dates[int(trade["salida_i"])]
    linear = read_degrees(highs, lows, closes, volumes=volumes)
    logged = read_degrees(highs, lows, closes, scale="log")
    return {
        "broker": symbol,
        "envia_ordenes": False,
        "barras": len(closes),
        "desde": dates[0],
        "hasta": dates[-1],
        "cierre": closes[-1],
        "spread_puntos": spread_points,
        "spread_precio": spread_price,
        "swap_long": float(getattr(info, "swap_long", 0.0) or 0.0),
        "swap_short": float(getattr(info, "swap_short", 0.0) or 0.0),
        "swap_mode": int(getattr(info, "swap_mode", -1)),
        "diario": {k: journal[k] for k in ("todo", "holdout", "holdout_desde_i", "rechazos", "operaciones")},
        "lectura_lineal": _anchors(linear),
        "lectura_log": _anchors(logged),
        "herramientas": linear.get("herramientas"),
    }


def _anchors(reading: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"postura": reading.get("postura"), "conflicto_principal": reading.get("conflicto_principal")}
    for name, grado in (reading.get("grados") or {}).items():
        linea = grado.get("linea") or {}
        out[name] = {
            "regime": grado.get("regime"),
            "estado": linea.get("estado"),
            "extension": linea.get("extension"),
            "anclas": linea.get("anclas"),
            "pct_por_barra": linea.get("pct_por_barra"),
        }
    return out


if __name__ == "__main__":
    sys.exit(main())
