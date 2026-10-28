"""V2: Donchian 20/10 + SMA200 en un ciclo que el diseño nunca vio (BTC Bitstamp 2012-2018, LTC 2015-2018). Research only.

Criterio fijado antes de mirar: pasa si la base 20/10 en BTC da CAGR neto > 0, drawdown menor que
buy & hold CFD, y >= 7 de 9 vecinos (entrada 10-55, salida 5-20) con CAGR > 0. Mismos costos que
analyze_swing_regime.py (0.10% por lado + swap 21%/anio).

    py analyze_swing_pre2018.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_swing_regime import sleeve, stats, yearly

DATA = Path("logs/binance/daily_pre2018")
OUT = Path("analyze_swing_pre2018_out.json")
WINDOWS = {"BTCUSDT": ("2012-03-19", "2018-03-04"), "LTCUSDT": ("2015-04-05", "2018-03-04")}
OVERLAP = ("2018-03-05", "2026-09-30")
GRID = [(i, o) for i in (10, 20, 30, 55) for o in (5, 10, 20) if o < i]


def don_pos(d: pd.DataFrame, n_in: int, n_out: int) -> pd.Series:
    c = d.close.to_numpy()
    s2 = d.close.rolling(200).mean().to_numpy()
    hi = d.high.rolling(n_in).max().shift(1).to_numpy()
    lo = d.low.rolling(n_out).min().shift(1).to_numpy()
    out = np.zeros(len(d))
    st = 0.0
    for i in range(len(d)):
        if np.isnan(s2[i]) or np.isnan(hi[i]) or np.isnan(lo[i]):
            st = 0.0
        elif st == 0.0 and c[i] > hi[i] and c[i] > s2[i]:
            st = 1.0
        elif st == 1.0 and (c[i] < lo[i] or c[i] < s2[i]):
            st = 0.0
        out[i] = st
    return pd.Series(out, index=d.index).where(~np.isnan(s2))


def main() -> int:
    out: dict = {"criterion": "base CAGR>0, DD < bh_cfd DD, >=7/9 vecinos CAGR>0 (BTC)", "symbols": {}}
    for pair, (a, b) in WINDOWS.items():
        d = pd.read_csv(DATA / f"{pair}_1d.csv", index_col="date", parse_dates=True)
        d = d[(d.close > 0) & (d.high >= d.low)]
        ones = pd.Series(1.0, index=d.index).where(d.close.rolling(200).mean().notna())
        res = {}
        for name, pos, kind in (("bh", ones, "bh"), ("bh_cfd", ones, "bh_cfd")):
            r, _ = sleeve(d, pos, kind)
            res[name] = stats(r, a, b) | {"yearly": yearly(r.loc[a:b])}
        grid = {}
        for n_in, n_out in GRID:
            pos = don_pos(d, n_in, n_out)
            r, tr = sleeve(d, pos, "don")
            s = stats(r, a, b, pos, tr)
            grid[f"{n_in}/{n_out}"] = s
            if (n_in, n_out) == (20, 10):
                res["don_20_10"] = s | {"yearly": yearly(r.loc[a:b])}
                res["don_20_10_overlap_2018_2026"] = stats(r, *OVERLAP)
        res["grid_cagr"] = {k: v["cagr_pct"] for k, v in grid.items()}
        res["grid_sharpe"] = {k: v["sharpe"] for k, v in grid.items()}
        res["grid_positive"] = int(sum(v["cagr_pct"] > 0 for v in grid.values()))
        base = res["don_20_10"]
        res["pass"] = bool(base["cagr_pct"] > 0 and base["max_dd_pct"] > res["bh_cfd"]["max_dd_pct"]
                           and res["grid_positive"] >= 7)
        out["symbols"][pair] = res
        print(f"\n=== {pair} {a} -> {b} ===")
        for k in ("bh", "bh_cfd", "don_20_10"):
            print(f"{k:10s}", {x: res[k].get(x) for x in ("cagr_pct", "sharpe", "max_dd_pct", "time_in_mkt", "trades", "wr", "pf", "avg_hold_d")})
        print("yearly", pd.DataFrame({k: res[k]["yearly"] for k in ("bh", "bh_cfd", "don_20_10")}).T.to_string())
        print("grid CAGR", res["grid_cagr"])
        print("grid Sharpe", res["grid_sharpe"])
        print("overlap 2018-2026 (sanity vs Binance)", {x: res["don_20_10_overlap_2018_2026"].get(x) for x in ("cagr_pct", "sharpe", "max_dd_pct")})
        print("PASS" if res["pass"] else "FAIL")
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
