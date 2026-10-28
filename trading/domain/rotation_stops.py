"""Rotation SL/TP: $0.50 money band when that fits inside 5% of price.

Cheap CFDs (tiny tick value / min lot) need a huge % to make $0.50, so the
stop stays at ±5% and volume rises until that band ≈ Kelly. Expensive CFDs
(BTC) can express $0.50 inside 5%: use the money distance, not ±5%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trading.domain.position_sizing import clamp_lot_volume

MAX_STOP_FRAC = 0.05
FLOOR_PARK_FRAC = 0.20
M5_ATR_MULT = 1.5
M1_ATR_MULT = 2.0
WIDEN_REPAIR_MULT = 1.5
DEFAULT_MAX_SPREAD_USD_FRAC = 0.10


@dataclass(frozen=True)
class PercentStopSize:
    ok: bool
    reason: str
    lot: float
    dist: float
    usd: float


def percent_stop_distance(
    entry: float, *, frac: float = MAX_STOP_FRAC, min_dist: float = 0.0
) -> Optional[float]:
    px = float(entry)
    if px <= 0.0:
        return None
    dist = px * max(0.0, float(frac))
    floor = max(0.0, float(min_dist))
    if dist < floor:
        dist = floor
    if dist <= 0.0 or dist >= px:
        return None
    return dist


def size_lot_for_percent_stop(
    *,
    risk_usd: float,
    price: float,
    tick_value: float,
    tick_size: float,
    volume_min: float,
    volume_step: float,
    volume_max: float,
    max_risk_usd: float,
    frac: float = MAX_STOP_FRAC,
) -> PercentStopSize:
    """Lot so a ±frac band equals Kelly dollars, never above max_risk_usd."""
    px = float(price)
    tv = float(tick_value)
    ts = float(tick_size)
    risk = max(0.0, float(risk_usd))
    cap = max(0.0, float(max_risk_usd))
    vmin = float(volume_min)
    if px <= 0.0 or tv <= 0.0 or ts <= 0.0 or vmin <= 0.0 or risk <= 0.0 or cap <= 0.0:
        return PercentStopSize(False, "datos_contrato", 0.0, 0.0, 0.0)
    vpp = tv / ts
    dist_5 = px * max(0.0, float(frac))
    if dist_5 <= 0.0 or dist_5 >= px:
        return PercentStopSize(False, "stop_5pct_invalido", 0.0, 0.0, 0.0)
    usd_per_lot_5 = dist_5 * vpp
    if usd_per_lot_5 <= 0.0:
        return PercentStopSize(False, "tick_value_invalido", 0.0, 0.0, 0.0)
    if usd_per_lot_5 > cap + 1e-9:
        lot = clamp_lot_volume(
            vmin, volume_min=vmin, volume_step=volume_step, volume_max=volume_max
        )
        dist = cap / (vpp * lot) if lot > 0.0 else 0.0
        if dist <= 0.0 or dist >= px:
            return PercentStopSize(False, "min_lote_supera_050", usd_per_lot_5, 0.0, usd_per_lot_5)
        return PercentStopSize(True, "ok_stop_acotado", lot, dist, round(cap, 4))
    lot_needed = risk / usd_per_lot_5
    lot = clamp_lot_volume(
        lot_needed, volume_min=vmin, volume_step=volume_step, volume_max=volume_max
    )
    usd = dist_5 * vpp * lot
    step = float(volume_step) if float(volume_step) > 0 else vmin
    while lot > vmin + 1e-12 and usd > cap + 1e-9:
        lot = clamp_lot_volume(
            lot - step, volume_min=vmin, volume_step=volume_step, volume_max=volume_max
        )
        usd = dist_5 * vpp * lot
    if usd > cap + 1e-9:
        return PercentStopSize(False, "min_lote_supera_050", lot, dist_5, round(usd, 4))
    return PercentStopSize(True, "ok", lot, dist_5, round(usd, 4))


def rotation_stop_distance(
    *,
    price: float,
    lot: float,
    tick_value: float,
    tick_size: float,
    max_risk_usd: float,
    frac: float = MAX_STOP_FRAC,
    min_dist: float = 0.0,
) -> Optional[float]:
    """Price distance for an already-chosen lot: $cap if that is ≤ frac, else frac.

    BTC min-lot $0.50 is a fraction of a percent → money stop.
    Illiquid/cheap names need >5% to make $0.50 at the lot we can send → 5%.
    """
    px = float(price)
    vol = float(lot)
    tv = float(tick_value)
    ts = float(tick_size)
    cap = max(0.0, float(max_risk_usd))
    if px <= 0.0 or vol <= 0.0 or tv <= 0.0 or ts <= 0.0 or cap <= 0.0:
        return None
    vpp = tv / ts
    if vpp <= 0.0:
        return None
    dist_money = cap / (vpp * vol)
    dist_pct = px * max(0.0, float(frac))
    dist = min(dist_money, dist_pct) if dist_pct > 0.0 else dist_money
    dist = max(dist, max(0.0, float(min_dist)))
    if dist <= 0.0 or dist >= px:
        return None
    return dist


def spread_usd_for_lot(
    *,
    spread_px: float,
    lot: float,
    tick_value: float,
    tick_size: float,
) -> Optional[float]:
    """Immediate mark-to-market hole of a one-way spread on ``lot``."""
    spr = max(0.0, float(spread_px))
    vol = float(lot)
    tv = float(tick_value)
    ts = float(tick_size)
    if vol <= 0.0 or tv <= 0.0 or ts <= 0.0:
        return None
    return (spr / ts) * tv * vol


def rotation_spread_skip_reason(
    *,
    spread_px: float,
    stop_dist: float,
    lot: float,
    tick_value: float,
    tick_size: float,
    risk_usd: float,
    max_spread_frac_of_risk: float = DEFAULT_MAX_SPREAD_USD_FRAC,
) -> str:
    """Empty if the intended stop still clears spread; else a skip_reason.

    ``spread_cubre_stop`` wins when the bid/ask already spans the SL.
    ``spread_caro`` when the hole is more than ``max_spread_frac_of_risk`` of risk.
    """
    dist = float(stop_dist)
    if dist <= 0.0:
        return "spread_sin_stop"
    spr = max(0.0, float(spread_px))
    if spr + 1e-12 >= dist:
        return "spread_cubre_stop"
    hole = spread_usd_for_lot(
        spread_px=spr, lot=lot, tick_value=tick_value, tick_size=tick_size
    )
    risk = max(0.0, float(risk_usd))
    cap = max(0.0, float(max_spread_frac_of_risk))
    if hole is None or risk <= 0.0 or cap <= 0.0:
        return ""
    if hole / risk > cap + 1e-12:
        return "spread_caro"
    return ""


def stop_off_percent(
    *,
    side: str,
    entry: float,
    sl: Optional[float],
    frac: float = MAX_STOP_FRAC,
    rel_tol: float = 0.12,
) -> bool:
    """True when SL is not the ±frac band (floor-parked, ATR-tight, or money-wide)."""
    if is_floor_parked_sl(side=side, entry=entry, sl=sl):
        return True
    px = float(entry)
    want = percent_stop_distance(px, frac=frac)
    if want is None or sl is None or float(sl) <= 0.0:
        return True
    current = abs(px - float(sl))
    return abs(current - want) / want > float(rel_tol)


def volatility_stop_distance(
    *,
    entry: float,
    atr_m5: Optional[float],
    atr_m1: Optional[float] = None,
    min_dist: float = 0.0,
    max_frac: float = MAX_STOP_FRAC,
    m5_mult: float = M5_ATR_MULT,
    m1_mult: float = M1_ATR_MULT,
) -> Optional[float]:
    """Price distance for a 1:1 stop from recent M1/M5 ATR, capped at max_frac of entry.

    1.5×ATR(M5) and 2×ATR(M1) sit outside the bars the ensemble already votes on.
    If ATR is missing, fall back to max_frac (5%). Never wider than that, never
    tighter than min_dist (broker/spread floor).
    """
    px = float(entry)
    if px <= 0.0:
        return None
    floor = max(0.0, float(min_dist))
    cap = px * max(0.0, float(max_frac))
    cands: list[float] = []
    if atr_m5 is not None and float(atr_m5) > 0.0:
        cands.append(float(atr_m5) * float(m5_mult))
    if atr_m1 is not None and float(atr_m1) > 0.0:
        cands.append(float(atr_m1) * float(m1_mult))
    raw = max(cands) if cands else cap
    if cap > 0.0:
        raw = min(raw, cap)
    dist = max(raw, floor)
    if cap > 0.0 and floor > cap:
        return None
    if dist <= 0.0:
        return None
    return dist


def stop_distance_too_wide(
    *,
    side: str,
    entry: float,
    sl: Optional[float],
    vol_dist: Optional[float],
    widen_mult: float = WIDEN_REPAIR_MULT,
) -> bool:
    """True if SL is floor-parked or much wider than the ATR band (tighten only)."""
    if is_floor_parked_sl(side=side, entry=entry, sl=sl):
        return True
    if sl is None or vol_dist is None or float(vol_dist) <= 0.0 or float(entry) <= 0.0:
        return False
    current = abs(float(entry) - float(sl))
    return current > float(vol_dist) * float(widen_mult)


def is_floor_parked_sl(*, side: str, entry: float, sl: Optional[float]) -> bool:
    """True when SL was parked at the tick floor (near 0 for BUY, ~2x for SELL)."""
    if sl is None:
        return False
    px = float(entry)
    stop = float(sl)
    if px <= 0.0 or stop <= 0.0:
        return True
    if str(side).upper() == "SELL":
        return stop >= px * (1.0 + (1.0 - FLOOR_PARK_FRAC))
    return stop < px * FLOOR_PARK_FRAC


def clamp_symmetric_stops(
    *,
    side: str,
    entry: float,
    sl: Optional[float],
    tp: Optional[float],
    min_dist: float,
    point: float,
    digits: int,
    max_frac: float = MAX_STOP_FRAC,
) -> Optional[tuple[float, float]]:
    """Return (sl, tp) 1:1 around entry, or None if no legal stop fits."""
    px = float(entry)
    if px <= 0.0:
        return None
    step = max(0.0, float(point))
    floor = max(0.0, float(min_dist), step)
    room = px - floor - step
    cap = px * max(0.0, float(max_frac))
    max_dist = min(room, cap) if cap > 0.0 else room
    if max_dist < floor:
        return None
    raw = 0.0
    if sl is not None:
        raw = abs(px - float(sl))
    elif tp is not None:
        raw = abs(float(tp) - px)
    dist = min(raw, max_dist) if raw > 0.0 else max_dist
    dist = max(dist, floor)
    if dist > max_dist + 1e-12:
        return None
    d = int(digits)
    if str(side).upper() == "SELL":
        sl_out = round(px + dist, d)
        tp_out = round(px - dist, d)
        if tp_out <= 0.0 or sl_out <= px:
            return None
        return sl_out, tp_out
    sl_out = round(px - dist, d)
    tp_out = round(px + dist, d)
    if sl_out <= 0.0 or sl_out >= px:
        return None
    return sl_out, tp_out
