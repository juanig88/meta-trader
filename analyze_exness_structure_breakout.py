"""Exploratory, read-only test of an M5 structure-breakout hypothesis on Exness.

This script never sends orders. The entry and exit assumptions are deliberately
simple so the directional premise can be measured before designing trade
management:

- signal on a closed M5 bar that closes beyond the prior 12-bar range
- require close/EMA20 direction, a four-bar EMA20 slope, and 1.2x prior
  20-bar tick volume
- enter at the next bar open and independently measure 15m, 1h, and 4h exits
- allow only one open trade per horizon; apply the historical bar spread

The rule was formulated after viewing the two example sessions. Results over
the same history are exploratory, not an out-of-sample validation.
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from trading.domain.demo_gate import TRADE_MODE_REAL

SYMBOL = "BTCUSDm"
TIMEFRAME_SECONDS = 5 * 60
RANGE_BARS = 12
EMA_PERIOD = 20
EMA_SLOPE_BARS = 4
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLE = 1.2
HORIZONS_MIN = (15, 60, 240)
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 20261027
DEFAULT_DAYS = 60
OUTPUT_PATH = Path("analyze_exness_structure_breakout_out.json")
EXAMPLE_WINDOWS = (
    (datetime(2026, 10, 24, 19, 0, tzinfo=timezone.utc),
     datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)),
    (datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc),
     datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)),
)


def ema(values: Sequence[float], period: int) -> list[float]:
    if period < 1:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def signal_side(
    index: int,
    times: Sequence[int],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    ema20: Sequence[float],
) -> str | None:
    """Return a breakout side using only bars closed at ``index`` or earlier."""
    need = max(RANGE_BARS, EMA_SLOPE_BARS, VOLUME_LOOKBACK)
    if index < need or index >= len(times):
        return None
    if any(
        times[i] - times[i - 1] != TIMEFRAME_SECONDS
        for i in range(index - need + 1, index + 1)
    ):
        return None
    prior_high = max(float(value) for value in highs[index - RANGE_BARS:index])
    prior_low = min(float(value) for value in lows[index - RANGE_BARS:index])
    prior_volume = sum(float(value) for value in volumes[index - VOLUME_LOOKBACK:index]) / VOLUME_LOOKBACK
    if prior_volume <= 0:
        return None
    close = float(closes[index])
    volume_confirms = float(volumes[index]) >= VOLUME_MULTIPLE * prior_volume
    if not volume_confirms:
        return None
    if close > prior_high and close > ema20[index] and ema20[index] > ema20[index - EMA_SLOPE_BARS]:
        return "BUY"
    if close < prior_low and close < ema20[index] and ema20[index] < ema20[index - EMA_SLOPE_BARS]:
        return "SELL"
    return None


def h4_bias(signal_close_time: int, h4: Mapping[str, Sequence[float]]) -> str:
    """Classify the last fully closed H4 bar; this is diagnostic, not a gate."""
    times = h4["time"]
    closes = h4["close"]
    averages = h4["ema20"]
    ends = [int(value) + 4 * 60 * 60 for value in times]
    index = bisect_right(ends, int(signal_close_time)) - 1
    if index < 4:
        return "unknown"
    if closes[index] > averages[index] and averages[index] > averages[index - 4]:
        return "bullish"
    if closes[index] < averages[index] and averages[index] < averages[index - 4]:
        return "bearish"
    return "mixed"


def build_trades(
    m5: Mapping[str, Sequence[float]],
    h4: Mapping[str, Sequence[float]],
    *,
    point: float,
    contract_size: float,
    volume_min: float,
    horizon_minutes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create non-overlapping trades for one fixed holding horizon."""
    if horizon_minutes not in HORIZONS_MIN:
        raise ValueError(f"unsupported horizon: {horizon_minutes}")
    times, opens = m5["time"], m5["open"]
    highs, lows, closes = m5["high"], m5["low"], m5["close"]
    spreads, volumes = m5["spread"], m5["volume"]
    averages = ema(closes, EMA_PERIOD)
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    hold_bars = horizon_minutes // 5
    next_available_index = 0

    for index in range(max(RANGE_BARS, EMA_SLOPE_BARS, VOLUME_LOOKBACK), len(times)):
        signal = signal_side(index, times, highs, lows, closes, volumes, averages)
        if signal is None:
            continue
        signal_time = int(times[index])
        entry_index = index + 1
        exit_index = entry_index + hold_bars - 1
        row = {
            "side": signal,
            "signal_time": signal_time,
            "signal_close": float(closes[index]),
            "h4_bias": h4_bias(signal_time + TIMEFRAME_SECONDS, h4),
        }
        if index < next_available_index:
            row["status"] = "position_open"
            signals.append(row)
            continue
        if index + hold_bars >= len(times):
            row["status"] = "insufficient_future_bars"
            signals.append(row)
            continue
        entry_index = index + 1
        exit_index = entry_index + hold_bars - 1
        if any(
            times[i] - times[i - 1] != TIMEFRAME_SECONDS
            for i in range(index + 1, exit_index + 1)
        ):
            row["status"] = "data_gap"
            signals.append(row)
            continue
        row["status"] = "traded"
        signals.append(row)

        entry_bid = float(opens[entry_index])
        exit_bid = float(closes[exit_index])
        entry_spread = max(0.0, float(spreads[entry_index]) * point)
        exit_spread = max(0.0, float(spreads[exit_index]) * point)
        if signal == "BUY":
            entry_fill = entry_bid + entry_spread
            net_price_move = exit_bid - entry_fill
        else:
            entry_fill = entry_bid
            net_price_move = entry_fill - (exit_bid + exit_spread)
        gross_price_move = (exit_bid - entry_bid) * (1.0 if signal == "BUY" else -1.0)
        net_bps = net_price_move / entry_fill * 10_000.0
        gross_bps = gross_price_move / entry_bid * 10_000.0
        favorable, adverse = _excursions(
            signal, entry_fill, entry_index, exit_index, highs, lows, spreads, point
        )
        trades.append({
            **row,
            "entry_time": int(times[entry_index]),
            "exit_time": int(times[exit_index]) + TIMEFRAME_SECONDS,
            "entry_price": entry_fill,
            "exit_bid": exit_bid,
            "gross_bps": gross_bps,
            "net_bps": net_bps,
            "net_usd_min_lot": net_price_move * contract_size * volume_min,
            "spread_cost_price": entry_spread if signal == "BUY" else exit_spread,
            "favorable_excursion_price": favorable,
            "adverse_excursion_price": adverse,
            "crosses_utc_date": (
                int(times[entry_index]) // 86400 != int(times[exit_index]) // 86400
            ),
        })
        next_available_index = exit_index + 1
    return signals, trades


