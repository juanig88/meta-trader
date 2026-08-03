"""
ETHUSD (or other symbol) grid search over SL/TP/spread/adx_min using the same
backtest engine as `backtest.py`. Requires MT5 + .env credentials.

Usage:
  py .\\eth_param_search.py
  py .\\eth_param_search.py --symbol ETHUSD --risk-pct 12
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


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip()


def _score_row(r: dict[str, Any]) -> float:
    """Higher is better: balance + Calmar-like bonus from realized PnL vs drawdown."""
    fb = float(r.get("final_balance") or 0.0)
    dd = float(r.get("max_dd_money") or 0.0)  # negative
    pnl = float(r.get("pnl_money") or 0.0)
    calmar = pnl / max(1e-9, abs(dd)) if dd < -1e-9 else pnl
    return fb + 0.25 * calmar


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ETHUSD")
    ap.add_argument("--risk-pct", type=float, default=12.0)
    ap.add_argument("--initial-balance", type=float, default=100.0)
    ap.add_argument("--lot", type=float, default=float(_env("BOT_LOT", "0.01") or "0.01"))
    ap.add_argument("--out", default="eth_search_results.jsonl")
    ap.add_argument(
        "--grid-days",
        type=int,
        default=45,
        help="Shorter window for the parameter grid (full EMA/ADX per bar is O(n²); keep this small).",
    )
    args = ap.parse_args()

    symbol = str(args.symbol).strip()
    cfg = Config(symbols=(symbol,))
    if not connect_mt5(cfg):
        return 2

    end = datetime.now()
    grid_days = max(14, min(180, int(args.grid_days)))
    start_grid = end - timedelta(days=grid_days)
    start_365 = end - timedelta(days=365)
    start_14 = end - timedelta(days=14)

    m15_grid = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_grid, end)
    h4_grid = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, start_grid - timedelta(days=10), end)
    m15_365 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_365, end)
    h4_365 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, start_365 - timedelta(days=10), end)
    m15_14 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_14, end)
    h4_14 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, start_14 - timedelta(days=10), end)
    if m15_grid is None or h4_grid is None:
        print(f"Could not load M15/H4 for {grid_days}d grid window.", flush=True)
        mt5.shutdown()
        return 4

    # Same strategy defaults as `backtest.py` CLI
    base_kw = dict(
        ema_fast=50,
        ema_slow=200,
        adx_period=14,
        entry_mode="hybrid",
        trend_mode="closes",
        allow_flat_h4=True,
        crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
    )

    # Reasonable ETH CFD grid (points); BTC-like 40k/80k was too wide for ETH in tests.
    # Focused grid (runtime): ~8 * 4 * 3 = 96 backtests on 180d, then validate top 5.
    pairs: list[tuple[float, float]] = [
        (5000, 10000),
        (6000, 12000),
        (8000, 16000),
        (10000, 20000),
        (12000, 24000),
        (16000, 32000),
        (20000, 40000),
        (24000, 48000),
    ]
    spreads = [300.0, 600.0, 1200.0, 2500.0]
    adx_mins = [16.0, 18.0, 20.0]

    rows: list[dict[str, Any]] = []
    total = 0
    for sl, tp in pairs:
        if tp < sl * 1.15:
            continue
        for spr in spreads:
            for adx_min in adx_mins:
                total += 1
    done = 0
    for sl, tp in pairs:
        if tp < sl * 1.15:
            continue
        for spr in spreads:
            for adx_min in adx_mins:
                done += 1
                if done == 1 or done % 10 == 0 or done == total:
                    print(f"[grid {grid_days}d] {done}/{total} …", flush=True)
                params = StrategyParams(**base_kw, adx_min=float(adx_min))
                try:
                    rep = run_backtest_on_rates(
                        symbol=symbol,
                        m15=m15_grid,
                        h4=h4_grid,
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
                            "phase": f"grid{grid_days}",
                            "symbol": symbol,
                            "sl": sl,
                            "tp": tp,
                            "spread": spr,
                            "adx_min": adx_min,
                            "error": str(e),
                        }
                    )
                    continue
                row = {
                    "phase": f"grid{grid_days}",
                    "symbol": symbol,
                    "sl": sl,
                    "tp": tp,
                    "spread": spr,
                    "adx_min": adx_min,
                    "trades": rep.trades,
                    "wins": rep.wins,
                    "losses": rep.losses,
                    "winrate": rep.winrate,
                    "pnl_money": rep.pnl_money,
                    "max_dd_money": rep.max_drawdown_money,
                    "final_balance": rep.final_balance,
                }
                row["_score"] = _score_row(row)
                rows.append(row)

    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: float(r.get("_score", 0.0)), reverse=True)

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    print(f"Wrote {len(rows)} rows to {args.out}")
    if not ok:
        mt5.shutdown()
        return 3

    print(f"\nTop 8 ({grid_days}d grid, sorted by score):")
    for r in ok[:8]:
        print(
            f"score={r['_score']:.3f} bal={r['final_balance']:.2f} pnl$={r['pnl_money']:.2f} "
            f"dd$={r['max_dd_money']:.2f} trades={r['trades']} wr={r['winrate']:.1f}% "
            f"SL={r['sl']:.0f} TP={r['tp']:.0f} spr={r['spread']:.0f} adx>={r['adx_min']:.0f}"
        )

    # Validate top 5 on 365d and 14d
    print("\n=== Validation (365d / 14d) for top 5 grid candidates ===")
    topn = ok[:5]
    for cand in topn:
        sl = float(cand["sl"])
        tp = float(cand["tp"])
        spr = float(cand["spread"])
        adx_min = float(cand["adx_min"])
        params = StrategyParams(**base_kw, adx_min=adx_min)
        for days, label in [(365, "365d"), (14, "14d")]:
            st = end - timedelta(days=days)
            try:
                m15_w = m15_365 if days == 365 else m15_14
                h4_w = h4_365 if days == 365 else h4_14
                if m15_w is None or h4_w is None:
                    print(f"[{label}] missing rates", flush=True)
                    continue
                rep = run_backtest_on_rates(
                    symbol=symbol,
                    m15=m15_w,
                    h4=h4_w,
                    sl_points=sl,
                    tp_points=tp,
                    spread_points=spr,
                    params=params,
                    initial_balance=float(args.initial_balance),
                    lot=float(args.lot),
                    risk_sizing=True,
                    risk_per_trade_pct=float(args.risk_pct),
                )
            except Exception as e:
                print(f"[{label}] SL={sl:.0f} TP={tp:.0f} spr={spr:.0f} adx>={adx_min:.0f} ERROR {e}")
                continue
            print(
                f"[{label}] SL={sl:.0f} TP={tp:.0f} spr={spr:.0f} adx>={adx_min:.0f} | "
                f"bal={rep.final_balance:.2f} pnl$={rep.pnl_money:.2f} dd$={rep.max_drawdown_money:.2f} "
                f"trades={rep.trades} wr={rep.winrate:.1f}%"
            )

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
