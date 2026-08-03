from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import MetaTrader5 as mt5

from bot_mt5 import Config, connect_mt5, _parse_portfolio_weights, resolve_symbol_config_with_overlay, _env_bool
from strategy_core import (
    StrategyParams,
    Features,
    decide,
    ema_trend_from_series,
    signal_m15_relaxed,
    signal_m15_strong_breakout,
    trend_h4_from_closes,
    trend_h4_from_highs,
    adx_from_series,
    symbol_uses_crypto_relaxed_m15,
    price_slope_m15,
)
from indicators import ema as ema_series
from indicators import adx_wilder


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


def _parse_weights(raw: Optional[str], symbols: list[str]) -> dict[str, float]:
    if raw is None or raw.strip() == "":
        w = 1.0 / max(1, len(symbols))
        return {s: w for s in symbols}
    out: dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        try:
            out[k] = float(v.strip())
        except ValueError:
            continue
    # normalize and fill missing
    for s in symbols:
        if s not in out:
            out[s] = 0.0
    total = sum(max(0.0, float(v)) for v in out.values())
    if total <= 0:
        w = 1.0 / max(1, len(symbols))
        return {s: w for s in symbols}
    return {s: max(0.0, float(out[s])) / total for s in symbols}


def _coerce_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _slice_h4_up_to(m15_ts: int, h4_rates) -> list[float]:
    highs: list[float] = []
    for r in h4_rates:
        if int(r["time"]) <= int(m15_ts):
            highs.append(float(r["high"]))
        else:
            break
    return highs


def _trend_h4_series_for_m15(*, m15_times: list[int], h4_rates, params: StrategyParams) -> list[str]:
    """
    Precompute H4 trend for each M15 bar timestamp (oldest->newest).
    """
    h4_times = [int(r["time"]) for r in h4_rates]
    h4_highs = [float(r["high"]) for r in h4_rates]
    h4_closes = [float(r["close"]) for r in h4_rates]
    out: list[str] = ["FLAT"] * len(m15_times)
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


def _value_per_point_1lot(symbol: str, point: float) -> Optional[float]:
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    tick_value = None
    tick_size = None
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
    if tick_value is None or tick_size is None or point <= 0:
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


def _lot_by_risk(
    *,
    symbol: str,
    equity: float,
    risk_amount: float,
    sl_points: float,
    vpp_1lot: float,
    base_lot: float,
) -> float:
    if risk_amount <= 0 or sl_points <= 0 or vpp_1lot <= 0:
        return _clamp_volume(symbol, base_lot)
    loss_1lot = float(sl_points) * float(vpp_1lot)
    if loss_1lot <= 0:
        return _clamp_volume(symbol, base_lot)
    lot = float(risk_amount) / loss_1lot
    # avoid opening absurd volume relative to equity in tiny accounts
    if equity > 0:
        lot = min(lot, float(base_lot) * 1000.0)
    return _clamp_volume(symbol, lot)


@dataclass
class Position:
    symbol: str
    side: str  # BUY|SELL
    lot: float
    entry_idx: int
    entry_price: float
    sl: float
    tp: float


@dataclass
class PortfolioReport:
    ts: str
    symbols: list[str]
    weights: dict[str, float]
    start: str
    end: str
    initial_balance: float
    final_balance: float
    pnl_money: float
    max_drawdown_money: float
    trades_total: int
    risk_pct: float
    sl_points: float
    tp_points: float
    spread_points_by_symbol: dict[str, float]
    lot_base: float
    rebalance_days: int
    notes: str
    sl_tp_by_symbol: dict[str, dict[str, float]]


@dataclass(frozen=True)
class MarketCache:
    symbols: list[str]
    start: datetime
    end: datetime
    params: StrategyParams
    m15_by_sym: dict[str, list]
    pre: dict[str, dict]
    point_by_sym: dict[str, float]
    vpp_by_sym: dict[str, float]
    time_to_idx: dict[str, dict[int, int]]
    timeline: list[int]
    start_i_min: int


