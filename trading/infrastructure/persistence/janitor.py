"""Partition lifecycle for the ephemeral market-data tier.

postgres:17-alpine ships without pg_cron, so retention runs from Python on a
timer inside the ingest daemon rather than inside the database.

Two jobs, both idempotent:
  - pre-create partitions for yesterday, today and tomorrow, so a UTC-midnight
    rollover (or a slightly late tick) never hits "no partition of relation"
  - DROP partitions whose day is older than the TTL cutoff
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from trading.infrastructure.data_layer_config import RetentionSettings
from trading.infrastructure.persistence.pg import PgStore

logger = logging.getLogger(__name__)

PARENTS = ("md_ticks", "md_bars")


@dataclass
class JanitorReport:
    ensured: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    cutoffs: dict[str, date] = field(default_factory=dict)

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    def as_dict(self) -> dict:
        return {
            "ensured": list(self.ensured),
            "dropped": dict(self.dropped),
            "cutoffs": {k: v.isoformat() for k, v in self.cutoffs.items()},
        }


def tick_cutoff(now: datetime, ttl_hours: int) -> date:
    """Oldest tick day to keep.

    Day-granular: with ttl_hours=48 a tick lives at least 48h and at most
    72h. Tightening that would mean row-level DELETEs, which is exactly the
    vacuum pressure the partitioning exists to avoid.
    """
    return (now - timedelta(hours=max(1, int(ttl_hours)))).date()


def bar_cutoff(now: datetime, ttl_days: int) -> date:
    return (now - timedelta(days=max(1, int(ttl_days)))).date()


class PartitionJanitor:
    def __init__(self, store: PgStore, retention: RetentionSettings) -> None:
        self._store = store
        self._retention = retention

    async def ensure_window(self, now: Optional[datetime] = None) -> list[str]:
        when = now or datetime.now(timezone.utc)
        today = when.date()
        days = (today - timedelta(days=1), today, today + timedelta(days=1))
        created: list[str] = []
        for parent in PARENTS:
            for day in days:
                child = await self._store.ensure_partition(parent, day)
                if child:
                    created.append(child)
        return created

    async def purge(self, now: Optional[datetime] = None) -> tuple[dict[str, int], dict[str, date]]:
        when = now or datetime.now(timezone.utc)
        cutoffs = {
            "md_ticks": tick_cutoff(when, self._retention.tick_ttl_hours),
            "md_bars": bar_cutoff(when, self._retention.bar_ttl_days),
        }
        dropped: dict[str, int] = {}
        for parent, cutoff in cutoffs.items():
            dropped[parent] = await self._store.drop_partitions_before(parent, cutoff)
            if dropped[parent]:
                # A day we just dropped may legitimately come back (a late
                # backfill), so stop claiming its partition exists.
                self._store.forget_partition_cache(parent)
        return dropped, cutoffs

    async def run_once(self, now: Optional[datetime] = None) -> JanitorReport:
        when = now or datetime.now(timezone.utc)
        ensured = await self.ensure_window(when)
        dropped, cutoffs = await self.purge(when)
        report = JanitorReport(ensured=ensured, dropped=dropped, cutoffs=cutoffs)
        if report.total_dropped:
            logger.info(
                "janitor: dropped %d partition(s) %s",
                report.total_dropped,
                {k: v.isoformat() for k, v in cutoffs.items()},
            )
        else:
            logger.debug("janitor: nothing to drop (cutoffs=%s)", cutoffs)
        return report
