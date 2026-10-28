"""Daily paper-session summary. Read-only. Optional Telegram is the caller's job.

The day is the local (log) calendar day. Broker net per closed rotation position
wins over the log, because stale closes only log the MTM profit at request time.
Logs rotate on every bot start, so one day spans several files.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from trading.application.log_events import iter_events_files, rotation_log_paths

LOCAL_TO_SERVER_SECONDS = 3 * 3600
CLOSE_EVENTS = ("TRADE_CLOSE", "ROTATION_STALE_CLOSE")
KILL_EVENTS = (
    "KILL_SWITCH_HALT",
    "KILL_SWITCH_FLATTEN",
    "KILL_SWITCH_BLOCK",
    "KILL_SWITCH_CLEAR",
    "KILL_SWITCH_DISARMED",
    "KILL_SWITCH_ERROR",
)
SCAN_EVENTS = ("TRADE_OPEN", "ENSEMBLE_SIGNAL", "ROTATION_OPEN_REJECT", *CLOSE_EVENTS, *KILL_EVENTS)
SWING_EVENTS = (
    "SWING_PLAN",
    "SWING_GATE",
    "SWING_OPEN",
    "SWING_OPEN_FAIL",
    "SWING_CLOSE",
    "SWING_CLOSE_FAIL",
    "SWING_SKIP",
    "SWING_ABORT",
)


def _num(raw: Any) -> Optional[float]:
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _pid(raw: Any) -> Optional[int]:
    try:
        out = int(raw)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _local_day(server_epoch: int) -> str:
    return datetime.fromtimestamp(int(server_epoch) - LOCAL_TO_SERVER_SECONDS, tz=timezone.utc).strftime("%Y-%m-%d")


def event_local_day(ts: str) -> Optional[str]:
    """Calendar day in Argentina (UTC-3) for an ISO timestamp. Naive timestamps are UTC."""
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(timezone.utc) - timedelta(seconds=LOCAL_TO_SERVER_SECONDS)
    return local.strftime("%Y-%m-%d")


def _plan_committed(plan: Mapping[str, Any]) -> bool:
    stale = plan.get("stale_bars") or []
    universe = int(plan.get("universe") or 0)
    return len(stale) * 2 <= max(universe, 1)


def swing_for_local_day(events: Iterable[Mapping[str, Any]], day: str) -> dict[str, Any]:
    """Swing decision that fell on this local day. A stale-majority plan does not count as decided."""
    day_events = [e for e in events if event_local_day(str(e.get("ts") or "")) == day]
    plans = [e for e in day_events if str(e.get("event") or "") == "SWING_PLAN"]
    decided = [p for p in plans if _plan_committed(p)]
    plan = decided[-1] if decided else (plans[-1] if plans else None)
    gates = [e for e in day_events if str(e.get("event") or "") == "SWING_GATE"]
    gate = gates[-1] if gates else {}
    gate_body = gate.get("gate") if isinstance(gate.get("gate"), Mapping) else {}
    skips: dict[str, int] = {}
    if plan is not None:
        for item in plan.get("skips") or []:
            reason = str(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else "unknown"
            skips[reason] = skips.get(reason, 0) + 1
    committed = plan is not None and _plan_committed(plan)
    return {
        "ran": committed,
        "stale_only": plan is not None and not committed,
        "swing_day": None if plan is None else plan.get("day"),
        "entries": 0 if plan is None else len(plan.get("entries") or []),
        "exits": 0 if plan is None else len(plan.get("exits") or []),
        "opened": sum(1 for e in day_events if e.get("event") == "SWING_OPEN"),
        "closed": sum(1 for e in day_events if e.get("event") == "SWING_CLOSE"),
        "open_fails": sum(1 for e in day_events if e.get("event") == "SWING_OPEN_FAIL"),
        "close_fails": sum(1 for e in day_events if e.get("event") == "SWING_CLOSE_FAIL"),
        "equity": None if plan is None else plan.get("equity"),
        "block_reason": str((plan or {}).get("block_reason") or gate.get("block_reason") or ""),
        "gate_reason": str(gate_body.get("reason") or ""),
        "stale": 0 if plan is None else len(plan.get("stale_bars") or []),
        "universe": None if plan is None else plan.get("universe"),
        "skips": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
    }


def log_closes_for_day(events: Iterable[Mapping[str, Any]], day: str) -> dict[int, float]:
    """One net per position closed that day. TRADE_CLOSE beats a stale MTM profit."""
    out: dict[int, tuple[str, float]] = {}
    for ev in events:
        if not str(ev.get("ts") or "").startswith(day):
            continue
        name = str(ev.get("event") or "")
        if name == "TRADE_CLOSE":
            pid, net = _pid(ev.get("position_id")), _num(ev.get("net"))
            if pid is not None and net is not None:
                out[pid] = ("trade_close", net)
        elif name == "ROTATION_STALE_CLOSE" and ev.get("closed", True):
            pid, net = _pid(ev.get("ticket")), _num(ev.get("profit"))
            if pid is not None and net is not None and out.get(pid, ("", 0.0))[0] != "trade_close":
                out[pid] = ("stale_close", net)
    return {pid: net for pid, (_src, net) in out.items()}


def broker_closes_for_day(positions: Mapping[int, Mapping[str, Any]], day: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for pid, pos in positions.items():
        if not pos.get("closed") or pos.get("net") is None or not pos.get("close_time"):
            continue
        if _local_day(int(pos["close_time"])) == day:
            out[int(pid)] = float(pos["net"])
    return out


def _stats(nets: Iterable[float]) -> dict[str, Any]:
    vals = list(nets)
    wins = [n for n in vals if n > 0]
    losses = [n for n in vals if n < 0]
    decided = len(wins) + len(losses)
    gross_loss = -sum(losses)
    return {
        "n": len(vals),
        "pnl": round(sum(vals), 4),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(vals) - decided,
        "win_rate": None if decided == 0 else round(len(wins) / float(decided), 4),
        "profit_factor": None if gross_loss <= 0 else round(sum(wins) / gross_loss, 4),
        "largest_loss": round(min(losses), 4) if losses else None,
        "largest_win": round(max(wins), 4) if wins else None,
        "mean_net": None if not vals else round(sum(vals) / len(vals), 6),
    }


def build_daily_report(
    paths: Optional[Iterable[Path | str]] = None,
    *,
    day: Optional[str] = None,
    broker_positions: Optional[Mapping[int, Mapping[str, Any]]] = None,
    calibration: Optional[Mapping[str, Any]] = None,
    swing_events: Optional[Iterable[Mapping[str, Any]]] = None,
    rotation_halt: str = "",
    root: Path | str = ".",
) -> dict[str, Any]:
    want = (day or datetime.now().strftime("%Y-%m-%d"))[:10]
    log_paths = [Path(p) for p in paths] if paths is not None else rotation_log_paths(root, since=want)
    events = [e for e in iter_events_files(log_paths, SCAN_EVENTS) if str(e.get("ts") or "").startswith(want)]
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for e in events:
        by_name.setdefault(str(e.get("event") or ""), []).append(e)
    log_nets = log_closes_for_day(events, want)
    broker_nets = broker_closes_for_day(broker_positions, want) if broker_positions is not None else None
    truth = broker_nets if broker_nets is not None else log_nets
    rejects: dict[str, int] = {}
    for e in by_name.get("ROTATION_OPEN_REJECT", []):
        key = str(e.get("reason") or e.get("why_not_filled") or "unknown")
        rejects[key] = rejects.get(key, 0) + 1
    kills: dict[str, int] = {name: len(by_name.get(name, [])) for name in KILL_EVENTS if by_name.get(name)}
    report: dict[str, Any] = {
        "day": want,
        "sources": [str(p) for p in log_paths],
        "pnl_source": "broker" if broker_nets is not None else "log",
        "n_signals": len(by_name.get("ENSEMBLE_SIGNAL", [])),
        "n_opens": len(by_name.get("TRADE_OPEN", [])),
        "rejects": dict(sorted(rejects.items(), key=lambda kv: -kv[1])),
        "closes": _stats(truth.values()),
        "log_closes": _stats(log_nets.values()),
        "log_vs_broker": None,
        "kill_switch": kills,
        "calibration": None,
        "gemini_latency_ms": None,
        "gemini_cost": None,
        "live_gate": False,
        "swing": swing_for_local_day(swing_events or [], want),
        "rotation_halt": rotation_halt or "",
    }
    if broker_nets is not None:
        both = set(log_nets) & set(broker_nets)
        report["log_vs_broker"] = {
            "n_broker": len(broker_nets),
            "n_log": len(log_nets),
            "n_both": len(both),
            "sum_log_on_both": round(sum(log_nets[p] for p in both), 4),
            "sum_broker_on_both": round(sum(broker_nets[p] for p in both), 4),
        }
    if calibration:
        scope = calibration.get("sent_consensus") or {}
        report["calibration"] = {
            "scope": "sent_consensus",
            "n_matched": calibration.get("n_matched"),
            "questions": {
                q["name"]: {
                    "n": q.get("n"),
                    "brier": q.get("brier"),
                    "brier_skill": q.get("brier_skill"),
                    "sufficient": q.get("sufficient"),
                    "gate_discussable": q.get("gate_discussable"),
                }
                for q in scope.get("questions") or []
                if "brier" in q
            },
        }
    return report


def write_daily_report(
    out: Path | str = "trading/docs/reports/daily_latest.json",
    **kwargs: Any,
) -> dict[str, Any]:
    report = build_daily_report(**kwargs)
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def format_daily_telegram(report: Mapping[str, Any]) -> str:
    c = report.get("closes") or {}
    wr = c.get("win_rate")
    wr_s = "n/a" if wr is None else f"{100.0 * float(wr):.1f}%"
    pf = c.get("profit_factor")
    kills = report.get("kill_switch") or {}
    top_rej = ", ".join(f"{k} {v}" for k, v in list((report.get("rejects") or {}).items())[:3]) or "none"
    lines = [
        f"Daily {report.get('day')} ({report.get('pnl_source')})",
        f"4/5 {report.get('n_signals')} opens {report.get('n_opens')} closes {c.get('n')}",
        f"pnl {c.get('pnl')} WR {wr_s} PF {pf if pf is not None else 'n/a'}",
        f"worst {c.get('largest_loss')} best {c.get('largest_win')}",
        f"rejects: {top_rej}",
        f"kill switch: {', '.join(f'{k} {v}' for k, v in kills.items()) or 'none'}",
    ]
    halt = str(report.get("rotation_halt") or "")
    if halt:
        lines.append(f"rotation halt: {halt}")
    lines.append(_swing_telegram(report.get("swing") or {}))
    return "\n".join(lines)


def _swing_telegram(swing: Mapping[str, Any]) -> str:
    if not swing.get("ran"):
        return "swing: velas viejas, sin decision" if swing.get("stale_only") else "swing: sin decision"
    skips = ", ".join(f"{k} {v}" for k, v in list((swing.get("skips") or {}).items())[:3]) or "none"
    fails = ""
    if swing.get("open_fails") or swing.get("close_fails"):
        fails = f" fallos open {swing.get('open_fails')} close {swing.get('close_fails')}"
    return (
        f"swing {swing.get('swing_day')} entradas {swing.get('entries')} salidas {swing.get('exits')} "
        f"abiertas {swing.get('opened')} cerradas {swing.get('closed')} equity {swing.get('equity')}{fails}\n"
        f"swing gate {swing.get('gate_reason') or 'n/a'} block {swing.get('block_reason') or 'none'} "
        f"skips {skips} stale {swing.get('stale')}/{swing.get('universe')}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Daily rotation paper report.")
    ap.add_argument("--day", default=None, help="local day YYYY-MM-DD (default today)")
    ap.add_argument("--out", default="trading/docs/reports/daily_latest.json")
    ap.add_argument("--no-broker", action="store_true", help="skip the read-only MT5 deal fetch")
    ap.add_argument("--calibration", default="trading/docs/reports/calibration_latest.json")
    ap.add_argument("--telegram", action="store_true", help="send even if TELEGRAM_NOTIFY_DAILY_REPORT=0")
    args = ap.parse_args(argv)
    day = (args.day or datetime.now().strftime("%Y-%m-%d"))[:10]
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(".env"), override=False)
    except ImportError:
        pass

    broker = None
    if not args.no_broker:
        from trading.application.calibration_backfill import positions_from_deals
        from trading.infrastructure.mt5_calibration_source import fetch_rotation_deals

        start = datetime.fromisoformat(day) - timedelta(days=1)
        deals, err = fetch_rotation_deals(start, start + timedelta(days=3))
        if err:
            print("broker skipped:", err)
        else:
            broker = positions_from_deals(deals)

    calib = None
    cpath = Path(args.calibration)
    if cpath.exists():
        try:
            calib = json.loads(cpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            calib = None

    from trading.application.log_events import iter_events_files
    from trading.infrastructure.kill_switch_file import load_halt

    swing_events = list(iter_events_files([Path("swing_trades.log")], SWING_EVENTS))
    halt = load_halt(Path("kill_switch.halt"))
    report = write_daily_report(
        args.out,
        day=day,
        broker_positions=broker,
        calibration=calib,
        swing_events=swing_events,
        rotation_halt=str((halt or {}).get("reason") or ""),
    )
    print(json.dumps(report, indent=2, default=str))

    try:
        import telegram_notify

        if telegram_notify.telegram_configured() and (
            args.telegram or telegram_notify.env_bool("TELEGRAM_NOTIFY_DAILY_REPORT", False)
        ):
            ok, why = telegram_notify.send_message_now(format_daily_telegram(report))
            print("telegram:", "sent" if ok else why)
    except Exception as exc:
        print("telegram skipped:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
