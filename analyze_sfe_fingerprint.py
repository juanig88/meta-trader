"""Huella de entrada de SFE Crypto Master sobre Binance 5m + horizonte que captura el ciclo alcista. Research only.

1. Offset horario del broker: el que minimiza |Open Price SFE - open Binance|.
2. Features al momento de cada entrada (firmadas por lado: + = a favor del trade) vs
   la misma hora en todos los dias (linea base).
3. Entradas reales de SFE con nuestras salidas (+-0.5 ATR, 7h): aisla si el edge esta en la entrada.
4. Horizonte: retorno largo en regimen alcista (SMA200) a 7h / 1d / 5d / 20d, neto de costo.

    py analyze_sfe_fingerprint.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_sfe_replica import IS, OOS, PAIRS, SFE_CSV, daily_features, load

OUT = Path("analyze_sfe_fingerprint_out.json")
COST_RT = 0.0010


def sfe_trades() -> pd.DataFrame:
    df = pd.read_csv(SFE_CSV, skiprows=1)
    df = df[df.Action.isin(["Buy", "Sell"])].copy()
    df["open_t"] = pd.to_datetime(df["Open Date"], format="%d.%m.%Y %H:%M:%S")
    df["close_t"] = pd.to_datetime(df["Close Date"], format="%d.%m.%Y %H:%M:%S")
    for c in ("Open Price", "Close Price", "Profit"):
        df[c] = pd.to_numeric(df[c])
    df["side"] = np.where(df.Action == "Buy", 1, -1)
    df["gain"] = pd.to_numeric(df.Gain.str.rstrip("%"), errors="coerce")
    return df.reset_index(drop=True)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def intraday_features(m5: pd.DataFrame) -> pd.DataFrame:
    c = m5.close
    h1 = m5.resample("1h").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    f = pd.DataFrame(index=m5.index)
    for lab, n in (("r15m", 3), ("r1h", 12), ("r4h", 48), ("r24h", 288)):
        f[lab] = (c / c.shift(n) - 1) * 100
    ema20 = h1.close.ewm(span=20).mean()
    ema50 = h1.close.ewm(span=50).mean()
    atr_h1 = (h1.high - h1.low).rolling(14).mean()
    hf = pd.DataFrame({"ema20_atr": (h1.close - ema20) / atr_h1, "ema50_atr": (h1.close - ema50) / atr_h1,
                       "rsi_h1": rsi(h1.close)}, index=h1.index).shift(1)  # solo H1 cerradas
    f = f.join(hf.reindex(m5.index, method="ffill"))
    f["rsi_m5"] = rsi(c)
    hi, lo = m5.high.rolling(288).max(), m5.low.rolling(288).min()
    f["pos24h"] = (c - lo) / (hi - lo)
    return f.shift(1)  # al open de la vela de entrada solo se conoce hasta el cierre previo


def best_offset(tr: pd.DataFrame, data: dict) -> int:
    errs = {}
    for off in range(-1, 5):
        e = []
        for _, r in tr.iterrows():
            m5 = data[r.Symbol]
            t = (r.open_t - pd.Timedelta(hours=off)).floor("5min")
            if t in m5.index:
                e.append(abs(m5.at[t, "open"] / r["Open Price"] - 1))
        errs[off] = float(np.median(e)) if e else 1.0
    print("offset -> median |px err|", {k: round(v * 100, 3) for k, v in errs.items()})
    return min(errs, key=errs.get)


def exit_like_ours(m5: pd.DataFrame, t0: pd.Timestamp, side: int, atr: float) -> tuple[float, str]:
    i0 = m5.index.searchsorted(t0)
    i1 = min(m5.index.searchsorted(m5.index[i0] + pd.Timedelta(hours=7)), len(m5) - 1)
    px = m5.open.iat[i0]
    dist = 0.5 * atr
    sl, tp = px * (1 - side * dist), px * (1 + side * dist)
    hh, ll = m5.high.to_numpy()[i0:i1], m5.low.to_numpy()[i0:i1]
    slh = ll <= sl if side == 1 else hh >= sl
    tph = hh >= tp if side == 1 else ll <= tp
    a = np.argmax(slh) if slh.any() else 10**9
    b = np.argmax(tph) if tph.any() else 10**9
    if a == b == 10**9:
        xp, how = m5.open.iat[i1], "time"
    elif a <= b:
        xp, how = sl, "sl"
    else:
        xp, how = tp, "tp"
    return (side * (xp / px - 1) - COST_RT) / dist, how


def main() -> int:
    data = {s: load(p) for s, p in PAIRS.items()}
    feats_d = {s: daily_features(m) for s, m in data.items()}
    feats_i = {s: intraday_features(m) for s, m in data.items()}
    tr = sfe_trades()
    off = best_offset(tr, data)
    tr["t_utc"] = (tr.open_t - pd.Timedelta(hours=off)).dt.floor("5min")
    out: dict = {"broker_offset_h": off}

    cols = ["r15m", "r1h", "r4h", "r24h", "ema20_atr", "ema50_atr", "rsi_h1", "rsi_m5", "pos24h"]
    rows = []
    for _, r in tr.iterrows():
        fi = feats_i[r.Symbol]
        if r.t_utc not in fi.index:
            continue
        x = fi.loc[r.t_utc, cols].to_dict()
        fd = feats_d[r.Symbol].loc[r.t_utc.normalize()]
        x.update(atr=fd.atr_pct, bull=fd.bull, prev_dir=fd.prev_dir)
        x.update(idx=r.name, side=r.side, sym=r.Symbol, win=r.Profit > 0, gain=r.gain, t=r.t_utc)
        rows.append(x)
    X = pd.DataFrame(rows)
    s = X.side
    signed = pd.DataFrame({
        "r15m": s * X.r15m, "r1h": s * X.r1h, "r4h": s * X.r4h, "r24h": s * X.r24h,
        "ema20_atr": s * X.ema20_atr, "ema50_atr": s * X.ema50_atr,
        "rsi_h1": np.where(s == 1, X.rsi_h1, 100 - X.rsi_h1), "rsi_m5": np.where(s == 1, X.rsi_m5, 100 - X.rsi_m5),
        "pos24h": np.where(s == 1, X.pos24h, 1 - X.pos24h),
        "with_regime": (s == np.where(X.bull == 1, 1, -1)).astype(float),
        "with_prev_day": (s == X.prev_dir).astype(float),
    })
    # linea base: mismas horas UTC, todos los dias del periodo IS, lado al azar simetrico -> mediana ~0 / 50
    base = []
    for sym, fi in feats_i.items():
        hrs = X[X.sym == sym].t.dt.hour.value_counts(normalize=True)
        b = fi.loc[IS[0]:IS[1]]
        b = b[b.index.minute == 0]
        b = b[b.index.hour.isin(hrs.index)]
        base.append(b)
    B = pd.concat(base)
    out["n_matched"] = int(len(X))
    fp = {}
    for c in signed.columns:
        v = signed[c].dropna()
        row = {"sfe_median": round(float(v.median()), 3), "sfe_mean": round(float(v.mean()), 3)}
        if c in B.columns:
            bb = B[c].dropna()
            if c.startswith("rsi"):
                row["base_abs_dev_median"] = round(float((bb - 50).abs().median()), 2)
            elif c == "pos24h":
                row["base_median"] = 0.5
            else:
                row["base_abs_median"] = round(float(bb.abs().median()), 3)
        w = X.loc[v.index]
        row["wr_if_above_median"] = round(float(w[v > v.median()].win.mean()), 3)
        row["wr_if_below_median"] = round(float(w[v <= v.median()].win.mean()), 3)
        fp[c] = row
    out["fingerprint_signed"] = fp
    print(pd.DataFrame(fp).T.to_string())

    q = signed.assign(win=X.win.values, gain=X.gain.values)
    out["pullback_vs_breakout"] = {
        "frac_entries_after_adverse_1h": round(float((q.r1h < 0).mean()), 3),
        "frac_entries_after_adverse_4h": round(float((q.r4h < 0).mean()), 3),
        "frac_rsi_h1_below_50_for_side": round(float((q.rsi_h1 < 50).mean()), 3),
        "frac_price_beyond_ema50_for_side": round(float((q.ema50_atr > 0).mean()), 3),
        "gain_by_r1h_sign": q.groupby(q.r1h > 0).gain.sum().round(1).to_dict(),
        "gain_by_r4h_sign": q.groupby(q.r4h > 0).gain.sum().round(1).to_dict(),
        "gain_by_ema50_side": q.groupby(q.ema50_atr > 0).gain.sum().round(1).to_dict(),
    }
    print(json.dumps(out["pullback_vs_breakout"], indent=1))

    rr = []
    for _, x in X.iterrows():
        r, how = exit_like_ours(data[x.sym], x.t, int(x.side), float(x.atr))
        rr.append((x.t.year, r, how, x.gain))
    R = pd.DataFrame(rr, columns=["year", "R", "how", "gain"])
    out["sfe_entries_our_exits"] = {
        "n": int(len(R)), "sum_R": round(float(R.R.sum()), 1), "mean_R": round(float(R.R.mean()), 3),
        "pf": round(float(R[R.R > 0].R.sum() / -R[R.R <= 0].R.sum()), 2),
        "by_year_R": R.groupby("year").R.sum().round(1).to_dict(),
        "sfe_actual_by_year_gain_pct": R.groupby("year").gain.sum().round(1).to_dict(),
        "exits": R.how.value_counts(normalize=True).round(3).to_dict(),
    }
    print("SFE entries + our exits:", out["sfe_entries_our_exits"])

    hz = {}
    for sym, m5 in data.items():
        d = m5.close.resample("1D").last().dropna()
        fd = feats_d[sym]
        h14 = m5.open[(m5.index.hour == 14) & (m5.index.minute == 0)]
        h21 = m5.open[(m5.index.hour == 21) & (m5.index.minute == 0)]
        r7h = pd.Series(h21.values / h14.reindex(h21.index - pd.Timedelta(hours=7)).values - 1,
                        index=h21.index.normalize())
        for per, (a, b) in (("oos", OOS), ("is", IS)):
            bull = fd.bull.loc[a:b] == 1
            res = {}
            res["7h"] = r7h.loc[a:b][bull.reindex(r7h.loc[a:b].index).fillna(False).values]
            for n in (1, 5, 20):
                fwd = (d.shift(-n) / d - 1).loc[a:b]
                res[f"{n}d"] = fwd[bull.reindex(fwd.index).fillna(False).values]
            hz[f"{sym}_{per}"] = {k: {"mean_gross_pct": round(float(v.mean() * 100), 3),
                                      "mean_net_pct": round(float((v.mean() - COST_RT) * 100), 3),
                                      "net_over_cost": round(float((v.mean() - COST_RT) / COST_RT), 1),
                                      "hit": round(float((v > COST_RT).mean()), 3)} for k, v in res.items()}
    out["bull_long_horizon"] = hz
    for k, v in hz.items():
        print(k, {h: (x["mean_net_pct"], x["hit"]) for h, x in v.items()})
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
