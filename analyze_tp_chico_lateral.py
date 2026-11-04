"""TP chico only in a sideways market, up to 2 positions: one shot on the holdout.

Pre-registration: trading/docs/PREREG_TP_CHICO_LATERAL.md (SHA-256 printed with the results).
Decides on 2025-11-04 -> 2026-11-04 in real USD (0.01 lot, +1 / -10); the 2018-2025 train,
where the idea came from, is shown only as a diagnostic. Fills and exits as in
analyze_tp_chico_filtros.py (bid candles, spread held for the trade, both hit in a bar = SL).

    python analyze_tp_chico_lateral.py
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_tp_chico_filtros import TP_PCT, Market, binance, stats, trade
from trading.domain.impulso_m1 import Bar
from trading.domain.rebound_zones import find_zones

PREREG = Path("trading/docs/PREREG_TP_CHICO_LATERAL.md")
OUT = Path("trading/docs/reports/tp_chico_lateral_holdout.json")
HOLD = ("2025-11-04", "2026-11-05")
TRAIN = ("2018-01-01", "2025-11-04")
ZONE_MAX = 0.01
BIG_MULT, BIG_LAST, BIG_REF = 2.5, 4, 96
RUN_BARS, RUN_MAX = 16, 0.01
MAX_OPEN, MIN_GAP_USD = 2, 100.0
CELLS = ("L1_lado_tendencia", "L2_lado_zona")


def zone_info(m: Market, i: int) -> tuple[float, int]:
    """(distance to the nearest zone edge as a fraction, side that zone implies: -1 sell, 1 buy)."""
    day = m.t[i].floor("1D")
    if day not in m._zones:
        prior = m.h1.loc[m.h1.index < day].iloc[-1440:]
        bars = [Bar(o, h, l, c) for o, h, l, c in prior[["open", "high", "low", "close"]].to_numpy()]
        m._zones[day] = find_zones(bars) if len(bars) >= 500 else None
    zones, p = m._zones[day], m.cl[i]
    if not zones:
        return float("nan"), 0
    best, side = float("inf"), 0
    for z in zones:
        if z.low <= p <= z.high:
            d, s = 0.0, (-1 if z.kind == "techo" else 1)
        elif p < z.low:
            d, s = (z.low - p) / p, -1  # zone above: sell into it
        else:
            d, s = (p - z.high) / p, 1  # zone below: buy off it
        if d < best:
            best, side = d, s
    return best, side


def calm(m: Market, i: int, rng: np.ndarray) -> bool:
    if i < BIG_REF + BIG_LAST or i < RUN_BARS:
        return False
    ref = np.median(rng[i - BIG_LAST - BIG_REF + 1:i - BIG_LAST + 1])
    if ref <= 0 or rng[i - BIG_LAST + 1:i + 1].max() > BIG_MULT * ref:
        return False
    return abs(m.cl[i] / m.cl[i - RUN_BARS] - 1) <= RUN_MAX


def run(m: Market, a: str, b: str, absolute: bool) -> dict:
    lo, hi = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
    rng = m.hi - m.lo
    idx = [i for i in range(len(m.t) - 1) if lo <= m.t[i] < hi]
    out = {}
    for cell in CELLS:
        trades, open_ = [], []  # open_: (exit index, entry price)
        both_open = 0
        for i in idx:
            open_ = [o for o in open_ if o[0] > i]
            if len(open_) >= MAX_OPEN:
                continue
            gap = MIN_GAP_USD if absolute else TP_PCT * m.cl[i]
            if any(abs(m.cl[i] - e) < gap for _, e in open_):
                continue
            dist, zside = zone_info(m, i)
            if not dist <= ZONE_MAX or not calm(m, i, rng):
                continue
            side = m.side(i) if cell == CELLS[0] else zside
            if side == 0:
                continue
            t = trade(m, i, side, absolute)
            if t is None:
                continue
            both_open += int(bool(open_))
            trades.append(t)
            open_.append((t["exit"], m.cl[i]))
        by_day = defaultdict(float)
        for t in trades:
            by_day[t["day"]] += t["net"]
        st = stats(trades)
        if st.get("n"):
            st.update({"operaciones_por_dia": round(len(trades) / max(1, (hi - lo).days), 2),
                       "peor_dia": round(min(by_day.values()), 3), "mejor_dia": round(max(by_day.values()), 3),
                       "con_2_abiertas": both_open, "total": round(sum(t["net"] for t in trades), 2)})
        out[cell] = st
    return out


def fmt(d: dict, scale: float = 1.0) -> dict:
    res = {}
    for cell, st in d.items():
        st = dict(st)
        if st.get("n"):
            st["media"] = round(st["media"] * scale, 3)
            st["ic95"] = [round(x * scale, 3) for x in st["ic95"]]
            for k in ("peor_dia", "mejor_dia", "total"):
                st[k] = round(st[k] * scale, 2)
            st["aprueba"] = (st["n"] >= 200 and st["media"] > 0 and st["p_una_cola"] < 0.05 / len(CELLS)
                             and (st["profit_factor"] or 0) > 1) if scale == 1.0 else None
        res[cell] = st
    return res


def main() -> None:
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    lock = PREREG.with_name(PREREG.name + ".sha256")
    report = {"prereg_sha256": sha, "prereg_lock_ok": lock.exists() and sha in lock.read_text()}
    m = binance()
    if OUT.exists():
        report["holdout"] = f"ya consumido: {OUT}"
    else:
        m.spr = np.full(len(m.cl), 6.4)
        report["holdout_usd"] = fmt(run(m, *HOLD, absolute=True))
        OUT.write_text(json.dumps({"prereg_sha256": sha, **report["holdout_usd"]}, indent=1, default=str), encoding="utf-8")
        m.spr = np.full(len(m.cl), 10.0)
        report["diag_holdout_spread_10"] = {c: {k: v for k, v in s.items() if k in ("n", "ganadoras_pct", "media", "total")}
                                            for c, s in fmt(run(m, *HOLD, absolute=True)).items()}
    m = binance()
    report["diag_train_usd_a_77k"] = {c: {k: v for k, v in s.items() if k != "aprueba"}
                                      for c, s in fmt(run(m, *TRAIN, absolute=False), scale=770).items()}
    report["moneda_pct"] = 90.91
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
