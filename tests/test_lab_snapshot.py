from __future__ import annotations

from datetime import datetime

from trading.infrastructure.persistence import lab_snapshot
from trading.infrastructure.persistence.lab_snapshot import direction_label, score_bar_pct


def test_direction_label_table():
    cases = [
        ("buy", "BUY", "Compra"),
        ("sell", "sell", "Venta"),
        ("none", "NONE", "Neutro"),
        ("empty", "", "—"),
        ("unknown", "FLAT", "FLAT"),
    ]
    for name, raw, want in cases:
        assert direction_label(raw) == want, name


def test_score_bar_pct_table():
    cases = [
        ("left", -1.0, 0.0),
        ("mid", 0.0, 50.0),
        ("right", 1.0, 100.0),
        ("none", None, 50.0),
        ("nan", float("nan"), 50.0),
        ("clamp high", 2.0, 100.0),
    ]
    for name, score, want in cases:
        assert abs(score_bar_pct(score) - want) < 1e-9, name


def test_loser_block_lists_wins_and_losses(monkeypatch):
    closes = [
        {
            "event": "TRADE_CLOSE",
            "symbol": "BTCUSD",
            "side": "BUY",
            "net": 0.99,
            "closed_at": "2026-09-24T18:25:19",
            "reason_label": "Take profit",
            "magic": 0,
        },
        {
            "event": "TRADE_CLOSE",
            "symbol": "ETHUSD",
            "side": "SELL",
            "net": -1.47,
            "closed_at": "2026-09-25T11:18:29",
            "reason_label": "Stop loss",
            "magic": 260414,
        },
    ]
    monkeypatch.setattr(lab_snapshot, "_load_trade_events", lambda: (closes, []))
    block = lab_snapshot._loser_block(None)
    assert block["n"] == 1
    assert block["n_wins"] == 1
    assert block["rows"][0]["symbol"] == "ETHUSD"
    assert block["win_rows"][0]["symbol"] == "BTCUSD"
    assert block["win_rows"][0]["origin"] == "manual"
    assert block["sum_wins"] == 0.99
    assert block["sum_net"] == -1.47


def test_loser_block_respects_momento_cero(monkeypatch):
    closes = [
        {
            "event": "TRADE_CLOSE",
            "symbol": "BTCUSD",
            "side": "BUY",
            "net": -24.38,
            "closed_at": "2026-09-23T14:13:05",
            "reason_label": "stop out",
            "magic": 0,
        },
        {
            "event": "TRADE_CLOSE",
            "symbol": "USDJPY",
            "side": "BUY",
            "net": 0.57,
            "closed_at": "2026-09-24T15:00:23",
            "reason_label": "Stop loss",
            "magic": 260414,
        },
        {
            "event": "TRADE_CLOSE",
            "symbol": "BTCUSD",
            "side": "BUY",
            "net": -1.47,
            "closed_at": "2026-09-25T11:18:29",
            "reason_label": "Stop loss",
            "magic": 260414,
        },
    ]
    monkeypatch.setattr(lab_snapshot, "_load_trade_events", lambda: (closes, []))
    block = lab_snapshot._loser_block(None, since=datetime(2026, 9, 24))
    assert block["n"] == 1
    assert block["n_wins"] == 1
    assert block["rows"][0]["symbol"] == "BTCUSD"
    assert block["win_rows"][0]["symbol"] == "USDJPY"


def test_loser_block_drops_other_broker_symbols(monkeypatch):
    closes = [
        {
            "event": "TRADE_CLOSE",
            "symbol": "BTCUSD",
            "side": "BUY",
            "net": -2.0,
            "closed_at": "2026-09-25T11:18:29",
            "reason_label": "Stop loss",
            "magic": 260414,
        },
        {
            "event": "TRADE_CLOSE",
            "symbol": "BTCUSD!",
            "side": "BUY",
            "net": -0.4,
            "closed_at": "2026-09-28T12:00:00",
            "reason_label": "Stop loss",
            "magic": 260414,
        },
    ]
    monkeypatch.setattr(lab_snapshot, "_load_trade_events", lambda: (closes, []))
    block = lab_snapshot._loser_block(None, symbols={"BTCUSD!", "ETHUSD!", "USDJPY."})
    assert block["n"] == 1
    assert block["rows"][0]["symbol"] == "BTCUSD!"
