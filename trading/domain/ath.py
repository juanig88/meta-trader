"""Distance below the all-time high. Telemetry only: no rule reads it (2026-11-03).

Idea from a thread: start DCA in BTC/ETH at 50% of the ATH, sell at the ATH. Too few
cycles to validate as a rule, so the bots only log where each trade happened relative
to the ATH, and the reports decide later whether the zone matters.
"""

from __future__ import annotations

from typing import Optional, Sequence


def drawdown_from_ath(highs: Sequence[float], price: float) -> Optional[float]:
    """Fraction below the highest high (0.36 = 36% under the ATH); None without data.

    A price above every high in ``highs`` is a new ATH and returns 0.0.
    """
    ath = max((h for h in highs if h > 0), default=0.0)
    if ath <= 0 or price <= 0:
        return None
    return round(max(0.0, 1.0 - price / ath), 4)
