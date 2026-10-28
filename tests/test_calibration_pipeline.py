from __future__ import annotations

import json
from pathlib import Path

from trading.application.calibration_backfill import (
    M15_SECONDS,
    build_backfill,
    closed_before,
    decision_side,
    features_at,
    positions_from_deals,
    rotation_opens,
)
from trading.application.calibration_report import (
    _cohort,
    build_calibration,
    build_rows,
    log_outcomes,
)
from trading.application.log_events import rotation_log_paths

MAGIC = 260414


def _write_log(path: Path, events: list[dict]) -> Path:
    lines = [f"2026-10-02 12:00:00 INFO {json.dumps(e)}" for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _bars(n: int, start: int, tf: int, *, drift: float = 0.0) -> list[tuple]:
    out = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + drift + (0.3 if i % 2 else -0.2)
        out.append((start + i * tf, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 10.0))
        price = c
    return out


def test_decision_side_table():
    cases = [
        ("explicit inverted", {"side_decision": "SELL", "side_inverted": True}, "BUY", ("SELL", True)),
        ("explicit not inverted", {"side_decision": "BUY", "side_inverted": False}, "BUY", ("BUY", False)),
        ("decision without flag", {"side_decision": "SELL"}, "BUY", ("SELL", True)),
        ("murphy side fallback", {"murphy": {"side": "SELL"}}, "BUY", ("SELL", True)),
        ("murphy agrees", {"murphy": {"side": "BUY"}}, "BUY", ("BUY", False)),
        ("only inverted flag", {"side_inverted": True}, "sell", ("BUY", True)),
        ("no log", {}, "buy", ("BUY", False)),
    ]
    for name, ev, exec_side, want in cases:
        assert decision_side(ev, exec_side) == want, name


def test_closed_before_never_includes_forming_bar():
    rows = _bars(10, 0, M15_SECONDS)
    cutoff = 5 * M15_SECONDS + 10
    got = closed_before(rows, cutoff, M15_SECONDS, 100)
    assert [r[0] for r in got] == [i * M15_SECONDS for i in range(5)]
    assert all(r[0] + M15_SECONDS <= cutoff for r in got)
    exact = closed_before(rows, 5 * M15_SECONDS, M15_SECONDS, 100)
    assert len(exact) == 5
    assert len(closed_before(rows, cutoff, M15_SECONDS, 2)) == 2
    assert closed_before([], cutoff, M15_SECONDS, 5) == []


def test_features_at_ignores_bars_after_open():
    m15 = _bars(260, 0, M15_SECONDS, drift=0.05)
    h4 = _bars(80, 0, 14_400)
    open_epoch = 200 * M15_SECONDS + 30
    base = features_at(symbol="X", side="BUY", open_epoch=open_epoch, m15_rows=m15, h4_rows=h4)
    future = [r if r[0] < 200 * M15_SECONDS else (r[0], r[1], r[2] * 3, r[3] / 3, r[4] * 2, r[5]) for r in m15]
    same = features_at(symbol="X", side="BUY", open_epoch=open_epoch, m15_rows=future, h4_rows=h4)
    assert base["ok"] and same["ok"]
    assert base["last_m15_close_epoch"] <= open_epoch
    assert base["alpha"] == same["alpha"] and base["murphy"] == same["murphy"]
    few = features_at(symbol="X", side="BUY", open_epoch=10 * M15_SECONDS, m15_rows=m15, h4_rows=h4)
    assert few == {"ok": False, "reason": "pocas_velas_m15", "n_m15": 10, "n_h4": 0}


def test_positions_from_deals_net_side_and_magic():
    deals = [
        {"position_id": 1, "entry": 0, "magic": MAGIC, "symbol": "A", "type": 1, "time_secs": 100, "profit": 0.0},
        {"position_id": 1, "entry": 1, "magic": MAGIC, "symbol": "A", "type": 0, "time_secs": 400,
         "profit": -0.3, "swap": -0.01, "commission": -0.02, "comment": "stale_loss"},
        {"position_id": 2, "entry": 0, "magic": MAGIC, "symbol": "B", "type": 0, "time_secs": 200},
        {"position_id": 3, "entry": 0, "magic": 999, "symbol": "C", "type": 0, "time_secs": 200},
        {"position_id": 3, "entry": 1, "magic": 999, "symbol": "C", "type": 1, "time_secs": 300, "profit": 5.0},
        {"position_id": 0, "entry": 0, "magic": MAGIC},
    ]
    got = positions_from_deals(deals)
    assert set(got) == {1, 2}
    assert got[1]["side"] == "SELL" and got[1]["closed"] and got[1]["net"] == -0.33
    assert got[1]["duration_seconds"] == 300 and got[1]["close_comment"] == "stale_loss"
    assert got[2]["closed"] is False and got[2]["net"] is None


def test_build_backfill_scores_against_decision_side():
    m15 = _bars(260, 0, M15_SECONDS, drift=0.05)
    h4 = _bars(80, 0, 14_400)
    calls = []

    def fetch(sym, tf, count):
        calls.append((sym, tf, count))
        return m15 if tf == "M15" else h4

    positions = {
        10: {"position_id": 10, "symbol": "X", "side": "BUY", "open_time": 220 * M15_SECONDS, "closed": True, "net": -0.1},
    }
    opens = {10: {"side_decision": "SELL", "side_inverted": True}}
    got = build_backfill(opens=opens, positions=positions, fetch_bars=fetch, m15_count=300, h4_count=90)
    row = got["rows"]["10"]
    assert row["side_decision"] == "SELL" and row["inverted"] is True and row["in_log"] is True
    direct = features_at(symbol="X", side="SELL", open_epoch=220 * M15_SECONDS, m15_rows=m15, h4_rows=h4)
    assert row["features"] == direct
    assert got["n_features_ok"] == 1 and sorted(calls) == [("X", "H4", 90), ("X", "M15", 300)]


def test_log_outcomes_trade_close_beats_stale(tmp_path):
    log = _write_log(
        tmp_path / "trades.log",
        [
            {"event": "ROTATION_STALE_CLOSE", "ticket": 5, "profit": -0.2, "why_closed": "stale_loss"},
            {"event": "TRADE_CLOSE", "position_id": 5, "net": -0.25, "close_how": "stale_loss"},
            {"event": "ROTATION_STALE_CLOSE", "ticket": 5, "profit": 9.0},
            {"event": "ROTATION_STALE_CLOSE", "ticket": 6, "profit": 0.04, "closed": False},
            {"event": "ROTATION_STALE_CLOSE", "ticket": 7, "profit": 0.04},
        ],
    )
    got = log_outcomes([log])
    assert got[5]["net"] == -0.25 and got[5]["src"] == "trade_close"
    assert 6 not in got
    assert got[7]["src"] == "stale_close"


def test_rotation_opens_keys_by_order_and_filters_magic(tmp_path):
    log = _write_log(
        tmp_path / "trades.log",
        [
            {"event": "TRADE_OPEN", "magic": MAGIC, "order": 11, "position_id": None, "symbol": "A"},
            {"event": "TRADE_OPEN", "magic": 1, "order": 12, "symbol": "B"},
            {"event": "TRADE_OPEN", "magic": MAGIC, "order": None, "symbol": "C"},
        ],
    )
    assert set(rotation_opens([log])) == {11}


def test_rotation_log_paths_since(tmp_path):
    (tmp_path / "logs").mkdir()
    for name in (
        "trades_2026-09-28_100000.log",
        "trades_2026-09-29_000001.log",
        "trades_2026-10-01_120000.log",
        "trades_watcher_2026-09-29_113200.log",
    ):
        (tmp_path / "logs" / name).write_text("", encoding="utf-8")
    (tmp_path / "trades.log").write_text("", encoding="utf-8")
    got = [p.name for p in rotation_log_paths(tmp_path, since="2026-09-29")]
    assert got == ["trades_2026-09-29_000001.log", "trades_2026-10-01_120000.log", "trades.log"]


def test_cohort_table():
    from datetime import datetime

    cases = [
        ("flag wins over time", datetime(2026, 9, 30), True, "invert"),
        ("pre", datetime(2026, 10, 1, 14, 0), False, "pre_invert"),
        ("after rollback flag false", datetime(2026, 10, 3, 9, 30), False, "post_rollback"),
        ("no flag inside window", datetime(2026, 10, 2), None, "invert"),
        ("no flag after window", datetime(2026, 10, 3, 10), None, "post_rollback"),
        ("no ts", None, False, "unknown"),
    ]
    for name, ts, inv, want in cases:
        assert _cohort(ts, inv) == want, name


def _bf_row(pid, *, net, side="BUY", veto=False, avoid=False, score=0.1, closed=True):
    return {
        "position_id": pid,
        "symbol": "X",
        "side": side,
        "open_time": 1_790_000_000,
        "closed": closed,
        "net": net if closed else None,
        "features": {
            "ok": True,
            "alpha": {"would_avoid": avoid, "score": score},
            "murphy": {"would_veto": veto},
        },
    }


def test_build_rows_precedence_and_fidelity():
    opens = {
        1: {"ts": "2026-10-03T12:00:00", "side": "BUY", "side_decision": "BUY", "side_inverted": False,
            "why_open": "consenso_4_5", "agree": 4, "buy": 4, "sell": 1, "none": 0,
            "murphy": {"side": "BUY", "would_veto": True}, "alpha": {"would_avoid": False, "score": -0.2}},
        2: {"ts": "2026-10-02T12:00:00", "side": "BUY", "side_decision": "SELL", "side_inverted": True,
            "why_open": "consenso_4_5", "agree": 4, "buy": 1, "sell": 4, "none": 0},
        3: {"ts": "2026-10-02T13:00:00", "side": "SELL", "why_open": "consenso_4_5"},
    }
    bf = {
        "rows": {
            "1": _bf_row(1, net=-0.3, veto=False, avoid=False, score=0.5),
            "2": _bf_row(2, net=0.2, veto=True, avoid=True, score=0.3),
            "4": _bf_row(4, net=0.1, closed=False),
        }
    }
    outcomes = {1: {"net": 99.0, "how": "x"}, 3: {"net": -0.05, "how": "stale_loss"}}
    rows, counts = build_rows(opens=opens, outcomes_log=outcomes, backfill=bf)
    by = {r["position_id"]: r for r in rows}
    assert set(by) == {1, 2, 3}
    assert counts == {"no_outcome": 1, "outcome_broker": 2, "outcome_log": 1,
                      "feat_live": 1, "feat_backfill": 1, "feat_none": 1}
    assert by[1]["net"] == -0.3
    assert by[1]["murphy_veto"] is True and by[1]["alpha_aligned"] == -0.2
    assert by[1]["live_murphy_veto"] is True and by[1]["bf_murphy_veto"] is False
    assert by[1]["p_consensus"] == 0.8 and by[1]["cohort"] == "post_rollback"
    assert by[2]["inverted"] is True and by[2]["cohort"] == "invert" and by[2]["side_decision"] == "SELL"
    assert by[2]["murphy_veto"] is True and by[2]["alpha_aligned"] == 0.3
    assert by[3]["net"] == -0.05 and by[3]["murphy_veto"] is None and by[3]["alpha_aligned"] is None
    assert by[3]["win"] == 0.0 and by[3]["loss"] == 1.0


def test_build_calibration_scopes_and_never_gates(tmp_path):
    opens = []
    bf_rows = {}
    for i in range(1, 241):
        inverted = i > 200
        veto = i % 2 == 0
        net = -0.05 if veto else 0.02
        opens.append({
            "event": "TRADE_OPEN", "magic": MAGIC, "order": i, "symbol": "X",
            "ts": "2026-10-03T12:00:00" if not inverted else "2026-10-02T12:00:00",
            "side": "BUY", "side_decision": "SELL" if inverted else "BUY", "side_inverted": inverted,
            "why_open": "consenso_4_5", "agree": 4, "buy": 4, "sell": 1, "none": 0,
            "murphy": {"side": "SELL" if inverted else "BUY", "would_veto": veto},
        })
        bf_rows[str(i)] = _bf_row(i, net=net, veto=veto)
    log = _write_log(tmp_path / "trades.log", opens)
    rep = build_calibration(paths=[log], backfill={"rows": bf_rows})
    assert rep["live_gate"] is False
    assert rep["n_matched"] == 240
    assert rep["sent_consensus"]["n_fills"] == 200
    assert rep["current_engine"]["n_fills"] == 240
    assert set(rep["current_engine_by_cohort"]) == {"invert", "post_rollback"}
    murphy = next(q for q in rep["sent_consensus"]["questions"] if q["name"] == "murphy_would_veto_predicts_loss")
    assert murphy["lift"]["flag_on_worse"] is True
    assert murphy["gate_discussable"] is True
    assert rep["fidelity"]["murphy_would_veto"]["rate"] == 1.0
    tape = next(q for q in rep["sent_consensus"]["questions"] if q["name"] == "tape_imbalance_aligned_vs_outcome")
    assert tape["n"] == 0 and tape["sufficient"] is False


def test_build_calibration_empty_inputs(tmp_path):
    rep = build_calibration(paths=[tmp_path / "missing.log"], backfill={})
    assert rep["n_matched"] == 0 and rep["live_gate"] is False
    assert rep["all"]["n_fills"] == 0 and rep["all"]["mean_net"] is None
