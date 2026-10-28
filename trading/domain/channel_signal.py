"""Telegram-channel trade signal as a pure value object. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


ALLOWED_PAIRS = frozenset({"BTCUSDT", "ETHUSDT"})


@dataclass(frozen=True)
class ChannelSignal:
    message_id: int
    timestamp_utc: datetime
    pair: str
    side: str  # LONG | SHORT
    kind: str  # structured_trade | claimed_result
    entry: Optional[float] = None
    entry_high: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    sl_source: str = "none"  # explicit | liquidation | percent | none
    leverage: Optional[float] = None
    score: Optional[float] = None
    confirmations: Tuple[str, ...] = ()
    template: str = ""
    raw_excerpt: str = ""

    @property
    def year(self) -> int:
        return int(self.timestamp_utc.year)

    def replayable(self) -> bool:
        if self.kind != "structured_trade":
            return False
        if self.pair not in ALLOWED_PAIRS:
            return False
        if self.side not in ("LONG", "SHORT"):
            return False
        if self.entry is None or self.entry <= 0:
            return False
        return self.tp is not None or self.sl is not None
