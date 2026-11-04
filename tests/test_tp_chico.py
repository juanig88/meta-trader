"""TP chico C1 cycle: side, levels, the skip after 9 wins and the stop rules."""

from __future__ import annotations

import pandas as pd
import pytest

from trading.domain.tp_chico import (
    BUY, DONE, OPEN, SELL, SKIP_CYCLE, CycleState, decide, ema_last, levels, record_close, trend_side,
)


def test_ema_matches_pandas():
    closes = [100.0 + (i % 7) * 3 - i * 0.5 for i in range(60)]
    expected = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
    assert ema_last(closes) == pytest.approx(expected)
    assert ema_last(closes[:10]) is None


@pytest.mark.parametrize("price,ema,side", [(99.0, 100.0, SELL), (101.0, 100.0, BUY), (100.0, None, 0)])
def test_trend_side(price, ema, side):
    assert trend_side(price, ema) == side


def test_levels_from_the_fill():
    assert levels(SELL, bid=77_000.0, ask=77_006.4) == (78_000.0, 76_900.0)
    assert levels(BUY, bid=77_000.0, ask=77_006.4) == (76_006.4, 77_106.4)


def test_skip_after_nine_wins_then_new_cycle():
    s = CycleState()
    for _ in range(9):
        assert decide(s) == OPEN
        record_close(s, 1.0)
    assert decide(s) == SKIP_CYCLE
    assert s.streak == 0 and s.skips == 1
    assert decide(s) == OPEN


def test_loss_resets_streak():
    s = CycleState(streak=5)
    record_close(s, -10.0)
    assert s.streak == 0 and s.net_usd == -10.0


def test_stops_at_loss_limit():
    s = CycleState()
    for _ in range(3):
        record_close(s, -10.0)
    assert s.finished == "perdida_30_usd" and decide(s) == DONE


def test_stops_at_100_trades():
    s = CycleState(trades=99)
    record_close(s, 1.0)
    assert s.finished == "100_operaciones"


def test_state_round_trip():
    s = CycleState(streak=3, trades=4, wins=4, net_usd=4.0)
    assert CycleState.from_dict(s.as_dict() | {"otro": 1}) == s
