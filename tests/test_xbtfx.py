"""XBTFX gate, symbol roots, contract specs, and reconnect policy."""

from __future__ import annotations

from trading.domain.xbtfx import (
    account_is_xbtfx,
    alias_hint,
    classify_terminal_error,
    connection_decision,
    contract_snapshot,
    margin_band,
    parse_server_markers,
    reconnect_sleep_seconds,
    root_symbol,
    should_retry,
)
from trading.infrastructure.xbtfx_session import enforcement_enabled, rejection_reason


def test_root_symbol_table():
    cases = [
        ("crypto bang", "BTCUSD!", "BTCUSD"),
        ("forex dot", "USDJPY.", "USDJPY"),
        ("bare", "EURUSD", "EURUSD"),
        ("nasdaq venue", "NVDA.NAS", "NVDA"),
        ("nyse extended", "TSM.NYSE-24", "TSM"),
        ("hash prefix", "#ETHUSD!", "ETHUSD"),
        ("broker prefix", "XBTFX.XAUUSD.", "XAUUSD"),
        ("empty", "", ""),
        ("lower", "btcusd!", "BTCUSD"),
    ]
    for name, raw, want in cases:
        assert root_symbol(raw) == want, name


def test_alias_hint_does_not_pick_when_ambiguous():
    assert alias_hint("BTCUSD", ["BTCUSD!"]) == " broker lists BTCUSD!"
    assert "ambiguous" in alias_hint("BTCUSD", ["BTCUSD!", "BTCUSD."])
    assert alias_hint("BTCUSD!", ["BTCUSD!"]) == ""
    assert alias_hint("GBPUSD", []) == ""


def test_server_gate_table():
    markers = parse_server_markers("")
    assert markers == ("XBTFX",)
    cases = [
        ("live server", "XBTFX-MetaTrader5", "XBTFX Ltd", True),
        ("company only", "", "XBTFX", True),
        ("other broker", "ICMarketsSC-Demo", "IC Markets", False),
        ("blank", "", "", False),
    ]
    for name, server, company, want in cases:
        assert account_is_xbtfx(server, company, markers) is want, name


def test_connection_decision_table():
    markers = ("XBTFX",)
    base = dict(markers=markers, enforced=True, max_attempts=5, base_sleep=2.0, attempt=1)
    cases = [
        ("accept xbtfx", dict(initialized=True, server="XBTFX-MetaTrader5", company="XBTFX"), "accept"),
        ("reject other", dict(initialized=True, server="ICMarketsSC-Demo", company="IC Markets"), "reject_broker"),
        ("enforce off", dict(initialized=True, server="Other", company="Other", enforced=False), "accept"),
        ("timeout retries", dict(initialized=False, server="", company="", error_code=-10005, error_message="timeout"), "retry"),
        ("auth stops on second", dict(initialized=False, server="", company="", error_code=-6, error_message="auth", attempt=2), "stop"),
        ("fatal stops", dict(initialized=False, server="", company="", error_code=-2, error_message="invalid"), "stop"),
        ("last attempt stops", dict(initialized=False, server="", company="", error_code=-10004, error_message="connect", attempt=5), "stop"),
    ]
    for name, extra, want in cases:
        kwargs = {**base, **extra}
        action, sleep_s = connection_decision(**kwargs)
        assert action == want, name
        if want == "retry":
            assert sleep_s > 0.0, name
        else:
            assert sleep_s == 0.0, name


def test_reconnect_backoff_and_error_class():
    assert classify_terminal_error(-10005, "timeout") == "retryable"
    assert classify_terminal_error(-6, "authorization failed") == "auth"
    assert classify_terminal_error(-8, "autotrading disabled") == "fatal"
    assert classify_terminal_error(1, "Success") == "ok"
    assert reconnect_sleep_seconds(1, 2.0, "retryable") == 2.0
    assert reconnect_sleep_seconds(3, 2.0, "retryable") == 8.0
    assert reconnect_sleep_seconds(10, 2.0, "retryable") == 30.0
    assert reconnect_sleep_seconds(4, 2.0, "auth") == 2.0
    assert should_retry("retryable", 1, 5) is True
    assert should_retry("fatal", 1, 5) is False
    assert should_retry("auth", 2, 5) is False


def test_contract_and_margin_table():
    crypto = contract_snapshot(
        {
            "name": "BTCUSD!",
            "point": 0.01,
            "digits": 2,
            "trade_mode": 4,
            "calc_mode": 4,
            "trade_exemode": 2,
            "spread_float": True,
            "trade_contract_size": 1.0,
            "volume_min": 0.01,
            "volume_step": 0.01,
            "volume_max": 20.0,
            "filling_mode": 3,
            "currency_base": "BTC",
            "currency_profit": "USD",
            "currency_margin": "USD",
            "margin_initial": 0.0,
        }
    )
    assert crypto.asset_class == "crypto"
    assert crypto.execution == "ecn"
    assert crypto.tradable is True
    assert crypto.filling == ("FOK", "IOC")

    disabled = contract_snapshot(
        {
            "name": "EURUSD",
            "point": 0.00001,
            "trade_mode": 0,
            "volume_min": 0.01,
            "volume_step": 0.01,
            "trade_contract_size": 100000,
        }
    )
    assert disabled.tradable is False
    assert disabled.reason == "trade_disabled"
    assert disabled.asset_class == "forex"

    equity = contract_snapshot(
        {
            "name": "NVDA.NAS",
            "point": 0.01,
            "trade_mode": 4,
            "calc_mode": 32,
            "trade_exemode": 3,
            "volume_min": 1,
            "volume_step": 1,
            "trade_contract_size": 1,
        }
    )
    assert equity.asset_class == "equity_cfd"
    assert equity.execution == "exchange"

    broken = contract_snapshot({"name": "XAUUSD.", "point": 0.0, "trade_mode": 4})
    assert broken.asset_class == "metal"
    assert broken.reason == "point"

    level, band = margin_band(1000.0, 0.0, 100.0, 50.0)
    assert level is None and band == "none"
    _level, band = margin_band(55.0, 100.0, 100.0, 50.0)
    assert band == "critical"
    _level, band = margin_band(90.0, 100.0, 100.0, 50.0)
    assert band == "warn"
    _level, band = margin_band(150.0, 100.0, 200.0, 50.0)
    assert band == "warn"
    _level, band = margin_band(250.0, 100.0, 100.0, 50.0)
    assert band == "caution"
    _level, band = margin_band(1000.0, 100.0, 100.0, 50.0)
    assert band == "ok"
    assert margin_band("bad", 1, None, None) == (None, "unknown")


def test_rejection_reason_fail_closed(monkeypatch):
    class _Acc:
        def __init__(self, server, company):
            self.server = server
            self.company = company

    class _Mt5:
        def __init__(self, acc):
            self._acc = acc

        def account_info(self):
            return self._acc

    monkeypatch.delenv("MT5_XBTFX_ONLY", raising=False)
    monkeypatch.delenv("MT5_BROKER_SERVER_MARKERS", raising=False)
    assert rejection_reason(_Mt5(_Acc("XBTFX-MetaTrader5", "XBTFX"))) == ""
    reason = rejection_reason(_Mt5(_Acc("ICMarketsSC-Demo", "IC Markets")))
    assert reason.startswith("XBTFX gate")
    assert rejection_reason(_Mt5(None)).startswith("XBTFX gate")
    monkeypatch.setenv("MT5_XBTFX_ONLY", "0")
    assert enforcement_enabled() is False
    assert rejection_reason(_Mt5(_Acc("Other", "Other"))) == ""