def build_market_cache(*, symbols: list[str], start: datetime, end: datetime, params: StrategyParams) -> MarketCache:
    m15_by_sym: dict[str, list] = {}
    point_by_sym: dict[str, float] = {}
    vpp_by_sym: dict[str, float] = {}
    pre: dict[str, dict] = {}

    need_len = max(params.ema_slow, 2 * params.adx_period + 5)

    for sym in symbols:
        m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start, end)
        h4 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H4, start - timedelta(days=10), end)
        if m15 is None or h4 is None or len(m15) < need_len or len(h4) < 5:
            raise RuntimeError(f"Not enough data for {sym}.")

        m15 = list(m15)
        h4 = list(h4)

        open_m15 = [float(r["open"]) for r in m15]
        high_m15 = [float(r["high"]) for r in m15]
        low_m15 = [float(r["low"]) for r in m15]
        close_m15 = [float(r["close"]) for r in m15]
        time_m15 = [int(r["time"]) for r in m15]

        info = mt5.symbol_info(sym)
        point = float(info.point) if info and info.point else 0.00001
        vpp = _value_per_point_1lot(sym, point) or 1.0

        ef = ema_series(close_m15, int(params.ema_fast))
        es = ema_series(close_m15, int(params.ema_slow))
        adx_res = adx_wilder(high_m15, low_m15, close_m15, period=int(params.adx_period))
        adx = [float(v) if v is not None else None for v in adx_res.adx]
        trend_h4 = _trend_h4_series_for_m15(m15_times=time_m15, h4_rates=h4, params=params)

        m15_by_sym[sym] = m15
        point_by_sym[sym] = point
        vpp_by_sym[sym] = float(vpp)
        pre[sym] = {
            "open": open_m15,
            "high": high_m15,
            "low": low_m15,
            "close": close_m15,
            "time": time_m15,
            "ema_fast": ef,
            "ema_slow": es,
            "adx": adx,
            "trend_h4": trend_h4,
        }

    times_sets = []
    time_to_idx: dict[str, dict[int, int]] = {}
    for sym in symbols:
        times = pre[sym]["time"]
        tset = set(times)
        times_sets.append(tset)
        time_to_idx[sym] = {t: i for i, t in enumerate(times)}

    common_times = set.intersection(*times_sets) if times_sets else set()
    timeline = sorted(common_times)
    start_i_min = max(int(params.ema_slow), 2 * int(params.adx_period) + 2, 210)

    return MarketCache(
        symbols=list(symbols),
        start=start,
        end=end,
        params=params,
        m15_by_sym=m15_by_sym,
        pre=pre,
        point_by_sym=point_by_sym,
        vpp_by_sym=vpp_by_sym,
        time_to_idx=time_to_idx,
        timeline=timeline,
        start_i_min=int(start_i_min),
    )


