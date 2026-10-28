"""Read-only view of the manual miner for the dashboard.

Sync, like the lab snapshot. Fail-open if Postgres is down. Nothing here
places an order or changes a weight.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from trading.infrastructure.data_layer_config import DataLayerConfigError, PgSettings


def _f(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    return str(value)


def _num(value: Any, digits: int = 2) -> str:
    parsed = _f(value)
    if parsed is None:
        return "—"
    return f"{parsed:.{digits}f}"


def _comparison(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def journal_view_row(row: tuple) -> dict[str, Any]:
    """One manual_trade_journal tuple, display-ready."""
    net = _f(row[4])
    winner = bool(row[5])
    return {
        "closed_at": _iso(row[0]),
        "symbol": str(row[1] or ""),
        "side": str(row[2] or ""),
        "volume": _num(row[3], 2),
        "net": net if net is not None else 0.0,
        "net_text": _num(net, 2),
        "winner": winner,
        "hour": "—" if row[6] is None else str(int(row[6])),
        "zscore": _num(row[7], 2),
        "rvol": _num(row[8], 2),
        "squeeze": "—" if row[9] is None else ("sí" if row[9] else "no"),
        "alpha_direction": str(row[10] or "") or "—",
        "q": _num(row[11], 2),
    }


def report_view_row(row: tuple) -> dict[str, Any]:
    comparison = _comparison(row[6])
    agreement = comparison.get("agreement")
    return {
        "ts": _iso(row[0]),
        "trigger": str(row[1] or ""),
        "n_trades": int(row[2] or 0),
        "n_winners": int(row[3] or 0),
        "n_new": int(row[4] or 0),
        "summary": str(row[5] or "")[:8000],
        "agreement": "n/a" if agreement is None else f"{float(agreement):.1%}",
        "refit_applied": bool(comparison.get("refit_applied")),
    }


def empty_miner_snapshot(error: Optional[str] = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "n_trades": 0,
        "n_winners": 0,
        "n_losers": 0,
        "win_rate": "—",
        "report": None,
        "rows": [],
    }


def fetch_miner_snapshot(*, limit: int = 40) -> dict[str, Any]:
    """Latest report plus recent manual closes. Never raises."""
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError as exc:
        return empty_miner_snapshot(str(exc))

    cap = max(1, min(int(limit), 200))
    try:
        import psycopg

        with psycopg.connect(settings.conninfo(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                return _read(cur, cap)
    except Exception as exc:
        text = str(exc)
        if "manual_trade_journal" in text or "manual_miner_reports" in text:
            return empty_miner_snapshot("El minero todavía no creó las tablas.")
        return empty_miner_snapshot(f"Postgres no responde ({exc}).")


def _read(cur: Any, limit: int) -> dict[str, Any]:
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE winner) FROM manual_trade_journal"
    )
    counts = cur.fetchone() or (0, 0)
    n_trades = int(counts[0] or 0)
    n_winners = int(counts[1] or 0)
    n_losers = max(0, n_trades - n_winners)
    win_rate = f"{(n_winners / n_trades):.1%}" if n_trades else "—"

    cur.execute(
        "SELECT ts, trigger, n_trades, n_winners, n_new, summary, comparison "
        "FROM manual_miner_reports ORDER BY id DESC LIMIT 1"
    )
    report_row = cur.fetchone()
    report = report_view_row(report_row) if report_row else None

    cur.execute(
        "SELECT closed_at, symbol, side, volume, net_profit, winner, hour_of_day, "
        "zscore, rvol, squeeze, alpha_direction, q "
        "FROM manual_trade_journal ORDER BY closed_at DESC NULLS LAST LIMIT %s",
        (limit,),
    )
    rows = [journal_view_row(row) for row in cur.fetchall()]
    return {
        "ok": True,
        "error": None,
        "n_trades": n_trades,
        "n_winners": n_winners,
        "n_losers": n_losers,
        "win_rate": win_rate,
        "report": report,
        "rows": rows,
    }
