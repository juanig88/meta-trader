from __future__ import annotations

from datetime import datetime

from trading.application.account_pnl import lab_epoch_start, mt5_history_end, rotation_epoch_start, summarize_account_pnl


def test_lab_epoch_start_table(monkeypatch):
    monkeypatch.delenv("BOT_LAB_SINCE", raising=False)
    cases = [
        ("default", None, datetime(2026, 9, 24)),
        ("env_ok", "2026-09-24", datetime(2026, 9, 24)),
        ("env_bad", "no-fecha", datetime(2026, 9, 24)),
        ("override_arg", "2026-10-01", datetime(2026, 10, 1)),
    ]
    for name, raw, want in cases:
        if name == "override_arg":
            got = lab_epoch_start(raw)
        elif raw is None:
            got = lab_epoch_start()
        else:
            monkeypatch.setenv("BOT_LAB_SINCE", raw)
            got = lab_epoch_start()
            monkeypatch.delenv("BOT_LAB_SINCE", raising=False)
        assert got == want, name


def test_rotation_epoch_start_table(monkeypatch):
    monkeypatch.delenv("BOT_ROTATION_SINCE", raising=False)
    cases = [
        ("default", None, datetime(2026, 9, 29)),
        ("env_ok", "2026-09-29", datetime(2026, 9, 29)),
        ("override_arg", "2026-10-01", datetime(2026, 10, 1)),
    ]
    for name, raw, want in cases:
        if name == "override_arg":
            got = rotation_epoch_start(raw)
        elif raw is None:
            got = rotation_epoch_start()
        else:
            monkeypatch.setenv("BOT_ROTATION_SINCE", raw)
            got = rotation_epoch_start()
            monkeypatch.delenv("BOT_ROTATION_SINCE", raising=False)
        assert got == want, name


def test_mt5_history_end_pads_local_now():
    now = datetime(2026, 9, 25, 12, 45, 0)
    assert mt5_history_end(now) == datetime(2026, 9, 26, 0, 45, 0)
    assert mt5_history_end(now, pad_hours=3) == datetime(2026, 9, 25, 15, 45, 0)


def test_summarize_account_pnl_table():
    cases = [
        {
            "name": "empty",
            "deals": [],
            "positions": [],
            "want": {"realized_net": 0.0, "floating_net": 0.0, "account_net": 0.0, "n_wins": 0, "n_losses": 0},
        },
        {
            "name": "today_mix_bot_manual_float",
            "deals": [
                {"time": "2026-09-25T03:15:20", "symbol": "ETHUSD", "magic": 0, "entry": 1, "type": 0, "profit": 0.32, "swap": 0.0, "commission": 0.0, "comment": "[sl]"},
                {"time": "2026-09-25T11:15:09", "symbol": "USDJPY", "magic": 260414, "entry": 0, "type": 1, "profit": 0.0, "swap": 0.0, "commission": -0.04, "comment": "bot"},
                {"time": "2026-09-25T11:18:29", "symbol": "BTCUSD", "magic": 260414, "entry": 1, "type": 1, "profit": -1.47, "swap": 0.0, "commission": 0.0, "comment": "[sl]"},
                {"time": "2026-09-25T12:00:00", "symbol": "", "magic": 0, "entry": 0, "type": 2, "profit": 200.0, "swap": 0.0, "commission": 0.0, "comment": "deposit"},
            ],
            "positions": [{"symbol": "ETHUSD", "side": "BUY", "profit": -1.08, "swap": 0.0, "magic": 260414}],
            "want": {
                "realized_profit": -1.15,
                "realized_commission": -0.04,
                "realized_net": -1.19,
                "bot_realized_net": -1.51,
                "manual_realized_net": 0.32,
                "floating_net": -1.08,
                "account_net": -2.27,
                "n_wins": 1,
                "n_losses": 1,
                "n_closed": 2,
            },
        },
        {
            "name": "close_side_from_deal_type",
            "deals": [
                {"time": "2026-09-24T18:25:19", "symbol": "BTCUSD", "magic": 0, "entry": 1, "type": 1, "profit": 0.99, "comment": "[tp]"},
            ],
            "positions": [],
            "want": {"n_wins": 1, "n_losses": 0, "manual_realized_net": 0.99, "bot_realized_net": 0.0},
        },
        {
            "name": "manual_out_inherits_in_magic",
            "deals": [
                {
                    "time": "2026-09-29T16:00:00",
                    "symbol": "ADAUSD!",
                    "magic": 260414,
                    "entry": 0,
                    "type": 0,
                    "profit": 0.0,
                    "position_id": 77,
                },
                {
                    "time": "2026-09-29T16:40:00",
                    "symbol": "ADAUSD!",
                    "magic": 0,
                    "entry": 1,
                    "type": 1,
                    "profit": -0.02,
                    "position_id": 77,
                    "reason": 0,
                },
            ],
            "positions": [],
            "want": {"n_closed": 1, "n_losses": 1},
        },
    ]
    for case in cases:
        got = summarize_account_pnl(case["deals"], case["positions"], bot_magics=(260414,))
        for key, want in case["want"].items():
            assert got[key] == want, f"{case['name']}.{key}: {got[key]!r} != {want!r}"
        if case["name"] == "close_side_from_deal_type":
            assert got["closed"][0]["side"] == "BUY"
            assert got["closed"][0]["origin"] == "manual"
        if case["name"] == "manual_out_inherits_in_magic":
            assert got["closed"][0]["origin"] == "bot"
            assert got["closed"][0]["magic"] == 260414
        if case["name"] == "today_mix_bot_manual_float":
            assert got["wins"][0]["symbol"] == "ETHUSD"
            assert got["wins"][0]["side"] == "SELL"
            assert got["wins"][0]["origin"] == "manual"
            assert got["losses"][0]["origin"] == "bot"
            by_sym = {r["symbol"]: r for r in got["by_symbol"]}
            assert by_sym["USDJPY"]["net"] == -0.04
            assert by_sym["USDJPY"]["n_closed"] == 0
            assert by_sym["ETHUSD"]["n_wins"] == 1
