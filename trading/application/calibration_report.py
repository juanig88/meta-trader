"""Brier / reliability / lift of shadow flags vs our own fills. Read-only. Never a live gate.

Join key: TRADE_OPEN ``order`` == MT5 position id == ROTATION_STALE_CLOSE ``ticket``
== TRADE_CLOSE ``position_id``. Broker net from the backfill cache wins over the
log, because stale closes only carry MTM ``profit`` at the moment of the close.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from trading.application.calibration_backfill import DEFAULT_CACHE, decision_side, load_backfill, rotation_opens
from trading.application.log_events import iter_events_files, rotation_log_paths
from trading.domain.calibration import (
    base_rate,
    brier,
    brier_skill,
    empirical_recalibration,
    flag_lift,
    quantile_buckets,
    reliability,
)

# Same split as ROTATION_ENGINE.md. Invert was a live experiment, not a voter.
# Per-fill side_inverted wins; times are only the fallback for rows without it.
PRE_INVERT = datetime.fromisoformat("2026-10-01T14:36:00")
POST_ROLLBACK = datetime.fromisoformat("2026-10-03T09:11:00")
MIN_N = 30
# Bar for even discussing a gate (risk-quant): flagged fills lose more often and
# with worse mean net, both sides >= 100 fills, p < 0.01, and skipping the flag
# would have removed losing USD. Still never automatic.
GATE_MIN_SIDE = 100
GATE_MAX_P = 0.01
CLOSE_EVENTS = ("TRADE_CLOSE", "ROTATION_STALE_CLOSE")
ERA_CURRENT = "consenso_4_5"
LOCAL_TO_SERVER = timedelta(hours=3)


def _parse_ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip().replace("Z", "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _cohort(ts: Optional[datetime], inverted: Optional[bool] = None) -> str:
    if inverted:
        return "invert"
    if ts is None:
        return "unknown"
    if ts < PRE_INVERT:
        return "pre_invert"
    if inverted is None and ts < POST_ROLLBACK:
        return "invert"
    return "post_rollback"


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _i(raw: Any) -> Optional[int]:
    try:
        out = int(raw)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def log_outcomes(paths: Iterable[Path | str]) -> dict[int, dict[str, Any]]:
    """Net per position from the log. TRADE_CLOSE (deal net) beats a stale MTM profit."""
    out: dict[int, dict[str, Any]] = {}
    for ev in iter_events_files(paths, CLOSE_EVENTS):
        name = str(ev.get("event") or "")
        if name == "TRADE_CLOSE":
            pid = _i(ev.get("position_id"))
            net = _f(ev.get("net"))
            if pid is None or net is None:
                continue
            out[pid] = {"net": net, "how": ev.get("close_how") or ev.get("why_closed"), "src": "trade_close"}
        else:
            pid = _i(ev.get("ticket"))
            net = _f(ev.get("profit"))
            if pid is None or net is None or not ev.get("closed", True):
                continue
            if pid in out and out[pid]["src"] == "trade_close":
                continue
            out[pid] = {"net": net, "how": ev.get("why_closed") or ev.get("reason"), "src": "stale_close"}
    return out


def _aligned(score: Optional[float], side: str) -> Optional[float]:
    if score is None:
        return None
    return float(score) if str(side).upper() == "BUY" else -float(score)


def build_rows(
    *,
    opens: Mapping[int, Mapping[str, Any]],
    outcomes_log: Mapping[int, Mapping[str, Any]],
    backfill: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    bf_rows = backfill.get("rows") if isinstance(backfill.get("rows"), dict) else {}
    pids = {int(p) for p in opens} | {int(p) for p in bf_rows}
    rows: list[dict[str, Any]] = []
    counts = {"no_outcome": 0, "outcome_broker": 0, "outcome_log": 0, "feat_live": 0, "feat_backfill": 0, "feat_none": 0}
    for pid in sorted(pids):
        op = opens.get(pid) or {}
        bf = bf_rows.get(str(pid)) or {}
        net = None
        how = ""
        if bf.get("closed") and bf.get("net") is not None:
            net = float(bf["net"])
            how = str(bf.get("close_comment") or "")
            counts["outcome_broker"] += 1
        elif pid in outcomes_log:
            net = float(outcomes_log[pid]["net"])
            how = str(outcomes_log[pid].get("how") or "")
            counts["outcome_log"] += 1
        if net is None:
            counts["no_outcome"] += 1
            continue
        side = str(op.get("side") or bf.get("side") or "").upper()
        if op:
            side_dec, inverted = decision_side(op, side)
        elif bf.get("side_decision"):
            side_dec, inverted = str(bf["side_decision"]).upper(), bool(bf.get("inverted"))
        else:
            side_dec, inverted = side, None
        live_alpha = op.get("alpha") if isinstance(op.get("alpha"), dict) else None
        live_murphy = op.get("murphy") if isinstance(op.get("murphy"), dict) else None
        feat = bf.get("features") if isinstance(bf.get("features"), dict) else {}
        bf_ok = bool(feat.get("ok"))
        bf_alpha = feat.get("alpha") if bf_ok and isinstance(feat.get("alpha"), dict) else None
        bf_murphy = feat.get("murphy") if bf_ok and isinstance(feat.get("murphy"), dict) else None
        alpha = live_alpha or bf_alpha
        murphy = live_murphy or bf_murphy
        if live_alpha or live_murphy:
            counts["feat_live"] += 1
        elif bf_alpha or bf_murphy:
            counts["feat_backfill"] += 1
        else:
            counts["feat_none"] += 1
        buy, sell, none = (_f(op.get(k)) for k in ("buy", "sell", "none"))
        agree = _f(op.get("agree"))
        voters = (buy or 0.0) + (sell or 0.0) + (none or 0.0)
        p_cons = (agree / voters) if agree is not None and voters > 0 else None
        ts = _parse_ts(op.get("ts"))
        if ts is None and bf.get("open_time"):
            ts = datetime.fromtimestamp(int(bf["open_time"]), tz=timezone.utc).replace(tzinfo=None) - LOCAL_TO_SERVER
        state = op.get("state") if isinstance(op.get("state"), dict) else {}
        tape = _f(state.get("tape_imbalance"))
        rows.append(
            {
                "position_id": pid,
                "symbol": op.get("symbol") or bf.get("symbol"),
                "side": side,
                "side_decision": side_dec,
                "inverted": inverted,
                "net": net,
                "win": 1.0 if net > 0.0 else 0.0,
                "loss": 1.0 if net < 0.0 else 0.0,
                "how": how,
                "era": str(op.get("why_open") or ("sin_log" if not op else "sin_etiqueta")),
                "cohort": _cohort(ts, inverted),
                "day": ts.strftime("%Y-%m-%d") if ts else "unknown",
                "alpha_avoid": None if alpha is None else bool(alpha.get("would_avoid")),
                "alpha_aligned": None if alpha is None else _aligned(_f(alpha.get("score")), side),
                "murphy_veto": None if murphy is None else bool(murphy.get("would_veto")),
                "p_consensus": p_cons,
                "tape_aligned": None if tape is None else _aligned(tape, side),
                "live_alpha_avoid": None if live_alpha is None else bool(live_alpha.get("would_avoid")),
                "bf_alpha_avoid": None if bf_alpha is None else bool(bf_alpha.get("would_avoid")),
                "live_murphy_veto": None if live_murphy is None else bool(live_murphy.get("would_veto")),
                "bf_murphy_veto": None if bf_murphy is None else bool(bf_murphy.get("would_veto")),
            }
        )
    return rows, counts


def _flag_question(name: str, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    used = [r for r in rows if r.get(key) is not None]
    pairs = [(1.0 if r[key] else 0.0, r["loss"]) for r in used]
    lift = flag_lift([(bool(r[key]), float(r["net"])) for r in used])
    on, off = lift["flag_on"], lift["flag_off"]
    discussable = (
        lift["flag_on_worse"]
        and on["n"] >= GATE_MIN_SIDE
        and off["n"] >= GATE_MIN_SIDE
        and lift["p_value"] is not None
        and lift["p_value"] < GATE_MAX_P
        and lift["skip_on_flag_would_remove_usd"] < 0.0
    )
    return {
        "name": name,
        "target": "loss (net < 0)",
        "n": len(used),
        "sufficient": len(used) >= MIN_N,
        "brier": None if not pairs else round(float(brier(pairs)), 6),
        "base_rate": None if not pairs else round(float(base_rate(pairs)), 4),
        "brier_skill": None if brier_skill(pairs) is None else round(float(brier_skill(pairs)), 4),
        "reliability": reliability(pairs, buckets=2),
        "recalibrated": empirical_recalibration(pairs),
        "lift": lift,
        "gate_discussable": bool(discussable),
    }


def _prob_question(name: str, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    pairs = [(float(r[key]), r["win"]) for r in rows if r.get(key) is not None]
    return {
        "name": name,
        "target": "win (net > 0)",
        "n": len(pairs),
        "sufficient": len(pairs) >= MIN_N,
        "brier": None if not pairs else round(float(brier(pairs)), 6),
        "base_rate": None if not pairs else round(float(base_rate(pairs)), 4),
        "brier_skill": None if brier_skill(pairs) is None else round(float(brier_skill(pairs)), 4),
        "reliability": reliability(pairs, buckets=5),
        "recalibrated": empirical_recalibration(pairs),
    }


def _score_question(name: str, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    pts = [(float(r[key]), float(r["net"])) for r in rows if r.get(key) is not None]
    return {
        "name": name,
        "target": "win rate / mean net by quintile (score is not a probability)",
        "n": len(pts),
        "sufficient": len(pts) >= MIN_N,
        "quintiles": quantile_buckets(pts, buckets=5),
    }


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(r["net"]) for r in rows]
    return {
        "n_fills": len(rows),
        "n_wins": sum(1 for r in rows if r["win"] > 0),
        "n_losses": sum(1 for r in rows if r["loss"] > 0),
        "sum_net": round(sum(nets), 4),
        "mean_net": None if not nets else round(sum(nets) / len(nets), 6),
        "questions": [
            _flag_question("alpha_would_avoid_predicts_loss", rows, "alpha_avoid"),
            _flag_question("murphy_would_veto_predicts_loss", rows, "murphy_veto"),
            _prob_question("consensus_share_predicts_win", rows, "p_consensus"),
            _score_question("alpha_score_aligned_vs_outcome", rows, "alpha_aligned"),
            _score_question("tape_imbalance_aligned_vs_outcome", rows, "tape_aligned"),
        ],
    }


def _fidelity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Live-logged flag vs the same flag rebuilt from MT5 bars. Low agreement = backfill untrustworthy."""

    def _agree(live_key: str, bf_key: str) -> dict[str, Any]:
        both = [r for r in rows if r.get(live_key) is not None and r.get(bf_key) is not None]
        same = sum(1 for r in both if bool(r[live_key]) == bool(r[bf_key]))
        return {"n": len(both), "agree": same, "rate": None if not both else round(same / len(both), 4)}

    return {
        "alpha_would_avoid": _agree("live_alpha_avoid", "bf_alpha_avoid"),
        "murphy_would_veto": _agree("live_murphy_veto", "bf_murphy_veto"),
    }


