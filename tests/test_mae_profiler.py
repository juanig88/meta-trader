from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trading.application.mae_profiler import (
    INVERT_SINCE,
    attach_mae,
    iter_rotation_log_events,
    pair_signals,
    summarize,
)
from trading.domain.mae_excursion import MaeBar


def test_pair_signals_uses_side_decision_not_fill() -> None:
    t0 = datetime(2026, 10, 1, 15, 0, 0)
    t1 = datetime(2026, 10, 1, 15, 0, 8)
    events = [
        {
            "event": "ENSEMBLE_SIGNAL",
            "_dt": t0,
            "symbol": "ETHUSD!",
            "side": "BUY",
            "side_decision": "BUY",
            "lot": 0.01,
            "risk_usd": 0.5,
        },
        {
            "event": "TRADE_OPEN",
            "_dt": t1,
            "symbol": "ETHUSD!",
            "side": "SELL",
            "side_decision": "BUY",
            "price": 2700.0,
            "lot": 0.01,
            "stop_dist": 5.0,
            "risk_usd": 0.5,
            "engine": "rotation",
        },
    ]
    rows = pair_signals(events)
    assert len(rows) == 1
    assert rows[0].side == "BUY"
    assert rows[0].exec_side == "SELL"
    assert rows[0].filled is True
    assert rows[0].fill_latency_s == 8.0
    assert rows[0].inverted_cohort is True
    assert rows[0].ref == 2700.0


def test_pair_signals_reject_and_pre_invert_open() -> None:
    pre = datetime(2026, 10, 1, 10, 0, 0)
    post = datetime(2026, 10, 1, 15, 0, 0)
    events = [
        {
            "event": "ENSEMBLE_SIGNAL",
            "_dt": pre,
            "symbol": "XRPUSD!",
            "side": "SELL",
        },
        {
            "event": "TRADE_OPEN",
            "_dt": datetime(2026, 10, 1, 10, 0, 2),
            "symbol": "XRPUSD!",
            "side": "SELL",
            "engine": "rotation",
        },
        {
            "event": "ENSEMBLE_SIGNAL",
            "_dt": post,
            "symbol": "SOLUSD!",
            "side_decision": "BUY",
            "side": "BUY",
        },
        {
            "event": "ROTATION_OPEN_REJECT",
            "_dt": datetime(2026, 10, 1, 15, 0, 1),
            "symbol": "SOLUSD!",
            "reason": "spread_caro",
        },
    ]
    rows = pair_signals(events)
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["XRPUSD!"].inverted_cohort is False
    assert by_sym["XRPUSD!"].filled is True
    assert by_sym["SOLUSD!"].filled is False
    assert by_sym["SOLUSD!"].rejected is True
    assert by_sym["SOLUSD!"].reject_reason == "spread_caro"
    assert INVERT_SINCE.hour == 14


def test_attach_mae_and_summarize_cohorts() -> None:
    t0 = datetime(2026, 10, 1, 10, 0, 0)
    events = [
        {
            "event": "ENSEMBLE_SIGNAL",
            "_dt": t0,
            "symbol": "AAAUSD!",
            "side": "BUY",
        },
        {
            "event": "TRADE_OPEN",
            "_dt": datetime(2026, 10, 1, 10, 0, 12),
            "symbol": "AAAUSD!",
            "side": "BUY",
            "price": 10.0,
            "engine": "rotation",
            "risk_usd": 0.5,
            "stop_dist": 0.5,
        },
    ]
    rows = pair_signals(events)
    signal_ts = t0.timestamp()
    # last closed at t0: bar open signal_ts-60 closes at t0
    bars = [
        MaeBar(time=signal_ts - 60.0, high=10.2, low=9.8, close=10.0),
        MaeBar(time=signal_ts, high=10.1, low=9.4, close=9.5),
        MaeBar(time=signal_ts + 60.0, high=10.6, low=9.3, close=9.4),
    ]
    attach_mae(rows, lambda _sym: bars, windows_sec=(60, 120))
    w60 = rows[0].windows[60]
    assert w60["missing"] is False
    assert w60["mae"] == 0.6
    assert w60["mfe"] == 0.1
    assert w60["mae_gt_mfe"] is True
    w120 = rows[0].windows[120]
    assert w120["n_bars"] == 2
    assert w120["mae"] == 0.7
    report = summarize(rows, windows_sec=(60, 120))
    filled = report["cohorts"]["filled"]
    assert filled["n"] == 1
    assert filled["fill_latency_median_s"] == 12.0
    assert filled["windows"]["60"]["pct_mae_gt_mfe"] == 1.0
    assert filled["windows"]["60"]["n_missing"] == 0
    assert filled["windows"]["60"]["mfe_usd_median"] is not None
    assert report["cohorts"]["pre_invert"]["n"] == 1
    assert report["cohorts"]["invert"]["n"] == 0


def test_iter_rotation_log_events_skips_legacy_open(tmp_path: Path) -> None:
    log = tmp_path / "trades.log"
    log.write_text(
        "\n".join(
            [
                '2026-10-01 10:00:00 | INFO | {"event":"ENSEMBLE_SIGNAL","ts":"2026-10-01T10:00:00","symbol":"ETHUSD!","side":"BUY"}',
                '2026-10-01 10:00:01 | INFO | {"event":"TRADE_OPEN","ts":"2026-10-01T10:00:01","symbol":"ETHUSD!","side":"BUY","engine":"legacy"}',
                '2026-10-01 10:00:02 | INFO | {"event":"TRADE_OPEN","ts":"2026-10-01T10:00:02","symbol":"ETHUSD!","side":"BUY","engine":"rotation"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    evs = list(iter_rotation_log_events([log], since=datetime(2026, 10, 1)))
    names = [e["event"] for e in evs]
    assert names == ["ENSEMBLE_SIGNAL", "TRADE_OPEN"]
    assert evs[1]["engine"] == "rotation"
