"""Backtest de la estructura de SFE Crypto Master (SignalStart) en BTC/ETH 5m. Research only.

Hipotesis extraida de docs/strategies/signalstart_strategies/SFE_Crypto_Master_Forex_Signals.csv:
direccion = vela diaria previa, entrada 14:00 UTC, piramide a favor (hasta 5 unidades),
bracket +-0.5 ATR diario por unidad, time-stop 7h. Cada unidad arriesga 1% (R = 1%).

Variantes fijadas antes de mirar resultados; IS = periodo vivo de SFE, OOS = 2018-04..2023-08.
Fills: primera entrada al open de las 14:00 (solo info de D-1); add-ons al open de la vela
siguiente al cierre que cumple; SL y TP en la misma vela -> SL; gap -> sale al open.

    py analyze_sfe_replica.py            # usa logs/binance/long/*_5m.csv.gz
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("logs/binance/long")
SFE_CSV = Path("docs/strategies/signalstart_strategies/SFE_Crypto_Master_Forex_Signals.csv")
OUT = Path("analyze_sfe_replica_out.json")
PAIRS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
OOS = ("2018-04-01", "2023-08-31")
IS = ("2023-09-01", "2026-09-30")


@dataclass(frozen=True)
class Cfg:
    name: str
    side_mode: str = "prev_day"   # prev_day | random | long | long_if_bull
    regime: bool = False          # solo operar a favor de SMA200 diaria
    entry_hour: int = 14
    max_units: int = 5
    step_atr: float = 0.10
    k_atr: float = 0.50
    hold_h: float = 7.0
    add_window_h: float = 4.0
    cost_rt: float = 0.0010       # ida y vuelta, fraccion del nocional
    seed: int = 0


BASE = Cfg("replica")
VARIANTS = [
    BASE,
    replace(BASE, name="sin_piramide", max_units=1),
    replace(BASE, name="replica_regimen", regime=True),
    replace(BASE, name="largos_en_bull", side_mode="long_if_bull"),
    replace(BASE, name="siempre_largo", side_mode="long"),
    replace(BASE, name="entrada_00utc", entry_hour=0),
    replace(BASE, name="replica_costo_x2", cost_rt=0.0020),
    replace(BASE, name="regimen_costo_x2", regime=True, cost_rt=0.0020),
]
N_RANDOM = 20


def load(pair: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"{pair}_5m.csv.gz", index_col="date", parse_dates=True)


def daily_features(m5: pd.DataFrame) -> pd.DataFrame:
    d = m5.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    f = pd.DataFrame(index=d.index)
    f["prev_dir"] = np.sign(d.close - d.open)
    f["atr_pct"] = tr.rolling(14).mean() / d.close
    f["bull"] = (d.close > d.close.rolling(200).mean()).astype(float)
    f.loc[d.close.rolling(200).mean().isna(), "bull"] = np.nan
    f["close"] = d.close
    return f.shift(1)  # lo que se sabe al empezar el dia D


def simulate(m5: pd.DataFrame, feats: pd.DataFrame, cfg: Cfg, start: str, end: str) -> pd.DataFrame:
    t = m5.index.values
    o, h, l, c = (m5[x].to_numpy(np.float64) for x in ("open", "high", "low", "close"))
    rng = np.random.default_rng(cfg.seed)
    bar = np.timedelta64(5, "m")
    rows = []
    for day, f in feats.loc[start:end].iterrows():
        if np.isnan(f.atr_pct) or np.isnan(f.bull):
            continue
        bull = f.bull == 1.0
        if cfg.side_mode == "prev_day":
            side = int(f.prev_dir)
        elif cfg.side_mode == "random":
            side = int(rng.choice([-1, 1]))
        elif cfg.side_mode == "long":
            side = 1
        else:
            side = 1 if bull else 0
        if side == 0 or (cfg.regime and side != (1 if bull else -1)):
            continue
        t0 = np.datetime64(day + pd.Timedelta(hours=cfg.entry_hour))
        i0 = np.searchsorted(t, t0)
        if i0 >= len(t) or t[i0] - t0 > np.timedelta64(30, "m"):
            continue
        dist = cfg.k_atr * f.atr_pct
        step = cfg.step_atr * f.atr_pct
        add_until = t[i0] + np.timedelta64(int(cfg.add_window_h * 60), "m")
        entries = [i0]
        last_px = o[i0]
        j = i0
        while len(entries) < cfg.max_units:
            k = j
            hit = None
            while k + 1 < len(t) and t[k] < add_until:
                if side * (c[k] / last_px - 1) >= step and k + 1 > entries[-1]:
                    hit = k + 1
                    break
                k += 1
            if hit is None:
                break
            entries.append(hit)
            last_px = o[hit]
            j = hit
        for n, ie in enumerate(entries):
            px = o[ie]
            sl = px * (1 - side * dist)
            tp = px * (1 + side * dist)
            t_exit = t[ie] + np.timedelta64(int(cfg.hold_h * 60), "m")
            ie2 = min(np.searchsorted(t, t_exit), len(t) - 1)
            hh, ll, oo = h[ie:ie2], l[ie:ie2], o[ie:ie2]
            if side == 1:
                sl_hit, tp_hit = ll <= sl, hh >= tp
            else:
                sl_hit, tp_hit = hh >= sl, ll <= tp
            a = np.argmax(sl_hit) if sl_hit.any() else 10**9
            b = np.argmax(tp_hit) if tp_hit.any() else 10**9
            if a == b == 10**9:
                xp, how = o[ie2], "time"
            elif a <= b:
                gap = (oo[a] < sl) if side == 1 else (oo[a] > sl)
                xp, how = (oo[a] if (gap and a > 0) else sl), "sl"
            else:
                gap = (oo[b] > tp) if side == 1 else (oo[b] < tp)
                xp, how = (oo[b] if (gap and b > 0) else tp), "tp"
            ret = side * (xp / px - 1)
            r = (ret - cfg.cost_rt) / dist
            rows.append(dict(day=day, unit=n + 1, side=side, bull=bull, entry=pd.Timestamp(t[ie]),
                             ret=ret, R=r, how=how))
    return pd.DataFrame(rows)


def metrics(tr: pd.DataFrame, start: str, end: str) -> dict:
    if tr.empty:
        return {"units": 0}
    days = pd.date_range(start, end, freq="D")
    daily = tr.groupby("day").R.sum().reindex(days, fill_value=0.0)
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    sess = tr.groupby("day").R.sum()
    w, ls = tr[tr.R > 0].R.sum(), -tr[tr.R <= 0].R.sum()
    yearly = daily.groupby(daily.index.year).sum()
    down = daily[daily < 0]
    return {
        "sessions": int(len(sess)), "units": int(len(tr)),
        "units_per_session": round(len(tr) / len(sess), 2),
        "wr_unit": round(float((tr.R > 0).mean()), 3),
        "wr_session": round(float((sess > 0).mean()), 3),
        "pf": round(float(w / ls), 2) if ls > 0 else None,
        "sum_R": round(float(tr.R.sum()), 1),
        "R_per_session": round(float(sess.mean()), 3),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(365)), 2) if daily.std() > 0 else None,
        "sortino": round(float(daily.mean() / down.std() * np.sqrt(365)), 2) if len(down) > 1 else None,
        "max_dd_R": round(float(dd), 1),
        "yearly_R": {int(k): round(float(v), 1) for k, v in yearly.items()},
        "exits": tr.how.value_counts(normalize=True).round(3).to_dict(),
    }


def by_regime(tr: pd.DataFrame) -> dict:
    out = {}
    for lab, g in tr.groupby("bull"):
        s = g.groupby("day").R.sum()
        out["bull" if lab else "bear"] = {"sessions": int(len(s)), "sum_R": round(float(g.R.sum()), 1),
                                          "R_per_session": round(float(s.mean()), 3),
                                          "wr_session": round(float((s > 0).mean()), 3)}
    return out


def sfe_monthly() -> pd.Series:
    df = pd.read_csv(SFE_CSV, skiprows=1)
    df = df[df.Action.isin(["Buy", "Sell"])]
    df["open"] = pd.to_datetime(df["Open Date"], format="%d.%m.%Y %H:%M:%S")
    df["gain"] = pd.to_numeric(df.Gain.str.rstrip("%"), errors="coerce")
    return df.groupby(df.open.dt.to_period("M")).gain.sum()


def main() -> int:
    data = {s: load(p) for s, p in PAIRS.items()}
    feats = {s: daily_features(m) for s, m in data.items()}
    out: dict = {"periods": {"oos": OOS, "is": IS}, "assumptions": {
        "risk_per_unit": "1R = 1% equity, sum_R ~ % equity sin componer",
        "base": BASE.__dict__}, "variants": {}}

    def run(cfg: Cfg) -> dict:
        res = {}
        for per, (a, b) in (("oos", OOS), ("is", IS)):
            tr = pd.concat([simulate(data[s], feats[s], cfg, a, b).assign(sym=s) for s in data], ignore_index=True)
            m = metrics(tr, a, b)
            m["by_symbol"] = {s: metrics(g, a, b) | {} for s, g in tr.groupby("sym")} if not tr.empty else {}
            for s in m["by_symbol"]:
                m["by_symbol"][s] = {k: m["by_symbol"][s][k] for k in ("sessions", "pf", "sum_R", "sharpe", "max_dd_R")}
            m["by_regime"] = by_regime(tr) if not tr.empty else {}
            res[per] = m
            res[f"_{per}_trades"] = tr
        return res

    trades_is = None
    for cfg in VARIANTS:
        r = run(cfg)
        if cfg.name == "replica":
            trades_is = r["_is_trades"]
        out["variants"][cfg.name] = {k: v for k, v in r.items() if not k.startswith("_")}
        print(f"{cfg.name:18s} OOS PF {r['oos'].get('pf')} R {r['oos'].get('sum_R')} Sh {r['oos'].get('sharpe')} | "
              f"IS PF {r['is'].get('pf')} R {r['is'].get('sum_R')} Sh {r['is'].get('sharpe')}", flush=True)

    rand = {"oos": [], "is": []}
    for sd in range(N_RANDOM):
        r = run(replace(BASE, name="azar", side_mode="random", seed=sd))
        for per in rand:
            rand[per].append((r[per].get("sum_R", 0.0), r[per].get("pf"), r[per].get("sharpe")))
    out["variants"]["azar_x20"] = {per: {
        "sum_R_mean": round(float(np.mean([x[0] for x in v])), 1),
        "sum_R_p05_p95": [round(float(np.percentile([x[0] for x in v], q)), 1) for q in (5, 95)],
        "pf_mean": round(float(np.mean([x[1] for x in v if x[1]])), 2),
        "sharpe_mean": round(float(np.mean([x[2] for x in v if x[2] is not None])), 2)} for per, v in rand.items()}
    print("azar_x20", out["variants"]["azar_x20"], flush=True)

    sfe = sfe_monthly()
    rep = trades_is.groupby(trades_is.day.dt.to_period("M")).R.sum()
    j = pd.concat([sfe.rename("sfe_gain_pct"), rep.rename("replica_R")], axis=1).dropna()
    out["fidelity_vs_sfe"] = {"months": int(len(j)),
                              "corr_monthly": round(float(j.sfe_gain_pct.corr(j.replica_R)), 2),
                              "sfe_sum_gain_pct": round(float(j.sfe_gain_pct.sum()), 1),
                              "replica_sum_R": round(float(j.replica_R.sum()), 1)}
    print("fidelity", out["fidelity_vs_sfe"], flush=True)

    bh = {}
    for s, f in feats.items():
        for per, (a, b) in (("oos", OOS), ("is", IS)):
            px = f.close.loc[a:b].dropna()
            bh[f"{s}_{per}"] = round(float(px.iloc[-1] / px.iloc[0] - 1) * 100, 1)
    out["buy_hold_pct"] = bh
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
