"""Market data value objects. Pure: no I/O, no MT5, no DB.

A note on time, because it is easy to get wrong here.

MT5 reports tick and bar timestamps in *broker server time*, presented as a
unix epoch "in UTC without the shift". On an ICMarkets demo the server runs
UTC+3, so a tick at 16:10 real UTC is reported as 19:10.

This pipeline stores that value as MT5 reports it rather than converting.
bot_mt5, trades.log, ladder_state.json and every existing analysis script in
the repo already work in that frame; converting only this one table would
desync it from everything it needs to be joined against. The ingest daemon
measures the broker offset at startup and logs it in its INGEST_START event,
so the frame is explicit and auditable instead of assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class Tick:
    symbol: str
    ts_msc: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    flags: Optional[int] = None

    @property
    def ts(self) -> datetime:
        """Timezone-aware datetime in the MT5 server frame (see module docstring)."""
        return datetime.fromtimestamp(self.ts_msc / 1000.0, tz=timezone.utc)

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (float(self.bid) + float(self.ask)) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return float(self.ask) - float(self.bid)

    def spread_points(self, point: float) -> Optional[float]:
        sp = self.spread
        if sp is None or point is None or float(point) <= 0.0:
            return None
        return sp / float(point)


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: Optional[int] = None
    real_volume: Optional[int] = None
    spread: Optional[int] = None

    @property
    def range(self) -> float:
        return float(self.high) - float(self.low)

    @property
    def body(self) -> float:
        return float(self.close) - float(self.open)
