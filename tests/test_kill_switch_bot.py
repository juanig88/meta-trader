from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402

MAGIC = 260414


def _pos(ticket, *, magic=MAGIC, symbol="BTCUSD!", side="BUY"):
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        volume=0.01,
        type=bot_mt5.mt5.POSITION_TYPE_BUY if side == "BUY" else bot_mt5.mt5.POSITION_TYPE_SELL,
        magic=magic,
        time=1_790_000_000,
        profit=-0.4,
        swap=0.0,
        commission=0.0,
    )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    events: list[dict] = []
    telegrams: list[str] = []
    sends: list[dict] = []
    book = {"positions": [_pos(1), _pos(2, side="SELL"), _pos(3, magic=999)]}
    fail_tickets: set[int] = set()

    def order_send(req):
        sends.append(dict(req))
        ok = int(req.get("position", 0)) not in fail_tickets
        if ok:
            book["positions"] = [p for p in book["positions"] if p.ticket != int(req.get("position", 0))]
        return SimpleNamespace(retcode=bot_mt5.mt5.TRADE_RETCODE_DONE if ok else 10006, comment="")

    monkeypatch.setattr(bot_mt5, "KILL_SWITCH_HALT_PATH", tmp_path / "kill_switch.halt")
    monkeypatch.setattr(bot_mt5, "KILL_SWITCH_CLEAR_PATH", tmp_path / "kill_switch.clear")
    monkeypatch.setattr(bot_mt5, "_kill_switch_last_block_log", 0.0)
    monkeypatch.setattr(bot_mt5, "_kill_switch_disarmed_logged", False)
    monkeypatch.setattr(bot_mt5, "_log_event", lambda ev: events.append(dict(ev)))
    monkeypatch.setattr(bot_mt5.telegram_notify, "notify_kill_switch", lambda text: telegrams.append(text))
    monkeypatch.setattr(bot_mt5, "_position_is_managed_by_bot", lambda p, cfg: True)
    monkeypatch.setattr(bot_mt5, "ticket_is_manually_excepted", lambda t: False)
    monkeypatch.setattr(bot_mt5.mt5, "positions_get", lambda *a, **k: tuple(book["positions"]))
    monkeypatch.setattr(bot_mt5.mt5, "symbol_info_tick", lambda s: SimpleNamespace(bid=100.0, ask=100.1))
    monkeypatch.setattr(bot_mt5.mt5, "order_send", order_send)

    def cfg(**kw):
        base = dict(
            magic=MAGIC,
            kill_switch_flatten=False,
            kill_switch_armed=True,
            daily_loss_limit=-25.0,
            rotation_enabled=True,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    return SimpleNamespace(
        events=events,
        telegrams=telegrams,
        sends=sends,
        book=book,
        fail=fail_tickets,
        cfg=cfg,
        halt=tmp_path / "kill_switch.halt",
        clear=tmp_path / "kill_switch.clear",
    )


def _names(events):
    return [e["event"] for e in events]


def test_flag_off_keeps_current_behavior(harness):
    got = bot_mt5._kill_switch_step(harness.cfg(), reached=True, pnl_today=-30.0)
    assert got["halt_active"] is False and got["flatten_enabled"] is False
    assert harness.sends == [] and harness.events == [] and harness.telegrams == []
    assert not harness.halt.exists()


def test_flag_on_flattens_rotation_book_once_and_persists(harness):
    cfg = harness.cfg(kill_switch_flatten=True)
    got = bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-30.0)
    assert got["halt_active"] is True and got["reason"] == "daily_loss_limit"
    assert sorted(int(s["position"]) for s in harness.sends) == [1, 2]
    assert all(s["comment"] == "kill_switch" for s in harness.sends)
    assert [p.ticket for p in harness.book["positions"]] == [3]
    assert _names(harness.events) == ["KILL_SWITCH_HALT", "KILL_SWITCH_FLATTEN", "KILL_SWITCH_BLOCK"]
    flat = harness.events[1]
    assert flat["n_targets"] == 2 and flat["n_closed"] == 2 and flat["n_failed"] == 0
    assert flat["n_manual_skipped"] == 0 and flat["flatten_pending"] is False
    rec = json.loads(harness.halt.read_text(encoding="utf-8"))
    assert rec["active"] is True and rec["flatten_pending"] is False and rec["n_closed"] == 2
    assert len(harness.telegrams) == 1 and "KILL SWITCH" in harness.telegrams[0]

    again = bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-31.0)
    assert again["halt_active"] is True
    assert len(harness.sends) == 2 and len(harness.telegrams) == 1
    assert _names(harness.events).count("KILL_SWITCH_FLATTEN") == 1


