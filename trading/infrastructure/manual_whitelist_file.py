"""Load ``manual_exceptions.txt`` from disk. Never raises into the trading loop."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from trading.domain.manual_whitelist import parse_manual_exception_tickets

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "manual_exceptions.txt"
ENV_PATH = "BOT_MANUAL_EXCEPTIONS_FILE"
REPO_ROOT = Path(__file__).resolve().parents[2]

_FILE_HEADER = (
    "# One MT5 position ticket per line.\n"
    "# Watcher and bot will not move SL/TP (or bank-close) these tickets.\n"
    "# Lines starting with # are comments. Inline comments are allowed.\n"
    "#\n"
    "# 123456789\n"
    "# 987654321  # XAU opened by hand\n"
)

_RETRY_ATTEMPTS = 3
_RETRY_SLEEP_S = 0.05

_lock = threading.Lock()
_cache_path: str | None = None
_cache_mtime_ns: int = -1
_cache_size: int = -1
_cache_tickets: frozenset[int] = frozenset()
_cache_valid: bool = False


def default_manual_exceptions_path() -> Path:
    raw = str(os.environ.get(ENV_PATH, "") or "").strip()
    if raw:
        return Path(raw)
    return REPO_ROOT / DEFAULT_FILENAME


def reset_manual_exception_cache() -> None:
    """Test helper: drop in-process cache."""
    global _cache_path, _cache_mtime_ns, _cache_size, _cache_tickets, _cache_valid
    with _lock:
        _cache_path = None
        _cache_mtime_ns = -1
        _cache_size = -1
        _cache_tickets = frozenset()
        _cache_valid = False


def ensure_manual_exceptions_file(*, path: Path | None = None) -> Path:
    """Create the operator file with a header if it does not exist. Best-effort."""
    target = Path(path) if path is not None else default_manual_exceptions_path()
    _create_empty_file(target)
    return target


def load_manual_exception_tickets(*, path: Path | None = None) -> frozenset[int]:
    """Read tickets from the operator file.

    Missing file → empty set (does not raise). Watcher start should call
    ``ensure_manual_exceptions_file`` so operators have a template on disk.

    Fail-closed on read errors: keep the last successful snapshot so a mid-save
    on Windows cannot empty the set and let the ladder grab a manual ticket.
    """
    target = Path(path) if path is not None else default_manual_exceptions_path()
    try:
        resolved = str(target.resolve())
    except OSError:
        resolved = str(target)

    with _lock:
        return _load_locked(target, resolved)


def append_manual_exception_ticket(ticket: int, *, path: Path | None = None) -> str:
    """Append one positive ticket if it is not already listed.

    Returns ``added``, ``duplicate``, ``invalid``, or ``error``.
    Never rewrites existing lines. A failed read does not append.
    """
    global _cache_valid
    try:
        tid = int(ticket)
    except (TypeError, ValueError):
        return "invalid"
    if tid <= 0:
        return "invalid"

    target = Path(path) if path is not None else default_manual_exceptions_path()
    with _lock:
        _create_empty_file(target)
        text = _read_text_retry(target)
        if text is None:
            logger.warning("manual_exceptions: could not read %s; not appending %s.", target, tid)
            return "error"
        if tid in parse_manual_exception_tickets(text):
            return "duplicate"
        prefix = "" if (text == "" or text.endswith("\n")) else "\n"
        try:
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{prefix}{tid}\n")
                handle.flush()
        except OSError:
            logger.warning("manual_exceptions: could not append %s to %s.", tid, target, exc_info=True)
            return "error"
        _cache_valid = False
        logger.info("manual_exceptions: appended ticket %s to %s", tid, target)
        return "added"


def ticket_is_manually_excepted(ticket: int, *, path: Path | None = None) -> bool:
    try:
        tid = int(ticket)
    except (TypeError, ValueError):
        return False
    if tid <= 0:
        return False
    return tid in load_manual_exception_tickets(path=path)


def _load_locked(target: Path, resolved: str) -> frozenset[int]:
    global _cache_path, _cache_mtime_ns, _cache_size, _cache_tickets, _cache_valid

    if not target.exists():
        _cache_path = resolved
        _cache_mtime_ns = -1
        _cache_size = -1
        _cache_tickets = frozenset()
        _cache_valid = True
        return _cache_tickets

    mtime_ns = _stat_mtime_ns(target)
    size = _stat_size(target)
    if (
        _cache_valid
        and _cache_path == resolved
        and mtime_ns == _cache_mtime_ns
        and size == _cache_size
        and mtime_ns >= 0
    ):
        return _cache_tickets

    text = _read_text_retry(target)
    if text is None:
        if _cache_valid and _cache_path == resolved:
            logger.warning(
                "manual_exceptions: could not read %s; keeping last snapshot (%d ticket(s)).",
                target,
                len(_cache_tickets),
            )
            return _cache_tickets
        logger.warning("manual_exceptions: could not read %s; treating as empty.", target)
        return frozenset()

    tickets = parse_manual_exception_tickets(text)
    prev = _cache_tickets if (_cache_valid and _cache_path == resolved) else None
    _cache_path = resolved
    _cache_mtime_ns = mtime_ns
    _cache_size = size
    _cache_tickets = tickets
    _cache_valid = True
    if prev is not None and prev != tickets:
        logger.info(
            "manual_exceptions: reloaded %s (%d -> %d ticket(s)).",
            target,
            len(prev),
            len(tickets),
        )
    return tickets


def _create_empty_file(target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        target.write_text(_FILE_HEADER, encoding="utf-8")
        logger.info("manual_exceptions: created empty %s", target)
    except OSError:
        logger.warning("manual_exceptions: could not create %s (non-fatal).", target, exc_info=True)


def _read_text_retry(target: Path) -> str | None:
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            last_exc = exc
            if attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_SLEEP_S)
    if last_exc is not None:
        logger.debug("manual_exceptions: read failed after retries: %s", last_exc)
    return None


def _stat_mtime_ns(target: Path) -> int:
    try:
        return int(target.stat().st_mtime_ns)
    except OSError:
        return -1


def _stat_size(target: Path) -> int:
    try:
        return int(target.stat().st_size)
    except OSError:
        return -1
