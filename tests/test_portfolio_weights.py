"""Portfolio weight parsing: default renormalize vs keep-sleeve."""

from __future__ import annotations

from bot_mt5 import _parse_portfolio_weights


def test_parse_portfolio_weights_table():
    raw = '{"BTCUSD":0.28,"ETHUSD":0.10,"USDJPY":0.24}'
    symbols = ("BTCUSD", "ETHUSD", "USDJPY")
    cases = [
        (
            "renormalize_default",
            True,
            {
                "BTCUSD": 0.28 / 0.62,
                "ETHUSD": 0.10 / 0.62,
                "USDJPY": 0.24 / 0.62,
            },
        ),
        (
            "keep_sleeve",
            False,
            {"BTCUSD": 0.28, "ETHUSD": 0.10, "USDJPY": 0.24},
        ),
    ]
    for name, renormalize, want in cases:
        got = _parse_portfolio_weights(raw, symbols, renormalize=renormalize)
        assert got.keys() == want.keys(), name
        for k, v in want.items():
            assert abs(got[k] - v) < 1e-12, (name, k, got[k], v)


def test_parse_portfolio_weights_edges():
    cases = [
        ("empty_symbols", "{}", (), True, {}),
        (
            "empty_raw_equal",
            "",
            ("BTCUSD", "ETHUSD"),
            True,
            {"BTCUSD": 0.5, "ETHUSD": 0.5},
        ),
        (
            "zero_weights_equal",
            '{"BTCUSD":0,"ETHUSD":0}',
            ("BTCUSD", "ETHUSD"),
            False,
            {"BTCUSD": 0.5, "ETHUSD": 0.5},
        ),
        (
            "drops_unknown_keys",
            '{"BTCUSD":0.28,"EURUSD":0.28}',
            ("BTCUSD",),
            False,
            {"BTCUSD": 0.28},
        ),
    ]
    for name, raw, symbols, renormalize, want in cases:
        got = _parse_portfolio_weights(raw, symbols, renormalize=renormalize)
        assert got == want, (name, got)
