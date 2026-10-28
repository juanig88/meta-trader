from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from trading.application.daily_report import (  # noqa: E402
    broker_closes_for_day,
    build_daily_report,
    format_daily_telegram,
    log_closes_for_day,
    swing_for_local_day,
)
from trading.application.four_five_lab import (  # noqa: E402
    four_five_rows,
    four_five_rows_from_events,
    tail_events,
)

MAGIC = 260414


def _line(ev: dict) -> str:
    return f"2026-10-03 12:00:00 | INFO | {json.dumps(ev)}"


def _fire(ts, sym, *, action="LIVE", skip="", direction="BUY"):
    return {
        "event": "ENSEMBLE_VOTE", "ts": ts, "symbol": sym, "direction": direction, "agree": 4,
        "fired": True, "action": action, "skip_reason": skip,
        "alpha": {"code": "alpha_igual", "score": 0.2, "would_avoid": False},
        "murphy": {"tag_m15": "retest_broken", "would_veto": False},
        "state": {"tape_imbalance": 0.25, "spread_usd": 0.01},
    }


def test_four_five_join_table():
    events = [
        _fire("2026-10-03T10:00:00", "AAA"),
        {"event": "TRADE_OPEN", "ts": "2026-10-03T10:00:02", "symbol": "AAA", "magic": MAGIC, "order": 11},
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-03T10:05:05", "ticket": 11, "profit": -0.02, "why_closed": "stale_loss"},
        {"event": "TRADE_CLOSE", "ts": "2026-10-03T10:05:06", "position_id": 11, "net": -0.021, "close_how": "stale_loss"},
        _fire("2026-10-03T10:01:00", "BBB"),
        {"event": "ROTATION_OPEN_REJECT", "ts": "2026-10-03T10:01:01", "symbol": "BBB", "reason": "spread_caro"},
        _fire("2026-10-03T10:02:00", "CCC"),
        {"event": "TRADE_OPEN", "ts": "2026-10-03T10:02:01", "symbol": "CCC", "magic": MAGIC, "order": 33},
        _fire("2026-10-03T10:03:00", "DDD", action="SKIP", skip="sin_pullback"),
        _fire("2026-10-03T10:04:00", "EEE"),
        {"event": "TRADE_OPEN", "ts": "2026-10-03T10:09:00", "symbol": "EEE", "magic": MAGIC, "order": 55},
        _fire("2026-10-03T10:05:00", "FFF"),
        {"event": "TRADE_OPEN", "ts": "2026-10-03T10:05:01", "symbol": "FFF", "magic": 999, "order": 66},
        {"event": "ENSEMBLE_VOTE", "ts": "2026-10-03T10:06:00", "symbol": "GGG", "fired": False},
    ]
    rows = four_five_rows_from_events(events, limit=50)
    by = {r["symbol"]: r for r in rows}
    assert [r["symbol"] for r in rows] == ["FFF", "EEE", "DDD", "CCC", "BBB", "AAA"]
    cases = [
        ("AAA", "closed", "", 11, -0.021, "stale_loss"),
        ("BBB", "reject", "spread_caro", None, None, ""),
        ("CCC", "open", "", 33, None, ""),
        ("DDD", "skip", "sin_pullback", None, None, ""),
        ("EEE", "pending", "", None, None, ""),
        ("FFF", "pending", "", None, None, ""),
    ]
    for sym, action, reason, ticket, net, how in cases:
        r = by[sym]
        assert (r["action"], r["reason"], r["ticket"], r["net"], r["close_how"]) == (action, reason, ticket, net, how), sym
    assert by["AAA"]["tape_imbalance"] == 0.25 and by["AAA"]["alpha_score"] == 0.2
    assert len(four_five_rows_from_events(events, limit=2)) == 2
    assert four_five_rows_from_events([]) == []


