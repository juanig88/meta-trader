"""TP chico / SL amplio with the user's filters (cycle of 10, trend, far from zones).

Pre-registration: trading/docs/PREREG_TP_CHICO.md (its SHA-256 is printed next to the results).
Part 1 decides: train 2018-01-01 -> 2025-11-03 on Binance 15m, TP 0.13% / SL 1.3%. Part 2 opens the
holdout once, only for cells that passed. Part 3 (asked by the user, diagnostic only): the last
30 days of real Exness BTCUSDm M1 with each candle's spread, TP 100 / SL 1000 USD, 0.01 lot.

Candles are bid prices; the spread ``s`` of the entry bar is held for the whole trade:
sell fills at bid ``e``, TP when ask <= e - tp (low <= e - tp - s), SL when ask >= e + sl;
buy fills at ask e + s, TP when bid >= e + s + tp, SL when bid <= e + s - sl. A bar that
touches both counts as SL. Binance spot (no spread) gets a fixed 0.0083% as ``s``.

    python analyze_tp_chico_filtros.py
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from trading.domain.impulso_m1 import Bar
from trading.domain.rebound_zones import find_zones
from trading.infrastructure.binance_klines import read_csv

PREREG = Path("trading/docs/PREREG_TP_CHICO.md")
HOLDOUT_OUT = Path("trading/docs/reports/tp_chico_holdout.json")
TP_PCT, SL_PCT = 0.0013, 0.013
SPREAD_PCT = 0.000083
SWAP_PCT_DAY = 0.000231  # buys only
SWAP_USD_DAY = 0.178  # 0.01 lot BTCUSDm
TRAIN = ("2018-01-01", "2025-11-04")
HOLD = ("2025-11-04", "2026-11-05")
CELLS = ("C1_ciclo10", "C2_tendencia", "C3_zona_1pct", "C4_zona_2pct", "C5_zona_5pct")
ZONE_MIN = {"C3_zona_1pct": 0.01, "C4_zona_2pct": 0.02, "C5_zona_5pct": 0.05}
CYCLE = 9


class Market:
    def __init__(self, bars: pd.DataFrame, h1: pd.DataFrame, spread: np.ndarray):
        self.t = bars.index
        self.hi, self.lo, self.cl = (bars[c].to_numpy() for c in ("high", "low", "close"))
        self.spr = spread
        step = self.t[1] - self.t[0]
        self.entries = [i for i, ts in enumerate(self.t[:-1]) if (ts + step).minute == 0]
        closes = h1["close"].resample("1D").last().dropna()
        self.ema_prev = closes.ewm(span=20, adjust=False).mean().shift(1)
        self.h1 = h1
        self._zones: dict = {}

    def side(self, i: int) -> int:
        ref = self.ema_prev.get(self.t[i].floor("1D"))
        if ref is None or pd.isna(ref):
            return 0
        return -1 if self.cl[i] < ref else 1

    def zone_distance(self, i: int) -> float:
        """Distance from the price to the nearest zone edge, as a fraction (0 inside a zone)."""
        day = self.t[i].floor("1D")
        if day not in self._zones:
            prior = self.h1.loc[self.h1.index < day].iloc[-1440:]
            bars = [Bar(o, h, l, c) for o, h, l, c in prior[["open", "high", "low", "close"]].to_numpy()]
            self._zones[day] = find_zones(bars) if len(bars) >= 500 else None
        zones = self._zones[day]
        if zones is None:
            return float("nan")
        p = self.cl[i]
        if not zones:
            return 1.0
        return min(0.0 if z.low <= p <= z.high else min(abs(p - z.low), abs(p - z.high)) / p for z in zones)


def first_hit(arr: np.ndarray, start: int, cond) -> int:
    n, step, k = len(arr), 240, start
    while k < n:
        seg = cond(arr[k:k + step])
        if seg.any():
            return k + int(np.argmax(seg))
        k += step
        step = min(step * 2, 50000)
    return -1


def trade(m: Market, i: int, side: int, absolute: bool) -> dict | None:
    e, s = m.cl[i], m.spr[i]
    tp, sl = (100.0, 1000.0) if absolute else (TP_PCT * e, SL_PCT * e)
    if side == 1:
        k_tp = first_hit(m.hi, i + 1, lambda x: x >= e + s + tp)
        k_sl = first_hit(m.lo, i + 1, lambda x: x <= e + s - sl)
    else:
        k_tp = first_hit(m.lo, i + 1, lambda x: x <= e - tp - s)
        k_sl = first_hit(m.hi, i + 1, lambda x: x >= e + sl - s)
    if k_tp < 0 and k_sl < 0:
        return None
    win = k_tp >= 0 and (k_sl < 0 or k_tp < k_sl)
    k = k_tp if win else k_sl
    days = (m.t[k] - m.t[i]).total_seconds() / 86400
    gross = tp if win else -sl
    if absolute:
        net = gross * 0.01 - (SWAP_USD_DAY * days if side == 1 else 0.0)
    else:
        net = gross / e - (SWAP_PCT_DAY * days if side == 1 else 0.0)
    return {"day": m.t[i].date(), "side": side, "win": win, "net": net, "exit": k, "min": days * 1440}


def run_cells(m: Market, idx: list[int], absolute: bool) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {c: [] for c in CELLS}
    out["C0_azar_ambos_lados"] = []
    for i in idx:
        for side in (1, -1):
            t = trade(m, i, side, absolute)
            if t:
                out["C0_azar_ambos_lados"].append(t)
        side = m.side(i)
        if side == 0:
            continue
        t = trade(m, i, side, absolute)
        if t is None:
            continue
        out["C2_tendencia"].append(t)
        dist = m.zone_distance(i)
        for c, need in ZONE_MIN.items():
            if dist == dist and dist >= need:
                out[c].append(t)
    out["C1_ciclo10"], out["streak_diag"] = serial(m, idx, absolute)
    return out


def serial(m: Market, idx: list[int], absolute: bool) -> tuple[list[dict], list[dict]]:
    """One position at a time with the trend. C1 skips the entry after 9 wins in a row.

    Also returns the no-skip serial run with the win streak before each trade (diagnostic).
    """
    res = {}
    for skip in (True, False):
        trades, busy_until, streak = [], -1, 0
        for i in idx:
            if i <= busy_until:
                continue
            side = m.side(i)
            if side == 0:
                continue
            if skip and streak >= CYCLE:
                streak = 0
                continue
            t = trade(m, i, side, absolute)
            if t is None:
                break
            t["streak_before"] = streak
            trades.append(t)
            busy_until = t["exit"]
            streak = streak + 1 if t["win"] else 0
        res[skip] = trades
    return res[True], res[False]


def stats(rs: list[dict], seed: int = 7, iters: int = 2000) -> dict:
    if not rs:
        return {"n": 0}
    net = np.array([r["net"] for r in rs])
    by_day = defaultdict(list)
    for r in rs:
        by_day[r["day"]].append(r["net"])
    sums = np.array([sum(v) for v in by_day.values()])
    counts = np.array([len(v) for v in by_day.values()])
    pick = np.random.default_rng(seed).integers(0, len(sums), size=(iters, len(sums)))  # whole days
    means = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    pos, neg = net[net > 0].sum(), -net[net < 0].sum()
    wins = [r["min"] for r in rs if r["win"]]
    return {"n": len(rs), "ganadoras_pct": round(100 * float(np.mean([r["win"] for r in rs])), 2),
            "media": float(net.mean()), "ic95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_una_cola": float(np.mean(means <= 0)), "profit_factor": round(float(pos / neg), 3) if neg else None,
            "compras": int(sum(r["side"] == 1 for r in rs)), "ventas": int(sum(r["side"] == -1 for r in rs)),
            "minutos_ganadoras_mediana": round(float(np.median(wins))) if wins else None}


def as_usd(st: dict) -> dict:
    """Percent-of-price results shown as USD with 0.01 lot at 77,000 (x 770)."""
    if not st.get("n"):
        return st
    st = dict(st)
    st["usd_por_trade_a_77k"] = round(st.pop("media") * 770, 3)
    st["ic95_usd"] = [round(x * 770, 3) for x in st.pop("ic95")]
    return st


def passes(st: dict) -> bool:
    return st.get("n", 0) >= 200 and st["media"] > 0 and st["p_una_cola"] < 0.01 / len(CELLS) and (st["profit_factor"] or 0) > 1


def streak_table(rs: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rs:
        by[min(r["streak_before"], CYCLE)].append(r["win"])
    return {f"tras_{k}{'+' if k == CYCLE else ''}_ganadoras": {"n": len(v), "ganadoras_pct": round(100 * np.mean(v), 1)}
            for k, v in sorted(by.items())}


def binance() -> Market:
    def load(path: str) -> pd.DataFrame:
        df = pd.DataFrame(read_csv(Path(path)))
        df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
        return df[["open", "high", "low", "close"]]
    bars, h1 = load("data/binance/BTCUSDT_15m.csv"), load("data/binance/BTCUSDT_1h.csv")
    return Market(bars, h1, bars["close"].to_numpy() * SPREAD_PCT)


def exness() -> Market:
    def load(name: str) -> pd.DataFrame:
        df = pd.read_csv(f"data/exness/BTCUSDm_{name}.csv")
        df.index = pd.to_datetime(df["time"], unit="s", utc=True)
        return df
    m1, h1 = load("M1"), load("H1")
    return Market(m1[["open", "high", "low", "close"]], h1[["open", "high", "low", "close"]], m1["spread"].to_numpy())


def window(m: Market, a: str, b: str) -> list[int]:
    lo, hi = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
    return [i for i in m.entries if lo <= m.t[i] < hi]


def main() -> None:
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    lock = PREREG.with_name(PREREG.name + ".sha256")
    report: dict = {"prereg_sha256": sha, "prereg_lock_ok": lock.exists() and sha in lock.read_text()}
    m = binance()
    train = run_cells(m, window(m, *TRAIN), absolute=False)
    tr = {c: stats(train[c]) for c in CELLS}
    report["1_train"] = {c: {**as_usd(tr[c]), "aprueba": passes(tr[c])} for c in CELLS}
    report["1_train_diag"] = {"C0_azar_ambos_lados": as_usd(stats(train["C0_azar_ambos_lados"])),
                              "ciclo_serie_sin_saltear": streak_table(train["streak_diag"]),
                              "moneda_pct": round(100 * SL_PCT / (TP_PCT + SL_PCT), 2)}
    passed = [c for c in CELLS if passes(tr[c])]
    if not passed:
        report["2_holdout"] = "no se abre: ninguna celda aprobó el train"
    elif HOLDOUT_OUT.exists():
        report["2_holdout"] = f"ya consumido: {HOLDOUT_OUT}"
    else:
        hold = run_cells(m, window(m, *HOLD), absolute=False)
        report["2_holdout"] = {c: as_usd(stats(hold[c])) for c in passed}
        HOLDOUT_OUT.write_text(json.dumps({"prereg_sha256": sha, **report["2_holdout"]}, indent=1, default=str), encoding="utf-8")
    x = exness()
    end = x.t[-1]
    month = run_cells(x, [i for i in x.entries if x.t[i] >= end - pd.Timedelta(days=30)], absolute=True)
    report["3_ultimo_mes_exness_usd"] = {
        "periodo": f"{(end - pd.Timedelta(days=30)):%Y-%m-%d} a {end:%Y-%m-%d %H:%M}",
        **{c: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats(month[c]).items()}
           for c in ("C0_azar_ambos_lados", *CELLS)},
        "ciclo_serie_sin_saltear": streak_table(month["streak_diag"]),
        "moneda_pct": round(100 * 1000 / 1100, 2)}
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
