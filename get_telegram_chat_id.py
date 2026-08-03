"""
Lista posibles TELEGRAM_CHAT_ID usando getUpdates (solo hace falta el token en .env).

Uso:
  1) Poné TELEGRAM_BOT_TOKEN en .env (BotFather).
  2) En Telegram, abrí tu bot y enviá /start o cualquier mensaje.
  3) En la carpeta del repo: py .\\get_telegram_chat_id.py

Si la lista sale vacía, el bot aún no recibió mensajes o tenés webhook activo
(ver MANUAL.md sección Telegram).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

import telegram_notify  # noqa: E402


def main() -> int:
    ok, err, chats = telegram_notify.fetch_recent_chat_hints()
    if not ok:
        print(err)
        return 1
    if not chats:
        print("Todavía no hay updates para este bot.")
        print("1) En Telegram, abrí el bot y tocá Iniciar o enviá /start.")
        print("2) Volvé a ejecutar: py .\\get_telegram_chat_id.py")
        print("Alternativa: escribile a @userinfobot — tu Id suele ser el mismo chat_id en privado con tu bot.")
        return 0
    print("Copiá el número de chat_id a TELEGRAM_CHAT_ID en .env:\n")
    for c in chats:
        print(f"  {c['chat_id']}\t{c['type']}\t{c['label']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