def test_tail_events_reads_only_tail_and_filters(tmp_path):
    log = tmp_path / "trades.log"
    old = [_line(_fire("2026-10-03T09:00:00", f"OLD{i}")) for i in range(200)]
    new = [
        _line(_fire("2026-10-03T11:00:00", "NEW")),
        _line({"event": "ENSEMBLE_VOTE", "ts": "2026-10-03T11:00:00", "symbol": "X", "fired": False}),
        _line({"event": "ROTATION_BOOK", "ts": "2026-10-03T11:00:00"}),
        "garbage line {not json",
    ]
    log.write_text("\n".join(old + new) + "\n", encoding="utf-8")
    tail = sum(len(x) + 1 for x in new) + 50
    got = tail_events(log, max_bytes=tail)
    assert [e["symbol"] for e in got] == ["NEW"]
    assert len(tail_events(log)) == 201
    assert tail_events(tmp_path / "missing.log") == []
    assert four_five_rows(log, max_bytes=tail)[0]["symbol"] == "NEW"


def test_log_closes_dedupe_and_day():
    events = [
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-02T10:00:00", "ticket": 1, "profit": -0.1},
        {"event": "TRADE_CLOSE", "ts": "2026-10-02T10:00:01", "position_id": 1, "net": -0.12},
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-02T10:00:02", "ticket": 1, "profit": 5.0},
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-02T11:00:00", "ticket": 2, "profit": 0.05},
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-02T11:00:00", "ticket": 3, "profit": 0.05, "closed": False},
        {"event": "ROTATION_STALE_CLOSE", "ts": "2026-10-03T00:00:01", "ticket": 4, "profit": -1.0},
    ]
    assert log_closes_for_day(events, "2026-10-02") == {1: -0.12, 2: 0.05}


def test_broker_closes_use_local_day():
    def srv(y, m, d, h, mi=0):
        return int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp())

    positions = {
        1: {"closed": True, "net": -0.2, "close_time": srv(2026, 10, 3, 2, 30)},
        2: {"closed": True, "net": 0.1, "close_time": srv(2026, 10, 3, 3, 30)},
        3: {"closed": False, "net": None, "close_time": None},
    }
    assert broker_closes_for_day(positions, "2026-10-02") == {1: -0.2}
    assert broker_closes_for_day(positions, "2026-10-03") == {2: 0.1}


def test_build_daily_report_broker_wins_and_counts(tmp_path):
    day = "2026-10-02"
    events = [
        {"event": "ENSEMBLE_SIGNAL", "ts": f"{day}T10:00:00", "symbol": "A"},
        {"event": "TRADE_OPEN", "ts": f"{day}T10:00:01", "symbol": "A", "magic": MAGIC, "order": 1},
        {"event": "ROTATION_OPEN_REJECT", "ts": f"{day}T10:01:00", "symbol": "B", "reason": "spread_caro"},
        {"event": "ROTATION_OPEN_REJECT", "ts": f"{day}T10:02:00", "symbol": "C", "reason": "spread_caro"},
        {"event": "ROTATION_STALE_CLOSE", "ts": f"{day}T10:05:00", "ticket": 1, "profit": -0.1},
        {"event": "KILL_SWITCH_BLOCK", "ts": f"{day}T11:00:00"},
        {"event": "TRADE_OPEN", "ts": "2026-10-01T23:59:59", "symbol": "Z", "magic": MAGIC, "order": 9},
    ]
    log = tmp_path / "trades.log"
    log.write_text("\n".join(_line(e) for e in events) + "\n", encoding="utf-8")
    srv = int(datetime(2026, 10, 2, 13, 5, tzinfo=timezone.utc).timestamp())
    broker = {
        1: {"closed": True, "net": -0.11, "close_time": srv},
        2: {"closed": True, "net": 0.3, "close_time": srv},
    }
    calib = {"n_matched": 10, "sent_consensus": {"questions": [
        {"name": "q1", "n": 5, "brier": 0.4, "brier_skill": -1.0, "sufficient": False, "gate_discussable": False},
        {"name": "quint", "n": 5, "quintiles": []},
    ]}}
    rep = build_daily_report([log], day=day, broker_positions=broker, calibration=calib)
    assert rep["pnl_source"] == "broker" and rep["live_gate"] is False
    assert rep["n_signals"] == 1 and rep["n_opens"] == 1
    assert rep["rejects"] == {"spread_caro": 2}
    assert rep["kill_switch"] == {"KILL_SWITCH_BLOCK": 1}
    c = rep["closes"]
    assert c["n"] == 2 and c["pnl"] == 0.19 and c["wins"] == 1 and c["losses"] == 1
    assert c["profit_factor"] == round(0.3 / 0.11, 4) and c["largest_loss"] == -0.11
    assert rep["log_closes"]["n"] == 1 and rep["log_closes"]["pnl"] == -0.1
    assert rep["log_vs_broker"] == {"n_broker": 2, "n_log": 1, "n_both": 1, "sum_log_on_both": -0.1, "sum_broker_on_both": -0.11}
    assert set(rep["calibration"]["questions"]) == {"q1"}
    text = format_daily_telegram(rep)
    assert "2026-10-02 (broker)" in text and "spread_caro 2" in text and "KILL_SWITCH_BLOCK 1" in text

    log_only = build_daily_report([log], day=day)
    assert log_only["pnl_source"] == "log" and log_only["closes"]["pnl"] == -0.1
    assert log_only["log_vs_broker"] is None and log_only["calibration"] is None
    empty = build_daily_report([tmp_path / "none.log"], day=day)
    assert empty["closes"]["n"] == 0 and empty["closes"]["win_rate"] is None
    assert "WR n/a" in format_daily_telegram(empty)


