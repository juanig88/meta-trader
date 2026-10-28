from __future__ import annotations

from pathlib import Path

from trading.domain.telegram_ticket_command import parse_ticket_command
from trading.infrastructure.telegram_manual_exceptions import (
    apply_updates,
    handle_ticket_text,
    plan_poll,
    poll_once,
)


def test_parse_ticket_command_table():
    cases = [
        ("bare ticket", "1940642770", ("ticket", 1940642770)),
        ("whitespace", "  1940642770\n", ("ticket", 1940642770)),
        ("except command", "/except 1940734365", ("ticket", 1940734365)),
        ("except with bot suffix", "/except@MetaBot 15", ("ticket", 15)),
        ("word except", "except 99", ("ticket", 99)),
        ("empty", "", ("ignore", None)),
        ("blank", "   \n", ("ignore", None)),
        ("prose", "hola", ("usage", None)),
        ("two tickets", "11 22", ("usage", None)),
        ("ticket plus words", "1940642770 btc", ("usage", None)),
        ("zero", "0", ("usage", None)),
        ("negative", "-5", ("usage", None)),
        ("decimal", "12.5", ("usage", None)),
        ("leading zero", "0123", ("usage", None)),
        ("too many digits", "1" * 16, ("usage", None)),
        ("shell junk", "1940642770; rm -rf", ("usage", None)),
    ]
    for name, text, want in cases:
        assert parse_ticket_command(text) == want, name


def test_plan_poll_table():
    pending = [{"update_id": 4}, {"update_id": 9}]
    cases = [
        ("fresh start skips backlog", None, pending, True, (10, False, False)),
        ("fresh start with nothing waits", None, [], True, (None, False, False)),
        ("live batch is processed", 10, [{"update_id": 10}], False, (11, True, False)),
        ("empty live poll keeps offset", 11, [], False, (11, True, False)),
    ]
    for name, stored, updates, boot, want in cases:
        assert plan_poll(stored_offset=stored, updates=updates, boot_pending=boot) == want, name


def test_handle_ticket_text_appends_once(tmp_path: Path):
    target = tmp_path / "manual_exceptions.txt"
    added = handle_ticket_text("1940642770", path=target)
    again = handle_ticket_text("1940642770", path=target)
    usage = handle_ticket_text("no es un ticket", path=target)
    assert added is not None and "1940642770" in added
    assert again is not None and "ya estaba" in again
    assert usage is not None and "solo el número" in usage
    body = target.read_text(encoding="utf-8")
    assert body.count("1940642770") == 1


def test_apply_updates_ignores_other_chat(tmp_path: Path):
    target = tmp_path / "manual_exceptions.txt"
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "1940642770"}},
        {"update_id": 2, "message": {"chat": {"id": 7}, "text": "1940734365"}},
        {"update_id": 3, "message": {"chat": {"id": 7}, "text": ""}},
    ]
    replies = apply_updates(updates, allowed_chat_id=7, path=target)
    assert replies == [
        "Listo. 1940734365 quedó en excepciones manuales. El bot y el watcher no le mueven SL/TP."
    ]
    assert "1940642770" not in target.read_text(encoding="utf-8")
    assert "1940734365" in target.read_text(encoding="utf-8")


def test_poll_once_skips_backlog_then_appends(tmp_path: Path):
    exceptions = tmp_path / "manual_exceptions.txt"
    offset_file = tmp_path / "telegram_update_offset.txt"
    sent: list[str] = []
    batches = [
        [{"update_id": 1, "message": {"chat": {"id": 7}, "text": "111"}}],
        [{"update_id": 2, "message": {"chat": {"id": 7}, "text": "222"}}],
    ]

    def fetch(*, offset):
        assert offset is None or offset == 2
        return True, "", batches.pop(0)

    stored, boot = poll_once(
        stored_offset=None,
        boot_pending=True,
        allowed_chat_id=7,
        exceptions_path=exceptions,
        offset_path=offset_file,
        fetch=fetch,
        send=lambda text, **kwargs: sent.append(text) or (True, ""),
    )
    assert stored == 2
    assert boot is False
    assert sent == []
    assert not exceptions.exists()

    stored, boot = poll_once(
        stored_offset=stored,
        boot_pending=boot,
        allowed_chat_id=7,
        exceptions_path=exceptions,
        offset_path=offset_file,
        fetch=fetch,
        send=lambda text, **kwargs: sent.append(text) or (True, ""),
    )
    assert stored == 3
    assert boot is False
    assert sent and "222" in sent[0]
    assert "111" not in exceptions.read_text(encoding="utf-8")
    assert "222" in exceptions.read_text(encoding="utf-8")
    assert offset_file.read_text(encoding="utf-8").strip() == "3"
