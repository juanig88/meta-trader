import bot_mt5
from datetime import datetime, time

from bot_mt5 import Config, _within_trading_window_sessions, parse_trading_window_sessions


def test_parse_sessions_two_ranges():
    s = parse_trading_window_sessions("06:00-12:00,13:00-18:00")
    assert len(s) == 2
    assert s[0][0] == time(6, 0)
    assert s[0][1] == time(12, 0)
    assert s[1][0] == time(13, 0)
    assert s[1][1] == time(18, 0)


def test_midday_gap_not_in_sessions():
    cfg = Config(
        trading_window_timezone="LOCAL",
        trading_window_sessions=parse_trading_window_sessions("06:00-12:00,13:00-18:00"),
    )
    wed = datetime(2026, 5, 11, 12, 35, 0)
    assert not _within_trading_window_sessions(cfg, wed)


def test_within_first_session_same_day():
    cfg = Config(
        trading_window_timezone="LOCAL",
        trading_window_sessions=parse_trading_window_sessions("06:00-12:00,13:00-18:00"),
    )
    wed = datetime(2026, 5, 11, 10, 0, 0)
    assert _within_trading_window_sessions(cfg, wed)


def test_utc_mode_uses_clock_helper(monkeypatch):
    cfg = Config(
        trading_window_timezone="UTC",
        trading_window_sessions=parse_trading_window_sessions("06:00-08:00"),
    )

    def fake_clock(_cfg: Config, _wall: datetime) -> float:
        return 7 * 60 + 30.0  # 07:30 inside [06:00, 08:00]

    monkeypatch.setattr(bot_mt5, "_clock_minutes_of_day_for_trading_window", fake_clock)
    assert _within_trading_window_sessions(cfg, datetime(2026, 5, 11, 0, 0, 0))


def test_btcusd_24x7_no_hour_windows():
    """BTC with no symbol sessions + weekend_trade_symbols is 24/7. FX weekend still blocked."""
    from bot_mt5 import _session_allows_new_trade

    cfg = Config(
        symbols=("BTCUSD", "EURUSD"),
        trading_window_enabled=True,
        trading_window_timezone="LOCAL",
        trading_window_sessions=(),
        weekend_block_enabled=True,
        weekend_trade_symbols=("BTCUSD",),
        quiet_session_block_enabled=False,
        symbol_config={"BTCUSD": {}},
    )
    cases = [
        ("sun_overnight_ok", "BTCUSD", datetime(2026, 8, 16, 23, 30), True),
        ("sun_midday_ok", "BTCUSD", datetime(2026, 8, 16, 11, 0), True),
        ("sat_ok", "BTCUSD", datetime(2026, 8, 15, 15, 0), True),
        ("wed_any_hour_ok", "BTCUSD", datetime(2026, 8, 19, 18, 30), True),
        ("sun_eur_blocked", "EURUSD", datetime(2026, 8, 16, 15, 0), False),
        ("wed_eur_ok", "EURUSD", datetime(2026, 8, 19, 15, 0), True),
    ]
    for name, sym, when, want in cases:
        assert _session_allows_new_trade(cfg, sym, when) is want, name


def test_ethusd_24x7_like_btc():
    """ETH mirrors BTC: weekend + quiet exempt, no hour windows → 24/7. FX still gated."""
    from bot_mt5 import _session_allows_new_trade

    cfg = Config(
        symbols=("BTCUSD", "ETHUSD", "EURUSD"),
        trading_window_enabled=True,
        trading_window_timezone="LOCAL",
        trading_window_sessions=(),
        weekend_block_enabled=True,
        weekend_trade_symbols=("BTCUSD", "ETHUSD"),
        quiet_session_block_enabled=True,
        quiet_session_hours=(0, 1, 2, 3, 4, 5),
        quiet_session_exempt_symbols=("BTCUSD", "ETHUSD"),
        symbol_config={"BTCUSD": {}, "ETHUSD": {}},
    )
    cases = [
        ("eth_sun_overnight_ok", "ETHUSD", datetime(2026, 8, 16, 23, 30), True),
        ("eth_sat_ok", "ETHUSD", datetime(2026, 8, 15, 15, 0), True),
        ("eth_wed_any_hour_ok", "ETHUSD", datetime(2026, 8, 19, 18, 30), True),
        ("eth_quiet_hour_ok", "ETHUSD", datetime(2026, 8, 19, 3, 0), True),
        ("btc_quiet_hour_ok", "BTCUSD", datetime(2026, 8, 19, 3, 0), True),
        ("eur_quiet_hour_blocked", "EURUSD", datetime(2026, 8, 19, 3, 0), False),
        ("eur_sun_blocked", "EURUSD", datetime(2026, 8, 16, 15, 0), False),
    ]
    for name, sym, when, want in cases:
        assert _session_allows_new_trade(cfg, sym, when) is want, name


def test_ethusd_weekend_blocked_if_not_in_allowlist():
    from bot_mt5 import _session_allows_new_trade

    cfg = Config(
        symbols=("ETHUSD",),
        weekend_block_enabled=True,
        weekend_trade_symbols=("BTCUSD",),
        quiet_session_block_enabled=False,
        symbol_config={"ETHUSD": {}},
    )
    assert _session_allows_new_trade(cfg, "ETHUSD", datetime(2026, 8, 16, 15, 0)) is False
    assert _session_allows_new_trade(cfg, "ETHUSD", datetime(2026, 8, 19, 15, 0)) is True
