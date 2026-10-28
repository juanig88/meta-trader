from __future__ import annotations

from trading.domain.decision_state import (
    closed_m1_closes,
    overlay_tape,
    pack_decision_state,
    realized_vol,
    tape_imbalance,
)


def test_pack_decision_state_table():
    closes = [100.0 + i * 0.1 for i in range(30)]
    votes = [{"name": "htf", "side": "BUY"}]
    ticks = [
        {"ts_msc": 1000, "bid": 10.0, "ask": 10.02},
        {"ts_msc": 1001, "bid": 10.01, "ask": 10.03},
        {"ts_msc": 1002, "bid": 10.03, "ask": 10.05},
        {"ts_msc": 5000, "bid": 99.0, "ask": 99.5},
    ]
    cases = [
        (
            "happy",
            dict(
                m1_closes=closes,
                votes=votes,
                murphy={"would_veto": False},
                alpha={"would_avoid": True, "score": 0.42},
                mid=101.0,
                bid=101.0,
                ask=101.2,
                tick_value=0.01,
                tick_size=0.01,
                lot=0.01,
                decision_ts_msc=2000,
                ticks=ticks,
            ),
            {"htf": 1, "murphy_veto": 0, "alpha_avoid": 1, "alpha_score": 0.42},
        ),
        (
            "missing tape and spread",
            dict(m1_closes=[1.0], votes=[], decision_ts_msc=1, ticks=[]),
            {"htf": 0, "tape_imbalance": None, "spread_usd": None, "rv_m1": None},
        ),
        (
            "veto flag",
            dict(m1_closes=closes, murphy={"would_veto": True}, alpha={"would_avoid": False, "score": 0.0}),
            {"murphy_veto": 1, "alpha_avoid": 0},
        ),
    ]
    for name, kwargs, want in cases:
        got = pack_decision_state(**kwargs)
        for key, val in want.items():
            assert got[key] == val, f"{name}:{key}"
        if name == "happy":
            assert got["mid"] == 101.0
            assert got["spread_usd"] is not None and got["spread_usd"] > 0
            assert got["rv_m1"] is not None
            assert got["tape_n"] == 3
            assert got["tape_imbalance"] is not None


def test_lookahead_tick_dropped():
    ticks = [
        {"ts_msc": 10, "bid": 1.0, "ask": 1.1},
        {"ts_msc": 11, "bid": 1.2, "ask": 1.3},
        {"ts_msc": 12, "bid": 1.0, "ask": 1.1},
        {"ts_msc": 99, "bid": 9.0, "ask": 9.5},
    ]
    imb, n = tape_imbalance(ticks, decision_ts_msc=99)
    assert n == 3
    late = tape_imbalance(ticks, decision_ts_msc=10)
    assert late == (None, None)


def test_closed_m1_drops_forming_bar():
    assert closed_m1_closes([1.0, 2.0, 3.0]) == [1.0, 2.0]
    assert closed_m1_closes([1.0]) == []
    assert realized_vol([1.0, 1.0]) is None


def test_realized_vol_forming_flag_table():
    flat_then_jump = [100.0] * 25 + [110.0]
    cases = [
        # live bot passes closed bars only: the last close is real data
        ("closed bars keep last", dict(last_forming=False), True),
        # a forming bar must not leak into RV
        ("forming bar dropped", dict(last_forming=True), False),
    ]
    for name, kwargs, want_positive in cases:
        rv = realized_vol(flat_then_jump, **kwargs)
        if want_positive:
            assert rv is not None and rv > 0.0, name
        else:
            assert rv is None or rv == 0.0, name


def test_pack_uses_last_close_as_mid_fallback():
    closes = [1.0, 1.1, 1.2]
    assert pack_decision_state(m1_closes=closes)["mid"] == 1.2
    assert pack_decision_state(m1_closes=closes, m1_last_forming=True)["mid"] == 1.1


def test_pack_bad_inputs_do_not_raise():
    got = pack_decision_state(
        m1_closes=[float("nan"), -1.0, 0.0, "x"],  # type: ignore[list-item]
        votes=[object()],
        murphy="junk",  # type: ignore[arg-type]
        alpha=None,
        bid=0.0,
        ask=0.0,
        ticks=[{"ts_msc": "bad"}, None, {"ts_msc": 1, "bid": None, "ask": 1.0}],
    )
    assert got["mid"] is None
    assert got["spread_usd"] is None
    assert got["tape_imbalance"] is None
    assert got["htf"] == 0
    assert got["murphy_veto"] == 0
    assert got["alpha_avoid"] == 0


def test_overlay_tape_fail_open():
    base = pack_decision_state(m1_closes=[1.0, 1.1, 1.2], ticks=[])
    assert base["tape_imbalance"] is None
    got = overlay_tape(base, [], decision_ts_msc=1)
    assert got["tape_imbalance"] is None
    got2 = overlay_tape(
        base,
        [
            {"ts_msc": 1, "bid": 1.0, "ask": 1.01},
            {"ts_msc": 2, "bid": 1.02, "ask": 1.03},
            {"ts_msc": 3, "bid": 1.04, "ask": 1.05},
        ],
        decision_ts_msc=10,
    )
    assert got2["tape_n"] == 3
    assert got2["tape_imbalance"] is not None