def build_calibration(
    path: Optional[Path | str] = None,
    *,
    paths: Optional[Iterable[Path | str]] = None,
    backfill: Optional[Mapping[str, Any]] = None,
    backfill_path: Path | str = DEFAULT_CACHE,
    root: Path | str = ".",
) -> dict[str, Any]:
    if paths is not None:
        log_paths = [Path(p) for p in paths]
    elif path is not None:
        log_paths = [Path(path)]
    else:
        log_paths = rotation_log_paths(root)
    bf = dict(backfill) if backfill is not None else load_backfill(backfill_path)
    opens = rotation_opens(log_paths)
    outcomes = log_outcomes(log_paths)
    rows, counts = build_rows(opens=opens, outcomes_log=outcomes, backfill=bf)
    by_era: dict[str, list[dict[str, Any]]] = {}
    by_cohort: dict[str, list[dict[str, Any]]] = {}
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_era.setdefault(r["era"], []).append(r)
        by_cohort.setdefault(r["cohort"], []).append(r)
        by_day.setdefault(r["day"], []).append(r)
    current = by_era.get(ERA_CURRENT, [])
    sent_consensus = [r for r in current if r.get("inverted") is False]
    current_by_cohort: dict[str, list[dict[str, Any]]] = {}
    for r in current:
        current_by_cohort.setdefault(r["cohort"], []).append(r)
    return {
        "sources": [str(p) for p in log_paths],
        "backfill_generated_utc": bf.get("generated_utc"),
        "min_n": MIN_N,
        "gate_bar": {"min_side": GATE_MIN_SIDE, "max_p": GATE_MAX_P},
        "live_gate": False,
        "n_log_opens": len(opens),
        "n_backfill_positions": len((bf.get("rows") or {})),
        "n_matched": len(rows),
        "n_unmatched": counts["no_outcome"],
        "counts": counts,
        "fidelity": _fidelity(rows),
        "all": _pack(rows),
        "current_engine": _pack(current),
        "sent_consensus": _pack(sent_consensus),
        "current_engine_by_cohort": {k: _pack(v) for k, v in sorted(current_by_cohort.items())},
        "eras": {k: _pack(v) for k, v in sorted(by_era.items())},
        "cohorts": {k: _pack(v) for k, v in sorted(by_cohort.items())},
        "days": {
            k: {"n": len(v), "sum_net": round(sum(float(r["net"]) for r in v), 4)}
            for k, v in sorted(by_day.items())
        },
    }


