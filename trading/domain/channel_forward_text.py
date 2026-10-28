"""Turn a SantinoCripto NUEVA SENAL body into the short text we forward. Pure."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from trading.domain.channel_signal_parse import parse_signal_body

_KEEP = re.compile(r"^(par|direcci[oó]n|precio|tp|sl)\s*:", re.IGNORECASE)


def forward_nueva_senal_text(body: str) -> str | None:
    """Return Par / Direccion / Precio / TP / SL, or None if this is not that signal.

    Affiliate lines, score cards, videos and other channel posts are dropped.
    """
    signal = parse_signal_body(
        body,
        message_id=0,
        timestamp_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    if signal is None or signal.template != "nueva_senal":
        return None
    kept: list[str] = []
    keys: set[str] = set()
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not _KEEP.match(line):
            continue
        key = line.split(":", 1)[0].strip().lower()
        keys.add(key)
        kept.append(line)
    has_dir = "direccion" in keys or "dirección" in keys
    if not (has_dir and "precio" in keys and "tp" in keys and "sl" in keys):
        return None
    return "SantinoCripto\n" + "\n".join(kept)
