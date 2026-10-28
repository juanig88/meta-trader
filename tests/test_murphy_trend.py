"""Chapter-4 structure reader: regime, line, retrace, and the two-symbol universe."""

from __future__ import annotations

from pathlib import Path

from trading.application.murphy_snapshot import snapshot_from_bars
from trading.domain.murphy_trend import nearest_round, read_degrees, read_structure
from trading.domain.murphy_universe import resolve_universe


def _flat(n: int, *, high: float, low: float, close: float) -> tuple[list[float], list[float], list[float]]:
    return [high] * n, [low] * n, [close] * n


def _punch(highs, lows, closes, index: int, *, high: float, low: float, close: float) -> None:
    highs[index] = high
    lows[index] = low
    closes[index] = close


def test_nearest_round_table():
    cases = [
        ("hundred", 100.0, 100.0),
        ("gold 400", 400.0, 400.0),
        ("gold 500", 500.0, 500.0),
        ("fifty", 50.0, 50.0),
        ("off a 20 grid", 432.0, 440.0),
        ("btc step", 83353.0, 85000.0),
    ]
    for name, price, want in cases:
        assert nearest_round(price) == want, name


def test_nearest_round_rejects_bad_prices():
    for price in (0.0, -10.0, float("nan"), float("inf")):
        assert nearest_round(price) is None


def test_short_or_broken_series_is_not_a_trend():
    cases = [
        ("empty", [], [], []),
        ("one bar", [1.0], [1.0], [1.0]),
        ("mismatched", [1.0, 2.0], [1.0], [1.0, 2.0]),
        ("high below low", [1.0, 1.0, 1.0, 1.0, 1.0], [2.0, 2.0, 2.0, 2.0, 2.0], [1.5] * 5),
        ("nan", [1.0, float("nan")], [1.0, 1.0], [1.0, 1.0]),
    ]
    for name, h, l, c in cases:
        got = read_structure(h, l, c)
        assert got["regime"] == "insuficiente", name
        assert got["postura"] == "sin_datos", name
        assert got["envia_ordenes"] is False, name


def test_uptrend_line_needs_a_third_touch_to_be_valid():
    # Base sits between the valleys (10, 12, 14) and the peaks (16, 18, 19)
    # so only those bars are pivots. Index 14 lands on the 10→12 line.
    n = 22
    highs, lows, closes = _flat(n, high=15.0, low=14.8, close=17.0)
    for index, price in ((2, 10.0), (8, 12.0), (14, 14.0)):
        _punch(highs, lows, closes, index, high=14.9, low=price, close=17.0)
    for index, price in ((5, 16.0), (11, 18.0), (17, 19.0)):
        _punch(highs, lows, closes, index, high=price, low=14.9, close=17.0)
    got = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert got["regime"] == "alcista"
    assert got["postura"] == "solo_lectura"
    assert got["envia_ordenes"] is False
    assert got["linea"]["estado"] == "valida"
    assert got["linea"]["toques"] >= 3
    assert got["linea"]["rompe_dos_cierres"] is False
    assert got["premisa_invalidada"] is False
    assert got["canal"]["ancho"] > 0.0
    assert got["canal"]["falla_en_alcanzar"] is True
    assert got["redondo"]["stop_largo"] == "debajo"


def test_two_closes_break_the_line_a_wick_does_not():
    n = 22
    highs, lows, closes = _flat(n, high=15.0, low=14.8, close=17.0)
    for index, price in ((2, 10.0), (8, 12.0), (14, 14.0)):
        _punch(highs, lows, closes, index, high=14.9, low=price, close=17.0)
    for index, price in ((5, 16.0), (11, 18.0), (17, 19.0)):
        _punch(highs, lows, closes, index, high=price, low=14.9, close=17.0)
    wick_h, wick_l, wick_c = list(highs), list(lows), list(closes)
    wick_l[-1] = 1.0
    wick = read_structure(wick_h, wick_l, wick_c, touch_tol_frac=0.0)
    assert wick["linea"]["rompe_dos_cierres"] is False

    closes[-2] = 1.0
    closes[-1] = 1.0
    highs[-2] = max(highs[-2], 1.0)
    highs[-1] = max(highs[-1], 1.0)
    lows[-2] = 0.5
    lows[-1] = 0.5
    broke = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert broke["linea"]["estado"] == "rota"
    assert broke["linea"]["rompe_dos_cierres"] is True
    assert "rompe_dos_cierres" in broke["motivos"]
    assert broke["premisa_invalidada"] is True
    assert broke["envia_ordenes"] is False


