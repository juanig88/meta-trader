"""
Backtest determinista (bar-by-bar) reutilizando la estrategia pura en `strategy_core.py`.

Notas:
- La simulación usa OHLC M15 con spread fijo en points (configurable).
- Ejecución: abre 1 trade a la vez por símbolo, entry en la apertura de la siguiente vela.
- Salida: SL/TP por points, calcula métricas básicas y equity/drawdown.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Literal, Any, Union, Tuple

import MetaTrader5 as mt5

from bot_mt5 import Config, _sl_price_to_lock_profit_usd, connect_mt5, resolve_symbol_config_with_overlay, _env_bool
from indicators import atr as atr_series, ema as ema_series_fn, adx_wilder
from strategy_core import (
    StrategyParams,
    Features,
    decide,
    Trend,
    signal_m15_relaxed,
    signal_m15_strong_breakout,
    trend_h4_from_closes,
    trend_h4_from_highs,
    symbol_uses_crypto_relaxed_m15,
    price_slope_m15,
)
try:
    from backtest_extended import (
        benchmark_buy_hold_return,
        bootstrap_trade_pnl_stats,
        default_benchmark_ticker,
        extended_trade_block,
        monte_carlo_trade_order_drawdown,
        pnls_and_holding_bars,
        walk_forward_trade_splits,
    )
except ImportError:  # pragma: no cover
    benchmark_buy_hold_return = None
    bootstrap_trade_pnl_stats = None
    default_benchmark_ticker = None
    extended_trade_block = None
    monte_carlo_trade_order_drawdown = None
    pnls_and_holding_bars = None
    walk_forward_trade_splits = None


Signal = Literal["BUY", "SELL", "NONE"]


@dataclass
class Trade:
    side: Signal
    entry_idx: int
    entry_price: float
    sl: float
    tp: float
    lot: float
    sl_initial: float = 0.0
    peak_mfe_money: float = 0.0
    exit_idx: Optional[int] = None
    exit_price: Optional[float] = None
    pnl_points: Optional[float] = None
    pnl_money: Optional[float] = None


@dataclass
class BacktestReport:
    symbol: str
    trades: int
    wins: int
    losses: int
    winrate: float
    pnl_points: float
    max_drawdown_points: float
    pnl_money: float
    max_drawdown_money: float
    initial_balance: float
    final_balance: float
    lot: float  # avg lot (if risk sizing enabled)
    lot_mode: str  # "fixed" | "risk"
    risk_per_trade_pct: float


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


def _parse_symbols(raw: Optional[str]) -> list[str]:
    if raw is None:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_symbol_config(raw: Optional[str]) -> dict[str, dict]:
    if raw is None or raw.strip() == "":
        return {}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, dict] = {}
        for k, v in obj.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k.strip()] = v
        return out
    except Exception:
        return {}


def _sym_get(symbol_cfg: dict[str, dict], symbol: str, key: str, default):
    d = symbol_cfg.get(symbol, {})
    if isinstance(d, dict) and key in d:
        return d[key]
    return default


def _value_per_point_per_lot(symbol: str, point: float) -> Optional[float]:
    """
    Best-effort conversion: points -> deposit currency per 1.0 lot.

    MT5 usually provides tick value/size (profit per tick for 1.0 lot).
    So: value_per_point = tick_value / tick_size * point
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    tick_value = None
    tick_size = None
    # Prefer trade_* fields when available (tend to be more consistent).
    for tv, ts in (
        (getattr(info, "trade_tick_value", None), getattr(info, "trade_tick_size", None)),
        (getattr(info, "tick_value", None), getattr(info, "tick_size", None)),
    ):
        try:
            if tv is None or ts is None:
                continue
            tvf = float(tv)
            tsf = float(ts)
            if tvf > 0 and tsf > 0:
                tick_value = tvf
                tick_size = tsf
                break
        except Exception:
            continue

    if tick_value is None or tick_size is None:
        return None
    if point <= 0:
        return None

    return float(tick_value / tick_size * point)


