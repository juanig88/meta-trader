"""Spread contra movimiento de BTC y ETH en la terminal Exness. Solo lectura.

Pregunta: a M1, M5 y M15, ¿el precio se mueve lo suficiente para pagar el
spread? Es la misma cuenta que mató al scalp en XBTFX (spread 0.197% contra
0.147% de movimiento a 5 min), ahora con los símbolos y spreads de Exness.

- Costo: spread de los ticks de las últimas ``TICK_HOURS`` horas y el campo
  ``spread`` de las velas M1 de los últimos ``DAYS`` días. Un ida y vuelta
  paga un spread (se compra en el ask y se cierra en el bid).
- Movimiento: |cierre a +h / cierre − 1| sobre velas M1, saltando ventanas con
  huecos. También el rango (máximo − mínimo) de la ventana y qué fracción de
  ventanas llega a 2× y 3× el spread hacia algún lado.

No dice nada de dirección. Solo dice si el horizonte es jugable. Este script no
contiene envío de órdenes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from trading.domain.demo_gate import TRADE_MODE_REAL
from trading.domain.murphy_universe import MURPHY_ROOTS, resolve_universe

TRADE_MODE_FULL = 4
DAYS = 30
TICK_HOURS = 72
HORIZONS_MIN = (1, 5, 15, 60, 240)
OUT = "analyze_exness_cost_vs_move_out.json"


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(json.dumps({"error": "MetaTrader5 no esta instalado"}))
        return 2
    if not mt5.initialize():
        err = mt5.last_error()
        print(json.dumps({"error": "initialize_failed", "mt5_error": [err[0], str(err[1])]}))
        return 2
    try:
        payload = _run(mt5)
    finally:
        mt5.shutdown()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    _print_summary(payload)
    return 0 if not payload.get("error") else 2


def _run(mt5: Any) -> dict[str, Any]:
    acc = mt5.account_info()
    if acc is None:
        return {"error": "sin_cuenta"}
    trade_mode = int(getattr(acc, "trade_mode", -1))
    base: dict[str, Any] = {
        "envia_ordenes": False,
        "server": str(getattr(acc, "server", "") or ""),
        "trade_mode": trade_mode,
        "dias": DAYS,
        "horas_ticks": TICK_HOURS,
        "generado": datetime.now(timezone.utc).isoformat(),
    }
    if trade_mode == TRADE_MODE_REAL:
        base["error"] = "cuenta_real"
        return base
    names = [
        str(row.name)
        for row in (mt5.symbols_get() or ())
        if int(getattr(row, "trade_mode", 0)) == TRADE_MODE_FULL and row.name
    ]
    resolved = resolve_universe(names, tradable={name: True for name in names})
    missing = [root for root in MURPHY_ROOTS if not resolved.get(root)]
    if missing:
        base["error"] = "simbolo_ausente"
        base["ausentes"] = missing
        return base
    base["simbolos"] = {root: _one(mt5, str(resolved[root])) for root in MURPHY_ROOTS}
    return base


def _one(mt5: Any, symbol: str) -> dict[str, Any]:
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, DAYS * 1440)
    if info is None or rates is None or len(rates) < 1440:
        return {"broker": symbol, "error": "sin_velas"}
    point = float(info.point)
    times = rates["time"].astype(np.int64)
    close = rates["close"].astype(float)
    high = rates["high"].astype(float)
    low = rates["low"].astype(float)
    bar_spread_pct = rates["spread"].astype(float) * point / close * 100.0

    ticks_out = _ticks(mt5, symbol)
    # El costo de referencia es la mediana de los ticks; si no hay, la de las velas.
    cost_pct = ticks_out.get("spread_pct_mediana") or float(np.median(bar_spread_pct))

    horizons = {}
    for h in HORIZONS_MIN:
        ok = (times[h:] - times[:-h]) == h * 60
        ret = np.abs(close[h:] / close[:-h] - 1.0)[ok] * 100.0
        win_hi = _rolling_max(high, h)
        win_lo = _rolling_min(low, h)
        # Rango desde el cierre de entrada: cuánto llega a favor hacia cada lado.
        up = (win_hi[1:] / close[:-h] - 1.0)[ok[: len(win_hi) - 1]] * 100.0 if len(win_hi) > 1 else np.array([])
        dn = (1.0 - win_lo[1:] / close[:-h])[ok[: len(win_lo) - 1]] * 100.0 if len(win_lo) > 1 else np.array([])
        n = min(len(up), len(dn))
        reach = np.maximum(up[:n], dn[:n])
        med = float(np.median(ret))
        horizons[f"{h}m"] = {
            "n": int(ret.size),
            "mov_abs_pct_mediana": med,
            "mov_abs_pct_media": float(np.mean(ret)),
            "costo_sobre_mov": cost_pct / med if med > 0 else None,
            "alcance_max_pct_mediana": float(np.median(reach)) if n else None,
            "frac_mov_mayor_2x_spread": float(np.mean(ret > 2 * cost_pct)),
            "frac_mov_mayor_3x_spread": float(np.mean(ret > 3 * cost_pct)),
        }

    hour = (times % 86400) // 3600
    by_hour = {}
    ok5 = (times[5:] - times[:-5]) == 300
    ret5 = np.abs(close[5:] / close[:-5] - 1.0) * 100.0
    for hh in range(24):
        sel_bar = hour == hh
        sel5 = ok5 & (hour[:-5] == hh)
        if not sel_bar.any() or not sel5.any():
            continue
        sp = float(np.median(bar_spread_pct[sel_bar]))
        mv = float(np.median(ret5[sel5]))
        by_hour[f"{hh:02d}"] = {
            "spread_pct": sp,
            "mov5m_pct": mv,
            "costo_sobre_mov5m": sp / mv if mv > 0 else None,
        }

    contract = float(info.trade_contract_size)
    vol_min = float(info.volume_min)
    last = float(close[-1])
    return {
        "broker": symbol,
        "barras_m1": int(close.size),
        "desde": datetime.fromtimestamp(int(times[0]), tz=timezone.utc).isoformat(),
        "hasta": datetime.fromtimestamp(int(times[-1]), tz=timezone.utc).isoformat(),
        "precio": last,
        "point": point,
        "contrato": contract,
        "volume_min": vol_min,
        "volume_step": float(info.volume_step),
        "swap_mode": int(info.swap_mode),
        "swap_long": float(info.swap_long),
        "swap_short": float(info.swap_short),
        "spread_actual_puntos": int(info.spread),
        "spread_fijo": bool(info.spread_float) is False,
        "costo_pct_referencia": cost_pct,
        "costo_usd_lote_min": cost_pct / 100.0 * last * contract * vol_min,
        "spread_velas_pct": {
            "mediana": float(np.median(bar_spread_pct)),
            "p75": float(np.percentile(bar_spread_pct, 75)),
            "p90": float(np.percentile(bar_spread_pct, 90)),
        },
        "ticks": ticks_out,
        "horizontes": horizons,
        "por_hora_servidor": by_hour,
    }


def _ticks(mt5: Any, symbol: str) -> dict[str, Any]:
    end = datetime.now(timezone.utc) + timedelta(hours=3)  # margen por la hora del servidor
    start = end - timedelta(hours=TICK_HOURS + 3)
    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_INFO)
    if ticks is None or len(ticks) == 0:
        return {"n": 0}
    bid = ticks["bid"].astype(float)
    ask = ticks["ask"].astype(float)
    ok = (bid > 0) & (ask > bid)
    mid = (bid[ok] + ask[ok]) / 2.0
    sp = (ask[ok] - bid[ok]) / mid * 100.0
    return {
        "n": int(sp.size),
        "desde": datetime.fromtimestamp(int(ticks["time"][0]), tz=timezone.utc).isoformat(),
        "hasta": datetime.fromtimestamp(int(ticks["time"][-1]), tz=timezone.utc).isoformat(),
        "spread_pct_mediana": float(np.median(sp)),
        "spread_pct_p75": float(np.percentile(sp, 75)),
        "spread_pct_p90": float(np.percentile(sp, 90)),
        "spread_pct_max": float(np.max(sp)),
        "spread_usd_mediana": float(np.median(ask[ok] - bid[ok])),
    }


def _rolling_max(x: np.ndarray, h: int) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(x, h).max(axis=1)


def _rolling_min(x: np.ndarray, h: int) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(x, h).min(axis=1)


def _print_summary(payload: dict[str, Any]) -> None:
    if payload.get("error"):
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(f"server={payload['server']} trade_mode={payload['trade_mode']} envia_ordenes=False")
    for root, row in payload["simbolos"].items():
        if row.get("error"):
            print(root, row)
            continue
        t = row["ticks"]
        print(
            f"\n{row['broker']} precio={row['precio']:.2f} contrato={row['contrato']} "
            f"lote_min={row['volume_min']} swap L/S={row['swap_long']}/{row['swap_short']} (modo {row['swap_mode']})"
        )
        print(
            f"  spread ticks {t.get('n', 0)}: mediana {t.get('spread_pct_mediana', float('nan')):.4f}% "
            f"(~{t.get('spread_usd_mediana', float('nan')):.2f} USD) p90 {t.get('spread_pct_p90', float('nan')):.4f}%"
        )
        print(f"  spread velas M1 {DAYS}d: mediana {row['spread_velas_pct']['mediana']:.4f}% p90 {row['spread_velas_pct']['p90']:.4f}%")
        print(f"  costo lote min: {row['costo_usd_lote_min']:.4f} USD por ida y vuelta")
        print("  horizonte  mov_med%  costo/mov  alcance_med%  >2x  >3x")
        for k, h in row["horizontes"].items():
            print(
                f"  {k:>8}  {h['mov_abs_pct_mediana']:8.4f}  {h['costo_sobre_mov']:8.2f}x  "
                f"{h['alcance_max_pct_mediana']:11.4f}  {h['frac_mov_mayor_2x_spread']:.0%}  {h['frac_mov_mayor_3x_spread']:.0%}"
            )
    print(f"\nDetalle por hora en {OUT}")


if __name__ == "__main__":
    sys.exit(main())