def run_portfolio_backtest(
    *,
    symbols: list[str],
    weights: dict[str, float],
    start: datetime,
    end: datetime,
    sl_points: float,
    tp_points: float,
    spread_points_by_symbol: dict[str, float],
    params: StrategyParams,
    initial_balance: float,
    base_lot: float,
    risk_pct: float,
    rebalance_days: int = 0,
    cache: MarketCache | None = None,
    sl_tp_by_symbol: Optional[dict[str, tuple[float, float]]] = None,
) -> PortfolioReport:
    if cache is None:
        cache = build_market_cache(symbols=symbols, start=start, end=end, params=params)

    def _sl_tp(sym: str) -> tuple[float, float]:
        if sl_tp_by_symbol and sym in sl_tp_by_symbol:
            return float(sl_tp_by_symbol[sym][0]), float(sl_tp_by_symbol[sym][1])
        return float(sl_points), float(tp_points)

    m15_by_sym = cache.m15_by_sym
    point_by_sym = cache.point_by_sym
    vpp_by_sym = cache.vpp_by_sym
    pre = cache.pre
    time_to_idx = cache.time_to_idx
    timeline = cache.timeline

    equity = float(initial_balance)
    peak = float(initial_balance)
    max_dd = 0.0

    positions: dict[str, Position] = {}
    trades_total = 0

    # rebalance support based on trailing pnl per symbol (closed trades only)
    pnl_trailing: dict[str, float] = {s: 0.0 for s in symbols}
    last_rebalance_ts = timeline[0] if timeline else 0

    start_i_min = int(cache.start_i_min)

    for t in timeline:
        # periodic rebalance (very simple): shift weights to recent winners
        if rebalance_days and (t - last_rebalance_ts) >= int(rebalance_days * 86400):
            # score: only positive contributors
            pos_scores = {s: max(0.0, float(pnl_trailing.get(s, 0.0))) for s in symbols}
            total = sum(pos_scores.values())
            if total > 0:
                weights = {s: pos_scores[s] / total for s in symbols}
            last_rebalance_ts = t
            pnl_trailing = {s: 0.0 for s in symbols}

        # manage existing positions for each symbol at this time
        for sym in list(positions.keys()):
            idx = time_to_idx[sym].get(t)
            if idx is None:
                continue
            # need next candle hi/lo
            if idx + 1 >= len(m15_by_sym[sym]):
                continue
            if idx < start_i_min:
                continue

            pos = positions[sym]
            hi = float(pre[sym]["high"][idx + 1])
            lo = float(pre[sym]["low"][idx + 1])
            point = point_by_sym[sym]
            vpp = vpp_by_sym[sym]

            if pos.side == "BUY":
                sl_hit = lo <= pos.sl
                tp_hit = hi >= pos.tp
                if sl_hit or tp_hit:
                    exit_price = pos.sl if sl_hit else pos.tp
                    pnl_points = (exit_price - pos.entry_price) / point
                    pnl_money = float(pnl_points) * float(vpp) * float(pos.lot)
                    equity += pnl_money
                    pnl_trailing[sym] += pnl_money
                    trades_total += 1
                    del positions[sym]
            else:
                sl_hit = hi >= pos.sl
                tp_hit = lo <= pos.tp
                if sl_hit or tp_hit:
                    exit_price = pos.sl if sl_hit else pos.tp
                    pnl_points = (pos.entry_price - exit_price) / point
                    pnl_money = float(pnl_points) * float(vpp) * float(pos.lot)
                    equity += pnl_money
                    pnl_trailing[sym] += pnl_money
                    trades_total += 1
                    del positions[sym]

        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

        # entries
        for sym in symbols:
            if sym in positions:
                continue
            idx = time_to_idx[sym].get(t)
            if idx is None:
                continue
            if idx < start_i_min or idx + 2 >= len(m15_by_sym[sym]):
                continue

            # fast features from precomputed series at idx (closed candle)
            # M15 entry signal (strict vs relaxed vs hybrid)
            entry_mode = getattr(params, "entry_mode", "hybrid")
            if entry_mode == "strict":
                o = float(pre[sym]["open"][idx])
                c = float(pre[sym]["close"][idx])
                prev_h = float(pre[sym]["high"][idx - 1])
                prev_l = float(pre[sym]["low"][idx - 1])
                sig = "BUY" if (c > o and c > prev_h) else ("SELL" if (c < o and c < prev_l) else "NONE")
            elif entry_mode == "relaxed":
                sig = signal_m15_relaxed(
                    open_m15=pre[sym]["open"][: idx + 1],
                    high_m15=pre[sym]["high"][: idx + 1],
                    low_m15=pre[sym]["low"][: idx + 1],
                    close_m15=pre[sym]["close"][: idx + 1],
                    is_crypto=symbol_uses_crypto_relaxed_m15(sym),
                )
                o = float(pre[sym]["open"][idx])
                c = float(pre[sym]["close"][idx])
            else:  # hybrid: relaxed for major crypto symbols
                if symbol_uses_crypto_relaxed_m15(sym):
                    sig = signal_m15_relaxed(
                        open_m15=pre[sym]["open"][: idx + 1],
                        high_m15=pre[sym]["high"][: idx + 1],
                        low_m15=pre[sym]["low"][: idx + 1],
                        close_m15=pre[sym]["close"][: idx + 1],
                        is_crypto=True,
                    )
                else:
                    o = float(pre[sym]["open"][idx])
                    c = float(pre[sym]["close"][idx])
                    prev_h = float(pre[sym]["high"][idx - 1])
                    prev_l = float(pre[sym]["low"][idx - 1])
                    sig = "BUY" if (c > o and c > prev_h) else ("SELL" if (c < o and c < prev_l) else "NONE")
                o = float(pre[sym]["open"][idx])
                c = float(pre[sym]["close"][idx])

            ef = pre[sym]["ema_fast"][idx]
            es = pre[sym]["ema_slow"][idx]
            ema_dir = "FLAT"
            if ef is not None and es is not None:
                if float(ef) > float(es) and c > float(ef):
                    ema_dir = "UP"
                elif float(ef) < float(es) and c < float(ef):
                    ema_dir = "DOWN"

            adx = pre[sym]["adx"][idx]
            trend_h4 = pre[sym]["trend_h4"][idx]

            closes_tail = pre[sym]["close"][: idx + 1]

            decision = decide(
                params,
                Features(
                    trend_h4=trend_h4,
                    signal_m15=sig,
                    ema_dir=ema_dir,
                    ema_fast=float(ef) if ef is not None else None,
                    ema_slow=float(es) if es is not None else None,
                    adx=float(adx) if adx is not None else None,
                    price_slope_m15=price_slope_m15([float(x) for x in closes_tail], n=params.price_slope_n),
                ),
                symbol=sym,
            )
            if decision == "NONE":
                continue

            point = point_by_sym[sym]
            vpp = vpp_by_sym[sym]
            half_spread = float(spread_points_by_symbol.get(sym, 0.0)) / 2.0
            spread_px = half_spread * point
            entry_open = float(pre[sym]["open"][idx + 1])

            w = float(weights.get(sym, 0.0))
            risk_amount = float(equity) * (max(0.0, float(risk_pct)) / 100.0) * w
            sl_s, tp_s = _sl_tp(sym)
            lot = _lot_by_risk(symbol=sym, equity=equity, risk_amount=risk_amount, sl_points=sl_s, vpp_1lot=vpp, base_lot=base_lot)

            if decision == "BUY":
                entry = entry_open + spread_px
                sl = entry - (sl_s * point)
                tp = entry + (tp_s * point)
                positions[sym] = Position(symbol=sym, side="BUY", lot=lot, entry_idx=idx + 1, entry_price=entry, sl=sl, tp=tp)
            else:
                entry = entry_open - spread_px
                sl = entry + (sl_s * point)
                tp = entry - (tp_s * point)
                positions[sym] = Position(symbol=sym, side="SELL", lot=lot, entry_idx=idx + 1, entry_price=entry, sl=sl, tp=tp)

    sl_tp_report = {s: {"sl_points": float(_sl_tp(s)[0]), "tp_points": float(_sl_tp(s)[1])} for s in symbols}
    return PortfolioReport(
        ts=datetime.now().isoformat(timespec="seconds"),
        symbols=list(symbols),
        weights={s: float(weights.get(s, 0.0)) for s in symbols},
        start=start.isoformat(timespec="seconds"),
        end=end.isoformat(timespec="seconds"),
        initial_balance=float(initial_balance),
        final_balance=float(equity),
        pnl_money=float(equity - float(initial_balance)),
        max_drawdown_money=float(max_dd),
        trades_total=int(trades_total),
        risk_pct=float(risk_pct),
        sl_points=float(sl_points),
        tp_points=float(tp_points),
        spread_points_by_symbol={s: float(spread_points_by_symbol.get(s, 0.0)) for s in symbols},
        lot_base=float(base_lot),
        rebalance_days=int(rebalance_days),
        notes="Portfolio backtest (single equity) with weighted risk sizing; simplified discrete execution.",
        sl_tp_by_symbol=sl_tp_report,
    )


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Portfolio backtest (single equity) across multiple symbols.")
    ap.add_argument("--symbols", help="Comma-separated symbols. Default: BOT_SYMBOLS.")
    ap.add_argument("--weights", help="Comma weights: GBPUSD=0.4,USDJPY=0.4,NZDJPY=0.2 (optional).")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--sl", type=float, default=80)
    ap.add_argument("--tp", type=float, default=160)
    ap.add_argument("--spreads", help="Comma spreads in points: GBPUSD=6,USDJPY=3,NZDJPY=9 (optional).")
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--adx-period", type=int, default=14)
    ap.add_argument("--adx-min", type=float, default=17.0, help="Align with BOT_ADX_MIN (live default 17).")
    ap.add_argument("--entry-mode", default="hybrid", choices=["strict", "relaxed", "hybrid"])
    ap.add_argument("--trend-mode", default="closes", choices=["highs", "closes"])
    ap.add_argument("--allow-flat-h4", type=int, default=1)
    ap.add_argument("--initial-balance", type=float, default=100.0)
    ap.add_argument(
        "--risk-pct",
        type=float,
        default=None,
        help="Risk per trade as %% of equity (scaled by weights). Default: BOT_RISK_PER_TRADE_PCT or 10.",
    )
    ap.add_argument("--lot", type=float, default=None, help="Base lot (clamp fallback). Default: BOT_LOT or 0.01.")
    ap.add_argument("--rebalance-days", type=int, default=0, help="0=off. Otherwise rebalance weights every N days by trailing pnl.")
    ap.add_argument("--out", default="portfolio_results.jsonl")
    args = ap.parse_args()

    sym_raw = args.symbols or _env("BOT_SYMBOLS") or ""
    symbols = _parse_symbols(sym_raw)
    if not symbols:
        print("No symbols. Set --symbols or BOT_SYMBOLS.")
        return 2

    if args.weights and str(args.weights).strip():
        weights = _parse_weights(args.weights, symbols)
    else:
        weights = _parse_portfolio_weights(_env("BOT_PORTFOLIO_WEIGHTS"), tuple(symbols))

    spread_map: dict[str, float] = {}
    if args.spreads:
        for part in args.spreads.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            try:
                spread_map[k.strip()] = float(v.strip())
            except ValueError:
                continue

    end = datetime.now()
    start = end - timedelta(days=int(args.days))
    base_lot = float(args.lot) if args.lot is not None else float(_env("BOT_LOT", "0.01") or "0.01")

    cfg = Config(symbols=tuple(symbols))
    if not connect_mt5(cfg):
        return 3

    # ensure symbols selected
    for s in symbols:
        mt5.symbol_select(s, True)

    # if spreads not given, use current spread as a screening proxy
    for s in symbols:
        if s in spread_map:
            continue
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        if info is None or tick is None or not info.point:
            spread_map[s] = 20.0
        else:
            spread_map[s] = float((tick.ask - tick.bid) / float(info.point))

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

    risk_pct = float(args.risk_pct) if args.risk_pct is not None else float(_env("BOT_RISK_PER_TRADE_PCT", "10") or "10")

    sym_cfg, _ = resolve_symbol_config_with_overlay()
    sl_tp_by_symbol: dict[str, tuple[float, float]] = {}
    for s in symbols:
        ovr = sym_cfg.get(s)
        ovr = ovr if isinstance(ovr, dict) else {}
        sl_tp_by_symbol[s] = (
            float(ovr.get("sl_points", args.sl)),
            float(ovr.get("tp_points", args.tp)),
        )

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
        risk_pct=risk_pct,
        rebalance_days=int(args.rebalance_days),
        sl_tp_by_symbol=sl_tp_by_symbol,
    )

    mt5.shutdown()

    print(rep)
    with open(args.out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rep.__dict__, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

