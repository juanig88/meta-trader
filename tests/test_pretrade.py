"""Chequeo previo: tamaño, R:R y razones en contra. Nunca opina la dirección."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading.domain.pretrade import (
    Context, Plan, breakeven_winrate, reasons_against, reward_risk, size_lots, trend, validate,
)


def ctx(**kw):
    base = dict(usd_per_price_unit=1.0, volume_min=0.01, volume_step=0.01, spread_price=6.4,
                sma200_d1=73000.0, trends={"D1": "alcista", "H4": "alcista", "H1": "alcista"},
                typical_move_15m=60.0, last_move_15m=0.0)
    base.update(kw)
    return Context(**base)


def test_validate_sides():
    assert validate(Plan("BUY", 100, 90, 120)) is None
    assert validate(Plan("SELL", 100, 110, 80)) is None
    assert "debajo" in validate(Plan("BUY", 100, 110, 120))
    assert "arriba" in validate(Plan("SELL", 100, 90, 80))


def test_rr_and_breakeven():
    assert reward_risk(Plan("BUY", 100, 90, 150)) == 5
    assert breakeven_winrate(5) == pytest.approx(1 / 6)
    assert breakeven_winrate(1) == 0.5


def test_size_rounds_down_and_reports_real_risk():
    # La operación del 27/10: 0.01 BTC con stop a 340 USD de precio = 3.40 USD.
    plan = Plan("BUY", 80645.86, 80305.74, 81005.47)
    lots, risk = size_lots(risk_usd=10.0, plan=plan, ctx=ctx())
    assert lots == 0.02 and risk == pytest.approx(6.80, abs=0.01)
    lots, risk = size_lots(risk_usd=1.0, plan=plan, ctx=ctx())
    assert lots == 0.01 and risk == pytest.approx(3.40, abs=0.01)


def test_min_lot_over_limit_is_the_first_reason():
    plan = Plan("BUY", 86700, 79100, 124700)
    lots, risk = size_lots(risk_usd=10.0, plan=plan, ctx=ctx())
    reasons = reasons_against(plan=plan, ctx=ctx(), lots=lots, risk_usd=10.0, real_risk_usd=risk)
    assert "lote mínimo" in reasons[0]


def test_reasons_cover_sma_trend_chase_noise_and_rr():
    plan = Plan("BUY", 70000, 69970, 70060)
    c = ctx(trends={"D1": "bajista", "H4": "alcista", "H1": "mixta"}, last_move_15m=120.0)
    lots, risk = size_lots(risk_usd=10.0, plan=plan, ctx=c)
    text = " ".join(reasons_against(plan=plan, ctx=c, lots=lots, risk_usd=10.0, real_risk_usd=risk))
    for needle in ("200 días", "D1 bajista", "H1 mixta", "persiguiendo", "ruido", "5 a 1", "spread"):
        assert needle in text, needle


def test_clean_plan_has_no_reasons():
    plan = Plan("BUY", 80000, 79800, 81000)
    lots, risk = size_lots(risk_usd=10.0, plan=plan, ctx=ctx(spread_price=1.0))
    assert reasons_against(plan=plan, ctx=ctx(spread_price=1.0), lots=lots, risk_usd=10.0, real_risk_usd=risk) == []


def test_trend_labels():
    assert trend([float(i) for i in range(40)]) == "alcista"
    assert trend([float(40 - i) for i in range(40)]) == "bajista"
    assert trend([1.0] * 10) == "sin_datos"


def test_cli_never_sends_orders():
    assert "order_send" not in Path("chequeo_previo.py").read_text(encoding="utf-8")