def test_downtrend_and_lateral_table():
    n = 20
    down_h, down_l, down_c = _flat(n, high=27.0, low=25.0, close=18.0)
    for index, price in ((3, 40.0), (9, 34.0), (15, 30.0)):
        _punch(down_h, down_l, down_c, index, high=price, low=25.0, close=18.0)
    for index, price in ((6, 24.0), (12, 20.0)):
        _punch(down_h, down_l, down_c, index, high=27.0, low=price, close=18.0)

    lat_h, lat_l, lat_c = _flat(n, high=12.0, low=10.0, close=11.0)
    for index in (3, 9, 15):
        _punch(lat_h, lat_l, lat_c, index, high=14.0, low=10.0, close=11.0)
    for index in (6, 12):
        _punch(lat_h, lat_l, lat_c, index, high=12.0, low=8.0, close=11.0)

    cases = [
        ("bajista", down_h, down_l, down_c, "bajista", "solo_lectura"),
        ("lateral", lat_h, lat_l, lat_c, "lateral", "afuera"),
    ]
    for name, h, l, c, regime, postura in cases:
        got = read_structure(h, l, c, touch_tol_frac=0.0)
        assert got["regime"] == regime, name
        assert got["postura"] == postura, name
        assert got["envia_ordenes"] is False, name
        if regime == "lateral":
            assert got["linea"] is None, name
            assert got["premisa_invalidada"] is False, name


def test_retracement_thirds():
    # Impulse 100 → 190. Two thirds of that span is 130. 120 is beyond it.
    n = 16
    highs, lows, closes = _flat(n, high=120.0, low=110.0, close=160.0)
    _punch(highs, lows, closes, 4, high=120.0, low=100.0, close=110.0)
    _punch(highs, lows, closes, 8, high=190.0, low=110.0, close=160.0)
    cases = [
        ("one third", 160.0, False),
        ("half", 145.0, False),
        ("on two thirds", 130.0, False),
        ("beyond two thirds", 120.0, True),
    ]
    for name, close, beyond in cases:
        closes[-2] = close
        closes[-1] = close
        got = read_structure(highs, lows, closes, touch_tol_frac=0.0)
        retro = got["retroceso"]
        assert retro is not None, name
        assert retro["impulso_alto"] == 190.0, name
        assert retro["pasa_dos_tercios"] is beyond, name


def test_downtrend_bounce_not_a_further_drop():
    n = 20
    highs, lows, closes = _flat(n, high=27.0, low=25.0, close=18.0)
    for index, price in ((3, 40.0), (9, 34.0), (15, 30.0)):
        _punch(highs, lows, closes, index, high=price, low=25.0, close=18.0)
    for index, price in ((6, 24.0), (12, 20.0)):
        _punch(highs, lows, closes, index, high=27.0, low=price, close=18.0)
    falling = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert falling["regime"] == "bajista"
    assert falling["retroceso"]["pasa_dos_tercios"] is False
    assert falling["premisa_invalidada"] is False

    closes[-2] = 30.0
    closes[-1] = 30.0
    bounced = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert bounced["regime"] == "bajista"
    assert bounced["retroceso"]["pasa_dos_tercios"] is True
    assert "pasa_dos_tercios" in bounced["motivos"]
    assert bounced["envia_ordenes"] is False


def test_equal_lows_warn_without_invalidating():
    n = 18
    highs, lows, closes = _flat(n, high=12.0, low=10.0, close=11.0)
    for index in (3, 9, 15):
        _punch(highs, lows, closes, index, high=14.0, low=10.0, close=11.0)
    for index in (6, 12):
        _punch(highs, lows, closes, index, high=12.0, low=8.0, close=11.0)
    got = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert got["regime"] == "lateral"
    assert got["apoyo_previo"]["minimos_iguales"] is True
    assert got["apoyo_previo"]["maximos_iguales"] is True
    assert got["linea"] is None
    assert got["premisa_invalidada"] is False


