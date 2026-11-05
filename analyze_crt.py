"""CRT (Candle Range Theory) on BTC: sweep of one side of the parent candle and close back inside.

Pre-registration: trading/docs/PREREG_CRT.md (its SHA-256 is printed next to the results).
Train 2018-01-01 -> 2025-11-03; the holdout opens once, only for cells that pass, and the result is
written to trading/docs/reports/crt_holdout.json (a second run refuses to recompute it).

H4 and D1 are built from Binance 1h; exits are checked on 15m. Candles are taken as bid; the spread
``s`` is added against the trade: a sell fills at bid and exits at ask (high + s / low + s), a buy
fills at ask (close + s) and exits at bid. A 15m bar that touches TP and SL counts as SL.

    python analyze_crt.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_tp_chico_filtros import stats
from trading.infrastructure.binance_klines import read_csv

PREREG = Path("trading/docs/PREREG_CRT.md")
HOLDOUT_OUT = Path("trading/docs/reports/crt_holdout.json")
TRAIN = ("2018-01-01", "2025-11-04")
HOLD_START = "2025-11-04"
SPREAD_PCT = 0.000083
SWAP_PCT_DAY = 0.000231
TFS = {"H4": ("4h", 4), "D1": ("1D", 24)}
FILTERS = ("B", "C50", "P50")
TARGETS = ("T50", "TOP")
CELLS = [f"{tf}_{f}_{t}" for tf in TFS for f in FILTERS for t in TARGETS]
P_BAR = 0.01 / len(CELLS)
HOLD_BARS = 10


def load(path: str) -> pd.DataFrame:
    df = pd.DataFrame(read_csv(Path(path)))
    df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    return df[["open", "high", "low", "close"]].astype(float)


def resample(h1: pd.DataFrame, rule: str, hours: int) -> pd.DataFrame:
    g = h1.resample(rule)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "n": g["close"].count()})
    return out[out["n"] == hours].drop(columns="n")


def patterns(bars: pd.DataFrame, tf_hours: int) -> list[dict]:
    """Every valid CRT on consecutive complete candles, with the filters it meets."""
    out = []
    o = bars.to_numpy()
    idx = bars.index
    step = pd.Timedelta(hours=tf_hours)
    for j in range(1, len(bars)):
        if idx[j] - idx[j - 1] != step:
            continue
        ph, pl = o[j - 1, 1], o[j - 1, 2]
        h, l, c = o[j, 1], o[j, 2], o[j, 3]
        if ph <= pl:
            continue
        mid = (ph + pl) / 2
        up, down = h > ph, l < pl
        if up == down or not (pl <= c <= ph):
            continue
        side = -1 if up else 1
        out.append({"t_close": idx[j] + step, "side": side, "close": c, "sl": h if up else l,
                    "t50": mid, "top": pl if up else ph,
                    "C50": c > mid if up else c < mid, "P50": l > mid if up else h < mid})
    return out


class Exits:
    def __init__(self, m15: pd.DataFrame):
        self.t = m15.index
        self.hi, self.lo, self.cl = (m15[c].to_numpy() for c in ("high", "low", "close"))

    def run(self, p: dict, tp: float, hold_15m: int, spread_mult: float, costs: bool, flip: bool = False) -> dict | None:
        i = int(self.t.searchsorted(p["t_close"]))
        if i >= len(self.t):
            return None
        side, e, sl = p["side"], p["close"], p["sl"]
        if flip:  # same geometry, other direction: target and stop swap roles
            side, sl, tp = -side, tp, sl
        dist_sl, dist_tp = abs(e - sl), abs(tp - e)
        if dist_sl <= 0 or dist_tp <= 0 or (tp - e) * side <= 0 or (e - sl) * side <= 0:
            return None
        s = e * SPREAD_PCT * spread_mult if costs else 0.0
        end = min(i + hold_15m, len(self.t))
        hi, lo = self.hi[i:end], self.lo[i:end]
        if side == -1:  # exits at ask = bid + s
            hit_sl, hit_tp = hi + s >= sl, lo + s <= tp
        else:  # exits at bid
            hit_sl, hit_tp = lo <= sl, hi >= tp
        k_sl = int(np.argmax(hit_sl)) if hit_sl.any() else -1
        k_tp = int(np.argmax(hit_tp)) if hit_tp.any() else -1
        if k_sl >= 0 and (k_tp < 0 or k_sl <= k_tp):
            k, exit_px, why = k_sl, sl, "sl"
        elif k_tp >= 0:
            k, exit_px, why = k_tp, tp, "tp"
        else:
            if end - i < hold_15m:
                return None  # data ends before the time limit
            k, why = end - i - 1, "tiempo"
            exit_px = self.cl[i + k] + (s if side == -1 else 0.0)
        fill = e if side == -1 else e + s
        pnl = (fill - exit_px) if side == -1 else (exit_px - fill)
        days = (self.t[i + k] - p["t_close"]).total_seconds() / 86400 + 0.25 / 24
        if costs and side == 1:
            pnl -= SWAP_PCT_DAY * e * days
        return {"day": p["t_close"].date(), "side": side, "win": pnl > 0, "net": pnl / dist_sl,
                "pct": pnl / e, "why": why, "min": days * 1440, "coin": dist_sl / (dist_sl + dist_tp)}


def cell_trades(ex: Exits, pats: dict, cell: str, a: str, b: str | None, **kw) -> list[dict]:
    tf, flt, tgt = cell.split("_")
    lo = pd.Timestamp(a, tz="UTC")
    hi = pd.Timestamp(b, tz="UTC") if b else None
    hold = HOLD_BARS * TFS[tf][1] * 4
    out = []
    for p in pats[tf]:
        if p["t_close"] < lo or (hi is not None and p["t_close"] >= hi):
            continue
        if flt != "B" and not p[flt]:
            continue
        r = ex.run(p, p["t50"] if tgt == "T50" else p["top"], hold, **kw)
        if r:
            out.append(r)
    return out


def summary(rs: list[dict]) -> dict:
    st = stats(rs)
    if not st.get("n"):
        return st
    st["media_R"] = round(st.pop("media"), 4)
    st["ic95_R"] = [round(x, 4) for x in st.pop("ic95")]
    st["p_una_cola"] = round(st["p_una_cola"], 4)
    st["moneda_pct"] = round(100 * float(np.mean([r["coin"] for r in rs])), 2)
    st["media_pct_precio"] = round(100 * float(np.mean([r["pct"] for r in rs])), 4)
    st["salidas"] = {w: sum(r["why"] == w for r in rs) for w in ("tp", "sl", "tiempo")}
    return st


def passes(st: dict) -> bool:
    return (st.get("n", 0) >= 100 and st["media_R"] > 0 and st["p_una_cola"] < P_BAR
            and (st["profit_factor"] or 0) > 1)


def main() -> None:
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    lock = PREREG.with_name(PREREG.name + ".sha256")
    report: dict = {"prereg_sha256": sha, "prereg_lock_ok": lock.exists() and sha in lock.read_text(),
                    "p_bar": P_BAR}
    h1, m15 = load("data/binance/BTCUSDT_1h.csv"), load("data/binance/BTCUSDT_15m.csv")
    pats = {tf: patterns(resample(h1, rule, hours), hours) for tf, (rule, hours) in TFS.items()}
    ex = Exits(m15)
    report["datos_hasta"] = str(m15.index[-1])

    train, diag, passed = {}, {}, []
    for cell in CELLS:
        rs = cell_trades(ex, pats, cell, *TRAIN, spread_mult=1.0, costs=True)
        st = summary(rs)
        st["aprueba"] = passes(st)
        train[cell] = st
        if st["aprueba"]:
            passed.append(cell)
        gross = summary(cell_trades(ex, pats, cell, *TRAIN, spread_mult=0.0, costs=False))
        flip = summary(cell_trades(ex, pats, cell, *TRAIN, spread_mult=0.0, costs=False, flip=True))
        stress = summary(cell_trades(ex, pats, cell, *TRAIN, spread_mult=2.0, costs=True))
        diag[cell] = {
            "sin_costos_media_R": gross.get("media_R"), "sin_costos_ic95_R": gross.get("ic95_R"),
            "sin_costos_ganadoras_pct": gross.get("ganadoras_pct"),
            "lado_invertido_sin_costos_media_R": flip.get("media_R"),
            "spread_x2_media_R": stress.get("media_R"),
            "compras": summary([r for r in rs if r["side"] == 1]),
            "ventas": summary([r for r in rs if r["side"] == -1]),
        }
    report["train"] = train
    report["diagnosticos_train"] = diag
    report["aprobaron_train"] = passed

    if HOLDOUT_OUT.exists():
        report["holdout"] = json.loads(HOLDOUT_OUT.read_text(encoding="utf-8"))
        report["holdout_nota"] = "ya consumido: se muestra el resultado guardado, no se recalcula"
    elif passed:
        hold = {}
        for cell in passed:
            st = summary(cell_trades(ex, pats, cell, HOLD_START, None, spread_mult=1.0, costs=True))
            st["confirma"] = st.get("n", 0) > 0 and st["media_R"] > 0 and st["p_una_cola"] < 0.05
            hold[cell] = st
        out = {"consumido": True, "prereg_sha256": sha, "celdas": hold}
        HOLDOUT_OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        report["holdout"] = out
    else:
        report["holdout"] = "sin abrir: ninguna celda aprobó el train"
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
