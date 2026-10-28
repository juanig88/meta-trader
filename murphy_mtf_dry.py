"""Measure the H4/M15 rule on Binance spot. Does not send orders.

    python murphy_mtf_dry.py

Writes the journal under logs/binance/mtf/ (gitignored) and prints the summary.
The Exness spread is the latest demo quote, not a historical series.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

from trading.domain.demo_gate import TRADE_MODE_REAL
from trading.domain.murphy_mtf import walk_h4_m15
from trading.infrastructure.binance_klines import fetch_klines

PAIRS = ("BTCUSDT", "ETHUSDT")
# Two years, ending at the last fully closed day before "now" in this run.
LOOKBACK_MS = 2 * 365 * 24 * 60 * 60 * 1000
OUT_DIR = Path("logs/binance/mtf")
# Last measured Exness demo spreads, in price units. Replaced when the terminal answers.
FALLBACK_SPREAD = {"BTCUSDT": 6.4, "ETHUSDT": 1.0}


def main() -> int:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - LOOKBACK_MS
    spreads = _exness_spreads()
    summary: dict[str, Any] = {
        "event": "MURPHY_MTF_DRY",
        "envia_ordenes": False,
        "regla": "h4_direccion_m15_retroceso",
        "fee_lado": 0.001,
        "spread_fuente": spreads["fuente"],
        "pares": {},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pair in PAIRS:
        m15 = _load(pair, "15m", start_ms, end_ms)
        h4 = _load(pair, "4h", start_ms, end_ms)
        journal = walk_h4_m15(
            [r["open_ms"] for r in m15],
            [r["open"] for r in m15],
            [r["high"] for r in m15],
            [r["low"] for r in m15],
            [r["close"] for r in m15],
            [r["open_ms"] for r in h4],
            [r["high"] for r in h4],
            [r["low"] for r in h4],
            [r["close"] for r in h4],
            spread_price=float(spreads["precio"].get(pair, 0.0)),
        )
        _stamp(journal["operaciones"], m15)
        path = OUT_DIR / f"{pair}_journal.json"
        path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        summary["pares"][pair] = {
            "m15": len(m15),
            "h4": len(h4),
            "desde": m15[0]["open_ms"] if m15 else None,
            "hasta": m15[-1]["open_ms"] if m15 else None,
            "volumen_ultima": m15[-1]["volume"] if m15 else None,
            "trades_ultima": m15[-1]["trades"] if m15 else None,
            "spread_exness": spreads["precio"].get(pair),
            "todo": journal["todo"],
            "holdout": journal["holdout"],
            "rechazos": journal.get("rechazos"),
            "archivo": str(path),
        }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _load(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    path = OUT_DIR / f"{symbol}_{interval}.csv"
    if path.exists():
        rows = _read_csv(path)
        if rows and rows[0]["open_ms"] <= start_ms + 4 * 60 * 60 * 1000 and rows[-1]["open_ms"] >= end_ms - 2 * 24 * 60 * 60 * 1000:
            return [r for r in rows if start_ms <= r["open_ms"] < end_ms]
    rows = fetch_klines(symbol, interval, start_ms, end_ms)
    _write_csv(path, rows)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["open_ms", "open", "high", "low", "close", "volume", "trades"])
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {
                "open_ms": int(row["open_ms"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "trades": int(float(row["trades"])),
            }
            for row in csv.DictReader(handle)
        ]


def _stamp(trades: list[dict[str, Any]], m15: list[dict[str, Any]]) -> None:
    for trade in trades:
        trade["entrada_ms"] = m15[int(trade["entrada_i"])]["open_ms"]
        trade["salida_ms"] = m15[int(trade["salida_i"])]["open_ms"]


def _exness_spreads() -> dict[str, Any]:
    precio = dict(FALLBACK_SPREAD)
    fuente = "ultima_corrida_demo"
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {"precio": precio, "fuente": fuente}
    if not mt5.initialize():
        return {"precio": precio, "fuente": fuente}
    try:
        acc = mt5.account_info()
        if acc is not None and int(getattr(acc, "trade_mode", -1)) == TRADE_MODE_REAL:
            return {"precio": precio, "fuente": "terminal_real_no_usada"}
        mapped = {"BTCUSDT": "BTCUSDm", "ETHUSDT": "ETHUSDm"}
        got = False
        for pair, broker in mapped.items():
            info = mt5.symbol_info(broker)
            if info is None:
                continue
            precio[pair] = int(getattr(info, "spread", 0) or 0) * float(info.point)
            got = True
        if got:
            fuente = "exness_demo_ahora"
    finally:
        mt5.shutdown()
    return {"precio": precio, "fuente": fuente}


if __name__ == "__main__":
    sys.exit(main())
