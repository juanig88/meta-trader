"""User's theory (2026-11-03): 0.01 lot, TP 100 USD of price (+1 USD), wide SL. "Price almost always
comes back to the TP before the SL." Research only, BTCUSDT 15m from Binance.

Entries: every hour on the hour close, BUY and SELL both (no view of the market), and a trend
version that only takes the side of yesterday's daily EMA 20 (below -> sell). Costs as Exness
BTCUSDm: spread 6.4 USD of price (TP needs a 106.4 move of the mid, SL fires 6.4 early), buys pay
swap ~0.178 USD per day at 0.01. A 15m bar that touches both TP and SL counts as a loss.

For a market with no drift, P(TP before SL) = SL / (TP + SL): with SL 1000 that is 91%, and the
expected value is zero before costs. The question is whether BTC does better than that.

    python analyze_tp_chico.py
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from trading.infrastructure.binance_klines import update_csv

DATA = Path("data/binance/BTCUSDT_15m.csv")
TP = 100.0
SPREAD = 6.4
LOT_USD = 0.01  # USD per 1 USD of price at 0.01 lot
SWAP_BUY_DAY = 0.178
SLS = (300.0, 500.0, 1000.0, 2000.0, 9000.0)  # 9000 ~ no SL: a ~90 USD account liquidates there


def first_hit(arr: np.ndarray, start: int, cond) -> int:
    """Index of the first bar from ``start`` where ``cond(arr[k])`` holds; -1 if never."""
    n, step = len(arr), 96
    k = start
    while k < n:
        seg = cond(arr[k:k + step])
        if seg.any():
            return k + int(np.argmax(seg))
        k += step
        step = min(step * 2, 20000)
    return -1


def simulate(df: pd.DataFrame, entries: list[tuple[int, int]], sl: float) -> list[dict]:
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    t = df.index
    out = []
    for i, side in entries:
        e = cl[i]
        if side == 1:
            tp_lvl, sl_lvl = e + TP + SPREAD, e - sl + SPREAD
            k_tp = first_hit(hi, i + 1, lambda x: x >= tp_lvl)
            k_sl = first_hit(lo, i + 1, lambda x: x <= sl_lvl)
        else:
            tp_lvl, sl_lvl = e - TP - SPREAD, e + sl - SPREAD
            k_tp = first_hit(lo, i + 1, lambda x: x <= tp_lvl)
            k_sl = first_hit(hi, i + 1, lambda x: x >= sl_lvl)
        if k_tp < 0 and k_sl < 0:
            continue  # unresolved at the end of the data
        win = k_tp >= 0 and (k_sl < 0 or k_tp < k_sl)
        k = k_tp if win else k_sl
        days = (t[k] - t[i]).total_seconds() / 86400
        worst = (e - lo[i + 1:k + 1].min()) if side == 1 else (hi[i + 1:k + 1].max() - e)
        usd = (TP if win else -sl) * LOT_USD - (SWAP_BUY_DAY * days if side == 1 else 0.0)
        out.append({"day": t[i].date(), "win": win, "usd": usd, "min": days * 1440, "worst": max(0.0, worst) + SPREAD})
    return out


def summary(rs: list[dict], sl: float, seed: int = 5) -> dict:
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r["win"]]
    by_day = defaultdict(list)
    for r in rs:
        by_day[r["day"]].append(r["usd"])
    days = list(by_day.values())
    rnd = random.Random(seed)
    means = sorted(np.mean([x for _ in days for x in rnd.choice(days)]) for _ in range(500))  # day-clustered bootstrap
    mins = sorted(r["min"] for r in wins)
    return {
        "n": len(rs),
        "ganadoras_pct": round(100 * len(wins) / len(rs), 1),
        "teorico_sin_tendencia_pct": round(100 * (sl - SPREAD) / (sl + TP), 1),
        "usd_por_trade": round(float(np.mean([r["usd"] for r in rs])), 3),
        "ic90_usd_por_trade": [round(float(means[25]), 3), round(float(means[474]), 3)],
        "usd_cada_100_trades": round(100 * float(np.mean([r["usd"] for r in rs])), 1),
        "ganadoras_minutos_mediana_p90": [round(mins[len(mins) // 2]), round(mins[int(len(mins) * 0.9)])] if mins else None,
        "ganadoras_que_fueron_500_en_contra_pct": round(100 * sum(r["worst"] >= 500 for r in wins) / max(1, len(wins)), 1),
    }


def main() -> None:
    rows = update_csv(DATA, "BTCUSDT", "15m", 1502942400000)
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    df = df.loc[df.index >= df.index[-1] - pd.Timedelta(days=365)]
    day = df["close"].resample("1D").last()
    ema_prev = day.ewm(span=20, adjust=False).mean().shift(1)
    hourly = [i for i, ts in enumerate(df.index) if ts.minute == 45 and i < len(df) - 1]  # close of the :45 bar = :00
    both = [(i, s) for i in hourly for s in (1, -1)]
    trend = []
    for i in hourly:
        ref = ema_prev.get(df.index[i].floor("1D"))
        if pd.notna(ref):
            trend.append((i, -1 if df["close"].iloc[i] < ref else 1))
    report = {"datos": f"{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}", "precio": [round(df['low'].min()), round(df['high'].max())]}
    for sl in SLS:
        report[f"SL_{int(sl)}"] = {"compra_y_venta_cada_hora": summary(simulate(df, both, sl), sl),
                                   "solo_a_favor_EMA20_diaria": summary(simulate(df, trend, sl), sl)}
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
