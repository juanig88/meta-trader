"""Classify rotation close/open audit tags. Pure: no MT5."""

from __future__ import annotations

from typing import Any, Mapping

# MetaTrader DEAL_REASON_* (stable ints in the official enum).
_DEAL_HOW = {
    0: "client_manual",
    1: "mobile",
    2: "web",
    3: "expert_or_bot",
    4: "stop_loss",
    5: "take_profit",
    6: "stop_out",
    7: "rollover",
    8: "margin",
    9: "split",
}


def classify_rotation_close(*, comment: str = "", reason_code: int | None = None) -> str:
    """Machine tag for why a rotation position left the book."""
    c = str(comment or "").lower()
    if "rot_stale_scratch" in c or "rot_stale_loss" in c:
        return "stale_loss"
    if "rot_stale_take" in c:
        return "stale_take"
    if "rot_book_harvest" in c:
        return "book_harvest"
    if "bot_filter_exit" in c:
        return "filter_exit"
    if "meta_stagnant" in c:
        return "stagnant_timeout"
    if "usd_ladder_bank" in c:
        return "ladder_bank"
    try:
        code = int(reason_code) if reason_code is not None else -1
    except (TypeError, ValueError):
        code = -1
    if code in _DEAL_HOW:
        return _DEAL_HOW[code]
    return "unknown"


def ensemble_vote_payload(row: Mapping[str, Any], *, ts: str, execution_mode: str = "") -> dict[str, Any]:
    """One JSON object per symbol per cycle (open why / skip why)."""
    consensus = row.get("consensus") if isinstance(row.get("consensus"), dict) else {}
    size = row.get("size") if isinstance(row.get("size"), dict) else {}
    votes = row.get("votes") if isinstance(row.get("votes"), list) else []
    vote_sides = [
        f"{v.get('name') or v.get('label')}:{v.get('side')}"
        for v in votes
        if isinstance(v, dict)
    ]
    out = {
        "event": "ENSEMBLE_VOTE",
        "ts": ts,
        "engine": "rotation",
        "symbol": row.get("symbol"),
        "direction": consensus.get("direction") or row.get("direction") or "NONE",
        "buy": consensus.get("buy", row.get("buy", 0)),
        "sell": consensus.get("sell", row.get("sell", 0)),
        "none": consensus.get("none", row.get("none", 0)),
        "agree": consensus.get("agree", row.get("agree")),
        "fired": bool(consensus.get("fired", row.get("fired"))),
        "action": row.get("action") or "SKIP",
        "skip_reason": row.get("skip_reason") or "",
        "why_open": "consenso_4_5" if row.get("action") in ("LIVE", "DRY_RUN") else (row.get("skip_reason") or "no_fire"),
        "risk_usd": size.get("risk_usd", row.get("risk_usd")),
        "lot": size.get("lot", row.get("lot")),
        "execution_mode": execution_mode,
        "votes": vote_sides,
    }
    murphy = row.get("murphy")
    if murphy:
        out["murphy"] = murphy
    alpha = row.get("alpha")
    if alpha:
        out["alpha"] = alpha
    state = row.get("state")
    if state:
        out["state"] = state
    return out


def open_audit_from_snap(row: Mapping[str, Any], *, execution_mode: str = "") -> dict[str, Any]:
    """Fields copied onto TRADE_OPEN so a later TRADE_CLOSE can join the why."""
    payload = ensemble_vote_payload(row, ts="", execution_mode=execution_mode)
    payload.pop("event", None)
    payload.pop("ts", None)
    payload["why_open"] = "consenso_4_5"
    payload["engine"] = "rotation"
    return payload
