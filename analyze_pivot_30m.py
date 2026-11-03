"""Count the "30 min pivot" on BTCUSDT (Binance spot, UTC days) since 2017. Research only.

Rule as the video states it (long; the short is the mirror):
1. daily: the last ``streak`` closed days all had their low above that day's EMA
2. today: price trades below yesterday's EMA (the "sweep"; yesterday's value, no hindsight)
3. 30m: after the sweep, take the first green candle after the latest low of the day;
   enter when price trades above its high
4. stop at the low of the day so far; skip when that stop is wider than ADR/2
   (ADR = mean daily high/low range of the 20 days before)

The video gives no exit. Primary exit (fixed before running): the UTC day close, which is
how it scores "+2R". Two others are reported, not chosen: a 2R target, and holding up to
5 days. Baseline: random 30m entries on random days with the same trend side, the same
stop rule, the same ADR filter and the same exit. Costs: 0.04% of price per round trip.

    python analyze_pivot_30m.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pandas as pd

from trading.infrastructure.binance_klines import update_csv

DATA = Path("data/binance/BTCUSDT_15m.csv")
COST = 0.0004  # Exness BTC spread ~6-20 USD at ~77k, rounded up
EXITS = ("cierre_dia", "objetivo_2R", "hasta_5_dias")


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = update_csv(DATA, "BTCUSDT", "15m", 1502942400000)
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    m30 = df.resample("30min").agg(agg).dropna()
    day = df.resample("1D").agg(agg).dropna()
    return m30, day.iloc[:-1]  # drop the day still forming


def daily_context(day: pd.DataFrame, n_ema: int) -> pd.DataFrame:
    d = day.copy()
    d["ema"] = d["close"].ewm(span=n_ema, adjust=False).mean()
    d["ema_prev"] = d["ema"].shift(1)
    d["adr"] = ((d["high"] / d["low"] - 1).rolling(20).mean()).shift(1)
    for side, above in (("long", d["low"] > d["ema"]), ("short", d["high"] < d["ema"])):
        run, out = 0, []
        for ok in above:
            out.append(run)  # streak up to yesterday
            run = run + 1 if ok else 0
        d[f"streak_{side}"] = out
    return d


def trade(bars: pd.DataFrame, later: pd.DataFrame, side: int, level: float | None, adr: float,
          start: int = 0) -> dict | None:
    """Walk one day of 30m bars. ``level`` None = random baseline: enter at bar ``start``'s open."""
    extreme = None  # low of the day (long) / high of the day (short) so far
    swept = level is None
    trigger = None
    entry = stop = None
    k_in = None
    o, h, l, c = (bars[x].to_numpy() for x in ("open", "high", "low", "close"))
    for k in range(len(bars)):
        lo, hi = (l[k], h[k]) if side == 1 else (-h[k], -l[k])  # mirror the short into a long
        op, cl = (o[k], c[k]) if side == 1 else (-o[k], -c[k])
        if level is None:
            if k == start and extreme is not None:
                entry, stop, k_in = op, extreme, k
                break
        elif swept and trigger is not None and hi > trigger:
            entry, stop, k_in = max(trigger, op), extreme, k
            break
        if extreme is None or lo < extreme:
            extreme = lo
            trigger = None  # a new low resets the green candle we wait for
        if level is not None and lo < (level if side == 1 else -level):
            swept = True
        if swept and cl > op and trigger is None:
            trigger = hi
    if entry is None or entry <= stop:
        return None
    risk = entry - stop
    risk_pct = risk / abs(entry)
    if risk_pct > adr / 2:
        return {"filtered": True}
    cost_r = COST * abs(entry) / risk
    out = {}
    for name in EXITS:
        seq = bars.iloc[k_in:] if name != "hasta_5_dias" else pd.concat([bars.iloc[k_in:], later])
        res = None
        for j, (_, b) in enumerate(seq.iterrows()):
            lo, hi, cl = (b["low"], b["high"], b["close"]) if side == 1 else (-b["high"], -b["low"], -b["close"])
            if lo <= stop:
                res = -1.0
                break
            if name == "objetivo_2R" and hi >= entry + 2 * risk:
                res = 2.0
                break
        if res is None:
            last = seq.iloc[-1]["close"] * (1 if side == 1 else -1)
            res = (last - entry) / risk
        out[name] = round(res - cost_r, 3)
    return {"filtered": False, "entry": abs(entry), "stop": abs(stop), "risk_pct": round(risk_pct * 100, 2),
            "r": out}


