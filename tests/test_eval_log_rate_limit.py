"""Table-driven tests for EVAL log rate-limit helper."""
from __future__ import annotations

from datetime import datetime, timedelta

from bot_mt5 import eval_log_should_emit


def test_eval_log_should_emit_table() -> None:
    t0 = datetime(2026, 8, 9, 12, 0, 0)
    cases = [
        ("first_emit", {}, "BTC|spread", t0, 120.0, False, True),
        (
            "within_cooldown",
            {"BTC|spread": t0},
            "BTC|spread",
            t0 + timedelta(seconds=30),
            120.0,
            False,
            False,
        ),
        (
            "after_cooldown",
            {"BTC|spread": t0},
            "BTC|spread",
            t0 + timedelta(seconds=120),
            120.0,
            False,
            True,
        ),
        (
            "force_overrides",
            {"BTC|spread": t0},
            "BTC|spread",
            t0 + timedelta(seconds=1),
            120.0,
            True,
            True,
        ),
        (
            "other_key_independent",
            {"BTC|spread": t0},
            "XNG|spread",
            t0 + timedelta(seconds=1),
            120.0,
            False,
            True,
        ),
    ]
    for name, last, key, now, cd, force, want in cases:
        got = eval_log_should_emit(last, key, now, cd, force=force)
        assert got is want, f"{name}: got={got} want={want}"
