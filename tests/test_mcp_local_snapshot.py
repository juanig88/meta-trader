from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from trading.infrastructure.mcp.local_snapshot import (
    bot_snapshot,
    clamp_eval_limit,
    compute_health,
    parse_recent_eval_events,
    redact,
    resolve_root,
)


def test_redact_table():
    cases = [
        ("nested password", {"mt5": {"password": "x", "ok": 1}}, {"mt5": {"password": "[redacted]", "ok": 1}}),
        ("api_key", {"api_key": "k", "symbol": "BTCUSD"}, {"api_key": "[redacted]", "symbol": "BTCUSD"}),
        ("list", [{"token": "t"}], [{"token": "[redacted]"}]),
        ("plain", {"running": True}, {"running": True}),
        ("empty dict", {}, {}),
    ]
    for name, src, want in cases:
        assert redact(src) == want, name


def test_clamp_eval_limit_table():
    cases = [
        ("default none", None, 40),
        ("ok", 10, 10),
        ("zero", 0, 1),
        ("negative", -3, 1),
        ("over max", 500, 100),
        ("string int", "7", 7),
        ("junk", "nope", 40),
    ]
    for name, value, want in cases:
        assert clamp_eval_limit(value) == want, name


def test_resolve_root_falls_back_when_missing(tmp_path: Path):
    fallback = tmp_path / "fb"
    fallback.mkdir()
    assert resolve_root(None, fallback=fallback) == fallback.resolve()
    assert resolve_root("   ", fallback=fallback) == fallback.resolve()
    missing = tmp_path / "nope"
    assert resolve_root(str(missing), fallback=fallback) == fallback.resolve()
    real = tmp_path / "ws"
    real.mkdir()
    assert resolve_root(str(real), fallback=fallback) == real.resolve()


def test_compute_health_stale_and_fresh():
    fresh_ts = datetime.now().isoformat(timespec="seconds")
    stale_ts = (datetime.now() - timedelta(seconds=400)).isoformat(timespec="seconds")
    cases = [
        ("empty", {}, False, False),
        (
            "fresh running",
            {"running": True, "ts": fresh_ts, "poll_seconds": 30, "pid": 12, "mode": "LIVE"},
            True,
            False,
        ),
        (
            "stale running",
            {"running": True, "ts": stale_ts, "poll_seconds": 30, "pid": 12},
            True,
            True,
        ),
        (
            "stopped not stale",
            {"running": False, "ts": stale_ts, "poll_seconds": 30},
            False,
            False,
        ),
        ("bad pid", {"running": False, "pid": "nope"}, False, False),
    ]
    for name, state, want_running, want_stale in cases:
        got = compute_health(state)
        assert got["running"] is want_running, name
        assert got["heartbeat_stale"] is want_stale, name


def test_parse_recent_eval_events_and_snapshot(tmp_path: Path):
    log = tmp_path / "trades.log"
    log.write_text(
        "\n".join(
            [
                "2026-08-09 10:00:00 | INFO | not json",
                '2026-08-09 10:00:01 | INFO | {"event":"EVAL_CYCLE","symbol":"BTCUSD"}',
                '2026-08-09 10:00:02 | INFO | {"event":"NOPE"}',
                '2026-08-09 10:00:03 | INFO | {"event":"EVAL_SKIP","symbol":"ETHUSD","api_key":"leak"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_recent_eval_events(log, limit=10)
    assert [e["event"] for e in events] == ["EVAL_CYCLE", "EVAL_SKIP"]

    (tmp_path / "bot_state.json").write_text(
        '{"running": false, "password": "nope", "mode": "STOPPED"}',
        encoding="utf-8",
    )
    snap = bot_snapshot(tmp_path)
    assert snap["state"]["password"] == "[redacted]"
    assert snap["state"]["mode"] == "STOPPED"
    assert snap["state_exists"] is True


def test_parse_recent_eval_events_missing_file(tmp_path: Path):
    assert parse_recent_eval_events(tmp_path / "missing.log") == []
