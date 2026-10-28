"""Regla ex-ante 'continuacion de impulso' derivada de la huella de SFE, testeada en BTC/ETH 5m. Research only.

Huella (analyze_sfe_fingerprint_out.json): SFE entra a favor de un impulso de 4h (~0.9 ATR diario),
con precio en el tope del rango de 24h y del lado correcto de la EMA50 H1.

Regla: al cierre de una vela 5m, si r4h firmado >= THR * ATR diario, pos24h >= 0.85 para ese lado
y close del lado de la EMA50 H1 -> entra al open siguiente en esa direccion. Bracket +-0.5 ATR,
time-stop 7h, piramide opcional (+0.1 ATR, max 5, ventana 4h). Una sesion por simbolo a la vez.
THR fijado antes de mirar resultados: 0.5 / 0.75 / 1.0. OOS 2018-04..2023-08 no contiene trades de SFE.

    py analyze_sfe_impulse.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_sfe_replica import IS, OOS, PAIRS, daily_features, load, metrics

OUT = Path("analyze_sfe_impulse_out.json")


@dataclass(frozen=True)
class Cfg:
    name: str
    thr: float = 0.75
    pos24: float = 0.85
    max_units: int = 5
    step_atr: float = 0.10
    k_atr: float = 0.50
    hold_h: float = 7.0
    add_window_h: float = 4.0
    cost_rt: float = 0.0010
    hours: tuple | None = None   # None = 24h
    regime: bool = False


BASE = Cfg("impulso_075")
VARIANTS = [
    replace(BASE, name="impulso_050", thr=0.50),
    BASE,
    replace(BASE, name="impulso_100", thr=1.00),
    replace(BASE, name="impulso_075_sin_piramide", max_units=1),
    replace(BASE, name="impulso_075_12_20utc", hours=tuple(range(12, 20))),
    replace(BASE, name="impulso_075_regimen", regime=True),
    replace(BASE, name="impulso_075_costo_x2", cost_rt=0.0020),
    replace(BASE, name="impulso_075_hold_24h", hold_h=24.0),
]


def prepare(m5: pd.DataFrame, fd: pd.DataFrame) -> pd.DataFrame:
    c = m5.close
    h1 = c.resample("1h").last().dropna()
    ema50 = h1.ewm(span=50).mean().shift(1).reindex(m5.index, method="ffill")  # solo H1 cerradas
    p = pd.DataFrame(index=m5.index)
    p["r4h"] = c / c.shift(48) - 1
    hi, lo = m5.high.rolling(288).max(), m5.low.rolling(288).min()
    p["pos24"] = (c - lo) / (hi - lo)
    p["above50"] = np.sign(c - ema50)
    day = fd.reindex(m5.index.normalize())
    p["atr"] = day.atr_pct.to_numpy()
    p["bull"] = day.bull.to_numpy()
    return p  # se evalua al CIERRE de la vela i, se entra al open de i+1


def session(t, o, h, l, c, i0, side, dist, step, cfg: Cfg):
    entries = [i0]
    last_px = o[i0]
    add_until = t[i0] + np.timedelta64(int(cfg.add_window_h * 60), "m")
    k = i0
    while len(entries) < cfg.max_units:
        hit = None
        while k + 1 < len(t) and t[k] < add_until:
            if side * (c[k] / last_px - 1) >= step:
                hit = k + 1
                break
            k += 1
        if hit is None:
            break
        entries.append(hit)
        last_px = o[hit]
        k = hit
    res, last_exit = [], i0
    for n, ie in enumerate(entries):
        px = o[ie]
        sl, tp = px * (1 - side * dist), px * (1 + side * dist)
        ie2 = min(np.searchsorted(t, t[ie] + np.timedelta64(int(cfg.hold_h * 60), "m")), len(t) - 1)
        hh, ll, oo = h[ie:ie2], l[ie:ie2], o[ie:ie2]
        slh = ll <= sl if side == 1 else hh >= sl
        tph = hh >= tp if side == 1 else ll <= tp
        a = int(np.argmax(slh)) if slh.any() else 10**9
        b = int(np.argmax(tph)) if tph.any() else 10**9
        if a == b == 10**9:
            xp, how, xi = o[ie2], "time", ie2
        elif a <= b:
            gap = (oo[a] < sl) if side == 1 else (oo[a] > sl)
            xp, how, xi = (oo[a] if gap and a > 0 else sl), "sl", ie + a
        else:
            gap = (oo[b] > tp) if side == 1 else (oo[b] < tp)
            xp, how, xi = (oo[b] if gap and b > 0 else tp), "tp", ie + b
        ret = side * (xp / px - 1)
        res.append(dict(unit=n + 1, entry=pd.Timestamp(t[ie]), ret=ret, R=(ret - cfg.cost_rt) / dist, how=how))
        last_exit = max(last_exit, xi)
    return res, last_exit


def simulate(m5: pd.DataFrame, p: pd.DataFrame, cfg: Cfg, start: str, end: str) -> pd.DataFrame:
    t = m5.index.values
    o, h, l, c = (m5[x].to_numpy(np.float64) for x in ("open", "high", "low", "close"))
    r4, pos, ab, atr, bull = (p[x].to_numpy() for x in ("r4h", "pos24", "above50", "atr", "bull"))
    hours = m5.index.hour.to_numpy()
    lo_i, hi_i = np.searchsorted(t, np.datetime64(start)), np.searchsorted(t, np.datetime64(pd.Timestamp(end) + pd.Timedelta(days=1)))
    with np.errstate(invalid="ignore"):
        long_ok = (r4 >= cfg.thr * atr) & (pos >= cfg.pos24) & (ab > 0)
        short_ok = (-r4 >= cfg.thr * atr) & (pos <= 1 - cfg.pos24) & (ab < 0)
    if cfg.regime:
        long_ok &= bull == 1
        short_ok &= bull == 0
    if cfg.hours is not None:
        hm = np.isin(hours, cfg.hours)
        long_ok &= hm
        short_ok &= hm
    cand = np.flatnonzero((long_ok | short_ok)[lo_i:hi_i - 1]) + lo_i
    rows, busy_until = [], -1
    for i in cand:
        if i <= busy_until or np.isnan(atr[i]):
            continue
        side = 1 if long_ok[i] else -1
        dist, step = cfg.k_atr * atr[i], cfg.step_atr * atr[i]
        res, last_exit = session(t, o, h, l, c, i + 1, side, dist, step, cfg)
        day = pd.Timestamp(t[i + 1]).normalize()
        for r in res:
            r.update(day=day, side=side, bull=bool(bull[i] == 1))
        rows += res
        busy_until = last_exit
    return pd.DataFrame(rows)


def main() -> int:
    data = {s: load(pr) for s, pr in PAIRS.items()}
    feats = {s: daily_features(m) for s, m in data.items()}
    prep = {s: prepare(data[s], feats[s]) for s in data}
    out: dict = {"periods": {"oos": OOS, "is": IS}, "base": BASE.__dict__, "variants": {}}
    for cfg in VARIANTS:
        res = {}
        for per, (a, b) in (("oos", OOS), ("is", IS)):
            tr = pd.concat([simulate(data[s], prep[s], cfg, a, b).assign(sym=s) for s in data], ignore_index=True)
            m = metrics(tr, a, b) if not tr.empty else {"units": 0}
            if not tr.empty:
                m["by_symbol"] = {s: {k: v for k, v in metrics(g, a, b).items() if k in ("sessions", "pf", "sum_R", "sharpe", "max_dd_R")}
                                  for s, g in tr.groupby("sym")}
                m["sides"] = tr.groupby("side").R.agg(["count", "sum"]).round(1).to_dict()
                m["regime"] = tr.groupby("bull").R.agg(["count", "sum"]).round(1).to_dict()
            res[per] = m
        out["variants"][cfg.name] = res
        o_, i_ = res["oos"], res["is"]
        print(f"{cfg.name:26s} OOS ses {o_.get('sessions')} PF {o_.get('pf')} R {o_.get('sum_R')} Sh {o_.get('sharpe')} DD {o_.get('max_dd_R')} | "
              f"IS ses {i_.get('sessions')} PF {i_.get('pf')} R {i_.get('sum_R')} Sh {i_.get('sharpe')} DD {i_.get('max_dd_R')}", flush=True)
        print("   yearly", {**o_.get("yearly_R", {}), **{f"{k}i": v for k, v in i_.get("yearly_R", {}).items()}}, flush=True)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
