"""Read-only local bot snapshot for MCP analysis (no broker I/O, no order routing)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization|login)",
    re.IGNORECASE,
)

_EVAL_EVENT_NAMES = frozenset(
    {
        "EVAL_CYCLE",
        "EVAL_SKIP",
        "EVAL_ENTRY_ATTEMPT",
        "EVAL_TRACKS_BOOT",
    }
)

_MAX_EVAL_LIMIT = 100
_DEFAULT_EVAL_LIMIT = 40
_MAX_LOG_BYTES = 400_000


def resolve_root(raw: str | None, *, fallback: Path) -> Path:
    """Resolve the workspace root. Empty/invalid values fall back to `fallback`."""
    if raw is None or not str(raw).strip():
        return fallback.resolve()
    path = Path(str(raw).strip())
    try:
        resolved = path.resolve()
    except OSError:
        return fallback.resolve()
    if not resolved.is_dir():
        return fallback.resolve()
    return resolved


def redact(obj: Any) -> Any:
    """Replace secret-looking mapping keys with a placeholder; recurse into dict/list."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            label = str(key)
            if _SENSITIVE_KEY_RE.search(label):
                out[label] = "[redacted]"
            else:
                out[label] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact(item) for item in obj]
    return obj


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def compute_health(state: dict[str, Any]) -> dict[str, Any]:
    """Heartbeat/running summary from bot_state.json (no process probing)."""
    out: dict[str, Any] = {
        "heartbeat_age_sec": None,
        "heartbeat_stale": False,
        "pid": None,
        "running": bool(state.get("running")) if state else False,
        "mode": state.get("mode") if state else None,
    }
    if not state:
        return out

    pid_raw = state.get("pid")
    try:
        pid = int(float(pid_raw)) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid = None
    out["pid"] = pid if pid is not None and pid > 0 else None

    ts_raw = state.get("ts")
    if isinstance(ts_raw, str):
        try:
            hb = datetime.fromisoformat(ts_raw)
            out["heartbeat_age_sec"] = round((datetime.now() - hb).total_seconds(), 1)
        except ValueError:
            out["heartbeat_age_sec"] = None

    try:
        poll = float(state.get("poll_seconds") or 30)
    except (TypeError, ValueError):
        poll = 30.0
    threshold = max(90.0, 2.0 * poll + 30.0)
    age = out["heartbeat_age_sec"]
    if out["running"] and age is not None and age > threshold:
        out["heartbeat_stale"] = True
    return out


def clamp_eval_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = _DEFAULT_EVAL_LIMIT
    if n < 1:
        return 1
    if n > _MAX_EVAL_LIMIT:
        return _MAX_EVAL_LIMIT
    return n


def parse_recent_eval_events(
    path: Path,
    *,
    limit: int = _DEFAULT_EVAL_LIMIT,
    max_bytes: int = _MAX_LOG_BYTES,
) -> list[dict[str, Any]]:
    """Tail-parse trades.log for EVAL_* JSON lines (newest last)."""
    limit = clamp_eval_limit(limit)
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        if " | " in text:
            text = text.split(" | ", 2)[-1].strip()
        if not (text.startswith("{") and text.endswith("}")):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        event = str(obj.get("event") or "")
        if event not in _EVAL_EVENT_NAMES:
            continue
        out.append(obj)
    if limit > 0 and len(out) > limit:
        out = out[-limit:]
    return out


def bot_snapshot(root: Path) -> dict[str, Any]:
    state_path = root / "bot_state.json"
    state = redact(load_json_file(state_path))
    return {
        "root": str(root),
        "state_path": str(state_path),
        "state_exists": state_path.is_file(),
        "health": compute_health(state if isinstance(state, dict) else {}),
        "state": state,
    }


def eval_tracks(root: Path) -> dict[str, Any]:
    state = load_json_file(root / "bot_state.json")
    tracks = state.get("eval_tracks") if isinstance(state, dict) else None
    return {"eval_tracks": redact(tracks) if tracks is not None else None}


def recent_eval_events(root: Path, *, limit: int = _DEFAULT_EVAL_LIMIT) -> dict[str, Any]:
    events = parse_recent_eval_events(root / "trades.log", limit=limit)
    return {"limit": clamp_eval_limit(limit), "events": redact(events)}
