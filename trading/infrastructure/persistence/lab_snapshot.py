"""Read-only snapshot of the quant data layer for the Flask lab page.

Sync on purpose: flask_dashboard is a sync app. Fail-open if Postgres is down
or MT5_PG_PASSWORD is missing — the trading loop does not depend on this.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from trading.domain.loser_review import (
    LoserRow,
    classify_alpha,
    classify_path,
    format_share_text,
    propose_improvements,
    ready_to_discuss_change,
    tally_alpha,
)
from trading.infrastructure.data_layer_config import DataLayerConfigError, PgSettings

_LOG_EVENTS = {"TRADE_CLOSE", "SIGNAL"}
_ALPHA_WINDOW = timedelta(minutes=15)

DIRECTION_ES = {"BUY": "Compra", "SELL": "Venta", "NONE": "Neutro"}


def direction_label(direction: str) -> str:
    key = str(direction or "").strip().upper()
    return DIRECTION_ES.get(key, key or "—")


def score_bar_pct(score: Optional[float]) -> float:
    """Map score in [-1, 1] to 0..100 for a CSS bar."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 50.0
    if s != s:
        return 50.0
    return max(0.0, min(100.0, (s + 1.0) * 50.0))


def _f(value: Any) -> Optional[float]:
    if value is None:
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


