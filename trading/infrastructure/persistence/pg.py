"""Async Postgres store for the MT5 quant data layer.

Every method raises PgUnavailable rather than a driver-specific exception, so
callers can degrade without importing psycopg. The bot's trading loop must
never die because a container is down: it catches PgUnavailable, logs, and
carries on writing to trades.log as it always has.

The store also rate-limits its own reconnect attempts. Without that, a dead
container turns every tick batch into a fresh TCP connect attempt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from trading.domain.market_data import Bar, Tick
from trading.infrastructure.data_layer_config import PgSettings

logger = logging.getLogger(__name__)

TICK_COLUMNS = ("symbol", "ts", "ts_msc", "bid", "ask", "last", "volume", "flags", "spread_pts")

# Explicit COPY types: bid/ask/last can be NULL in the first row, and text-format
# COPY would otherwise have nothing to infer the column type from.
_TICK_COPY_TYPES = [
    "text",
    "timestamptz",
    "int8",
    "float8",
    "float8",
    "float8",
    "float8",
    "int4",
    "float8",
]

_MISSING_PARTITION_SQLSTATE = "23514"  # check_violation: no partition found for row


class PgUnavailable(RuntimeError):
    """Postgres could not be reached, or a statement failed against it."""


@dataclass
class _Backoff:
    base: float = 1.0
    cap: float = 60.0
    _next_attempt_at: float = 0.0
    _failures: int = 0

    def blocked(self) -> bool:
        return time.monotonic() < self._next_attempt_at

    def record_failure(self) -> float:
        self._failures += 1
        delay = min(self.cap, self.base * (2 ** (self._failures - 1)))
        delay *= 0.5 + random.random() / 2.0  # jitter, avoid lockstep retries
        self._next_attempt_at = time.monotonic() + delay
        return delay

    def record_success(self) -> None:
        self._failures = 0
        self._next_attempt_at = 0.0


class PgStore:
    def __init__(self, settings: PgSettings) -> None:
        self._settings = settings
        self._pool: Any = None
        self._backoff = _Backoff()
        self._lock = asyncio.Lock()
        # Days already known to have a partition. Avoids a round-trip per write
        # and, more importantly, avoids letting a 500-bar backfill spanning six
        # days abort an executemany pipeline before the reactive retry kicks in.
        self._ensured: set[tuple[str, date]] = set()

    @property
    def settings(self) -> PgSettings:
        return self._settings

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    async def open(self) -> None:
        """Open the pool. Raises PgUnavailable if the server cannot be reached."""
        async with self._lock:
            if self._pool is not None:
                return
            if self._backoff.blocked():
                raise PgUnavailable("postgres reconnect is backing off")
            try:
                from psycopg_pool import AsyncConnectionPool

                pool = AsyncConnectionPool(
                    self._settings.conninfo(),
                    min_size=self._settings.pool_min,
                    max_size=self._settings.pool_max,
                    open=False,
                    name="meta-trader",
                )
                await pool.open(wait=True, timeout=float(self._settings.connect_timeout) + 5.0)
            except Exception as exc:
                delay = self._backoff.record_failure()
                raise PgUnavailable(
                    f"cannot connect to {self._settings.redacted()}: {exc} "
                    f"(next attempt in ~{delay:.0f}s)"
                ) from exc
            self._pool = pool
            self._backoff.record_success()
            logger.info("pg: connected to %s", self._settings.redacted())

    async def ensure_open(self) -> bool:
        """Best-effort open. Returns False instead of raising when unavailable."""
        if self._pool is not None:
            return True
        try:
            await self.open()
            return True
        except PgUnavailable as exc:
            logger.warning("pg: unavailable (%s)", exc)
            return False

    async def close(self) -> None:
        async with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                logger.debug("pg: error while closing pool", exc_info=True)

    async def _drop_pool(self) -> None:
        """Discard a pool that has gone bad so the next call reconnects."""
        async with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                logger.debug("pg: error while discarding pool", exc_info=True)

    async def health(self) -> bool:
        try:
            await self.execute("SELECT 1")
            return True
        except PgUnavailable:
            return False

    # -- generic helpers ----------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        await self._run(lambda cur: cur.execute(sql, params))

    async def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> Optional[tuple]:
        holder: list[Optional[tuple]] = []

        async def _op(cur: Any) -> None:
            await cur.execute(sql, params)
            holder.append(await cur.fetchone())

        await self._run(_op)
        return holder[0] if holder else None

    async def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        holder: list[list[tuple]] = []

        async def _op(cur: Any) -> None:
            await cur.execute(sql, params)
            holder.append(list(await cur.fetchall()))

        await self._run(_op)
        return holder[0] if holder else []

    async def _run(self, op: Any) -> None:
        if self._pool is None:
            await self.open()
        pool = self._pool
        if pool is None:
            raise PgUnavailable("postgres pool is not open")
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    result = op(cur)
                    if asyncio.iscoroutine(result):
                        await result
        except Exception as exc:
            if _is_connection_error(exc):
                await self._drop_pool()
                self._backoff.record_failure()
            raise PgUnavailable(str(exc)) from exc

    # -- market data (ephemeral tier) --------------------------------------

    async def copy_ticks(self, ticks: Sequence[Tick]) -> int:
        """Bulk-insert ticks via COPY. Returns the number of rows written."""
        rows = [_tick_row(t) for t in ticks]
        if not rows:
            return 0
        try:
            await self.ensure_partitions_for("md_ticks", (r[1].date() for r in rows))
            await self._copy_tick_rows(rows)
        except PgUnavailable as exc:
            # Safety net: the cache thought a partition existed but the janitor
            # dropped it underneath us. Re-check and retry once; a second
            # failure is a real error.
            if not _is_missing_partition(exc):
                raise
            logger.info("pg: recreating missing md_ticks partition(s)")
            self.forget_partition_cache("md_ticks")
            await self.ensure_partitions_for("md_ticks", (r[1].date() for r in rows))
            await self._copy_tick_rows(rows)
        return len(rows)

    async def _copy_tick_rows(self, rows: Sequence[tuple]) -> None:
        columns = ", ".join(TICK_COLUMNS)
        sql = f"COPY md_ticks ({columns}) FROM STDIN"

        async def _op(cur: Any) -> None:
            async with cur.copy(sql) as copy:
                copy.set_types(_TICK_COPY_TYPES)
                for row in rows:
                    await copy.write_row(row)

        await self._run(_op)

    async def upsert_bars(self, bars: Sequence[Bar]) -> int:
        """Bars are low-volume and revisable (the last bar is still forming),
        so they go through ON CONFLICT rather than COPY."""
        rows = [_bar_row(b) for b in bars]
        if not rows:
            return 0
        sql = """
            INSERT INTO md_bars
                (symbol, timeframe, ts, open, high, low, close, tick_volume, real_volume, spread)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, timeframe, ts) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                tick_volume = EXCLUDED.tick_volume,
                real_volume = EXCLUDED.real_volume,
                spread = EXCLUDED.spread
        """

        async def _op(cur: Any) -> None:
            await cur.executemany(sql, rows)

        # Pre-create up front: executemany runs in a pipeline, so one row
        # missing its partition aborts the whole batch.
        await self.ensure_partitions_for("md_bars", (r[2].date() for r in rows))
        try:
            await self._run(_op)
        except PgUnavailable as exc:
            if not _is_missing_partition(exc):
                raise
            self.forget_partition_cache("md_bars")
            await self.ensure_partitions_for("md_bars", (r[2].date() for r in rows))
            await self._run(_op)
        return len(rows)

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[Bar]:
        """Most recent `limit` bars, returned oldest-first."""
        rows = await self.fetch_all(
            "SELECT symbol, timeframe, ts, open, high, low, close, "
            "tick_volume, real_volume, spread "
            "FROM md_bars WHERE symbol = %s AND timeframe = %s "
            "ORDER BY ts DESC LIMIT %s",
            (str(symbol).strip().upper(), str(timeframe).strip().upper(), max(1, int(limit))),
        )
        return [_bar_from_row(row) for row in reversed(rows)]

    async def fetch_recent_ticks(self, symbol: str, limit: int) -> list[Tick]:
        """Most recent `limit` ticks, returned oldest-first."""
        rows = await self.fetch_all(
            "SELECT symbol, ts_msc, bid, ask, last, volume, flags "
            "FROM md_ticks WHERE symbol = %s ORDER BY ts DESC LIMIT %s",
            (str(symbol).strip().upper(), max(1, int(limit))),
        )
        ticks: list[Tick] = []
        for row in reversed(rows):
            ticks.append(
                Tick(
                    symbol=str(row[0]),
                    ts_msc=int(row[1]),
                    bid=_f(row[2]),
                    ask=_f(row[3]),
                    last=_f(row[4]),
                    volume=_f(row[5]),
                    flags=_i(row[6]),
                )
            )
        return ticks

    async def row_count(self, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
        allowed = {
            "md_ticks",
            "md_bars",
            "trade_events",
            "positions_closed",
            "equity_snapshots",
            "alpha_scores",
            "manual_trade_journal",
        }
        if table not in allowed:
            raise ValueError(f"row_count: unsupported table {table}")
        sql = f"SELECT count(*) FROM {table}"
        if where:
            sql = f"{sql} WHERE {where}"
        row = await self.fetch_one(sql, params)
        return int(row[0]) if row and row[0] is not None else 0

    # -- partitions ---------------------------------------------------------

    async def ensure_partition(self, parent: str, day: date) -> Optional[str]:
        row = await self.fetch_one("SELECT md_ensure_partition(%s, %s)", (parent, day))
        self._ensured.add((parent, day))
        return row[0] if row else None

    async def ensure_partitions_for(self, parent: str, days: Iterable[date]) -> None:
        for day in sorted({d for d in days}):
            if (parent, day) in self._ensured:
                continue
            await self.ensure_partition(parent, day)

    def forget_partition_cache(self, parent: Optional[str] = None) -> None:
        """Invalidate after a purge, so a dropped day can be recreated on demand."""
        if parent is None:
            self._ensured.clear()
            return
        self._ensured = {key for key in self._ensured if key[0] != parent}

    async def drop_partitions_before(self, parent: str, cutoff: date) -> int:
        row = await self.fetch_one("SELECT md_drop_partitions_before(%s, %s)", (parent, cutoff))
        dropped = int(row[0]) if row and row[0] is not None else 0
        if dropped:
            self._ensured = {
                key for key in self._ensured if not (key[0] == parent and key[1] < cutoff)
            }
        return dropped

    async def partition_inventory(self, parent: Optional[str] = None) -> list[tuple]:
        if parent:
            return await self.fetch_all(
                "SELECT parent, child, day, bytes FROM md_partition_inventory "
                "WHERE parent = %s ORDER BY day",
                (parent,),
            )
        return await self.fetch_all(
            "SELECT parent, child, day, bytes FROM md_partition_inventory ORDER BY parent, day"
        )

    # -- persistent tier ----------------------------------------------------

    async def insert_trade_events(self, events: Sequence[dict]) -> int:
        rows = [_trade_event_row(e) for e in events]
        if not rows:
            return 0
        sql = (
            "INSERT INTO trade_events (ts, event, symbol, ticket, magic, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        )

        async def _op(cur: Any) -> None:
            await cur.executemany(sql, rows)

        await self._run(_op)
        return len(rows)

    async def insert_equity_snapshot(
        self,
        *,
        balance: Optional[float] = None,
        equity: Optional[float] = None,
        margin: Optional[float] = None,
        free_margin: Optional[float] = None,
        margin_level: Optional[float] = None,
        open_positions: Optional[int] = None,
        floating_pnl: Optional[float] = None,
        ts: Optional[datetime] = None,
    ) -> None:
        await self.execute(
            "INSERT INTO equity_snapshots "
            "(ts, balance, equity, margin, free_margin, margin_level, open_positions, floating_pnl) "
            "VALUES (COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s)",
            (ts, balance, equity, margin, free_margin, margin_level, open_positions, floating_pnl),
        )

    async def upsert_positions_closed(self, positions: Sequence[dict]) -> int:
        rows = [_position_closed_row(p) for p in positions]
        if not rows:
            return 0
        sql = """
            INSERT INTO positions_closed
                (ticket, symbol, side, volume, price_open, price_close, sl, tp,
                 opened_at, closed_at, profit, commission, swap, net_profit,
                 magic, comment, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticket) DO UPDATE SET
                price_close = EXCLUDED.price_close,
                closed_at = EXCLUDED.closed_at,
                profit = EXCLUDED.profit,
                commission = EXCLUDED.commission,
                swap = EXCLUDED.swap,
                net_profit = EXCLUDED.net_profit,
                payload = EXCLUDED.payload
        """

        async def _op(cur: Any) -> None:
            await cur.executemany(sql, rows)

        await self._run(_op)
        return len(rows)

    async def insert_alpha_scores(self, scores: Sequence[dict]) -> int:
        rows = [_alpha_score_row(s) for s in scores]
        if not rows:
            return 0
        sql = """
            INSERT INTO alpha_scores
                (ts, symbol, timeframe, direction, score, momentum, volatility, zscore,
                 flow, gross_edge, cost_spread, cost_commission, cost_slippage, net_edge, features)
            VALUES (COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        async def _op(cur: Any) -> None:
            await cur.executemany(sql, rows)

        await self._run(_op)
        return len(rows)

    async def fetch_bars_asof(
        self,
        symbol: str,
        timeframe: str,
        asof: datetime,
        limit: int,
    ) -> list[Bar]:
        """Bars at or before `asof`, oldest-first. Does not include later bars."""
        when = asof if asof.tzinfo else asof.replace(tzinfo=timezone.utc)
        rows = await self.fetch_all(
            "SELECT symbol, timeframe, ts, open, high, low, close, "
            "tick_volume, real_volume, spread "
            "FROM md_bars WHERE symbol = %s AND timeframe = %s AND ts <= %s "
            "ORDER BY ts DESC LIMIT %s",
            (
                str(symbol).strip().upper(),
                str(timeframe).strip().upper(),
                when,
                max(1, int(limit)),
            ),
        )
        return [_bar_from_row(row) for row in reversed(rows)]

    async def fetch_alpha_asof(self, symbol: str, asof: datetime) -> Optional[dict]:
        """Latest shadow score at or before the trade open. None if none exists."""
        when = asof if asof.tzinfo else asof.replace(tzinfo=timezone.utc)
        row = await self.fetch_one(
            "SELECT direction, score, features FROM alpha_scores "
            "WHERE symbol = %s AND ts <= %s ORDER BY ts DESC LIMIT 1",
            (str(symbol).strip().upper(), when),
        )
        if not row:
            return None
        features = row[2] if isinstance(row[2], dict) else {}
        return {
            "direction": row[0],
            "score": _f(row[1]),
            "q": _f(features.get("q")) if isinstance(features, dict) else None,
            "features": features,
        }

    async def upsert_manual_journal(self, rows_in: Sequence[dict]) -> int:
        rows = [_manual_journal_row(r) for r in rows_in if _i(r.get("position_id"))]
        if not rows:
            return 0
        sql = """
            INSERT INTO manual_trade_journal
                (position_id, close_deal, symbol, side, magic, comment, volume,
                 price_open, price_close, opened_at, closed_at, net_profit, winner,
                 hour_of_day, correlation_groups, zscore, rsi, rsi_velocity, atr,
                 bandwidth, squeeze, rvol, alpha_direction, alpha_score, q,
                 dashboard, context)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (position_id) DO UPDATE SET
                net_profit = EXCLUDED.net_profit,
                winner = EXCLUDED.winner,
                price_close = EXCLUDED.price_close,
                closed_at = EXCLUDED.closed_at,
                zscore = COALESCE(manual_trade_journal.zscore, EXCLUDED.zscore),
                rsi = COALESCE(manual_trade_journal.rsi, EXCLUDED.rsi),
                rsi_velocity = COALESCE(manual_trade_journal.rsi_velocity, EXCLUDED.rsi_velocity),
                atr = COALESCE(manual_trade_journal.atr, EXCLUDED.atr),
                bandwidth = COALESCE(manual_trade_journal.bandwidth, EXCLUDED.bandwidth),
                squeeze = COALESCE(manual_trade_journal.squeeze, EXCLUDED.squeeze),
                rvol = COALESCE(manual_trade_journal.rvol, EXCLUDED.rvol),
                alpha_direction = COALESCE(manual_trade_journal.alpha_direction, EXCLUDED.alpha_direction),
                alpha_score = COALESCE(manual_trade_journal.alpha_score, EXCLUDED.alpha_score),
                q = COALESCE(manual_trade_journal.q, EXCLUDED.q),
                hour_of_day = COALESCE(manual_trade_journal.hour_of_day, EXCLUDED.hour_of_day),
                correlation_groups = COALESCE(manual_trade_journal.correlation_groups, EXCLUDED.correlation_groups),
                dashboard = COALESCE(manual_trade_journal.dashboard, EXCLUDED.dashboard),
                context = CASE
                    WHEN manual_trade_journal.zscore IS NULL THEN EXCLUDED.context
                    ELSE manual_trade_journal.context
                END
        """

        async def _op(cur: Any) -> None:
            await cur.executemany(sql, rows)

        await self._run(_op)
        return len(rows)

    async def list_manual_journal(self, limit: int = 500) -> list[dict]:
        cap = max(1, min(int(limit), 5000))
        rows = await self.fetch_all(
            "SELECT position_id, symbol, side, net_profit, winner, hour_of_day, "
            "correlation_groups, zscore, rsi, rvol, squeeze, q, closed_at, alpha_direction "
            "FROM manual_trade_journal ORDER BY closed_at DESC NULLS LAST LIMIT %s",
            (cap,),
        )
        out: list[dict] = []
        for row in rows:
            out.append(
                {
                    "position_id": row[0],
                    "symbol": row[1],
                    "side": row[2],
                    "net_profit": _f(row[3]),
                    "winner": bool(row[4]),
                    "hour_of_day": row[5],
                    "correlation_groups": row[6],
                    "zscore": _f(row[7]),
                    "rsi": _f(row[8]),
                    "rvol": _f(row[9]),
                    "squeeze": row[10],
                    "q": _f(row[11]),
                    "closed_at": row[12],
                    "alpha_direction": row[13],
                }
            )
        return out

    async def ensure_miner_reports_table(self) -> None:
        """Idempotent. Existing volumes never re-run schema.sql on their own."""
        await self.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_miner_reports (
                id          bigserial   PRIMARY KEY,
                ts          timestamptz NOT NULL DEFAULT now(),
                trigger     text        NOT NULL,
                n_trades    integer     NOT NULL,
                n_winners   integer     NOT NULL,
                n_new       integer     NOT NULL,
                summary     text        NOT NULL,
                comparison  jsonb
            )
            """
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS manual_miner_reports_ts_idx "
            "ON manual_miner_reports (ts DESC)"
        )

    async def count_manual_journal(self) -> tuple[int, int]:
        row = await self.fetch_one(
            "SELECT count(*), count(*) FILTER (WHERE winner) FROM manual_trade_journal"
        )
        if not row:
            return 0, 0
        return int(row[0] or 0), int(row[1] or 0)

    async def latest_miner_report_cursor(self) -> tuple[int, int, bool]:
        latest = await self.fetch_one(
            "SELECT n_trades, n_winners FROM manual_miner_reports ORDER BY id DESC LIMIT 1"
        )
        stat = await self.fetch_one(
            "SELECT 1 FROM manual_miner_reports WHERE trigger LIKE %s LIMIT 1",
            ("%stat_min%",),
        )
        if not latest:
            return 0, 0, bool(stat)
        return int(latest[0] or 0), int(latest[1] or 0), bool(stat)

    async def insert_miner_report(
        self,
        *,
        trigger: str,
        n_trades: int,
        n_winners: int,
        n_new: int,
        summary: str,
        comparison: dict,
    ) -> None:
        allowed = {"every_n", "stat_min", "stat_min+every_n"}
        label = trigger if trigger in allowed else "every_n"
        text = str(summary or "")[:8000]
        await self.execute(
            "INSERT INTO manual_miner_reports "
            "(trigger, n_trades, n_winners, n_new, summary, comparison) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                label,
                max(0, int(n_trades)),
                max(0, int(n_winners)),
                max(0, int(n_new)),
                text,
                _jsonb(comparison if isinstance(comparison, dict) else {}),
            ),
        )


# ---------------------------------------------------------------------------
# Row adapters
# ---------------------------------------------------------------------------


def _tick_row(tick: Tick) -> tuple:
    return (
        tick.symbol,
        tick.ts,
        int(tick.ts_msc),
        _f(tick.bid),
        _f(tick.ask),
        _f(tick.last),
        _f(tick.volume),
        int(tick.flags) if tick.flags is not None else None,
        None,
    )


def _bar_row(bar: Bar) -> tuple:
    ts = bar.ts if bar.ts.tzinfo else bar.ts.replace(tzinfo=timezone.utc)
    return (
        bar.symbol,
        bar.timeframe,
        ts,
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
        int(bar.tick_volume) if bar.tick_volume is not None else None,
        int(bar.real_volume) if bar.real_volume is not None else None,
        int(bar.spread) if bar.spread is not None else None,
    )


def _trade_event_row(event: dict) -> tuple:
    payload = dict(event or {})
    ts = payload.get("ts") or payload.get("timestamp")
    if not isinstance(ts, datetime):
        ts = datetime.now(timezone.utc)
    return (
        ts,
        str(payload.get("event") or payload.get("type") or "UNKNOWN"),
        _s(payload.get("symbol")),
        _i(payload.get("ticket")),
        _i(payload.get("magic")),
        _jsonb(payload),
    )


def _position_closed_row(pos: dict) -> tuple:
    p = dict(pos or {})
    profit = _f(p.get("profit"))
    commission = _f(p.get("commission"))
    swap = _f(p.get("swap"))
    net = p.get("net_profit")
    if net is None:
        parts = [x for x in (profit, commission, swap) if x is not None]
        net = sum(parts) if parts else None
    return (
        _i(p.get("ticket")),
        _s(p.get("symbol")) or "",
        _s(p.get("side")) or "",
        _f(p.get("volume")),
        _f(p.get("price_open")),
        _f(p.get("price_close")),
        _f(p.get("sl")),
        _f(p.get("tp")),
        p.get("opened_at"),
        p.get("closed_at"),
        profit,
        commission,
        swap,
        _f(net),
        _i(p.get("magic")),
        _s(p.get("comment")),
        _jsonb(p),
    )


def _alpha_score_row(score: dict) -> tuple:
    s = dict(score or {})
    return (
        s.get("ts"),
        _s(s.get("symbol")) or "",
        _s(s.get("timeframe")) or "",
        _s(s.get("direction")) or "NONE",
        _f(s.get("score")) or 0.0,
        _f(s.get("momentum")),
        _f(s.get("volatility")),
        _f(s.get("zscore")),
        _f(s.get("flow")),
        _f(s.get("gross_edge")),
        _f(s.get("cost_spread")),
        _f(s.get("cost_commission")),
        _f(s.get("cost_slippage")),
        _f(s.get("net_edge")),
        _jsonb(s.get("features") or {}),
    )


def _bar_from_row(row: Sequence[Any]) -> Bar:
    ts = row[2]
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        symbol=str(row[0]),
        timeframe=str(row[1]),
        ts=ts,
        open=float(row[3]),
        high=float(row[4]),
        low=float(row[5]),
        close=float(row[6]),
        tick_volume=int(row[7]) if row[7] is not None else None,
        real_volume=int(row[8]) if row[8] is not None else None,
        spread=int(row[9]) if row[9] is not None else None,
    )


def _manual_journal_row(row: dict) -> tuple:
    r = dict(row or {})
    hour = _i(r.get("hour_of_day")) if r.get("hour_of_day") is not None else None
    if hour is not None and (hour < 0 or hour > 23):
        hour = None
    return (
        _i(r.get("position_id")),
        _i(r.get("close_deal")),
        (_s(r.get("symbol")) or "")[:32],
        (_s(r.get("side")) or "?")[:8],
        _i(r.get("magic")),
        (_s(r.get("comment")) or "")[:120],
        _f(r.get("volume")),
        _f(r.get("price_open")),
        _f(r.get("price_close")),
        r.get("opened_at"),
        r.get("closed_at"),
        _f(r.get("net_profit")),
        bool(r.get("winner")),
        hour,
        (_s(r.get("correlation_groups")) or "")[:120],
        _f(r.get("zscore")),
        _f(r.get("rsi")),
        _f(r.get("rsi_velocity")),
        _f(r.get("atr")),
        _f(r.get("bandwidth")),
        r.get("squeeze") if isinstance(r.get("squeeze"), bool) else None,
        _f(r.get("rvol")),
        _s(r.get("alpha_direction")),
        _f(r.get("alpha_score")),
        _f(r.get("q")),
        _jsonb(r.get("dashboard")),
        _jsonb(r.get("context") or {}),
    )


def _jsonb(value: Any) -> str:
    try:
        from psycopg.types.json import Jsonb

        return Jsonb(value)  # type: ignore[return-value]
    except Exception:
        return json.dumps(value, ensure_ascii=False, default=str)


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # drop NaN


def _i(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    out = str(value).strip()
    return out or None


def _is_missing_partition(exc: BaseException) -> bool:
    text = str(exc)
    return _MISSING_PARTITION_SQLSTATE in text or "no partition of relation" in text


def _is_connection_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {"OperationalError", "InterfaceError", "PoolTimeout", "PoolClosed"}:
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("connection", "server closed", "timeout", "could not connect")
    )


def chunked(items: Iterable[Any], size: int) -> Iterable[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
