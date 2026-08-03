from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta

import MetaTrader5 as mt5

from bot_mt5 import Config, connect_mt5, _env_bool


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
from portfolio_backtest import build_market_cache, run_portfolio_backtest
from strategy_core import StrategyParams


def _weights_grid_3(step: float) -> list[tuple[float, float, float]]:
    """
    Simplex grid for 3 weights that sum to 1.0 (within rounding).
    """
    step = float(step)
    if step <= 0 or step > 1:
        step = 0.1
    n = int(round(1.0 / step))
    out: list[tuple[float, float, float]] = []
    for i in range(n + 1):
        w1 = i * step
        for j in range(n + 1 - i):
            w2 = j * step
            w3 = 1.0 - w1 - w2
            if w3 < -1e-9:
                continue
            # snap tiny negatives to 0
            w3 = max(0.0, w3)
            # normalize after float ops
            s = w1 + w2 + w3
            if s <= 0:
                continue
            w1n, w2n, w3n = w1 / s, w2 / s, w3 / s
            out.append((round(w1n, 6), round(w2n, 6), round(w3n, 6)))
    # unique
    uniq = list(dict.fromkeys(out))
    return uniq


def _weights_grid_4(step: float) -> list[tuple[float, float, float, float]]:
    """
    Simplex grid for 4 weights that sum to 1.0 (within rounding).
    Combinations count for step=0.1 is C(10+3,3)=286 (manageable).
    """
    step = float(step)
    if step <= 0 or step > 1:
        step = 0.1
    n = int(round(1.0 / step))
    out: list[tuple[float, float, float, float]] = []
    for i in range(n + 1):
        w1 = i * step
        for j in range(n + 1 - i):
            w2 = j * step
            for k in range(n + 1 - i - j):
                w3 = k * step
                w4 = 1.0 - w1 - w2 - w3
                if w4 < -1e-9:
                    continue
                w4 = max(0.0, w4)
                s = w1 + w2 + w3 + w4
                if s <= 0:
                    continue
                w1n, w2n, w3n, w4n = w1 / s, w2 / s, w3 / s, w4 / s
                out.append((round(w1n, 6), round(w2n, 6), round(w3n, 6), round(w4n, 6)))
    uniq = list(dict.fromkeys(out))
    return uniq


