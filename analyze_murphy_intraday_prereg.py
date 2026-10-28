"""Test pre-registrado: regla Murphy de retroceso en BTC M5 / M15, costos Exness.

Diseño fijado en ``trading/docs/PREREG_MURPHY_INTRADIA.md`` antes de correr; la
salida guarda el SHA-256 de ese archivo. Reusa ``signal_from_reading`` y
``exit_reason`` de ``murphy_rule`` sin cambios. Solo lee datos: no hay órdenes.

    python analyze_murphy_intraday_prereg.py --tf 5            # train
    python analyze_murphy_intraday_prereg.py --tf 5 --reveal-holdout
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

from trading.domain.murphy_rule import exit_reason, signal_from_reading
from trading.domain.murphy_tools import stop_fill
from trading.domain.murphy_trend import read_degrees

PREREG = Path("trading/docs/PREREG_MURPHY_INTRADIA.md")
DATA = Path("logs/binance/BTCUSDT_1m.npz")
SPLIT = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
WINDOW = 180
SPREAD_FRAC = 0.000118
SWAP_LONG_FRAC_PER_DAY = 0.000222
BOOT_ITER = 2000
SEED = 20261027
# Vara del pre-registro.
MIN_TRADES = 100
P_TRAIN = 0.005
P_HOLDOUT = 0.05


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, choices=(5, 15), required=True)
    ap.add_argument("--reveal-holdout", action="store_true")
    args = ap.parse_args()

    t, o, h, l, c = _resample(np.load(DATA), args.tf)
    if args.reveal_holdout:
        end = len(c)
    else:
        # Train: velas hasta el corte más 3 días para cerrar lo que quedó abierto.
        end = int(np.searchsorted(t, SPLIT + 3 * 86400))
    trades = _walk(t[:end], o[:end], h[:end], l[:end], c[:end])
    train = [r for r in trades if r["entry_t"] < SPLIT]
    hold = [r for r in trades if r["entry_t"] >= SPLIT]

    out: dict[str, Any] = {
        "envia_ordenes": False,
        "prereg": str(PREREG),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "generado": datetime.now(timezone.utc).isoformat(),
        "simbolo": "BTCUSDT (Binance spot, proxy de BTCUSDm)",
        "tf_min": args.tf,
        "barras": int(len(c)),
        "costos": {"spread_frac": SPREAD_FRAC, "swap_largo_frac_dia": SWAP_LONG_FRAC_PER_DAY, "comision": 0.0},
        "train": _report(train, P_TRAIN),
    }
    if args.reveal_holdout:
        out["holdout"] = _report(hold, P_HOLDOUT)
    path = Path(f"analyze_murphy_intraday_prereg_m{args.tf}{'_holdout' if args.reveal_holdout else ''}_out.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(out)
    return 0


def _resample(d: Any, tf: int) -> tuple[np.ndarray, ...]:
    t1 = d["time"].astype(np.int64)
    bucket = t1 // (tf * 60)
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(t1)]
    t = bucket[starts] * tf * 60
    o = d["open"].astype(float)[starts]
    c = d["close"].astype(float)[ends - 1]
    h = np.maximum.reduceat(d["high"].astype(float), starts)
    l = np.minimum.reduceat(d["low"].astype(float), starts)
    return t, o, h, l, c


def _walk(t: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> list[dict[str, Any]]:
    hl, ll, cl, ol = h.tolist(), l.tolist(), c.tolist(), o.tolist()
    n = len(cl)
    trades: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    for i in range(WINDOW - 1, n - 1):
        lo = i - WINDOW + 1
        reading = read_degrees(hl[lo : i + 1], ll[lo : i + 1], cl[lo : i + 1])
        if pos is None:
            sig = signal_from_reading(reading)
            if sig["accion"] != "entrar":
                continue
            entry = ol[i + 1]
            stop = float(sig["stop"])
            side = str(sig["lado"])
            if (side == "BUY" and not stop < entry) or (side == "SELL" and not stop > entry):
                continue
            pos = {"side": side, "entry": entry, "stop": stop, "entry_i": i + 1, "entry_t": int(t[i + 1])}
            continue
        if i < pos["entry_i"]:
            continue
        hit = stop_fill(pos["side"], pos["stop"], ol[i], hl[i], ll[i])
        if hit is not None:
            trades.append(_close(pos, hit, int(t[i]), i, "stop"))
            pos = None
            continue
        reason = exit_reason(reading, pos["side"])
        if reason:
            trades.append(_close(pos, ol[i + 1], int(t[i + 1]), i + 1, reason))
            pos = None
    if pos is not None:
        trades.append(_close(pos, cl[-1], int(t[-1]), n - 1, "fin_de_serie"))
    return trades


def _close(pos: dict[str, Any], exit_px: float, exit_t: int, exit_i: int, reason: str) -> dict[str, Any]:
    sign = 1.0 if pos["side"] == "BUY" else -1.0
    gross = sign * (float(exit_px) - pos["entry"]) / pos["entry"]
    nights = exit_t // 86400 - pos["entry_t"] // 86400
    swap = SWAP_LONG_FRAC_PER_DAY * nights if pos["side"] == "BUY" else 0.0
    opp_swap = SWAP_LONG_FRAC_PER_DAY * nights if pos["side"] == "SELL" else 0.0
    risk = abs(pos["entry"] - pos["stop"]) / pos["entry"]
    net = gross - SPREAD_FRAC - swap
    return {
        "side": pos["side"],
        "entry_t": pos["entry_t"],
        "exit_t": exit_t,
        "bars": exit_i - pos["entry_i"],
        "reason": reason,
        "gross_bps": gross * 1e4,
        "net_bps": net * 1e4,
        "stress_bps": (net - SPREAD_FRAC) * 1e4,
        "opp_net_bps": (-gross - SPREAD_FRAC - opp_swap) * 1e4,
        "r": net / risk if risk > 0 else None,
    }


def _stats(rows: list[dict[str, Any]], key: str, rng: np.random.Generator) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    x = np.array([r[key] for r in rows], dtype=float)
    days = np.array([r["entry_t"] // 86400 for r in rows])
    uniq, inv = np.unique(days, return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnts = np.bincount(inv).astype(float)
    pick = rng.integers(0, len(uniq), size=(BOOT_ITER, len(uniq)))
    means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    return {
        "n": int(x.size),
        "dias": int(len(uniq)),
        "media_bps": float(x.mean()),
        "ic95_bps": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        "p_una_cola": float(np.mean(means <= 0.0)),
        "winrate": float(np.mean(x > 0)),
        "pf": float(wins / losses) if losses > 0 else None,
        "suma_bps": float(x.sum()),
    }


def _report(rows: list[dict[str, Any]], p_bar: float) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    net = _stats(rows, "net_bps", rng)
    passed = bool(
        net.get("n", 0) >= (MIN_TRADES if p_bar == P_TRAIN else 1)
        and net.get("media_bps", 0) > 0
        and net.get("p_una_cola", 1) < p_bar
        and (p_bar != P_TRAIN or (net.get("pf") or 0) > 1)
    )
    rs = [r["r"] for r in rows if r["r"] is not None]
    return {
        "pasa": passed,
        "vara": {"n_min": MIN_TRADES if p_bar == P_TRAIN else None, "p": p_bar},
        "neto": net,
        "diag_bruto": _stats(rows, "gross_bps", np.random.default_rng(SEED)),
        "diag_estres_2x": _stats(rows, "stress_bps", np.random.default_rng(SEED)),
        "diag_lado_opuesto": _stats(rows, "opp_net_bps", np.random.default_rng(SEED)),
        "diag_por_lado": {
            s: _stats([r for r in rows if r["side"] == s], "net_bps", np.random.default_rng(SEED)) for s in ("BUY", "SELL")
        },
        "diag_por_salida": {
            k: {"n": sum(1 for r in rows if r["reason"] == k),
                "media_bps": float(np.mean([r["net_bps"] for r in rows if r["reason"] == k]))}
            for k in sorted({r["reason"] for r in rows})
        },
        "r_medio": float(np.mean(rs)) if rs else None,
        "barras_mediana": float(np.median([r["bars"] for r in rows])) if rows else None,
    }


def _print(out: dict[str, Any]) -> None:
    print(f"M{out['tf_min']}  barras={out['barras']}  prereg sha256={out['prereg_sha256'][:12]}")
    for block in ("train", "holdout"):
        if block not in out:
            continue
        rep = out[block]
        nt = rep["neto"]
        print(f"\n[{block}] PASA={rep['pasa']}")
        if not nt.get("n"):
            print("  sin trades")
            continue
        print(
            f"  neto: n={nt['n']} dias={nt['dias']} media={nt['media_bps']:.2f} bps "
            f"IC95=[{nt['ic95_bps'][0]:.2f}, {nt['ic95_bps'][1]:.2f}] p={nt['p_una_cola']:.4f} "
            f"WR={nt['winrate']:.1%} PF={nt['pf']:.2f}"
        )
        for k, label in (("diag_bruto", "bruto"), ("diag_estres_2x", "estres 2x"), ("diag_lado_opuesto", "opuesto")):
            s = rep[k]
            print(f"  {label:>9}: media={s['media_bps']:.2f} bps p={s['p_una_cola']:.4f} PF={s['pf']:.2f}")
        for side, s in rep["diag_por_lado"].items():
            if s.get("n"):
                print(f"  {side:>9}: n={s['n']} media={s['media_bps']:.2f} bps WR={s['winrate']:.1%}")
        print("  salidas: " + ", ".join(f"{k} {v['n']} ({v['media_bps']:.1f})" for k, v in rep["diag_por_salida"].items()))
        print(f"  R medio={rep['r_medio']:.3f}  barras mediana={rep['barras_mediana']}")


if __name__ == "__main__":
    sys.exit(main())
