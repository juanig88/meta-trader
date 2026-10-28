"""Integration tests against the local Docker data layer.

Skip cleanly when Postgres/Redis are not reachable so `pytest` stays green
without Docker. Marked `integration`.
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from trading.domain.market_data import Bar, Tick
from trading.infrastructure.async_runtime import run as run_async
from trading.infrastructure.bus.redis_bus import BusUnavailable, RedisBus
from trading.infrastructure.data_layer_config import (
    DataLayerConfigError,
    PgSettings,
    RedisSettings,
    RetentionSettings,
)
from trading.infrastructure.persistence.janitor import PartitionJanitor
from trading.infrastructure.persistence.pg import PgStore, PgUnavailable

pytestmark = pytest.mark.integration


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _pg_or_skip() -> PgSettings:
    _load_dotenv()
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError as exc:
        pytest.skip(str(exc))
    if not _port_open(settings.host, settings.port):
        pytest.skip(f"postgres not listening on {settings.host}:{settings.port}")
    return settings


def _redis_or_skip() -> RedisSettings:
    _load_dotenv()
    settings = RedisSettings.from_env()
    if not _port_open(settings.host, settings.port):
        pytest.skip(f"redis not listening on {settings.host}:{settings.port}")
    return settings


def test_ticks_persist_and_partitions_roll():
    settings = _pg_or_skip()

    async def _inner() -> None:
        store = PgStore(settings)
        try:
            if not await store.ensure_open():
                pytest.skip("postgres is not reachable on the configured host/port")
            janitor = PartitionJanitor(store, RetentionSettings.from_env())
            report = await janitor.run_once()
            assert report.ensured

            symbol = "ITEST"
            now = datetime.now(timezone.utc)
            ticks = [
                Tick(symbol=symbol, ts_msc=int(now.timestamp() * 1000) + i, bid=1.0 + i * 0.0001, ask=1.0002 + i * 0.0001)
                for i in range(5)
            ]
            written = await store.copy_ticks(ticks)
            assert written == 5
            count = await store.row_count("md_ticks", "symbol = %s", (symbol,))
            assert count >= 5

            today = now.date()
            inventory = await store.partition_inventory("md_ticks")
            days = {row[2] for row in inventory}
            assert today in days
            assert today + timedelta(days=1) in days

            old_day = today - timedelta(days=10)
            child = await store.ensure_partition("md_ticks", old_day)
            assert child
            dropped = await store.drop_partitions_before("md_ticks", today - timedelta(days=5))
            assert dropped >= 1
            leftover = {row[2] for row in await store.partition_inventory("md_ticks")}
            assert old_day not in leftover
        except PgUnavailable as exc:
            pytest.skip(f"postgres unavailable: {exc}")
        finally:
            await store.close()

    run_async(_inner)


def test_bars_upsert_and_alpha_score_row():
    settings = _pg_or_skip()

    async def _inner() -> None:
        store = PgStore(settings)
        try:
            if not await store.ensure_open():
                pytest.skip("postgres is not reachable")
            ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            bar = Bar(
                symbol="ITEST",
                timeframe="M15",
                ts=ts,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.05,
                tick_volume=10,
                spread=8,
            )
            assert await store.upsert_bars([bar]) == 1
            bars = await store.fetch_bars("ITEST", "M15", 5)
            assert bars and bars[-1].close == 1.05

            n = await store.insert_alpha_scores(
                [
                    {
                        "symbol": "ITEST",
                        "timeframe": "M15",
                        "direction": "NONE",
                        "score": 0.0,
                        "net_edge": -1.0,
                    }
                ]
            )
            assert n == 1
            assert await store.row_count("alpha_scores", "symbol = %s", ("ITEST",)) >= 1
        except PgUnavailable as exc:
            pytest.skip(f"postgres unavailable: {exc}")
        finally:
            await store.close()

    run_async(_inner)


def test_redis_publish_roundtrip():
    settings = _redis_or_skip()

    async def _inner() -> None:
        bus = RedisBus(settings)
        try:
            if not await bus.ensure_open():
                pytest.skip("redis is not reachable")
            tick = Tick(symbol="ITEST", ts_msc=1_700_000_000_000, bid=1.0, ask=1.1)
            assert await bus.publish_ticks([tick]) == 1
            recent = await bus.read_recent_ticks("ITEST", count=5)
            if settings.stream_maxlen > 0:
                assert recent
                assert recent[-1].symbol == "ITEST"
        except BusUnavailable as exc:
            pytest.skip(f"redis unavailable: {exc}")
        finally:
            await bus.close()

    run_async(_inner)
