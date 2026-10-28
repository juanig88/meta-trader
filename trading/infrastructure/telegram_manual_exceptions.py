"""Listen to the operator Telegram chat and append order tickets to manual_exceptions.txt.

Only TELEGRAM_CHAT_ID is accepted. Other chats are ignored.
A message must be the ticket alone (or /except <ticket>).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import telegram_notify
from trading.domain.telegram_ticket_command import parse_ticket_command
from trading.infrastructure.manual_whitelist_file import (
    append_manual_exception_ticket,
    default_manual_exceptions_path,
)

logger = logging.getLogger(__name__)

OFFSET_FILENAME = "telegram_update_offset.txt"
POLL_SECONDS = 2.0

USAGE_REPLY = "Mandá solo el número de la orden para exceptuarla. Ejemplo: 1940642770"
ADDED_REPLY = "Listo. {ticket} quedó en excepciones manuales. El bot y el watcher no le mueven SL/TP."
DUPLICATE_REPLY = "{ticket} ya estaba en excepciones manuales."
ERROR_REPLY = "No pude guardar {ticket} en manual_exceptions.txt."

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def next_update_offset(updates: list) -> int | None:
    """Return last update_id + 1, or None when there is nothing to confirm."""
    best: int | None = None
    for update in updates:
        if not isinstance(update, dict):
            continue
        uid = update.get("update_id")
        if isinstance(uid, bool) or not isinstance(uid, int):
            continue
        cand = uid + 1
        best = cand if best is None else max(best, cand)
    return best


def plan_poll(
    *,
    stored_offset: int | None,
    updates: list,
    boot_pending: bool,
) -> tuple[int | None, bool, bool]:
    """Decide whether this batch is a startup backlog or live commands.

    Returns (offset_to_store, should_process, boot_pending_after).
    The first batch seen when no offset was stored is acknowledged and not applied,
    so old chat messages are not replayed after a restart of a fresh install.
    """
    nxt = next_update_offset(updates)
    if boot_pending and stored_offset is None:
        if nxt is not None:
            return nxt, False, False
        return None, False, False
    if nxt is not None:
        return nxt, True, False
    return stored_offset, True, False


def handle_ticket_text(text: str, *, path: Path | None = None) -> str | None:
    """Turn one allowed-chat message into a reply, or None when there is nothing to say."""
    kind, ticket = parse_ticket_command(text)
    if kind == "ignore":
        return None
    if kind != "ticket" or ticket is None:
        return USAGE_REPLY
    status = append_manual_exception_ticket(ticket, path=path)
    if status == "added":
        return ADDED_REPLY.format(ticket=ticket)
    if status == "duplicate":
        return DUPLICATE_REPLY.format(ticket=ticket)
    return ERROR_REPLY.format(ticket=ticket)


def handle_update(update: dict, *, allowed_chat_id: int, path: Path | None = None) -> str | None:
    """Reply text for one update. None means do not send anything."""
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    try:
        chat_id = int(chat.get("id"))
    except (TypeError, ValueError):
        return None
    if chat_id != int(allowed_chat_id):
        logger.info("telegram manual exceptions: ignored chat_id %s", chat_id)
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    return handle_ticket_text(text, path=path)


def apply_updates(
    updates: list,
    *,
    allowed_chat_id: int,
    path: Path | None = None,
) -> list[str]:
    replies: list[str] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        reply = handle_update(update, allowed_chat_id=allowed_chat_id, path=path)
        if reply:
            replies.append(reply)
    return replies


def default_offset_path() -> Path:
    return default_manual_exceptions_path().parent / OFFSET_FILENAME


def load_offset(path: Path | None = None) -> int | None:
    target = Path(path) if path is not None else default_offset_path()
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def save_offset(offset: int, path: Path | None = None) -> None:
    target = Path(path) if path is not None else default_offset_path()
    try:
        target.write_text(f"{int(offset)}\n", encoding="utf-8")
    except OSError:
        logger.warning("telegram manual exceptions: could not save offset to %s", target, exc_info=True)


def poll_once(
    *,
    stored_offset: int | None,
    boot_pending: bool,
    allowed_chat_id: int,
    exceptions_path: Path | None = None,
    offset_path: Path | None = None,
    fetch=None,
    send=None,
) -> tuple[int | None, bool]:
    """One short poll. Returns (stored_offset, boot_pending) for the next call."""
    fetcher = fetch if fetch is not None else telegram_notify.fetch_updates
    sender = send if send is not None else telegram_notify.send_message_now
    ok, err, updates = fetcher(offset=stored_offset)
    if not ok:
        logger.warning("telegram manual exceptions: %s", err)
        return stored_offset, boot_pending

    new_offset, should_process, boot_after = plan_poll(
        stored_offset=stored_offset,
        updates=updates,
        boot_pending=boot_pending,
    )
    if boot_pending and stored_offset is None and not should_process and updates:
        logger.info(
            "telegram manual exceptions: skipped %d pending update(s) from before this start",
            len(updates),
        )
    if should_process:
        for reply in apply_updates(updates, allowed_chat_id=allowed_chat_id, path=exceptions_path):
            sender(reply)
    if new_offset is not None and new_offset != stored_offset:
        save_offset(new_offset, path=offset_path)
    return new_offset, boot_after


def start_listener() -> None:
    """Daemon thread. Safe to call once from bot startup."""
    global _thread
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_MANUAL_EXCEPTIONS", True):
        logger.info("telegram manual exceptions: disabled (TELEGRAM_MANUAL_EXCEPTIONS=0)")
        return
    chat_raw = os.getenv("TELEGRAM_CHAT_ID")
    try:
        allowed = int(str(chat_raw).strip())
    except (TypeError, ValueError):
        logger.warning("telegram manual exceptions: TELEGRAM_CHAT_ID is not an integer")
        return

    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_loop,
            name="telegram-manual-exceptions",
            args=(allowed,),
            daemon=True,
        )
        _thread.start()
    logger.info(
        "telegram manual exceptions: listening. Send a position ticket to add it to %s",
        default_manual_exceptions_path(),
    )


def _loop(allowed_chat_id: int) -> None:
    offset_path = default_offset_path()
    stored = load_offset(offset_path)
    boot_pending = stored is None
    while True:
        try:
            stored, boot_pending = poll_once(
                stored_offset=stored,
                boot_pending=boot_pending,
                allowed_chat_id=allowed_chat_id,
                offset_path=offset_path,
            )
        except Exception:
            logger.exception("telegram manual exceptions: poll failed")
        time.sleep(POLL_SECONDS)
