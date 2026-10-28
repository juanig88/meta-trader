"""Persist ensemble vote snapshots. Fail-open if Postgres is down."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Sequence

from trading.infrastructure.data_layer_config import DataLayerConfigError, PgSettings

ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS ensemble_votes (
    id              bigserial    PRIMARY KEY,
    ts              timestamptz  NOT NULL DEFAULT now(),
    symbol          text         NOT NULL,
    direction       text         NOT NULL,
    buy_count       integer      NOT NULL,
    sell_count      integer      NOT NULL,
    none_count      integer      NOT NULL,
    fired           boolean      NOT NULL,
    action          text         NOT NULL,
    risk_usd        double precision,
    lot             double precision,
    skip_reason     text,
    dry_run         boolean      NOT NULL DEFAULT true,
    votes           jsonb        NOT NULL,
    murphy          jsonb,
    alpha           jsonb,
    state           jsonb
);
ALTER TABLE ensemble_votes ADD COLUMN IF NOT EXISTS murphy jsonb;
ALTER TABLE ensemble_votes ADD COLUMN IF NOT EXISTS alpha jsonb;
ALTER TABLE ensemble_votes ADD COLUMN IF NOT EXISTS state jsonb;
CREATE INDEX IF NOT EXISTS ensemble_votes_symbol_ts_idx ON ensemble_votes (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS ensemble_votes_ts_idx ON ensemble_votes (ts DESC);
"""

WAREHOUSE_EVENTS = frozenset(
    {
        "ENSEMBLE_SIGNAL",
        "TRADE_OPEN",
        "TRADE_CLOSE",
        "ROTATION_OPEN_REJECT",
        "ROTATION_STALE_CLOSE",
        "ROTATION_BOOK_HARVEST",
        "KILL_SWITCH_FLATTEN",
        "KILL_SWITCH_HALT",
    }
)

ENSURE_TRADE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS trade_events (
    id      bigserial    PRIMARY KEY,
    ts      timestamptz  NOT NULL DEFAULT now(),
    event   text         NOT NULL,
    symbol  text,
    ticket  bigint,
    magic   bigint,
    payload jsonb        NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_events_ts_idx ON trade_events (ts DESC);
CREATE INDEX IF NOT EXISTS trade_events_event_idx ON trade_events (event, ts DESC);
CREATE INDEX IF NOT EXISTS trade_events_symbol_idx ON trade_events (symbol, ts DESC);
"""


def insert_ensemble_votes(rows: Sequence[dict], *, dry_run: bool = True) -> int:
    if not rows:
        return 0
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError:
        return 0
    try:
        import psycopg

        with psycopg.connect(settings.conninfo(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(ENSURE_SQL)
                inserted = 0
                for row in rows:
                    cons = row.get("consensus") or {}
                    size = row.get("size") or {}
                    cur.execute(
                        "INSERT INTO ensemble_votes "
                        "(ts, symbol, direction, buy_count, sell_count, none_count, fired, "
                        "action, risk_usd, lot, skip_reason, dry_run, votes, murphy, alpha, state) "
                        "VALUES (COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            row.get("ts"),
                            str(row.get("symbol") or ""),
                            str(cons.get("direction") or row.get("direction") or "NONE"),
                            int(cons.get("buy") or 0),
                            int(cons.get("sell") or 0),
                            int(cons.get("none") or 0),
                            bool(cons.get("fired")),
                            str(row.get("action") or "SKIP"),
                            size.get("risk_usd"),
                            size.get("lot"),
                            str(row.get("skip_reason") or "") or None,
                            bool(dry_run),
                            json.dumps(row.get("votes") or []),
                            json.dumps(row.get("murphy")) if row.get("murphy") is not None else None,
                            json.dumps(row.get("alpha")) if row.get("alpha") is not None else None,
                            json.dumps(row.get("state")) if row.get("state") is not None else None,
                        ),
                    )
                    inserted += 1
            conn.commit()
            return inserted
    except Exception:
        return 0


def _parse_event_ts(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw
    text = str(raw or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _opt_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def insert_trade_event(payload: dict[str, Any]) -> int:
    """Sparse rotation events into trade_events. Fail-open. Never blocks the send path long."""
    if not isinstance(payload, dict):
        return 0
    event = str(payload.get("event") or "")
    if event not in WAREHOUSE_EVENTS:
        return 0
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError:
        return 0
    try:
        import psycopg

        with psycopg.connect(settings.conninfo(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(ENSURE_TRADE_EVENTS_SQL)
                cur.execute(
                    "INSERT INTO trade_events (ts, event, symbol, ticket, magic, payload) "
                    "VALUES (COALESCE(%s, now()), %s, %s, %s, %s, %s)",
                    (
                        _parse_event_ts(payload.get("ts") or payload.get("timestamp")),
                        event,
                        str(payload.get("symbol") or "") or None,
                        _opt_int(payload.get("ticket") or payload.get("position_id")),
                        _opt_int(payload.get("magic")),
                        json.dumps(payload, default=str),
                    ),
                )
            conn.commit()
            return 1
    except Exception:
        return 0


def fetch_latest_ensemble(*, limit: int = 80) -> dict[str, Any]:
    empty: dict[str, Any] = {"latest": [], "error": None}
    try:
        settings = PgSettings.from_env()
    except DataLayerConfigError as exc:
        empty["error"] = str(exc)
        return empty
    try:
        import psycopg

        with psycopg.connect(settings.conninfo(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(ENSURE_SQL)
                cur.execute(
                    "SELECT DISTINCT ON (symbol) "
                    "ts, symbol, direction, buy_count, sell_count, none_count, fired, "
                    "action, risk_usd, lot, skip_reason, dry_run, votes, murphy, alpha, state "
                    "FROM ensemble_votes ORDER BY symbol, ts DESC"
                )
                latest = []
                for row in cur.fetchall():
                    votes = row[12]
                    if isinstance(votes, str):
                        try:
                            votes = json.loads(votes)
                        except json.JSONDecodeError:
                            votes = []
                    murphy = row[13] if len(row) > 13 else None
                    if isinstance(murphy, str):
                        try:
                            murphy = json.loads(murphy)
                        except json.JSONDecodeError:
                            murphy = None
                    alpha = row[14] if len(row) > 14 else None
                    if isinstance(alpha, str):
                        try:
                            alpha = json.loads(alpha)
                        except json.JSONDecodeError:
                            alpha = None
                    state = row[15] if len(row) > 15 else None
                    if isinstance(state, str):
                        try:
                            state = json.loads(state)
                        except json.JSONDecodeError:
                            state = None
                    ts = row[0]
                    latest.append(
                        {
                            "ts": ts.isoformat(timespec="seconds") if isinstance(ts, datetime) else str(ts),
                            "symbol": row[1],
                            "direction": row[2],
                            "buy": row[3],
                            "sell": row[4],
                            "none": row[5],
                            "fired": row[6],
                            "action": row[7],
                            "risk_usd": row[8],
                            "lot": row[9],
                            "skip_reason": row[10] or "",
                            "dry_run": row[11],
                            "votes": votes or [],
                            "murphy": murphy,
                            "alpha": alpha,
                            "state": state,
                        }
                    )
                latest.sort(key=lambda r: (-int(r.get("fired") or 0), str(r.get("symbol"))))
                return {"latest": latest[: max(1, int(limit))], "error": None}
    except Exception as exc:
        empty["error"] = str(exc)
        return empty
