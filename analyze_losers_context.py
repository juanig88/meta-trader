"""
Align worst trades with M15 candles to see the signal context.

For each losing trade in parsed_trades.json, look at:
- The signal candle (last closed M15 before entry)
- ATR (M15) at entry
- Recent EMA50/200 alignment
- Whether the candle was an exhaustion bar (range vs ATR)
- Drawdown depth into entry price during the trade

The goal: identify the dominant failure mode (chop vs exhaustion vs trend reversal).
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

FILES = Path(r"C:\Users\juani\Projects\meta-trader\Files")
TRADES_JSON = FILES / "parsed_trades.json"


def load_candles(symbol: str) -> list[dict]:
    fp = FILES / f"{symbol}_M15_2025-05-12_2026-06-10.csv"
    out: list[dict] = []
    with open(fp, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = datetime.strptime(row["Datetime"], "%Y.%m.%d %H:%M")
            out.append({
                "ts": ts,
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
                "v": float(row["Volume"]),
            })
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (period + 1)
    e = None
    for v in values:
        e = v if e is None else (v - e) * k + e
        out.append(e)
    # require warmup
    for i in range(min(period, len(out))):
        out[i] = None
    return out


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c["h"] - c["l"])
        else:
            prev_close = candles[i - 1]["c"]
            tr = max(c["h"] - c["l"], abs(c["h"] - prev_close), abs(c["l"] - prev_close))
            trs.append(tr)
    out: list[float | None] = [None] * len(candles)
    if len(trs) < period:
        return out
    # Wilder ATR
    a = sum(trs[:period]) / period
    out[period - 1] = a
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def find_bar_index_at_or_before(candles: list[dict], ts: datetime) -> int:
    lo, hi = 0, len(candles) - 1
    res = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if candles[mid]["ts"] <= ts:
            res = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return res


def categorize_signal_bar(symbol_candles: list[dict], idx: int) -> dict:
    """idx = index of the last closed M15 bar before/at entry."""
    if idx < 60:
        return {"category": "insufficient"}
    closes = [c["c"] for c in symbol_candles]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    atr14 = atr(symbol_candles, 14)

    bar = symbol_candles[idx]
    prev = symbol_candles[idx - 1]
    bar_range = bar["h"] - bar["l"]
    body = abs(bar["c"] - bar["o"])
    body_frac = body / bar_range if bar_range > 0 else 0.0
    upper_wick = bar["h"] - max(bar["o"], bar["c"])
    lower_wick = min(bar["o"], bar["c"]) - bar["l"]
    upper_wick_frac = upper_wick / bar_range if bar_range > 0 else 0.0
    lower_wick_frac = lower_wick / bar_range if bar_range > 0 else 0.0

    a = atr14[idx] or 0.0
    rng_vs_atr = bar_range / a if a > 0 else 0.0

    # Trend: ema50 vs ema200, and price vs ema50
    e50 = ema50[idx]
    e200 = ema200[idx]
    ema_dir = "FLAT"
    if e50 is not None and e200 is not None:
        if e50 > e200 and bar["c"] > e50:
            ema_dir = "UP"
        elif e50 < e200 and bar["c"] < e50:
            ema_dir = "DOWN"

    # Did the bar break previous bar's high/low (entry trigger)?
    break_up = bar["c"] > prev["h"] and bar["c"] > bar["o"]
    break_dn = bar["c"] < prev["l"] and bar["c"] < bar["o"]

    return {
        "category": "ok",
        "bar_range": bar_range,
        "body_frac": body_frac,
        "upper_wick_frac": upper_wick_frac,
        "lower_wick_frac": lower_wick_frac,
        "atr14": a,
        "range_vs_atr": rng_vs_atr,
        "ema50": e50,
        "ema200": e200,
        "ema_dir": ema_dir,
        "broke_prev_high": break_up,
        "broke_prev_low": break_dn,
        "bar_open": bar["o"],
        "bar_close": bar["c"],
        "prev_high": prev["h"],
        "prev_low": prev["l"],
    }


def adverse_excursion(candles: list[dict], entry_idx: int, exit_idx: int, entry_px: float, side: str) -> dict:
    """Max favorable / adverse excursion within the trade window."""
    if entry_idx < 0 or exit_idx < 0 or exit_idx < entry_idx:
        return {"mfe": 0.0, "mae": 0.0}
    mfe = 0.0
    mae = 0.0
    for i in range(entry_idx, min(exit_idx + 1, len(candles))):
        c = candles[i]
        if side == "buy":
            mfe = max(mfe, c["h"] - entry_px)
            mae = min(mae, c["l"] - entry_px)
        else:
            mfe = max(mfe, entry_px - c["l"])
            mae = min(mae, entry_px - c["h"])
    return {"mfe": mfe, "mae": mae}


def main():
    trades = json.loads(TRADES_JSON.read_text(encoding="utf-8"))
    for t in trades:
        t["open_dt"] = datetime.fromisoformat(t["open_dt"])
        t["close_dt"] = datetime.fromisoformat(t["close_dt"])

    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)

    cache_candles: dict[str, list[dict]] = {}

    enriched: list[dict] = []
    for t in trades:
        sym = t["symbol"]
        if sym not in cache_candles:
            cache_candles[sym] = load_candles(sym)
        c = cache_candles[sym]
        # Trade timestamps may have seconds; the bot reads M15 closed bars before entry.
        # Use the bar that closed just before open_dt (so the strict "previous M15 close").
        entry_dt = t["open_dt"]
        signal_dt = entry_dt - timedelta(minutes=15)
        sig_idx = find_bar_index_at_or_before(c, signal_dt)
        entry_idx = find_bar_index_at_or_before(c, entry_dt)
        exit_idx = find_bar_index_at_or_before(c, t["close_dt"])
        ctx = categorize_signal_bar(c, sig_idx)
        exc = adverse_excursion(c, entry_idx, exit_idx, t["open_px"], t["side"])
        enriched.append({**t, "ctx": ctx, "exc": exc})

    # Aggregate features of LOSING trades vs WINNING trades
    losers = [t for t in enriched if t["net"] <= 0 and t["ctx"]["category"] == "ok"]
    winners = [t for t in enriched if t["net"] > 0 and t["ctx"]["category"] == "ok"]

    def agg(name: str, ts: list[dict]):
        if not ts:
            print(f"  {name}: empty")
            return
        def avg(key, sub="ctx"):
            vals = [t[sub][key] for t in ts if t[sub].get(key) is not None]
            return statistics.mean(vals) if vals else 0.0
        def share(predicate):
            n = sum(1 for t in ts if predicate(t))
            return 100 * n / len(ts)

        print(f"\n=== {name} (n={len(ts)}) ===")
        print(f"  avg body_frac       = {avg('body_frac'):.3f}")
        print(f"  avg upper_wick_frac = {avg('upper_wick_frac'):.3f}")
        print(f"  avg lower_wick_frac = {avg('lower_wick_frac'):.3f}")
        print(f"  avg range_vs_atr    = {avg('range_vs_atr'):.2f}")
        print(f"  % bar exhausted (range>2*ATR) = {share(lambda t: t['ctx']['range_vs_atr'] > 2.0):.1f}%")
        print(f"  % ema_dir FLAT      = {share(lambda t: t['ctx']['ema_dir'] == 'FLAT'):.1f}%")
        print(f"  % ema_dir aligned w/ side = {share(lambda t: (t['ctx']['ema_dir']=='UP' and t['side']=='buy') or (t['ctx']['ema_dir']=='DOWN' and t['side']=='sell')):.1f}%")
        print(f"  % ema_dir COUNTER side    = {share(lambda t: (t['ctx']['ema_dir']=='UP' and t['side']=='sell') or (t['ctx']['ema_dir']=='DOWN' and t['side']=='buy')):.1f}%")
        # MFE/MAE in price units → convert to "%" of entry
        mfe_pct = [100 * t["exc"]["mfe"] / t["open_px"] for t in ts]
        mae_pct = [100 * abs(t["exc"]["mae"]) / t["open_px"] for t in ts]
        print(f"  avg MFE %           = {statistics.mean(mfe_pct):.3f}%")
        print(f"  avg MAE %           = {statistics.mean(mae_pct):.3f}%")
        print(f"  avg trade duration (min) = {statistics.mean(t['duration_min'] for t in ts):.1f}")

    agg("LOSERS", losers)
    agg("WINNERS", winners)

    print("\n=== TOP 10 WORST LOSERS with context ===")
    worst = sorted(enriched, key=lambda t: t["net"])[:10]
    for t in worst:
        ctx = t["ctx"]
        if ctx["category"] != "ok":
            continue
        print(
            f"  {t['open_dt']} {t['symbol']} {t['side']:4s} "
            f"net={t['net']:+.2f} | bar range%={100*ctx['bar_range']/t['open_px']:.2f} "
            f"body={ctx['body_frac']:.2f} upWick={ctx['upper_wick_frac']:.2f} "
            f"loWick={ctx['lower_wick_frac']:.2f} rng/ATR={ctx['range_vs_atr']:.2f} "
            f"ema_dir={ctx['ema_dir']} brokeHi={ctx['broke_prev_high']} brokeLo={ctx['broke_prev_low']} "
            f"MFE%={100*t['exc']['mfe']/t['open_px']:.2f} MAE%={100*abs(t['exc']['mae'])/t['open_px']:.2f}"
        )


if __name__ == "__main__":
    main()
