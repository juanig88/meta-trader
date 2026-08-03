"""
Quick volatility / structure analysis for NVDA.NAS, MELI.NAS, TSM.NYSE-24.
Computes M15 ATR(14), typical daily range, and what the configured
SL/TP would translate to in $ vs typical bar moves.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FILES = Path(r"C:\Users\juani\Projects\meta-trader\Files")

# Configured SL/TP from BOT_SYMBOL_CONFIG (in points; 1 point = 0.01 USD for these symbols typically)
CFG = {
    "NVDA.NAS": {"sl_points": 195, "tp_points": 520, "sl_atr_mult_old": 1.5, "tp_atr_mult_old": 4.0, "trading_window": "13:30-20:00 UTC"},
    "MELI.NAS": {"sl_points": 1458, "tp_points": 3888, "sl_atr_mult_old": 1.5, "tp_atr_mult_old": 4.0, "trading_window": "13:30-20:00 UTC"},
    "TSM.NYSE-24": {"sl_points": 207, "tp_points": 552, "sl_atr_mult_old": 1.5, "tp_atr_mult_old": 4.0, "trading_window": "13:30-20:00 UTC"},
}


def load_m15(symbol: str) -> list[dict]:
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
                "spread": int(row.get("Spread", 0) or 0),
            })
    return out


def atr_wilder(candles: list[dict], period: int = 14) -> list[float | None]:
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
    a = sum(trs[:period]) / period
    out[period - 1] = a
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def main():
    print(f"{'Symbol':<14} {'Trading hours':>16} {'Avg price':>12} {'Avg M15 ATR':>14} {'ATR % of price':>16} {'M15 bars':>10}")
    print("-" * 90)

    for sym, cfg in CFG.items():
        try:
            candles = load_m15(sym)
        except FileNotFoundError as e:
            print(f"{sym}: missing file {e}")
            continue

        # Filter only bars during trading window (13:30-20:00 UTC)
        active = []
        for c in candles:
            hh = c["ts"].hour
            mm = c["ts"].minute
            t = hh * 60 + mm
            if 13 * 60 + 30 <= t < 20 * 60:
                active.append(c)

        atrs = atr_wilder(active, 14)
        valid_atrs = [a for a in atrs if a is not None]
        avg_atr = statistics.mean(valid_atrs) if valid_atrs else 0.0
        avg_price = statistics.mean(c["c"] for c in active)
        atr_pct = 100 * avg_atr / avg_price if avg_price > 0 else 0.0

        print(f"{sym:<14} {cfg['trading_window']:>16} {avg_price:>12.2f} {avg_atr:>14.4f} {atr_pct:>15.3f}% {len(active):>10}")

        # ATR-based SL/TP for current config vs new proposed config (2.0x SL, 3.5x TP)
        # Note: in points, 1 point = 0.01 USD on NVDA/MELI/TSM CFDs typically.
        # Actually for IC Markets stock CFDs, 1 point varies. Let's just compare ATR-based distances directly.
        old_sl = avg_atr * cfg["sl_atr_mult_old"]
        old_tp = avg_atr * cfg["tp_atr_mult_old"]
        new_sl = avg_atr * 2.0
        new_tp = avg_atr * 3.5
        # Translate to $ assuming typical lot 1 share for NVDA/TSM, 0.1 for MELI
        # (just approximation; user can convert when comparing to USD targets)
        print(f"    Distancias ATR (precio):")
        print(f"      Old SL: {old_sl:.3f}  TP: {old_tp:.3f}  R:R={old_tp/old_sl:.2f}")
        print(f"      New SL: {new_sl:.3f}  TP: {new_tp:.3f}  R:R={new_tp/new_sl:.2f}")
        print(f"    Como % del precio: SL_new={100*new_sl/avg_price:.3f}%  TP_new={100*new_tp/avg_price:.3f}%")

        # Hourly distribution: where are the big moves?
        by_hour = defaultdict(list)
        for c in active:
            rng = c["h"] - c["l"]
            by_hour[c["ts"].hour].append(rng)
        print(f"    Volatilidad por hora (M15 range avg):")
        for h in sorted(by_hour.keys()):
            ranges = by_hour[h]
            print(f"      {h:02d}h UTC: n={len(ranges):4d}  avg_range={statistics.mean(ranges):.3f}  median={statistics.median(ranges):.3f}")

        # Average daily move (sum of M15 ranges in a day)
        days = defaultdict(list)
        for c in active:
            days[c["ts"].date()].append(c["h"] - c["l"])
        daily_total = [sum(v) for v in days.values()]
        if daily_total:
            print(f"    Movimiento diario (suma M15 ranges): avg={statistics.mean(daily_total):.2f} median={statistics.median(daily_total):.2f}")
        print()


if __name__ == "__main__":
    main()
