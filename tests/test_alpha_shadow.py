from __future__ import annotations

from trading.domain.alpha_shadow import alpha_shadow_from_bars
from trading.domain.ensemble import Ohlcv
from trading.domain.loser_review import ALPHA_AVOIDED, ALPHA_MISSING, ALPHA_OPPOSITE, ALPHA_SAME
from trading.infrastructure.persistence.ensemble_store import WAREHOUSE_EVENTS, insert_trade_event


def _m15(n: int, *, step: float = 0.4) -> Ohlcv:
    closes = [100.0 + i * step for i in range(n)]
    opens = [c - 0.2 for c in closes]
    highs = [c + 0.15 for c in closes]
    lows = [o - 0.05 for o in opens]
    vols = [40.0 + i for i in range(n)]
    return Ohlcv.from_lists(opens, highs, lows, closes, vols)


def test_alpha_shadow_table():
    trend = _m15(80, step=0.5)
    cases = [
        ("too short", "ETHUSD!", "BUY", _m15(10), ALPHA_MISSING, False),
        ("no side", "ETHUSD!", "NONE", trend, None, False),
    ]
    for name, symbol, side, bars, want_code, want_avoid in cases:
        got = alpha_shadow_from_bars(symbol, side, bars)
        if want_code is None:
            assert got is None, name
            continue
        assert got["code"] == want_code, name
        assert got["would_avoid"] is want_avoid, name

    scored = alpha_shadow_from_bars("ETHUSD!", "BUY", trend)
    assert scored is not None
    assert scored["code"] in {ALPHA_SAME, ALPHA_AVOIDED, ALPHA_OPPOSITE}
    assert "score" in scored
    assert scored["agree"] is (scored["code"] == ALPHA_SAME)
    assert scored["would_avoid"] is (scored["code"] in {ALPHA_AVOIDED, ALPHA_OPPOSITE})


def test_insert_trade_event_skips_votes():
    assert insert_trade_event({"event": "ENSEMBLE_VOTE", "symbol": "ETHUSD!"}) == 0
    assert "ENSEMBLE_SIGNAL" in WAREHOUSE_EVENTS
