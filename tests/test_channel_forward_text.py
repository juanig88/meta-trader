from __future__ import annotations

from trading.domain.channel_forward_text import forward_nueva_senal_text

NUEVA = """LONG NUEVA SENAL #1
Par: BTCUSDT
Direccion: LONG
Precio: 77521.9000 USDT
TP: +3.0% -> 79847.5570
SL: -2.0% -> 75971.4620
Confirmaciones: 11/16
Score: 106.364
Senales: TV:COMPRA | EMA-alcista
--------------------------------
Opera con Neural Trade IA
BingX - hasta 6.000$ en bonos: https://bingx.com/invite/LB5HPD/
"""

SCORE = """[SCORE: 85.899/100]
SENAL DE ALTA CONFIANZA 8/7
Par:            ETHUSDT
Direccion:      LONG
Precio:         2482.65 USDT
TP:  +30.0% → 3227.45 USDT
SL:  -15.0% → 2110.25 USDT
"""


def test_forward_nueva_senal_text_table():
    cases = [
        ("nueva senal keeps the four lines", NUEVA, True),
        ("score card is not this type", SCORE, False),
        ("video", "MAÑANA PUEDE SER UN DÍA HISTÓRICO PARA BITCOIN", False),
        ("empty", "", False),
        ("whitespace", "  \n", False),
        ("promo only", "Opera con Neural Trade IA", False),
        ("missing sl", "LONG NUEVA SENAL #1\nPar: BTCUSDT\nDireccion: LONG\nPrecio: 1\nTP: +3%", False),
    ]
    for name, body, want_text in cases:
        got = forward_nueva_senal_text(body)
        if not want_text:
            assert got is None, name
            continue
        assert got is not None, name
        assert got.startswith("SantinoCripto\n"), name
        assert "Direccion: LONG" in got, name
        assert "Precio: 77521.9000 USDT" in got, name
        assert "TP: +3.0% -> 79847.5570" in got, name
        assert "SL: -2.0% -> 75971.4620" in got, name
        assert "Par: BTCUSDT" in got, name
        assert "Confirmaciones" not in got, name
        assert "BingX" not in got, name
        assert "Score" not in got, name
