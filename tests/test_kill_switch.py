from __future__ import annotations

import json

from trading.domain.kill_switch import (
    after_flatten,
    entries_blocked,
    flatten_now,
    flatten_retry,
    halt_enforced,
    halt_is_active,
    halt_record,
)
from trading.infrastructure.kill_switch_file import (
    clear_halt,
    consume_clear_request,
    load_halt,
    save_halt,
)


def test_kill_switch_table():
    cases = [
        ("default off", dict(flatten_enabled=False, daily_reached=True, halt_active=False), False),
        ("flag on first hit", dict(flatten_enabled=True, daily_reached=True, halt_active=False), True),
        ("already halted", dict(flatten_enabled=True, daily_reached=True, halt_active=True), False),
        ("day ok", dict(flatten_enabled=True, daily_reached=False, halt_active=False), False),
    ]
    for name, kwargs, want in cases:
        assert flatten_now(**kwargs) is want, name

    block_cases = [
        ("daily", True, False, True),
        ("halt file", False, True, True),
        ("clear", False, False, False),
        ("both", True, True, True),
    ]
    for name, daily, halt, want in block_cases:
        assert entries_blocked(daily_reached=daily, halt_active=halt) is want, name


def test_flatten_retry_and_enforced_table():
    pending = {"active": True, "flatten_pending": True}
    done = {"active": True, "flatten_pending": False}
    cases = [
        ("pending flag on", True, pending, True),
        ("pending flag off", False, pending, False),
        ("done", True, done, False),
        ("no record", True, None, False),
        ("inactive", True, {"active": False, "flatten_pending": True}, False),
    ]
    for name, enabled, rec, want in cases:
        assert flatten_retry(flatten_enabled=enabled, record=rec) is want, name
    assert halt_enforced(armed=True, record=done) is True
    assert halt_enforced(armed=False, record=done) is False
    assert halt_enforced(armed=True, record=None) is False


def test_halt_record_and_after_flatten():
    rec = halt_record(ts="t", pnl_today=-30.0, daily_loss_limit=-25.0)
    assert rec["active"] is True and rec["flatten_pending"] is True
    assert halt_is_active(rec) is True
    assert halt_is_active({}) is False
    assert halt_is_active(None) is False
    one = after_flatten(rec, n_closed=3, n_failed=1, ts="t1")
    assert one["flatten_pending"] is True and one["n_closed"] == 3 and one["n_failed_last"] == 1
    two = after_flatten(one, n_closed=1, n_failed=0, ts="t2")
    assert two["flatten_pending"] is False and two["n_closed"] == 4 and two["last_flatten_ts"] == "t2"
    assert rec["flatten_pending"] is True


def test_halt_file_fail_closed_table(tmp_path):
    p = tmp_path / "kill_switch.halt"
    assert load_halt(p) is None
    cases = [
        ("garbage", "{oops", True),
        ("list", "[1, 2]", True),
        ("inactive", json.dumps({"active": False}), False),
        ("active", json.dumps({"active": True, "reason": "daily_loss_limit"}), True),
    ]
    for name, body, want in cases:
        p.write_text(body, encoding="utf-8")
        assert halt_is_active(load_halt(p)) is want, name
    p.write_bytes(b"\xff\xfe\x00bad")
    assert halt_is_active(load_halt(p)) is True
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"active": True, "reason": "manual"}).encode("utf-8"))
    assert load_halt(p)["reason"] == "manual"


def test_save_clear_and_consume(tmp_path):
    halt = tmp_path / "kill_switch.halt"
    clear = tmp_path / "kill_switch.clear"
    save_halt({"active": True, "reason": "x"}, halt)
    assert load_halt(halt)["reason"] == "x"
    assert not (tmp_path / "kill_switch.halt.tmp").exists()
    assert consume_clear_request(halt, clear) is False
    clear.write_text("", encoding="utf-8")
    assert consume_clear_request(halt, clear) is True
    assert not halt.exists() and not clear.exists()
    assert clear_halt(halt) is False
