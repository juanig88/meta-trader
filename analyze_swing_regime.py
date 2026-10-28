"""Modulo multi-dia con filtro de regimen: captura el ciclo alcista? BTC/ETH desde 2018 + altcoins XBTFX. Research only.

Estrategias clasicas, parametros fijos (no ajustados), solo largos, senal al cierre diario:
  bh      buy & hold spot (sin swap) y bh_cfd (con swap XBTFX)
  r200    largo si close > SMA200
  gx      largo si SMA50 > SMA200
  don     entra con close > max 20d previo y close > SMA200; sale con close < min 10d previo o < SMA200
Costos: 0.10% por lado (spread mediano XBTFX 0.197%) + swap 21%/anio del nocional mientras hay
posicion (MT5 2026-10-06, igual largo y corto). Exposicion 1x por manga, cartera equiponderada.
Universo = lista XBTFX de hoy -> sesgo de supervivencia en altcoins.

    py analyze_swing_regime.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("logs/binance/daily")
OUT = Path("analyze_swing_regime_out.json")
COST_SIDE = 0.0010
SWAP_DAY = 0.21 / 365
WARMUP = 200
PERIODS = {"full": ("2018-03-05", "2026-09-30"), "A_2018_2023": ("2018-03-05", "2023-08-31"),
           "B_2023_2026": ("2023-09-01", "2026-09-30")}
MAJORS = ("BTCUSDT", "ETHUSDT")
STRATS = ("bh", "bh_cfd", "r200", "gx", "don")


def positions(d: pd.DataFrame) -> dict[str, pd.Series]:
    c = d.close
    sma200, sma50 = c.rolling(200).mean(), c.rolling(50).mean()
    hi20, lo10 = d.high.rolling(20).max().shift(1), d.low.rolling(10).min().shift(1)
    pos = {"bh": pd.Series(1.0, index=d.index), "bh_cfd": pd.Series(1.0, index=d.index),
           "r200": (c > sma200).astype(float), "gx": (sma50 > sma200).astype(float)}
    don = np.zeros(len(d))
    state = 0.0
    cc, s2, h20, l10 = c.to_numpy(), sma200.to_numpy(), hi20.to_numpy(), lo10.to_numpy()
    for i in range(len(d)):
        if np.isnan(s2[i]) or np.isnan(h20[i]):
            state = 0.0
        elif state == 0.0 and cc[i] > h20[i] and cc[i] > s2[i]:
            state = 1.0
        elif state == 1.0 and (cc[i] < l10[i] or cc[i] < s2[i]):
            state = 0.0
        don[i] = state
    pos["don"] = pd.Series(don, index=d.index)
    valid = sma200.notna()
    return {k: v.where(valid) for k, v in pos.items()}


def sleeve(d: pd.DataFrame, pos: pd.Series, name: str) -> tuple[pd.Series, pd.DataFrame]:
    """Retorno diario neto: posicion decidida al cierre t aplica de t a t+1."""
    ret = d.close.pct_change().shift(-1)
    p = pos.fillna(0.0)
    chg = p.diff().abs().fillna(p.abs())
    cost = 0.0 if name == "bh" else chg * COST_SIDE
    swap = 0.0 if name == "bh" else p * SWAP_DAY
    r = (p * ret - cost - swap).where(pos.notna())
    # trades para PF / WR
    trades = []
    inpos, acc, t0 = False, 0.0, None
    for t, pi, ri in zip(p.index, p.to_numpy(), (p * ret - swap if name != "bh" else p * ret).to_numpy()):
        if pi > 0 and not inpos:
            inpos, acc, t0 = True, -COST_SIDE, t
        if inpos:
            acc += 0.0 if np.isnan(ri) else ri
            if pi == 0:
                trades.append((t0, t, acc - COST_SIDE))
                inpos = False
    return r.dropna(), pd.DataFrame(trades, columns=["t0", "t1", "ret"])


def stats(r: pd.Series, a: str, b: str, pos: pd.Series | None = None, trades: pd.DataFrame | None = None) -> dict:
    r = r.loc[a:b]
    if len(r) < 30:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min()
    down = r[r < 0]
    out = {"cagr_pct": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
           "total_pct": round(float(eq.iloc[-1] - 1) * 100, 1),
           "sharpe": round(float(r.mean() / r.std() * np.sqrt(365)), 2) if r.std() > 0 else None,
           "sortino": round(float(r.mean() / down.std() * np.sqrt(365)), 2) if len(down) > 1 else None,
           "max_dd_pct": round(float(dd) * 100, 1),
           "calmar": round(float((eq.iloc[-1] ** (1 / yrs) - 1) / -dd), 2) if dd < 0 else None}
    if pos is not None:
        out["time_in_mkt"] = round(float(pos.loc[a:b].fillna(0).mean()), 3)
    if trades is not None and len(trades):
        t = trades[(trades.t0 >= a) & (trades.t0 <= b)]
        if len(t):
            w, l = t[t.ret > 0].ret.sum(), -t[t.ret <= 0].ret.sum()
            out.update(trades=int(len(t)), wr=round(float((t.ret > 0).mean()), 3),
                       pf=round(float(w / l), 2) if l > 0 else None,
                       avg_hold_d=round(float((t.t1 - t.t0).dt.days.mean()), 1))
    return out


def yearly(r: pd.Series) -> dict:
    return {int(y): round(float((1 + g).prod() - 1) * 100, 1) for y, g in r.groupby(r.index.year)}


def main() -> int:
    # Costos de otro broker sin tocar el default XBTFX (p. ej. Exness: --cost-side 0.0002 --swap-year 0.104).
    import argparse
    global COST_SIDE, SWAP_DAY, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-side", type=float, default=COST_SIDE)
    ap.add_argument("--swap-year", type=float, default=SWAP_DAY * 365)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    COST_SIDE, SWAP_DAY, OUT = args.cost_side, args.swap_year / 365, Path(args.out)
    files = sorted(DATA.glob("*_1d.csv"))
    sleeves: dict[str, dict[str, pd.Series]] = {s: {} for s in STRATS}
    per_symbol: dict = {}
    for f in files:
        pair = f.name.split("_")[0]
        d = pd.read_csv(f, index_col="date", parse_dates=True)
        if len(d) < WARMUP + 180:
            continue
        pos = positions(d)
        per_symbol[pair] = {}
        for s in STRATS:
            r, tr = sleeve(d, pos[s], s)
            sleeves[s][pair] = r
            if pair in MAJORS:
                per_symbol[pair][s] = {p: stats(r, a, b, pos[s], tr if s not in ("bh", "bh_cfd") else None)
                                       for p, (a, b) in PERIODS.items()}
                per_symbol[pair][s]["yearly"] = yearly(r)
    out: dict = {"assumptions": {"cost_per_side": COST_SIDE, "swap_pct_year": round(SWAP_DAY * 365 * 100, 2), "warmup_days": WARMUP,
                                 "universe": "XBTFX hoy (supervivencia)", "n_symbols": len(per_symbol)},
                 "majors": {k: per_symbol[k] for k in MAJORS}, "portfolios": {}}
    for label, members in (("btc_eth", MAJORS), ("alts", tuple(k for k in per_symbol if k not in MAJORS))):
        out["portfolios"][label] = {}
        for s in STRATS:
            m = pd.DataFrame({k: sleeves[s][k] for k in members if k in sleeves[s]})
            r = m.mean(axis=1, skipna=True).dropna()
            out["portfolios"][label][s] = {p: stats(r, a, b) for p, (a, b) in PERIODS.items()}
            out["portfolios"][label][s]["yearly"] = yearly(r)
            out["portfolios"][label][s]["avg_members"] = round(float(m.notna().sum(axis=1).loc[r.index].mean()), 1)
    # cuanto del ciclo alcista captura y cuanto del bajista evita (por anio calendario, cartera btc_eth)
    for label in ("btc_eth", "alts"):
        print(f"\n=== {label} ===")
        P = out["portfolios"][label]
        for s in STRATS:
            f_, a_, b_ = P[s]["full"], P[s]["A_2018_2023"], P[s]["B_2023_2026"]
            print(f"{s:7s} full CAGR {f_.get('cagr_pct'):7} Sh {f_.get('sharpe'):5} DD {f_.get('max_dd_pct'):6} | "
                  f"A CAGR {a_.get('cagr_pct'):7} Sh {a_.get('sharpe'):5} DD {a_.get('max_dd_pct'):6} | "
                  f"B CAGR {b_.get('cagr_pct'):7} Sh {b_.get('sharpe'):5} DD {b_.get('max_dd_pct'):6}")
        print("yearly %:")
        print(pd.DataFrame({s: P[s]["yearly"] for s in STRATS}).T.to_string())
    for k in MAJORS:
        print(f"\n{k} don full:", {x: out["majors"][k]["don"]["full"].get(x) for x in ("cagr_pct", "sharpe", "max_dd_pct", "time_in_mkt", "trades", "wr", "pf", "avg_hold_d")})
        print(f"{k} r200 full:", {x: out["majors"][k]["r200"]["full"].get(x) for x in ("cagr_pct", "sharpe", "max_dd_pct", "time_in_mkt", "trades", "wr", "pf", "avg_hold_d")})
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
