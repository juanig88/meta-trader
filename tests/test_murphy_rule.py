"""Dry rule, cost math, and the labels that are not a second system."""

from __future__ import annotations

from pathlib import Path

from trading.domain.murphy_rule import signal_from_reading, walk_journal
from trading.domain.murphy_tools import gap, r_multiple, ray_at, reversal_day, stop_fill, volume_ratio
from trading.domain.murphy_trend import read_structure


def _reading(*, regime="alcista", menor="bajista", frac=0.4, pasa=False, invalida=False, stop=90.0, conflicto=False):
    return {
        "envia_ordenes": False,
        "conflicto_principal": conflicto,
        "grados": {
            "menor": {"suficiente": True, "regime": menor},
            "intermedia": {
                "suficiente": True,
                "regime": regime,
                "premisa_invalidada": invalida,
                "retroceso": {"frac_cierre": frac, "pasa_dos_tercios": pasa},
                "apoyo_previo": {"ultimo": stop},
                "motivos": ["premisa"] if invalida else [],
            },
            "principal": {"suficiente": True, "regime": regime},
        },
    }


def test_signal_table():
    cases = [
        ("pullback long", _reading(), "entrar", "BUY", "retroceso_menor"),
        ("lateral stays out", _reading(regime="lateral", menor="lateral"), "nada", None, "lateral"),
        ("conflict stays out", _reading(conflicto=True), "nada", None, "conflicto_principal"),
        ("past two thirds", _reading(pasa=True, frac=0.8), "nada", None, "sin_retroceso_operable"),
        ("no giveback yet", _reading(frac=0.0), "nada", None, "sin_retroceso_operable"),
        ("minor regime does not gate", _reading(menor="alcista"), "entrar", "BUY", "retroceso_menor"),
        ("broken premise exits", _reading(invalida=True), "salir", "BUY", "premisa"),
        ("short pullback", _reading(regime="bajista", menor="alcista", stop=110.0), "entrar", "SELL", "retroceso_menor"),
    ]
    for name, reading, accion, lado, motivo in cases:
        got = signal_from_reading(reading)
        assert got["envia_ordenes"] is False, name
        assert got["accion"] == accion, name
        assert got["lado"] == lado, name
        assert got["motivo"] == motivo, name


def test_r_and_stop_fill_table():
    assert r_multiple(100, 110, 10, "BUY") == 1
    assert r_multiple(100, 90, 10, "SELL") == 1
    assert r_multiple(100, 90, 10, "BUY") == -1
    assert r_multiple(100, 110, 0, "BUY") is None
    assert stop_fill("BUY", 95, 100, 101, 96) is None
    assert stop_fill("BUY", 95, 100, 101, 94) == 95
    assert stop_fill("BUY", 95, 90, 101, 89) == 90
    assert stop_fill("SELL", 105, 100, 106, 99) == 105
    assert stop_fill("SELL", 105, 110, 112, 109) == 110


def test_labels_are_not_orders():
    assert reversal_day(10, 8, 12, 9, 9.2) == "cambio_bajista"
    assert reversal_day(10, 8, 11, 7, 10.5) == "cambio_alcista"
    assert reversal_day(10, 8, 11, 9, 10) is None
    assert gap(10, 8, 12, 11) == "hueco_alza"
    assert gap(10, 8, 7, 6) == "hueco_baja"
    assert gap(10, 8, 10, 8) is None
    assert volume_ratio([1, 1, 1, 4], lookback=3) == 4
    assert ray_at(100, 190, 2 / 3, 0, 10, 10) == 160
    assert ray_at(100, 190, 1 / 3, 0, 10, 10) == 130


def test_min_span_refuses_a_short_line_on_a_long_window():
    n = 22
    highs = [15.0] * n
    lows = [14.8] * n
    closes = [17.0] * n
    for index, price in ((2, 10.0), (8, 12.0), (14, 14.0)):
        highs[index], lows[index] = 14.9, price
    for index, price in ((5, 16.0), (11, 18.0), (17, 19.0)):
        highs[index], lows[index] = price, 14.9
    short = read_structure(highs, lows, closes, touch_tol_frac=0.0, min_span=0)
    long = read_structure(highs, lows, closes, touch_tol_frac=0.0, min_span=60)
    assert short["linea"]["extension"] < 60
    assert long["regime"] == "alcista"
    assert long["linea"] is None


def test_log_line_accepts_a_doubling_touch_the_linear_line_misses():
    n = 30
    highs = [55.0] * n
    lows = [50.0] * n
    closes = [52.0] * n
    for index, price in ((2, 10.0), (12, 20.0), (22, 40.0)):
        highs[index], lows[index], closes[index] = 55.0, price, price
    for index, price in ((6, 60.0), (16, 80.0), (24, 90.0)):
        highs[index], lows[index], closes[index] = price, 50.0, price
    highs[-2], highs[-1] = 210.0, 210.0
    lows[-2], lows[-1] = 190.0, 190.0
    closes[-2], closes[-1] = 200.0, 200.0
    linear = read_structure(highs, lows, closes, touch_tol_frac=0.0, scale="linear")
    logged = read_structure(highs, lows, closes, touch_tol_frac=0.0, scale="log")
    assert linear["linea"]["toques_extra"] == 0
    assert logged["linea"]["estado"] == "valida"
    assert logged["linea"]["escala"] == "log"
    assert logged["linea"]["toques_extra"] >= 1


def test_walk_uses_the_injected_reader_and_never_sends():
    def read(highs, lows, closes):
        i = len(closes) - 1
        if i < 185:
            return _reading(frac=0.0)
        if i < 190:
            return _reading()
        return _reading(invalida=True)

    n = 200
    highs = [110.0] * n
    lows = [95.0] * n
    closes = [100.0] * n
    opens = [100.0] * n
    # Entry on the signal at 184 fills the open of 185. Stop is 90, so risk is 10.
    # Exit signal at 190 fills the open of 191, still 100, so R is about 0 before spread.
    got = walk_journal(highs, lows, closes, opens, spread_price=0.0, read=read)
    assert got["envia_ordenes"] is False
    assert got["todo"]["n"] == 1
    assert got["operaciones"][0]["motivo_salida"] == "premisa"
    assert got["operaciones"][0]["r"] == 0


def test_dry_script_does_not_send_orders():
    source = Path("murphy_dry.py").read_text(encoding="utf-8")
    assert "order_send(" not in source
