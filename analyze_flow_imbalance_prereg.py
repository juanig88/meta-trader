"""Test pre-registrado: desbalance de flujo agresor en BTC, 5 a 30 min, costos Exness.

Diseño fijado en ``trading/docs/PREREG_FLUJO_AGRESOR.md`` antes de bajar los datos;
la salida guarda el SHA-256 de ese archivo. Solo lee datos: no hay órdenes.

    python analyze_flow_imbalance_prereg.py                      # train, 12 celdas
    python analyze_flow_imbalance_prereg.py --reveal-holdout N5_H15_cont   # solo celdas que pasaron
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from analyze_murphy_intraday_prereg import _stats

PREREG = Path("trading/docs/PREREG_FLUJO_AGRESOR.md")
DATA = Path("logs/binance/flow/BTCUSDT_1m_flow.npz")
SPLIT = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
NS = (5, 15)
HS = (5, 15, 30)
DIRS = ("cont", "rev")
QUANTILE = 0.90
LOOKBACK_DAYS = 30
SPREAD_FRAC = 0.000118
SWAP_LONG_FRAC_PER_DAY = 0.000222
MIN_TRADES = 200
P_TRAIN = 0.01 / (len(NS) * len(HS) * len(DIRS))
P_HOLDOUT = 0.05
SEED = 20261027


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reveal-holdout", nargs="*", default=None, help="celdas que pasaron el train")
    args = ap.parse_args()

    d = np.load(DATA)
    t = d["time"].astype(np.int64)
    o, c = d["open"].astype(float), d["close"].astype(float)
    vol, buy = d["volume"].astype(float), d["taker_buy"].astype(float)

    holdout = args.reveal_holdout is not None
    out: dict[str, Any] = {
        "envia_ordenes": False,
        "prereg": str(PREREG),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "generado": datetime.now(timezone.utc).isoformat(),
        "vara_p_train": P_TRAIN,
        "celdas": {},
    }
    for n in NS:
        imb = _imbalance(t, vol, buy, n)
        thr = _threshold(t, imb)
        for h in HS:
            for direction in DIRS:
                name = f"N{n}_H{h}_{direction}"
                if holdout and name not in args.reveal_holdout:
                    continue
                trades, info = _walk(t, o, c, imb, thr, n, h, direction)
                train = [r for r in trades if r["entry_t"] < SPLIT]
                cell = {"train": _report(train, P_TRAIN, MIN_TRADES), "diag_info_train": info["train"]}
                if holdout:
                    cell["holdout"] = _report([r for r in trades if r["entry_t"] >= SPLIT], P_HOLDOUT, 1)
                out["celdas"][name] = cell
    suffix = "_holdout" if holdout else ""
    Path(f"analyze_flow_imbalance_prereg{suffix}_out.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(out)
    return 0


def _imbalance(t: np.ndarray, vol: np.ndarray, buy: np.ndarray, n: int) -> np.ndarray:
    """(2·buy − vol) / vol over the last n minutes; NaN when the window has a gap or no volume."""
    cv, cb = np.r_[0.0, np.cumsum(vol)], np.r_[0.0, np.cumsum(buy)]
    out = np.full(len(t), np.nan)
    sv = cv[n:] - cv[:-n]
    sb = cb[n:] - cb[:-n]
    ok = ((t[n - 1:] - t[: len(t) - n + 1]) == (n - 1) * 60) & (sv > 0)
    val = np.where(ok, (2 * sb - sv) / np.where(sv > 0, sv, 1.0), np.nan)
    out[n - 1:] = val
    return out


def _threshold(t: np.ndarray, imb: np.ndarray) -> np.ndarray:
    """Per-minute threshold: q90 of |I| over the 30 previous UTC days. NaN during warm-up."""
    day = t // 86400
    uniq, start = np.unique(day, return_index=True)
    ends = np.r_[start[1:], len(t)]
    absI = np.abs(imb)
    thr_day = np.full(len(uniq), np.nan)
    for k in range(LOOKBACK_DAYS, len(uniq)):
        if uniq[k] - uniq[k - LOOKBACK_DAYS] != LOOKBACK_DAYS:
            continue
        window = absI[start[k - LOOKBACK_DAYS] : start[k]]
        window = window[~np.isnan(window)]
        if window.size:
            thr_day[k] = np.quantile(window, QUANTILE)
    out = np.empty(len(t))
    for k in range(len(uniq)):
        out[start[k] : ends[k]] = thr_day[k]
    return out


def _walk(t, o, c, imb, thr, n, h, direction):
    sig = np.flatnonzero(~np.isnan(imb) & ~np.isnan(thr) & (np.abs(imb) > thr))
    sig = sig[sig + h < len(t)]
    # La tenencia de t+1 a t+h tiene que ser continua.
    sig = sig[(t[sig + h] - t[sig + 1]) == (h - 1) * 60]
    flip = 1.0 if direction == "cont" else -1.0
    trades: list[dict[str, Any]] = []
    free_from = -1
    for i in sig:
        if i < free_from:
            continue
        side = flip * np.sign(imb[i])
        if side == 0:
            continue
        entry, exit_ = o[i + 1], c[i + h]
        gross = side * (exit_ / entry - 1.0)
        nights = int(t[i + h] // 86400 - t[i + 1] // 86400)
        swap = SWAP_LONG_FRAC_PER_DAY * nights if side > 0 else 0.0
        net = gross - SPREAD_FRAC - swap
        trades.append({"entry_t": int(t[i + 1]), "side": "BUY" if side > 0 else "SELL", "hour": int(t[i + 1] % 86400 // 3600),
                       "gross_bps": gross * 1e4, "net_bps": net * 1e4, "stress_bps": (net - SPREAD_FRAC) * 1e4})
        free_from = i + h
    # Cota superior: todos los minutos con señal, solapados, sin costos.
    tr = sig[t[sig + 1] < SPLIT]
    ret = flip * np.sign(imb[tr]) * (c[tr + h] / o[tr + 1] - 1.0) * 1e4
    info = {"train": {"n_minutos": int(tr.size), "media_bps": float(ret.mean()) if tr.size else None}}
    return trades, info


def _report(rows: list[dict[str, Any]], p_bar: float, n_min: int) -> dict[str, Any]:
    net = _stats(rows, "net_bps", np.random.default_rng(SEED))
    passed = bool(net.get("n", 0) >= n_min and net.get("media_bps", 0) > 0 and net.get("p_una_cola", 1) < p_bar
                  and (p_bar != P_TRAIN or (net.get("pf") or 0) > 1))
    by_hour = {}
    for hh in range(24):
        xs = [r["net_bps"] for r in rows if r["hour"] == hh]
        if xs:
            by_hour[f"{hh:02d}"] = {"n": len(xs), "media_bps": float(np.mean(xs))}
    return {
        "pasa": passed,
        "neto": net,
        "diag_bruto": _stats(rows, "gross_bps", np.random.default_rng(SEED)),
        "diag_estres_2x": _stats(rows, "stress_bps", np.random.default_rng(SEED)),
        "diag_por_lado": {s: _stats([r for r in rows if r["side"] == s], "net_bps", np.random.default_rng(SEED))
                          for s in ("BUY", "SELL")},
        "diag_por_hora": by_hour,
    }


def _print(out: dict[str, Any]) -> None:
    print(f"prereg sha256={out['prereg_sha256'][:12]}  vara p<{out['vara_p_train']:.5f}")
    print("celda              n      bruto   neto    IC95 neto          p       WR     PF    info_solapada")
    for name, cell in out["celdas"].items():
        for block in ("train", "holdout"):
            if block not in cell:
                continue
            r = cell[block]
            nt, br = r["neto"], r["diag_bruto"]
            if not nt.get("n"):
                print(f"{name:16} {block}: sin trades")
                continue
            info = cell["diag_info_train"]["media_bps"] if block == "train" else float("nan")
            print(f"{name:16} {nt['n']:6d}  {br['media_bps']:6.2f}  {nt['media_bps']:6.2f}  "
                  f"[{nt['ic95_bps'][0]:6.2f}, {nt['ic95_bps'][1]:6.2f}]  {nt['p_una_cola']:.4f}  "
                  f"{nt['winrate']:.1%}  {nt['pf']:.2f}  {info:6.2f}  {block} pasa={r['pasa']}")


if __name__ == "__main__":
    sys.exit(main())
