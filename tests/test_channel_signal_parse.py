from __future__ import annotations

from datetime import datetime, timezone

from trading.domain.channel_signal_parse import (
    normalize_pair,
    parse_price,
    parse_signal_body,
    utc_from_export,
)

TS = datetime(2024, 1, 1, 7, 1, 37, tzinfo=timezone.utc)


def test_parse_price_table():
    cases = [
        ("us thousands", "$28,150", 28150.0),
        ("plain", "77270.1000", 77270.1),
        ("small decimal", "$0.0966", 0.0966),
        ("eu decimal comma", "0,3406", 0.3406),
        ("both separators", "1.234,56", 1234.56),
        ("empty", "", None),
        ("junk", "abc", None),
        ("zero", "0", None),
        ("negative", "-2.5", None),
    ]
    for name, raw, want in cases:
        got = parse_price(raw)
        if want is None:
            assert got is None, name
        else:
            assert got is not None, name
            assert abs(got - want) < 1e-9, name


def test_normalize_pair_table():
    cases = [
        ("btcusdt", "BTCUSDT", "BTCUSDT"),
        ("hash", "#BTC/USDT", "BTCUSDT"),
        ("ticker", "$ETH", "ETHUSDT"),
        ("bitcoin word", "Bitcoin", "BTCUSDT"),
        ("alt", "#GRT/USDT", None),
        ("empty", "", None),
    ]
    for name, raw, want in cases:
        assert normalize_pair(raw) == want, name


def test_parse_neural_multiline_btc_long():
    body = """LONG NUEVA SENAL #1
Par: BTCUSDT
Direccion: LONG
Precio: 77521.9000 USDT
TP: +3.0% -> 79847.5570
SL: -2.0% -> 75971.4620
Confirmaciones: 11/16
Score: 106.364
Senales: TV:COMPRA | Cerca-LiqLong-x50@76087.91(1.8%) | Rebote-desde-LiqZone(70509.17) | Min-crecientes | Soporte-200v(77095.1870) | BB-inf-tocada | EMA-alcista | Vol:1.13x | BB-inf:0.981% | MACD/ROC+ | OB:1.112
Funding: 0.009849%
Mercado: ALCISTA
Hora: 07:01:36 UTC"""
    got = parse_signal_body(body, message_id=19845, timestamp_utc=TS)
    assert got is not None
    assert got.template == "nueva_senal"
    assert got.pair == "BTCUSDT"
    assert got.side == "LONG"
    assert abs(got.entry - 77521.9) < 1e-6
    assert abs(got.tp - 79847.5570) < 1e-3
    assert abs(got.sl - 75971.4620) < 1e-3
    assert abs(got.tp_pct - 3.0) < 1e-9
    assert abs(got.sl_pct - (-2.0)) < 1e-9
    assert got.sl_source == "percent"
    assert got.score == 106.364
    assert got.confirmations[0] == "11/16"
    assert "TV:COMPRA" in got.confirmations
    assert "EMA-alcista" in got.confirmations
    assert got.replayable()


def test_parse_score_card_eth_not_pooled_with_nueva():
    body = """[SCORE: 85.899/100]
SENAL DE ALTA CONFIANZA 8/7
Par:            ETHUSDT
Direccion:      LONG
Precio:         2482.65 USDT
Confirmaciones: 8/7
TP:  +30.0% → 3227.45 USDT
SL:  -15.0% → 2110.25 USDT
"""
    got = parse_signal_body(body, message_id=19797, timestamp_utc=TS)
    assert got is not None
    assert got.template == "score_card"
    assert got.pair == "ETHUSDT"
    assert got.side == "LONG"
    assert abs(got.entry - 2482.65) < 1e-6
    assert abs(got.tp_pct - 30.0) < 1e-9
    assert abs(got.sl_pct - (-15.0)) < 1e-9
    assert got.confirmations[0] == "8/7"
    assert got.replayable()