def write_calibration(
    path: Optional[Path | str] = None,
    out: Path | str = "trading/docs/reports/calibration_latest.json",
    **kwargs: Any,
) -> dict[str, Any]:
    report = build_calibration(path, **kwargs)
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def summary_lines(report: Mapping[str, Any], scope: str = "current_engine") -> list[str]:
    block = report.get(scope) or {}
    lines = [
        f"[{scope}] fills={block.get('n_fills')} wins={block.get('n_wins')} losses={block.get('n_losses')} "
        f"net={block.get('sum_net')} mean={block.get('mean_net')}"
    ]
    for q in block.get("questions") or []:
        if "lift" in q:
            lift = q["lift"]
            on, off = lift["flag_on"], lift["flag_off"]
            lines.append(
                f"  {q['name']}: n={q['n']} brier={q['brier']} skill={q['brier_skill']} "
                f"loss% on={on['loss_rate']} (n={on['n']}, net={on['sum_net']}) "
                f"off={off['loss_rate']} (n={off['n']}, net={off['sum_net']}) "
                f"p={lift['p_value']} gate_discussable={q['gate_discussable']}"
            )
        elif "quintiles" in q:
            qs = " | ".join(
                f"[{b['score_lo']},{b['score_hi']}] n={b['n']} wr={b['win_rate']} net={b['mean_net']}"
                for b in q["quintiles"]
            )
            lines.append(f"  {q['name']}: n={q['n']} {qs}")
        else:
            lines.append(
                f"  {q['name']}: n={q['n']} brier={q['brier']} skill={q['brier_skill']} recal={q['recalibrated']}"
            )
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Brier calibration of shadow flags vs fills.")
    ap.add_argument("--log", default=None, help="single log (default: logs/trades_* since rotation + trades.log)")
    ap.add_argument("--backfill", default=str(DEFAULT_CACHE))
    ap.add_argument("--out", default="trading/docs/reports/calibration_latest.json")
    args = ap.parse_args(argv)
    report = write_calibration(args.log, args.out, backfill_path=args.backfill)
    print(json.dumps({k: report[k] for k in ("n_log_opens", "n_backfill_positions", "n_matched", "n_unmatched", "counts", "fidelity")}, indent=2))
    for scope in ("all", "current_engine", "sent_consensus"):
        print("\n".join(summary_lines(report, scope)))
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
