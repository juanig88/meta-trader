from __future__ import annotations

from datetime import date, datetime, timezone

from trading.infrastructure.persistence.janitor import bar_cutoff, tick_cutoff


def test_tick_cutoff_table():
    now = datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc)
    cases = [
        ("48h same calendar day minus 2", 48, date(2026, 9, 22)),
        ("1h still previous day", 1, date(2026, 9, 24)),
        ("clamps zero to 1h", 0, date(2026, 9, 24)),
        ("72h", 72, date(2026, 9, 21)),
    ]
    for name, hours, want in cases:
        assert tick_cutoff(now, hours) == want, name


def test_bar_cutoff_table():
    now = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
    cases = [
        ("30d", 30, date(2026, 8, 25)),
        ("clamps zero to 1d", 0, date(2026, 9, 23)),
        ("1d", 1, date(2026, 9, 23)),
    ]
    for name, days, want in cases:
        assert bar_cutoff(now, days) == want, name
