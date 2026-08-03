"""
Barrido de backtest (M15) para BTC/ETH: distintos pares EMA + SL/TP más chicos (más trades, TP más cercano).

Requiere MT5 + .env. Escribe `crypto_sweep_results.jsonl` y resume por consola.

Uso:
  py .\\crypto_sweep.py
  py .\\crypto_sweep.py --days 365 --risk-pct 12
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from typing import Any

import MetaTrader5 as mt5

from backtest import run_backtest_on_rates
from bot_mt5 import Config, connect_mt5, _env_bool
from strategy_core import StrategyParams


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _score(r: dict[str, Any]) -> float:
    """Preferir balance final con penalización suave por drawdown en $."""
    fb = float(r.get("final_balance", 0.0))
    dd = float(r.get("max_dd_money", 0.0))
    return fb + 0.15 * float(dd)  # dd negativo => resta


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--risk-pct", type=float, default=12.0)
    ap.add_argument("--initial-balance", type=float, default=100.0)
    ap.add_argument("--lot", type=float, default=float(os.getenv("BOT_LOT", "0.01") or "0.01"))
    ap.add_argument("--out", default="crypto_sweep_results.jsonl")
    args = ap.parse_args()

    cfg = Config(symbols=("BTCUSD", "ETHUSD"))
    if not connect_mt5(cfg):
        return 2

    end = datetime.now()
    start = end - timedelta(days=int(args.days))

    ema_grid = [
        (12, 26),
        (20, 50),
        (34, 89),
        (50, 200),
    ]

    scenarios: dict[str, list[tuple[float, float, float]]] = {
        # symbol: (spread_points, list of (sl, tp))
        "BTCUSD": [
            (500.0, 40000.0, 80000.0),  # baseline-ish
            (500.0, 25000.0, 28000.0),  # TP más cerca (≈ scalping)
            (500.0, 20000.0, 24000.0),
            (500.0, 15000.0, 18000.0),
            (500.0, 30000.0, 32000.0),
        ],
        "ETHUSD": [
            (1200.0, 12000.0, 24000.0),  # actual tuning
            (1200.0, 8000.0, 10000.0),
            (1200.0, 6000.0, 8000.0),
            (1200.0, 5000.0, 7000.0),
            (1200.0, 10000.0, 12000.0),
        ],
    }

    adx_by_symbol = {"BTCUSD": 17.0, "ETHUSD": 18.0}

    rows: list[dict[str, Any]] = []

    for symbol, triples in scenarios.items():
        m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
        h4 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, start - timedelta(days=10), end)
        if m15 is None or h4 is None or len(m15) < 250:
            print(f"[skip] {symbol}: sin datos M15/H4 suficientes", flush=True)
            continue

        for ema_f, ema_s in ema_grid:
            for spr, sl, tp in triples:
                params = StrategyParams(
                    ema_fast=int(ema_f),
                    ema_slow=int(ema_s),
                    adx_period=14,
                    adx_min=float(adx_by_symbol.get(symbol, 17.0)),
                    entry_mode="hybrid",
                    trend_mode="closes",
                    allow_flat_h4=True,
                    crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
                )
                try:
                    rep = run_backtest_on_rates(
                        symbol=symbol,
                        m15=m15,
                        h4=h4,
                        sl_points=float(sl),
                        tp_points=float(tp),
                        spread_points=float(spr),
                        params=params,
                        initial_balance=float(args.initial_balance),
                        lot=float(args.lot),
                        risk_sizing=True,
                        risk_per_trade_pct=float(args.risk_pct),
                    )
                except Exception as e:
                    rows.append(
                        {
                            "symbol": symbol,
                            "ema_fast": ema_f,
                            "ema_slow": ema_s,
                            "spread": spr,
                            "sl": sl,
                            "tp": tp,
                            "error": str(e),
                        }
                    )
                    continue
                row = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "days": int(args.days),
                    "ema_fast": ema_f,
                    "ema_slow": ema_s,
                    "spread_points": spr,
                    "sl_points": sl,
                    "tp_points": tp,
                    "adx_min": float(adx_by_symbol.get(symbol, 17.0)),
                    "trades": rep.trades,
                    "winrate": rep.winrate,
                    "final_balance": rep.final_balance,
                    "pnl_money": rep.pnl_money,
                    "max_dd_money": rep.max_drawdown_money,
                    "risk_pct": float(args.risk_pct),
                }
                row["_score"] = _score(row)
                rows.append(row)

    mt5.shutdown()

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    ok = [r for r in rows if "error" not in r]
    print(f"Wrote {len(rows)} rows -> {args.out}", flush=True)

    for sym in ("BTCUSD", "ETHUSD"):
        sub = [r for r in ok if r.get("symbol") == sym]
        sub.sort(key=lambda r: float(r.get("_score", 0.0)), reverse=True)
        print(f"\n=== Top 5 {sym} (score) ===", flush=True)
        for r in sub[:5]:
            print(
                f"score={r['_score']:.2f} bal={r['final_balance']:.2f} pnl$={r['pnl_money']:.2f} "
                f"dd$={r['max_dd_money']:.2f} trades={r['trades']} wr={r['winrate']:.1f}% "
                f"EMA{r['ema_fast']}/{r['ema_slow']} SL={r['sl_points']:.0f} TP={r['tp_points']:.0f} "
                f"spr={r['spread_points']:.0f}",
                flush=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
