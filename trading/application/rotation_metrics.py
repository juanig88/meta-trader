"""Rotation close / floating stats for the dashboard. No I/O."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from trading.domain.crypto_universe import CRYPTO_UNIVERSE


def summarize_rotation_closes(
    closed: Sequence[Mapping[str, Any]] = (),
    floating: Sequence[Mapping[str, Any]] = (),
    *,
    universe: Sequence[str] = CRYPTO_UNIVERSE,
    origin: str = "",
) -> dict[str, Any]:
    """PnL of crypto-universe trades. ``origin`` empty = bot + manual + other magics."""
    uni = {str(s or "").strip().upper() for s in universe if str(s or "").strip()}
    want_origin = str(origin or "").strip().lower()

    rows: list[Mapping[str, Any]] = []
    for row in closed:
        symbol = str(row.get("symbol") or "").strip().upper()
        if uni and symbol not in uni:
            continue
        label = str(row.get("origin") or "").strip().lower()
        if want_origin and label and label != want_origin:
            continue
        rows.append(row)

    wins = [r for r in rows if float(r.get("net") or 0) > 0]
    losses = [r for r in rows if float(r.get("net") or 0) < 0]
    decided = wins + losses
    n_dec = len(decided)
    realized = sum(float(r.get("net") or 0) for r in rows)
    float_rows = []
    for pos in floating:
        symbol = str(pos.get("symbol") or "").strip().upper()
        if uni and symbol not in uni:
            continue
        label = str(pos.get("origin") or "").strip().lower()
        if want_origin and label and label != want_origin:
            continue
        float_rows.append(pos)
    floating_net = sum(float(p.get("net") or 0) for p in float_rows)
    return {
        "n_closed": len(rows),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_decided": n_dec,
        "winrate": round((len(wins) / n_dec), 4) if n_dec else None,
        "realized_net": round(realized, 2),
        "floating_net": round(floating_net, 2),
        "n_open": len(float_rows),
        "account_net": round(realized + floating_net, 2),
    }


def rotation_close_rows(
    closed: Sequence[Mapping[str, Any]] = (),
    *,
    universe: Sequence[str] = CRYPTO_UNIVERSE,
    origin: str = "",
) -> list[dict[str, Any]]:
    """Closed deals in the crypto universe. Empty origin keeps manual closes too."""
    from trading.application.rotation_audit import classify_rotation_close

    uni = {str(s or "").strip().upper() for s in universe if str(s or "").strip()}
    want_origin = str(origin or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in closed:
        symbol = str(row.get("symbol") or "").strip().upper()
        if uni and symbol not in uni:
            continue
        label = str(row.get("origin") or "").strip().lower()
        if want_origin and label and label != want_origin:
            continue
        item = dict(row)
        item["how"] = classify_rotation_close(
            comment=str(item.get("comment") or ""),
            reason_code=item.get("reason"),
        )
        out.append(item)
    return out


def summarize_hourly_opens(
    deals: Sequence[Mapping[str, Any]] = (),
    *,
    magics: Sequence[int] = (),
    cap: int = 60,
) -> dict[str, Any]:
    """ENTRY_IN deals in the already-sliced window (caller fetches last 60m)."""
    from trading.application.account_pnl import DEAL_ENTRY_IN, DEAL_TYPE_BUY, is_balance_deal

    want = {int(m) for m in magics if m is not None}
    rows: list[dict[str, Any]] = []
    for deal in deals:
        if is_balance_deal(deal):
            continue
        try:
            entry = int(deal.get("entry", -1))
            mag = int(deal.get("magic") or 0)
        except (TypeError, ValueError):
            continue
        if entry != DEAL_ENTRY_IN:
            continue
        if want and mag not in want:
            continue
        try:
            typ = int(deal.get("type", -1))
        except (TypeError, ValueError):
            typ = -1
        rows.append(
            {
                "time": deal.get("time"),
                "symbol": str(deal.get("symbol") or ""),
                "side": "BUY" if typ == DEAL_TYPE_BUY else "SELL",
                "volume": float(deal.get("volume") or 0.0),
                "ticket": deal.get("ticket") or deal.get("deal"),
                "magic": mag,
            }
        )
    rows.sort(key=lambda r: str(r.get("time") or ""), reverse=True)
    n = len(rows)
    cap_i = max(0, int(cap))
    return {
        "ok": True,
        "n": n,
        "cap": cap_i,
        "remaining": max(0, cap_i - n) if cap_i > 0 else 0,
        "at_cap": bool(cap_i > 0 and n >= cap_i),
        "rows": rows,
    }
