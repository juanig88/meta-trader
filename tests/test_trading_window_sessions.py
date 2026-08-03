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
