"""Persist the kill-switch halt next to bot_state.json.

Fail-closed: a halt file that exists but cannot be read still blocks entries.
A human clears it by deleting the file or dropping ``kill_switch.clear`` next to it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from trading.domain.kill_switch import REASON_CORRUPT, halt_is_active

DEFAULT_PATH = Path("kill_switch.halt")
DEFAULT_CLEAR_PATH = Path("kill_switch.clear")


def load_halt(path: Path | str = DEFAULT_PATH) -> Optional[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
        return {"active": True, "reason": REASON_CORRUPT, "path": str(p)}
    if not isinstance(raw, dict):
        return {"active": True, "reason": REASON_CORRUPT, "path": str(p)}
    if not halt_is_active(raw):
        return None
    return raw


def save_halt(record: dict[str, Any], path: Path | str = DEFAULT_PATH) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def clear_halt(path: Path | str = DEFAULT_PATH) -> bool:
    """True if a halt file was removed."""
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def consume_clear_request(
    halt_path: Path | str = DEFAULT_PATH,
    clear_path: Path | str = DEFAULT_CLEAR_PATH,
) -> bool:
    """If the operator dropped kill_switch.clear, remove both files. True when a request was consumed."""
    c = Path(clear_path)
    if not c.exists():
        return False
    clear_halt(halt_path)
    try:
        c.unlink()
    except OSError:
        pass
    return True
