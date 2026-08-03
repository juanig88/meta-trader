from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from itertools import product
from typing import Optional

import MetaTrader5 as mt5

from bot_mt5 import Config, connect_mt5, _env_bool
from backtest import run_backtest
from strategy_core import StrategyParams


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _parse_list(raw: str, cast):
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(cast(part))
    return out


def _parse_symbols(raw: Optional[str]) -> list[str]:
    if raw is None:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _score(pnl_points: float, max_dd_points: float) -> float:
    # conservative: drawdown is negative; penalize it linearly
    return float(pnl_points) + float(max_dd_points)


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Grid search optimization for backtest.py.")
    ap.add_argument("--symbols", help="Comma-separated symbols. Default: BOT_SYMBOLS or EURUSD.")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--start", help="YYYY-MM-DD or ISO. Optional.")
    ap.add_argument("--end", help="YYYY-MM-DD or ISO. Optional.")

    ap.add_argument("--sl", type=float, default=80, help="SL in points (single value).")
    ap.add_argument("--tp-list", default="100,160,200", help="TP points list, comma-separated.")
    ap.add_argument("--spread", type=float, default=20, help="Spread in points (single value).")

    ap.add_argument("--adx-min-list", default="20,25,30", help="ADX min list, comma-separated.")
    ap.add_argument("--ema-fast-list", default="20,50,100", help="EMA fast list, comma-separated.")
    ap.add_argument("--ema-slow-list", default="100,200", help="EMA slow list, comma-separated.")
    ap.add_argument("--adx-period", type=int, default=14)

    ap.add_argument("--min-trades", type=int, default=30, help="Filter: ignore configs with fewer trades.")
    ap.add_argument("--top", type=int, default=10, help="Top N per symbol to print.")
    ap.add_argument("--out", default="opt_results.jsonl", help="Output JSONL file.")
    args = ap.parse_args()

    sym_raw = args.symbols or _env("BOT_SYMBOLS") or "EURUSD"
    symbols = _parse_symbols(sym_raw) or ["EURUSD"]

    end = datetime.fromisoformat(args.end) if args.end else datetime.now()
    start = datetime.fromisoformat(args.start) if args.start else (end - timedelta(days=int(args.days)))

    tp_list = _parse_list(args.tp_list, float)
    adx_min_list = _parse_list(args.adx_min_list, float)
    ema_fast_list = _parse_list(args.ema_fast_list, int)
    ema_slow_list = _parse_list(args.ema_slow_list, int)

    cfg = Config(symbols=tuple(symbols))
    if not connect_mt5(cfg):
        return 2

    # pre-warm symbol info
    for s in symbols:
        mt5.symbol_select(s, True)

    total = len(tp_list) * len(adx_min_list) * len(ema_fast_list) * len(ema_slow_list) * len(symbols)
    print(f"Running grid search: {total} evaluations")

    results_by_symbol: dict[str, list[dict]] = {s: [] for s in symbols}

    for tp, adx_min, ema_fast, ema_slow in product(tp_list, adx_min_list, ema_fast_list, ema_slow_list):
        if ema_fast >= ema_slow:
            continue

        params = StrategyParams(
            ema_fast=int(ema_fast),
            ema_slow=int(ema_slow),
            adx_period=int(args.adx_period),
            adx_min=float(adx_min),
            crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
        )

        for sym in symbols:
            rep = run_backtest(
                symbol=sym,
                start=start,
                end=end,
                sl_points=float(args.sl),
                tp_points=float(tp),
                spread_points=float(args.spread),
                params=params,
            )

            if rep.trades < int(args.min_trades):
                continue

            row = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": sym,
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "sl_points": float(args.sl),
                "tp_points": float(tp),
                "spread_points": float(args.spread),
                "ema_fast": int(ema_fast),
                "ema_slow": int(ema_slow),
                "adx_period": int(args.adx_period),
                "adx_min": float(adx_min),
                "trades": rep.trades,
                "wins": rep.wins,
                "losses": rep.losses,
                "winrate": rep.winrate,
                "pnl_points": rep.pnl_points,
                "max_drawdown_points": rep.max_drawdown_points,
            }
            row["score"] = _score(row["pnl_points"], row["max_drawdown_points"])

            results_by_symbol[sym].append(row)

            with open(args.out, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Print top N per symbol
    for sym in symbols:
        rows = sorted(results_by_symbol[sym], key=lambda r: float(r.get("score", 0.0)), reverse=True)
        print("\n" + "=" * 80)
        print(f"Top {args.top} for {sym}")
        for r in rows[: int(args.top)]:
            print(
                f"score={r['score']:.1f} pnl={r['pnl_points']:.1f} dd={r['max_drawdown_points']:.1f} "
                f"wr={r['winrate']:.1f}% trades={r['trades']} "
                f"TP={r['tp_points']:.0f} ADX>={r['adx_min']:.0f} EMA={r['ema_fast']}/{r['ema_slow']}"
            )

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

