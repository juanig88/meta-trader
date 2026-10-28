"""MT5 market-data ingest daemon.

Separate process from bot_mt5.py, exactly like watcher_ladder.py. It reads
ticks and bars from the MT5 terminal, fans them out on Redis pub/sub, and
batches them into Postgres. It sends no orders and touches no position.

If this daemon dies, the bot keeps trading unchanged.

On the "asynchronous listener" in the brief: the MetaTrader5 package is
synchronous and exposes no push/callback API, so a true event-driven listener
is not possible. Instead this polls copy_ticks_from with a per-symbol cursor.
copy_ticks_from returns every tick since the cursor, so nothing is lost
between polls, only delayed by at most one interval.

Usage:
    py mt5_tick_daemon.py
    py mt5_tick_daemon.py --symbols BTCUSD,ETHUSD --interval 0.5
    py mt5_tick_daemon.py --once            # one cycle, for verification
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import MetaTrader5 as mt5

import bot_mt5
from trading.domain.market_data import Bar, Tick
from trading.domain.xbtfx import classify_terminal_error, is_link_failure, reconnect_sleep_seconds
from trading.infrastructure.async_runtime import run as run_async
from trading.infrastructure.bus.redis_bus import BusUnavailable, RedisBus
from trading.infrastructure.data_layer_config import (
    DataLayerConfigError,
    IngestSettings,
    PgSettings,
    RedisSettings,
    RetentionSettings,
)
from trading.infrastructure.persistence.janitor import PartitionJanitor
from trading.infrastructure.persistence.pg import PgStore, PgUnavailable

LOG_FILE = "mt5_data.log"

# MT5's Python bindings are not safe to call from several threads at once, so
# every terminal call is funnelled through this single worker thread.
_MT5_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-io")


def _setup_logging(level: str = "INFO", filename: str = LOG_FILE) -> None:
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        fh = logging.FileHandler(filename, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        logging.warning("could not open %s for writing; logging to stdout only", filename)


def _log_event(payload: dict) -> None:
    """Structured JSON line, same shape as bot_mt5._log_event but on our own log."""
    try:
        logging.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logging.info("%s", payload)


async def _mt5_call(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_MT5_EXECUTOR, lambda: fn(*args))


def _build_cfg() -> bot_mt5.Config:
    """Minimal Config: connect_mt5 only needs credentials (from env) and retries."""
    return bot_mt5.Config(
        symbols=bot_mt5._env_str_list("BOT_SYMBOLS") or bot_mt5.Config.symbols,
        connect_retries=int(float(bot_mt5._env("BOT_CONNECT_RETRIES", str(bot_mt5.Config.connect_retries)))),
        connect_retry_sleep=float(
            bot_mt5._env("BOT_CONNECT_RETRY_SLEEP", str(bot_mt5.Config.connect_retry_sleep))
        ),
    )


def _query_time_from_server_ms(ts_ms: int, server_offset_seconds: int) -> datetime:
    """Convert a server-time cursor into the naive local datetime MT5 wants.

    copy_ticks_from is asymmetric, and getting this backwards fails silently
    with last_error Success and an empty array:
      - the `date_from` argument is the **terminal PC's local time** (Windows)
      - the returned time / time_msc are in SERVER time

    Subtract the measured broker offset so the query instant is real-world time,
    then express it as naive local (not UTC-stripped). On a UTC-3 box (ART),
    a UTC-naive datetime asks three hours into the future. On a UTC VPS,
    local == UTC so both forms coincide.
    """
    seconds = (ts_ms / 1000.0) - float(server_offset_seconds)
    return datetime.fromtimestamp(seconds)


class TickIngestor:
    """Per-symbol cursor over copy_ticks_from.

    Cursors are held in MT5 server time (the frame the ticks come back in) and
    translated to real UTC only when querying.
    """

    def __init__(self, symbols: Sequence[str], settings: IngestSettings) -> None:
        self._symbols = list(symbols)
        self._settings = settings
        self._cursor_ms: dict[str, int] = {}
        self._server_offset_seconds = 0
        self.link_failures = 0
        self.stats = {"fetched": 0, "published": 0, "persisted": 0, "dropped": 0}

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    async def bootstrap(self, server_offset_seconds: int = 0) -> None:
        """Start each cursor just behind the current tick so we don't dump history.

        The fallback adds the measured broker offset: cursors live in MT5
        server time, and seeding one with real UTC on a UTC+3 broker would
        rewind it three hours and replay stale ticks.
        """
        self._server_offset_seconds = int(server_offset_seconds)
        for symbol in self._symbols:
            info = await _mt5_call(mt5.symbol_info_tick, symbol)
            fallback_ms = int(
                (datetime.now(timezone.utc).timestamp() + server_offset_seconds) * 1000
            )
            ts_ms = int(getattr(info, "time_msc", 0) or 0) if info is not None else 0
            self._cursor_ms[symbol] = (ts_ms or fallback_ms) - 1000
        logging.info("tick cursors initialised for %s", ", ".join(self._symbols))

    async def poll(self, symbol: str) -> list[Tick]:
        cursor = self._cursor_ms.get(symbol)
        if cursor is None:
            return []
        raw = await _mt5_call(
            mt5.copy_ticks_from,
            symbol,
            _query_time_from_server_ms(cursor, self._server_offset_seconds),
            int(self._settings.tick_batch_max),
            mt5.COPY_TICKS_ALL,
        )
        if raw is None:
            code, msg = mt5.last_error()
            kind = classify_terminal_error(code, str(msg))
            logging.warning(
                "copy_ticks_from(%s) returned None: [%s] %s (%s)",
                symbol,
                code,
                msg,
                kind,
            )
            if code not in (0, 1) and is_link_failure(kind):
                self.link_failures += 1
            return []

        out: list[Tick] = []
        newest = cursor
        for row in raw:
            ts_msc = int(row["time_msc"])
            # copy_ticks_from is inclusive of the cursor instant, so re-filter.
            if ts_msc <= cursor:
                continue
            out.append(
                Tick(
                    symbol=symbol,
                    ts_msc=ts_msc,
                    bid=_pos(row["bid"]),
                    ask=_pos(row["ask"]),
                    last=_pos(row["last"]),
                    volume=float(row["volume"]) if row["volume"] else None,
                    flags=int(row["flags"]),
                )
            )
            if ts_msc > newest:
                newest = ts_msc
        self._cursor_ms[symbol] = newest
        self.stats["fetched"] += len(out)
        return out

    def saturated(self, count: int) -> bool:
        """True when a poll filled the batch, meaning there is likely more waiting."""
        return count >= int(self._settings.tick_batch_max)


class BarIngestor:
    def __init__(self, symbols: Sequence[str], settings: IngestSettings) -> None:
        self._symbols = list(symbols)
        self._settings = settings
        self._backfilled: set[tuple[str, str]] = set()

    async def poll(self, symbol: str, timeframe: str) -> list[Bar]:
        key = (symbol, timeframe)
        # First pass pulls history; afterwards only the last few bars, so the
        # still-forming bar gets corrected by the upsert as it closes.
        count = self._settings.bar_backfill if key not in self._backfilled else 3
        if count <= 0:
            self._backfilled.add(key)
            return []
        rates = await _mt5_call(
            mt5.copy_rates_from_pos, symbol, bot_mt5._mt5_timeframe(timeframe), 0, int(count)
        )
        self._backfilled.add(key)
        if rates is None or len(rates) == 0:
            return []
        bars: list[Bar] = []
        for row in rates:
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=datetime.fromtimestamp(int(row["time"]), tz=timezone.utc),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    tick_volume=int(row["tick_volume"]),
                    real_volume=int(row["real_volume"]),
                    spread=int(row["spread"]),
                )
            )
        return bars


class TickDaemon:
    def __init__(
        self,
        *,
        ingest: IngestSettings,
        pg: Optional[PgSettings],
        redis: RedisSettings,
        retention: RetentionSettings,
    ) -> None:
        self._ingest = ingest
        self._store = PgStore(pg) if pg is not None else None
        self._bus = RedisBus(redis)
        self._janitor = (
            PartitionJanitor(self._store, retention) if self._store is not None else None
        )
        self._retention = retention
        self._stop = asyncio.Event()
        self._ticks = TickIngestor(ingest.symbols, ingest)
        self._bars = BarIngestor(ingest.symbols, ingest)
        # Bounded buffer so a short Postgres outage does not lose ticks, and a
        # long one does not exhaust memory. Oldest ticks are dropped first.
        self._pending: deque[Tick] = deque(maxlen=max(10_000, ingest.tick_batch_max * 50))
        self._pending_overflow = 0
        self._server_offset_seconds = 0
        self._reconnect_attempt = 0
        self._reconnect_not_before = 0.0

    def request_stop(self) -> None:
        self._stop.set()

    async def _connect_mt5(self) -> bool:
        cfg = _build_cfg()
        ok = await _mt5_call(bot_mt5.connect_mt5, cfg)
        if not ok:
            return False
        for symbol in list(self._ingest.symbols):
            if not await _mt5_call(bot_mt5._ensure_symbol, symbol):
                logging.error("symbol unavailable in MT5, skipping: %s", symbol)
        return True

    async def _mt5_alive(self) -> bool:
        return (await _mt5_call(mt5.terminal_info)) is not None

    async def _measure_server_offset(self) -> int:
        """Seconds MT5 server time runs ahead of real UTC.

        MT5 reports timestamps as "UTC without the shift", so stored values
        are broker time. We keep them that way to stay joinable with
        trades.log, and record the offset so the frame is never a guess.
        """
        for symbol in self._ticks.symbols:
            info = await _mt5_call(mt5.symbol_info_tick, symbol)
            server = int(getattr(info, "time", 0) or 0) if info is not None else 0
            if server:
                return int(round(server - datetime.now(timezone.utc).timestamp()))
        return 0

    async def _flush_ticks(self, ticks: Sequence[Tick]) -> None:
        if ticks and self._ingest.publish_ticks:
            try:
                self._ticks.stats["published"] += await self._bus.publish_ticks(ticks)
            except BusUnavailable as exc:
                logging.warning("redis publish failed (%s); continuing", exc)

        if not self._ingest.persist_ticks or self._store is None:
            return

        before = len(self._pending)
        self._pending.extend(ticks)
        # deque(maxlen) silently discards from the left; count what we lost.
        lost = before + len(ticks) - len(self._pending)
        if lost > 0:
            self._pending_overflow += lost
            self._ticks.stats["dropped"] += lost

        if not self._pending:
            return
        batch = list(self._pending)
        try:
            written = await self._store.copy_ticks(batch)
        except PgUnavailable as exc:
            logging.warning(
                "postgres write failed (%s); %d tick(s) buffered", exc, len(self._pending)
            )
            return
        self._pending.clear()
        self._ticks.stats["persisted"] += written
        if self._pending_overflow:
            logging.error(
                "dropped %d tick(s) while postgres was unavailable", self._pending_overflow
            )
            self._pending_overflow = 0

    async def _tick_loop(self) -> None:
        while not self._stop.is_set():
            saturated = False
            failures_before = self._ticks.link_failures
            for symbol in self._ticks.symbols:
                if self._stop.is_set():
                    break
                try:
                    ticks = await self._ticks.poll(symbol)
                except Exception:
                    logging.exception("tick poll failed for %s", symbol)
                    continue
                if ticks:
                    await self._flush_ticks(ticks)
                    saturated = saturated or self._ticks.saturated(len(ticks))
            if self._ticks.link_failures == failures_before:
                self._ticks.link_failures = 0
            elif self._ticks.link_failures >= 3:
                await self._reconnect_terminal("tick link")
            if saturated:
                # The batch cap was hit: keep draining instead of sleeping.
                continue
            await _sleep_or_stop(self._stop, self._ingest.tick_poll_seconds)

    async def _bar_loop(self) -> None:
        if self._store is None or not self._ingest.bar_timeframes:
            return
        while not self._stop.is_set():
            for symbol in self._ticks.symbols:
                for timeframe in self._ingest.bar_timeframes:
                    if self._stop.is_set():
                        break
                    try:
                        bars = await self._bars.poll(symbol, timeframe)
                        if bars:
                            await self._store.upsert_bars(bars)
                    except PgUnavailable as exc:
                        logging.warning("bar write failed (%s); will retry", exc)
                    except Exception:
                        logging.exception("bar poll failed for %s %s", symbol, timeframe)
            await _sleep_or_stop(self._stop, self._ingest.bar_poll_seconds)

    async def _janitor_loop(self) -> None:
        if self._janitor is None:
            return
        interval = self._retention.janitor_interval_minutes * 60.0
        while not self._stop.is_set():
            try:
                report = await self._janitor.run_once()
                _log_event({"event": "JANITOR", **report.as_dict()})
            except PgUnavailable as exc:
                logging.warning("janitor skipped (%s)", exc)
            except Exception:
                logging.exception("janitor failed")
            await _sleep_or_stop(self._stop, interval)

    async def _heartbeat_loop(self, interval: float = 60.0) -> None:
        while not self._stop.is_set():
            await _sleep_or_stop(self._stop, interval)
            if self._stop.is_set():
                break
            _log_event(
                {
                    "event": "INGEST_HEARTBEAT",
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "symbols": self._ticks.symbols,
                    "buffered": len(self._pending),
                    **self._ticks.stats,
                }
            )
            if not await self._mt5_alive():
                await self._reconnect_terminal("terminal_info")

    async def _reconnect_terminal(self, reason: str) -> bool:
        """Shut the API connection down and attach again. Backs off after a miss."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._reconnect_not_before:
            return False
        logging.warning("XBTFX link down (%s); reconnecting", reason)
        try:
            await _mt5_call(mt5.shutdown)
        except Exception:
            logging.exception("mt5.shutdown failed during reconnect")
        if await self._connect_mt5():
            self._reconnect_attempt = 0
            self._reconnect_not_before = 0.0
            self._ticks.link_failures = 0
            self._server_offset_seconds = await self._measure_server_offset()
            await self._ticks.bootstrap(self._server_offset_seconds)
            _log_event({"event": "INGEST_RECONNECT", "reason": reason})
            return True
        self._reconnect_attempt += 1
        delay = reconnect_sleep_seconds(self._reconnect_attempt, 1.0, "retryable")
        self._reconnect_not_before = loop.time() + delay
        logging.error("XBTFX reconnect failed (%s); next attempt in %.1fs", reason, delay)
        _log_event({"event": "INGEST_RECONNECT_FAIL", "reason": reason, "sleep": delay})
        return False

    async def run_once(self) -> dict:
        """One pass of every loop body. Used by --once and by the tests."""
        if self._janitor is not None:
            try:
                report = await self._janitor.run_once()
                _log_event({"event": "JANITOR", **report.as_dict()})
            except PgUnavailable as exc:
                logging.warning("janitor skipped (%s)", exc)
        for symbol in self._ticks.symbols:
            ticks = await self._ticks.poll(symbol)
            if ticks:
                await self._flush_ticks(ticks)
            if self._store is not None:
                for timeframe in self._ingest.bar_timeframes:
                    bars = await self._bars.poll(symbol, timeframe)
                    if bars:
                        try:
                            await self._store.upsert_bars(bars)
                        except PgUnavailable as exc:
                            logging.warning("bar write failed (%s)", exc)
        return dict(self._ticks.stats)

    async def start(self, *, once: bool = False) -> int:
        if not self._ingest.symbols:
            logging.error("no symbols configured (set MT5_TICK_DAEMON_SYMBOLS or enable rotation universe)")
            return 2

        if not await self._connect_mt5():
            logging.error("could not connect to MT5")
            _log_event({"event": "INGEST_CONNECT_FAIL"})
            return 2

        # Both sinks are optional at startup: the daemon is still useful with
        # only one of them, and the other reconnects on its own.
        if self._store is not None:
            await self._store.ensure_open()
        if self._ingest.publish_ticks:
            await self._bus.ensure_open()

        self._server_offset_seconds = await self._measure_server_offset()
        await self._ticks.bootstrap(self._server_offset_seconds)
        _log_event(
            {
                "event": "INGEST_START",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbols": self._ticks.symbols,
                "timeframes": self._ingest.bar_timeframes,
                "persist": self._ingest.persist_ticks and self._store is not None,
                "publish": self._ingest.publish_ticks,
                "tick_ttl_hours": self._retention.tick_ttl_hours,
                # Stored md_ticks.ts / md_bars.ts are in this frame, not real UTC.
                "server_utc_offset_seconds": self._server_offset_seconds,
            }
        )

        try:
            if once:
                stats = await self.run_once()
                _log_event({"event": "INGEST_ONCE", **stats})
                return 0
            await asyncio.gather(
                self._tick_loop(),
                self._bar_loop(),
                self._janitor_loop(),
                self._heartbeat_loop(),
            )
            return 0
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        if self._pending and self._store is not None:
            try:
                await self._store.copy_ticks(list(self._pending))
                self._pending.clear()
            except PgUnavailable as exc:
                logging.warning("could not flush %d buffered tick(s): %s", len(self._pending), exc)
        if self._store is not None:
            await self._store.close()
        await self._bus.close()
        # The live bot shares this terminal. Do not mt5.shutdown() on stop.
        _log_event({"event": "INGEST_STOP", **self._ticks.stats})


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep that wakes immediately on shutdown instead of holding up Ctrl-C."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, float(seconds)))
    except (asyncio.TimeoutError, TimeoutError):
        pass


