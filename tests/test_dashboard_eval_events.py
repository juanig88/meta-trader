"""Tests for dashboard EVAL log tail parser."""
from __future__ import annotations

from pathlib import Path

from flask_dashboard import (
    _deal_to_dict,
    _int_attr,
    _lab_vote_rows,
    _managed_bot_magics,
    _rotation_magics,
    _trade_universe,
    build_parallel_tracks_view,
    parse_recent_eval_events,
)


def test_parse_recent_eval_events_table(tmp_path: Path) -> None:
    log = tmp_path / "trades.log"
    lines = [
        "2026-08-09 10:00:00 | INFO | not json",
        '2026-08-09 10:00:01 | INFO | {"event":"EVAL_CYCLE","symbol":"BTCUSD","decision":"NONE"}',
        '2026-08-09 10:00:02 | INFO | {"event":"NOPE","symbol":"X"}',
        '2026-08-09 10:00:03 | INFO | {"event":"EVAL_SKIP","symbol":"XNGUSD","reason":"spread"}',
        '2026-08-09 10:00:04 | INFO | {"event":"XAUUSD_PARALLEL_SIGNAL","symbol":"XAUUSD","decision":"BUY"}',
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cases = [
        ("limit_all", 10, ["EVAL_CYCLE", "EVAL_SKIP"]),
        ("limit_1", 1, ["EVAL_SKIP"]),
    ]
    for name, limit, want_events in cases:
        got = parse_recent_eval_events(log, limit=limit)
        assert [g["event"] for g in got] == want_events, name


def test_build_parallel_tracks_view_from_state() -> None:
    state = {
        "eval_tracks": {
            "ts": "2026-08-09T12:00:00",
            "crypto_sell_requires_h4_down": True,
            "daily_loss_limit": -25.0,
            "max_open_trades_total": 3,
            "tracks": [
                {
                    "track": "btc_breakout_h4",
                    "symbol": "BTCUSD",
                    "path": "main_loop",
                    "window": "06:00-09:00",
                    "in_window": False,
                }
            ],
        }
    }
    view = build_parallel_tracks_view(state)
    assert view["meta"]["from_bot_state"] is True
    assert view["tracks"][0]["symbol"] == "BTCUSD"


class _Deal:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_int_attr_keeps_zero():
    cases = [
        ("zero", _Deal(entry=0, type=0), "entry", -1, 0),
        ("type_buy", _Deal(type=0), "type", -1, 0),
        ("missing", _Deal(), "type", -1, -1),
        ("none", _Deal(type=None), "type", -1, -1),
    ]
    for name, obj, field, default, want in cases:
        assert _int_attr(obj, field, default) == want, name


def test_deal_to_dict_preserves_buy_close():
    d = _Deal(
        time=1_758_800_000,
        ticket=11,
        position_id=22,
        symbol="ETHUSD",
        magic=0,
        entry=1,
        type=0,
        volume=0.05,
        price=2677.49,
        profit=0.32,
        swap=0.0,
        commission=0.0,
        comment="[sl]",
    )
    row = _deal_to_dict(d)
    assert row["type"] == 0
    assert row["entry"] == 1
    assert row["magic"] == 0


def test_managed_bot_magics_includes_main_and_tracks(monkeypatch):
    monkeypatch.delenv("BOT_MAGIC", raising=False)
    state = {"eval_tracks": {"tracks": [{"symbol": "BTCUSD", "magic": 260500}]}}
    got = _managed_bot_magics(state)
    assert got == {260414, 260500}


def test_trade_universe_rotation_ignores_env_symbols(monkeypatch):
    monkeypatch.setenv("BOT_SYMBOLS", "EURUSD,XAUUSD.")
    cases = [
        (
            "rotation flag",
            {"rotation_enabled": True, "symbols": ["ADAUSD!", "RSRUSD!"]},
            {"ADAUSD!", "RSRUSD!"},
        ),
        (
            "ensemble leftover",
            {"ensemble": {"scanned": 79}, "symbols": ["BTCUSD!"]},
            {"BTCUSD!"},
        ),
    ]
    for name, state, want in cases:
        got = _trade_universe(state)
        assert got == want, name


def test_rotation_magics_single_not_tracks(monkeypatch):
    monkeypatch.setenv("BOT_MAGIC", "260414")
    cases = [
        ("from state", {"magic": 260414, "eval_tracks": {"tracks": [{"magic": 99}]}}, {260414}),
        ("from env", {}, {260414}),
    ]
    for name, state, want in cases:
        got = _rotation_magics(state)
        assert got == want, name
        assert 99 not in got


def test_lab_vote_rows_prefers_payloads_with_votes():
    cases = [
        (
            "pg with votes",
            {"latest": [{"symbol": "ADAUSD!", "votes": [{"name": "m1_trend", "side": "BUY"}]}]},
            {"fired": [], "near_miss": [], "symbols": [{"symbol": "X"}]},
            "ADAUSD!",
        ),
        (
            "live near miss",
            {"latest": [{"symbol": "ADAUSD!", "votes": []}]},
            {"fired": [], "near_miss": [{"symbol": "BCHUSD!", "votes": [{"name": "m5_trend", "side": "BUY"}]}], "symbols": []},
            "BCHUSD!",
        ),
        (
            "live module skip",
            {"latest": [{"symbol": "ADAUSD!", "votes": []}]},
            {"fired": [], "near_miss": [], "module_skips": [{"symbol": "SOLUSD!", "votes": [{"name": "htf", "side": "SELL"}], "skip_reason": "veto_htf"}], "symbols": []},
            "SOLUSD!",
        ),
        (
            "slim chips",
            {"latest": []},
            {"fired": [], "near_miss": [], "symbols": [{"symbol": "RSRUSD!"}]},
            "RSRUSD!",
        ),
    ]
    for name, pg, live, want_sym in cases:
        got = _lab_vote_rows(pg, live)
        assert got[0]["symbol"] == want_sym, name