def test_swing_for_local_day_and_telegram():
    stale = {
        "event": "SWING_PLAN", "ts": "2026-10-09T00:10:01+00:00", "day": "2026-10-09",
        "entries": [], "exits": [], "skips": [], "stale_bars": ["A"] * 70, "universe": 79,
        "equity": 1.0, "block_reason": "",
    }
    decided = {
        "event": "SWING_PLAN", "ts": "2026-10-09T00:11:05+00:00", "day": "2026-10-09",
        "entries": ["BTCUSD!"], "exits": [["ETHUSD!", "bajo_sma200"]],
        "skips": [["X", "swap_caro"], ["Y", "swap_caro"]], "stale_bars": [], "universe": 79,
        "equity": 9918.1, "block_reason": "",
    }
    events = [
        stale,
        {"event": "SWING_GATE", "ts": "2026-10-09T00:11:05+00:00", "block_reason": "", "gate": {"reason": "ok"}},
        decided,
        {"event": "SWING_OPEN", "ts": "2026-10-09T00:11:06+00:00", "symbol": "BTCUSD!"},
        {"event": "SWING_OPEN_FAIL", "ts": "2026-10-09T00:11:07+00:00", "symbol": "NOPE"},
        {"event": "SWING_CLOSE", "ts": "2026-10-09T00:11:08+00:00", "symbol": "ETHUSD!"},
        {"event": "SWING_PLAN", "ts": "2026-10-08T00:11:00+00:00", "day": "2026-10-08",
         "entries": ["OLD"], "exits": [], "skips": [], "stale_bars": [], "universe": 79, "equity": 1},
    ]
    got = swing_for_local_day(events, "2026-10-08")
    assert got["ran"] is True and got["stale_only"] is False
    assert got["swing_day"] == "2026-10-09"
    assert (got["entries"], got["exits"], got["opened"], got["closed"], got["open_fails"]) == (1, 1, 1, 1, 1)
    assert got["skips"] == {"swap_caro": 2} and got["equity"] == 9918.1 and got["gate_reason"] == "ok"
    text = format_daily_telegram({
        "day": "2026-10-08", "pnl_source": "broker", "n_signals": 0, "n_opens": 0,
        "closes": {"n": 0, "pnl": 0, "win_rate": None, "profit_factor": None, "largest_loss": None, "largest_win": None},
        "rejects": {}, "kill_switch": {}, "rotation_halt": "rotacion_reemplazada_por_swing", "swing": got,
    })
    assert "rotation halt: rotacion_reemplazada_por_swing" in text
    assert "swing 2026-10-09 entradas 1 salidas 1 abiertas 1 cerradas 1 equity 9918.1" in text
    assert "fallos open 1 close 0" in text and "skips swap_caro 2" in text and "stale 0/79" in text

    cases = [
        ("empty", [], False, False, "swing: sin decision"),
        ("other local day", [{
            "event": "SWING_PLAN", "ts": "2026-10-07T21:00:00+00:00", "day": "2026-10-07",
            "entries": ["Z"], "exits": [], "skips": [], "stale_bars": [], "universe": 10, "equity": 1,
        }], False, False, "swing: sin decision"),
        ("stale only", [{
            "event": "SWING_PLAN", "ts": "2026-10-09T00:10:00+00:00", "day": "2026-10-09",
            "entries": ["A"], "exits": [], "skips": [], "stale_bars": ["x"] * 8, "universe": 10, "equity": 1,
        }], False, True, "swing: velas viejas, sin decision"),
        ("bad ts", [{
            "event": "SWING_PLAN", "ts": "nope", "day": "2026-10-08",
            "entries": ["A"], "exits": [], "skips": [], "stale_bars": [], "universe": 1, "equity": 1,
        }], False, False, "swing: sin decision"),
    ]
    for name, evs, ran, stale_only, snippet in cases:
        summary = swing_for_local_day(evs, "2026-10-08")
        assert (summary["ran"], summary["stale_only"]) == (ran, stale_only), name
        shown = format_daily_telegram({
            "day": "2026-10-08", "pnl_source": "log", "n_signals": 0, "n_opens": 0,
            "closes": {"n": 0, "pnl": 0, "win_rate": None, "profit_factor": None, "largest_loss": None, "largest_win": None},
            "rejects": {}, "kill_switch": {}, "swing": summary,
        })
        assert snippet in shown, name
        assert "rotation halt:" not in shown


