from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest

from trading.application import alpha_engine as alpha_mod
from trading.application.alpha_engine import (
    AlphaEngine,
    bars_to_series,
    infer_point,
    parse_point_overrides,
    spread_points_from_tick,
)
from trading.domain.market_data import Bar, Tick
from trading.infrastructure.bus.redis_bus import BusUnavailable
from trading.infrastructure.data_layer_config import AlphaSettings


def test_no_execution_import_path():
    src = Path("trading/application/alpha_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"bot_mt5", "watcher_ladder", "execution"}
    assert imported.isdisjoint(forbidden)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open_trade", "order_send"}


def test_infer_point_table():
    cases = [
        ("jpy suffix", "USDJPY", None, {}, 0.001),
        ("jpy broker dot", "USDJPY.", None, {}, 0.001),
        ("btc", "BTCUSD", None, {}, 0.01),
        ("btc broker bang", "BTCUSD!", None, {}, 0.01),
        ("gold broker dot", "XAUUSD.", None, {}, 0.01),
        ("override wins", "USDJPY", None, {"USDJPY": 0.01}, 0.01),
        ("fx default", "EURUSD", 1.08, {}, 0.00001),
        ("large price fallback", "FOO", 2500.0, {}, 0.01),
    ]
    for name, symbol, price, overrides, want in cases:
        assert infer_point(symbol, price, overrides) == want, name


def test_parse_point_overrides_and_spread():
    assert parse_point_overrides('{"BTCUSD": 0.1}') == {"BTCUSD": 0.1}
    assert parse_point_overrides("nope") == {}
    tick = Tick(symbol="EURUSD", ts_msc=1, bid=1.10000, ask=1.10010)
    assert abs(spread_points_from_tick(tick, 0.00001) - 10.0) < 1e-9
    assert spread_points_from_tick(None, 0.00001) == 0.0


def _bars(n: int = 80, start: float = 100.0, step: float = 0.2) -> list[Bar]:
    out: list[Bar] = []
    price = start
    ts0 = datetime(2026, 9, 24, tzinfo=timezone.utc)
    for i in range(n):
        o = price
        c = price + step
        out.append(
            Bar(
                symbol="EURUSD",
                timeframe="M15",
                ts=ts0,
                open=o,
                high=max(o, c) + 0.05,
                low=min(o, c) - 0.05,
                close=c,
                tick_volume=1000 + i,
                spread=8,
            )
        )
        price = c
    return out


class _FakeStore:
    def __init__(self, bars, ticks=None, fail=False):
        self.bars = bars
        self.ticks = ticks or []
        self.fail = fail
        self.written: list[dict] = []
        self.closed = False

    async def fetch_bars(self, symbol, timeframe, limit):
        if self.fail:
            from trading.infrastructure.persistence.pg import PgUnavailable

            raise PgUnavailable("down")
        return list(self.bars)[: int(limit)]

    async def fetch_recent_ticks(self, symbol, limit):
        return list(self.ticks)[: int(limit)]

    async def insert_alpha_scores(self, rows):
        if self.fail:
            from trading.infrastructure.persistence.pg import PgUnavailable

            raise PgUnavailable("down")
        self.written.extend(rows)
        return len(rows)

    async def ensure_open(self):
        return not self.fail

    async def close(self):
        self.closed = True


class _FlakyBus:
    def __init__(self, fail_first: int = 1):
        self.fail_first = fail_first
        self.calls = 0

    async def subscribe_ticks(self, symbols):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise BusUnavailable("redis down")
        yield Tick(symbol="EURUSD", ts_msc=1, bid=1.1, ask=1.1001)
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_score_symbol_happy_and_persist():
    store = _FakeStore(_bars())
    engine = AlphaEngine(
        settings=AlphaSettings(symbols=["EURUSD"], timeframe="M15", bars=80, min_score=0.05),
        store=store,  # type: ignore[arg-type]
    )
    score = await engine.score_symbol("EURUSD")
    assert score is not None
    written = await engine.persist(score)
    assert written == 1
    assert store.written[0]["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_score_symbol_too_few_bars():
    store = _FakeStore(_bars(n=5))
    engine = AlphaEngine(settings=AlphaSettings(symbols=["EURUSD"]), store=store)  # type: ignore[arg-type]
    assert await engine.score_symbol("EURUSD") is None


@pytest.mark.asyncio
async def test_run_once_skips_when_pg_down():
    store = _FakeStore(_bars(), fail=True)
    engine = AlphaEngine(settings=AlphaSettings(symbols=["EURUSD"]), store=store)  # type: ignore[arg-type]
    out = await engine.run_once()
    assert out == []
    assert engine.stats["skipped"] == 1


@pytest.mark.asyncio
async def test_run_once_resumes_after_pg_returns():
    store = _FakeStore(_bars(), fail=True)
    engine = AlphaEngine(
        settings=AlphaSettings(symbols=["EURUSD"], timeframe="M15", bars=80, min_score=0.05),
        store=store,  # type: ignore[arg-type]
    )
    assert await engine.run_once() == []
    store.fail = False
    out = await engine.run_once()
    assert len(out) == 1
    assert store.written[0]["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_start_once_survives_pg_down():
    store = _FakeStore(_bars(), fail=True)
    engine = AlphaEngine(settings=AlphaSettings(symbols=["EURUSD"]), store=store)  # type: ignore[arg-type]
    assert await engine.start(once=True) == 0
    assert store.closed is True


@pytest.mark.asyncio
async def test_subscribe_loop_retries_after_bus_drop(monkeypatch):
    monkeypatch.setattr(alpha_mod, "_SUBSCRIBE_RETRY_SECONDS", 0.01)
    store = _FakeStore(_bars())
    bus = _FlakyBus(fail_first=1)
    engine = AlphaEngine(
        settings=AlphaSettings(symbols=["EURUSD"]),
        store=store,  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
    )
    task = asyncio.create_task(engine._subscribe_loop())
    try:
        for _ in range(50):
            if "EURUSD" in engine._dirty and bus.calls >= 2:
                break
            await asyncio.sleep(0.02)
        assert bus.calls >= 2
        assert "EURUSD" in engine._dirty
    finally:
        engine.request_stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_bars_to_series_length():
    series = bars_to_series(_bars(n=4))
    assert len(series["closes"]) == 4
    assert series["volumes"][0] == 1000
