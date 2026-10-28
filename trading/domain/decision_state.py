"""Compact numeric snapshot at a 4/5 decision. Pure: no I/O.

Only timestamps strictly before the decision may enter tape/RV. The live bot
fetches M1 from position 1 (closed bars only), so every close counts; pass
``m1_last_forming=True`` when the series still carries the open bar.
``alpha_score`` is a shadow score, not a probability.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from trading.domain.rotation_stops import spread_usd_for_lot

RV_BARS = 20
TAPE_MIN = 3

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def htf_sign(votes: Sequence[Any]) -> int:
    for vote in votes:
        if isinstance(vote, Mapping):
            name = str(vote.get("name") or "")
            side = str(vote.get("side") or "")
        else:
            name = str(getattr(vote, "name", "") or "")
            side = str(getattr(vote, "side", "") or "")
        if name == "htf":
            if side == SIDE_BUY:
                return 1
            if side == SIDE_SELL:
                return -1
            return 0
    return 0


def closed_m1_closes(closes: Sequence[float]) -> list[float]:
    vals = [float(c) for c in closes if _f(c) is not None and float(c) > 0.0]
    if len(vals) < 2:
        return []
    return vals[:-1]


def realized_vol(
    closes: Sequence[float],
    *,
    bars: int = RV_BARS,
    last_forming: bool = False,
) -> Optional[float]:
    if last_forming:
        closed = closed_m1_closes(closes)
    else:
        closed = [float(c) for c in closes if _f(c) is not None and float(c) > 0.0]
    need = max(2, int(bars))
    if len(closed) < need + 1:
        need = len(closed) - 1
    if need < 2:
        return None
    window = closed[-(need + 1) :]
    rets: list[float] = []
    for prev, nxt in zip(window, window[1:]):
        if prev > 0.0 and nxt > 0.0:
            rets.append(math.log(nxt / prev))
    if len(rets) < 2:
        return None
    mean = sum(rets) / float(len(rets))
    var = sum((r - mean) ** 2 for r in rets) / float(len(rets) - 1)
    if var < 0.0:
        return None
    return math.sqrt(var)


def _tick_fields(tick: Any) -> tuple[Optional[int], Optional[float], Optional[float]]:
    if isinstance(tick, Mapping):
        ts = tick.get("ts_msc")
        bid = _f(tick.get("bid"))
        ask = _f(tick.get("ask"))
    else:
        ts = getattr(tick, "ts_msc", None)
        bid = _f(getattr(tick, "bid", None))
        ask = _f(getattr(tick, "ask", None))
    try:
        ts_i = int(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_i = None
    return ts_i, bid, ask


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0:
        return None
    return (bid + ask) / 2.0


def tape_imbalance(
    ticks: Sequence[Any],
    *,
    decision_ts_msc: Optional[int],
) -> tuple[Optional[float], Optional[int]]:
    """Signed uptick share in (-1, 1). Ticks at/after the decision are dropped."""
    cutoff = int(decision_ts_msc) if decision_ts_msc else None
    mids: list[float] = []
    for tick in ticks:
        ts_msc, bid, ask = _tick_fields(tick)
        if ts_msc is None:
            continue
        if cutoff is not None and ts_msc >= cutoff:
            continue
        mid = _mid(bid, ask)
        if mid is None:
            continue
        mids.append(mid)
    if len(mids) < TAPE_MIN:
        return None, None
    up = 0
    down = 0
    for prev, nxt in zip(mids, mids[1:]):
        if nxt > prev:
            up += 1
        elif nxt < prev:
            down += 1
    n = up + down
    if n <= 0:
        return None, len(mids)
    return (up - down) / float(n), len(mids)


def overlay_tape(
    state: Optional[Mapping[str, Any]],
    ticks: Sequence[Any],
    *,
    decision_ts_msc: Optional[int],
) -> dict[str, Any]:
    out = dict(state or {})
    imb, n = tape_imbalance(ticks, decision_ts_msc=decision_ts_msc)
    out["tape_imbalance"] = None if imb is None else round(float(imb), 6)
    out["tape_n"] = n
    return out


def pack_decision_state(
    *,
    m1_closes: Sequence[float] = (),
    votes: Sequence[Any] = (),
    murphy: Optional[Mapping[str, Any]] = None,
    alpha: Optional[Mapping[str, Any]] = None,
    mid: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    tick_value: float = 0.0,
    tick_size: float = 0.0,
    lot: float = 0.0,
    decision_ts_msc: Optional[int] = None,
    ticks: Sequence[Any] = (),
    m1_last_forming: bool = False,
) -> dict[str, Any]:
    if m1_last_forming:
        closed = closed_m1_closes(m1_closes)
    else:
        closed = [float(c) for c in m1_closes if _f(c) is not None and float(c) > 0.0]
    px = _f(mid)
    if px is None and closed:
        px = closed[-1]
    spread_px = None
    if bid is not None and ask is not None and bid > 0.0 and ask > 0.0:
        spread_px = max(0.0, float(ask) - float(bid))
    spread_usd = None
    if spread_px is not None:
        spread_usd = spread_usd_for_lot(
            spread_px=spread_px,
            lot=float(lot or 0.0),
            tick_value=float(tick_value or 0.0),
            tick_size=float(tick_size or 0.0),
        )
    rv = realized_vol(m1_closes, last_forming=m1_last_forming)
    imb, tape_n = tape_imbalance(ticks, decision_ts_msc=decision_ts_msc)
    mur = murphy if isinstance(murphy, Mapping) else {}
    al = alpha if isinstance(alpha, Mapping) else {}
    avoid = bool(al.get("would_avoid")) if al else False
    score = _f(al.get("score")) if al else None
    return {
        "mid": None if px is None else round(float(px), 8),
        "spread_usd": None if spread_usd is None else round(float(spread_usd), 6),
        "rv_m1": None if rv is None else round(float(rv), 8),
        "htf": htf_sign(votes),
        "murphy_veto": 1 if mur.get("would_veto") is True else 0,
        "alpha_avoid": 1 if avoid else 0,
        "alpha_score": None if score is None else round(float(score), 4),
        "tape_imbalance": None if imb is None else round(float(imb), 6),
        "tape_n": tape_n,
        "decision_ts_msc": int(decision_ts_msc) if decision_ts_msc else None,
    }