def _pos(value: Any) -> Optional[float]:
    """MT5 reports an absent price as 0.0, which is not a price."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0.0 else None


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="MT5 tick/bar ingest daemon (Redis + Postgres).")
    ap.add_argument("--symbols", default=None, help="Comma-separated override of the symbol list.")
    ap.add_argument("--interval", type=float, default=None, help="Tick poll interval in seconds.")
    ap.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    ap.add_argument("--no-persist", action="store_true", help="Skip Postgres; publish only.")
    ap.add_argument("--no-publish", action="store_true", help="Skip Redis; persist only.")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    bot_mt5._load_dotenv()
    _setup_logging(args.log_level)

    ingest = IngestSettings.from_env()
    if args.symbols:
        ingest = IngestSettings(
            **{
                **ingest.__dict__,
                "symbols": [s.strip().upper() for s in args.symbols.split(",") if s.strip()],
            }
        )
    if args.interval is not None:
        ingest = IngestSettings(**{**ingest.__dict__, "tick_poll_seconds": max(0.1, args.interval)})
    if args.no_persist:
        ingest = IngestSettings(**{**ingest.__dict__, "persist_ticks": False})
    if args.no_publish:
        ingest = IngestSettings(**{**ingest.__dict__, "publish_ticks": False})

    pg: Optional[PgSettings] = None
    if ingest.persist_ticks or ingest.bar_timeframes:
        try:
            pg = PgSettings.from_env()
        except DataLayerConfigError as exc:
            logging.error("postgres disabled: %s", exc)

    daemon = TickDaemon(
        ingest=ingest,
        pg=pg,
        redis=RedisSettings.from_env(),
        retention=RetentionSettings.from_env(),
    )

    async def _run() -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.request_stop)
            except (NotImplementedError, AttributeError):
                # Windows ProactorEventLoop has no add_signal_handler; the
                # KeyboardInterrupt path below covers Ctrl-C there.
                pass
        return await daemon.start(once=args.once)

    try:
        return run_async(_run)
    except KeyboardInterrupt:
        logging.info("interrupted; shutting down")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
