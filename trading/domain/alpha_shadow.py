"""Shadow overlay of alpha_score vs a 4/5 side. Telemetry only; never a skip."""

from __future__ import annotations

from typing import Any, Optional

from trading.domain.alpha_score import CostModel, score_bars
from trading.domain.loser_review import (
    ALPHA_AVOIDED,
    ALPHA_MISSING,
    ALPHA_OPPOSITE,
    ALPHA_SAME,
    classify_alpha,
)

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"


def alpha_shadow_from_bars(
    symbol: str,
    side: str,
    m15: Any,
    *,
    min_score: float = 0.15,
) -> Optional[dict[str, Any]]:
    """Score M15 the same way the shadow engine does. Empty if it cannot run."""
    side_u = str(side or "").strip().upper()
    if side_u not in (SIDE_BUY, SIDE_SELL):
        return None
    if m15 is None:
        return None
    opens = getattr(m15, "opens", ()) or ()
    highs = getattr(m15, "highs", ()) or ()
    lows = getattr(m15, "lows", ()) or ()
    closes = getattr(m15, "closes", ()) or ()
    volumes = getattr(m15, "volumes", ()) or ()
    if len(closes) < 50:
        return {
            "direction": None,
            "score": None,
            "code": ALPHA_MISSING,
            "agree": False,
            "would_avoid": False,
            "reason": "pocas_velas",
        }
    try:
        scored = score_bars(
            symbol=str(symbol or ""),
            timeframe="M15",
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            point=1.0,
            costs=CostModel(),
            min_score=float(min_score),
        )
    except Exception:
        return None
    code = classify_alpha(side_u, scored.direction)
    return {
        "direction": scored.direction,
        "score": round(float(scored.score), 4),
        "net_edge": round(float(scored.net_edge), 4),
        "momentum": None if scored.momentum is None else round(float(scored.momentum), 4),
        "zscore": None if scored.zscore is None else round(float(scored.zscore), 4),
        "flow": None if scored.flow is None else round(float(scored.flow), 4),
        "code": code,
        "agree": code == ALPHA_SAME,
        "would_avoid": code in (ALPHA_AVOIDED, ALPHA_OPPOSITE),
    }
