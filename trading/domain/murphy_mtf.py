"""H4 direction, M15 pullback. Pure: no orders, no network.

The rule was fixed before any H4/M15 result:

- Trade only with the H4 regime. Lateral H4 means no order.
- Enter on M15 while that timeframe is giving back the last impulse and the
  giveback has not passed two thirds.
- Exit if the M15 close crosses the swing recorded at entry, if the giveback
  passes two thirds, or if H4 leaves that direction.

Lookbacks are detection windows, chosen before the measurement: 120 H4 bars
(20 days) and 96 M15 bars (one day). They are not tuned to a win rate.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from trading.domain.murphy_tools import r_multiple, stop_fill
from trading.domain.murphy_trend import TWO_THIRDS, read_structure
from trading.domain.swing_structure import confirmed_pivots

H4_LOOKBACK = 120
M15_LOOKBACK = 96
H4_MS = 4 * 60 * 60 * 1000
M15_MS = 15 * 60 * 1000
# Binance spot default taker, no BNB discount. Charged on the way in and out.
BINANCE_TAKER_FEE = 0.001


def h4_is_closed(h4_open_ms: int, m15_open_ms: int) -> bool:
    """An H4 bar is visible only after it has closed, at or before the M15 close."""
    return int(h4_open_ms) + H4_MS <= int(m15_open_ms) + M15_MS


def retrace_with_higher(pivots: Sequence[dict[str, Any]], close: float, side: str) -> Optional[dict[str, float]]:
    """Giveback of the last impulse in the higher-timeframe direction."""
    highs = [p for p in pivots if p["kind"] == "high"]
    lows = [p for p in pivots if p["kind"] == "low"]
    if side == "BUY":
        if not highs or not lows:
            return None
        hi = highs[-1]
        prior = [p for p in lows if int(p["index"]) < int(hi["index"])]
        if not prior:
            return None
        lo = prior[-1]
        span = float(hi["price"]) - float(lo["price"])
        if span <= 0.0:
            return None
        return {
            "frac": (float(hi["price"]) - float(close)) / span,
            "stop": float(lows[-1]["price"]),
        }
    if side == "SELL":
        if not highs or not lows:
            return None
        lo = lows[-1]
        prior = [p for p in highs if int(p["index"]) < int(lo["index"])]
        if not prior:
            return None
        hi = prior[-1]
        span = float(hi["price"]) - float(lo["price"])
        if span <= 0.0:
            return None
        return {
            "frac": (float(close) - float(lo["price"])) / span,
            "stop": float(highs[-1]["price"]),
        }
    return None


def decision(h4_regime: str, pivots: Sequence[dict[str, Any]], close: float) -> dict[str, Any]:
    base = {"envia_ordenes": False, "accion": "nada", "lado": None, "motivo": "sin_datos", "stop": None, "frac": None}
    if h4_regime == "lateral":
        base["motivo"] = "h4_lateral"
        return base
    if h4_regime not in ("alcista", "bajista"):
        base["motivo"] = "h4_sin_datos"
        return base
    side = "BUY" if h4_regime == "alcista" else "SELL"
    retrace = retrace_with_higher(pivots, close, side)
    if retrace is None:
        base["motivo"] = "sin_impulso"
        base["lado"] = side
        return base
    frac = float(retrace["frac"])
    base["frac"] = frac
    base["lado"] = side
    base["stop"] = float(retrace["stop"])
    if frac <= 0.0:
        base["motivo"] = "sin_retroceso"
        return base
    if frac > TWO_THIRDS + 1e-9:
        base["accion"] = "salir"
        base["motivo"] = "pasa_dos_tercios"
        return base
    base["accion"] = "entrar"
    base["motivo"] = "retroceso_m15"
    return base


def exit_reason(h4_regime: str, side: str, close: float, stop: float, frac: Optional[float]) -> Optional[str]:
    want = "alcista" if side == "BUY" else "bajista"
    if h4_regime != want:
        return "h4_cambio"
    if side == "BUY" and float(close) < float(stop):
        return "cierra_bajo_valle"
    if side == "SELL" and float(close) > float(stop):
        return "cierra_sobre_pico"
    if frac is not None and float(frac) > TWO_THIRDS + 1e-9:
        return "pasa_dos_tercios"
    return None


def _pivots(highs: Sequence[float], lows: Sequence[float]) -> list[dict[str, Any]]:
    return [{"index": i, "price": p, "kind": k} for i, p, k in confirmed_pivots(highs, lows)]


def _fee_price(price: float, side: str, *, entering: bool, fee: float) -> float:
    pay = fee if entering else -fee
    if side == "SELL":
        pay = -pay
    return float(price) * (1.0 + pay)


def walk_h4_m15(
    m15_open_ms: Sequence[int],
    m15_open: Sequence[float],
    m15_high: Sequence[float],
    m15_low: Sequence[float],
    m15_close: Sequence[float],
    h4_open_ms: Sequence[int],
    h4_high: Sequence[float],
    h4_low: Sequence[float],
    h4_close: Sequence[float],
    *,
    fee: float = BINANCE_TAKER_FEE,
    spread_price: float = 0.0,
    holdout_frac: float = 0.2,
) -> dict[str, Any]:
    """Next-open journal. Binance fee is inside R. Exness spread is a second column."""
    n = len(m15_close)
    empty = _summary([], holdout_from=0, bars=n, fee=fee, spread_price=spread_price)
    if not (len(m15_open_ms) == len(m15_open) == len(m15_high) == len(m15_low) == n):
        return empty
    if n < M15_LOOKBACK + 2 or len(h4_close) < H4_LOOKBACK:
        return empty
    trades: list[dict[str, Any]] = []
    rechazos: dict[str, int] = {}
    position: Optional[dict[str, Any]] = None
    was_in = False
    h4_i = 0
    h4_regime = "insuficiente"
    h4_ready_until = -1
    for i in range(M15_LOOKBACK, n - 1):
        while h4_i < len(h4_open_ms) and h4_is_closed(h4_open_ms[h4_i], m15_open_ms[i]):
            h4_i += 1
        visible = h4_i
        if visible != h4_ready_until:
            h4_ready_until = visible
            if visible >= H4_LOOKBACK:
                window = read_structure(
                    h4_high[visible - H4_LOOKBACK : visible],
                    h4_low[visible - H4_LOOKBACK : visible],
                    h4_close[visible - H4_LOOKBACK : visible],
                )
                h4_regime = str(window["regime"])
            else:
                h4_regime = "insuficiente"
        pivots = _pivots(m15_high[i - M15_LOOKBACK + 1 : i + 1], m15_low[i - M15_LOOKBACK + 1 : i + 1])
        sig = decision(h4_regime, pivots, m15_close[i])
        retrace = retrace_with_higher(pivots, m15_close[i], position["side"]) if position else None
        frac_now = None if retrace is None else float(retrace["frac"])
        if position is not None and i >= int(position["entry_i"]):
            hit = stop_fill(position["side"], position["stop"], m15_open[i], m15_high[i], m15_low[i])
            if hit is not None:
                _close(trades, position, hit, i, "stop", fee, spread_price)
                position = None
                was_in = False
                continue
            reason = exit_reason(h4_regime, position["side"], m15_close[i], position["stop"], frac_now)
            if reason:
                _close(trades, position, m15_open[i + 1], i + 1, reason, fee, spread_price)
                position = None
                was_in = False
                continue
        if position is None:
            enter = sig["accion"] == "entrar" and not was_in
            was_in = sig["accion"] == "entrar"
            if not enter:
                if sig["accion"] != "entrar":
                    key = str(sig["motivo"])
                    rechazos[key] = rechazos.get(key, 0) + 1
                continue
            opened = _try_entry(m15_open, sig, i + 1, fee, spread_price, signal_i=i)
            if opened is None:
                rechazos["stop_invalido"] = rechazos.get("stop_invalido", 0) + 1
                continue
            position = opened
    if position is not None:
        _close(trades, position, m15_close[-1], n - 1, "fin_de_serie", fee, spread_price)
    holdout_from = int(n * (1.0 - holdout_frac))
    out = _summary(trades, holdout_from=holdout_from, bars=n, fee=fee, spread_price=spread_price)
    out["rechazos"] = rechazos
    return out


def _try_entry(
    opens: Sequence[float],
    sig: dict[str, Any],
    entry_i: int,
    fee: float,
    spread_price: float,
    *,
    signal_i: int,
) -> Optional[dict[str, Any]]:
    if entry_i >= len(opens):
        return None
    side = str(sig["lado"])
    raw = float(opens[entry_i])
    entry = _fee_price(raw, side, entering=True, fee=fee)
    stop = float(sig["stop"])
    if side == "BUY" and not stop < entry:
        return None
    if side == "SELL" and not stop > entry:
        return None
    risk = abs(entry - stop)
    if risk <= 0.0:
        return None
    return {
        "side": side,
        "entry": entry,
        "raw_entry": raw,
        "stop": stop,
        "risk": risk,
        "entry_i": entry_i,
        "signal_i": signal_i,
        "fee": fee,
        "spread_price": spread_price,
    }


def _close(
    trades: list[dict[str, Any]],
    position: dict[str, Any],
    raw_exit: float,
    exit_i: int,
    reason: str,
    fee: float,
    spread_price: float,
) -> None:
    side = position["side"]
    exit_fee = _fee_price(raw_exit, side, entering=False, fee=fee)
    multiple = r_multiple(position["entry"], exit_fee, position["risk"], side)
    spread = max(0.0, float(spread_price))
    raw_in = float(position["raw_entry"])
    ex_in = raw_in + spread if side == "BUY" else raw_in - spread
    ex_out = float(raw_exit) - spread if side == "BUY" else float(raw_exit) + spread
    ex_risk = abs(ex_in - float(position["stop"]))
    ex_r = r_multiple(ex_in, ex_out, ex_risk, side) if ex_risk > 0.0 else None
    trades.append({
        "lado": side,
        "entrada_i": position["entry_i"],
        "salida_i": exit_i,
        "r_binance": multiple,
        "r_exness_spread": ex_r,
        "motivo_salida": reason,
        "barras": int(exit_i) - int(position["entry_i"]),
    })


def _summary(
    trades: list[dict[str, Any]],
    *,
    holdout_from: int,
    bars: int,
    fee: float,
    spread_price: float,
) -> dict[str, Any]:
    return {
        "envia_ordenes": False,
        "regla": "h4_direccion_m15_retroceso",
        "barras": bars,
        "fee_lado": fee,
        "spread_precio_exness": spread_price,
        "todo": _block(trades),
        "holdout": _block([t for t in trades if int(t["entrada_i"]) >= holdout_from]),
        "holdout_desde_i": holdout_from,
        "operaciones": trades,
    }


def _block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    binance = [float(t["r_binance"]) for t in trades if t.get("r_binance") is not None]
    exness = [float(t["r_exness_spread"]) for t in trades if t.get("r_exness_spread") is not None]
    return {
        "n": len(binance),
        "ganadoras_binance": sum(1 for r in binance if r > 0.0),
        "esperanza_r_binance": (sum(binance) / len(binance)) if binance else None,
        "r_suma_binance": sum(binance) if binance else 0.0,
        "esperanza_r_exness": (sum(exness) / len(exness)) if exness else None,
    }
