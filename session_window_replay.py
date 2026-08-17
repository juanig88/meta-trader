"""
Replay 30d (M15) for multi-session coverage: USDJPY + BTCUSD.

Filters entries to proposed LOCAL (ART) session windows, applies spread +
commission + slippage. Writes JSONL metrics for enable/disable gate.

Usage:
  python session_window_replay.py
  python session_window_replay.py --days 30 --out session_replay_results.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, time as dt_time
from typing import Any, Optional, Sequence, Tuple

import MetaTrader5 as mt5

from backtest import (
    BacktestReport,
    Trade,
    _clamp_volume,
    _equity_for_risk_sizing,
    _lot_by_risk_points,
    _trend_h4_series_for_m15,
    _value_per_point_per_lot,
    connect_mt5,
)
from backtest import _load_dotenv
from bot_mt5 import Config, parse_trading_window_sessions
from indicators import atr as atr_series, ema as ema_series_fn, adx_wilder
from strategy_core import (
    Features,
    StrategyParams,
    decide,
    price_slope_m15,
    signal_m15_relaxed,
    signal_m15_strong_breakout,
    symbol_uses_crypto_relaxed_m15,
)

# Broker M15 bar clock ≈ ART + 3h (IC Markets, validated).
BROKER_MINUS_LOCAL_H = 3

WINDOWS: dict[str, str] = {
    "USDJPY": "20:00-02:00,04:00-08:00",
    "BTCUSD": "06:00-09:00,14:00-18:00,21:00-23:00",
}

COST: dict[str, dict[str, float]] = {
    "USDJPY": {
        "spread_points": 25.0,
        "slippage_points": 5.0,
        "commission_rt_per_lot": 6.0,
        "adx_min": 20.0,
        "sl_points": 80.0,
        "tp_points": 160.0,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.5,
        "use_atr": 0,
        "ema_fast": 50,
        "ema_slow": 200,
        "weekdays_only": 0,
        "entry_mode_strict": 1,
    },
    "BTCUSD": {
        "spread_points": 1200.0,
        "slippage_points": 200.0,
        "commission_rt_per_lot": 0.0,
        "adx_min": 22.0,
        "sl_points": 50000.0,
        "tp_points": 75000.0,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.5,
        "use_atr": 1,
        "ema_fast": 12,
        "ema_slow": 26,
        "weekdays_only": 1,
        "entry_mode_strict": 0,
    },
}


def _local_from_broker_ts(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts)) - timedelta(hours=BROKER_MINUS_LOCAL_H)


def _in_sessions(local: datetime, sessions: Tuple[Tuple[dt_time, dt_time], ...]) -> bool:
    mm = local.hour * 60.0 + local.minute + local.second / 60.0
    for ta, tb in sessions:
        lo = ta.hour * 60.0 + ta.minute
        hi = tb.hour * 60.0 + tb.minute
        if lo <= hi:
            if lo <= mm <= hi:
                return True
        else:
            if mm >= lo or mm <= hi:
                return True
    return False


def _entry_mask(
    m15: Any,
    sessions: Tuple[Tuple[dt_time, dt_time], ...],
    *,
    weekdays_only: bool,
) -> list[bool]:
    out: list[bool] = []
    for r in m15:
        local = _local_from_broker_ts(int(r["time"]))
        if weekdays_only and local.weekday() >= 5:
            out.append(False)
            continue
        out.append(_in_sessions(local, sessions))
    return out


def _run_filtered(
    *,
    symbol: str,
    m15,
    h4,
    sl_points: float,
    tp_points: float,
    spread_points: float,
    params: StrategyParams,
    initial_balance: float,
    lot: float,
    risk_sizing: bool,
    risk_per_trade_pct: float,
    use_atr: bool,
    atr_period: int,
    atr_sl_mult: float,
    atr_tp_mult: float,
    atr_min_points: float,
    commission_round_trip_per_lot: float,
    slippage_points: float,
    entry_allowed: Sequence[bool],
    session_start: datetime,
    session_end: datetime,
    entry_mode_strict: bool,
) -> tuple[BacktestReport, list[Trade]]:
    open_m15 = [float(r["open"]) for r in m15]
    high_m15 = [float(r["high"]) for r in m15]
    low_m15 = [float(r["low"]) for r in m15]
    close_m15 = [float(r["close"]) for r in m15]
    time_m15 = [int(r["time"]) for r in m15]
    trend_h4_series = _trend_h4_series_for_m15(m15_times=time_m15, h4_rates=h4, params=params)

    atr_vals = atr_series(high_m15, low_m15, close_m15, period=int(atr_period))
    ema_fast_arr = ema_series_fn(close_m15, int(params.ema_fast))
    ema_slow_arr = ema_series_fn(close_m15, int(params.ema_slow))
    adx_arr = adx_wilder(high_m15, low_m15, close_m15, period=int(params.adx_period)).adx

    info = mt5.symbol_info(symbol)
    point = float(info.point) if info and info.point else 0.00001
    vpp = _value_per_point_per_lot(symbol, point) or 1.0

    balance_pts = 0.0
    peak_pts = 0.0
    max_dd_pts = 0.0
    balance_money = float(initial_balance)
    peak_money = float(initial_balance)
    max_dd_money = 0.0
    trades: list[Trade] = []
    pos: Optional[Trade] = None
    start_i = max(int(params.ema_slow) + 5, 2 * int(params.adx_period) + 2, 50)

    for i in range(start_i, len(m15) - 2):
        if pos is not None:
            hi = high_m15[i + 1]
            lo = low_m15[i + 1]
            eff_sl = float(pos.sl_initial)
            if pos.side == "BUY":
                sl_hit = lo <= eff_sl
                tp_hit = hi >= pos.tp
                if sl_hit or tp_hit:
                    exit_price = eff_sl if sl_hit else pos.tp
                    if slippage_points > 0:
                        exit_price = float(exit_price) - (float(slippage_points) * float(point))
                    pos.exit_idx = i + 1
                    pos.exit_price = exit_price
                    pos.pnl_points = (exit_price - pos.entry_price) / point
                    gross = float(pos.pnl_points) * float(vpp) * float(pos.lot)
                    pos.pnl_money = gross - float(commission_round_trip_per_lot) * float(pos.lot)
                    balance_pts += float(pos.pnl_points)
                    balance_money += float(pos.pnl_money)
                    trades.append(pos)
                    pos = None
            else:
                sl_hit = hi >= eff_sl
                tp_hit = lo <= pos.tp
                if sl_hit or tp_hit:
                    exit_price = eff_sl if sl_hit else pos.tp
                    if slippage_points > 0:
                        exit_price = float(exit_price) + (float(slippage_points) * float(point))
                    pos.exit_idx = i + 1
                    pos.exit_price = exit_price
                    pos.pnl_points = (pos.entry_price - exit_price) / point
                    gross = float(pos.pnl_points) * float(vpp) * float(pos.lot)
                    pos.pnl_money = gross - float(commission_round_trip_per_lot) * float(pos.lot)
                    balance_pts += float(pos.pnl_points)
                    balance_money += float(pos.pnl_money)
                    trades.append(pos)
                    pos = None

        eq_pts = float(balance_pts)
        eq_money = float(balance_money)
        if pos is not None:
            if pos.side == "BUY":
                unreal = (low_m15[i + 1] - float(pos.entry_price)) / float(point)
            else:
                unreal = (float(pos.entry_price) - high_m15[i + 1]) / float(point)
            eq_pts += float(unreal)
            eq_money += float(unreal) * float(vpp) * float(pos.lot)
        if eq_pts < peak_pts:
            max_dd_pts = min(max_dd_pts, eq_pts - peak_pts)
        peak_pts = max(peak_pts, eq_pts)
        if eq_money < peak_money:
            max_dd_money = min(max_dd_money, eq_money - peak_money)
        peak_money = max(peak_money, eq_money)

        if pos is not None:
            continue

        local = _local_from_broker_ts(time_m15[i])
        if local < session_start or local > session_end:
            continue
        if i >= len(entry_allowed) or not entry_allowed[i]:
            continue

        trend_h4 = trend_h4_series[i]
        _lo = max(0, i - 1)
        _oh = open_m15[_lo : i + 1]
        _hh = high_m15[_lo : i + 1]
        _lh = low_m15[_lo : i + 1]
        _ch = close_m15[_lo : i + 1]
        if entry_mode_strict:
            sig = signal_m15_strong_breakout(open_m15=_oh, high_m15=_hh, low_m15=_lh, close_m15=_ch)
        else:
            sig = signal_m15_relaxed(
                open_m15=_oh,
                high_m15=_hh,
                low_m15=_lh,
                close_m15=_ch,
                is_crypto=symbol_uses_crypto_relaxed_m15(symbol),
            )

        ef = ema_fast_arr[i] if i < len(ema_fast_arr) else None
        es = ema_slow_arr[i] if i < len(ema_slow_arr) else None
        p = close_m15[i]
        if ef is None or es is None or ef != ef or es != es:
            continue
        if float(ef) > float(es) and float(p) > float(ef):
            ema_dir = "UP"
        elif float(ef) < float(es) and float(p) < float(ef):
            ema_dir = "DOWN"
        else:
            ema_dir = "FLAT"

        adx_v = adx_arr[i] if i < len(adx_arr) else None
        adx = float(adx_v) if adx_v is not None and adx_v == adx_v else None
        features = Features(
            trend_h4=trend_h4,
            signal_m15=sig,
            ema_dir=ema_dir,
            ema_fast=float(ef),
            ema_slow=float(es),
            adx=adx,
            price_slope_m15=price_slope_m15([float(close_m15[j]) for j in range(i + 1)], n=params.price_slope_n),
        )
        decision = decide(params, features, symbol=symbol)
        if decision not in ("BUY", "SELL"):
            continue

        sl_pts = float(sl_points)
        tp_pts = float(tp_points)
        if use_atr:
            a_now = atr_vals[i]
            if a_now is None or a_now != a_now or float(a_now) <= 0:
                continue
            atr_points = float(a_now) / float(point) if point > 0 else 0.0
            sl_pts = max(sl_pts, atr_points * float(atr_sl_mult), float(atr_min_points))
            tp_pts = max(tp_pts, atr_points * float(atr_tp_mult), float(atr_min_points))

        entry_open = open_m15[i + 1]
        spread_px = (float(spread_points) / 2.0) * point
        if decision == "BUY":
            entry = entry_open + spread_px
            if slippage_points > 0:
                entry += float(slippage_points) * point
            sl = entry - sl_pts * point
            tp = entry + tp_pts * point
        else:
            entry = entry_open - spread_px
            if slippage_points > 0:
                entry -= float(slippage_points) * point
            sl = entry + sl_pts * point
            tp = entry - tp_pts * point

        use_lot = (
            _lot_by_risk_points(
                symbol=symbol,
                equity=_equity_for_risk_sizing(balance_money, None),
                risk_pct=float(risk_per_trade_pct),
                sl_points=float(sl_pts),
                value_per_point_1lot=float(vpp),
                base_lot=float(lot),
            )
            if risk_sizing
            else _clamp_volume(symbol, float(lot))
        )
        use_lot = min(float(use_lot), 0.03)

        pos = Trade(
            side=decision,
            entry_idx=i + 1,
            entry_price=float(entry),
            sl=float(sl),
            tp=float(tp),
            lot=float(use_lot),
            sl_initial=float(sl),
            peak_mfe_money=0.0,
        )

    wins = sum(1 for t in trades if (t.pnl_money or 0) > 0)
    losses = sum(1 for t in trades if (t.pnl_money or 0) <= 0)
    wr = (100.0 * wins / len(trades)) if trades else 0.0
    rep = BacktestReport(
        symbol=symbol,
        trades=len(trades),
        wins=wins,
        losses=losses,
        winrate=wr,
        pnl_points=sum(float(t.pnl_points or 0) for t in trades),
        max_drawdown_points=max_dd_pts,
        pnl_money=sum(float(t.pnl_money or 0) for t in trades),
        max_drawdown_money=max_dd_money,
        initial_balance=initial_balance,
        final_balance=balance_money,
        lot=lot,
        lot_mode="risk" if risk_sizing else "fixed",
        risk_per_trade_pct=risk_per_trade_pct,
    )
    return rep, trades


def run_session_filtered_backtest(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    sessions_str: str,
    cost: dict[str, float],
    initial_balance: float = 100.0,
    lot: float = 0.01,
) -> dict[str, Any]:
    sessions = parse_trading_window_sessions(sessions_str)
    warm_start = start - timedelta(days=40)
    m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, warm_start, end)
    h4 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, warm_start, end)
    if m15 is None or h4 is None or len(m15) < 100:
        raise RuntimeError(f"{symbol}: insufficient rates m15={0 if m15 is None else len(m15)}")

    mask = _entry_mask(
        m15,
        sessions,
        weekdays_only=bool(int(cost.get("weekdays_only", 0))),
    )
    params = StrategyParams(
        ema_fast=int(cost["ema_fast"]),
        ema_slow=int(cost["ema_slow"]),
        adx_period=14,
        adx_min=float(cost["adx_min"]),
        entry_mode="strict" if int(cost.get("entry_mode_strict", 1)) else "relaxed",
        trend_mode="closes",
        allow_flat_h4=True,
        crypto_sell_requires_h4_down=True,
        price_slope_filter_enabled=False,
    )
    rep, trades = _run_filtered(
        symbol=symbol,
        m15=m15,
        h4=h4,
        sl_points=float(cost["sl_points"]),
        tp_points=float(cost["tp_points"]),
        spread_points=float(cost["spread_points"]),
        params=params,
        initial_balance=initial_balance,
        lot=lot,
        risk_sizing=True,
        risk_per_trade_pct=2.0,
        use_atr=bool(int(cost.get("use_atr", 0))),
        atr_period=14,
        atr_sl_mult=float(cost["atr_sl_mult"]),
        atr_tp_mult=float(cost["atr_tp_mult"]),
        atr_min_points=400.0 if "BTC" in symbol else 0.0,
        commission_round_trip_per_lot=float(cost["commission_rt_per_lot"]),
        slippage_points=float(cost["slippage_points"]),
        entry_allowed=mask,
        session_start=start,
        session_end=end,
        entry_mode_strict=bool(int(cost.get("entry_mode_strict", 1))),
    )

    pnls = [float(t.pnl_money or 0.0) for t in trades]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    pf = (gross_win / gross_loss) if gross_loss > 1e-9 else (999.0 if gross_win > 0 else 0.0)
    in_win = sum(
        1
        for i, r in enumerate(m15)
        if start <= _local_from_broker_ts(int(r["time"])) <= end and mask[i]
    )
    enable = bool(rep.trades >= 5 and pf >= 1.0 and float(rep.max_drawdown_money) > -40.0)
    return {
        "symbol": symbol,
        "sessions": sessions_str,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "bars_in_window": in_win,
        "trades": int(rep.trades),
        "wins": int(rep.wins),
        "losses": int(rep.losses),
        "winrate": float(rep.winrate),
        "pnl_money": float(rep.pnl_money),
        "max_drawdown_money": float(rep.max_drawdown_money),
        "profit_factor": round(pf, 3),
        "final_balance": float(rep.final_balance),
        "cost": cost,
        "enable_recommended": enable,
        "gate_reason": (
            "ok"
            if enable
            else (
                "too_few_trades"
                if rep.trades < 5
                else ("pf_below_1" if pf < 1.0 else "drawdown_too_large")
            )
        ),
    }


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="Session-window replay (USDJPY/BTCUSD).")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="session_replay_results.jsonl")
    ap.add_argument("--balance", type=float, default=100.0)
    args = ap.parse_args()

    cfg = Config()
    if not connect_mt5(cfg):
        print("MT5 connect failed", file=sys.stderr)
        return 1

    end = datetime.now()
    start = end - timedelta(days=int(args.days))
    rows = []
    for sym, sessions in WINDOWS.items():
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"MISSING {sym}")
            continue
        if not info.visible:
            mt5.symbol_select(sym, True)
        print(f"\n=== {sym} sessions={sessions} ===")
        try:
            row = run_session_filtered_backtest(
                symbol=sym,
                start=start,
                end=end,
                sessions_str=sessions,
                cost=COST[sym],
                initial_balance=float(args.balance),
            )
        except Exception as e:
            print(f"FAIL {sym}: {e}")
            row = {"symbol": sym, "error": str(e), "enable_recommended": False, "gate_reason": "error"}
        rows.append(row)
        print(json.dumps({k: row[k] for k in row if k != "cost"}, ensure_ascii=False, indent=2))

    with open(args.out, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(rows)} rows -> {args.out}")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
