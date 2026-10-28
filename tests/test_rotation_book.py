"""Rotation floating-book snapshot: peak-to-trough vs young vs aged legs."""

from __future__ import annotations

from trading.domain.rotation_book import (
    book_harvest_should_flatten,
    summarize_rotation_book,
    update_book_watermarks,
)


def test_summarize_rotation_book_table():
    hour = 3600.0
    cases = [
        (
            "empty book",
            dict(legs=(), ts="t0"),
            {
                "n_open": 0,
                "floating_net": 0.0,
                "n_green": 0,
                "n_red": 0,
                "n_scratch": 0,
                "n_young": 0,
                "n_aged": 0,
                "young_net": 0.0,
                "aged_net": 0.0,
                "best_net": 0.0,
                "worst_net": 0.0,
                "min_age_seconds": None,
                "max_age_seconds": None,
                "hwm": 0.0,
                "trough": 0.0,
                "drawdown_from_hwm": 0.0,
            },
        ),
        (
            "mixed young dump plus one aged",
            dict(
                legs=(
                    {"net": 0.40, "age_seconds": 10.0},
                    {"net": -0.20, "age_seconds": hour + 1.0},
                    {"net": 0.10, "age_seconds": 20.0},
                    {"net": 0.0, "age_seconds": 30.0},
                ),
                ts="t1",
            ),
            {
                "n_open": 4,
                "n_green": 2,
                "n_red": 1,
                "n_scratch": 1,
                "n_young": 3,
                "n_aged": 1,
                "floating_net": 0.3,
                "young_net": 0.5,
                "aged_net": -0.2,
                "best_net": 0.4,
                "worst_net": -0.2,
                "min_age_seconds": 10,
                "max_age_seconds": 3601,
                "hwm": 0.3,
                "drawdown_from_hwm": 0.0,
            },
        ),
        (
            "all young red — stale would not fire",
            dict(
                legs=(
                    {"net": -0.50, "age_seconds": 120.0},
                    {"net": -1.30, "age_seconds": 200.0},
                ),
                ts="t2",
            ),
            {
                "n_open": 2,
                "n_young": 2,
                "n_aged": 0,
                "floating_net": -1.8,
                "young_net": -1.8,
                "aged_net": 0.0,
                "n_red": 2,
                "hwm": -1.8,
                "trough": -1.8,
                "drawdown_from_hwm": 0.0,
            },
        ),
    ]
    for name, kwargs, want in cases:
        got, _wm = summarize_rotation_book(**kwargs)
        assert got["event"] == "ROTATION_BOOK", name
        for key, value in want.items():
            assert got[key] == value, f"{name}: {key} {got[key]!r} != {value!r}"


def test_watermarks_peak_then_trough():
    steps = [
        (
            "first print is both hwm and trough",
            [{"net": 0.50, "age_seconds": 30.0}],
            "10:00",
            None,
            {"hwm": 0.5, "trough": 0.5, "drawdown_from_hwm": 0.0, "hwm_ts": "10:00"},
        ),
        (
            "dump after peak is trough, hwm sticks",
            [
                {"net": -0.60, "age_seconds": 90.0},
                {"net": -1.20, "age_seconds": 80.0},
            ],
            "10:12",
            "prev",
            {
                "hwm": 0.5,
                "hwm_ts": "10:00",
                "trough": -1.8,
                "trough_ts": "10:12",
                "drawdown_from_hwm": 2.3,
                "floating_net": -1.8,
                "n_young": 2,
            },
        ),
        (
            "partial bounce does not move trough or hwm",
            [{"net": -0.40, "age_seconds": 4000.0}],
            "11:00",
            "prev",
            {
                "hwm": 0.5,
                "trough": -1.8,
                "trough_ts": "10:12",
                "drawdown_from_hwm": 0.9,
                "floating_net": -0.4,
                "n_aged": 1,
                "n_young": 0,
            },
        ),
        (
            "new high resets trough",
            [{"net": 0.80, "age_seconds": 10.0}],
            "12:00",
            "prev",
            {
                "hwm": 0.8,
                "hwm_ts": "12:00",
                "trough": 0.8,
                "trough_ts": "12:00",
                "drawdown_from_hwm": 0.0,
            },
        ),
    ]
    wm = None
    for name, legs, ts, _use_prev, want in steps:
        got, wm = summarize_rotation_book(legs, watermarks=wm, ts=ts)
        for key, value in want.items():
            assert got[key] == value, f"{name}: {key} {got[key]!r} != {value!r}"


def test_update_book_watermarks_empty_prev():
    cases = [
        ("first", 0.12, None, {"hwm": 0.12, "trough": 0.12}),
        ("equal keeps high", 0.12, {"hwm": 0.12, "trough": 0.12, "hwm_ts": "a"}, {"hwm": 0.12}),
        ("down", -0.05, {"hwm": 0.12, "trough": 0.12, "hwm_ts": "a"}, {"hwm": 0.12, "trough": -0.05}),
    ]
    for name, net, prev, want in cases:
        got = update_book_watermarks(net, prev, ts="t")
        for key, value in want.items():
            assert got[key] == value, f"{name}: {key} {got[key]!r} != {value!r}"


def test_book_harvest_should_flatten_table():
    cases = [
        ("15:48 print", dict(floating_net=0.5114, n_open=23, min_age_seconds=739.0), True),
        ("exact cap", dict(floating_net=0.50, n_open=8, min_age_seconds=600.0), True),
        ("under cap", dict(floating_net=0.499, n_open=23, min_age_seconds=739.0), False),
        ("too young", dict(floating_net=0.60, n_open=23, min_age_seconds=100.0), False),
        ("single ticket is per-trade TP", dict(floating_net=0.60, n_open=1, min_age_seconds=900.0), False),
        ("empty", dict(floating_net=0.80, n_open=0, min_age_seconds=900.0), False),
        ("no age", dict(floating_net=0.80, n_open=5, min_age_seconds=None), False),
        ("disabled", dict(floating_net=0.80, n_open=10, min_age_seconds=900.0, harvest_usd=0.0), False),
        ("1h floor would have missed 15:48", dict(floating_net=0.5114, n_open=23, min_age_seconds=739.0, min_age_seconds_required=3600.0), False),
    ]
    for name, kwargs, want in cases:
        assert book_harvest_should_flatten(**kwargs) is want, name
