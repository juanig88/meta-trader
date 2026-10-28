"""Closing deals sent with magic 0 are attributed to the bot through their ENTRY_IN deal."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402

BOT = 260414
IN = int(bot_mt5.mt5.DEAL_ENTRY_IN)
OUT = int(bot_mt5.mt5.DEAL_ENTRY_OUT)


def _deal(*, entry: int, magic: int, position_id: int):
    return SimpleNamespace(entry=entry, magic=magic, position_id=position_id)


@pytest.fixture(autouse=True)
def _clear_cache():
    bot_mt5._POSITION_IN_MAGIC_CACHE.clear()
    yield
    bot_mt5._POSITION_IN_MAGIC_CACHE.clear()


def test_in_deal_magic_by_position_table():
    deals = [
        _deal(entry=IN, magic=BOT, position_id=1),
        _deal(entry=OUT, magic=0, position_id=1),
        _deal(entry=IN, magic=0, position_id=2),
        _deal(entry=IN, magic=BOT, position_id=0),
        _deal(entry=IN, magic=999, position_id=3),
        _deal(entry=IN, magic=BOT, position_id=3),
    ]
    assert bot_mt5._in_deal_magic_by_position(deals) == {1: BOT, 2: 0, 3: 999}
    assert bot_mt5._in_deal_magic_by_position(None) == {}


@pytest.mark.parametrize(
    "name,out_magic,pid,in_map,broker_in,want",
    [
        ("out_has_bot_magic", BOT, 10, {}, None, BOT),
        ("magic0_in_window_bot", 0, 11, {11: BOT}, None, BOT),
        ("magic0_in_window_manual", 0, 12, {12: 0}, None, None),
        ("magic0_in_window_other_magic", 0, 13, {13: 777}, None, None),
        ("magic0_broker_fallback_bot", 0, 14, {}, _deal(entry=IN, magic=BOT, position_id=14), BOT),
        ("magic0_broker_fallback_missing", 0, 15, {}, None, None),
        ("no_position_id", 0, 0, {}, None, None),
    ],
)
def test_out_deal_owner_magic(name, out_magic, pid, in_map, broker_in, want):
    cfg = bot_mt5.Config(magic=BOT)
    d = _deal(entry=OUT, magic=out_magic, position_id=pid)
    with patch.object(bot_mt5, "_first_in_deal_for_position_from_broker", return_value=broker_in) as fb:
        assert bot_mt5._out_deal_owner_magic(d, cfg, in_map) == want, name
    should_query = out_magic != BOT and pid > 0 and pid not in in_map
    assert fb.called is should_query, name


def test_out_deal_owner_magic_caches_broker_lookup():
    cfg = bot_mt5.Config(magic=BOT)
    d = _deal(entry=OUT, magic=0, position_id=20)
    fin = _deal(entry=IN, magic=BOT, position_id=20)
    with patch.object(bot_mt5, "_first_in_deal_for_position_from_broker", return_value=fin) as fb:
        assert bot_mt5._out_deal_owner_magic(d, cfg, {}) == BOT
        assert bot_mt5._out_deal_owner_magic(d, cfg, {}) == BOT
    assert fb.call_count == 1


@pytest.mark.parametrize(
    "name,positions,raises,want",
    [
        ("bot_position", (SimpleNamespace(magic=BOT),), False, BOT),
        ("manual_position", (SimpleNamespace(magic=0),), False, 0),
        ("not_found", None, False, 0),
        ("empty", (), False, 0),
        ("api_error", None, True, 0),
    ],
)
def test_position_magic(name, positions, raises, want):
    kw = {"side_effect": RuntimeError("mt5")} if raises else {"return_value": positions}
    with patch.object(bot_mt5.mt5, "positions_get", **kw):
        assert bot_mt5._position_magic(42) == want, name


@pytest.mark.parametrize(
    "name,entry,want",
    [("in_is_zero", IN, IN), ("out", OUT, OUT), ("missing", None, -1), ("garbage", "x", -1)],
)
def test_deal_entry(name, entry, want):
    assert bot_mt5._deal_entry(SimpleNamespace(entry=entry)) == want, name


def test_count_broker_entry_ins_counts_in_deals_with_bot_magic():
    from datetime import datetime

    cfg = bot_mt5.Config(magic=BOT)
    deals = [
        _deal(entry=IN, magic=BOT, position_id=1),
        _deal(entry=IN, magic=BOT, position_id=2),
        _deal(entry=OUT, magic=0, position_id=1),
        _deal(entry=OUT, magic=BOT, position_id=2),
        _deal(entry=IN, magic=0, position_id=3),
        _deal(entry=IN, magic=777, position_id=4),
    ]
    with patch.object(bot_mt5.mt5, "history_deals_get", return_value=deals):
        assert bot_mt5._count_broker_entry_ins(cfg, datetime(2026, 10, 6, 12, 0, 0), 3600.0) == 2


def test_log_new_closes_drains_backlog_newest_first(monkeypatch):
    cfg = bot_mt5.Config(magic=BOT)
    ins = [
        SimpleNamespace(entry=IN, magic=BOT, position_id=100 + i, ticket=1000 + i, time=1_000 + i, time_msc=0,
                        symbol="ADAUSD!", price=1.0, volume=1.0)
        for i in range(8)
    ]
    outs = [
        SimpleNamespace(entry=OUT, magic=0, position_id=100 + i, ticket=2000 + i, time=2_000 + i, time_msc=0,
                        symbol="ADAUSD!", price=1.1, volume=1.0, profit=0.1, swap=0.0, commission=0.0,
                        reason=3, type=int(bot_mt5.mt5.DEAL_TYPE_SELL), comment="stale_take")
        for i in range(8)
    ]
    logged: list[dict] = []
    monkeypatch.setattr(bot_mt5.mt5, "history_deals_get", lambda *a, **k: tuple(ins + outs))
    monkeypatch.setattr(bot_mt5.mt5, "positions_get", lambda *a, **k: ())
    monkeypatch.setattr(bot_mt5, "_seed_close_log_tracker", lambda cfg: None)
    monkeypatch.setattr(bot_mt5, "_compute_trade_grades", lambda **k: {})
    monkeypatch.setattr(bot_mt5, "_spread_usd_now", lambda *a, **k: None)
    monkeypatch.setattr(bot_mt5, "_log_event", lambda p: logged.append(p))
    monkeypatch.setattr(bot_mt5, "_CLOSE_LOG_SEEN_DEAL_TICKETS", set())
    monkeypatch.setattr(bot_mt5, "_CLOSE_LOG_BACKFILL_DONE", False)
    monkeypatch.setattr(bot_mt5, "_CLOSE_LOG_MAX_PER_CALL", 5)

    bot_mt5._log_new_closes(cfg)
    assert [p["deal_ticket"] for p in logged] == [2007, 2006, 2005, 2004, 2003]
    assert bot_mt5._CLOSE_LOG_BACKFILL_DONE is False
    assert all(p["magic"] == BOT and p["price_open"] == 1.0 for p in logged)

    bot_mt5._log_new_closes(cfg)
    assert [p["deal_ticket"] for p in logged[5:]] == [2002, 2001, 2000]
    assert bot_mt5._CLOSE_LOG_BACKFILL_DONE is True

    bot_mt5._log_new_closes(cfg)
    assert len(logged) == 8


def test_close_position_market_sends_position_magic():
    tick = SimpleNamespace(bid=1.0, ask=1.01)
    ok = SimpleNamespace(retcode=bot_mt5.mt5.TRADE_RETCODE_DONE, comment="ok")
    with patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=tick), patch.object(
        bot_mt5.mt5, "positions_get", return_value=(SimpleNamespace(magic=BOT),)
    ), patch.object(bot_mt5.mt5, "order_send", return_value=ok) as send:
        assert bot_mt5._close_position_market("ADAUSD!", 7, 0.01, "BUY", comment="stale_take") is True
    request = send.call_args[0][0]
    assert request["magic"] == BOT
    assert request["position"] == 7
