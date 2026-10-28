"""Stale rotation close: after min age, cut loss/take; optional time-stop of the band."""

from __future__ import annotations

from trading.domain.rotation_stale import stale_rotation_close_reason


def test_stale_rotation_close_reason_table():
    hour = 3600.0
    cases = [
        ("too young loss", dict(age_seconds=3599.0, profit=-0.02, min_age_seconds=hour), None),
        ("too young take", dict(age_seconds=100.0, profit=0.05, min_age_seconds=hour), None),
        ("tiny loss stays", dict(age_seconds=hour, profit=-0.00005, min_age_seconds=hour), None),
        ("exactly -0.0001 stays", dict(age_seconds=hour, profit=-0.0001, min_age_seconds=hour), None),
        ("flat stays", dict(age_seconds=hour, profit=0.0, min_age_seconds=hour), None),
        ("small green stays", dict(age_seconds=hour, profit=0.005, min_age_seconds=hour), None),
        ("exactly 0.01 stays", dict(age_seconds=hour, profit=0.01, min_age_seconds=hour), None),
        ("loss below floor", dict(age_seconds=hour, profit=-0.00011, min_age_seconds=hour), "stale_loss"),
        ("real loss", dict(age_seconds=hour, profit=-0.02, min_age_seconds=hour), "stale_loss"),
        ("take above 0.01", dict(age_seconds=hour, profit=0.01001, min_age_seconds=hour), "stale_take"),
        ("disabled age", dict(age_seconds=9999.0, profit=-0.02, min_age_seconds=0.0), None),
        ("disabled take keeps green", dict(age_seconds=hour, profit=0.50, take_usd=0.0, min_age_seconds=hour), None),
        ("five min still young", dict(age_seconds=299.0, profit=-0.02, min_age_seconds=300.0), None),
        ("five min loss", dict(age_seconds=300.0, profit=-0.02, min_age_seconds=300.0), "stale_loss"),
        ("band stays without close_all", dict(age_seconds=300.0, profit=0.005, min_age_seconds=300.0), None),
        (
            "band time-stops with close_all",
            dict(age_seconds=300.0, profit=0.005, min_age_seconds=300.0, close_all_after_age=True),
            "stale_time",
        ),
        (
            "close_all still tags take",
            dict(age_seconds=300.0, profit=0.02, min_age_seconds=300.0, close_all_after_age=True),
            "stale_take",
        ),
    ]
    for name, kwargs, want in cases:
        got = stale_rotation_close_reason(**kwargs)
        assert got == want, f"{name}: {got!r} != {want!r}"
