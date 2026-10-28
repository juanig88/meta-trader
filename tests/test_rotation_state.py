"""Phase 1: decision state rides on 4/5 snapshots without changing the decision."""

from __future__ import annotations

from dataclasses import replace

from trading.application.rotation_engine import (
    RotationSettings,
    overlay_cycle_tape,
    run_rotation_cycle,
    tape_symbols,
)
from trading.domain.ensemble import ContractSpec, Ohlcv, decision_cutoff_msc


def _up_bars(n: int) -> Ohlcv:
    closes = [100.0 + i for i in range(n)]
    opens = [c - 0.4 for c in closes]
    highs = [c + 0.2 for c in closes]
    lows = [o - 0.2 for o in opens]
    vols = [50.0 + i for i in range(n)]
    return Ohlcv.from_lists(opens, highs, lows, closes, vols)


def _force_four_five(monkeypatch, *, murphy_veto: bool = False) -> None:
    import trading.domain.ensemble as ens

    four = (
        ens.Vote("htf", "BUY"),
        ens.Vote("momentum_short", "BUY"),
        ens.Vote("intermarket", "BUY"),
        ens.Vote("vol_bands", "BUY"),
        ens.Vote("structure", "NONE"),
    )
    monkeypatch.setattr(ens, "cast_votes", lambda *_a, **_k: four)
    monkeypatch.setattr(ens, "pullback_allows", lambda *_a, **_k: True)
    monkeypatch.setattr(ens, "rsi_range_blocks", lambda *_a, **_k: False)
    monkeypatch.setattr(
        ens,
        "_murphy_if_fired",
        lambda *_a, **_k: {"would_veto": murphy_veto, "tag_m15": "x", "m15": {}, "h4": {}},
    )


def _contract(tick_msc: int = 1_000_000) -> ContractSpec:
    return ContractSpec(
        0.01,
        0.01,
        usd_at_min_lot_min_stop=0.05,
        price=100.0,
        tick_value=0.01,
        tick_size=0.01,
        bid=99.99,
        ask=100.01,
        tick_time_msc=tick_msc,
    )


def _cycle(symbols=("ETHUSD!", "IMXUSD!"), tick_msc: int = 1_000_000):
    m1, m5, m15 = _up_bars(80), _up_bars(40), _up_bars(60)
    return run_rotation_cycle(
        settings=RotationSettings(symbols=tuple(symbols), dry_run=True, hour_cap=60),
        fetch_bars=lambda s: (m1, m5, m15, 1_000),
        fetch_contract=lambda s: _contract(tick_msc),
        open_symbols=(),
        opens_this_hour=0,
    )


def test_decision_cutoff_table():
    cases = [
        ("quote wins", 5_000, 100, 5_001),
        ("no quote: close of last closed M1", 0, 100, 160_000),
        ("nothing", 0, None, None),
    ]
    for name, tick_msc, m1_time, want in cases:
        assert decision_cutoff_msc(tick_msc, m1_time) == want, name


def test_state_on_fired_and_on_vetoed_four_five(monkeypatch):
    _force_four_five(monkeypatch)
    cycle = _cycle()
    by_sym = {s.symbol: s for s in cycle.snapshots}
    eth, imx = by_sym["ETHUSD!"], by_sym["IMXUSD!"]
    assert eth.action == "DRY_RUN"
    assert imx.skip_reason == "rank_tail"
    for snap in (eth, imx):
        assert snap.state is not None
        assert snap.state["decision_ts_msc"] == 1_000_001
        assert snap.state["htf"] == 1
        assert snap.state["spread_usd"] is not None
        assert snap.state["rv_m1"] is not None
        assert snap.state["tape_imbalance"] is None
    assert set(tape_symbols(cycle)) == {"ETHUSD!", "IMXUSD!"}


def test_no_state_without_four_five():
    cycle = _cycle(symbols=("ETHUSD!",))
    for snap in cycle.snapshots:
        if not snap.consensus.fired:
            assert snap.state is None


def test_overlay_keeps_decision_and_drops_lookahead(monkeypatch):
    _force_four_five(monkeypatch)
    cycle = _cycle(tick_msc=1_000_000)
    ticks = [
        {"ts_msc": 999_990, "bid": 1.00, "ask": 1.01},
        {"ts_msc": 999_995, "bid": 1.02, "ask": 1.03},
        {"ts_msc": 1_000_000, "bid": 1.04, "ask": 1.05},
        # after the quote the bot sized on: must not enter
        {"ts_msc": 1_000_500, "bid": 0.50, "ask": 0.51},
        {"ts_msc": 1_000_600, "bid": 0.40, "ask": 0.41},
    ]
    got = overlay_cycle_tape(cycle, {"ETHUSD!": ticks})
    before = {s.symbol: s for s in cycle.snapshots}
    after = {s.symbol: s for s in got.snapshots}
    for sym in before:
        a, b = before[sym], after[sym]
        assert (a.action, a.skip_reason, a.consensus, a.size, a.votes) == (
            b.action,
            b.skip_reason,
            b.consensus,
            b.size,
            b.votes,
        )
    eth = after["ETHUSD!"].state
    assert eth["tape_n"] == 3
    assert eth["tape_imbalance"] == 1.0
    assert after["IMXUSD!"].state["tape_imbalance"] is None
    assert [s.symbol for s in got.intended] == [s.symbol for s in cycle.intended]
    assert got.intended[0].state["tape_n"] == 3
    assert got.summary == cycle.summary


def test_overlay_fail_open_inputs():
    m1, m5, m15 = _up_bars(80), _up_bars(40), _up_bars(60)
    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("ETHUSD!",), dry_run=True),
        fetch_bars=lambda s: (m1, m5, m15, 1),
        fetch_contract=lambda s: _contract(),
        open_symbols=(),
        opens_this_hour=0,
    )
    assert overlay_cycle_tape(cycle, {}) is cycle
    no_state = replace(cycle, snapshots=tuple(replace(s, state=None) for s in cycle.snapshots))
    assert overlay_cycle_tape(no_state, {"ETHUSD!": [{"ts_msc": 1}]}).snapshots == no_state.snapshots


def test_tick_peek_unreachable_redis_is_empty(monkeypatch):
    from trading.infrastructure import tick_peek

    monkeypatch.setenv("MT5_REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("MT5_REDIS_PORT", "1")
    assert tick_peek.peek_ticks_many(["ETHUSD!"], timeout=0.05) == {}
    assert tick_peek.peek_ticks_many([]) == {}