def _spread_map_from_mt5(symbols: list[str]) -> dict[str, float]:
    """
    Fallback: use current spread as proxy in points.
    """
    out: dict[str, float] = {}
    for s in symbols:
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        if info is None or tick is None or not getattr(info, "point", None):
            out[s] = 20.0
            continue
        point = float(info.point)
        if point <= 0:
            out[s] = 20.0
            continue
        out[s] = float((float(tick.ask) - float(tick.bid)) / point)
    return out


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Grid search portfolio: weights + risk_pct + rebalance with DD constraint.")
    ap.add_argument("--symbols", default="GBPUSD,USDJPY,NZDJPY,XAUUSD")
    ap.add_argument("--spreads", default="", help="Optional. Comma: GBPUSD=6,USDJPY=3,... (points). If empty, uses current spread.")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--initial-balance", type=float, default=100.0)
    ap.add_argument("--sl", type=float, default=80)
    ap.add_argument("--tp", type=float, default=160)
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--adx-period", type=int, default=14)
    ap.add_argument("--adx-min", type=float, default=20.0, help="Match BOT_ADX_MIN / live bot.")
    ap.add_argument("--risk-list", default="2,3,4,5,6", help="Grid of risk %% values (portfolio scales by weights).")
    ap.add_argument("--rebalance-list", default="0,7,14,30")
    ap.add_argument("--weight-step", type=float, default=0.1, help="Grid step for weights simplex (e.g. 0.1).")
    ap.add_argument("--target-final", type=float, default=1000.0)
    ap.add_argument("--dd-min", type=float, default=-200.0, help="Keep only runs with max_drawdown_money >= this.")
    ap.add_argument("--out", default="portfolio_grid.jsonl")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max-tests", type=int, default=0, help="Optional cap for number of combos tested (0=all).")
    ap.add_argument("--progress-every", type=int, default=50, help="Print progress every N tests (0=off).")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if len(symbols) not in (3, 4):
        raise SystemExit("This grid runner supports 3 or 4 symbols.")

    spread_map: dict[str, float] = {}
    if str(args.spreads).strip():
        for part in str(args.spreads).split(","):
            if not part.strip() or "=" not in part:
                continue
            k, v = part.split("=", 1)
            spread_map[k.strip()] = float(v.strip())

    risk_list = [float(x.strip()) for x in str(args.risk_list).split(",") if x.strip()]
    rebalance_list = [int(float(x.strip())) for x in str(args.rebalance_list).split(",") if x.strip()]

    cfg = Config(symbols=tuple(symbols))
    if not connect_mt5(cfg):
        return 2
    for s in symbols:
        mt5.symbol_select(s, True)

    if not spread_map:
        spread_map = _spread_map_from_mt5(symbols)
    else:
        for s in symbols:
            if s not in spread_map:
                spread_map[s] = float(_spread_map_from_mt5([s]).get(s, 20.0))

    end = datetime.now()
    start = end - timedelta(days=int(args.days))

    params = StrategyParams(
        ema_fast=int(args.ema_fast),
        ema_slow=int(args.ema_slow),
        adx_period=int(args.adx_period),
        adx_min=float(args.adx_min),
        crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
    )

    cache = build_market_cache(symbols=symbols, start=start, end=end, params=params)

    base_lot = float(os.getenv("BOT_LOT", "0.01") or "0.01")
    weight_step = float(args.weight_step)
    weight_grid_3 = _weights_grid_3(weight_step) if len(symbols) == 3 else None
    weight_grid_4 = _weights_grid_4(weight_step) if len(symbols) == 4 else None

    keep: list[dict] = []
    tested = 0
    weight_iter = weight_grid_3 if len(symbols) == 3 else weight_grid_4
    assert weight_iter is not None
    for ws in weight_iter:
        weights = {symbols[i]: float(ws[i]) for i in range(len(symbols))}
        for risk_pct in risk_list:
            for reb_days in rebalance_list:
                tested += 1
                if int(args.max_tests) > 0 and tested > int(args.max_tests):
                    break
                rep = run_portfolio_backtest(
                    symbols=symbols,
                    weights=weights,
                    start=start,
                    end=end,
                    sl_points=float(args.sl),
                    tp_points=float(args.tp),
                    spread_points_by_symbol=spread_map,
                    params=params,
                    initial_balance=float(args.initial_balance),
                    base_lot=float(base_lot),
                    risk_pct=float(risk_pct),
                    rebalance_days=int(reb_days),
                    cache=cache,
                )
                row = asdict(rep)
                row["tested"] = tested
                row["dd_min_constraint"] = float(args.dd_min)
                row["target_final"] = float(args.target_final)

                ok_dd = float(rep.max_drawdown_money) >= float(args.dd_min)
                ok_target = float(rep.final_balance) >= float(args.target_final)
                row["ok_dd"] = ok_dd
                row["ok_target"] = ok_target

                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

                if ok_dd and ok_target:
                    keep.append(row)
                pe = int(args.progress_every)
                if pe > 0 and tested % pe == 0:
                    print(f"Progress: tested={tested} keep={len(keep)} last_final={float(rep.final_balance):.2f} last_dd={float(rep.max_drawdown_money):.2f}")
            if int(args.max_tests) > 0 and tested > int(args.max_tests):
                break
        if int(args.max_tests) > 0 and tested > int(args.max_tests):
            break

    mt5.shutdown()

    keep_sorted = sorted(keep, key=lambda r: float(r.get("final_balance", 0.0)), reverse=True)
    print(f"Tested: {tested} combos. Passing constraints: {len(keep_sorted)}")
    for r in keep_sorted[: int(args.top)]:
        w = r.get("weights", {})
        print(
            f"final={r.get('final_balance'):.2f} dd={r.get('max_drawdown_money'):.2f} "
            f"risk={r.get('risk_pct')} reb={r.get('rebalance_days')} "
            f"w={w}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