def test_fan_third_line_break_is_a_label_not_an_order():
    n = 20
    highs, lows, closes = _flat(n, high=40.0, low=35.0, close=38.0)
    for index, price in ((2, 10.0), (6, 18.0), (10, 22.0), (14, 24.0)):
        _punch(highs, lows, closes, index, high=price + 0.5, low=price, close=38.0)
    for index, price in ((4, 42.0), (8, 46.0), (12, 50.0), (16, 54.0)):
        _punch(highs, lows, closes, index, high=price, low=price - 0.5, close=38.0)
    holding = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert holding["regime"] == "alcista"
    assert holding["abanico"] is not None
    assert holding["abanico"]["lineas"] == 3
    assert holding["abanico"]["rompe_tercera"] is False
    assert holding["envia_ordenes"] is False

    closes[-2] = 5.0
    closes[-1] = 5.0
    lows[-2] = 4.0
    lows[-1] = 4.0
    highs[-2] = max(highs[-2], 5.0)
    highs[-1] = max(highs[-1], 5.0)
    broke = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    assert broke["abanico"]["rompe_tercera"] is True
    assert broke["envia_ordenes"] is False


def test_role_flip_after_two_closes():
    n = 16
    highs, lows, closes = _flat(n, high=12.0, low=11.0, close=11.5)
    _punch(highs, lows, closes, 3, high=20.0, low=19.0, close=19.5)
    for i in range(6, n):
        closes[i] = 22.0
        highs[i] = max(highs[i], 22.0)
    got = read_structure(highs, lows, closes, touch_tol_frac=0.0)
    flips = [row for row in got["cambio_de_papel"] if row["era"] == "resistencia"]
    assert flips
    assert flips[-1]["ahora"] == "apoyo"
    assert flips[-1]["dos_cierres"] is True


def test_degrees_do_not_invent_a_major_trend():
    n = 30
    highs, lows, closes = _flat(n, high=11.0, low=10.0, close=10.5)
    got = read_degrees(highs, lows, closes)
    assert got["envia_ordenes"] is False
    assert got["fase"] == 1
    assert got["grados"]["menor"]["suficiente"] is True
    assert got["grados"]["intermedia"]["suficiente"] is False
    assert got["grados"]["principal"]["suficiente"] is False
    assert got["postura"] == "sin_datos"
    assert got["conflicto_principal"] is False


def test_resolve_universe_table():
    names = ["EURUSD", "BTCUSDTm", "ETHBTCm", "BTCJPYm", "BTCUSDm", "ETHUSDm", "BTCUSD"]
    cases = [
        ("exness standard", ["BTCUSDm", "ETHUSDm", "BTCUSDTm", "ETHBTCm"], {"BTCUSD": "BTCUSDm", "ETHUSD": "ETHUSDm"}),
        ("exact wins over suffix", ["BTCUSD", "BTCUSDm", "ETHUSDm"], {"BTCUSD": "BTCUSD", "ETHUSD": "ETHUSDm"}),
        ("usdt is not btcusd", ["BTCUSDTm", "ETHUSDm"], {"BTCUSD": None, "ETHUSD": "ETHUSDm"}),
        ("empty book", [], {"BTCUSD": None, "ETHUSD": None}),
    ]
    for name, listed, want in cases:
        assert resolve_universe(listed) == want, name
    # The mixed board still resolves the two dollar cryptos and ignores the rest.
    mixed = resolve_universe(names)
    assert mixed["BTCUSD"] == "BTCUSD"
    assert mixed["ETHUSD"] == "ETHUSDm"
    assert "BTCUSDTm" not in mixed.values()


def test_snapshot_stamps_anchor_dates_and_never_orders():
    n = 30
    highs, lows, closes = _flat(n, high=11.0, low=10.0, close=10.5)
    dates = [f"2026-01-{day:02d}" for day in range(1, n + 1)]
    got = snapshot_from_bars(highs, lows, closes, dates)
    assert got["envia_ordenes"] is False
    assert got["ultima_barra"] == "2026-01-30"
    assert got["postura"] == "sin_datos"


def test_reader_script_does_not_send_orders():
    source = Path("murphy_read.py").read_text(encoding="utf-8")
    assert "order_send(" not in source
    assert "TRADE_MODE_REAL" in source
