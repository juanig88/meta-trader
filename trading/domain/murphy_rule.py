"""One dry rule from the trend chapter. Pure: no orders.

Enter with the intermediate trend, during a minor move the other way, while
the retracement has not passed two thirds. Exit when that premise breaks:
the close crosses the last swing, the retrace passes two thirds, the
intermediate regime flips, or it conflicts with the major trend.

The journal fills the next open and charges the spread. It does not search
for a better rule.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from trading.domain.murphy_tools import r_multiple, stop_fill
from trading.domain.murphy_trend import read_degrees

ReadFn = Callable[..., dict[str, Any]]


def signal_from_reading(reading: dict[str, Any]) -> dict[str, Any]:
    base = {"envia_ordenes": False, "accion": "nada", "lado": None, "motivo": "afuera", "stop": None}
    grados = reading.get("grados") or {}
    inter = grados.get("intermedia") or {}
    menor = grados.get("menor") or {}
    if not inter.get("suficiente") or not menor.get("suficiente"):
        base["motivo"] = "sin_datos"
        return base
    if reading.get("conflicto_principal"):
        base["motivo"] = "conflicto_principal"
        return base
    regime = inter.get("regime")
    if regime == "lateral":
        base["motivo"] = "lateral"
        return base
    if regime not in ("alcista", "bajista"):
        base["motivo"] = "sin_datos"
        return base
    if inter.get("premisa_invalidada"):
        base["accion"] = "salir"
        base["lado"] = "BUY" if regime == "alcista" else "SELL"
        base["motivo"] = "premisa"
        return base
    retro = inter.get("retroceso") or {}
    frac = retro.get("frac_cierre")
    if frac is None or float(frac) <= 0.0 or retro.get("pasa_dos_tercios"):
        base["motivo"] = "sin_retroceso_operable"
        return base
    # The minor correction is the retracement itself. A separate minor regime
    # can never oppose the intermediate one: both windows end on the same bar
    # and share their last pivots (0 of 3153 daily BTC readings).
    stop = (inter.get("apoyo_previo") or {}).get("ultimo")
    if stop is None:
        base["motivo"] = "sin_stop"
        return base
    base["accion"] = "entrar"
    base["lado"] = "BUY" if regime == "alcista" else "SELL"
    base["motivo"] = "retroceso_menor"
    base["stop"] = float(stop)
    return base


def exit_reason(reading: dict[str, Any], side: str) -> Optional[str]:
    inter = (reading.get("grados") or {}).get("intermedia") or {}
    regime = inter.get("regime")
    want = "alcista" if side == "BUY" else "bajista"
    if reading.get("conflicto_principal"):
        return "conflicto_principal"
    if regime != want:
        return f"regime_{regime}"
    if inter.get("premisa_invalidada"):
        return "premisa"
    return None


def walk_journal(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    opens: Sequence[float],
    *,
    spread_price: float = 0.0,
    holdout_bars: int = 60,
    read: ReadFn = read_degrees,
) -> dict[str, Any]:
    """Close-to-next-open journal of the one rule. ``envia_ordenes`` stays false."""
    n = len(closes)
    if not (len(highs) == len(lows) == n == len(opens)) or n < 182:
        return _summary([], holdout_from=0, bars=n, spread_price=spread_price)
    holdout_from = max(0, n - int(holdout_bars))
    trades: list[dict[str, Any]] = []
    rechazos: dict[str, int] = {}
    position: Optional[dict[str, Any]] = None
    spread = max(0.0, float(spread_price))
    for i in range(180, n - 1):
        reading = read(highs[: i + 1], lows[: i + 1], closes[: i + 1])
        if position is None:
            sig = signal_from_reading(reading)
            if sig["accion"] != "entrar":
                key = str(sig["motivo"])
                rechazos[key] = rechazos.get(key, 0) + 1
                continue
            position = _try_entry(opens, sig, i + 1, spread, signal_i=i)
            if position is None:
                rechazos["stop_invalido"] = rechazos.get("stop_invalido", 0) + 1
            continue
        if i < int(position["entry_i"]):
            continue
        hit = stop_fill(position["side"], position["stop"], opens[i], highs[i], lows[i])
        if hit is not None:
            _close(trades, position, hit, i, "stop")
            position = None
            continue
        reason = exit_reason(reading, position["side"])
        if reason:
            fill = float(opens[i + 1])
            exit_px = fill - spread if position["side"] == "BUY" else fill + spread
            _close(trades, position, exit_px, i + 1, reason)
            position = None
    if position is not None:
        last = n - 1
        fill = float(closes[last])
        exit_px = fill - spread if position["side"] == "BUY" else fill + spread
        _close(trades, position, exit_px, last, "fin_de_serie")
    out = _summary(trades, holdout_from=holdout_from, bars=n, spread_price=spread)
    out["rechazos"] = rechazos
    return out


def _try_entry(
    opens: Sequence[float],
    sig: dict[str, Any],
    entry_i: int,
    spread: float,
    *,
    signal_i: int,
) -> Optional[dict[str, Any]]:
    if entry_i >= len(opens):
        return None
    side = str(sig["lado"])
    raw = float(opens[entry_i])
    entry = raw + spread if side == "BUY" else raw - spread
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
        "stop": stop,
        "risk": risk,
        "entry_i": entry_i,
        "signal_i": signal_i,
    }


def _close(
    trades: list[dict[str, Any]],
    position: dict[str, Any],
    exit_px: float,
    exit_i: int,
    reason: str,
) -> None:
    multiple = r_multiple(position["entry"], exit_px, position["risk"], position["side"])
    trades.append({
        "lado": position["side"],
        "entrada_i": position["entry_i"],
        "salida_i": exit_i,
        "entrada": position["entry"],
        "salida": float(exit_px),
        "stop": position["stop"],
        "r": multiple,
        "motivo_salida": reason,
        "barras": int(exit_i) - int(position["entry_i"]),
    })


def _summary(
    trades: list[dict[str, Any]],
    *,
    holdout_from: int,
    bars: int,
    spread_price: float,
) -> dict[str, Any]:
    return {
        "envia_ordenes": False,
        "regla": "retroceso_menor",
        "barras": bars,
        "spread_precio": spread_price,
        "todo": _block(trades),
        "holdout": _block([t for t in trades if int(t["entrada_i"]) >= holdout_from]),
        "holdout_desde_i": holdout_from,
        "operaciones": trades,
    }


def _block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [float(t["r"]) for t in trades if t.get("r") is not None]
    n = len(rs)
    wins = sum(1 for r in rs if r > 0.0)
    return {
        "n": n,
        "ganadoras": wins,
        "winrate": (wins / n) if n else None,
        "r_medio": (sum(rs) / n) if n else None,
        "r_suma": sum(rs) if n else 0.0,
    }
