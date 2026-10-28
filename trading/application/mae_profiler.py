"""MAE autopsy of 4/5 signals vs later M1 bars. Read-only: no orders.

Usage:
  python -m trading.application.mae_profiler --since 2026-10-01
  python -m trading.application.mae_profiler --since 2026-10-01 --no-mt5
  python -m trading.application.mae_profiler --bars-json path.json --since 2026-10-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from trading.domain.mae_excursion import (
    DEFAULT_M1_PERIOD,
    MaeBar,
    MaeWindow,
    excursion_after_signal,
    original_decision_side,
    usd_per_price_from_ticket,
)

ROOT = Path(__file__).resolve().parents[2]
INVERT_SINCE = datetime(2026, 10, 1, 14, 36, 0)
WINDOWS_SEC = (60, 120, 180, 300)
JOIN_SECONDS = 120.0
LOG_EVENTS = frozenset({"ENSEMBLE_SIGNAL", "TRADE_OPEN", "ROTATION_OPEN_REJECT"})
ENGINE_ROTATION = "rotation"


@dataclass
class SignalEvent:
    ts: datetime
    symbol: str
    side: str
    filled: bool
    rejected: bool
    reject_reason: str
    fill_ts: Optional[datetime]
    fill_latency_s: Optional[float]
    ref: Optional[float]
    lot: Optional[float]
    risk_usd: Optional[float]
    stop_dist: Optional[float]
    skip_reason: str
    inverted_cohort: bool
    exec_side: str = ""
    windows: dict[int, dict[str, Any]] = field(default_factory=dict)
    missing_bars: bool = False


def _parse_dt(value: object, line_prefix: str = "") -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text[:19])
        except ValueError:
            pass
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    prefix = str(line_prefix or "")[:19]
    if len(prefix) >= 19:
        try:
            return datetime.strptime(prefix.replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


def _json_from_line(line: str) -> Optional[dict[str, Any]]:
    i = line.find("{")
    if i < 0:
        return None
    try:
        obj = json.loads(line[i:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def iter_rotation_log_events(
    paths: Sequence[Path],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Iterable[dict[str, Any]]:
    """Yield ENSEMBLE_SIGNAL / TRADE_OPEN / ROTATION_OPEN_REJECT from bot logs."""
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not any(name in line for name in LOG_EVENTS):
                    continue
                obj = _json_from_line(line)
                if not obj:
                    continue
                ev = str(obj.get("event") or "")
                if ev not in LOG_EVENTS:
                    continue
                dt = _parse_dt(obj.get("ts") or obj.get("opened_at"), line)
                if dt is None:
                    continue
                if since is not None and dt < since:
                    continue
                if until is not None and dt > until:
                    continue
                engine = str(obj.get("engine") or "")
                if ev == "TRADE_OPEN" and engine and engine != ENGINE_ROTATION:
                    continue
                obj["_dt"] = dt
                yield obj


def default_log_paths(root: Path = ROOT) -> list[Path]:
    out: list[Path] = []
    main = root / "trades.log"
    if main.is_file():
        out.append(main)
    log_dir = root / "logs"
    if log_dir.is_dir():
        out.extend(sorted(log_dir.glob("trades_*.log")))
    return out


def _f(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pair_signals(
    events: Sequence[dict[str, Any]],
    *,
    invert_since: datetime = INVERT_SINCE,
    join_seconds: float = JOIN_SECONDS,
) -> list[SignalEvent]:
    """FIFO-join 4/5 attempts to fills/rejects. Side is the original vote."""
    signals: list[SignalEvent] = []
    unused: dict[str, list[int]] = defaultdict(list)
    for ev in events:
        name = str(ev.get("event") or "")
        dt = ev.get("_dt")
        if not isinstance(dt, datetime):
            continue
        symbol = str(ev.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if name == "ENSEMBLE_SIGNAL":
            side = original_decision_side(
                side_decision=ev.get("side_decision"),
                side=ev.get("side"),
            )
            if side not in ("BUY", "SELL"):
                continue
            exec_side = original_decision_side(side=ev.get("side_exec")) or side
            row = SignalEvent(
                ts=dt,
                symbol=symbol,
                side=side,
                filled=False,
                rejected=False,
                reject_reason="",
                fill_ts=None,
                fill_latency_s=None,
                ref=_f(ev.get("price") or ev.get("price_open")),
                lot=_f(ev.get("lot")),
                risk_usd=_f(ev.get("risk_usd")),
                stop_dist=_f(ev.get("stop_dist")),
                skip_reason=str(ev.get("skip_reason") or ""),
                inverted_cohort=dt >= invert_since,
                exec_side=exec_side,
            )
            unused[symbol].append(len(signals))
            signals.append(row)
            continue
        if name not in ("TRADE_OPEN", "ROTATION_OPEN_REJECT"):
            continue
        pending = unused.get(symbol) or []
        match_i: Optional[int] = None
        for idx in pending:
            delta = (dt - signals[idx].ts).total_seconds()
            if 0.0 <= delta <= float(join_seconds):
                match_i = idx
                break
        if match_i is None:
            if name != "TRADE_OPEN":
                continue
            side = original_decision_side(
                side_decision=ev.get("side_decision"),
                side=ev.get("side"),
            )
            if side not in ("BUY", "SELL"):
                continue
            exec_side = (
                original_decision_side(side=ev.get("side_exec"))
                or original_decision_side(side=ev.get("side"))
                or side
            )
            row = SignalEvent(
                ts=dt,
                symbol=symbol,
                side=side,
                filled=True,
                rejected=False,
                reject_reason="",
                fill_ts=dt,
                fill_latency_s=0.0,
                ref=_f(ev.get("price") or ev.get("price_open")),
                lot=_f(ev.get("lot") or ev.get("volume")),
                risk_usd=_f(ev.get("risk_usd")),
                stop_dist=_f(ev.get("stop_dist")),
                skip_reason="",
                inverted_cohort=dt >= invert_since,
                exec_side=exec_side,
            )
            signals.append(row)
            continue
        pending.remove(match_i)
        row = signals[match_i]
        if name == "TRADE_OPEN":
            row.filled = True
            row.fill_ts = dt
            row.fill_latency_s = (dt - row.ts).total_seconds()
            row.ref = _f(ev.get("price") or ev.get("price_open")) or row.ref
            row.lot = _f(ev.get("lot") or ev.get("volume")) or row.lot
            row.risk_usd = _f(ev.get("risk_usd")) or row.risk_usd
            row.stop_dist = _f(ev.get("stop_dist")) or row.stop_dist
            fill_side = original_decision_side(side=ev.get("side"))
            if fill_side in ("BUY", "SELL"):
                row.exec_side = fill_side
        else:
            row.rejected = True
            row.reject_reason = str(ev.get("reason") or ev.get("skip_reason") or "")
    return signals


def _median(values: Sequence[float]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not nums:
        return None
    nums.sort()
    return nums[len(nums) // 2]


def attach_mae(
    rows: Sequence[SignalEvent],
    bars_for: Callable[[str], Sequence[MaeBar]],
    *,
    windows_sec: Sequence[int] = WINDOWS_SEC,
    period_seconds: float = DEFAULT_M1_PERIOD,
    usd_lookup: Optional[Callable[[SignalEvent], float]] = None,
) -> list[SignalEvent]:
    cache: dict[str, Sequence[MaeBar]] = {}
    for row in rows:
        if row.symbol not in cache:
            cache[row.symbol] = list(bars_for(row.symbol) or ())
        bars = cache[row.symbol]
        usd = 0.0
        if usd_lookup is not None:
            usd = float(usd_lookup(row) or 0.0)
        if usd <= 0.0:
            usd = usd_per_price_from_ticket(
                risk_usd=row.risk_usd,
                stop_dist=row.stop_dist,
                lot=row.lot,
            )
        signal_ts = row.ts.timestamp()
        missing_any = False
        for win in windows_sec:
            result: MaeWindow = excursion_after_signal(
                bars,
                side=row.side,
                signal_ts=signal_ts,
                window_seconds=float(win),
                period_seconds=period_seconds,
                ref=row.ref,
                usd_per_price=usd,
            )
            row.windows[int(win)] = {
                "missing": result.missing,
                "reason": result.reason,
                "n_bars": result.n_bars,
                "ref": result.ref,
                "mae": result.mae,
                "mfe": result.mfe,
                "mae_usd": result.mae_usd,
                "mfe_usd": result.mfe_usd,
                "mae_gt_mfe": result.mae_gt_mfe,
                "ambiguous": result.ambiguous,
            }
            if result.missing:
                missing_any = True
        row.missing_bars = missing_any
    return list(rows)


def summarize(rows: Sequence[SignalEvent], *, windows_sec: Sequence[int] = WINDOWS_SEC) -> dict[str, Any]:
    def cohort(name: str, picked: Sequence[SignalEvent]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "n": len(picked),
            "n_filled": sum(1 for r in picked if r.filled),
            "n_unfilled": sum(1 for r in picked if not r.filled),
            "fill_latency_median_s": _median(
                [float(r.fill_latency_s) for r in picked if r.fill_latency_s is not None]
            ),
            "windows": {},
        }
        lat = [float(r.fill_latency_s) for r in picked if r.fill_latency_s is not None]
        if lat:
            payload["fill_same_m1"] = sum(1 for s in lat if s < 60.0)
            payload["fill_1_2_m1_late"] = sum(1 for s in lat if 60.0 <= s < 180.0)
        for win in windows_sec:
            ok = []
            mae_usd = []
            mfe_usd = []
            mae_gt = 0
            amb = 0
            missing = 0
            for r in picked:
                w = r.windows.get(int(win)) or {}
                if w.get("missing"):
                    missing += 1
                    continue
                ok.append(r)
                if w.get("mae_gt_mfe"):
                    mae_gt += 1
                if w.get("ambiguous"):
                    amb += 1
                if w.get("mae_usd") is not None:
                    mae_usd.append(float(w["mae_usd"]))
                if w.get("mfe_usd") is not None:
                    mfe_usd.append(float(w["mfe_usd"]))
            n_ok = len(ok)
            payload["windows"][str(int(win))] = {
                "n_ok": n_ok,
                "n_missing": missing,
                "pct_mae_gt_mfe": round(mae_gt / n_ok, 4) if n_ok else None,
                "mae_usd_median": _median(mae_usd),
                "mfe_usd_median": _median(mfe_usd),
                "n_ambiguous": amb,
            }
        return payload

    groups = {
        "all_passed_pullback": list(rows),
        "pre_invert": [r for r in rows if not r.inverted_cohort],
        "invert": [r for r in rows if r.inverted_cohort],
        "filled": [r for r in rows if r.filled],
        "unfilled": [r for r in rows if not r.filled],
        "pre_invert_filled": [r for r in rows if (not r.inverted_cohort) and r.filled],
        "invert_filled": [r for r in rows if r.inverted_cohort and r.filled],
    }
    return {
        "n": len(rows),
        "invert_since": invert_since_iso(INVERT_SINCE),
        "risk_note": "Kelly cap 0.50 USD per ticket (0.005% of 10k demo) when risk_usd is present.",
        "cohorts": {name: cohort(name, picked) for name, picked in groups.items()},
    }


def invert_since_iso(when: datetime = INVERT_SINCE) -> str:
    return when.isoformat(sep=" ", timespec="seconds")


def bars_from_records(records: Sequence[dict[str, Any]]) -> list[MaeBar]:
    out: list[MaeBar] = []
    for rec in records:
        ts = rec.get("time")
        if ts is None:
            ts = rec.get("ts")
        t: Optional[float]
        if isinstance(ts, (int, float)):
            t = float(ts)
        else:
            dt = _parse_dt(ts)
            t = dt.timestamp() if dt else None
        if t is None:
            continue
        try:
            out.append(
                MaeBar(
                    time=t,
                    high=float(rec["high"]),
                    low=float(rec["low"]),
                    close=float(rec["close"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda b: b.time)
    return out


def load_bars_json(path: Path) -> dict[str, list[MaeBar]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    blob = raw.get("bars") if isinstance(raw, dict) else raw
    if not isinstance(blob, dict):
        return {}
    return {
        str(sym).strip().upper(): bars_from_records(rows)
        for sym, rows in blob.items()
        if isinstance(rows, list)
    }


def fetch_m1_mt5(
    symbols: Sequence[str],
    *,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, list[MaeBar]], dict[str, float]]:
    """Read M1 from the already-open XBTFX terminal. Does not send orders."""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass
    import os

    import MetaTrader5 as mt5

    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]
    server = os.environ["MT5_SERVER"]
    if not mt5.initialize(login=login, password=password, server=server):
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
    out: dict[str, list[MaeBar]] = {}
    usd_1lot: dict[str, float] = {}
    pad_end = end + timedelta(minutes=6)
    pad_start = start - timedelta(minutes=6)
    for symbol in symbols:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is not None:
            usd_1lot[symbol] = usd_per_price_from_ticket(
                lot=1.0,
                tick_value=getattr(info, "trade_tick_value", None),
                tick_size=getattr(info, "trade_tick_size", None),
            )
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, pad_start, pad_end)
        if rates is None or len(rates) == 0:
            out[symbol] = []
            continue
        bars: list[MaeBar] = []
        for row in rates:
            bars.append(
                MaeBar(
                    time=float(row["time"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
        bars.sort(key=lambda b: b.time)
        out[symbol] = bars
    # Do not mt5.shutdown(): the live bot / dashboard may share this terminal.
    return out, usd_1lot


def run_profiler(
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
    symbols = sorted({r.symbol for r in rows})
    bars = dict(bars_by_symbol or {})
    usd_1lot: dict[str, float] = {}
    usd_lookup: Optional[Callable[[SignalEvent], float]] = None
    mt5_error = ""
    if use_mt5 and rows:
        try:
            fetched, usd_1lot = fetch_m1_mt5(
                symbols,
                start=min(r.ts for r in rows),
                end=max((r.fill_ts or r.ts) for r in rows),
            )
            bars.update(fetched)
            usd_lookup = lambda row, _u=usd_1lot: (_u.get(row.symbol) or 0.0) * float(row.lot or 0.0)
        except Exception as exc:
            mt5_error = str(exc)
    attach_mae(rows, lambda sym: bars.get(sym) or [], usd_lookup=usd_lookup)
    report = summarize(rows)
    report["mt5_error"] = mt5_error
    report["n_log_events"] = len(events)
    report["n_symbols"] = len(symbols)
    report["n_symbols_with_bars"] = sum(1 for s in symbols if bars.get(s))
    report["n_m1_bars"] = sum(len(v) for v in bars.values())
    report["n_missing_bars"] = sum(1 for r in rows if r.missing_bars)
    report["sample"] = [
        {
            "ts": r.ts.isoformat(sep=" ", timespec="seconds"),
            "symbol": r.symbol,
            "side": r.side,
            "filled": r.filled,
            "rejected": r.rejected,
            "reject_reason": r.reject_reason,
            "fill_latency_s": r.fill_latency_s,
            "inverted_cohort": r.inverted_cohort,
            "windows": r.windows,
        }
        for r in rows[:12]
    ]
    return report


def _parse_cli_dt(text: str) -> datetime:
    raw = str(text).strip()
    if len(raw) == 10:
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.fromisoformat(raw[:19])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MAE autopsy of rotation 4/5 signals")
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
    report = run_profiler(
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
