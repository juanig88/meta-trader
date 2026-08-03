from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
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


def _score(pnl_points: float, max_dd_points: float) -> float:
    return float(pnl_points) + float(max_dd_points)


def _spread_points_now(symbol: str) -> Optional[float]:
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None or not info.point:
        return None
    return float((tick.ask - tick.bid) / float(info.point))


def _looks_like_fx(symbol: str) -> bool:
    # crude heuristic: 6 letters core, often with suffixes like ".m", "-ECN", etc.
    core = symbol
    for sep in (".", "-", "_"):
        if sep in core:
            core = core.split(sep, 1)[0]
            break
    if len(core) != 6:
        return False
    return core.isalpha()


def _core_pair(symbol: str) -> Optional[str]:
    core = symbol
    for sep in (".", "-", "_"):
        if sep in core:
            core = core.split(sep, 1)[0]
            break
    core = core.strip()
    if len(core) != 6 or not core.isalpha():
        return None
    return core.upper()


def _is_tier1_fx(symbol: str) -> bool:
    core = _core_pair(symbol)
    if core is None:
        return False
    a, b = core[:3], core[3:]
    tier1 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
    return a in tier1 and b in tier1 and a != b


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Scan ~25 symbols and rank top 3 by backtest score.")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--sl", type=float, default=80)
    ap.add_argument("--tp", type=float, default=160)
    ap.add_argument("--adx-min", type=float, default=20.0, help="Match BOT_ADX_MIN (live default 20).")
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--adx-period", type=int, default=14)
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--out", default="scan_results.jsonl")
    ap.add_argument("--max-spread-now", type=float, default=80, help="Skip symbols with current spread > this (points).")
    ap.add_argument(
        "--min-spread-bt",
        type=float,
        default=1.0,
        help="Floor for backtest spread (points). Avoids spread=0 ticks skewing results.",
    )
    args = ap.parse_args()

    cfg = Config()
    if not connect_mt5(cfg):
        return 2

    end = datetime.now()
    start = end - timedelta(days=int(args.days))
    params = StrategyParams(
        ema_fast=int(args.ema_fast),
        ema_slow=int(args.ema_slow),
        adx_period=int(args.adx_period),
        adx_min=float(args.adx_min),
        crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
    )

    syms = mt5.symbols_get()
    if syms is None:
        print("Could not list symbols from MT5.")
        return 3

    candidates: list[tuple[str, float]] = []
    for s in syms:
        name = getattr(s, "name", None)
        if not name or not isinstance(name, str):
            continue
        if not _looks_like_fx(name):
            continue
        if not _is_tier1_fx(name):
            continue
        if not mt5.symbol_select(name, True):
            continue
        spr = _spread_points_now(name)
        if spr is None:
            continue
        if spr > float(args.max_spread_now):
            continue
        candidates.append((name, spr))

    # Prefer low spread symbols first (liquidity proxy)
    candidates.sort(key=lambda x: x[1])
    selected = candidates[: int(args.limit)]
    print(f"Selected {len(selected)} symbols (lowest current spread).", flush=True)

    rows: list[dict] = []
    for i, (name, spr_now) in enumerate(selected, start=1):
        print(f"[{i}/{len(selected)}] backtesting {name} (spread_now={spr_now:.1f} pts)...", flush=True)
        # Use current spread as backtest spread assumption (screening). Optional: override later per-session.
        spread_bt = max(float(spr_now), float(args.min_spread_bt))
        try:
            rep = run_backtest(
                symbol=name,
                start=start,
                end=end,
                sl_points=float(args.sl),
                tp_points=float(args.tp),
                spread_points=float(spread_bt),
                params=params,
            )
        except RuntimeError:
            print(f"{name}: skip (no data / insufficient history)", flush=True)
            continue
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": name,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "sl_points": float(args.sl),
            "tp_points": float(args.tp),
            "spread_points": float(spread_bt),
            "spread_now_points": float(spr_now),
            "ema_fast": params.ema_fast,
            "ema_slow": params.ema_slow,
            "adx_period": params.adx_period,
            "adx_min": params.adx_min,
            "trades": rep.trades,
            "winrate": rep.winrate,
            "pnl_points": rep.pnl_points,
            "max_drawdown_points": rep.max_drawdown_points,
        }
        row["score"] = _score(row["pnl_points"], row["max_drawdown_points"])
        rows.append(row)

        with open(args.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        print(
            f"{name}: score={row['score']:.1f} pnl={row['pnl_points']:.1f} dd={row['max_drawdown_points']:.1f} "
            f"wr={row['winrate']:.1f}% trades={row['trades']} spr={row['spread_points']:.1f}",
            flush=True,
        )

    rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
    print("\nTOP 3", flush=True)
    for r in rows[:3]:
        print(
            f"{r['symbol']}: score={r['score']:.1f} pnl={r['pnl_points']:.1f} dd={r['max_drawdown_points']:.1f} "
            f"wr={r['winrate']:.1f}% trades={r['trades']} spr={r['spread_points']:.1f}",
            flush=True,
        )

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

