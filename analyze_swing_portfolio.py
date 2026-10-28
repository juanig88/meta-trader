"""V3: cartera Donchian 20/10 + SMA200 con la cuenta demo real y los contratos XBTFX. Research only.

- Capital inicial = saldo demo (logs/xbtfx_specs_2026-10-07.json), compone a diario.
- Tamaño por riesgo: unidades = riesgo% * equity / (entrada - minimo 10d previo); redondeo a volume_step,
  se salta si queda bajo volume_min. Tope de posiciones abiertas; con mas senales que lugares, entra
  primero la de mayor retorno 90d.
- Costos: medio spread XBTFX mediano a 23-00 UTC por lado (0.2% si falta) + swap 21%/anio del nocional.
- Salida al cierre: close < minimo 10d previo o close < SMA200.

Criterio fijado antes de mirar: base (0.5%, 20 posiciones) con CAGR > 0 en full, A y B, DD >= -35%,
Sharpe >= 0.5, y >= 2/3 de las variantes de sensibilidad con CAGR > 0 en A y B.

    py analyze_swing_portfolio.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fetch_binance_1m import PAIRS

DATA = Path("logs/binance/daily")
SPECS = Path("logs/xbtfx_specs_2026-10-07.json")
OUT = Path("analyze_swing_portfolio_out.json")
SWAP_DAY = 0.21 / 365
DEFAULT_SPREAD = 0.002
EXCLUDE = {"SHBUSD!"}  # swap 89%/anio
PERIODS = {"full": ("2018-03-05", "2026-09-30"), "A_2018_2023": ("2018-03-05", "2023-08-31"),
           "B_2023_2026": ("2023-09-01", "2026-09-30")}


@dataclass(frozen=True)
class Cfg:
    name: str
    risk: float = 0.005
    max_pos: int = 20


VARIANTS = [Cfg("base_0.5pct_20pos"), Cfg("0.25pct_20pos", risk=0.0025), Cfg("1pct_10pos", risk=0.01, max_pos=10),
            Cfg("0.5pct_10pos", max_pos=10), Cfg("0.5pct_40pos", max_pos=40), Cfg("1pct_20pos", risk=0.01)]


def load_universe() -> tuple[dict, dict, float]:
    spec = json.loads(SPECS.read_text())
    inv = {v: k for k, v in PAIRS.items()}
    frames, meta = {}, {}
    for f in sorted(DATA.glob("*_1d.csv")):
        pair = f.name.split("_")[0]
        xb = inv.get(pair)
        if xb is None or xb in EXCLUDE or xb not in spec["symbols"]:
            continue
        d = pd.read_csv(f, index_col="date", parse_dates=True)
        if len(d) < 380:
            continue
        s = spec["symbols"][xb]
        c = d.close
        d["sma200"] = c.rolling(200).mean()
        d["hi20"] = d.high.rolling(20).max().shift(1)
        d["lo10"] = d.low.rolling(10).min().shift(1)
        d["mom90"] = c / c.shift(90) - 1
        frames[xb] = d
        meta[xb] = {"contract": s["vpp"], "vol_min": s["vol_min"], "vol_step": s["vol_step"],
                    "half_spread": (s["spread_pct_00utc"] or DEFAULT_SPREAD) / 2}
    return frames, meta, float(spec["account"]["balance"])


def simulate(frames: dict, meta: dict, cash0: float, cfg: Cfg, start: str, end: str) -> dict:
    dates = pd.date_range(start, end, freq="D")
    close = pd.DataFrame({k: v.close for k, v in frames.items()}).reindex(dates)
    cols = {f: pd.DataFrame({k: v[f] for k, v in frames.items()}).reindex(dates) for f in ("sma200", "hi20", "lo10", "mom90")}
    syms = list(frames)
    C, S2, HI, LO, MOM = (x.to_numpy() for x in (close, cols["sma200"], cols["hi20"], cols["lo10"], cols["mom90"]))
    cash = cash0
    pos: dict[int, dict] = {}
    equity, n_open, gross, trades = [], [], [], []
    skipped_lot = skipped_cap = 0
    for t in range(len(dates)):
        # mark-to-market + swap
        mtm = 0.0
        for j, p in pos.items():
            px = C[t, j]
            if not np.isnan(px):
                p["last"] = px
            cash -= p["units"] * p["last"] * SWAP_DAY
            mtm += p["units"] * p["last"]
        eq = cash + mtm
        # salidas
        for j in list(pos):
            px = C[t, j]
            if np.isnan(px):
                continue
            if px < LO[t, j] or px < S2[t, j]:
                p = pos.pop(j)
                fill = px * (1 - meta[syms[j]]["half_spread"])
                cash += p["units"] * fill
                trades.append({"sym": syms[j], "t0": p["t0"], "t1": dates[t], "ret": fill / p["entry"] - 1,
                               "pnl": p["units"] * (fill - p["entry"]) - p["swap_paid"]})
        for p in pos.values():
            p["swap_paid"] += p["units"] * p["last"] * SWAP_DAY
        eq = cash + sum(p["units"] * p["last"] for p in pos.values())
        # entradas
        sig = [j for j in range(len(syms)) if j not in pos and not np.isnan(C[t, j]) and not np.isnan(S2[t, j])
               and not np.isnan(HI[t, j]) and C[t, j] > HI[t, j] and C[t, j] > S2[t, j] and LO[t, j] < C[t, j]]
        sig.sort(key=lambda j: -(MOM[t, j] if not np.isnan(MOM[t, j]) else -9))
        for j in sig:
            if len(pos) >= cfg.max_pos:
                skipped_cap += 1
                continue
            m = meta[syms[j]]
            fill = C[t, j] * (1 + m["half_spread"])
            dist = fill - LO[t, j]
            lots = (cfg.risk * eq / dist) / m["contract"]
            lots = np.floor(lots / m["vol_step"]) * m["vol_step"]
            if lots < m["vol_min"]:
                skipped_lot += 1
                continue
            units = lots * m["contract"]
            cash -= units * fill
            pos[j] = {"units": units, "entry": fill, "last": C[t, j], "t0": dates[t], "swap_paid": 0.0}
        eq = cash + sum(p["units"] * p["last"] for p in pos.values())
        equity.append(eq)
        n_open.append(len(pos))
        gross.append(sum(p["units"] * p["last"] for p in pos.values()) / eq if eq > 0 else np.nan)
    e = pd.Series(equity, index=dates)
    r = e.pct_change().dropna()
    yrs = len(r) / 365
    dd = (e / e.cummax() - 1).min()
    down = r[r < 0]
    tr = pd.DataFrame(trades)
    w = tr[tr.pnl > 0].pnl.sum() if len(tr) else 0
    l = -tr[tr.pnl <= 0].pnl.sum() if len(tr) else 0
    monthly = e.resample("ME").last().pct_change().dropna()
    return {
        "final_equity": round(float(e.iloc[-1]), 0),
        "cagr_pct": round(float((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100, 1),
        "sharpe": round(float(r.mean() / r.std() * np.sqrt(365)), 2),
        "sortino": round(float(r.mean() / down.std() * np.sqrt(365)), 2),
        "max_dd_pct": round(float(dd) * 100, 1),
        "worst_day_pct": round(float(r.min()) * 100, 2),
        "worst_month_pct": round(float(monthly.min()) * 100, 1),
        "days_below_minus_0p25pct": int((r < -0.0025).sum()),
        "trades": int(len(tr)), "wr": round(float((tr.pnl > 0).mean()), 3) if len(tr) else None,
        "pf": round(float(w / l), 2) if l > 0 else None,
        "avg_hold_d": round(float((tr.t1 - tr.t0).dt.days.mean()), 1) if len(tr) else None,
        "max_open": int(max(n_open)), "avg_open": round(float(np.mean(n_open)), 1),
        "avg_gross_x": round(float(np.nanmean(gross)), 2), "max_gross_x": round(float(np.nanmax(gross)), 2),
        "skipped_min_lot": skipped_lot, "skipped_cap": skipped_cap,
        "yearly_pct": {int(y): round(float(g.iloc[-1] / g.iloc[0] - 1) * 100, 1) for y, g in e.groupby(e.index.year)},
        "top_symbols_pnl": tr.groupby("sym").pnl.sum().round(0).sort_values().iloc[[0, 1, 2, -3, -2, -1]].to_dict() if len(tr) > 6 else {},
    }


def main() -> int:
    frames, meta, cash0 = load_universe()
    out: dict = {"cash0": cash0, "n_symbols": len(frames), "variants": {}}
    print(f"universo {len(frames)} simbolos, capital {cash0:.0f}")
    for cfg in VARIANTS:
        res = {p: simulate(frames, meta, cash0, cfg, a, b) for p, (a, b) in PERIODS.items()}
        out["variants"][cfg.name] = res
        f, A, B = res["full"], res["A_2018_2023"], res["B_2023_2026"]
        print(f"\n{cfg.name}: full CAGR {f['cagr_pct']} Sh {f['sharpe']} DD {f['max_dd_pct']} | A CAGR {A['cagr_pct']} DD {A['max_dd_pct']} | "
              f"B CAGR {B['cagr_pct']} DD {B['max_dd_pct']}")
        print("   ", {k: f[k] for k in ("trades", "wr", "pf", "avg_hold_d", "max_open", "avg_open", "avg_gross_x", "max_gross_x",
                                       "worst_day_pct", "worst_month_pct", "skipped_min_lot", "skipped_cap", "days_below_minus_0p25pct")})
        print("    yearly", f["yearly_pct"])
    base = out["variants"]["base_0.5pct_20pos"]
    sens = [v for k, v in out["variants"].items() if k != "base_0.5pct_20pos"]
    crit = {
        "base_cagr_full_A_B_pos": all(base[p]["cagr_pct"] > 0 for p in PERIODS),
        "base_dd_ge_-35": base["full"]["max_dd_pct"] >= -35,
        "base_sharpe_ge_0.5": base["full"]["sharpe"] >= 0.5,
        "sens_2of3_pos_A_B": sum(v["A_2018_2023"]["cagr_pct"] > 0 and v["B_2023_2026"]["cagr_pct"] > 0 for v in sens) >= 2 * len(sens) / 3,
    }
    out["criterion"] = crit | {"pass": all(crit.values())}
    print("\ncriterio:", out["criterion"])
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
