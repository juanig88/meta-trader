from __future__ import annotations

from datetime import datetime

from trading.application.fill_mtm_profiler import attach_fill_mtm, summarize_fill_mtm
from trading.application.mae_profiler import pair_signals
from trading.domain.mae_excursion import MaeBar


def test_attach_fill_mtm_and_recommend_horizon() -> None:
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
            "_dt": datetime(2026, 10, 1, 10, 0, 1),
            "symbol": "AAAUSD!",
            "side": "BUY",
            "price": 10.0,
            "engine": "rotation",
            "risk_usd": 0.5,
            "stop_dist": 0.5,
            "lot": 1.0,
        },
    ]
    rows = pair_signals(events)
    assert rows[0].exec_side == "BUY"
    fill_ts = rows[0].fill_ts.timestamp()
    bars = [
        MaeBar(time=fill_ts - 60.0, high=10.1, low=9.9, close=10.0),
        MaeBar(time=fill_ts, high=10.2, low=9.8, close=10.2),
        MaeBar(time=fill_ts + 60.0, high=10.4, low=9.7, close=10.3),
        MaeBar(time=fill_ts + 240.0, high=10.5, low=9.6, close=10.4),
        MaeBar(time=fill_ts + 840.0, high=10.1, low=9.5, close=9.8),
        MaeBar(time=fill_ts + 1740.0, high=10.0, low=9.4, close=9.6),
        MaeBar(time=fill_ts + 3540.0, high=9.9, low=9.3, close=9.5),
    ]
    attach_fill_mtm(rows, lambda _sym: bars, horizons_sec=(300, 900, 1800, 3600))
    w300 = rows[0].windows[300]
    assert w300["missing"] is False
    assert w300["fill_pnl_usd"] == 0.4
    assert w300["vote_pnl_usd"] == 0.4
    assert w300["stale_if_aged"] == "stale_take"
    w900 = rows[0].windows[900]
    assert abs(w900["fill_pnl_usd"] - (-0.2)) < 1e-9
    assert w900["stale_if_aged"] == "stale_loss"
    report = summarize_fill_mtm(rows, horizons_sec=(300, 900, 1800, 3600))
    assert report["n_filled"] == 1
    assert report["cohorts"]["pre_invert_filled"]["horizons"]["300"]["pct_green"] == 1.0
    assert report["recommended_time_stop_seconds"] is None
