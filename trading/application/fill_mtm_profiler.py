"""Fill-side mark-to-market at +5/+15/+30/+60 min. Read-only: no orders.

Usage:
  python -m trading.application.fill_mtm_profiler --since 2026-10-01
  python -m trading.application.fill_mtm_profiler --since 2026-10-01 --no-mt5
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from trading.application.mae_profiler import (
    INVERT_SINCE,
    SignalEvent,
    default_log_paths,
    fetch_m1_mt5,
    invert_since_iso,
    iter_rotation_log_events,
    load_bars_json,
    pair_signals,
    usd_per_price_from_ticket,
)
from trading.domain.fill_mtm import mtm_at_horizon
from trading.domain.mae_excursion import MaeBar
from trading.domain.rotation_stale import stale_rotation_close_reason

ROOT = Path(__file__).resolve().parents[2]
HORIZONS_SEC = (300, 900, 1800, 3600)


def _median(values: Sequence[float]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not nums:
        return None
    nums.sort()
    return nums[len(nums) // 2]


def _mean(values: Sequence[float]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 6)


def _usd_for(row: SignalEvent, usd_1lot: dict[str, float]) -> float:
    tick = (usd_1lot.get(row.symbol) or 0.0) * float(row.lot or 0.0)
    if tick > 0.0:
        return tick
    return usd_per_price_from_ticket(
        risk_usd=row.risk_usd,
        stop_dist=row.stop_dist,
        lot=row.lot,
    )


def attach_fill_mtm(
    rows: Sequence[SignalEvent],
    bars_for: Callable[[str], Sequence[MaeBar]],
    *,
    horizons_sec: Sequence[int] = HORIZONS_SEC,
    usd_lookup: Optional[Callable[[SignalEvent], float]] = None,
) -> list[SignalEvent]:
    cache: dict[str, Sequence[MaeBar]] = {}
    for row in rows:
        if not row.filled or row.fill_ts is None or not row.ref:
            continue
        if row.symbol not in cache:
            cache[row.symbol] = list(bars_for(row.symbol) or ())
        bars = cache[row.symbol]
        usd = float(usd_lookup(row) or 0.0) if usd_lookup is not None else 0.0
        if usd <= 0.0:
            usd = _usd_for(row, {})
        fill_ts = row.fill_ts.timestamp()
        exec_side = row.exec_side or row.side
        missing_any = False
        for win in horizons_sec:
            fill = mtm_at_horizon(
                bars,
                side=exec_side,
                entry=float(row.ref),
                fill_ts=fill_ts,
                horizon_seconds=float(win),
                usd_per_price=usd,
            )
            vote = mtm_at_horizon(
                bars,
                side=row.side,
                entry=float(row.ref),
                fill_ts=fill_ts,
                horizon_seconds=float(win),
                usd_per_price=usd,
            )
            stale = None
            if fill.pnl_usd is not None:
                stale = stale_rotation_close_reason(
                    age_seconds=float(win),
                    profit=float(fill.pnl_usd),
                    min_age_seconds=float(win),
                )
            row.windows[int(win)] = {
                "missing": fill.missing,
                "reason": fill.reason,
                "n_bars": fill.n_bars,
                "fill_side": exec_side,
                "vote_side": row.side,
                "fill_pnl_usd": fill.pnl_usd,
                "vote_pnl_usd": vote.pnl_usd,
                "stale_if_aged": stale,
            }
            if fill.missing:
                missing_any = True
        row.missing_bars = missing_any
    return list(rows)


def summarize_fill_mtm(
    rows: Sequence[SignalEvent],
    *,
    horizons_sec: Sequence[int] = HORIZONS_SEC,
) -> dict[str, Any]:
    filled = [r for r in rows if r.filled]

    def cohort(picked: Sequence[SignalEvent]) -> dict[str, Any]:
        payload: dict[str, Any] = {"n": len(picked), "horizons": {}}
        for win in horizons_sec:
            pnls: list[float] = []
            vote_pnls: list[float] = []
            missing = 0
            stale_take = 0
            stale_loss = 0
            stay = 0
            for r in picked:
                w = r.windows.get(int(win)) or {}
                if w.get("missing") or w.get("fill_pnl_usd") is None:
                    missing += 1
                    continue
                pnl = float(w["fill_pnl_usd"])
                pnls.append(pnl)
                if w.get("vote_pnl_usd") is not None:
                    vote_pnls.append(float(w["vote_pnl_usd"]))
                reason = w.get("stale_if_aged")
                if reason == "stale_take":
                    stale_take += 1
                elif reason == "stale_loss":
                    stale_loss += 1
                else:
                    stay += 1
            n_ok = len(pnls)
            greens = sum(1 for p in pnls if p > 0.0)
            payload["horizons"][str(int(win))] = {
                "n_ok": n_ok,
                "n_missing": missing,
                "pct_green": round(greens / n_ok, 4) if n_ok else None,
                "mean_usd": _mean(pnls),
                "median_usd": _median(pnls),
                "vote_mean_usd": _mean(vote_pnls),
                "stale_take_n": stale_take,
                "stale_loss_n": stale_loss,
                "stale_stay_n": stay,
                "time_stop_ev": _mean(pnls),
            }
        return payload

    groups = {
        "filled": filled,
        "pre_invert_filled": [r for r in filled if not r.inverted_cohort],
        "invert_filled": [r for r in filled if r.inverted_cohort],
    }
    report = {
        "n_filled": len(filled),
        "invert_since": invert_since_iso(INVERT_SINCE),
        "note": (
            "fill_pnl is the executed ticket. vote_pnl uses the original 4/5 side "
            "at the same entry. Pre-invert is the sample for live (invert off). "
            "time_stop_ev = mean mark-to-market if every fill closed at that horizon."
        ),
        "cohorts": {name: cohort(picked) for name, picked in groups.items()},
    }
    pre = report["cohorts"]["pre_invert_filled"]["horizons"]
    ranked = []
    for key, blob in pre.items():
        if blob.get("mean_usd") is None or blob.get("n_ok", 0) < 30:
            continue
        ranked.append((blob["mean_usd"], int(key), blob.get("pct_green")))
    ranked.sort(reverse=True)
    report["recommended_time_stop_seconds"] = ranked[0][1] if ranked else None
    report["recommended_from"] = "pre_invert_filled mean_usd (n_ok>=30)"
    return report


def run_fill_mtm(
    *,
    log_paths: Sequence[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    bars_by_symbol: Optional[dict[str, list[MaeBar]]] = None,
    use_mt5: bool = False,
) -> dict[str, Any]:
    events = list(iter_rotation_log_events(log_paths, since=since, until=until))
    events.sort(
        key=lambda e: (
            e.get("_dt") or datetime.min,
            0 if e.get("event") == "ENSEMBLE_SIGNAL" else 1,
            str(e.get("symbol") or ""),
        )
    )
    rows = pair_signals(events)
    filled = [r for r in rows if r.filled and r.fill_ts is not None]
    symbols = sorted({r.symbol for r in filled})
    bars = dict(bars_by_symbol or {})
    usd_1lot: dict[str, float] = {}
    usd_lookup: Optional[Callable[[SignalEvent], float]] = None
    mt5_error = ""
    if use_mt5 and filled:
        try:
            fetched, usd_1lot = fetch_m1_mt5(
                symbols,
                start=min(r.fill_ts or r.ts for r in filled),
                end=max(r.fill_ts or r.ts for r in filled) + timedelta(minutes=65),
            )
            bars.update(fetched)
            usd_lookup = lambda row, _u=usd_1lot: _usd_for(row, _u)
        except Exception as exc:
            mt5_error = str(exc)
    attach_fill_mtm(filled, lambda sym: bars.get(sym) or [], usd_lookup=usd_lookup)
    report = summarize_fill_mtm(filled)
    report["mt5_error"] = mt5_error
    report["n_symbols"] = len(symbols)
    report["n_symbols_with_bars"] = sum(1 for s in symbols if bars.get(s))
    report["n_m1_bars"] = sum(len(v) for v in bars.values())
    report["n_missing_bars"] = sum(1 for r in filled if r.missing_bars)
    return report


def _parse_cli_dt(text: str) -> datetime:
    raw = str(text).strip()
    if len(raw) == 10:
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.fromisoformat(raw[:19])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill-side MTM at 5/15/30/60 minutes")
    parser.add_argument("--since", default="2026-10-01")
    parser.add_argument("--until", default="")
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--bars-json", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--no-mt5", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    since = _parse_cli_dt(args.since)
    until = _parse_cli_dt(args.until) if str(args.until).strip() else None
    logs = [Path(p) for p in args.log] if args.log else default_log_paths()
    bars = load_bars_json(Path(args.bars_json)) if str(args.bars_json).strip() else None
    use_mt5 = (not args.no_mt5) and not bars
    report = run_fill_mtm(
        log_paths=logs,
        since=since,
        until=until,
        bars_by_symbol=bars,
        use_mt5=use_mt5,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if str(args.out).strip():
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
