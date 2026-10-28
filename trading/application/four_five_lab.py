"""Recent 4/5 fires joined to their open / reject / close. Dashboard-only, read-only.

A fire is an ENSEMBLE_VOTE with ``fired: true`` (sent or vetoed). A sent fire is
joined to the first TRADE_OPEN or ROTATION_OPEN_REJECT of the same symbol within
a short window, then to its close by ticket (TRADE_OPEN ``order`` == close ticket).
Only the tail of trades.log is read: it grows by hundreds of MB a day.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ROTATION_MAGIC = 260414
JOIN_WINDOW_SECONDS = 120
DEFAULT_TAIL_BYTES = 40 * 1024 * 1024
NEEDLES = (
    '"event": "TRADE_OPEN"',
    '"event": "ROTATION_OPEN_REJECT"',
    '"event": "TRADE_CLOSE"',
    '"event": "ROTATION_STALE_CLOSE"',
)
FIRE_NEEDLE = '"event": "ENSEMBLE_VOTE"'


def _ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _pid(raw: Any) -> Optional[int]:
    try:
        out = int(raw)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def tail_events(path: Path | str, *, max_bytes: int = DEFAULT_TAIL_BYTES) -> list[dict[str, Any]]:
    """Fires, opens, rejects and closes from the last ``max_bytes`` of the log."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()
            for raw in fh:
                line = raw.decode("utf-8", errors="replace")
                is_fire = FIRE_NEEDLE in line and '"fired": true' in line
                if not is_fire and not any(nd in line for nd in NEEDLES):
                    continue
                brace = line.find("{")
                if brace < 0:
                    continue
                try:
                    rec = json.loads(line[brace:])
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("event"):
                    out.append(rec)
    except OSError:
        return []
    return out


def four_five_rows_from_events(events: Iterable[Mapping[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    fires: list[Mapping[str, Any]] = []
    entries: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
    closes: dict[int, dict[str, Any]] = {}
    for ev in events:
        name = str(ev.get("event") or "")
        sym = str(ev.get("symbol") or "").strip().upper()
        if name == "ENSEMBLE_VOTE":
            if ev.get("fired"):
                fires.append(ev)
        elif name in ("TRADE_OPEN", "ROTATION_OPEN_REJECT"):
            if name == "TRADE_OPEN" and int(ev.get("magic") or 0) != ROTATION_MAGIC:
                continue
            t = _ts(ev.get("ts"))
            if t is not None and sym:
                entries.setdefault(sym, []).append((t, ev))
        elif name == "TRADE_CLOSE":
            pid = _pid(ev.get("position_id"))
            if pid is not None:
                closes[pid] = {"net": ev.get("net"), "how": ev.get("close_how") or ev.get("why_closed"), "src": "trade_close"}
        elif name == "ROTATION_STALE_CLOSE" and ev.get("closed", True):
            pid = _pid(ev.get("ticket"))
            if pid is not None and closes.get(pid, {}).get("src") != "trade_close":
                closes[pid] = {"net": ev.get("profit"), "how": ev.get("why_closed") or ev.get("reason"), "src": "stale_close"}
    for rows in entries.values():
        rows.sort(key=lambda r: r[0])

    out: list[dict[str, Any]] = []
    for fire in reversed(fires):
        if len(out) >= int(limit):
            break
        sym = str(fire.get("symbol") or "").strip().upper()
        t_fire = _ts(fire.get("ts"))
        alpha = fire.get("alpha") if isinstance(fire.get("alpha"), Mapping) else {}
        murphy = fire.get("murphy") if isinstance(fire.get("murphy"), Mapping) else {}
        state = fire.get("state") if isinstance(fire.get("state"), Mapping) else {}
        row: dict[str, Any] = {
            "ts": fire.get("ts"),
            "symbol": fire.get("symbol"),
            "side": fire.get("direction"),
            "agree": fire.get("agree"),
            "action": "skip",
            "reason": fire.get("skip_reason") or "",
            "alpha_code": alpha.get("code"),
            "alpha_score": alpha.get("score"),
            "would_avoid": alpha.get("would_avoid"),
            "murphy_tag": murphy.get("tag_m15") or murphy.get("tag_h4"),
            "murphy_veto": murphy.get("would_veto"),
            "tape_imbalance": state.get("tape_imbalance"),
            "spread_usd": state.get("spread_usd"),
            "ticket": None,
            "net": None,
            "close_how": "",
        }
        if str(fire.get("action") or "").upper() != "SKIP" and t_fire is not None:
            row["action"] = "pending"
            row["reason"] = ""
            for t_ent, ent in entries.get(sym, []):
                dt = (t_ent - t_fire).total_seconds()
                if dt < 0:
                    continue
                if dt > JOIN_WINDOW_SECONDS:
                    break
                if ent.get("event") == "ROTATION_OPEN_REJECT":
                    row["action"] = "reject"
                    row["reason"] = str(ent.get("reason") or ent.get("why_not_filled") or "reject")
                else:
                    pid = _pid(ent.get("order")) or _pid(ent.get("position_id"))
                    row["ticket"] = pid
                    close = closes.get(pid) if pid is not None else None
                    if close:
                        row["action"] = "closed"
                        row["net"] = close.get("net")
                        row["close_how"] = str(close.get("how") or "")
                    else:
                        row["action"] = "open"
                break
        out.append(row)
    return out


def four_five_rows(
    path: Path | str = "trades.log",
    *,
    limit: int = 40,
    max_bytes: int = DEFAULT_TAIL_BYTES,
) -> list[dict[str, Any]]:
    return four_five_rows_from_events(tail_events(path, max_bytes=max_bytes), limit=limit)
