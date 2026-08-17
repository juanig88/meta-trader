"""
Deep instrument scan for IC Markets (MT5).

Premise: many small trades (lot 0.01), similar economics to bot_mt5:
  - breakout path: strategy_core hybrid + ATR SL×1.5 / TP×2.0
  - scalp path: EMA9/21 cross + ADX≥25 (+ H1 bias), ATR SL×1.5 / RR2

Universe: Forex / Crypto / Commodities / Indices / Bonds + curated liquid stocks.
Skips expired/disabled futures and the bulk Stock CFD catalogue.

Outputs (under this folder):
  results/scan_progress.jsonl   — one row per symbol (resume-safe)
  results/scan_summary.json     — aggregates
  informe_deep_analysis.md      — human report

Usage (from repo root):
  python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py"
  python ".../deep_instrument_scan.py" --resume
  python ".../deep_instrument_scan.py" --report-only
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import MetaTrader5 as mt5
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from indicators import atr as atr_series, ema as ema_series, adx_wilder  # noqa: E402
from strategy_core import (  # noqa: E402
    Features,
    StrategyParams,
    decide,
    signal_m15_relaxed,
    signal_m15_strong_breakout,
    symbol_uses_crypto_relaxed_m15,
    trend_h4_from_closes,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CSV_CATALOG = HERE / "icmarkets_instrumentos.csv"
PROGRESS = RESULTS / "scan_progress.jsonl"
SUMMARY = RESULTS / "scan_summary.json"
REPORT = HERE / "informe_deep_analysis.md"

ART = timezone(timedelta(hours=-3))
COMMISSION_RT_PER_LOT = 7.0  # IC Raw-ish round-trip $/lot
LOT = 0.01
INITIAL_BALANCE = 100.0
OOS_FRAC = 0.20
MIN_BARS_M15 = 800
MIN_TRADES_GATE = 25

CURATED_STOCKS = [
    "NVDA.NAS",
    "AAPL.NAS",
    "TSLA.NAS",
    "MELI.NAS",
    "META.NAS",
    "MSFT.NAS",
    "AMZN.NAS",
    "GOOGL.NAS",
    "AMD.NAS",
    "NFLX.NAS",
]

SKIP_TIPOS = {"Stock CFD's"}
# Prefer spot / continuous names; skip obvious expired futures suffixes when trade_mode bad.


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def connect() -> None:
    _load_dotenv()
    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]
    server = os.environ["MT5_SERVER"]
    ok = mt5.initialize(login=login, password=password, server=server)
    if not ok:
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")


def load_universe() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    all_by_sym: dict[str, dict[str, str]] = {}
    with CSV_CATALOG.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            all_by_sym[sym] = r
            tipo = (r.get("tipo") or "").strip()
            mode = (r.get("trade_mode") or "").strip().lower()
            if tipo in SKIP_TIPOS:
                continue
            if mode in ("disabled",):
                continue
            rows.append(
                {
                    "symbol": sym,
                    "tipo": tipo or "Unknown",
                    "subtipo": (r.get("subtipo") or "").strip(),
                    "description": (r.get("description") or "").strip(),
                    "trade_mode": mode,
                }
            )
    have = {r["symbol"] for r in rows}
    for sym in CURATED_STOCKS:
        if sym in have:
            continue
        src = all_by_sym.get(sym, {})
        rows.append(
            {
                "symbol": sym,
                "tipo": "Stock CFD's (curated)",
                "subtipo": (src.get("subtipo") or "NAS").strip(),
                "description": (src.get("description") or sym).strip(),
                "trade_mode": (src.get("trade_mode") or "full").strip().lower(),
            }
        )
    rows.sort(key=lambda r: (r["tipo"], r["symbol"]))
    return rows


def done_symbols() -> set[str]:
    if not PROGRESS.exists():
        return set()
    out: set[str] = set()
    for line in PROGRESS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.add(json.loads(line)["symbol"])
        except Exception:
            continue
    return out


def append_progress(row: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def value_per_point_per_lot(info) -> Optional[float]:
    point = float(getattr(info, "point", 0) or 0)
    if point <= 0:
        return None
    for tv, ts in (
        (getattr(info, "trade_tick_value", None), getattr(info, "trade_tick_size", None)),
        (getattr(info, "tick_value", None), getattr(info, "tick_size", None)),
    ):
        try:
            tvf = float(tv)
            tsf = float(ts)
            if tvf > 0 and tsf > 0:
                return float(tvf / tsf * point)
        except Exception:
            continue
    return None


def fetch_rates(symbol: str, tf: int, years: float, chunk_days: int) -> Optional[np.ndarray]:
    mt5.symbol_select(symbol, True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(365.25 * years) + 5)
    chunks: list[np.ndarray] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, tf, cur, nxt)
        if rates is not None and len(rates):
            chunks.append(rates)
        cur = nxt
    if not chunks:
        # fallback recent bars
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, 99000)
        return rates if rates is not None and len(rates) else None
    merged = np.concatenate(chunks)
    # de-dupe by time
    _, idx = np.unique(merged["time"], return_index=True)
    merged = merged[np.sort(idx)]
    return merged


def session_profile(m15: np.ndarray) -> dict[str, Any]:
    by_hour: dict[int, list[float]] = defaultdict(list)
    by_dow: dict[int, list[float]] = defaultdict(list)
    for r in m15:
        t = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
        rng = float(r["high"]) - float(r["low"])
        by_hour[t.hour].append(rng)
        by_dow[t.weekday()].append(rng)

    def pack(d: dict[int, list[float]]) -> list[dict[str, Any]]:
        out = []
        for k in sorted(d):
            vals = d[k]
            out.append(
                {
                    "key": k,
                    "n": len(vals),
                    "avg_range": float(sum(vals) / len(vals)),
                    "med_range": float(sorted(vals)[len(vals) // 2]),
                }
            )
        return out

    hours = pack(by_hour)
    # top 5 hours by avg range
    top_hours = sorted(hours, key=lambda x: -x["avg_range"])[:5]
    return {
        "by_hour_utc": hours,
        "by_dow": pack(by_dow),  # 0=Mon
        "top_range_hours_utc": [h["key"] for h in top_hours],
        "bars": int(len(m15)),
        "first_utc": datetime.fromtimestamp(int(m15[0]["time"]), tz=timezone.utc).isoformat(),
        "last_utc": datetime.fromtimestamp(int(m15[-1]["time"]), tz=timezone.utc).isoformat(),
        "years": float((int(m15[-1]["time"]) - int(m15[0]["time"])) / 86400 / 365.25),
    }


def _h4_trend_series(m15_times: list[int], h4: np.ndarray) -> list[str]:
    h4_times = [int(r["time"]) for r in h4]
    h4_closes = [float(r["close"]) for r in h4]
    out = ["FLAT"] * len(m15_times)
    j = 0
    last_closes: list[float] = []
    for i, t in enumerate(m15_times):
        while j < len(h4_times) and h4_times[j] <= t:
            last_closes.append(h4_closes[j])
            if len(last_closes) > 10:
                last_closes = last_closes[-10:]
            j += 1
        out[i] = trend_h4_from_closes(last_closes)
    return out


def _h1_ema_at_m15(m15_times: list[int], h1: Optional[np.ndarray], period: int = 20) -> list[Optional[float]]:
    if h1 is None or len(h1) < period + 2:
        return [None] * len(m15_times)
    closes = [float(r["close"]) for r in h1]
    emas = ema_series(closes, period)
    h1_times = [int(r["time"]) for r in h1]
    out: list[Optional[float]] = [None] * len(m15_times)
    j = 0
    last: Optional[float] = None
    for i, t in enumerate(m15_times):
        while j < len(h1_times) and h1_times[j] <= t:
            last = emas[j]
            j += 1
        out[i] = last
    return out


@dataclass
class SimTrade:
    side: str
    entry_time: int
    exit_time: int
    pnl_money: float
    strategy: str


def _metrics(trades: list[SimTrade], balance0: float = INITIAL_BALANCE) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
            "pnl": 0.0,
            "pf": 0.0,
            "avg_trade": 0.0,
            "max_dd": 0.0,
            "final_balance": balance0,
            "trades_per_week": 0.0,
        }
    pnls = [t.pnl_money for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    bal = balance0
    peak = balance0
    max_dd = 0.0
    for p in pnls:
        bal += p
        peak = max(peak, bal)
        max_dd = min(max_dd, bal - peak)
    t0 = trades[0].entry_time
    t1 = trades[-1].exit_time
    weeks = max(1.0, (t1 - t0) / 86400 / 7.0)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": len(wins) / len(trades),
        "pnl": sum(pnls),
        "pf": pf,
        "avg_trade": sum(pnls) / len(trades),
        "max_dd": max_dd,
        "final_balance": bal,
        "trades_per_week": len(trades) / weeks,
    }


def _hour_day_breakdown(trades: list[SimTrade]) -> dict[str, Any]:
    by_h: dict[int, list[float]] = defaultdict(list)
    by_d: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        dt = datetime.fromtimestamp(t.entry_time, tz=timezone.utc)
        by_h[dt.hour].append(t.pnl_money)
        by_d[dt.weekday()].append(t.pnl_money)

    def rank(d: dict[int, list[float]], min_n: int = 5) -> list[dict[str, Any]]:
        rows = []
        for k, vals in d.items():
            if len(vals) < min_n:
                continue
            w = [v for v in vals if v > 0]
            l = [v for v in vals if v <= 0]
            gp = sum(w)
            gl = abs(sum(l))
            pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
            rows.append(
                {
                    "key": k,
                    "n": len(vals),
                    "pnl": sum(vals),
                    "pf": pf,
                    "avg": sum(vals) / len(vals),
                }
            )
        rows.sort(key=lambda x: (-x["pf"], -x["pnl"]))
        return rows

    best_h = rank(by_h)[:5]
    best_d = rank(by_d, min_n=3)[:5]
    # contiguous windows of profitable hours (PF>=1 and pnl>0)
    hour_ok = {r["key"] for r in rank(by_h) if r["pf"] >= 1.0 and r["pnl"] > 0}
    windows: list[str] = []
    start = None
    for h in range(24):
        if h in hour_ok:
            if start is None:
                start = h
        elif start is not None:
            windows.append(f"{start:02d}:00-{h:02d}:00 UTC")
            start = None
    if start is not None:
        windows.append(f"{start:02d}:00-24:00 UTC")
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "best_hours_utc": best_h,
        "best_days": [{**r, "name": dow_names[r["key"]]} for r in best_d],
        "suggested_windows_utc": windows,
        "suggested_windows_art": [
            _utc_window_to_art(w) for w in windows
        ],
    }


def _utc_window_to_art(w: str) -> str:
    # "HH:00-HH:00 UTC"
    try:
        a, b = w.replace(" UTC", "").split("-")
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
        # ART = UTC-3
        ah = (ah - 3) % 24
        bh = (bh - 3) % 24
        return f"{ah:02d}:{am:02d}-{bh:02d}:{bm:02d} ART"
    except Exception:
        return w


def sim_breakout(
    symbol: str,
    m15: np.ndarray,
    h4: np.ndarray,
    *,
    point: float,
    vpp: float,
    spread_points: float,
    digits: int,
    adx_min: float,
    i0: int,
    i1: int,
) -> list[SimTrade]:
    params = StrategyParams(adx_min=adx_min, entry_mode="hybrid", allow_flat_h4=True)
    open_ = [float(r["open"]) for r in m15]
    high = [float(r["high"]) for r in m15]
    low = [float(r["low"]) for r in m15]
    close = [float(r["close"]) for r in m15]
    times = [int(r["time"]) for r in m15]
    trend = _h4_trend_series(times, h4)
    atrs = atr_series(high, low, close, 14)
    ema_f = ema_series(close, 50)
    ema_s = ema_series(close, 200)
    adx = adx_wilder(high, low, close, 14).adx
    half_spread = float(spread_points) * point / 2.0
    slip = float(spread_points) * point * 0.25
    comm = COMMISSION_RT_PER_LOT * LOT
    trades: list[SimTrade] = []
    pos = None
    start_i = max(i0, 210, 50)
    end_i = min(i1, len(m15) - 2)
    for i in range(start_i, end_i):
        if pos is not None:
            side, entry, sl, tp, et = pos
            hi, lo = high[i + 1], low[i + 1]
            exit_price = None
            if side == "BUY":
                if lo <= sl:
                    exit_price = sl - slip
                elif hi >= tp:
                    exit_price = tp - slip
                if exit_price is not None:
                    pnl = (exit_price - entry) / point * vpp * LOT - comm
                    trades.append(SimTrade(side, et, times[i + 1], pnl, "breakout"))
                    pos = None
            else:
                if hi >= sl:
                    exit_price = sl + slip
                elif lo <= tp:
                    exit_price = tp + slip
                if exit_price is not None:
                    pnl = (entry - exit_price) / point * vpp * LOT - comm
                    trades.append(SimTrade(side, et, times[i + 1], pnl, "breakout"))
                    pos = None
            if pos is not None:
                continue

        a = adx[i]
        ef, es = ema_f[i], ema_s[i]
        atrv = atrs[i]
        if a is None or ef is None or es is None or atrv is None or atrv <= 0:
            continue
        _lo = max(0, i - 1)
        if symbol_uses_crypto_relaxed_m15(symbol):
            sig = signal_m15_relaxed(
                open_[_lo : i + 1],
                high[_lo : i + 1],
                low[_lo : i + 1],
                close[_lo : i + 1],
                is_crypto=True,
            )
        else:
            sig = signal_m15_strong_breakout(
                open_[_lo : i + 1],
                high[_lo : i + 1],
                low[_lo : i + 1],
                close[_lo : i + 1],
            )
        price = close[i]
        if ef > es and price > ef:
            ema_dir = "UP"
        elif ef < es and price < ef:
            ema_dir = "DOWN"
        else:
            ema_dir = "FLAT"
        decision = decide(
            params,
            Features(
                trend_h4=trend[i],  # type: ignore[arg-type]
                signal_m15=sig,  # type: ignore[arg-type]
                ema_dir=ema_dir,  # type: ignore[arg-type]
                ema_fast=ef,
                ema_slow=es,
                adx=float(a),
            ),
            symbol=symbol,
        )
        if decision == "NONE":
            continue
        sl_dist = max(atrv * 1.5, point * 10)
        tp_dist = sl_dist * 2.0
        # entry next open +/- spread
        entry = open_[i + 1]
        if decision == "BUY":
            entry += half_spread
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            entry -= half_spread
            sl = entry + sl_dist
            tp = entry - tp_dist
        pos = (decision, entry, sl, tp, times[i + 1])
    return trades


def sim_scalp(
    symbol: str,
    m15: np.ndarray,
    h1: Optional[np.ndarray],
    *,
    point: float,
    vpp: float,
    spread_points: float,
    adx_min: float,
    i0: int,
    i1: int,
) -> list[SimTrade]:
    open_ = [float(r["open"]) for r in m15]
    high = [float(r["high"]) for r in m15]
    low = [float(r["low"]) for r in m15]
    close = [float(r["close"]) for r in m15]
    times = [int(r["time"]) for r in m15]
    atrs = atr_series(high, low, close, 14)
    ema_f = ema_series(close, 9)
    ema_s = ema_series(close, 21)
    adx = adx_wilder(high, low, close, 14).adx
    h1_ema = _h1_ema_at_m15(times, h1, 20)
    half_spread = float(spread_points) * point / 2.0
    slip = float(spread_points) * point * 0.25
    comm = COMMISSION_RT_PER_LOT * LOT
    trades: list[SimTrade] = []
    pos = None
    start_i = max(i0, 60)
    end_i = min(i1, len(m15) - 2)
    for i in range(start_i, end_i):
        if pos is not None:
            side, entry, sl, tp, et = pos
            hi, lo = high[i + 1], low[i + 1]
            exit_price = None
            if side == "BUY":
                if lo <= sl:
                    exit_price = sl - slip
                elif hi >= tp:
                    exit_price = tp - slip
                if exit_price is not None:
                    pnl = (exit_price - entry) / point * vpp * LOT - comm
                    trades.append(SimTrade(side, et, times[i + 1], pnl, "scalp"))
                    pos = None
            else:
                if hi >= sl:
                    exit_price = sl + slip
                elif lo <= tp:
                    exit_price = tp + slip
                if exit_price is not None:
                    pnl = (entry - exit_price) / point * vpp * LOT - comm
                    trades.append(SimTrade(side, et, times[i + 1], pnl, "scalp"))
                    pos = None
            if pos is not None:
                continue

        a = adx[i]
        f0, s0 = ema_f[i - 1], ema_s[i - 1]
        f1, s1 = ema_f[i], ema_s[i]
        atrv = atrs[i]
        if None in (a, f0, s0, f1, s1, atrv) or atrv <= 0 or a < adx_min:
            continue
        prev_up = f0 > s0
        curr_up = f1 > s1
        prev_dn = f0 < s0
        curr_dn = f1 < s1
        side = "NONE"
        if (not prev_up) and curr_up:
            side = "BUY"
        elif (not prev_dn) and curr_dn:
            side = "SELL"
        if side == "NONE":
            continue
        # price side of EMA9
        if side == "BUY" and close[i] < f1:
            continue
        if side == "SELL" and close[i] > f1:
            continue
        h1e = h1_ema[i]
        if h1e is not None:
            if side == "BUY" and close[i] <= h1e:
                continue
            if side == "SELL" and close[i] >= h1e:
                continue
        sl_dist = max(atrv * 1.5, point * 10)
        # metals-like floor for goldish prices
        if "XAU" in symbol.upper():
            sl_dist = max(sl_dist, 1.5)
        tp_dist = sl_dist * 2.0
        entry = open_[i + 1]
        if side == "BUY":
            entry += half_spread
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            entry -= half_spread
            sl = entry + sl_dist
            tp = entry - tp_dist
        pos = (side, entry, sl, tp, times[i + 1])
    return trades


def score_candidate(bo_is, bo_oos, sc_is, sc_oos) -> tuple[float, str, dict]:
    """Pick best strategy path; score favors OOS PF, frequency, small positive expectancy."""
    candidates = []
    for name, ism, oos in (("breakout", bo_is, bo_oos), ("scalp", sc_is, sc_oos)):
        if ism["trades"] < MIN_TRADES_GATE:
            continue
        if ism["pf"] < 1.05 or ism["avg_trade"] <= 0:
            continue
        oos_pf = oos["pf"] if oos["trades"] >= max(8, MIN_TRADES_GATE // 3) else 0.0
        # score: OOS PF primary, then IS PF, then trades/week (many small), penalize DD
        score = (
            2.0 * oos_pf
            + 1.0 * min(ism["pf"], 3.0)
            + 0.15 * min(ism["trades_per_week"], 40)
            + 0.5 * (1.0 if oos_pf >= 1.0 else -1.0)
            + 0.01 * ism["pnl"]
            + 0.05 * ism["max_dd"]  # max_dd negative
        )
        candidates.append((score, name, ism, oos))
    if not candidates:
        return -999.0, "none", {}
    candidates.sort(key=lambda x: -x[0])
    score, name, ism, oos = candidates[0]
    return score, name, {"is": ism, "oos": oos}


def analyze_symbol(meta: dict[str, str]) -> dict[str, Any]:
    sym = meta["symbol"]
    info = mt5.symbol_info(sym)
    if info is None:
        return {"symbol": sym, "ok": False, "error": "symbol_info_none", **meta}

    mt5.symbol_select(sym, True)
    point = float(info.point or 0) or 0.00001
    digits = int(info.digits or 5)
    spread = float(info.spread or 0)
    vpp = value_per_point_per_lot(info)
    if vpp is None or vpp <= 0:
        return {"symbol": sym, "ok": False, "error": "no_vpp", **meta}

    trade_mode = int(getattr(info, "trade_mode", -1))
    if trade_mode == 0:  # disabled
        return {"symbol": sym, "ok": False, "error": "trade_disabled", **meta}

    m15 = fetch_rates(sym, mt5.TIMEFRAME_M15, years=5.0, chunk_days=90)
    h4 = fetch_rates(sym, mt5.TIMEFRAME_H4, years=5.0, chunk_days=180)
    h1 = fetch_rates(sym, mt5.TIMEFRAME_H1, years=5.0, chunk_days=120)
    if m15 is None or len(m15) < MIN_BARS_M15 or h4 is None or len(h4) < 50:
        return {
            "symbol": sym,
            "ok": False,
            "error": "insufficient_history",
            "m15_bars": 0 if m15 is None else int(len(m15)),
            "h4_bars": 0 if h4 is None else int(len(h4)),
            **meta,
        }

    # align: use overlapping window
    t0 = max(int(m15[0]["time"]), int(h4[0]["time"]))
    m15 = m15[m15["time"] >= t0]
    h4 = h4[h4["time"] >= t0]
    if h1 is not None:
        h1 = h1[h1["time"] >= t0]
    if len(m15) < MIN_BARS_M15:
        return {"symbol": sym, "ok": False, "error": "short_after_align", **meta}

    profile = session_profile(m15)
    n = len(m15)
    split = int(n * (1.0 - OOS_FRAC))
    adx_min = 17.0 if symbol_uses_crypto_relaxed_m15(sym) else 20.0
    # slightly higher for indices/metals scalp noise
    if meta["tipo"].startswith("Indices") or "XAU" in sym or "XAG" in sym:
        adx_min_bo = max(adx_min, 20.0)
    else:
        adx_min_bo = adx_min

    bo_is_tr = sim_breakout(
        sym, m15, h4, point=point, vpp=vpp, spread_points=spread, digits=digits,
        adx_min=adx_min_bo, i0=0, i1=split,
    )
    bo_oos_tr = sim_breakout(
        sym, m15, h4, point=point, vpp=vpp, spread_points=spread, digits=digits,
        adx_min=adx_min_bo, i0=split, i1=n,
    )
    sc_is_tr = sim_scalp(
        sym, m15, h1, point=point, vpp=vpp, spread_points=spread, adx_min=25.0, i0=0, i1=split,
    )
    sc_oos_tr = sim_scalp(
        sym, m15, h1, point=point, vpp=vpp, spread_points=spread, adx_min=25.0, i0=split, i1=n,
    )

    bo_is = _metrics(bo_is_tr)
    bo_oos = _metrics(bo_oos_tr)
    sc_is = _metrics(sc_is_tr)
    sc_oos = _metrics(sc_oos_tr)

    score, best_name, best_pack = score_candidate(bo_is, bo_oos, sc_is, sc_oos)
    all_best_trades = (bo_is_tr + bo_oos_tr) if best_name == "breakout" else (sc_is_tr + sc_oos_tr)
    sessions = _hour_day_breakdown(all_best_trades) if all_best_trades else {}

    # cost context: $ per point at 0.01
    usd_per_point_001 = vpp * LOT
    # rough: money for 1 ATR median
    atrs = atr_series(
        [float(r["high"]) for r in m15],
        [float(r["low"]) for r in m15],
        [float(r["close"]) for r in m15],
        14,
    )
    atr_vals = [a for a in atrs[-500:] if a is not None]
    med_atr = float(sorted(atr_vals)[len(atr_vals) // 2]) if atr_vals else 0.0
    sl_money_est = (med_atr * 1.5 / point) * usd_per_point_001 if point > 0 else 0.0

    promising = (
        best_name != "none"
        and best_pack.get("oos", {}).get("pf", 0) >= 1.0
        and best_pack.get("is", {}).get("pf", 0) >= 1.1
        and best_pack.get("is", {}).get("avg_trade", 0) > 0
        and best_pack.get("is", {}).get("trades", 0) >= MIN_TRADES_GATE
    )

    return {
        "symbol": sym,
        "ok": True,
        "error": None,
        "tipo": meta["tipo"],
        "subtipo": meta["subtipo"],
        "description": meta["description"],
        "trade_mode_csv": meta.get("trade_mode"),
        "point": point,
        "digits": digits,
        "spread_points": spread,
        "vpp_1lot": vpp,
        "usd_per_point_0.01": usd_per_point_001,
        "med_atr_m15": med_atr,
        "est_sl_money_atr15": sl_money_est,
        "commission_rt_0.01": COMMISSION_RT_PER_LOT * LOT,
        "profile": profile,
        "breakout_is": bo_is,
        "breakout_oos": bo_oos,
        "scalp_is": sc_is,
        "scalp_oos": sc_oos,
        "best_strategy": best_name,
        "score": score,
        "best": best_pack,
        "sessions": sessions,
        "promising": promising,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_report(rows: list[dict[str, Any]]) -> str:
    ok = [r for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]
    prom = sorted([r for r in ok if r.get("promising")], key=lambda r: -float(r.get("score") or -999))
    near = sorted(
        [r for r in ok if not r.get("promising") and r.get("best_strategy") != "none"],
        key=lambda r: -float(r.get("score") or -999),
    )[:25]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines += [
        "# Informe deep-analysis — IC Markets / bot_mt5 style",
        "",
        f"- **Generado:** {now}",
        f"- **Broker:** Raw Trading Ltd / ICMarketsSC-Demo (vía MT5)",
        f"- **Premisa:** muchas operaciones, poca ganancia por trade (lot `{LOT}`, ATR SL×1.5 / TP RR2, comisión RT ≈ `${COMMISSION_RT_PER_LOT}`/lot)",
        f"- **Universo analizado:** {len(rows)} símbolos (Forex/Crypto/Commodities/Indices/Bonds + stocks curados; **sin** el dump de 7k stocks)",
        f"- **OK con datos:** {len(ok)} | **fallidos/sin hist:** {len(bad)} | **prometedores (gate):** {len(prom)}",
        f"- **Holdout OOS:** último {int(OOS_FRAC*100)}% de barras M15",
        "",
        "## 0. Veredicto ejecutivo (risk-quant)",
        "",
    ]
    if prom:
        lines.append(
            f"Hay **{len(prom)}** instrumentos que pasan el gate IS/OOS con la premisa de scalping/breakout "
            "de muchas ops chicas. **No es aprobación live.** Es shortlist para paper/demo en paralelo "
            "con riesgo acotado (0.01, 1 posición, cooldown)."
        )
    else:
        lines.append(
            "Ningún instrumento pasó el gate estricto IS+OOS. Revisá la sección de 'casi' y los perfiles "
            "horarios; puede hacer falta relajar ventanas o bajar ADX en crypto."
        )
    lines += [
        "",
        "**Lunes en paralelo:** solo candidatos `promising=true` con ventana horaria sugerida, "
        "máx 2–4 símbolos, demo, y sin aumentar lot. Stocks masivos quedan fuera de esta corrida.",
        "",
        "## 1. Metodología",
        "",
        "### 1.1 Datos",
        "",
        "- Fuente primaria: **MT5 `copy_rates_range`** (cuenta demo ICMarketsSC).",
        "- Timeframes usados en el scan: **M15 + H4** (estrategias), **H1** (bias del scalp).",
        "- Profundidad real observada en este broker/terminal (límites prácticos ~100k barras):",
        "  - H4 ≈ 5 años",
        "  - M15 ≈ 3–4 años (a veces menos en crypto)",
        "  - M5/M1 **no** se usaron para backtest masivo (hist. insuficiente a 5y)",
        "- Costos modelados: spread actual del símbolo + 25% spread como slip + comisión RT $7/lot.",
        "",
        "### 1.2 Estrategias (espejo del bot)",
        "",
        "| Path | Lógica | Salidas |",
        "|---|---|---|",
        "| `breakout` | `strategy_core.decide` hybrid: breakout M15 + EMA50/200 + ADX + H4 | ATR×1.5 SL / RR2 TP |",
        "| `scalp` | Edge EMA9/21 + ADX≥25 + precio del lado de EMA9 + bias H1 EMA20 | ATR×1.5 SL / RR2 TP (como XAU parallel) |",
        "",
        "### 1.3 Gate 'promising'",
        "",
        f"- IS trades ≥ {MIN_TRADES_GATE}, PF IS ≥ 1.1, avg trade > 0",
        "- OOS PF ≥ 1.0 con suficientes trades OOS",
        "- Score combina PF OOS/IS, trades/semana (frecuencia) y penaliza drawdown",
        "",
        "### 1.4 Sesiones (contexto IC Markets + mercado)",
        "",
        "Fuente broker: [IC Markets Trading Hours](https://www.icmarkets.sc/global/en/trading-pricing/trading-hours) "
        "(server GMT+2 / GMT+3 DST).",
        "",
        "- **Forex:** 00:01–23:59 server (vie 23:57)",
        "- **XAU/metales:** ~01:02–23:59 server",
        "- **Energies spot:** XTI/XNG ~01:00–23:59; XBR con break diario",
        "- **Índices US (US500/USTEC/US30):** ~01:00–23:59 server con micro-breaks",
        "- **Crypto CFD:** casi 24/7 (con huecos cortos vie/sáb)",
        "- **Stocks NAS/NYSE cash:** ~16:35–22:55 server; variantes 24HR distintas",
        "",
        "Liquidez FX típica (no es edge garantizado): overlap **London–NY** "
        "(aprox 12:00–16:00 UTC / 09:00–13:00 ART). El scan **no asume** eso: "
        "propone ventanas según PnL histórico simulado por hora de entrada.",
        "",
        "## 2. Shortlist prometedora",
        "",
    ]

    if not prom:
        lines.append("_Vacío bajo el gate estricto._")
    else:
        lines += [
            "| # | Símbolo | Tipo | Strat | Score | IS n | IS PF | IS PnL$ | IS DD$ | OOS n | OOS PF | OOS PnL$ | ops/sem | Ventanas UTC | Ventanas ART |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for i, r in enumerate(prom, 1):
            b = r.get("best") or {}
            ism = b.get("is") or {}
            oos = b.get("oos") or {}
            sess = r.get("sessions") or {}
            w_utc = ", ".join(sess.get("suggested_windows_utc") or []) or "—"
            w_art = ", ".join(sess.get("suggested_windows_art") or []) or "—"
            lines.append(
                f"| {i} | `{r['symbol']}` | {r.get('tipo','')} | {r.get('best_strategy')} | "
                f"{float(r.get('score') or 0):.2f} | {ism.get('trades',0)} | {float(ism.get('pf') or 0):.2f} | "
                f"{float(ism.get('pnl') or 0):.2f} | {float(ism.get('max_dd') or 0):.2f} | "
                f"{oos.get('trades',0)} | {float(oos.get('pf') or 0):.2f} | {float(oos.get('pnl') or 0):.2f} | "
                f"{float(ism.get('trades_per_week') or 0):.1f} | {w_utc} | {w_art} |"
            )

    lines += ["", "## 3. Fichas detalladas (prometedores)", ""]
    for r in prom:
        b = r.get("best") or {}
        ism = b.get("is") or {}
        oos = b.get("oos") or {}
        sess = r.get("sessions") or {}
        prof = r.get("profile") or {}
        lines += [
            f"### `{r['symbol']}` — {r.get('description','')}",
            "",
            f"- **Tipo:** {r.get('tipo')} / {r.get('subtipo') or '—'}",
            f"- **Estrategia ganadora en scan:** `{r.get('best_strategy')}` (score {float(r.get('score') or 0):.2f})",
            f"- **Histórico M15 usado:** {prof.get('bars')} barras ≈ {float(prof.get('years') or 0):.2f}y "
            f"({prof.get('first_utc')} → {prof.get('last_utc')})",
            f"- **Spread actual (points):** {r.get('spread_points')} | "
            f"**$/point @0.01:** {float(r.get('usd_per_point_0.01') or 0):.4f} | "
            f"**SL$ est. ATR×1.5:** {float(r.get('est_sl_money_atr15') or 0):.2f}",
            f"- **IS:** n={ism.get('trades')} PF={float(ism.get('pf') or 0):.2f} "
            f"WR={float(ism.get('winrate') or 0)*100:.1f}% PnL=${float(ism.get('pnl') or 0):.2f} "
            f"avg=${float(ism.get('avg_trade') or 0):.3f} DD=${float(ism.get('max_dd') or 0):.2f} "
            f"ops/sem={float(ism.get('trades_per_week') or 0):.1f}",
            f"- **OOS:** n={oos.get('trades')} PF={float(oos.get('pf') or 0):.2f} "
            f"PnL=${float(oos.get('pnl') or 0):.2f} DD=${float(oos.get('max_dd') or 0):.2f}",
            f"- **Horas top rango (UTC):** {prof.get('top_range_hours_utc')}",
            f"- **Ventanas sugeridas UTC:** {', '.join(sess.get('suggested_windows_utc') or []) or '—'}",
            f"- **Ventanas sugeridas ART:** {', '.join(sess.get('suggested_windows_art') or []) or '—'}",
            "",
        ]
        bh = sess.get("best_hours_utc") or []
        if bh:
            lines.append("| Hora UTC | n | PF | PnL$ | avg$ |")
            lines.append("|---:|---:|---:|---:|---:|")
            for h in bh[:8]:
                lines.append(
                    f"| {h['key']:02d} | {h['n']} | {h['pf']:.2f} | {h['pnl']:.2f} | {h['avg']:.3f} |"
                )
            lines.append("")
        bd = sess.get("best_days") or []
        if bd:
            lines.append("| Día | n | PF | PnL$ |")
            lines.append("|---|---:|---:|---:|")
            for d in bd:
                lines.append(f"| {d.get('name', d['key'])} | {d['n']} | {d['pf']:.2f} | {d['pnl']:.2f} |")
            lines.append("")
        lines += [
            f"- **Breakout IS/OOS PF:** {float(r['breakout_is'].get('pf') or 0):.2f} / "
            f"{float(r['breakout_oos'].get('pf') or 0):.2f} "
            f"(n {r['breakout_is'].get('trades')}/{r['breakout_oos'].get('trades')})",
            f"- **Scalp IS/OOS PF:** {float(r['scalp_is'].get('pf') or 0):.2f} / "
            f"{float(r['scalp_oos'].get('pf') or 0):.2f} "
            f"(n {r['scalp_is'].get('trades')}/{r['scalp_oos'].get('trades')})",
            "",
            "**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), "
            "o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.",
            "",
        ]

    lines += [
        "## 4. Casi-prometedores (no pasan gate, útiles para research)",
        "",
    ]
    if not near:
        lines.append("_Ninguno._")
    else:
        lines += [
            "| Símbolo | Tipo | Strat | Score | IS PF | OOS PF | IS n | ops/sem |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for r in near:
            b = r.get("best") or {}
            ism = b.get("is") or {}
            oos = b.get("oos") or {}
            lines.append(
                f"| `{r['symbol']}` | {r.get('tipo')} | {r.get('best_strategy')} | "
                f"{float(r.get('score') or 0):.2f} | {float(ism.get('pf') or 0):.2f} | "
                f"{float(oos.get('pf') or 0):.2f} | {ism.get('trades',0)} | "
                f"{float(ism.get('trades_per_week') or 0):.1f} |"
            )

    lines += ["", "## 5. Ranking completo por tipo (mejor score)", ""]
    by_tipo: dict[str, list] = defaultdict(list)
    for r in ok:
        by_tipo[r.get("tipo") or "Unknown"].append(r)
    for tipo in sorted(by_tipo.keys()):
        items = sorted(by_tipo[tipo], key=lambda r: -float(r.get("score") or -999))[:15]
        lines.append(f"### {tipo}")
        lines.append("")
        lines.append("| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |")
        lines.append("|---|---|---:|---:|---:|---:|---:|:---:|")
        for r in items:
            b = r.get("best") or {}
            ism = b.get("is") or {}
            oos = b.get("oos") or {}
            lines.append(
                f"| `{r['symbol']}` | {r.get('best_strategy')} | {float(r.get('score') or 0):.2f} | "
                f"{float(ism.get('pf') or 0):.2f} | {float(oos.get('pf') or 0):.2f} | "
                f"{ism.get('trades',0)} | {float(ism.get('trades_per_week') or 0):.1f} | "
                f"{'YES' if r.get('promising') else 'no'} |"
            )
        lines.append("")

    lines += [
        "## 6. Plan lunes (operativo, demo)",
        "",
        "1. Elegir **máximo 3** de la shortlist (diversificar tipos: ej. 1 metal/índice + 1 FX + 1 crypto).",
        "2. Correr solo en **ventanas ART sugeridas**; fuera de ventana = flat.",
        "3. Lot **0.01**, 1 posición/símbolo, cooldown ≥ 5 min, magic distinto por símbolo.",
        "4. Objetivo: validar si el PF paper ≈ OOS; no optimizar mid-week.",
        "5. Kill-switch: si un símbolo hace DD < −$15 en la semana o PF rolling < 0.8 → apagar.",
        "",
        "## 7. Limitaciones (leer antes de emocionarse)",
        "",
        "- No es walk-forward multiperiodo ni Monte Carlo; es un holdout temporal simple.",
        "- Spread del scan = snapshot; en news el costo real es mayor.",
        "- M1/M5 5y **no disponibles** de forma usable en este terminal → el edge microescala no se midió.",
        "- Escanear ~100+ símbolos implica **data snooping**: algunos 'promising' serán suerte.",
        "- Stocks (7k) excluidos a propósito; solo un puñado curado.",
        "- Futuros con vencimiento (`_U6`, etc.) pueden tener hist. corta / roll → tratar con cuidado.",
        "- Aprobación paper/live formal del repo sigue pidiendo dual-backtest (Vibe + QC); este informe es research MT5.",
        "",
        "## 8. Errores / sin datos",
        "",
    ]
    if not bad:
        lines.append("_Ninguno._")
    else:
        err_counts: dict[str, int] = defaultdict(int)
        for r in bad:
            err_counts[str(r.get("error"))] += 1
        lines.append("| Error | n |")
        lines.append("|---|---:|")
        for k, v in sorted(err_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| `{k}` | {v} |")
        lines.append("")
        lines.append("<details><summary>Símbolos fallidos</summary>")
        lines.append("")
        for r in bad:
            lines.append(f"- `{r.get('symbol')}`: {r.get('error')}")
        lines.append("")
        lines.append("</details>")

    lines += [
        "",
        "## 9. Cómo regenerar",
        "",
        "```bash",
        'python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py"',
        'python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py" --resume',
        'python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py" --report-only',
        "```",
        "",
        f"Progreso parcial: `{PROGRESS.relative_to(HERE).as_posix()}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def load_progress_rows() -> list[dict[str, Any]]:
    if not PROGRESS.exists():
        return []
    rows = []
    for line in PROGRESS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", help="skip symbols already in jsonl")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max symbols to process this run")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)

    if args.report_only:
        rows = load_progress_rows()
        REPORT.write_text(build_report(rows), encoding="utf-8")
        SUMMARY.write_text(
            json.dumps(
                {
                    "n": len(rows),
                    "promising": [r["symbol"] for r in rows if r.get("promising")],
                    "generated": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("REPORT", REPORT)
        return 0

    connect()
    acc = mt5.account_info()
    print(
        f"Connected {getattr(acc,'login',None)} {getattr(acc,'server',None)} "
        f"{getattr(acc,'company',None)}"
    )

    universe = load_universe()
    print(f"Universe size: {len(universe)}")
    skip = done_symbols() if args.resume else set()
    if not args.resume and PROGRESS.exists():
        PROGRESS.unlink()
        skip = set()

    processed = 0
    t0 = time.time()
    for meta in universe:
        sym = meta["symbol"]
        if sym in skip:
            continue
        if args.limit and processed >= args.limit:
            break
        t_sym = time.time()
        try:
            row = analyze_symbol(meta)
        except Exception as e:
            row = {"symbol": sym, "ok": False, "error": f"exception:{e}", **meta}
        append_progress(row)
        processed += 1
        status = "OK" if row.get("ok") else f"FAIL:{row.get('error')}"
        flag = "PROMISING" if row.get("promising") else row.get("best_strategy", "")
        print(
            f"[{processed}] {sym} {status} {flag} "
            f"score={row.get('score', '')} ({time.time()-t_sym:.1f}s)"
        )

    rows = load_progress_rows()
    REPORT.write_text(build_report(rows), encoding="utf-8")
    SUMMARY.write_text(
        json.dumps(
            {
                "n": len(rows),
                "ok": sum(1 for r in rows if r.get("ok")),
                "promising": [r["symbol"] for r in rows if r.get("promising")],
                "elapsed_sec": time.time() - t0,
                "generated": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("DONE symbols", len(rows), "promising", [r["symbol"] for r in rows if r.get("promising")])
    print("REPORT", REPORT)
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
