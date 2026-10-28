"""Parse a Telegram message into a manual-exception ticket. Pure: no I/O."""

from __future__ import annotations

_MAX_DIGITS = 15


def parse_ticket_command(text: str) -> tuple[str, int | None]:
    """Classify one inbound message.

    Returns:
    - ``("ignore", None)`` for empty text (no reply)
    - ``("ticket", n)`` when the message is only a positive ticket, or
      ``/except <ticket>`` / ``except <ticket>``
    - ``("usage", None)`` for any other text
    """
    raw = str(text or "").strip()
    if not raw:
        return "ignore", None

    body = raw
    first, _, rest = raw.partition(" ")
    cmd = first.split("@", 1)[0].lower()
    if cmd in ("/except", "except"):
        body = rest.strip()

    if _is_canonical_ticket(body):
        return "ticket", int(body)
    return "usage", None


def _is_canonical_ticket(token: str) -> bool:
    if not token.isdigit():
        return False
    if not 1 <= len(token) <= _MAX_DIGITS:
        return False
    if len(token) > 1 and token.startswith("0"):
        return False
    return int(token) > 0
