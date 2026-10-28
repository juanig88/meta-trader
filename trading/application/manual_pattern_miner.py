"""Journal closed manual (magic 0) trades and describe repeated signatures.

Read-only. It does not import bot_mt5 and it does not send orders. q and the
factor snapshot are context for later review. They are not a Kelly input and
they do not open trades.

`--once` and `--report` still run on demand. `--follow` and
`manual_miner_daemon.py` poll XBTFX on an interval and write an analytical
summary only when the journal crosses a threshold.

Usage:
    py -m trading.application.manual_pattern_miner --once
    py -m trading.application.manual_pattern_miner --report
    py -m trading.application.manual_pattern_miner --follow
    py manual_miner_daemon.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from indicators import atr as atr_series

from trading.domain.manual_journal import (
    context_from_bars,
    count_open_manual,
    dashboard_slice,
    compare_winners_to_alpha,
    format_alpha_comparison,
    format_pattern_report,
    journal_record,
    manual_round_trips,
    pattern_report,
    ReportCursor,
    should_emit_report,
)
from trading.infrastructure.async_runtime import run as run_async
from trading.infrastructure.data_layer_config import (
    DataLayerConfigError,
    ManualMinerSettings,
    PgSettings,
)
from trading.infrastructure.persistence.pg import PgStore, PgUnavailable

logger = logging.getLogger(__name__)


def _setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    root.addHandler(handler)


def atr_of_bars(bars: Sequence[Any]) -> Optional[float]:
    if len(bars) < 16:
        return None
    series = atr_series(
        [float(b.high) for b in bars],
        [float(b.low) for b in bars],
        [float(b.close) for b in bars],
        14,
    )
    last = series[-1] if series else None
    if last is None or float(last) <= 0.0:
        return None
    return float(last)


def load_dashboard(path: str) -> tuple[Optional[dict], Optional[datetime]]:
    """Read bot_state.json. Returns (state, mtime) or (None, None)."""
    file = Path(path)
    if not file.is_file() or file.suffix.lower() != ".json":
        return None, None
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    try:
        observed = datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc)
    except OSError:
        observed = None
    return payload, observed


async def journal_deals(
    store: PgStore,
    deals: Sequence[Mapping[str, Any]],
    *,
    settings: ManualMinerSettings,
    dashboard_state: Optional[Mapping[str, Any]] = None,
    dashboard_observed_at: Optional[datetime] = None,
) -> list[dict]:
    """Build and upsert journal rows for closed manual round-trips."""
    trips = manual_round_trips(deals, bot_magics=settings.bot_magics)
    records: list[dict] = []
    for trip in trips:
        bars: list = []
        alpha: Optional[dict] = None
        if trip.opened_at is not None:
            try:
                bars = await store.fetch_bars_asof(
                    trip.symbol,
                    settings.timeframe,
                    trip.opened_at,
                    settings.bars,
                )
            except PgUnavailable as exc:
                logger.warning("miner: bars unavailable for %s (%s)", trip.symbol, exc)
            try:
                alpha = await store.fetch_alpha_asof(trip.symbol, trip.opened_at)
            except PgUnavailable as exc:
                logger.warning("miner: alpha unavailable for %s (%s)", trip.symbol, exc)
                alpha = None
        context = context_from_bars(
            bars,
            opened_at=trip.opened_at,
            symbol=trip.symbol,
            groups=settings.correlation_groups,
            atr_value=atr_of_bars(bars),
            alpha_direction=None if not alpha else alpha.get("direction"),
            alpha_score=None if not alpha else alpha.get("score"),
            alpha_q=None if not alpha else alpha.get("q"),
            dashboard=dashboard_slice(
                dashboard_state,
                trip.symbol,
                observed_at=dashboard_observed_at,
                closed_at=trip.closed_at,
                max_lag_seconds=settings.dashboard_max_lag_seconds,
            ),
            zscore_lookback=settings.zscore_lookback,
        )
        records.append(journal_record(trip, context))
    if records:
        await store.upsert_manual_journal(records)
    return records


async def render_report(store: PgStore, *, min_n: int) -> str:
    rows = await store.list_manual_journal(2000)
    body = format_pattern_report(pattern_report(rows, min_n=min_n))
    return body + "\n" + format_alpha_comparison(compare_winners_to_alpha(rows))


async def maybe_publish_report(
    store: PgStore,
    settings: ManualMinerSettings,
    cursor: ReportCursor,
) -> ReportCursor:
    """Persist and log a summary when the sample crosses a threshold."""
    n_trades, n_winners = await store.count_manual_journal()
    trigger = should_emit_report(
        cursor,
        n_trades,
        n_winners,
        every_n=settings.report_every_n,
        stat_min=settings.stat_min_winners,
    )
    if not trigger:
        return cursor
    rows = await store.list_manual_journal(2000)
    comparison = compare_winners_to_alpha(rows)
    text = format_pattern_report(pattern_report(rows, min_n=settings.pattern_min_n))
    text = text + "\n" + format_alpha_comparison(comparison)
    await store.insert_miner_report(
        trigger=trigger,
        n_trades=n_trades,
        n_winners=n_winners,
        n_new=max(0, n_trades - cursor.n_trades),
        summary=text,
        comparison=comparison.as_dict(),
    )
    logger.info(json.dumps({
        "event": "MANUAL_MINER_REPORT",
        "trigger": trigger,
        "n_trades": n_trades,
        "n_winners": n_winners,
        "refit_applied": False,
    }))
    logger.info("\n%s", text)
    return ReportCursor(
        n_trades=n_trades,
        n_winners=n_winners,
        stat_emitted=cursor.stat_emitted or "stat_min" in trigger,
    )


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, float(seconds)))
    except (asyncio.TimeoutError, TimeoutError):
        pass


async def follow(store: PgStore, settings: ManualMinerSettings, stop: asyncio.Event) -> None:
    """Poll XBTFX until stop is set. A down database or terminal is a warning."""
    try:
        await store.ensure_miner_reports_table()
        n_trades, n_winners, stat_emitted = await store.latest_miner_report_cursor()
        cursor = ReportCursor(n_trades=n_trades, n_winners=n_winners, stat_emitted=stat_emitted)
    except PgUnavailable as exc:
        logger.warning("miner: report table unavailable (%s)", exc)
        cursor = ReportCursor()
    logger.info(json.dumps({
        "event": "MANUAL_MINER_START",
        "interval_seconds": settings.interval_seconds,
        "report_every_n": settings.report_every_n,
        "stat_min_winners": settings.stat_min_winners,
        "cursor_trades": cursor.n_trades,
    }))
    while not stop.is_set():
        summary = await _ingest_window(store, settings)
        try:
            cursor = await maybe_publish_report(store, settings, cursor)
            n_trades, n_winners = await store.count_manual_journal()
        except PgUnavailable as exc:
            logger.warning("miner: report skipped (%s)", exc)
            n_trades, n_winners = 0, 0
        logger.info(json.dumps({
            "event": "MANUAL_MINER_HEARTBEAT",
            "ok": bool(summary.get("ok")),
            "written": summary.get("written", 0),
            "open_manual": summary.get("open_manual", 0),
            "n_trades": n_trades,
            "n_winners": n_winners,
        }))
        await _sleep_or_stop(stop, settings.interval_seconds)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Read-only manual trade pattern miner.")
    ap.add_argument("--once", action="store_true", help="Ingest the lookback window once, then report.")
    ap.add_argument("--report", action="store_true", help="Print the stored pattern report and exit.")
    ap.add_argument("--follow", action="store_true", help="Keep polling MT5 until interrupted.")
    ap.add_argument("--lookback-days", type=int, default=None)
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass


async def _ingest_window(store: PgStore, settings: ManualMinerSettings) -> dict:
    from trading.infrastructure.mt5_deals import fetch_deals_and_positions

    end = datetime.now(timezone.utc) + timedelta(hours=12)
    start = end - timedelta(days=int(settings.lookback_days) + 1)
    deals, positions, err = fetch_deals_and_positions(start, end)
    if err:
        logger.error("miner: %s", err)
        return {"ok": False, "error": err, "written": 0, "open_manual": 0}
    state, observed = load_dashboard(settings.dashboard_path)
    try:
        records = await journal_deals(
            store,
            deals,
            settings=settings,
            dashboard_state=state,
            dashboard_observed_at=observed,
        )
    except PgUnavailable as exc:
        logger.warning("miner: postgres write failed (%s)", exc)
        return {"ok": False, "error": str(exc), "written": 0, "open_manual": count_open_manual(positions)}
    summary = {
        "ok": True,
        "error": "",
        "written": len(records),
        "open_manual": count_open_manual(positions),
        "winners": sum(1 for r in records if r.get("winner")),
    }
    logger.info(json.dumps({"event": "MANUAL_JOURNAL", **summary}, default=str))
    return summary


async def _amain(args: argparse.Namespace) -> int:
    try:
        pg = PgSettings.from_env()
    except DataLayerConfigError as exc:
        logger.error("postgres disabled: %s", exc)
        return 2
    settings = ManualMinerSettings.from_env()
    if args.lookback_days is not None:
        settings = ManualMinerSettings(
            **{**settings.__dict__, "lookback_days": max(1, int(args.lookback_days))}
        )
    store = PgStore(pg)
    try:
        if not await store.ensure_open():
            logger.error("postgres unavailable")
            return 2
        if args.follow:
            stop = asyncio.Event()
            await follow(store, settings, stop)
            return 0
        report_only = bool(args.report) and not args.once
        if not report_only:
            await _ingest_window(store, settings)
        try:
            logger.info("\n%s", await render_report(store, min_n=settings.pattern_min_n))
        except PgUnavailable as exc:
            logger.error("miner: report failed (%s)", exc)
            return 2
        return 0
    finally:
        await store.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    _load_dotenv()
    _setup_logging(args.log_level)
    if not args.once and not args.report and not args.follow:
        args.once = True
    try:
        return run_async(lambda: _amain(args))
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
