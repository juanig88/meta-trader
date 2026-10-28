"""Parse structured JSON lines from trades.log. No MT5."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

DEFAULT_LOG = Path("trades.log")


def iter_json_events(path: Path | str = DEFAULT_LOG) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                rec = _parse_line(line)
                if rec is not None:
                    yield rec
    except OSError:
        return


def _parse_line(line: str) -> Optional[dict[str, Any]]:
    text = str(line or "").strip()
    if not text:
        return None
    brace = text.find("{")
    if brace < 0:
        return None
    try:
        payload = json.loads(text[brace:])
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("event"):
        return payload
    return None


def events_named(path: Path | str, names: Iterable[str]) -> list[dict[str, Any]]:
    want = {str(n) for n in names}
    return [e for e in iter_json_events(path) if str(e.get("event") or "") in want]


def rotation_log_paths(
    root: Path | str = ".",
    *,
    since: str = "2026-09-29",
) -> list[Path]:
    """Rotated session logs (``logs/trades_YYYY-MM-DD_HHMMSS.log``) since a date, then trades.log.

    The bot moves trades.log aside on every start, so history lives in logs/.
    """
    base = Path(root)
    cutoff = f"trades_{since[:10]}"
    rotated = sorted(
        p
        for p in (base / "logs").glob("trades_*.log")
        if p.name[len("trades_"):len("trades_") + 1].isdigit() and p.name >= cutoff
    )
    live = base / "trades.log"
    return rotated + ([live] if live.exists() else [])


def iter_events_files(
    paths: Iterable[Path | str],
    names: Iterable[str],
) -> Iterator[dict[str, Any]]:
    """Events named ``names`` across many logs. Substring prefilter before json.loads.

    Rotated logs run to hundreds of MB of ENSEMBLE_VOTE lines; parsing every one
    would take minutes.
    """
    want = {str(n) for n in names}
    needles = [f'"event": "{n}"' for n in want]
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not any(nd in line for nd in needles):
                        continue
                    rec = _parse_line(line)
                    if rec is not None and str(rec.get("event") or "") in want:
                        yield rec
        except OSError:
            continue