def _clamp_volume(symbol: str, lot: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return float(max(0.0, lot))
    vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
    vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
    vstep = float(getattr(info, "volume_step", 0.01) or 0.01)
    lot = float(lot)
    if lot <= 0:
        return float(vmin)
    lot = min(max(lot, vmin), vmax)
    if vstep > 0:
        lot = math.floor(lot / vstep) * vstep
    lot = min(max(lot, vmin), vmax)
    return float(round(lot, 8))


def _equity_for_risk_sizing(equity: float, cap: Optional[float]) -> float:
    """If cap is set (e.g. account ceiling), use min(equity, cap) for risk % → lot. Avoids runaway compounding in backtest."""
    if cap is not None and float(cap) > 0 and float(equity) > float(cap):
        return float(cap)
    return float(equity)


def _lot_by_risk_points(
    *,
    symbol: str,
    equity: float,
    risk_pct: float,
    sl_points: float,
    value_per_point_1lot: float,
    base_lot: float,
) -> float:
    if sl_points <= 0 or value_per_point_1lot <= 0:
        return _clamp_volume(symbol, base_lot)
    risk_amount = float(equity) * (max(0.0, float(risk_pct)) / 100.0)
    loss_1lot = float(sl_points) * float(value_per_point_1lot)
    if risk_amount <= 0 or loss_1lot <= 0:
        return _clamp_volume(symbol, base_lot)
    lot = risk_amount / loss_1lot
    return _clamp_volume(symbol, lot)


def _trend_h4_series_for_m15(*, m15_times: list[int], h4_rates, params: StrategyParams) -> list[Trend]:
    """
    Tendencia H4 por índice M15 (O(len(m15)+len(h4))).
    `trend_h4_from_*` solo usa las últimas 3 velas H4 incluidas; guardamos solo las últimas 10.
    """
    h4_times = [int(r["time"]) for r in h4_rates]
    h4_highs = [float(r["high"]) for r in h4_rates]
    h4_closes = [float(r["close"]) for r in h4_rates]
    out: list[Trend] = ["FLAT"] * len(m15_times)
    j = 0
    last_highs: list[float] = []
    last_closes: list[float] = []
    for i, t in enumerate(m15_times):
        while j < len(h4_times) and h4_times[j] <= t:
            last_highs.append(h4_highs[j])
            last_closes.append(h4_closes[j])
            if len(last_highs) > 10:
                last_highs = last_highs[-10:]
            if len(last_closes) > 10:
                last_closes = last_closes[-10:]
            j += 1
        if getattr(params, "trend_mode", "closes") == "highs":
            out[i] = trend_h4_from_highs(last_highs)
        else:
            out[i] = trend_h4_from_closes(last_closes)
    return out


def run_backtest_on_rates(
    *,
    symbol: str,
    m15: Any,
    h4: Any,
    sl_points: float,
    tp_points: float,
    spread_points: float,
    params: StrategyParams,
    initial_balance: float = 100.0,
    lot: float = 0.01,
    risk_sizing: bool = False,
    risk_per_trade_pct: float = 1.0,
    mode: str = "default",
    atr_period: int = 14,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    atr_lookback: int = 20,
    atr_expand_mult: float = 1.10,
    atr_min_points: float = 0.0,
    open_sl_at_entry: bool = False,
    profit_lock_enabled: bool = False,
    profit_lock_step_money: float = 1.0,
    profit_lock_secure_money: float = 1.0,
    max_equity_for_risk_sizing: Optional[float] = None,
    commission_round_trip_per_lot: float = 0.0,
    slippage_points: float = 0.0,
    return_trades: bool = False,
) -> Union[BacktestReport, Tuple[BacktestReport, list[Trade]]]:
    if m15 is None or len(m15) < max(params.ema_slow, 2 * params.adx_period + 5):
        raise RuntimeError("Not enough M15 data for backtest.")
    if h4 is None or len(h4) < 5:
        raise RuntimeError("Not enough H4 data for backtest.")

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
    sym_digits = int(info.digits) if info else 5

    vpp = _value_per_point_per_lot(symbol, point)  # deposit currency / point / 1.0 lot
    if vpp is None:
        # Fallback: keep money metrics consistent but explicitly "points-based" (1 point == 1 currency unit per 1 lot).
        vpp = 1.0

    # Realistic accounting:
    # - balance_* updates only when trades close (realized PnL)
    # - equity_for_dd includes unrealized PnL (conservative: worst excursion inside next bar)
    balance_pts = 0.0
    peak_pts = 0.0
    max_dd_pts = 0.0

    balance_money = float(initial_balance)
    peak_money = float(initial_balance)
    max_dd_money = 0.0  # negative

    trades: list[Trade] = []
    pos: Optional[Trade] = None

    # Primer índice con indicadores razonablemente estables (sin piso fijo 210: permite EMAs rápidos en crypto).
    start_i = max(int(params.ema_slow) + 5, 2 * int(params.adx_period) + 2, 50)

    for i in range(start_i, len(m15) - 2):
        # Manage open position using the NEXT bar's high/low (simplified discrete simulation)
        if pos is not None:
            hi = high_m15[i + 1]
            lo = low_m15[i + 1]
            if pos.side == "BUY":
                mfe_bar = max(0.0, (hi - pos.entry_price) / point * float(vpp) * float(pos.lot))
            else:
                mfe_bar = max(0.0, (pos.entry_price - lo) / point * float(vpp) * float(pos.lot))
            pos.peak_mfe_money = max(float(pos.peak_mfe_money), mfe_bar)

            eff_sl = float(pos.sl_initial)
            st = float(profit_lock_step_money)
            sc = float(profit_lock_secure_money)
            if profit_lock_enabled and st > 0 and sc > 0:
                n = int(pos.peak_mfe_money // st)
                lock_usd = float(n) * sc
                if lock_usd > 0:
                    lock_sl = _sl_price_to_lock_profit_usd(
                        symbol, pos.side, pos.entry_price, pos.lot, lock_usd, digits=sym_digits
                    )
                    if lock_sl is not None:
                        if pos.side == "BUY":
                            eff_sl = max(eff_sl, float(lock_sl))
                        else:
                            eff_sl = min(eff_sl, float(lock_sl))

            if pos.side == "BUY":
                sl_hit = lo <= eff_sl
                tp_hit = hi >= pos.tp
                if sl_hit or tp_hit:
                    # if both in same candle, assume worst case (SL first) for conservative testing
                    exit_price = eff_sl if sl_hit else pos.tp
                    # slippage: adverse on exits (BUY sells -> worse = lower price)
                    if slippage_points > 0:
                        exit_price = float(exit_price) - (float(slippage_points) * float(point))
                    pos.exit_idx = i + 1
                    pos.exit_price = exit_price
                    pos.pnl_points = (exit_price - pos.entry_price) / point
                    gross_money = float(pos.pnl_points) * float(vpp) * float(pos.lot)
                    comm = float(commission_round_trip_per_lot) * float(pos.lot)
                    pos.pnl_money = gross_money - comm
                    balance_pts += float(pos.pnl_points)
                    balance_money += float(pos.pnl_money)
                    trades.append(pos)
                    pos = None
            else:
                sl_hit = hi >= eff_sl
                tp_hit = lo <= pos.tp
                if sl_hit or tp_hit:
                    exit_price = eff_sl if sl_hit else pos.tp
                    # slippage: adverse on exits (SELL buys back -> worse = higher price)
                    if slippage_points > 0:
                        exit_price = float(exit_price) + (float(slippage_points) * float(point))
                    pos.exit_idx = i + 1
                    pos.exit_price = exit_price
                    pos.pnl_points = (pos.entry_price - exit_price) / point
                    gross_money = float(pos.pnl_points) * float(vpp) * float(pos.lot)
                    comm = float(commission_round_trip_per_lot) * float(pos.lot)
                    pos.pnl_money = gross_money - comm
                    balance_pts += float(pos.pnl_points)
                    balance_money += float(pos.pnl_money)
                    trades.append(pos)
                    pos = None

        # Drawdown on equity (includes unrealized PnL if a position is open).
        eq_pts_for_dd = float(balance_pts)
        eq_money_for_dd = float(balance_money)
        if pos is not None:
            # Conservative (worst) unrealized inside the NEXT bar.
            if pos.side == "BUY":
                unreal_pts = (low_m15[i + 1] - float(pos.entry_price)) / float(point)
            else:
                unreal_pts = (float(pos.entry_price) - high_m15[i + 1]) / float(point)
            eq_pts_for_dd += float(unreal_pts)
            eq_money_for_dd += float(unreal_pts) * float(vpp) * float(pos.lot)

        if eq_pts_for_dd < peak_pts:
            max_dd_pts = min(max_dd_pts, eq_pts_for_dd - peak_pts)
        peak_pts = max(peak_pts, eq_pts_for_dd)

        if eq_money_for_dd < peak_money:
            max_dd_money = min(max_dd_money, eq_money_for_dd - peak_money)
        peak_money = max(peak_money, eq_money_for_dd)

        if pos is not None:
            continue

        # Build features using data up to index i (closed candles)
        trend_h4 = trend_h4_series[i]

        # Señales M15 solo usan la vela cerrada actual y la anterior → tail O(1), no slice 0..i.
        _lo = max(0, i - 1)
        _oh = open_m15[_lo : i + 1]
        _hh = high_m15[_lo : i + 1]
        _lh = low_m15[_lo : i + 1]
        _ch = close_m15[_lo : i + 1]

        entry_mode = getattr(params, "entry_mode", "hybrid")
        if entry_mode == "strict":
            sig = signal_m15_strong_breakout(
                open_m15=_oh,
                high_m15=_hh,
                low_m15=_lh,
                close_m15=_ch,
            )
        elif entry_mode == "relaxed":
            sig = signal_m15_relaxed(
                open_m15=_oh,
                high_m15=_hh,
                low_m15=_lh,
                close_m15=_ch,
                is_crypto=symbol_uses_crypto_relaxed_m15(symbol),
            )
        else:  # hybrid (strict for FX/metals, relaxed for major crypto symbols)
            if symbol_uses_crypto_relaxed_m15(symbol):
                sig = signal_m15_relaxed(
                    open_m15=_oh,
                    high_m15=_hh,
                    low_m15=_lh,
                    close_m15=_ch,
                    is_crypto=True,
                )
            else:
                sig = signal_m15_strong_breakout(
                    open_m15=_oh,
                    high_m15=_hh,
                    low_m15=_lh,
                    close_m15=_ch,
                )

        ef = ema_fast_arr[i] if i < len(ema_fast_arr) else None
        es = ema_slow_arr[i] if i < len(ema_slow_arr) else None
        p = close_m15[i]
        if ef is None or es is None:
            ema_dir: Trend = "FLAT"
        elif float(ef) > float(es) and float(p) > float(ef):
            ema_dir = "UP"
        elif float(ef) < float(es) and float(p) < float(ef):
            ema_dir = "DOWN"
        else:
            ema_dir = "FLAT"

        adx_v = adx_arr[i] if i < len(adx_arr) else None
        adx = float(adx_v) if adx_v is not None else None

        features = Features(
            trend_h4=trend_h4,
            signal_m15=sig,
            ema_dir=ema_dir,
            ema_fast=ef,
            ema_slow=es,
            adx=adx,
            price_slope_m15=price_slope_m15([float(close_m15[j]) for j in range(i + 1)], n=params.price_slope_n),
        )
        decision = decide(params, features, symbol=symbol)
        if decision == "NONE":
            continue

        # Optional "XAU ATR" gate + dynamic SL/TP sizing
        sl_points_use = float(sl_points)
        tp_points_use = float(tp_points)
        if mode.lower() in ("xau_atr", "atr"):
            a_now = atr_vals[i]
            a_prev = atr_vals[i - int(atr_lookback)] if i - int(atr_lookback) >= 0 else None
            if a_now is None or a_prev is None or float(a_prev) <= 0:
                continue
            # expansion filter: trade only if volatility is expanding
            if float(a_now) < float(a_prev) * float(atr_expand_mult):
                continue

            atr_points = float(a_now) / float(point) if point > 0 else 0.0
            sl_points_use = max(float(sl_points_use), float(atr_points) * float(atr_sl_mult), float(atr_min_points))
            tp_points_use = max(float(tp_points_use), float(atr_points) * float(atr_tp_mult), float(atr_min_points))

        # Entry at next bar open, applying spread:
        # BUY pays ask = open + spread/2; SELL receives bid = open - spread/2
        entry_open = open_m15[i + 1]
        half_spread = float(spread_points) / 2.0
        spread_px = half_spread * point

        if decision == "BUY":
            entry = entry_open + spread_px
            # slippage: adverse on entries (BUY pays more)
            if slippage_points > 0:
                entry = float(entry) + (float(slippage_points) * float(point))
            sl = entry - (sl_points_use * point)
            tp = entry + (tp_points_use * point)
            if open_sl_at_entry:
                sl = entry
            use_lot = (
                _lot_by_risk_points(
                    symbol=symbol,
                    equity=_equity_for_risk_sizing(balance_money, max_equity_for_risk_sizing),
                    risk_pct=float(risk_per_trade_pct),
                    sl_points=float(sl_points_use),
                    value_per_point_1lot=float(vpp),
                    base_lot=float(lot),
                )
                if risk_sizing
                else _clamp_volume(symbol, float(lot))
            )
            pos = Trade(
                side="BUY",
                entry_idx=i + 1,
                entry_price=entry,
                sl=sl,
                tp=tp,
                lot=float(use_lot),
                sl_initial=float(sl),
                peak_mfe_money=0.0,
            )
        else:
            entry = entry_open - spread_px
            # slippage: adverse on entries (SELL receives less)
            if slippage_points > 0:
                entry = float(entry) - (float(slippage_points) * float(point))
            sl = entry + (sl_points_use * point)
            tp = entry - (tp_points_use * point)
            if open_sl_at_entry:
                sl = entry
            use_lot = (
                _lot_by_risk_points(
                    symbol=symbol,
                    equity=_equity_for_risk_sizing(balance_money, max_equity_for_risk_sizing),
                    risk_pct=float(risk_per_trade_pct),
                    sl_points=float(sl_points_use),
                    value_per_point_1lot=float(vpp),
                    base_lot=float(lot),
                )
                if risk_sizing
                else _clamp_volume(symbol, float(lot))
            )
            pos = Trade(
                side="SELL",
                entry_idx=i + 1,
                entry_price=entry,
                sl=sl,
                tp=tp,
                lot=float(use_lot),
                sl_initial=float(sl),
                peak_mfe_money=0.0,
            )

    # Close any open pos at last close (mark-to-market)
    if pos is not None:
        last = close_m15[-1]
        if pos.side == "BUY":
            pos.exit_price = last
            pos.exit_idx = len(m15) - 1
            pos.pnl_points = (last - pos.entry_price) / point
        else:
            pos.exit_price = last
            pos.exit_idx = len(m15) - 1
            pos.pnl_points = (pos.entry_price - last) / point
        gross_money = float(pos.pnl_points or 0.0) * float(vpp) * float(pos.lot)
        comm = float(commission_round_trip_per_lot) * float(pos.lot)
        pos.pnl_money = gross_money - comm
        balance_pts += float(pos.pnl_points or 0.0)
        balance_money += float(pos.pnl_money or 0.0)
        trades.append(pos)

    pnl = sum(float(t.pnl_points or 0.0) for t in trades)
    pnl_money = sum(float(t.pnl_money or 0.0) for t in trades)
    lot_avg = sum(float(t.lot) for t in trades) / len(trades) if trades else float(_clamp_volume(symbol, float(lot)))
    wins = sum(1 for t in trades if (t.pnl_points or 0.0) > 0)
    losses = sum(1 for t in trades if (t.pnl_points or 0.0) <= 0)
    n = len(trades)
    winrate = (wins / n * 100.0) if n else 0.0

    report = BacktestReport(
        symbol=symbol,
        trades=n,
        wins=wins,
        losses=losses,
        winrate=winrate,
        pnl_points=float(pnl),
        max_drawdown_points=float(max_dd_pts),
        pnl_money=float(pnl_money),
        max_drawdown_money=float(max_dd_money),
        initial_balance=float(initial_balance),
        final_balance=float(initial_balance) + float(pnl_money),
        lot=float(lot_avg),
        lot_mode="risk" if risk_sizing else "fixed",
        risk_per_trade_pct=float(risk_per_trade_pct) if risk_sizing else 0.0,
    )
    if return_trades:
        return report, trades
    return report


def run_backtest(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    sl_points: float,
    tp_points: float,
    spread_points: float,
    params: StrategyParams,
    initial_balance: float = 100.0,
    lot: float = 0.01,
    risk_sizing: bool = False,
    risk_per_trade_pct: float = 1.0,
    mode: str = "default",
    atr_period: int = 14,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    atr_lookback: int = 20,
    atr_expand_mult: float = 1.10,
    atr_min_points: float = 0.0,
    open_sl_at_entry: bool = False,
    profit_lock_enabled: bool = False,
    profit_lock_step_money: float = 1.0,
    profit_lock_secure_money: float = 1.0,
    max_equity_for_risk_sizing: Optional[float] = None,
    commission_round_trip_per_lot: float = 0.0,
    slippage_points: float = 0.0,
    return_trades: bool = False,
) -> Union[BacktestReport, Tuple[BacktestReport, list[Trade]]]:
    m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
    h4 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, start - timedelta(days=10), end)
    return run_backtest_on_rates(
        symbol=symbol,
        m15=m15,
        h4=h4,
        sl_points=sl_points,
        tp_points=tp_points,
        spread_points=spread_points,
        params=params,
        initial_balance=initial_balance,
        lot=lot,
        risk_sizing=risk_sizing,
        risk_per_trade_pct=risk_per_trade_pct,
        mode=mode,
        atr_period=atr_period,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult,
        atr_lookback=atr_lookback,
        atr_expand_mult=atr_expand_mult,
        atr_min_points=atr_min_points,
        open_sl_at_entry=open_sl_at_entry,
        profit_lock_enabled=profit_lock_enabled,
        profit_lock_step_money=profit_lock_step_money,
        profit_lock_secure_money=profit_lock_secure_money,
        max_equity_for_risk_sizing=max_equity_for_risk_sizing,
        commission_round_trip_per_lot=commission_round_trip_per_lot,
        slippage_points=slippage_points,
        return_trades=return_trades,
    )


def _parse_dt(s: str) -> datetime:
    """
    Accepts: YYYY-MM-DD or ISO datetime.
    """
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d")
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid date/datetime: {s}") from e


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Deterministic MT5 backtest (M15) with SL/TP in points.")
    ap.add_argument("--symbols", help="Comma-separated symbols. Default: BOT_SYMBOLS or EURUSD.")
    ap.add_argument("--days", type=int, default=60, help="Lookback days (ignored if --start/--end provided).")
    ap.add_argument("--start", type=_parse_dt, help="Start date/time (YYYY-MM-DD or ISO).")
    ap.add_argument("--end", type=_parse_dt, help="End date/time (YYYY-MM-DD or ISO). Default: now.")
    ap.add_argument("--sl", type=float, help="Stop loss in points. Default: from BOT_SYMBOL_CONFIG or 200.")
    ap.add_argument("--tp", type=float, help="Take profit in points. Default: from BOT_SYMBOL_CONFIG or 250.")
    ap.add_argument("--spread", type=float, help="Spread in points. Default: from BOT_SYMBOL_CONFIG or 20.")
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--adx-period", type=int, default=14)
    ap.add_argument("--adx-min", type=float, default=17.0)
    ap.add_argument("--entry-mode", default="hybrid", choices=["strict", "relaxed", "hybrid"])
    ap.add_argument("--trend-mode", default="closes", choices=["highs", "closes"])
    ap.add_argument("--allow-flat-h4", type=int, default=1, help="1=allow H4 FLAT (but not against H4). 0=strict H4 alignment.")
    ap.add_argument("--mode", default="default", help="default | xau_atr")
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--atr-sl-mult", type=float, default=2.0)
    ap.add_argument("--atr-tp-mult", type=float, default=3.0)
    ap.add_argument("--atr-lookback", type=int, default=20)
    ap.add_argument("--atr-expand-mult", type=float, default=1.10)
    ap.add_argument("--atr-min-points", type=float, default=0.0)
    ap.add_argument("--initial-balance", type=float, default=100.0, help="Starting balance in deposit currency.")
    ap.add_argument("--lot", type=float, help="Base/fixed lot size. Default: BOT_LOT or 0.01.")
    ap.add_argument("--risk-sizing", type=int, default=1, help="1=use risk sizing (live default). 0=fixed lot.")
    ap.add_argument(
        "--risk-pct",
        type=float,
        default=float(_env("BOT_RISK_PER_TRADE_PCT", "5") or "5"),
        help="Risk per trade %% when risk-sizing=1 (default from BOT_RISK_PER_TRADE_PCT or 5).",
    )
    ap.add_argument("--out", default="backtest_results.jsonl", help="Output file (JSONL).")
    ap.add_argument(
        "--commission-rt",
        type=float,
        default=float(_env("BOT_BACKTEST_COMMISSION_RT_PER_LOT", "0") or "0"),
        help="Commission round-trip (entry+exit) per 1.0 lot, in deposit currency (default BOT_BACKTEST_COMMISSION_RT_PER_LOT or 0).",
    )
    ap.add_argument(
        "--slippage",
        type=float,
        default=float(_env("BOT_BACKTEST_SLIPPAGE_POINTS", "0") or "0"),
        help="Adverse slippage in points applied at entry and exit (default BOT_BACKTEST_SLIPPAGE_POINTS or 0).",
    )
    ap.add_argument(
        "--open-sl-at-entry",
        type=int,
        default=0,
        help="1=set initial SL to entry (BE-at-open). Lot sizing still uses --sl / symbol config for distance.",
    )
    ap.add_argument(
        "--profit-lock",
        type=int,
        default=0,
        help="1=ratchet SL from peak MFE: each +step USD of favorable excursion locks +secure USD (simulated on each M15 bar).",
    )
    ap.add_argument("--profit-lock-step", type=float, default=1.0, help="USD step for profit-lock ladder (default 1).")
    ap.add_argument(
        "--profit-lock-secure",
        type=float,
        default=1.0,
        help="USD secured per full step of peak MFE (default 1).",
    )
    ap.add_argument("--label", default="", help="Optional tag stored in the JSONL line (e.g. 365d).")
    ap.add_argument(
        "--max-equity-risk",
        type=float,
        default=None,
        help="Cap equity for risk%% lot sizing (fixed account ceiling; omit = compounding on full balance). Also: env BOT_BACKTEST_MAX_EQUITY_RISK.",
    )
    ap.add_argument(
        "--extended-stats",
        type=int,
        default=0,
        help="1=append extended metrics + stat_validation (bootstrap, MC trade-order DD) to JSONL.",
    )
    ap.add_argument("--bootstrap-n", type=int, default=800, help="Bootstrap resamples for PnL / Sharpe intervals (0=skip).")
    ap.add_argument(
        "--mc-order-sims",
        type=int,
        default=800,
        help="Permutations of trade order for drawdown distribution (0=skip). Approx if lots vary.",
    )
    ap.add_argument(
        "--walk-forward-splits",
        type=int,
        default=0,
        help="Split closed trades into K chronological chunks in JSONL (0=off, min 2).",
    )
    ap.add_argument("--stat-seed", type=int, default=None, help="Seed for bootstrap / MC (omit = nondeterministic).")
    ap.add_argument(
        "--trading-days-year",
        type=float,
        default=252.0,
        help="Annualization: 252 ~ FX, 365 ~ crypto (Sharpe scaling only).",
    )
    ap.add_argument(
        "--benchmark",
        default="",
        help="Buy & hold via yfinance: empty=off, auto=map XAU/BTC/ETH, else ticker (e.g. GC=F).",
    )
    args = ap.parse_args()

    if bool(int(args.extended_stats)) and extended_trade_block is None:
        print(
            "[error] Falta backtest_extended.py (requerido para --extended-stats 1).",
            file=sys.stderr,
        )
        return 2
    if ((args.benchmark or "").strip()) and benchmark_buy_hold_return is None:
        print(
            "[error] Falta backtest_extended.py (requerido para --benchmark).",
            file=sys.stderr,
        )
        return 2

    max_eq_risk: Optional[float] = args.max_equity_risk
    if max_eq_risk is None:
        raw_mx = _env("BOT_BACKTEST_MAX_EQUITY_RISK")
        if raw_mx is not None and str(raw_mx).strip() != "":
            try:
                max_eq_risk = float(raw_mx)
            except ValueError:
                max_eq_risk = None

    sym_raw = args.symbols or _env("BOT_SYMBOLS") or "EURUSD"
    symbols = _parse_symbols(sym_raw) or ["EURUSD"]

    end = args.end or datetime.now()
    if args.start is not None:
        start = args.start
    else:
        start = end - timedelta(days=int(args.days))

    symbol_cfg, _ = resolve_symbol_config_with_overlay()

    cfg = Config(symbols=tuple(symbols))
    if not connect_mt5(cfg):
        return 2

    params = StrategyParams(
        ema_fast=int(args.ema_fast),
        ema_slow=int(args.ema_slow),
        adx_period=int(args.adx_period),
        adx_min=float(args.adx_min),
        entry_mode=str(args.entry_mode),
        trend_mode=str(args.trend_mode),
        allow_flat_h4=bool(int(args.allow_flat_h4)),
        crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
        price_slope_filter_enabled=_env_bool("BOT_PRICE_SLOPE_FILTER_ENABLED", False),
        price_slope_n=max(2, int(float(os.getenv("BOT_PRICE_SLOPE_N") or "6"))),
    )

    want_ext = bool(int(args.extended_stats))
    results: list[BacktestReport] = []
    for sym in symbols:
        sl_default = float(_env("BOT_SL_POINTS", "80") or "80")
        tp_default = float(_env("BOT_TP_POINTS", "160") or "160")
        sl = float(args.sl) if args.sl is not None else float(_sym_get(symbol_cfg, sym, "sl_points", sl_default))
        tp = float(args.tp) if args.tp is not None else float(_sym_get(symbol_cfg, sym, "tp_points", tp_default))
        spread = float(args.spread) if args.spread is not None else float(_sym_get(symbol_cfg, sym, "spread_points", 20))
        lot = float(args.lot) if args.lot is not None else float(_env("BOT_LOT", "0.01") or "0.01")

        trades: list[Trade] = []
        if want_ext:
            rep, trades = run_backtest(
                symbol=sym,
                start=start,
                end=end,
                sl_points=sl,
                tp_points=tp,
                spread_points=spread,
                params=params,
                initial_balance=float(args.initial_balance),
                lot=float(lot),
                risk_sizing=bool(int(args.risk_sizing)),
                risk_per_trade_pct=float(args.risk_pct),
                mode=str(args.mode),
                atr_period=int(args.atr_period),
                atr_sl_mult=float(args.atr_sl_mult),
                atr_tp_mult=float(args.atr_tp_mult),
                atr_lookback=int(args.atr_lookback),
                atr_expand_mult=float(args.atr_expand_mult),
                atr_min_points=float(args.atr_min_points),
                open_sl_at_entry=bool(int(args.open_sl_at_entry)),
                profit_lock_enabled=bool(int(args.profit_lock)),
                profit_lock_step_money=float(args.profit_lock_step),
                profit_lock_secure_money=float(args.profit_lock_secure),
                max_equity_for_risk_sizing=max_eq_risk,
                commission_round_trip_per_lot=float(args.commission_rt or 0.0),
                slippage_points=float(args.slippage or 0.0),
                return_trades=True,
            )
        else:
            rep = run_backtest(
                symbol=sym,
                start=start,
                end=end,
                sl_points=sl,
                tp_points=tp,
                spread_points=spread,
                params=params,
                initial_balance=float(args.initial_balance),
                lot=float(lot),
                risk_sizing=bool(int(args.risk_sizing)),
                risk_per_trade_pct=float(args.risk_pct),
                mode=str(args.mode),
                atr_period=int(args.atr_period),
                atr_sl_mult=float(args.atr_sl_mult),
                atr_tp_mult=float(args.atr_tp_mult),
                atr_lookback=int(args.atr_lookback),
                atr_expand_mult=float(args.atr_expand_mult),
                atr_min_points=float(args.atr_min_points),
                open_sl_at_entry=bool(int(args.open_sl_at_entry)),
                profit_lock_enabled=bool(int(args.profit_lock)),
                profit_lock_step_money=float(args.profit_lock_step),
                profit_lock_secure_money=float(args.profit_lock_secure),
                max_equity_for_risk_sizing=max_eq_risk,
                commission_round_trip_per_lot=float(args.commission_rt or 0.0),
                slippage_points=float(args.slippage or 0.0),
                return_trades=False,
            )
        results.append(rep)
        print(rep)

        row: dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "label": (args.label or None),
            "symbol": rep.symbol,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "sl_points": sl,
            "tp_points": tp,
            "spread_points": spread,
            "open_sl_at_entry": bool(int(args.open_sl_at_entry)),
            "profit_lock_enabled": bool(int(args.profit_lock)),
            "profit_lock_step_money": float(args.profit_lock_step),
            "profit_lock_secure_money": float(args.profit_lock_secure),
            "max_equity_for_risk_sizing": max_eq_risk,
            "commission_round_trip_per_lot": float(args.commission_rt or 0.0),
            "slippage_points": float(args.slippage or 0.0),
            "ema_fast": params.ema_fast,
            "ema_slow": params.ema_slow,
            "adx_period": params.adx_period,
            "adx_min": params.adx_min,
            "trades": rep.trades,
            "wins": rep.wins,
            "losses": rep.losses,
            "winrate": rep.winrate,
            "pnl_points": rep.pnl_points,
            "max_drawdown_points": rep.max_drawdown_points,
            "initial_balance": rep.initial_balance,
            "final_balance": rep.final_balance,
            "lot": rep.lot,
            "lot_mode": rep.lot_mode,
            "risk_per_trade_pct": rep.risk_per_trade_pct,
            "pnl_money": rep.pnl_money,
            "max_drawdown_money": rep.max_drawdown_money,
        }

        if want_ext and trades:
            row["extended_stats"] = extended_trade_block(
                trades,
                initial_balance=float(args.initial_balance),
                trading_days_per_year=float(args.trading_days_year),
            )
            pnls, _ = pnls_and_holding_bars(trades)
            val: dict[str, Any] = {}
            if int(args.bootstrap_n) > 0:
                val["bootstrap"] = bootstrap_trade_pnl_stats(
                    pnls,
                    initial_balance=float(args.initial_balance),
                    n_boot=int(args.bootstrap_n),
                    seed=args.stat_seed,
                )
            if int(args.mc_order_sims) > 0:
                val["mc_order_drawdown"] = monte_carlo_trade_order_drawdown(
                    pnls,
                    initial_balance=float(args.initial_balance),
                    n_sims=int(args.mc_order_sims),
                    seed=args.stat_seed,
                )
            if int(args.walk_forward_splits) >= 2:
                val["walk_forward_trade_splits"] = walk_forward_trade_splits(
                    pnls, n_splits=int(args.walk_forward_splits)
                )
            if val:
                row["stat_validation"] = val

        bench_raw = (args.benchmark or "").strip()
        if bench_raw:
            tick: Optional[str] = None
            if bench_raw.lower() == "auto":
                tick = default_benchmark_ticker(sym)
            else:
                tick = bench_raw
            if tick:
                bh = dict(benchmark_buy_hold_return(tick, start=start, end=end))
                init_b = float(rep.initial_balance)
                if init_b > 0:
                    bh["strategy_total_return"] = round((float(rep.final_balance) / init_b) - 1.0, 6)
                if bh.get("ok") and bh.get("strategy_total_return") is not None:
                    bh["excess_vs_benchmark"] = round(
                        float(bh["strategy_total_return"]) - float(bh.get("total_return", 0.0)),
                        6,
                    )
                row["benchmark_buy_hold"] = bh

        with open(args.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

