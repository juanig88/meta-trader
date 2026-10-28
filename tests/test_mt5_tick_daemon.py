from __future__ import annotations

from datetime import datetime, timezone

import mt5_tick_daemon as daemon


def test_query_time_from_server_ms_table():
    cases = [
        ("zero offset", 1_700_000_000_000, 0, datetime.fromtimestamp(1_700_000_000)),
        (
            "plus three hours",
            int((1_700_000_000 + 3 * 3600) * 1000),
            3 * 3600,
            datetime.fromtimestamp(1_700_000_000),
        ),
        ("small negative offset", 1_700_000_004_000, -4, datetime.fromtimestamp(1_700_000_008)),
    ]
    for name, ts_ms, offset, want in cases:
        got = daemon._query_time_from_server_ms(ts_ms, offset)
        assert got == want, name
        assert got.tzinfo is None, name


def test_query_time_is_pc_local_not_utc_naive():
    """UTC-stripped naive datetimes make copy_ticks_from return [] on ART."""
    ts_ms = 1_700_000_000_000
    got = daemon._query_time_from_server_ms(ts_ms, 0)
    utc_naive = datetime.fromtimestamp(1_700_000_000, tz=timezone.utc).replace(tzinfo=None)
    local = datetime.fromtimestamp(1_700_000_000)
    assert got == local
    if local != utc_naive:
        assert got != utc_naive


def test_pos_table():
    cases = [
        ("price", 1.234, 1.234),
        ("zero is missing", 0.0, None),
        ("negative", -1.0, None),
        ("none", None, None),
        ("junk", "x", None),
    ]
    for name, value, want in cases:
        assert daemon._pos(value) == want, name
