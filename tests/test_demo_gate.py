"""Demo execution gate: fail-closed unless paper-demo + XBTFX demo."""

from __future__ import annotations

from trading.domain.demo_gate import (
    TRADE_MODE_CONTEST,
    TRADE_MODE_DEMO,
    TRADE_MODE_REAL,
    account_is_mt5_demo,
    resolve_demo_execution,
)


def test_account_is_mt5_demo_table():
    cases = [
        ("demo trade_mode", TRADE_MODE_DEMO, "XBTFX-MetaTrader5", True),
        ("real trade_mode", TRADE_MODE_REAL, "XBTFX-Demo", False),
        ("contest", TRADE_MODE_CONTEST, "XBTFX-Demo", False),
        ("unknown with DEMO server", 99, "XBTFX-Demo", True),
        ("unknown live server", 99, "XBTFX-MetaTrader5", False),
        ("bad mode", "x", "DEMO", False),
    ]
    for name, mode, server, want in cases:
        assert account_is_mt5_demo(mode, server) is want, name


def test_resolve_demo_execution_table():
    base = dict(
        dry_run_env=False,
        paper_demo_live=True,
        is_xbtfx=True,
        is_demo=True,
    )
    cases = [
        ("all green sends demo", base, True, "DEMO_XBTFX", "ok"),
        (
            "dry run env wins",
            {**base, "dry_run_env": True},
            False,
            "SIM",
            "BOT_DRY_RUN",
        ),
        (
            "missing paper opt-in",
            {**base, "paper_demo_live": False},
            False,
            "BLOCKED",
            "falta_PAPER_DEMO_LIVE",
        ),
        (
            "not xbtfx",
            {**base, "is_xbtfx": False},
            False,
            "BLOCKED",
            "terminal_no_es_XBTFX",
        ),
        (
            "real account",
            {**base, "is_demo": False},
            False,
            "BLOCKED",
            "cuenta_no_es_demo",
        ),
        (
            "dry run even if paper",
            {**base, "dry_run_env": True, "paper_demo_live": True},
            False,
            "SIM",
            "BOT_DRY_RUN",
        ),
    ]
    for name, kwargs, send, mode, reason in cases:
        got = resolve_demo_execution(**kwargs)
        assert got.send_orders is send, name
        assert got.dry_run is (not send), name
        assert got.mode == mode, name
        assert got.reason == reason, name


def test_bot_config_defaults_stay_fail_closed():
    import bot_mt5

    cfg = bot_mt5.Config()
    cases = [
        ("dry_run", cfg.dry_run, True),
        ("paper_demo_live", cfg.paper_demo_live, False),
        ("risk cap", cfg.max_risk_usd, 0.50),
        ("hour cap", cfg.max_opens_per_hour, 60),
        ("one slot", cfg.max_positions_per_symbol, 1),
    ]
    for name, got, want in cases:
        assert got == want, name