def scan(m30: pd.DataFrame, d: pd.DataFrame, streak: int, seed: int = 7, n_random: int = 4000) -> dict:
    by_day = {k: g for k, g in m30.groupby(m30.index.floor("1D"))}
    days = list(d.index)
    setups, filtered = [], 0
    for i, ts in enumerate(days):
        row = d.loc[ts]
        if pd.isna(row["adr"]) or ts not in by_day:
            continue
        later = pd.concat([by_day[x] for x in days[i + 1:i + 5] if x in by_day]) if i + 1 < len(days) else by_day[ts].iloc[:0]
        for side, name in ((1, "long"), (-1, "short")):
            if row[f"streak_{name}"] < streak:
                continue
            swept = row["low"] < row["ema_prev"] if side == 1 else row["high"] > row["ema_prev"]
            if not swept:
                continue
            t = trade(by_day[ts], later, side, row["ema_prev"], row["adr"])
            if t is None:
                continue
            if t["filtered"]:
                filtered += 1
                continue
            setups.append({"day": ts.date().isoformat(), "side": name, **t})
    rnd = random.Random(seed)
    trend_days = {1: [ts for ts in days if d.loc[ts, "close"] > d.loc[ts, "ema_prev"] and not pd.isna(d.loc[ts, "adr"])],
                  -1: [ts for ts in days if d.loc[ts, "close"] < d.loc[ts, "ema_prev"] and not pd.isna(d.loc[ts, "adr"])]}
    base = []
    sides = [1 if s["side"] == "long" else -1 for s in setups] or [1, -1]
    tries = 0
    while len(base) < n_random and tries < n_random * 5:
        tries += 1
        side = rnd.choice(sides)
        ts = rnd.choice(trend_days[side])
        if ts not in by_day or len(by_day[ts]) < 10:
            continue
        i = days.index(ts)
        later = pd.concat([by_day[x] for x in days[i + 1:i + 5] if x in by_day]) if i + 1 < len(days) else by_day[ts].iloc[:0]
        t = trade(by_day[ts], later, side, None, d.loc[ts, "adr"], start=rnd.randrange(2, len(by_day[ts]) - 2))
        if t and not t["filtered"]:
            base.append({**t["r"], "risk_pct": t["risk_pct"]})
    return {"setups": setups, "filtered_adr": filtered, "baseline": base}


def summary(rs: list[float], seed: int = 11) -> dict:
    if not rs:
        return {"n": 0}
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choice(rs) for _ in rs) / len(rs) for _ in range(2000))
    return {"n": len(rs), "media_R": round(sum(rs) / len(rs), 3), "ganadoras_pct": round(100 * sum(r > 0 for r in rs) / len(rs), 1),
            "ic90": [round(means[100], 3), round(means[1899], 3)]}


def main() -> None:
    m30, day = load()
    report = {"datos": f"{day.index[0].date()} a {day.index[-1].date()}", "dias": len(day)}
    for n_ema, streak in ((20, 30), (20, 20), (10, 20), (50, 30)):  # first = primary, the rest only for context
        d = daily_context(day, n_ema)
        res = scan(m30, d, streak)
        key = f"EMA{n_ema}_racha{streak}"
        risks = [s["risk_pct"] for s in res["setups"]]
        if risks:  # same stop widths as the setups, so costs weigh the same in R
            res["baseline"] = [b for b in res["baseline"] if min(risks) <= b["risk_pct"] <= max(risks)]
        report[key] = {
            "setups": len(res["setups"]), "descartados_por_ADR": res["filtered_adr"],
            **{f"setup_{e}": summary([s["r"][e] for s in res["setups"]]) for e in EXITS},
            **{f"azar_{e}": summary([b[e] for b in res["baseline"]]) for e in EXITS},
        }
        if (n_ema, streak) == (20, 30):
            report[key]["lista"] = res["setups"]
        if (n_ema, streak) == (20, 20):
            report[key]["lista"] = [{k: s[k] for k in ("day", "side", "risk_pct", "r")} for s in res["setups"]]
    d = daily_context(day, 20)
    tail = d.loc["2026-10-10":, ["open", "high", "low", "close", "ema", "streak_long", "streak_short"]].round(0)
    report["btc_ultimos_dias"] = {str(k.date()): v for k, v in tail.to_dict("index").items()}
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
