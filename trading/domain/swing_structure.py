"""Murphy swing / retracement snapshot. Pure: no I/O.

Labels only here. ``evaluate_symbol`` skips a 4/5 when ``would_veto``
(climax_unbroken on M15 or H4).

Closed bars only: the series is already a prefix of completed candles.
Pivots need ``wing`` bars on both sides, so the last ``wing`` bars cannot
be a confirmed swing (no lookahead).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from indicators import atr as atr_series

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
WING = 2
PENETRATE_FRAC = 0.01
PENETRATE_ATR = 1.0
RETRACE_LO = 0.38
RETRACE_HI = 0.50


def _atr_last(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Optional[float]:
    series = atr_series([float(x) for x in highs], [float(x) for x in lows], [float(x) for x in closes], period)
    for v in reversed(series):
        if v is not None and float(v) > 0.0:
            return float(v)
    return None


def confirmed_pivots(
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    wing: int = WING,
) -> list[tuple[int, float, str]]:
    """Return (index, price, 'high'|'low') pivots with ``wing`` bars on each side."""
    h = [float(x) for x in highs]
    l = [float(x) for x in lows]
    n = min(len(h), len(l))
    w = max(1, int(wing))
    out: list[tuple[int, float, str]] = []
    if n < 2 * w + 1:
        return out
    for i in range(w, n - w):
        hi = h[i]
        lo = l[i]
        is_high = all(hi > h[i - k] for k in range(1, w + 1)) and all(hi >= h[i + k] for k in range(1, w + 1))
        is_low = all(lo < l[i - k] for k in range(1, w + 1)) and all(lo <= l[i + k] for k in range(1, w + 1))
        if is_high:
            out.append((i, hi, "high"))
        if is_low:
            out.append((i, lo, "low"))
    return out


def _pen(px: float, atr: Optional[float]) -> float:
    a = float(atr) if atr is not None and atr > 0 else 0.0
    by_frac = abs(float(px)) * PENETRATE_FRAC
    by_atr = a * PENETRATE_ATR
    if by_atr <= 0.0:
        return by_frac if by_frac > 0 else 0.0
    if by_frac <= 0.0:
        return by_atr
    return min(by_frac, by_atr) if abs(px) > 1.0 else max(by_frac, by_atr)


def swing_context(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    side: str,
    wing: int = WING,
    tf: str = "M15",
) -> dict[str, Any]:
    """Snapshot of last confirmed swings vs ``closes[-1]``. Does not vote."""
    h = [float(x) for x in highs]
    l = [float(x) for x in lows]
    c = [float(x) for x in closes]
    n = min(len(h), len(l), len(c))
    empty = {
        "tf": tf,
        "ok": False,
        "reason": "pocas_velas",
        "tag": None,
        "would_veto": False,
        "would_keep": False,
    }
    if n < 2 * int(wing) + 3:
        return empty
    h, l, c = h[:n], l[:n], c[:n]
    close = c[-1]
    atr = _atr_last(h, l, c)
    pivots = confirmed_pivots(h, l, wing=wing)
    highs_p = [p for p in pivots if p[2] == "high"]
    lows_p = [p for p in pivots if p[2] == "low"]
    if not highs_p or not lows_p:
        empty["reason"] = "sin_pivotes"
        empty["n_bars"] = n
        empty["atr"] = None if atr is None else round(atr, 8)
        return empty
    last_high = highs_p[-1]
    last_low = lows_p[-1]
    hi_i, hi_px, _ = last_high
    lo_i, lo_px, _ = last_low
    impulse_low = None
    for idx, px, kind in reversed(pivots):
        if kind == "low" and idx < hi_i:
            impulse_low = (idx, px)
            break
    retrace = None
    if impulse_low is not None:
        span = hi_px - impulse_low[1]
        if span > 0:
            retrace = (hi_px - close) / span
    pen = _pen(close, atr)
    path_high = max(h[hi_i:]) if hi_i < n else hi_px
    broke_atr = close >= hi_px + (atr or 0.0) * PENETRATE_ATR if atr else False
    broke_1pct = close >= hi_px * (1.0 + PENETRATE_FRAC)
    broke_path = path_high >= hi_px + pen
    dist_high_pct = (hi_px - close) / close if close else None
    dist_high_atr = (hi_px - close) / atr if atr and atr > 0 else None
    broken_high = None
    for idx, px, _kind in reversed(highs_p):
        path = max(h[idx:]) if idx < n else px
        if path >= px + pen:
            broken_high = (idx, px)
            break
    broken_low = None
    for idx, px, _kind in reversed(lows_p):
        path = min(l[idx:]) if idx < n else px
        if path <= px - pen:
            broken_low = (idx, px)
            break

    tag = "other"
    broken = broken_high
    if side == SIDE_BUY:
        if broken_high is not None and abs(close - broken_high[1]) <= pen:
            tag = "retest_broken"
        elif retrace is not None and RETRACE_LO <= retrace <= RETRACE_HI:
            tag = "pullback_38_50"
        elif not broke_path and retrace is not None and retrace < RETRACE_LO:
            tag = "climax_unbroken"
        elif retrace is not None and retrace > 0.62:
            tag = "deep_retrace"
    elif side == SIDE_SELL:
        impulse_high = None
        for idx, px, kind in reversed(pivots):
            if kind == "high" and idx < lo_i:
                impulse_high = (idx, px)
                break
        sell_retrace = None
        if impulse_high is not None:
            span = impulse_high[1] - lo_px
            if span > 0:
                sell_retrace = (close - lo_px) / span
        retrace = sell_retrace
        path_low = min(l[lo_i:]) if lo_i < n else lo_px
        broke_path = path_low <= lo_px - pen
        broke_atr = close <= lo_px - (atr or 0.0) * PENETRATE_ATR if atr else False
        broke_1pct = close <= lo_px * (1.0 - PENETRATE_FRAC)
        dist_high_pct = (close - lo_px) / close if close else None
        dist_high_atr = (close - lo_px) / atr if atr and atr > 0 else None
        broken = broken_low
        if broken_low is not None and abs(close - broken_low[1]) <= pen:
            tag = "retest_broken"
        elif retrace is not None and RETRACE_LO <= retrace <= RETRACE_HI:
            tag = "pullback_38_50"
        elif not broke_path and retrace is not None and retrace < RETRACE_LO:
            tag = "climax_unbroken"
        elif retrace is not None and retrace > 0.62:
            tag = "deep_retrace"

    would_veto = side in (SIDE_BUY, SIDE_SELL) and tag == "climax_unbroken"
    would_keep = side in (SIDE_BUY, SIDE_SELL) and tag in ("pullback_38_50", "retest_broken")
    return {
        "tf": tf,
        "ok": True,
        "reason": "",
        "n_bars": n,
        "atr": None if atr is None else round(atr, 8),
        "pen": round(pen, 8),
        "close": round(close, 8),
        "last_high": round(hi_px, 8),
        "last_high_i": hi_i,
        "last_low": round(lo_px, 8),
        "last_low_i": lo_i,
        "broken_level": None if broken is None else round(float(broken[1]), 8),
        "broken_i": None if broken is None else int(broken[0]),
        "retrace": None if retrace is None else round(float(retrace), 4),
        "broke_path": bool(broke_path),
        "broke_atr": bool(broke_atr),
        "broke_1pct": bool(broke_1pct),
        "dist_level_pct": None if dist_high_pct is None else round(float(dist_high_pct), 6),
        "dist_level_atr": None if dist_high_atr is None else round(float(dist_high_atr), 4),
        "tag": tag,
        "would_veto": would_veto,
        "would_keep": would_keep,
        "n_pivots": len(pivots),
    }


def murphy_snapshot(
    *,
    side: str,
    m15_highs: Sequence[float] = (),
    m15_lows: Sequence[float] = (),
    m15_closes: Sequence[float] = (),
    h4_highs: Sequence[float] = (),
    h4_lows: Sequence[float] = (),
    h4_closes: Sequence[float] = (),
) -> dict[str, Any]:
    """M15 + H4 labels for a 4/5 fire. ``would_veto`` is a live skip in ensemble."""
    s = side if side in (SIDE_BUY, SIDE_SELL) else ""
    m15 = swing_context(m15_highs, m15_lows, m15_closes, side=s, tf="M15")
    h4 = swing_context(h4_highs, h4_lows, h4_closes, side=s, tf="H4") if len(h4_closes) else {
        "tf": "H4",
        "ok": False,
        "reason": "sin_barras",
        "tag": None,
        "would_veto": False,
        "would_keep": False,
    }
    return {
        "side": s or None,
        "m15": m15,
        "h4": h4,
        "would_veto": bool(m15.get("would_veto") or h4.get("would_veto")),
        "would_keep": bool(m15.get("would_keep") or h4.get("would_keep")),
        "tag_m15": m15.get("tag"),
        "tag_h4": h4.get("tag"),
    }
