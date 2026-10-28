"""H4 direction and M15 pullback. The rule is fixed before the measurement."""

from __future__ import annotations

from pathlib import Path

from trading.domain.murphy_mtf import H4_MS, M15_MS, decision, h4_is_closed, walk_h4_m15


def _pivots(*rows: tuple[int, float, str]) -> list[dict]:
    return [{"index": i, "price": p, "kind": k} for i, p, k in rows]


def test_h4_bar_is_hidden_until_it_closes():
    m15_open = 1_000_000
    still_open = m15_open + M15_MS - H4_MS
    just_closed = m15_open + M15_MS - H4_MS
    assert h4_is_closed(still_open + 1, m15_open) is False
    assert h4_is_closed(just_closed, m15_open) is True


def test_decision_table():
    pivots = _pivots((2, 100.0, "low"), (6, 80.0, "low"), (10, 160.0, "high"), (14, 120.0, "low"))
    cases = [
        ("half pullback longs", "alcista", 130.0, "entrar", "BUY", "retroceso_m15"),
        ("no pullback", "alcista", 170.0, "nada", "BUY", "sin_retroceso"),
        ("past two thirds exits", "alcista", 100.0, "salir", "BUY", "pasa_dos_tercios"),
        ("lateral stays out", "lateral", 130.0, "nada", None, "h4_lateral"),
        ("no h4", "insuficiente", 130.0, "nada", None, "h4_sin_datos"),
    ]
    for name, regime, close, accion, lado, motivo in cases:
        got = decision(regime, pivots, close)
        assert got["envia_ordenes"] is False, name
        assert got["accion"] == accion, name
        assert got["lado"] == lado, name
        assert got["motivo"] == motivo, name
    short = decision("bajista", _pivots((2, 160.0, "high"), (8, 100.0, "low"), (12, 140.0, "high")), 120.0)
    assert short["accion"] == "entrar"
    assert short["lado"] == "SELL"
    assert short["stop"] == 140.0


def test_walk_needs_both_windows_and_never_sends():
    n = 10
    got = walk_h4_m15(
        list(range(n)), [1.0] * n, [1.0] * n, [1.0] * n, [1.0] * n,
        list(range(n)), [1.0] * n, [1.0] * n, [1.0] * n,
    )
    assert got["envia_ordenes"] is False
    assert got["todo"]["n"] == 0
    assert got["regla"] == "h4_direccion_m15_retroceso"


def test_script_does_not_send_orders():
    source = Path("murphy_mtf_dry.py").read_text(encoding="utf-8")
    assert "order_send(" not in source
