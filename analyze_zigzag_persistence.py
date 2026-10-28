"""¿Las tendencias que ya se ven siguen? Zigzag siempre adentro en BTC M5. Solo lectura.

La idea del usuario: cuando el precio ya subió (o bajó) un tramo claro, entrar a
favor y salir cuando se da vuelta. Es un zigzag: largo cuando el cierre sube
``θ`` desde el mínimo corriente, corto cuando baja ``θ`` desde el máximo, y
cada giro cierra una y abre la otra. Cada tramo gana ``tramo − 2θ`` antes de
costos. Si el mercado no tiene persistencia, la media de los tramos es ``2θ`` y
el bruto es cero.

Umbrales fijados antes de correr: 0.15%, 0.25%, 0.5%, 1%. Vara: media neta > 0
con p < 0.0025 (0.01 / 4). Datos: Binance spot BTCUSDT 1m → M5, solo el train
(hasta 2026-01-31; el holdout no se toca). Exness BTCUSDm M5 de la terminal
como contraste descriptivo. Costos de Exness: spread 0.0118% por tramo y swap
0.0222% por noche en largos. No hay órdenes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

DATA = Path("logs/binance/BTCUSDT_1m.npz")
SPLIT = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
THETAS = (0.0015, 0.0025, 0.005, 0.01)
TF_MIN = 5
SPREAD_FRAC = 0.000118
SWAP_LONG_FRAC_PER_DAY = 0.000222
BOOT_ITER = 2000
SEED = 20261027
P_BAR = 0.01 / len(THETAS)
OUT = Path("analyze_zigzag_persistence_out.json")


def main() -> int:
    d = np.load(DATA)
    t, o, c = _m5(d["time"].astype(np.int64), d["open"].astype(float), d["close"].astype(float))
    keep = t < SPLIT
    out: dict[str, Any] = {
        "envia_ordenes": False,
        "generado": datetime.now(timezone.utc).isoformat(),
        "vara_p": P_BAR,
        "binance_train": {f"{th:.4f}": _report(*_zigzag(t[keep], o[keep], c[keep], th), th) for th in THETAS},
    }
    exness = _exness()
    if exness is not None:
        out["exness_m5"] = {f"{th:.4f}": _report(*_zigzag(*exness, th), th) for th in THETAS}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for block in ("binance_train", "exness_m5"):
        if block not in out:
            continue
        print(f"\n[{block}]  theta  n  bruto_bps  neto_bps  IC95  p  WR  tramo_medio%  2theta%")
        for th, r in out[block].items():
            print(
                f"  {float(th):.2%}  {r['n']}  {r['bruto_bps']:.2f}  {r['neto_bps']:.2f}  "
                f"[{r['ic95_neto_bps'][0]:.2f}, {r['ic95_neto_bps'][1]:.2f}]  p={r['p_neto']:.4f}  "
                f"WR={r['winrate']:.1%}  {r['tramo_medio_pct']:.3f}  {2 * float(th):.2%}  pasa={r['pasa']}"
            )
    return 0


def _m5(t1: np.ndarray, o1: np.ndarray, c1: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bucket = t1 // (TF_MIN * 60)
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(t1)]
    return bucket[starts] * TF_MIN * 60, o1[starts], c1[ends - 1]


def _exness() -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not mt5.initialize():
        return None
    try:
        rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_M5, 1, 50000)
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0:
        return None
    return rates["time"].astype(np.int64), rates["open"].astype(float), rates["close"].astype(float)


def _zigzag(t: np.ndarray, o: np.ndarray, c: np.ndarray, theta: float) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Señal al cierre, llena en la apertura siguiente. Siempre adentro tras el primer giro."""
    trades: list[dict[str, Any]] = []
    side = 0
    hi = lo = c[0]
    entry_px = entry_t = None
    legs = []
    leg_start = c[0]
    for i in range(1, len(c) - 1):
        px = c[i]
        hi, lo = max(hi, px), min(lo, px)
        flip = 0
        if side <= 0 and px >= lo * (1 + theta):
            flip = 1
        elif side >= 0 and px <= hi * (1 - theta):
            flip = -1
        if not flip:
            continue
        fill, ft = o[i + 1], int(t[i + 1])
        if side != 0 and entry_px is not None:
            gross = side * (fill - entry_px) / entry_px
            nights = ft // 86400 - int(entry_t) // 86400
            swap = SWAP_LONG_FRAC_PER_DAY * nights if side > 0 else 0.0
            trades.append({"entry_t": int(entry_t), "gross": gross, "net": gross - SPREAD_FRAC - swap})
        # Tramo extremo a extremo que terminó con este giro.
        extreme = hi if flip < 0 else lo
        legs.append(abs(extreme / leg_start - 1.0))
        leg_start = extreme
        side, entry_px, entry_t = flip, fill, ft
        hi = lo = px
    return trades, np.array(legs[1:], dtype=float)


def _report(trades: list[dict[str, Any]], legs: np.ndarray, theta: float) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    net = np.array([r["net"] for r in trades]) * 1e4
    gross = np.array([r["gross"] for r in trades]) * 1e4
    days = np.array([r["entry_t"] // 86400 for r in trades])
    uniq, inv = np.unique(days, return_inverse=True)
    sums = np.bincount(inv, weights=net)
    cnts = np.bincount(inv).astype(float)
    pick = rng.integers(0, len(uniq), size=(BOOT_ITER, len(uniq)))
    means = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    p = float(np.mean(means <= 0.0))
    return {
        "theta": theta,
        "n": int(net.size),
        "dias": int(len(uniq)),
        "bruto_bps": float(gross.mean()),
        "neto_bps": float(net.mean()),
        "ic95_neto_bps": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        "p_neto": p,
        "winrate": float(np.mean(net > 0)),
        "pf": float(net[net > 0].sum() / -net[net < 0].sum()) if (net < 0).any() else None,
        "tramo_medio_pct": float(legs.mean() * 100) if legs.size else None,
        "tramo_mediano_pct": float(np.median(legs) * 100) if legs.size else None,
        "pasa": bool(net.mean() > 0 and p < P_BAR and net.size >= 100),
    }


if __name__ == "__main__":
    sys.exit(main())