def _excursions(
    side: str,
    entry: float,
    start: int,
    end: int,
    highs: Sequence[float],
    lows: Sequence[float],
    spreads: Sequence[float],
    point: float,
) -> tuple[float, float]:
    if side == "BUY":
        return (
            max(float(highs[i]) for i in range(start, end + 1)) - entry,
            entry - min(float(lows[i]) for i in range(start, end + 1)),
        )
    return (
        entry - min(
            float(lows[i]) + max(0.0, float(spreads[i]) * point)
            for i in range(start, end + 1)
        ),
        max(float(highs[i]) + max(0.0, float(spreads[i]) * point) for i in range(start, end + 1)) - entry,
    )


def report_stats(trades: Sequence[Mapping[str, Any]], seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    returns = np.asarray([float(row["net_bps"]) for row in trades], dtype=float)
    days = np.asarray([int(row["entry_time"]) // 86400 for row in trades], dtype=np.int64)
    unique_days, inverse = np.unique(days, return_inverse=True)
    daily_sums = np.bincount(inverse, weights=returns)
    daily_counts = np.bincount(inverse).astype(float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(unique_days), size=(BOOTSTRAP_ITERATIONS, len(unique_days)))
    sampled_means = daily_sums[picks].sum(axis=1) / daily_counts[picks].sum(axis=1)
    wins = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    return {
        "n": int(len(trades)),
        "days_with_trades": int(len(unique_days)),
        "net_mean_bps": float(returns.mean()),
        "net_median_bps": float(np.median(returns)),
        "net_ci95_bps_day_bootstrap": [
            float(np.percentile(sampled_means, 2.5)),
            float(np.percentile(sampled_means, 97.5)),
        ],
        "bootstrap_fraction_mean_le_zero": float(np.mean(sampled_means <= 0.0)),
        "win_rate": float(np.mean(returns > 0)),
        "profit_factor": float(wins / losses) if losses > 0 else None,
        "net_sum_bps": float(returns.sum()),
        "net_sum_usd_min_lot": float(sum(float(row["net_usd_min_lot"]) for row in trades)),
        "gross_mean_bps": float(np.mean([float(row["gross_bps"]) for row in trades])),
        "spread_cost_mean_bps": float(np.mean([
            float(row["spread_cost_price"]) / float(row["entry_price"]) * 10_000.0
            for row in trades
        ])),
        "median_favorable_excursion_price": float(np.median([
            float(row["favorable_excursion_price"]) for row in trades
        ])),
        "median_adverse_excursion_price": float(np.median([
            float(row["adverse_excursion_price"]) for row in trades
        ])),
        "utc_date_crossings_without_swap": sum(bool(row["crosses_utc_date"]) for row in trades),
    }


def _subset_stats(trades: Sequence[Mapping[str, Any]], key: str, value: str) -> dict[str, Any]:
    return report_stats([row for row in trades if row.get(key) == value])


def analyze_rates(
    rates: Any,
    *,
    point: float,
    contract_size: float,
    volume_min: float,
    days: int,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    now_epoch = int(now.timestamp())
    rows = sorted(
        (row for row in rates if int(row["time"]) + TIMEFRAME_SECONDS <= now_epoch),
        key=lambda row: int(row["time"]),
    )
    if len(rows) < 300:
        raise ValueError(f"only {len(rows)} closed M5 bars available")
    times = [int(row["time"]) for row in rows]
    start_epoch = now_epoch - days * 86400
    eval_start = next((i for i, t in enumerate(times) if t >= start_epoch), len(times))
    if eval_start == len(times):
        raise ValueError("no bars in requested evaluation window")

    m5 = {
        "time": times,
        "open": [float(row["open"]) for row in rows],
        "high": [float(row["high"]) for row in rows],
        "low": [float(row["low"]) for row in rows],
        "close": [float(row["close"]) for row in rows],
        "spread": [float(row["spread"]) for row in rows],
        "volume": [float(row["tick_volume"]) for row in rows],
    }
    # Keep one shared UTC daily series for contextual H4 classification.
    h4_rows = _aggregate_bars(rows, 4 * 60 * 60)
    h4 = {
        "time": [int(row["time"]) for row in h4_rows],
        "close": [float(row["close"]) for row in h4_rows],
    }
    h4["ema20"] = ema(h4["close"], EMA_PERIOD)

    # Keep the lookback bars for indicators, but only report entries in-window.
    all_results: dict[str, Any] = {}
    for horizon in HORIZONS_MIN:
        signals, trades = build_trades(
            m5, h4, point=point, contract_size=contract_size,
            volume_min=volume_min, horizon_minutes=horizon,
        )
        window_signals = [s for s in signals if s["signal_time"] >= start_epoch]
        window_trades = [t for t in trades if t["signal_time"] >= start_epoch]
        all_results[f"{horizon}m"] = {
            "signals_in_window": len(window_signals),
            "eligible_trades": len(window_trades),
            "signal_dispositions": {
                status: sum(1 for signal in window_signals if signal["status"] == status)
                for status in ("traded", "position_open", "data_gap", "insufficient_future_bars")
            },
            "overall": report_stats(window_trades, BOOTSTRAP_SEED + horizon),
            "by_side": {
                side: _subset_stats(window_trades, "side", side)
                for side in ("BUY", "SELL")
            },
            "by_h4_bias": {
                bias: _subset_stats(window_trades, "h4_bias", bias)
                for bias in ("bullish", "bearish", "mixed", "unknown")
            },
            "example_window_trades": [
                _trade_summary(t) for t in window_trades
                if any(start <= datetime.fromtimestamp(t["signal_time"], timezone.utc) < end
                       for start, end in EXAMPLE_WINDOWS)
            ],
        }

    spreads = [float(row["spread"]) * point for row in rows[eval_start:]]
    from_time = datetime.fromtimestamp(times[eval_start], timezone.utc)
    return {
        "envia_ordenes": False,
        "resultados_exploratorios": True,
        "regla_post_hoc": True,
        "generated_at_utc": now.isoformat(),
        "symbol": SYMBOL,
        "server": None,
        "trade_mode": None,
        "period_days_requested": days,
        "evaluation_from_utc": from_time.isoformat(),
        "evaluation_to_utc": datetime.fromtimestamp(times[-1], timezone.utc).isoformat(),
        "bars_m5_total_closed": len(rows),
        "bars_m5_in_evaluation": len(rows) - eval_start,
        "contract": {
            "point": point,
            "contract_size": contract_size,
            "volume_min": volume_min,
            "median_historical_spread_price": float(np.median(spreads)),
            "p90_historical_spread_price": float(np.percentile(spreads, 90)),
            "commission_included": False,
            "swap_included": False,
            "slippage_included": False,
        },
        "rule": {
            "signal_timeframe": "M5",
            "breakout": "close beyond max/min of previous 12 closed M5 bars",
            "trend_filter": "close on same side of EMA20 and EMA20 slope aligned over 4 bars",
            "volume_filter": "tick_volume >= 1.2 * mean of previous 20 closed M5 bars",
            "entry": "next M5 bar open; chart bars assumed bid, historical spread applied once",
            "exits": "fixed 15m, 60m, and 240m close; one position per horizon",
            "h4_bias": "last fully closed UTC H4 close relative to EMA20 and EMA20 4-bar slope; diagnostic only",
            "stop_loss_take_profit": "none; this tests direction persistence, not a finished strategy",
        },
        "notes": [
            "The rule was chosen after seeing the two supplied chart intervals; exclude them from claims of independent validation.",
            "Results on historical data are exploratory. Confirm only with a future frozen holdout/forward test.",
            "MT5 OHLC are treated as bid prices. Entry/exit spread is estimated from the corresponding M5 bar spread field.",
            "Commission, slippage, and swap are not included. UTC date crossings are reported because 4h trades may incur swap.",
            "Tick volume is broker tick activity, not consolidated BTC exchange volume.",
            "Historical contract terms can change; report uses current contract_size and volume_min for dollar estimates.",
        ],
        "horizons": all_results,
    }


def _aggregate_bars(rows: Sequence[Any], seconds: int) -> list[dict[str, float | int]]:
    buckets: dict[int, list[Any]] = {}
    for row in rows:
        start = int(row["time"])
        bucket = start // seconds * seconds
        buckets.setdefault(bucket, []).append(row)
    result: list[dict[str, float | int]] = []
    for bucket, group in sorted(buckets.items()):
        group.sort(key=lambda row: int(row["time"]))
        expected = seconds // TIMEFRAME_SECONDS
        if len(group) != expected or any(
            int(group[i]["time"]) - int(group[i - 1]["time"]) != TIMEFRAME_SECONDS
            for i in range(1, len(group))
        ):
            continue
        if int(group[0]["time"]) != bucket:
            continue
        result.append({
            "time": bucket,
            "open": float(group[0]["open"]),
            "high": max(float(row["high"]) for row in group),
            "low": min(float(row["low"]) for row in group),
            "close": float(group[-1]["close"]),
            "volume": sum(int(row["tick_volume"]) for row in group),
        })
    return result


def _trade_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    summary = dict(row)
    for key in ("signal_time", "entry_time", "exit_time"):
        summary[key.replace("_time", "_utc")] = datetime.fromtimestamp(
            int(summary.pop(key)), timezone.utc
        ).isoformat()
    return summary


def _load_mt5() -> Any:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 Python package is not installed") from exc
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    return mt5


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="evaluation lookback in calendar days")
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH, help="JSON output path")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be positive")
    try:
        mt5 = _load_mt5()
        try:
            account = mt5.account_info()
            if account is None:
                raise RuntimeError(f"MT5 account_info failed: {mt5.last_error()}")
            if int(getattr(account, "trade_mode", -1)) == TRADE_MODE_REAL:
                raise RuntimeError("refusing to run from a real-trading account")
            info = mt5.symbol_info(SYMBOL)
            if info is None or not mt5.symbol_select(SYMBOL, True):
                raise RuntimeError(f"symbol {SYMBOL} unavailable: {mt5.last_error()}")
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=args.days + 10)
            rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, start, now)
            if rates is None:
                raise RuntimeError(f"MT5 rates request failed: {mt5.last_error()}")
            result = analyze_rates(
                rates,
                point=float(info.point),
                contract_size=float(info.trade_contract_size),
                volume_min=float(info.volume_min),
                days=args.days,
                generated_at=now,
            )
            result["server"] = str(getattr(account, "server", "") or "")
            result["trade_mode"] = int(getattr(account, "trade_mode", -1))
        finally:
            mt5.shutdown()
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_summary(result)
        print(f"\nJSON: {args.out}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _print_summary(result: Mapping[str, Any]) -> None:
    print(
        f"{result['symbol']} {result['evaluation_from_utc']} to {result['evaluation_to_utc']} "
        f"bars={result['bars_m5_in_evaluation']} "
        f"median_spread_price={result['contract']['median_historical_spread_price']:.4f} "
        f"trade_mode={result['trade_mode']} enviar_ordenes={result['envia_ordenes']}"
    )
    for horizon, block in result["horizons"].items():
        stats = block["overall"]
        dispositions = block["signal_dispositions"]
        print(
            f"{horizon}: signals={block['signals_in_window']} trades={stats.get('n', 0)} "
            f"blocked={dispositions['position_open']} data_gaps={dispositions['data_gap']} "
            f"mean_net={stats.get('net_mean_bps', 0.0):.3f} bps "
            f"CI95={stats.get('net_ci95_bps_day_bootstrap', [])} "
            f"WR={stats.get('win_rate', 0.0):.1%} PF={stats.get('profit_factor')} "
            f"net_min_lot=${stats.get('net_sum_usd_min_lot', 0.0):.2f}"
        )
        for side, side_stats in block["by_side"].items():
            if side_stats.get("n"):
                print(
                    f"  {side}: n={side_stats['n']} "
                    f"mean_net={side_stats['net_mean_bps']:.3f} bps "
                    f"WR={side_stats['win_rate']:.1%} PF={side_stats['profit_factor']}"
                )


if __name__ == "__main__":
    raise SystemExit(main())
