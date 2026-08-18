from datetime import datetime

from bot_mt5 import Config, _xauusd_parallel_in_window, _xauusd_parallel_window_label


def test_xau_sessions_afternoon_open():
    cfg = Config(xauusd_parallel_sessions="07:00-10:00,14:00-16:00,19:00-23:00")
    assert _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 14, 30, 0))
    assert _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 15, 59, 0))
    assert not _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 16, 1, 0))
    assert not _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 12, 0, 0))


def test_xau_sessions_label():
    cfg = Config(xauusd_parallel_sessions="07:00-10:00,14:00-16:00")
    label = _xauusd_parallel_window_label(cfg)
    assert "07:00" in label and "14:00" in label


def test_xau_fallback_start_end_exclusive():
    cfg = Config(xauusd_parallel_sessions="", xauusd_parallel_start_hour=10, xauusd_parallel_end_hour=14)
    assert _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 13, 59, 0))
    assert not _xauusd_parallel_in_window(cfg, datetime(2026, 8, 10, 14, 0, 0))


def test_weekend_blocks_new_trade_table():
    from bot_mt5 import _weekend_blocks_new_trade

    cases = [
        ("sat_blocked", True, (), datetime(2026, 8, 8, 11, 0), True),
        ("sun_blocked", True, (), datetime(2026, 8, 9, 11, 0), True),
        ("mon_open", True, (), datetime(2026, 8, 10, 11, 0), False),
        ("sat_exempt", True, ("XAUUSD",), datetime(2026, 8, 8, 11, 0), False),
        ("sat_disabled", False, (), datetime(2026, 8, 8, 11, 0), False),
    ]
    for name, enabled, exempt, when, want in cases:
        cfg = Config(weekend_block_enabled=enabled, weekend_trade_symbols=exempt)
        assert _weekend_blocks_new_trade(cfg, "XAUUSD", when) is want, name
