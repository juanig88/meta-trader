from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from trading.domain.currency_exposure import (
    CurrencyPair,
    ExposureLimits,
    PositionLot,
    aggregate_exposure,
    check_new_trade,
    parse_currency_overrides,
    parse_symbol,
    signed_legs,
)


def test_parse_symbol_table():
    overrides = {"NAS100": ("NAS", "USD"), "GER40": ("GER", "EUR")}
    cases = [
        ("fx six letter", "EURUSD", None, CurrencyPair("EUR", "USD")),
        ("jpy", "USDJPY", None, CurrencyPair("USD", "JPY")),
        ("crypto implicit", "BTCUSD", None, CurrencyPair("BTC", "USD")),
        ("metal implicit", "XAUUSD", None, CurrencyPair("XAU", "USD")),
        ("mini suffix", "EURUSDm", None, CurrencyPair("EUR", "USD")),
        ("override index", "NAS100", overrides, CurrencyPair("NAS", "USD")),
        ("unknown index", "US30", None, None),
        ("empty", "", None, None),
        ("lowercase", "gbpusd", None, CurrencyPair("GBP", "USD")),
    ]
    for name, symbol, ov, want in cases:
        got = parse_symbol(symbol, ov)
        assert got == want, name


def test_parse_overrides_table():
    cases = [
        ("empty", "", {}),
        ("none", None, {}),
        ("junk json", "{not-json", {}),
        (
            "valid",
            '{"BTCUSD":["BTC","USD"],"XAUUSD":["XAU","USD"]}',
            {"BTCUSD": ("BTC", "USD"), "XAUUSD": ("XAU", "USD")},
        ),
        ("mapping", {"ethusd": ["eth", "usd"]}, {"ETHUSD": ("ETH", "USD")}),
        ("short list ignored", '{"FOO":["ONLY"]}', {}),
    ]
    for name, raw, want in cases:
        assert parse_currency_overrides(raw) == want, name


def test_signed_legs_table():
    pair = CurrencyPair("USD", "JPY")
    cases = [
        ("buy", "BUY", 0.10, {"USD": 0.10, "JPY": -0.10}),
        ("sell", "SELL", 0.10, {"USD": -0.10, "JPY": 0.10}),
        ("zero volume", "BUY", 0.0, {}),
        ("nan volume", "BUY", float("nan"), {}),
        ("bad side", "FLAT", 0.10, {}),
    ]
    for name, side, volume, want in cases:
        assert signed_legs(pair, side, volume) == want, name


def test_aggregate_and_shared_usd():
    positions = [
        PositionLot("USDJPY", "BUY", 0.10),
        PositionLot("USDCHF", "BUY", 0.10),
        PositionLot("EURUSD", "BUY", 0.05),
    ]
    books = aggregate_exposure(positions)
    # USDJPY +0.10 USD, USDCHF +0.10 USD, EURUSD -0.05 USD
    assert abs(books["USD"].net - 0.15) < 1e-12
    assert abs(books["USD"].gross - 0.25) < 1e-12
    assert abs(books["JPY"].net - (-0.10)) < 1e-12
    assert abs(books["EUR"].net - 0.05) < 1e-12


def test_check_new_trade_table():
    open_usd = [PositionLot("USDJPY", "BUY", 0.10)]
    limits_net = ExposureLimits(max_net=0.15, max_gross=0.0)
    limits_gross = ExposureLimits(max_net=0.0, max_gross=0.20)
    cases = [
        (
            "under net cap",
            open_usd,
            "USDCHF",
            "BUY",
            0.04,
            limits_net,
            True,
            "ok",
        ),
        (
            "shared usd breaches net",
            open_usd,
            "USDCHF",
            "BUY",
            0.06,
            limits_net,
            False,
            "net_cap",
        ),
        (
            "offsetting usd allowed",
            open_usd,
            "EURUSD",
            "BUY",
            0.10,
            limits_net,
            True,
            "ok",
        ),
        (
            "gross cap",
            open_usd,
            "USDCHF",
            "SELL",
            0.11,
            limits_gross,
            False,
            "gross_cap",
        ),
        (
            "no cap always ok",
            open_usd,
            "USDCHF",
            "BUY",
            10.0,
            ExposureLimits(),
            True,
            "no_cap",
        ),
        (
            "unknown symbol fail-open",
            open_usd,
            "US30",
            "BUY",
            1.0,
            limits_net,
            True,
            "unknown_symbol",
        ),
        (
            "empty book first trade",
            [],
            "EURUSD",
            "BUY",
            0.10,
            limits_net,
            True,
            "ok",
        ),
    ]
    for name, positions, symbol, side, volume, limits, allowed, reason in cases:
        verdict = check_new_trade(positions, symbol, side, volume, limits=limits)
        assert verdict.allowed is allowed, name
        assert verdict.reason == reason, name


def test_bot_helper_disabled_is_noop():
    import bot_mt5

    cfg = bot_mt5.Config(currency_exposure_enabled=False, max_net_exposure_per_currency=0.01)
    assert bot_mt5._currency_exposure_blocks_open(cfg, "USDJPY", "BUY", 0.10) is False


def test_bot_helper_blocks_when_enabled():
    import bot_mt5

    cfg = bot_mt5.Config(
        symbols=("USDJPY", "USDCHF"),
        currency_exposure_enabled=True,
        max_net_exposure_per_currency=0.15,
        currency_symbol_overrides={},
    )
    existing = SimpleNamespace(symbol="USDJPY", type=0, volume=0.10, magic=cfg.magic)

    with (
        patch.object(bot_mt5.mt5, "positions_get", return_value=[existing]),
        patch.object(bot_mt5, "_position_is_managed_by_bot", return_value=True),
        patch.object(bot_mt5, "_position_side", return_value="BUY"),
        patch.object(bot_mt5, "_log_event"),
    ):
        assert bot_mt5._currency_exposure_blocks_open(cfg, "USDCHF", "BUY", 0.10) is True
        assert bot_mt5._currency_exposure_blocks_open(cfg, "USDCHF", "BUY", 0.04) is False


def test_bot_helper_fail_open_on_error():
    import bot_mt5

    cfg = bot_mt5.Config(currency_exposure_enabled=True, max_net_exposure_per_currency=0.1)
    with patch.object(bot_mt5, "_managed_position_lots", side_effect=RuntimeError("mt5 down")):
        assert bot_mt5._currency_exposure_blocks_open(cfg, "EURUSD", "BUY", 0.01) is False
