"""Chequeo previo de una operación manual. Puro: sin terminal ni órdenes.

Tamaño por riesgo, relación riesgo/beneficio y las razones para no entrar. Las
alertas salen de lo que se midió en este repo (`INTRADIA_EXNESS.md`), no de una
opinión sobre la dirección: el chequeo nunca dice si comprar o vender.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

PTJ_MIN_RR = 5.0
SPREAD_MAX_FRAC_OF_RISK = 0.10
CHASE_MULT = 1.5


@dataclass(frozen=True)
class Plan:
    side: str  # "BUY" o "SELL"
    entry: float
    stop: float
    take_profit: float


@dataclass(frozen=True)
class Context:
    usd_per_price_unit: float  # por 1 lote
    volume_min: float
    volume_step: float
    spread_price: float
    sma200_d1: Optional[float]
    trends: dict[str, str] = field(default_factory=dict)  # {"D1": "alcista", ...}
    typical_move_15m: Optional[float] = None  # mediana |Δ precio| en 15 min
    last_move_15m: Optional[float] = None  # precio ahora − hace 15 min


def validate(plan: Plan) -> Optional[str]:
    if plan.side not in ("BUY", "SELL"):
        return "lado debe ser BUY o SELL"
    if plan.side == "BUY" and not plan.stop < plan.entry < plan.take_profit:
        return "en un BUY el stop va debajo de la entrada y el take profit arriba"
    if plan.side == "SELL" and not plan.take_profit < plan.entry < plan.stop:
        return "en un SELL el stop va arriba de la entrada y el take profit debajo"
    return None


def reward_risk(plan: Plan) -> float:
    return abs(plan.take_profit - plan.entry) / abs(plan.entry - plan.stop)


def breakeven_winrate(rr: float) -> float:
    """Win rate que deja en cero, sin costos, una entrada sin ventaja con este R:R."""
    return 1.0 / (1.0 + rr)


def size_lots(*, risk_usd: float, plan: Plan, ctx: Context) -> tuple[float, float]:
    """Lotes redondeados hacia abajo al step, y el riesgo real en USD con esos lotes.

    Si ni el lote mínimo entra en el riesgo, devuelve el lote mínimo y su riesgo real:
    el que decide si lo acepta es el chequeo, no el redondeo.
    """
    per_lot = abs(plan.entry - plan.stop) * ctx.usd_per_price_unit
    raw = risk_usd / per_lot if per_lot > 0 else 0.0
    step = ctx.volume_step or ctx.volume_min
    lots = math.floor(raw / step + 1e-9) * step
    lots = max(lots, ctx.volume_min)
    return round(lots, 8), lots * per_lot


def trend(closes: Sequence[float], period: int = 20, slope_bars: int = 4) -> str:
    """Cierre contra EMA20 y pendiente de la EMA en las últimas ``slope_bars`` velas."""
    if len(closes) < period + slope_bars:
        return "sin_datos"
    alpha = 2.0 / (period + 1.0)
    ema = [float(closes[0])]
    for x in closes[1:]:
        ema.append(alpha * float(x) + (1.0 - alpha) * ema[-1])
    if closes[-1] > ema[-1] and ema[-1] > ema[-1 - slope_bars]:
        return "alcista"
    if closes[-1] < ema[-1] and ema[-1] < ema[-1 - slope_bars]:
        return "bajista"
    return "mixta"


def reasons_against(*, plan: Plan, ctx: Context, lots: float, risk_usd: float, real_risk_usd: float) -> list[str]:
    """Las razones medibles para no entrar, de la más grave a la menos."""
    out: list[str] = []
    long = plan.side == "BUY"
    want = "alcista" if long else "bajista"
    stop_dist = abs(plan.entry - plan.stop)

    if real_risk_usd > risk_usd * 1.0001:
        out.append(f"El lote mínimo ({ctx.volume_min}) arriesga {real_risk_usd:.2f} USD, más que tu límite de "
                   f"{risk_usd:.2f}. Acercá el stop o no entres.")
    if ctx.sma200_d1 is not None:
        if long and plan.entry < ctx.sma200_d1:
            out.append(f"Compra con el precio debajo de la media de 200 días ({ctx.sma200_d1:,.2f}). "
                       "Tudor Jones: si está debajo, afuera.")
        if not long and plan.entry > ctx.sma200_d1:
            out.append(f"Venta con el precio sobre la media de 200 días ({ctx.sma200_d1:,.2f}): vas contra la tendencia mayor.")
    against = [tf for tf, tr in ctx.trends.items() if tr not in (want, "sin_datos")]
    if against:
        out.append("Temporalidades que no acompañan: " + ", ".join(f"{tf} {ctx.trends[tf]}" for tf in against) + ".")
    if ctx.typical_move_15m and ctx.last_move_15m is not None:
        chasing = ctx.last_move_15m if long else -ctx.last_move_15m
        if chasing > CHASE_MULT * ctx.typical_move_15m:
            out.append(f"Estás persiguiendo un movimiento: los últimos 15 min fueron {abs(ctx.last_move_15m):,.2f} a tu favor, "
                       f"{chasing / ctx.typical_move_15m:.1f}× lo normal. En BTC, a 15–30 min, el precio tiende a devolver "
                       "(holdout feb–sep 2026).")
        if stop_dist < ctx.typical_move_15m:
            out.append(f"El stop ({stop_dist:,.2f}) está dentro del ruido normal de 15 min ({ctx.typical_move_15m:,.2f}): "
                       "te puede sacar un movimiento cualquiera.")
    rr = reward_risk(plan)
    if rr < PTJ_MIN_RR:
        out.append(f"R:R {rr:.2f} a 1, debajo del 5 a 1 de Tudor Jones. Sin ventaja en la entrada, con este R:R "
                   f"acertarías {breakeven_winrate(rr):.0%} de las veces y quedarías en cero antes de costos.")
    spread_usd = ctx.spread_price * ctx.usd_per_price_unit * lots
    if real_risk_usd > 0 and spread_usd > SPREAD_MAX_FRAC_OF_RISK * real_risk_usd:
        out.append(f"El spread ({spread_usd:.2f} USD) es más del 10% de lo que arriesgás.")
    return out