def test_telegram_flags_default_off(monkeypatch):
    import telegram_notify

    sent: list[str] = []
    monkeypatch.setattr(telegram_notify, "send_message_now", lambda text, **k: (sent.append(text), (True, ""))[1])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("TELEGRAM_NOTIFY_KILL_SWITCH", raising=False)
    monkeypatch.delenv("TELEGRAM_NOTIFY_ERROR", raising=False)
    assert telegram_notify.notify_kill_switch("x") == (False, "disabled")
    telegram_notify.notify_error("y")
    assert sent == []
    monkeypatch.setenv("TELEGRAM_NOTIFY_KILL_SWITCH", "1")
    monkeypatch.setenv("TELEGRAM_NOTIFY_ERROR", "1")
    monkeypatch.setattr(telegram_notify, "_last_sent", {})
    assert telegram_notify.notify_kill_switch("kill") == (True, "")
    telegram_notify.notify_error("err1")
    telegram_notify.notify_error("err2")
    assert sent == ["kill", "err1"]


def test_lab_page_renders_four_five_table(tmp_path, monkeypatch):
    import flask_dashboard
    import trading.infrastructure.persistence.ensemble_store as es

    log = tmp_path / "trades.log"
    events = [
        _fire("2026-10-03T10:00:00", "AAA"),
        {"event": "TRADE_OPEN", "ts": "2026-10-03T10:00:02", "symbol": "AAA", "magic": MAGIC, "order": 11},
        {"event": "TRADE_CLOSE", "ts": "2026-10-03T10:05:06", "position_id": 11, "net": -0.021, "close_how": "stale_loss"},
        _fire("2026-10-03T10:03:00", "DDD", action="SKIP", skip="sin_pullback"),
    ]
    log.write_text("\n".join(_line(e) for e in events) + "\n", encoding="utf-8")
    monkeypatch.setattr(flask_dashboard, "TRADES_LOG_PATH", log)
    monkeypatch.setattr(
        flask_dashboard,
        "get_bot_state",
        lambda: {"rotation_enabled": True, "kill_switch": {"halt_active": True, "reason": "daily_loss_limit", "since": "t0"}},
    )
    monkeypatch.setattr(es, "fetch_latest_ensemble", lambda *a, **k: {})
    resp = flask_dashboard.app.test_client().get("/lab")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "4/5 recientes" in html and "AAA" in html and "stale_loss" in html and "-0.0210" in html
    assert "sin_pullback · 1" in html
    assert "Kill switch: halt activo" in html

    monkeypatch.setattr(flask_dashboard, "TRADES_LOG_PATH", tmp_path / "missing.log")
    monkeypatch.setattr(flask_dashboard, "get_bot_state", lambda: {"rotation_enabled": True})
    html2 = flask_dashboard.app.test_client().get("/lab").get_data(as_text=True)
    assert "Ningún 4/5 enviado" in html2 and "Kill switch: halt activo" not in html2
