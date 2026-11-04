"""Bad days of the TP chico lateral rule with ONE position: are they predictable beforehand?

Exploration on the train only (2018-01-01 -> 2025-11-03, % of price, shown as USD at 77k with
0.01 lot). The holdout year was consumed by PREREG_TP_CHICO_LATERAL.md, so nothing found here
is confirmed: any filter must be pre-registered and run forward on the demo.

Rule: analyze_tp_chico_lateral.py (near a zone, no big candles, no 1% run in 4h, trend side),
but one position at a time. Every feature is known at the moment of entry. A feature only
counts as a pattern if it points the same way in 2018-2021 and in 2022-2025.

    python analyze_dias_malos.py
"""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import pandas as pd

from analyze_tp_chico_filtros import binance, trade
from analyze_tp_chico_lateral import TRAIN, ZONE_MAX, calm, zone_info

USD = 770  # % of price -> USD with 0.01 lot at 77,000
HALVES = (("2018-01-01", "2022-01-01"), ("2022-01-01", "2025-11-04"))


def trades_one_position(m) -> list[dict]:
    lo, hi = (pd.Timestamp(x, tz="UTC") for x in TRAIN)
    rng = m.hi - m.lo
    out, busy = [], -1
    for i in range(len(m.t) - 1):
        if not lo <= m.t[i] < hi or i <= busy:
            continue
        dist, _ = zone_info(m, i)
        if not dist <= ZONE_MAX or not calm(m, i, rng):
            continue
        side = m.side(i)
        if side == 0:
            continue
        t = trade(m, i, side, absolute=False)
        if t is None:
            break
        t.update(i=i, zone_dist=dist)
        out.append(t)
        busy = t["exit"]
    return out


def add_features(m, trades: list[dict]) -> pd.DataFrame:
    d = m.h1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    rng = d["high"] / d["low"] - 1
    feats = pd.DataFrame({
        "rango_ayer": rng.shift(1),
        "rango_ayer_vs_adr": (rng / rng.rolling(20).mean()).shift(1),
        "mov_ayer_abs": (d["close"] / d["open"] - 1).abs().shift(1),
        "vol_5d_vs_30d": (rng.rolling(5).mean() / rng.rolling(30).mean()).shift(1),
        "dist_ema20": ((d["close"] / d["close"].ewm(span=20, adjust=False).mean()) - 1).abs().shift(1),
    })
    rows = []
    for t in trades:
        i, ts = t["i"], m.t[t["i"]]
        day = ts.floor("1D")
        f = feats.loc[day] if day in feats.index else None
        if f is None or f.isna().any():
            continue
        day_bars = slice(int(np.searchsorted(m.t, day)), i + 1)
        rows.append({
            "day": t["day"], "net_usd": t["net"] * USD, "sl": not t["win"], "side": t["side"],
            **f.to_dict(),
            "rango_hoy_hasta_ahora": m.hi[day_bars].max() / m.lo[day_bars].min() - 1,
            "mov_1h": abs(m.cl[i] / m.cl[i - 4] - 1),
            "dist_zona": t["zone_dist"],
            "hora_utc": ts.hour, "dia_semana": ts.dayofweek,
        })
    return pd.DataFrame(rows)


def concentration(df: pd.DataFrame) -> dict:
    by_day = df.groupby("day")["net_usd"].sum().sort_values()
    n = len(by_day)
    total = by_day.sum()
    out = {"dias": n, "total_usd": round(total, 1), "dias_con_perdida": int((by_day < 0).sum()),
           "dias_con_ganancia": int((by_day > 0).sum())}
    for k in (1, 5, 10):
        worst = by_day.iloc[: max(1, n * k // 100)]
        out[f"sin_el_peor_{k}pct_de_dias"] = round(total - worst.sum(), 1)
    out["ganancia_media_dia_bueno"] = round(by_day[by_day > 0].mean(), 2)
    out["perdida_media_dia_malo"] = round(by_day[by_day < 0].mean(), 2)
    return out


def buckets(df: pd.DataFrame, col: str) -> dict:
    """Quartiles fixed on the whole train; net per trade and SL rate in each half."""
    if col in ("hora_utc", "dia_semana"):
        groups = df[col] // (6 if col == "hora_utc" else 1)
        labels = {k: (f"{k * 6:02d}-{k * 6 + 5:02d}h" if col == "hora_utc" else "lun mar mie jue vie sab dom".split()[k])
                  for k in sorted(groups.unique())}
    else:
        groups = pd.qcut(df[col], 4, labels=False, duplicates="drop")
        edges = df[col].quantile([0, .25, .5, .75, 1]).round(4).tolist()
        labels = {k: f"Q{k + 1} [{edges[k]}, {edges[k + 1]}]" for k in sorted(groups.dropna().unique())}
    res = {}
    for k, label in labels.items():
        row = {}
        for a, b in HALVES:
            sub = df[(groups == k) & (df["day"] >= pd.Timestamp(a).date()) & (df["day"] < pd.Timestamp(b).date())]
            row[a[:4] + "-" + str(int(b[:4]) - 1)] = {"n": len(sub), "usd_trade": round(sub["net_usd"].mean(), 3) if len(sub) else None,
                                                    "sl_pct": round(100 * sub["sl"].mean(), 2) if len(sub) else None}
        res[label] = row
    return res


def main() -> None:
    m = binance()
    trades = trades_one_position(m)
    df = add_features(m, trades)
    report = {"operaciones": len(df), "ganadoras_pct": round(100 * (1 - df["sl"].mean()), 2),
              "usd_por_trade": round(df["net_usd"].mean(), 3), "concentracion": concentration(df)}
    for col in ("rango_ayer", "rango_ayer_vs_adr", "mov_ayer_abs", "vol_5d_vs_30d", "dist_ema20",
                "rango_hoy_hasta_ahora", "mov_1h", "dist_zona", "hora_utc", "dia_semana"):
        report[col] = buckets(df, col)
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