def test_halt_survives_restart_and_blocks_open_trade(harness):
    bot_mt5._kill_switch_step(harness.cfg(kill_switch_flatten=True), reached=True, pnl_today=-30.0)
    harness.events.clear()
    harness.sends.clear()
    cfg = harness.cfg(kill_switch_flatten=False)
    got = bot_mt5._kill_switch_step(cfg, reached=False, pnl_today=0.0)
    assert got["halt_active"] is True
    assert bot_mt5.open_trade(cfg, "ETHUSD!", "BUY") is False
    assert harness.sends == []
    rejects = [e for e in harness.events if e["event"] == "ROTATION_OPEN_REJECT"]
    assert rejects and rejects[0]["reason"] == "kill_switch_halt"


def test_clear_file_removes_halt(harness):
    bot_mt5._kill_switch_step(harness.cfg(kill_switch_flatten=True), reached=True, pnl_today=-30.0)
    harness.clear.write_text("", encoding="utf-8")
    got = bot_mt5._kill_switch_step(harness.cfg(), reached=False, pnl_today=0.0)
    assert got["halt_active"] is False
    assert not harness.halt.exists() and not harness.clear.exists()
    assert "KILL_SWITCH_CLEAR" in _names(harness.events)
    assert bot_mt5._kill_switch_halt_active(harness.cfg()) is False


def test_disarmed_ignores_halt_and_never_flattens(harness):
    harness.halt.write_text(json.dumps({"active": True, "reason": "daily_loss_limit"}), encoding="utf-8")
    cfg = harness.cfg(kill_switch_flatten=True, kill_switch_armed=False)
    got = bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-40.0)
    assert got == {"armed": False, "flatten_enabled": True, "halt_active": False}
    assert harness.sends == []
    assert _names(harness.events) == ["KILL_SWITCH_DISARMED"]
    bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-40.0)
    assert _names(harness.events) == ["KILL_SWITCH_DISARMED"]
    assert harness.halt.exists()
    assert bot_mt5._kill_switch_halt_active(cfg) is False


def test_corrupt_halt_file_fails_closed(harness):
    harness.halt.write_text("{not json", encoding="utf-8")
    cfg = harness.cfg()
    assert bot_mt5._kill_switch_halt_active(cfg) is True
    got = bot_mt5._kill_switch_step(cfg, reached=False, pnl_today=0.0)
    assert got["halt_active"] is True and got["reason"] == "halt_file_unreadable"
    assert bot_mt5.open_trade(cfg, "ETHUSD!", "SELL") is False


def test_failed_close_retries_until_book_is_flat(harness):
    harness.fail.add(2)
    cfg = harness.cfg(kill_switch_flatten=True)
    bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-30.0)
    rec = json.loads(harness.halt.read_text(encoding="utf-8"))
    assert rec["flatten_pending"] is True and rec["n_failed_last"] == 1
    harness.fail.clear()
    bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-30.0)
    rec = json.loads(harness.halt.read_text(encoding="utf-8"))
    assert rec["flatten_pending"] is False and rec["n_closed"] == 2
    retries = [e for e in harness.events if e["event"] == "KILL_SWITCH_FLATTEN" and e["retry"]]
    assert len(retries) == 1 and retries[0]["n_closed"] == 1
    bot_mt5._kill_switch_step(cfg, reached=True, pnl_today=-30.0)
    assert len([e for e in harness.events if e["event"] == "KILL_SWITCH_FLATTEN"]) == 2


def test_unexpected_error_blocks_entries(harness, monkeypatch):
    import trading.infrastructure.kill_switch_file as ksf

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ksf, "load_halt", boom)
    got = bot_mt5._kill_switch_step(harness.cfg(), reached=False, pnl_today=0.0)
    assert got["halt_active"] is True and got["reason"] == "error"
    assert _names(harness.events) == ["KILL_SWITCH_ERROR"]
    assert bot_mt5._kill_switch_halt_active(harness.cfg()) is True


def test_manual_exception_ticket_is_left_open(harness, monkeypatch):
    monkeypatch.setattr(bot_mt5, "ticket_is_manually_excepted", lambda t: int(t) == 2)
    bot_mt5._kill_switch_step(harness.cfg(kill_switch_flatten=True), reached=True, pnl_today=-30.0)
    flat = next(e for e in harness.events if e["event"] == "KILL_SWITCH_FLATTEN")
    assert flat["n_closed"] == 1 and flat["n_manual_skipped"] == 1
    assert sorted(p.ticket for p in harness.book["positions"]) == [2, 3]
