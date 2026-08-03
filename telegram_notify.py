"""
Alertas opcionales vía Telegram Bot API (sin dependencias extra).

Configuración en `.env`:
  TELEGRAM_BOT_TOKEN=<token del bot de @BotFather>
  TELEGRAM_CHAT_ID=<tu chat id o id de grupo>
  TELEGRAM_NOTIFY_START=1   # opcional: avisar al conectar (default 1 si hay token+chat)
  TELEGRAM_NOTIFY_STOP=1    # opcional: avisar al detenerse (default 1)
  TELEGRAM_NOTIFY_TRADE_OPEN=1   # avisar al ejecutar una orden (entrada)
  TELEGRAM_NOTIFY_TRADE_CLOSE=1  # avisar al cerrar posición (SL/TP/manual, etc.)

Si faltan token o chat_id, todas las llamadas son no-op.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

_last_sent: dict[str, datetime] = {}


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip()


def telegram_configured() -> bool:
    return bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"))


def env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


def fetch_recent_chat_hints() -> tuple[bool, str, list[dict]]:
    """
    Llama a getUpdates y lista chats únicos que ya interactuaron con el bot.
    Solo requiere TELEGRAM_BOT_TOKEN (sirve para completar TELEGRAM_CHAT_ID).

    Returns:
        (True, "", list[{chat_id, type, label}]) o (False, error, []).
    """
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        return False, "Falta TELEGRAM_BOT_TOKEN en .env", []

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    timeout = float(_env("TELEGRAM_TIMEOUT", "12") or "12")
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=max(5.0, timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if not data.get("ok"):
            return False, str(data.get("description") or raw)[:400], []
        updates = data.get("result") or []
    except urllib.error.HTTPError as e:
        body_txt = (e.read() or b"")[:400].decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}: {body_txt}", []
    except Exception as e:
        return False, str(e)[:400], []

    by_id: dict[int, dict] = {}
    for u in updates:
        if not isinstance(u, dict):
            continue
        chat: Optional[dict] = None
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            inner = u.get(key)
            if isinstance(inner, dict):
                chat = inner.get("chat")
                if isinstance(chat, dict):
                    break
        if chat is None:
            cq = u.get("callback_query")
            if isinstance(cq, dict):
                msg = cq.get("message")
                if isinstance(msg, dict):
                    chat = msg.get("chat")
        if not isinstance(chat, dict):
            continue
        cid = chat.get("id")
        if cid is None:
            continue
        try:
            icid = int(cid)
        except (TypeError, ValueError):
            continue
        parts: list[str] = []
        for k in ("title", "username", "first_name", "last_name"):
            v = chat.get(k)
            if v:
                parts.append(str(v))
        ctype = str(chat.get("type") or "?")
        label = " · ".join(parts) if parts else ctype
        by_id[icid] = {"chat_id": icid, "type": ctype, "label": label}

    out = sorted(by_id.values(), key=lambda x: int(x["chat_id"]))
    return True, "", out


def send_message_now(text: str, *, timeout: Optional[float] = None) -> tuple[bool, str]:
    """
    Envía un mensaje de inmediato (sin anti-spam por dedup_key).
    Returns:
        (True, "") si Telegram respondió ok
        (False, reason) si falta config, error HTTP o respuesta no-ok
    """
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": str(text)[:4000],
        "disable_web_page_preview": True,
    }
    req_timeout = float(
        timeout if timeout is not None else (_env("TELEGRAM_TIMEOUT", "12") or "12")
    )
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=max(3.0, req_timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if not data.get("ok"):
            desc = str(data.get("description") or raw)[:400]
            logging.warning("Telegram sendMessage not ok: %s", raw[:500])
            return False, desc
        return True, ""
    except urllib.error.HTTPError as e:
        body_txt = (e.read() or b"")[:400].decode("utf-8", errors="replace")
        logging.warning("Telegram HTTPError: %s | body=%s", e, body_txt)
        return False, f"HTTP {e.code}: {body_txt}"
    except urllib.error.URLError as e:
        # DNS/offline/firewall: típico al apagar el bot sin red o api.telegram.org irresoluble.
        # No usar logging.exception: no es bug del bot, solo aviso informativo.
        reason = getattr(e, "reason", None)
        detail = str(reason) if reason is not None else str(e)
        logging.warning("Telegram send skipped (network/DNS): %s", detail[:400])
        return False, detail[:400]
    except TimeoutError as e:
        logging.warning("Telegram send timed out after %.1fs: %s", max(3.0, req_timeout), str(e)[:400])
        return False, str(e)[:400]
    except KeyboardInterrupt:
        # Evita traceback feo si el usuario pulsa Ctrl+C otra vez durante connect/read.
        logging.debug("Telegram send interrupted (KeyboardInterrupt).")
        return False, "KeyboardInterrupt"
    except Exception as e:
        logging.exception("Telegram send failed.")
        return False, str(e)[:400]


def notify(
    text: str,
    *,
    dedup_key: Optional[str] = None,
    min_interval_sec: float = 300.0,
    timeout: Optional[float] = None,
) -> None:
    """
    Envía un mensaje de texto. Si `dedup_key` está definido, no reenvía el mismo key
    antes de `min_interval_sec` (evita spam en reconexiones repetidas).
    """
    now = datetime.now()
    if dedup_key:
        prev = _last_sent.get(dedup_key)
        if prev is not None and (now - prev).total_seconds() < float(min_interval_sec):
            return

    ok, _err = send_message_now(text, timeout=timeout)
    if ok and dedup_key:
        _last_sent[dedup_key] = now
