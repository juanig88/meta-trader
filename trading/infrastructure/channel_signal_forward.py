"""Forward new SantinoCripto NUEVA SENAL posts to the operator bot chat.

Uses the existing Telethon user session (sesion_exportar). Does not replay history.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path

import telegram_notify
from trading.domain.channel_forward_text import forward_nueva_senal_text

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL = "t.me/SantinoCripto"
SESSION_NAME = "sesion_exportar"

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_seen_ids: set[int] = set()


def channel_target() -> str:
    raw = (os.getenv("TELEGRAM_EXPORT_CHANNEL") or "").strip()
    return raw or DEFAULT_CHANNEL


def export_target_candidates(raw: str) -> list[str | int]:
    """Username/URL stay strings; ``-100…`` channel ids become ints Telethon can try."""
    s = (raw or "").strip() or DEFAULT_CHANNEL
    if s.lstrip("-").isdigit():
        n = int(s)
        out: list[str | int] = [n]
        marked = str(n)
        if marked.startswith("-100") and len(marked) > 4:
            inner = int(marked[4:])
            if inner > 0:
                out.append(inner)
        return out
    return [s]


def entity_id_aliases(entity_id: int) -> set[int]:
    eid = int(entity_id)
    out = {eid, -abs(eid)}
    text = str(eid)
    if text.startswith("-100") and len(text) > 4:
        out.add(int(text[4:]))
    elif eid > 0:
        out.add(int(f"-100{eid}"))
    return out


def session_path() -> Path:
    return Path(f"{SESSION_NAME}.session")


def start_channel_forwarder() -> None:
    """Daemon thread. Safe to call once from bot startup."""
    global _thread
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_FORWARD_CHANNEL", True):
        logger.info("channel forward: disabled (TELEGRAM_FORWARD_CHANNEL=0)")
        return
    if not session_path().exists():
        logger.warning(
            "channel forward: falta %s. Sin esa sesión no se puede leer SantinoCripto.",
            session_path(),
        )
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_loop,
            name="telegram-channel-forward",
            daemon=True,
        )
        _thread.start()


def _loop() -> None:
    try:
        asyncio.run(_listen())
    except Exception:
        logger.exception("channel forward: listener stopped")


async def _resolve_entity(client, target: str):
    last_err: Exception | None = None
    for cand in export_target_candidates(target):
        try:
            return await client.get_entity(cand)
        except (ValueError, TypeError) as exc:
            last_err = exc
    want: set[int] = set()
    for cand in export_target_candidates(target):
        if isinstance(cand, int):
            want |= entity_id_aliases(cand)
    if want:
        async for dialog in client.iter_dialogs():
            eid = int(getattr(getattr(dialog, "entity", None), "id", 0) or 0)
            if eid and (eid in want or entity_id_aliases(eid) & want):
                return dialog.entity
    logger.warning(
        "channel forward: no se encuentra %s (%s). "
        "La sesión no tiene ese canal. Usá TELEGRAM_EXPORT_CHANNEL=t.me/SantinoCripto "
        "o TELEGRAM_FORWARD_CHANNEL=0.",
        target,
        last_err or "sin entidad",
    )
    return None


async def _listen() -> None:
    try:
        from telethon import TelegramClient, events
    except ImportError:
        logger.warning("channel forward: telethon no está instalado")
        return

    api_id_raw = (os.getenv("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    if not api_id_raw.isdigit() or not api_hash:
        logger.warning("channel forward: faltan TELEGRAM_API_ID o TELEGRAM_API_HASH")
        return

    target = channel_target()
    client = TelegramClient(SESSION_NAME, int(api_id_raw), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            logger.warning("channel forward: la sesión de Telethon no está autorizada")
            return
        entity = await _resolve_entity(client, target)
        if entity is None:
            return

        @client.on(events.NewMessage(chats=entity))
        async def _on_message(event) -> None:
            message = getattr(event, "message", None)
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id and message_id in _seen_ids:
                return
            if message_id:
                _seen_ids.add(message_id)
            body = str(getattr(event, "raw_text", None) or getattr(message, "message", "") or "")
            text = forward_nueva_senal_text(body)
            if not text:
                return
            ok, err = telegram_notify.send_message_now(text)
            if ok:
                logger.info("channel forward: sent message %s", message_id or "?")
            else:
                logger.warning("channel forward: send failed for %s: %s", message_id or "?", err)

        logger.info("channel forward: listening for NUEVA SENAL on %s", target)
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
