"""Reversión de corto plazo en BTC con guard. Una sola corrida, en el holdout.

Diseño fijado en ``trading/docs/PREREG_REVERSION_CORTA.md`` antes de mirar; la salida
guarda el SHA-256 de ese archivo. Si la salida ya existe, el script no vuelve a correr.
Solo lee datos: no hay órdenes.

    python analyze_reversal_holdout_prereg.py --check   # conteos en el train, sin retornos
    python analyze_reversal_holdout_prereg.py           # la corrida única sobre el holdout
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

PREREG = Path("trading/docs/PREREG_REVERSION_CORTA.md")
DATA = Path("logs/binance/flow/BTCUSDT_1m_flow.npz")
OUT = Path("analyze_reversal_holdout_prereg_out.json")
HOLD_FROM = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
HOLD_TO = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
NS = (5, 15)
HS = (15, 30)
TRIGGER = 1.5
BRUSCO = 3.0
SEGUIDAS = 0.8
BASE_DAYS = 30
SPREAD_FRAC = 0.000118
SWAP_LONG_FRAC_PER_DAY = 0.000222
MIN_TRADES = 200
P_BAR = 0.01 / (len(NS) * len(HS))
SEED = 20261027


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="conteos en el train, sin retornos")
    args = ap.parse_args()
    if not args.check and OUT.exists():
        print(f"{OUT} ya existe: la prueba es una sola vez.", file=sys.stderr)
        return 2

    d = np.load(DATA)
    t = d["time"].astype(np.int64)
    o, c = d["open"].astype(float), d["close"].astype(float)
    if args.check:
        lo, hi = int(datetime(2024, 11, 1, tzinfo=timezone.utc).timestamp()), HOLD_FROM
    else:
        lo, hi = HOLD_FROM, HOLD_TO

    out: dict[str, Any] = {
        "envia_ordenes": False,
        "prereg": str(PREREG),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "generado": datetime.now(timezone.utc).isoformat(),
        "tramo": [datetime.fromtimestamp(lo, timezone.utc).isoformat(), datetime.fromtimestamp(hi, timezone.utc).isoformat()],
        "vara_p": P_BAR,
        "celdas": {},
    }
    for n in NS:
        move = _move(t, c, n)
        normal = _normal(t, move)
        frac_same = _same_side_fraction(t, o, c, move, n)
        trig = ~np.isnan(move) & ~np.isnan(normal) & (np.abs(move) > TRIGGER * normal)
        brusco = trig & (np.abs(move) > BRUSCO * normal)
        seguidas = trig & ~brusco & (frac_same >= SEGUIDAS)
        ok = trig & ~brusco & ~seguidas
        for h in HS:
            name = f"N{n}_H{h}"
            window = (t >= lo) & (t < hi)
            trades = _walk(t, o, c, move, ok & window, h)
            if args.check:
                out["celdas"][name] = {"senales": int((ok & window).sum()), "trades": len(trades),
                                       "brusco": int((brusco & window).sum()), "seguidas": int((seguidas & window).sum())}
                continue
            out["celdas"][name] = {
                **_report(trades),
                "diag_sin_guard": _stats(_walk(t, o, c, move, trig & window, h), "net_bps", np.random.default_rng(SEED)),
                "diag_guard_brusco": _overlap(t, o, c, move, brusco & window, h),
                "diag_guard_seguidas": _overlap(t, o, c, move, seguidas & window, h),
            }
    if args.check:
        print(json.dumps(out["celdas"], indent=1))
        return 0
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(out)
    return 0


def _move(t: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(t), np.nan)
    ok = (t[n:] - t[:-n]) == n * 60
    out[n:] = np.where(ok, c[n:] / c[:-n] - 1.0, np.nan)
    return out


def _normal(t: np.ndarray, move: np.ndarray) -> np.ndarray:
    """Median |M| at the same UTC hour over the 30 previous days."""
    day = t // 86400
    d0 = int(day.min())
    di = (day - d0).astype(int)
    hour = (t % 86400 // 3600).astype(int)
    minute = (t % 3600 // 60).astype(int)
    grid = np.full((int(di.max()) + 1, 24, 60), np.nan)
    grid[di, hour, minute] = np.abs(move)
    med = np.full((grid.shape[0], 24), np.nan)
    for k in range(BASE_DAYS, grid.shape[0]):
        block = grid[k - BASE_DAYS : k].transpose(1, 0, 2).reshape(24, -1)
        counts = (~np.isnan(block)).sum(axis=1)
        with np.errstate(all="ignore"):
            m = np.nanmedian(np.where(counts[:, None] > 0, block, 0.0), axis=1)
        med[k] = np.where(counts > 0, m, np.nan)
    return med[di, hour]


def _same_side_fraction(t, o, c, move, n):
    up = np.r_[0.0, np.cumsum((c > o).astype(float))]
    dn = np.r_[0.0, np.cumsum((c < o).astype(float))]
    out = np.full(len(t), np.nan)
    i = np.arange(n - 1, len(t))
    ups = (up[i + 1] - up[i + 1 - n]) / n
    dns = (dn[i + 1] - dn[i + 1 - n]) / n
    out[i] = np.where(move[i] > 0, ups, dns)
    return out


def _trade(t, o, c, move, i, h) -> dict[str, Any]:
    side = -np.sign(move[i])
    entry, exit_ = o[i + 1], c[i + h]
    gross = side * (exit_ / entry - 1.0)
    nights = int(t[i + h] // 86400 - t[i + 1] // 86400)
    swap = SWAP_LONG_FRAC_PER_DAY * nights if side > 0 else 0.0
    net = gross - SPREAD_FRAC - swap
    return {"entry_t": int(t[i + 1]), "side": "BUY" if side > 0 else "SELL", "hour": int(t[i + 1] % 86400 // 3600),
            "gross_bps": gross * 1e4, "net_bps": net * 1e4, "stress_bps": (net - SPREAD_FRAC) * 1e4}


def _valid(t, sig, h):
    sig = sig[sig + h < len(t)]
    return sig[(t[sig + h] - t[sig + 1]) == (h - 1) * 60]


def _walk(t, o, c, move, mask, h) -> list[dict[str, Any]]:
    trades, free_from = [], -1
    for i in _valid(t, np.flatnonzero(mask), h):
        if i < free_from or move[i] == 0:
            continue
        trades.append(_trade(t, o, c, move, i, h))
        free_from = i + h
    return trades


def _overlap(t, o, c, move, mask, h) -> dict[str, Any]:
    rows = [_trade(t, o, c, move, i, h) for i in _valid(t, np.flatnonzero(mask), h) if move[i] != 0]
    return {"n_minutos": len(rows), "neto_medio_bps": float(np.mean([r["net_bps"] for r in rows])) if rows else None}


def _report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net = _stats(rows, "net_bps", np.random.default_rng(SEED))
    passed = bool(net.get("n", 0) >= MIN_TRADES and net.get("media_bps", 0) > 0
                  and net.get("p_una_cola", 1) < P_BAR and (net.get("pf") or 0) > 1)
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
    print(f"HOLDOUT {out['tramo'][0][:10]} -> {out['tramo'][1][:10]}  prereg sha256={out['prereg_sha256'][:12]}  vara p<{out['vara_p']}")
    for name, r in out["celdas"].items():
        nt, br, st = r["neto"], r["diag_bruto"], r["diag_estres_2x"]
        if not nt.get("n"):
            print(f"{name}: sin trades")
            continue
        print(f"\n{name}  PASA={r['pasa']}")
        print(f"  neto: n={nt['n']} media={nt['media_bps']:.2f} bps IC95=[{nt['ic95_bps'][0]:.2f}, {nt['ic95_bps'][1]:.2f}] "
              f"p={nt['p_una_cola']:.4f} WR={nt['winrate']:.1%} PF={nt['pf']:.2f}")
        print(f"  bruto={br['media_bps']:.2f} bps  estres 2x={st['media_bps']:.2f} bps")
        for s, x in r["diag_por_lado"].items():
            if x.get("n"):
                print(f"  {s}: n={x['n']} media={x['media_bps']:.2f} bps")
        sg = r["diag_sin_guard"]
        print(f"  sin guard: n={sg.get('n')} media={sg.get('media_bps', float('nan')):.2f} bps")
        print(f"  guard brusco: {r['diag_guard_brusco']}  guard seguidas: {r['diag_guard_seguidas']}")


if __name__ == "__main__":
    sys.exit(main())