def test_parse_pipe_btc_long():
    body = (
        "LONG NUEVA SENAL #1 | Par: BTCUSDT | Direccion: LONG | Precio: 77270.1000 | "
        "TP: +5.0% -> 81133.6050 | SL: -2.5% -> 75338.3475 | Score: 8.5 | "
        "Confirmaciones: EMA, RSI | Hora: 07:01:37 UTC"
    )
    got = parse_signal_body(body, message_id=1, timestamp_utc=TS)
    assert got is not None
    assert got.pair == "BTCUSDT"
    assert got.side == "LONG"
    assert got.kind == "structured_trade"
    assert abs(got.entry - 77270.1) < 1e-6
    assert abs(got.tp - 81133.6050) < 1e-4
    assert abs(got.sl - 75338.3475) < 1e-4
    assert abs(got.tp_pct - 5.0) < 1e-9
    assert abs(got.sl_pct - (-2.5)) < 1e-9
    assert got.sl_source == "percent"
    assert got.score == 8.5
    assert got.confirmations == ("EMA", "RSI")
    assert got.replayable()


def test_parse_pipe_btc_long_2026_usdt():
    body = (
        "LONG NUEVA SENAL #1 | Par: BTCUSDT | Direccion: LONG | Precio: 77521.9000 USDT | "
        "TP: +3.0% -> 79847.5570 | SL: -2.0% -> 75971.4620 | Confirmaciones: 11/16 | "
        "Score: 106.364 | Senales: foo"
    )
    got = parse_signal_body(body, message_id=19845, timestamp_utc=TS)
    assert got is not None
    assert got.pair == "BTCUSDT"
    assert got.side == "LONG"
    assert abs(got.entry - 77521.9) < 1e-6
    assert abs(got.tp_pct - 3.0) < 1e-9
    assert abs(got.sl_pct - (-2.0)) < 1e-9
    assert got.score == 106.364
    assert got.confirmations == ("11/16", "foo")
    assert got.template in ("neural", "pipe")

    body = "Par: ETHUSDT | Direccion: SHORT | Precio: 2000 | TP: 4% | SL: 2%"
    got = parse_signal_body(body, message_id=2, timestamp_utc=TS)
    assert got is not None
    assert got.pair == "ETHUSDT"
    assert got.side == "SHORT"
    assert abs(got.tp - 1920.0) < 1e-9
    assert abs(got.sl - 2040.0) < 1e-9


def test_parse_bullet_and_edges():
    cases = [
        (
            "btc long",
            "• BTC - Largo\n• Entrada: $28,150\n• TP: $29,000\n• Precio de liquidación: $26,010",
            "BTCUSDT",
            "LONG",
            28150.0,
            29000.0,
            26010.0,
            "liquidation",
        ),
        (
            "btc short",
            "• BTC - Corto\n• Entrada: $28,550\n• TP: $28,000\n• Precio de liquidación: $32,300",
            "BTCUSDT",
            "SHORT",
            28550.0,
            28000.0,
            32300.0,
            "liquidation",
        ),
    ]
    for name, body, pair, side, entry, tp, sl, sl_source in cases:
        got = parse_signal_body(body, message_id=3, timestamp_utc=TS)
        assert got is not None, name
        assert got.pair == pair, name
        assert got.side == side, name
        assert got.entry == entry, name
        assert got.tp == tp, name
        assert got.sl == sl, name
        assert got.sl_source == sl_source, name


def test_parse_rejects_empty_and_alts():
    cases = [
        ("empty", ""),
        ("whitespace", "   "),
        ("grt", "Tipo de señal: SEÑAL LARGA\nPar: #GRT/USDT\nENTRADA: 0.112\nStop loss: 0.1030"),
        ("doge bullet", "• DOGE - Largo\n• Entrada: $0.0966\n• TP: $0.1"),
        ("no levels", "BTCUSDT se ve interesante hoy"),
    ]
    for name, body in cases:
        assert parse_signal_body(body, message_id=4, timestamp_utc=TS) is None, name


def test_claimed_eth_result():
    body = "#ETH/USDT\nTake Profit Target 1 ✅🚀\nTake Profit Target 2 ✅🚀"
    got = parse_signal_body(body, message_id=5, timestamp_utc=TS)
    assert got is not None
    assert got.kind == "claimed_result"
    assert got.pair == "ETHUSDT"
    assert not got.replayable()


def test_utc_from_export_table():
    cases = [
        ("offset", "2023-04-05 02:13:32+00:00"),
        ("zulu", "2023-04-05 02:13:32Z"),
    ]
    for name, raw in cases:
        got = utc_from_export(raw)
        assert got.tzinfo is not None, name
        assert got.year == 2023, name
        assert got.hour == 2, name
