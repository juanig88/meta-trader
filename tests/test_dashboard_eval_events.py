"""Tests for dashboard EVAL log tail parser."""
from __future__ import annotations

from pathlib import Path

from flask_dashboard import build_parallel_tracks_view, parse_recent_eval_events


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
        ("limit_all", 10, ["EVAL_CYCLE", "EVAL_SKIP", "XAUUSD_PARALLEL_SIGNAL"]),
        ("limit_2", 2, ["EVAL_SKIP", "XAUUSD_PARALLEL_SIGNAL"]),
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
