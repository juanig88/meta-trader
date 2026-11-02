"""Real BTC volume (Binance spot BTCUSDT) for the false-break rule.

Exness CFD volume is only tick count, so the bot reads USDT traded on Binance. The variable is
the break candle's relative volume: its USDT traded over the median of the 24 H1 before it.

Measured on 2017-08 → 2026-10 (3443 suspected breaks, the same rule the bot runs):

- low volume (≤ 0.98x): 87% false breaks before 2024-11, 92% after
- medium (≤ 1.73x): 84% / 88%
- high (> 1.73x): 74% / 79%

So volume does tell sustained breaks from false ones. As a filter on the return trade it was
rejected (chosen on 2017-2024, it lost on 2024-11 → 2026-10, permutation p 0.87), so the bot
only records and reports it. Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

import statistics
from typing import Optional, Sequence

LOW, HIGH = 0.98, 1.73  # terciles of the break candle's relative volume, 2017-08 → 2024-11
FALSE_SHARE = {"bajo": (87, 92), "medio": (84, 88), "alto": (74, 79)}  # % false (before / after 2024-11)


def relative_volume(previous: Sequence[float], value: float) -> Optional[float]:
    """``value`` over the median of ``previous`` (the 24 H1 before), None without history."""
    if not previous:
        return None
    m = statistics.median(previous)
    return value / m if m > 0 else None


def volume_band(rel: Optional[float]) -> Optional[str]:
    if rel is None:
        return None
    return "bajo" if rel <= LOW else "medio" if rel <= HIGH else "alto"


def volume_text(rel: Optional[float]) -> str:
    """One Telegram line: how much was traded on the break and what that usually meant."""
    band = volume_band(rel)
    if band is None:
        return "Volumen real (Binance): sin dato."
    before, after = FALSE_SHARE[band]
    return (f"Volumen real (Binance) de la ruptura: {rel:.2f}x lo normal ({band}). "
            f"Históricamente {before}–{after}% de estas rupturas fueron falsas.")
