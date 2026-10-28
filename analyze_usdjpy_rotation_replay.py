"""Offline replay of the live rotation 4/5 logic on USDJPY.

Read-only research: never sends orders, never calls mt5.shutdown() (the bot
shares the terminal). Uses the live `evaluate_symbol` (5 voters, HTF/pullback/
RSI/Murphy gates, Kelly size) with bars that were closed at decision time, then
simulates the live exit: 0.01 lot, symmetric $0.50 SL/TP, close at 5 minutes.

Sources:
- ``mt5``: XBTFX bars already cached in the terminal. Asking for more than the
  cache (e.g. 100k M1) makes the terminal download history and freezes the live
  bot for minutes, so keep ``--bars`` small.
- ``yf``: Yahoo 1m CSVs (USDJPY=X, BTC-USD) in ``logs/``; M5/M15/H4 are
  resampled from 1m. Spread comes from XBTFX: median per UTC hour from the
  cached USDJPY. M1 bars (server clock = UTC).

Costs: BUY pays the spread on entry, SELL on exit. A second pass floors the
spread at the XBTFX p90.

    py analyze_usdjpy_rotation_replay.py --source yf
    py analyze_usdjpy_rotation_replay.py --source mt5 --bars 5000
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np

from trading.domain.ensemble import SIDE_BUY, SIDE_SELL, ContractSpec, Ohlcv, evaluate_symbol
from trading.domain.rotation_stops import rotation_spread_skip_reason, rotation_stop_distance

SYMBOL = "USDJPY."
PEER = "BTCUSD!"
M1_N, M5_N, M15_N, H4_N = 80, 40, 192, 72
MAX_RISK_USD = 0.50
HOLD_BARS = 5
MAX_SPREAD_FRAC = 0.10
YF_FILES = {SYMBOL: Path("logs/USDJPY_X_1m_yf.csv"), PEER: Path("logs/BTC_USD_1m_yf.csv")}
BAR_DTYPE = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                      ("close", "f8"), ("tick_volume", "f8"), ("spread", "f8")])


def _rates(symbol: str, tf, count: int) -> np.ndarray:
    r = mt5.copy_rates_from_pos(symbol, tf, 1, int(count))
    if r is None or len(r) == 0:
        raise SystemExit(f"no rates for {symbol} tf={tf}: {mt5.last_error()}")
    return np.sort(r, order="time")


def _yf_frame(path: Path):
    import pandas as pd

    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()


def _to_bars(df, spread_by_hour: dict[int, float] | None = None) -> np.ndarray:
    out = np.zeros(len(df), dtype=BAR_DTYPE)
    out["time"] = (df.index.asi8 // 1_000_000_000).astype(np.int64)
    for k in ("open", "high", "low", "close"):
        out[k] = df[k].to_numpy(dtype=float)
    out["tick_volume"] = df["volume"].to_numpy(dtype=float)
    if spread_by_hour:
        hours = (out["time"] // 3600) % 24
        out["spread"] = np.array([spread_by_hour.get(int(h), 0.0) for h in hours])
    return out


def _resample(df, rule: str):
    agg = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return agg.dropna()


def load_yf(spread_by_hour: dict[int, float]) -> dict[str, np.ndarray]:
    jpy = _yf_frame(YF_FILES[SYMBOL])
    btc = _yf_frame(YF_FILES[PEER])
    return {
        "m1": _to_bars(jpy, spread_by_hour),
        "m5": _to_bars(_resample(jpy, "5min")),
        "m15": _to_bars(_resample(jpy, "15min")),
        "h4": _to_bars(_resample(jpy, "4h")),
        "btc5": _to_bars(_resample(btc, "5min")),
    }


def load_mt5(bars: int) -> dict[str, np.ndarray]:
    m1 = _rates(SYMBOL, mt5.TIMEFRAME_M1, bars)
    span_s = int(m1["time"][-1] - m1["time"][0])
    return {
        "m1": m1,
        "m5": _rates(SYMBOL, mt5.TIMEFRAME_M5, span_s // 300 + 500),
        "m15": _rates(SYMBOL, mt5.TIMEFRAME_M15, span_s // 900 + 500),
        "h4": _rates(SYMBOL, mt5.TIMEFRAME_H4, span_s // 14400 + 200),
        "btc5": _rates(PEER, mt5.TIMEFRAME_M5, span_s // 300 + 500),
    }


def _ohlcv(r: np.ndarray, lo: int, hi: int) -> Ohlcv:
    s = r[lo:hi]
    return Ohlcv.from_lists(
        s["open"].tolist(), s["high"].tolist(), s["low"].tolist(), s["close"].tolist(),
        s["tick_volume"].astype(float).tolist(),
    )


def _closed_window(r: np.ndarray, ends: np.ndarray, decision_t: int, n: int) -> tuple[int, int] | None:
    """Last `n` bars whose close (open + tf) is at or before decision_t."""
    hi = int(np.searchsorted(ends, decision_t, side="right"))
    lo = hi - n
    if lo < 0:
        return None
    return lo, hi


def simulate(m1: np.ndarray, i: int, side: str, *, dist: float, lot: float, contract: float,
             point: float, spread_floor_pts: float, hold: int = HOLD_BARS,
             usd_per_px_per_lot: float | None = None) -> dict:
    """Entry at open of bar i+1; SL/TP on bid (BUY) / ask (SELL); time exit at open of i+1+hold.

    ``usd_per_px_per_lot`` set means a USD-quoted CFD; None converts JPY at the exit price.
    """
    spr = np.maximum(m1["spread"].astype(float), float(spread_floor_pts)) * point
    e = i + 1
    if side == SIDE_BUY:
        entry = float(m1["open"][e]) + spr[e]
        sl, tp = entry - dist, entry + dist
    else:
        entry = float(m1["open"][e])
        sl, tp = entry + dist, entry - dist
    exit_px, how = None, "time"
    for j in range(e, e + hold):
        hi, lo = float(m1["high"][j]), float(m1["low"][j])
        if side == SIDE_BUY:
            hit_sl, hit_tp = lo <= sl, hi >= tp
        else:
            hit_sl, hit_tp = hi + spr[j] >= sl, lo + spr[j] <= tp
        if hit_sl:
            exit_px, how = sl, "sl"
            break
        if hit_tp:
            exit_px, how = tp, "tp"
            break
    x = e + hold
    if exit_px is None:
        exit_px = float(m1["open"][x]) + (spr[x] if side == SIDE_SELL else 0.0)
    sign = 1.0 if side == SIDE_BUY else -1.0
    if usd_per_px_per_lot is not None:
        usd_per_px = lot * float(usd_per_px_per_lot)
    else:
        usd_per_px = lot * contract / float(exit_px)
    net = (exit_px - entry) * sign * usd_per_px
    paid = spr[e] if side == SIDE_BUY else (spr[x] if how == "time" else spr[min(j, x)])
    return {"net": net, "gross": net + paid * usd_per_px, "how": how, "spread_usd": paid * usd_per_px}


def metrics(rows: list[dict], key: str = "net") -> dict:
    vals = [float(r[key]) for r in rows]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    mean = st.fmean(vals)
    sd = st.pstdev(vals) if n > 1 else 0.0
    eq, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    hows: dict[str, int] = {}
    for r in rows:
        hows[r["how"]] = hows.get(r["how"], 0) + 1
    return {
        "n": n,
        "win_rate_pct": round(100.0 * len(wins) / n, 1),
        "total_usd": round(sum(vals), 2),
        "mean_usd": round(mean, 4),
        "median_usd": round(st.median(vals), 4),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
        "sharpe_per_trade": round(mean / sd, 4) if sd > 0 else None,
        "t_stat": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 and n > 1 else None,
        "max_drawdown_usd": round(dd, 2),
        "exits": hows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("mt5", "yf"), default="yf")
    ap.add_argument("--bars", type=int, default=5000)
    args = ap.parse_args()

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    mt5.symbol_select(SYMBOL, True)
    mt5.symbol_select(PEER, True)
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    point, contract = float(info.point), float(info.trade_contract_size)
    tick_size = float(info.trade_tick_size)
    live_spread_pts = round((tick.ask - tick.bid) / point) if tick else 0
    cached = _rates(SYMBOL, mt5.TIMEFRAME_M1, 5000)
    cached_hours = (cached["time"] // 3600) % 24
    spread_by_hour = {int(h): float(np.median(cached["spread"][cached_hours == h])) for h in np.unique(cached_hours)}
    spread_p90_pts = float(np.percentile(cached["spread"], 90))

    data = load_yf(spread_by_hour) if args.source == "yf" else load_mt5(args.bars)
    m1, m5, m15, h4, btc5 = data["m1"], data["m5"], data["m15"], data["h4"], data["btc5"]
    span_s = int(m1["time"][-1] - m1["time"][0])
    ends = {
        "m5": m5["time"] + 300, "m15": m15["time"] + 900,
        "h4": h4["time"] + 14400, "btc5": btc5["time"] + 300,
    }

    t1 = m1["time"].astype(np.int64)
    n = len(m1)
    fires: list[dict] = []
    trades: dict[str, list[dict]] = {"bar_spread": [], "p90_floor": []}
    vetoed: list[dict] = []
    gate_pass = 0
    busy_until = -1
    for i in range(M1_N, n - HOLD_BARS - 2):
        if t1[i + 1] - t1[i] > 120 or t1[i + 1 + HOLD_BARS] - t1[i + 1] > 60 * HOLD_BARS + 120:
            continue
        decision_t = int(t1[i]) + 60
        wins = {k: _closed_window(r, ends[k], decision_t, nn) for k, r, nn in (
            ("m5", m5, M5_N), ("m15", m15, M15_N), ("h4", h4, H4_N), ("btc5", btc5, M5_N))}
        if wins["m5"] is None or wins["m15"] is None or wins["btc5"] is None:
            continue
        px = float(m1["close"][i])
        tv = tick_size * contract / px
        spec = ContractSpec(
            volume_min=float(info.volume_min), volume_step=float(info.volume_step),
            volume_max=float(info.volume_max),
            usd_at_min_lot_min_stop=max(point, tick_size, info.trade_stops_level * point) / tick_size * tv * info.volume_min,
            price=px, tick_value=tv, tick_size=tick_size,
        )
        snap = evaluate_symbol(
            SYMBOL,
            _ohlcv(m1, i - M1_N + 1, i + 1),
            _ohlcv(m5, *wins["m5"]),
            m15=_ohlcv(m15, *wins["m15"]),
            h4=_ohlcv(h4, *wins["h4"]) if wins["h4"] else None,
            btc_m5=_ohlcv(btc5, *wins["btc5"]),
            contract=spec,
            m1_time=int(t1[i]),
        )
        if not snap.consensus.fired or snap.consensus.direction not in (SIDE_BUY, SIDE_SELL):
            continue
        side = snap.consensus.direction
        fires.append({"i": i, "side": side, "agree": snap.consensus.agree, "skip": snap.skip_reason or "", "busy": i <= busy_until})
        lot = float(snap.size.lot) if snap.size and snap.size.lot else float(info.volume_min)
        dist = rotation_stop_distance(price=px, lot=lot, tick_value=tv, tick_size=tick_size, max_risk_usd=MAX_RISK_USD)
        if not dist:
            continue
        if snap.action != "FIRE":
            if snap.skip_reason in ("veto_htf", "sin_pullback", "rsi_rango", "murphy_climax"):
                row = simulate(m1, i, side, dist=dist, lot=lot, contract=contract, point=point, spread_floor_pts=0)
                row.update(skip=snap.skip_reason)
                vetoed.append(row)
            continue
        if i <= busy_until:
            continue
        gate = rotation_spread_skip_reason(
            spread_px=float(m1["spread"][i + 1]) * point, stop_dist=dist, lot=lot,
            tick_value=tv, tick_size=tick_size, risk_usd=float(snap.size.risk_usd or MAX_RISK_USD),
            max_spread_frac_of_risk=MAX_SPREAD_FRAC,
        )
        gate_pass += 0 if gate else 1
        base = {"t": int(t1[i]), "side": side, "agree": snap.consensus.agree, "spread_gate": gate or "pasa"}
        for key, floor in (("bar_spread", 0), ("p90_floor", spread_p90_pts)):
            row = simulate(m1, i, side, dist=dist, lot=lot, contract=contract, point=point, spread_floor_pts=floor)
            mirror = simulate(m1, i, SIDE_SELL if side == SIDE_BUY else SIDE_BUY, dist=dist, lot=lot,
                              contract=contract, point=point, spread_floor_pts=floor)
            trades[key].append({**base, **row, "mirror_net": mirror["net"]})
        busy_until = i + HOLD_BARS
        if len(trades["bar_spread"]) % 100 == 0:
            print(f"  {len(trades['bar_spread'])} trades, bar {i}/{n}", file=sys.stderr, flush=True)

    def _ts(x: int) -> str:
        return datetime.fromtimestamp(int(x), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    main_rows = trades["bar_spread"]
    half = len(main_rows) // 2
    days = span_s / 86400.0
    skips: dict[str, int] = {}
    for f in fires:
        k = "slot_ocupado" if f["busy"] and not f["skip"] else (f["skip"] or "dispara")
        skips[k] = skips.get(k, 0) + 1
    spr_pts = m1["spread"].astype(float)
    out = {
        "symbol": SYMBOL,
        "source": ("Yahoo 1m USDJPY=X + BTC-USD (higher TFs resampled), XBTFX spread by UTC hour"
                   if args.source == "yf" else "XBTFX MT5 terminal cache (server clock = UTC)"),
        "range_utc": [_ts(t1[0]), _ts(t1[-1])],
        "m1_bars": int(n),
        "calendar_days": round(days, 1),
        "contract": {"point": point, "contract_size": contract, "volume_min": float(info.volume_min),
                     "live_spread_pts": live_spread_pts, "xbtfx_spread_p90_pts": spread_p90_pts},
        "bar_spread_pts": {"median": float(np.median(spr_pts)), "p90": float(np.percentile(spr_pts, 90)),
                           "min": float(spr_pts.min())},
        "fires_4_5": len(fires),
        "fires_per_day": round(len(fires) / days, 1) if days else None,
        "fire_outcome": skips,
        "spread_gate_pass": gate_pass,
        "trades_bar_spread": metrics(main_rows),
        "trades_bar_spread_gross": metrics(main_rows, "gross"),
        "trades_spread_p90_floor": metrics(trades["p90_floor"]),
        "mirror_side_bar_spread": metrics([{**r, "net": r["mirror_net"]} for r in main_rows]),
        "first_half": metrics(main_rows[:half]),
        "second_half": metrics(main_rows[half:]),
        "agree_4": metrics([r for r in main_rows if r["agree"] == 4]),
        "agree_5": metrics([r for r in main_rows if r["agree"] == 5]),
        "buy": metrics([r for r in main_rows if r["side"] == SIDE_BUY]),
        "sell": metrics([r for r in main_rows if r["side"] == SIDE_SELL]),
        "vetoed_by_gates_bar_spread_overlapping": metrics(vetoed),
        "mean_spread_usd": round(st.fmean(r["spread_usd"] for r in main_rows), 4) if main_rows else None,
    }
    Path(f"analyze_usdjpy_rotation_replay_{args.source}_out.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