def fetch_lab_snapshot(
    *,
    history_limit: int = 48,
    since: datetime | None = None,
    symbols: set[str] | None = None,
) -> dict[str, Any]:
    """Return a JSON-friendly dict. Never raises to the caller."""
    empty: dict[str, Any] = {
        "ok": False,
        "error": None,
        "pg": None,
        "counts": {"ticks": 0, "bars": 0, "scores": 0},
        "ticks": [],
        "bars": [],
        "latest": [],
        "history": [],
        "partitions": [],
        "losers": _empty_losers(),
    }
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError as exc:
        empty["error"] = str(exc)
        empty["losers"] = _loser_block(None, since=since, symbols=symbols)
        return empty

    empty["pg"] = settings.redacted()
    try:
        import psycopg

        with psycopg.connect(settings.conninfo(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                snap = _read(cur, settings.redacted(), history_limit)
                snap = _filter_snapshot_symbols(snap, symbols)
                snap["losers"] = _loser_block(cur, since=since, symbols=symbols)
                return snap
    except Exception as exc:
        empty["error"] = f"Postgres no responde ({exc}). ¿Está Docker up en el puerto {settings.port}?"
        empty["losers"] = _loser_block(None, since=since, symbols=symbols)
        return empty


def _read(cur: Any, redacted: str, history_limit: int) -> dict[str, Any]:
    counts = {
        "ticks": _scalar(cur, "SELECT count(*) FROM md_ticks"),
        "bars": _scalar(cur, "SELECT count(*) FROM md_bars"),
        "scores": _scalar(cur, "SELECT count(*) FROM alpha_scores"),
    }

    cur.execute("SELECT symbol, count(*), max(ts) FROM md_ticks GROUP BY symbol ORDER BY symbol")
    tick_counts = {row[0]: (int(row[1] or 0), row[2]) for row in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT ON (symbol) symbol, ts, bid, ask "
        "FROM md_ticks ORDER BY symbol, ts DESC"
    )
    ticks = []
    for row in cur.fetchall():
        count, last_ts = tick_counts.get(row[0], (0, row[1]))
        ticks.append(
            {
                "symbol": row[0],
                "count": count,
                "last_ts": _iso(last_ts),
                "bid": _f(row[2]),
                "ask": _f(row[3]),
            }
        )

    cur.execute(
        "SELECT symbol, timeframe, count(*), max(ts) "
        "FROM md_bars GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    )
    bar_counts = {(row[0], row[1]): (int(row[2] or 0), row[3]) for row in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT ON (symbol, timeframe) symbol, timeframe, ts, close "
        "FROM md_bars ORDER BY symbol, timeframe, ts DESC"
    )
    bars = []
    for row in cur.fetchall():
        count, last_ts = bar_counts.get((row[0], row[1]), (0, row[2]))
        bars.append(
            {
                "symbol": row[0],
                "timeframe": row[1],
                "count": count,
                "last_ts": _iso(last_ts),
                "close": _f(row[3]),
            }
        )

    cur.execute(
        "SELECT DISTINCT ON (symbol) "
        "ts, symbol, timeframe, direction, score, momentum, volatility, zscore, "
        "flow, gross_edge, cost_spread, cost_commission, cost_slippage, net_edge "
        "FROM alpha_scores ORDER BY symbol, ts DESC"
    )
    latest = [_score_row(row) for row in cur.fetchall()]

    cur.execute(
        "SELECT ts, symbol, timeframe, direction, score, momentum, volatility, zscore, "
        "flow, gross_edge, cost_spread, cost_commission, cost_slippage, net_edge "
        "FROM alpha_scores ORDER BY ts DESC LIMIT %s",
        (max(3, int(history_limit)),),
    )
    history = [_score_row(row) for row in cur.fetchall()]

    cur.execute(
        "SELECT parent, child, day, bytes FROM md_partition_inventory ORDER BY parent, day"
    )
    partitions = [
        {
            "parent": row[0],
            "child": row[1],
            "day": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
            "mb": round(float(row[3] or 0) / (1024 * 1024), 2),
        }
        for row in cur.fetchall()
    ]

    return {
        "ok": True,
        "error": None,
        "pg": redacted,
        "counts": counts,
        "ticks": ticks,
        "bars": bars,
        "latest": latest,
        "history": history,
        "partitions": partitions,
    }


def _score_row(row: tuple) -> dict[str, Any]:
    score = _f(row[4]) or 0.0
    direction = str(row[3] or "NONE")
    return {
        "ts": _iso(row[0]),
        "symbol": row[1],
        "timeframe": row[2],
        "direction": direction,
        "direction_label": direction_label(direction),
        "score": score,
        "score_pct": score_bar_pct(score),
        "momentum": _f(row[5]),
        "volatility": _f(row[6]),
        "zscore": _f(row[7]),
        "flow": _f(row[8]),
        "gross_edge": _f(row[9]),
        "cost_spread": _f(row[10]),
        "cost_commission": _f(row[11]),
        "cost_slippage": _f(row[12]),
        "net_edge": _f(row[13]),
    }


def _scalar(cur: Any, sql: str) -> int:
    cur.execute(sql)
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def _empty_losers() -> dict[str, Any]:
    return {
        "rows": [],
        "win_rows": [],
        "n": 0,
        "n_wins": 0,
        "sum_net": 0.0,
        "sum_wins": 0.0,
        "tally": {},
        "ready": False,
        "min_n": 15,
        "suggestions": [],
        "share_text": format_share_text([], [], n_wins=0),
    }


def _log_close_row(ev: dict[str, Any]) -> dict[str, Any]:
    closed = _parse_dt(ev.get("closed_at") or ev.get("ts"))
    opened = _parse_dt(ev.get("opened_at"))
    net = _f(ev.get("net"))
    magic = ev.get("magic")
    try:
        magic_i = int(magic) if magic is not None else 0
    except (TypeError, ValueError):
        magic_i = 0
    return {
        "symbol": str(ev.get("symbol") or "").upper() or "?",
        "side": str(ev.get("side") or "").upper() or "?",
        "net": float(net) if net is not None else 0.0,
        "opened_at": opened.isoformat(timespec="seconds") if opened else None,
        "closed_at": closed.isoformat(timespec="seconds") if closed else None,
        "reason_label": str(ev.get("reason_label") or ev.get("reason") or "—"),
        "magic": magic_i,
        "origin": "bot" if magic_i > 0 else "manual",
    }


def _close_on_or_after(ev: dict[str, Any], since: datetime | None) -> bool:
    if since is None:
        return True
    when = _parse_dt(ev.get("closed_at") or ev.get("ts"))
    if when is None:
        return False
    left = when.replace(tzinfo=None) if when.tzinfo else when
    right = since.replace(tzinfo=None) if since.tzinfo else since
    return left >= right


def _filter_snapshot_symbols(snap: dict[str, Any], symbols: set[str] | None) -> dict[str, Any]:
    if not symbols:
        return snap
    allowed = {str(s).strip().upper() for s in symbols if str(s).strip()}
    for key in ("ticks", "bars", "latest", "history"):
        rows = snap.get(key)
        if isinstance(rows, list):
            snap[key] = [
                row
                for row in rows
                if str((row or {}).get("symbol") or "").strip().upper() in allowed
            ]
    return snap


def _loser_block(
    cur: Any,
    *,
    since: datetime | None = None,
    symbols: set[str] | None = None,
) -> dict[str, Any]:
    closes, signals = _load_trade_events()
    if since is not None:
        closes = [c for c in closes if _close_on_or_after(c, since)]
    if symbols:
        allowed = {str(s).strip().upper() for s in symbols if str(s).strip()}
        closes = [c for c in closes if str(c.get("symbol") or "").strip().upper() in allowed]
        signals = [s for s in signals if str(s.get("symbol") or "").strip().upper() in allowed]
    wins = [c for c in closes if _f(c.get("net")) is not None and float(c["net"]) > 0]
    losers = [c for c in closes if _f(c.get("net")) is not None and float(c["net"]) < 0]
    rows: list[LoserRow] = []
    for ev in losers:
        opened = _parse_dt(ev.get("opened_at") or ev.get("ts"))
        closed = _parse_dt(ev.get("closed_at"))
        symbol = str(ev.get("symbol") or "").upper()
        side = str(ev.get("side") or "").upper()
        alpha_dir, alpha_score = (None, None)
        if cur is not None and opened is not None and symbol:
            alpha_dir, alpha_score = _nearest_alpha(cur, symbol, opened)
        highs, lows = ([], [])
        if cur is not None and opened is not None and symbol:
            highs, lows = _m15_after(cur, symbol, opened, limit=12)
        path_code, mfe, mae = classify_path(side, _f(ev.get("price_open")), highs, lows)
        skip = _nearest_signal_skip(signals, symbol, opened)
        rows.append(
            LoserRow(
                symbol=symbol or "?",
                side=side or "?",
                net=float(ev.get("net") or 0.0),
                opened_at=opened,
                closed_at=closed,
                reason_label=str(ev.get("reason_label") or ev.get("reason") or "—"),
                alpha_code=classify_alpha(side, alpha_dir),
                alpha_direction=str(alpha_dir or "NONE"),
                alpha_score=alpha_score,
                path_code=path_code,
                mfe=mfe,
                mae=mae,
                signal_skip=skip,
            )
        )
    rows.sort(key=lambda r: r.closed_at or r.opened_at or datetime.min, reverse=True)
    win_rows = [_log_close_row(ev) for ev in wins]
    win_rows.sort(key=lambda r: str(r.get("closed_at") or ""), reverse=True)
    suggestions = propose_improvements(rows, n_wins=len(wins))
    return {
        "rows": [r.as_dict() for r in rows[:40]],
        "win_rows": win_rows[:40],
        "n": len(rows),
        "n_wins": len(wins),
        "sum_net": round(sum(r.net for r in rows), 2),
        "sum_wins": round(sum(float(r.get("net") or 0.0) for r in win_rows), 2),
        "tally": tally_alpha(rows),
        "ready": ready_to_discuss_change(len(rows)),
        "min_n": 15,
        "suggestions": [s.as_dict() for s in suggestions],
        "share_text": format_share_text(rows, suggestions, n_wins=len(wins)),
    }


def _load_trade_events() -> tuple[list[dict], list[dict]]:
    root = Path(__file__).resolve().parents[3]
    paths = [root / "trades.log"]
    log_dir = root / "logs"
    if log_dir.is_dir():
        rotated = [
            p
            for p in log_dir.glob("trades_*.log")
            if not p.name.startswith("trades_watcher_")
        ]
        rotated.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        paths.extend(rotated[:12])
    closes: list[dict] = []
    signals: list[dict] = []
    seen: set[tuple] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            msg = line.split(" | ", 2)[-1] if " | " in line else line
            msg = msg.strip()
            if not msg.startswith("{"):
                continue
            try:
                obj = json.loads(msg)
            except Exception:
                continue
            if not isinstance(obj, dict) or obj.get("event") not in _LOG_EVENTS:
                continue
            key = (
                obj.get("event"),
                obj.get("symbol"),
                obj.get("deal_ticket") or obj.get("position_id") or obj.get("ts"),
                obj.get("closed_at") or obj.get("ts"),
            )
            if key in seen:
                continue
            seen.add(key)
            if obj.get("event") == "TRADE_CLOSE":
                closes.append(obj)
            else:
                signals.append(obj)
    return closes, signals


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _nearest_signal_skip(signals: list[dict], symbol: str, opened: Optional[datetime]) -> str:
    if opened is None:
        return ""
    best = None
    best_delta = None
    for ev in signals:
        if str(ev.get("symbol") or "").upper() != symbol:
            continue
        when = _parse_dt(ev.get("ts") or ev.get("_dt"))
        if when is None:
            continue
        delta = abs((when - opened).total_seconds())
        if delta > 1800:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = ev
    if not best:
        return ""
    skip = str(best.get("skip_reason") or "").strip()
    decision = str(best.get("decision") or "").strip()
    if skip and skip not in ("", "none"):
        return skip
    return decision


def _nearest_alpha(cur: Any, symbol: str, opened: datetime) -> tuple[Optional[str], Optional[float]]:
    when = opened.astimezone(timezone.utc)
    cur.execute(
        "SELECT direction, score, ts FROM alpha_scores "
        "WHERE symbol = %s AND ts BETWEEN %s AND %s "
        "ORDER BY abs(extract(epoch from ts - %s)) ASC LIMIT 1",
        (symbol, when - _ALPHA_WINDOW, when + _ALPHA_WINDOW, when),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return str(row[0] or "NONE"), _f(row[1])


def _m15_after(cur: Any, symbol: str, opened: datetime, *, limit: int) -> tuple[list[float], list[float]]:
    when = opened.astimezone(timezone.utc)
    cur.execute(
        "SELECT high, low FROM md_bars "
        "WHERE symbol = %s AND timeframe = 'M15' AND ts >= %s "
        "ORDER BY ts ASC LIMIT %s",
        (symbol, when, max(1, int(limit))),
    )
    highs: list[float] = []
    lows: list[float] = []
    for high, low in cur.fetchall():
        h, lo = _f(high), _f(low)
        if h is not None:
            highs.append(h)
        if lo is not None:
            lows.append(lo)
    return highs, lows

