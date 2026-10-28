"""Rotation dashboard metrics from closed/floating rows."""

from __future__ import annotations

from trading.application.rotation_metrics import summarize_rotation_closes


def test_summarize_rotation_closes_table():
    closed = [
        {"symbol": "BTCUSD!", "origin": "bot", "net": 0.50},
        {"symbol": "ETHUSD!", "origin": "bot", "net": -0.50},
        {"symbol": "EURUSD", "origin": "bot", "net": 2.00},
        {"symbol": "SOLUSD!", "origin": "manual", "net": 1.00},
        {"symbol": "ADAUSD!", "origin": "bot", "net": 0.0},
    ]
    floating = [
        {"symbol": "XRPUSD!", "origin": "bot", "net": 0.12},
        {"symbol": "BTCUSD!", "origin": "manual", "net": 9.0},
    ]
    uni = ("BTCUSD!", "ETHUSD!", "SOLUSD!", "ADAUSD!", "XRPUSD!")
    cases = [
        (
            "bot crypto only",
            dict(closed=closed, floating=floating, universe=uni, origin="bot"),
            {"n_closed": 3, "n_wins": 1, "n_losses": 1, "n_decided": 2, "winrate": 0.5, "realized_net": 0.0, "n_open": 1, "floating_net": 0.12},
        ),
        (
            "all origins include manual",
            dict(closed=closed, floating=floating, universe=uni, origin=""),
            {"n_closed": 4, "n_wins": 2, "n_losses": 1, "n_decided": 3, "n_open": 2},
        ),
        (
            "empty",
            dict(closed=(), floating=(), universe=uni),
            {"n_closed": 0, "n_wins": 0, "n_losses": 0, "n_decided": 0, "winrate": None, "realized_net": 0.0, "n_open": 0},
        ),
    ]
    for name, kwargs, want in cases:
        got = summarize_rotation_closes(**kwargs)
        for key, value in want.items():
            assert got[key] == value, f"{name}:{key}"


def test_rotation_close_rows():
    from trading.application.rotation_metrics import rotation_close_rows

    closed = [
        {"symbol": "ADAUSD!", "origin": "bot", "net": 0.12, "time": "2026-09-29T16:00:00", "comment": "rot_10_12", "reason": 3},
        {"symbol": "EURUSD", "origin": "bot", "net": 2.00, "time": "2026-09-29T16:00:00"},
        {"symbol": "RSRUSD!", "origin": "manual", "net": 0.50, "time": "2026-09-29T16:00:00", "comment": "", "reason": 0},
    ]
    uni = ("ADAUSD!", "RSRUSD!")
    cases = [
        ("bot crypto only", dict(closed=closed, universe=uni, origin="bot"), ["ADAUSD!"]),
        ("all origins", dict(closed=closed, universe=uni, origin=""), ["ADAUSD!", "RSRUSD!"]),
        ("empty", dict(closed=(), universe=uni), []),
    ]
    for name, kwargs, want_syms in cases:
        got = rotation_close_rows(**kwargs)
        assert [r["symbol"] for r in got] == want_syms, name
    hows = {r["symbol"]: r["how"] for r in rotation_close_rows(closed, universe=uni, origin="")}
    assert hows["RSRUSD!"] == "client_manual"
    assert hows["ADAUSD!"] == "expert_or_bot"


def test_summarize_hourly_opens():
    from trading.application.account_pnl import DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_TYPE_BUY
    from trading.application.rotation_metrics import summarize_hourly_opens

    deals = [
        {"symbol": "ADAUSD!", "magic": 260414, "entry": DEAL_ENTRY_IN, "type": DEAL_TYPE_BUY, "time": "2026-09-29T16:05:52", "volume": 0.01, "ticket": 1},
        {"symbol": "RSRUSD!", "magic": 260414, "entry": DEAL_ENTRY_IN, "type": DEAL_TYPE_BUY, "time": "2026-09-29T16:10:00", "volume": 10.0, "ticket": 2},
        {"symbol": "ADAUSD!", "magic": 260414, "entry": DEAL_ENTRY_OUT, "type": DEAL_TYPE_BUY, "time": "2026-09-29T16:20:00", "volume": 0.01, "ticket": 3},
        {"symbol": "EURUSD", "magic": 99, "entry": DEAL_ENTRY_IN, "type": DEAL_TYPE_BUY, "time": "2026-09-29T16:11:00", "volume": 0.1, "ticket": 4},
    ]
    got = summarize_hourly_opens(deals, magics=(260414,), cap=60)
    assert got["n"] == 2
    assert got["cap"] == 60
    assert got["remaining"] == 58
    assert got["at_cap"] is False
    assert got["rows"][0]["symbol"] == "RSRUSD!"
    full = summarize_hourly_opens(deals, magics=(260414,), cap=2)
    assert full["at_cap"] is True
    assert full["remaining"] == 0
