from __future__ import annotations

from trading.domain.ensemble import (
    VOTER_ORDER,
    ContractSpec,
    Ohlcv,
    Vote,
    apply_account_gates,
    evaluate_symbol,
    hourly_quota_allows,
    pullback_allows,
    risk_usd_from_votes,
    rsi_range_blocks,
    rotation_module_skip,
    rotation_structure_skip,
    size_rotation_trade,
    symbol_slot_free,
    tally_votes,
    three_m1_then_fourth_stats,
    vote_htf,
    vote_m1_follow3,
    vote_m1_streak,
    vote_m5_body,
    vote_m5_trend,
    vote_zscore,
)
from trading.domain.expected_value import kelly_fraction as kelly_f


def _ohlcv(closes: list[float], *, vol: float = 100.0, bullish: bool | None = None) -> Ohlcv:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        if bullish is True:
            o = c - 1.0
        elif bullish is False:
            o = c + 1.0
        else:
            o = prev
        h = max(o, c) + 0.2
        lo = min(o, c) - 0.2
        opens.append(o)
        highs.append(h)
        lows.append(lo)
        volumes.append(vol)
        prev = c
    return Ohlcv.from_lists(opens, highs, lows, closes, volumes)


def _votes(buy: int, sell: int, none: int) -> list[Vote]:
    out: list[Vote] = []
    for i in range(buy):
        out.append(Vote(name=f"b{i}", side="BUY"))
    for i in range(sell):
        out.append(Vote(name=f"s{i}", side="SELL"))
    for i in range(none):
        out.append(Vote(name=f"n{i}", side="NONE"))
    return out


def test_tally_votes_table():
    cases = [
        ("4 buy fires", 4, 0, 1, True, "BUY"),
        ("3 buy does not", 3, 0, 2, False, "NONE"),
        ("4 sell fires", 0, 4, 1, True, "SELL"),
        ("2-2 no fire", 2, 2, 1, False, "NONE"),
        ("4 buy 1 sell still fires", 4, 1, 0, True, "BUY"),
        ("2 buy not near miss", 2, 1, 2, False, "NONE"),
    ]
    for name, buy, sell, none, fired, direction in cases:
        got = tally_votes(_votes(buy, sell, none), min_agree=4, voter_count=5)
        assert got.fired is fired, name
        assert got.direction == direction, name
        if name == "3 buy does not":
            assert got.near_miss is True, name
        if name == "2 buy not near miss":
            assert got.near_miss is False, name


def test_neutrals_count_against_threshold():
    got = tally_votes(_votes(3, 0, 2), min_agree=4, voter_count=5)
    assert got.fired is False
    assert got.buy == 3
    assert got.none == 2
    assert got.near_miss is True


def test_kelly_risk_never_exceeds_cap():
    cases = [
        ("4/5 under cap", 4, None),
        ("5/5 is cap", 5, 0.50),
        ("0 is zero", 0, 0.0),
    ]
    for name, agree, want in cases:
        risk, f_star = risk_usd_from_votes(agree, max_risk_usd=0.50)
        assert risk <= 0.50 + 1e-12, name
        assert 0.0 <= f_star <= 1.0, name
        if want is not None:
            assert abs(risk - want) < 1e-9, name
    risk4, _ = risk_usd_from_votes(4, max_risk_usd=0.50)
    risk5, _ = risk_usd_from_votes(5, max_risk_usd=0.50)
    assert risk4 < risk5
    assert abs(risk5 - 0.50) < 1e-9
    assert abs(kelly_f(1.0, 1.0) - 1.0) < 1e-12


def test_size_skips_when_min_lot_exceeds_cap():
    cases = [
        (
            "min stop 0.80 skip",
            ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.80),
            False,
            "min_lote_supera_050",
        ),
        (
            "min stop 0.20 ok",
            ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.20),
            True,
            "ok",
        ),
        (
            "min stop 0.40 bumps 10/12",
            ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.40),
            True,
            "ok",
        ),
    ]
    for name, spec, ok, reason in cases:
        got = size_rotation_trade(agree=10, contract=spec, max_risk_usd=0.50)
        assert got.ok is ok, name
        assert got.reason == reason, name
        if ok:
            assert got.risk_usd <= 0.50 + 1e-9, name
            assert got.lot >= spec.volume_min - 1e-12, name

    rsr = size_rotation_trade(
        agree=10,
        contract=ContractSpec(
            0.01,
            0.01,
            volume_max=100000.0,
            usd_at_min_lot_min_stop=0.01,
            price=0.00167,
            tick_value=1.0,
            tick_size=1.0,
        ),
        max_risk_usd=0.50,
    )
    assert rsr.ok is True
    assert rsr.lot > 1.0
    assert rsr.risk_usd <= 0.50 + 1e-9


def test_hourly_and_symbol_gates():
    cases = [
        ("under cap", hourly_quota_allows(59, cap=60), True),
        ("at cap", hourly_quota_allows(60, cap=60), False),
        ("free slot", symbol_slot_free(("ETHUSD!",), "BTCUSD!"), True),
        ("taken slot", symbol_slot_free(("BTCUSD!",), "btcusd!"), False),
    ]
    for name, got, want in cases:
        assert got is want, name


def test_apply_account_gates_table():
    m1 = _ohlcv([float(100 + i) for i in range(80)], bullish=True, vol=200)
    m5 = _ohlcv([float(100 + i * 2) for i in range(40)], bullish=True, vol=200)
    # Force a fired snapshot via tally override path: use apply on a stub FIRE snap
    base = evaluate_symbol(
        "AAAUSD!",
        m1,
        m5,
        contract=ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.10),
    )
    # Even if evaluate does not fire on synthetic series, gates must be pure
    fired = base
    if fired.action != "FIRE":
            # Build a FIRE snapshot with dummy consensus by applying gates only when FIRE
        votes = tuple(_votes(10, 0, 2))
        from trading.domain.ensemble import Consensus, EnsembleSnapshot, SizeDecision

        fired = EnsembleSnapshot(
            symbol="AAAUSD!",
            votes=votes,
            consensus=Consensus("BUY", 10, 0, 2, 10, True, False),
            size=SizeDecision(True, "ok", 0.33, 0.01, 0.66),
            skip_reason="",
            action="FIRE",
            murphy={"tag_m15": "climax_unbroken", "would_veto": True},
        )
    gated = apply_account_gates(
        fired, open_symbols=("AAAUSD!",), opens_this_hour=0, hour_cap=60, dry_run=True
    )
    assert gated.action == "SKIP"
    assert gated.skip_reason == "ya_abierto"
    assert gated.murphy == fired.murphy
    assert gated.alpha == fired.alpha

    hour = apply_account_gates(
        fired, open_symbols=(), opens_this_hour=60, hour_cap=60, dry_run=True
    )
    assert hour.skip_reason == "cuota_hora"

    dry = apply_account_gates(
        fired, open_symbols=(), opens_this_hour=0, hour_cap=60, dry_run=True
    )
    assert dry.action == "DRY_RUN"

    live = apply_account_gates(
        fired, open_symbols=(), opens_this_hour=0, hour_cap=60, dry_run=False
    )
    assert live.action == "LIVE"


def test_m1_follow3_is_exactly_three_then_fourth():
    # down then 3 up → vote BUY (the 4th candle)
    fresh = Ohlcv.from_lists(
        opens=[11, 10, 11, 12],
        highs=[12, 11, 12, 13],
        lows=[10, 9, 10, 11],
        closes=[10, 11, 12, 13],
        volumes=[1, 1, 1, 1],
    )
    got = vote_m1_follow3(fresh)
    assert got.side == "BUY"
    assert vote_m1_streak(fresh, bars=3).side == "BUY"
    # 4+ up in a row is a longer streak: follow3 sits out, streak still votes
    long_up = _ohlcv([10, 11, 12, 13, 14, 15], bullish=True)
    assert vote_m1_follow3(long_up).side == "NONE"
    assert vote_m1_streak(long_up, bars=3).side == "BUY"


def test_three_m1_then_fourth_stats_table():
    # Two exact-3 runs: 4th continues, then 4th fails.
    opens = [5, 1, 2, 3, 4, 6, 5, 6, 7, 8]
    closes = [1, 2, 3, 4, 5, 5, 6, 7, 8, 7]
    exact = three_m1_then_fourth_stats(opens, closes, run=3, exact=True)
    assert exact["n"] == 2
    assert exact["hits"] == 1
    assert abs(float(exact["hit_rate"]) - 0.5) < 1e-9
    empty = three_m1_then_fourth_stats([1.0], [1.0])
    assert empty["n"] == 0
    assert empty["hit_rate"] == 0.0


def test_m1_streak_and_m5_body():
    up = _ohlcv([10, 11, 12, 13, 14], bullish=True)
    assert vote_m1_streak(up, bars=3).side == "BUY"
    assert vote_m5_body(up, min_body_frac=0.1).side == "BUY"
    # mixed last-3 bodies → no streak
    mixed_streak = Ohlcv.from_lists(
        opens=[10, 11, 13, 12, 14],
        highs=[11, 12, 14, 13, 15],
        lows=[9, 10, 12, 11, 13],
        closes=[11, 12, 12.5, 13.5, 13.2],
        volumes=[1, 1, 1, 1, 1],
    )
    assert vote_m1_streak(mixed_streak, bars=3).side == "NONE"


def test_zscore_stretched_up_votes_sell():
    # Last print far above a long flat base
    closes = [10.0] * 40 + [16.0]
    ohlc = _ohlcv(closes, bullish=True)
    got = vote_zscore(ohlc, stretch=1.5)
    assert got.side == "SELL"


def test_m5_trend_follows_sma():
    up = _ohlcv([float(i) for i in range(1, 30)], bullish=True)
    down = _ohlcv([float(40 - i) for i in range(1, 30)], bullish=False)
    assert vote_m5_trend(up, period=10).side == "BUY"
    assert vote_m5_trend(down, period=10).side == "SELL"


def test_flow_is_not_a_tally_voter():
    assert "m1_flow" not in VOTER_ORDER
    assert VOTER_ORDER == (
        "htf",
        "momentum_short",
        "intermarket",
        "vol_bands",
        "structure",
    )


def test_momentum_short_majority(monkeypatch):
    import trading.domain.ensemble as ens

    dummy = _ohlcv([100.0] * 10, bullish=True)
    cases = [
        ("4 buy", ("BUY", "BUY", "BUY", "BUY"), "BUY"),
        ("3 buy", ("BUY", "BUY", "BUY", "NONE"), "BUY"),
        ("2-2", ("BUY", "BUY", "SELL", "SELL"), "NONE"),
        ("2 buy", ("BUY", "BUY", "NONE", "NONE"), "NONE"),
    ]
    for name, sides, want in cases:
        names = ("m5_body", "m1_streak", "m1_follow3", "m1_momentum")
        monkeypatch.setattr(ens, "vote_m5_body", lambda *a, s=sides[0], **k: Vote(names[0], s))
        monkeypatch.setattr(ens, "vote_m1_streak", lambda *a, s=sides[1], **k: Vote(names[1], s))
        monkeypatch.setattr(ens, "vote_m1_follow3", lambda *a, s=sides[2], **k: Vote(names[2], s))
        monkeypatch.setattr(ens, "vote_m1_momentum", lambda *a, s=sides[3], **k: Vote(names[3], s))
        got = ens.vote_momentum_short(dummy, dummy)
        assert got.side == want, name


def test_htf_vote_table():
    cases = [
        ("uptrend", [float(i) for i in range(1, 80)], True, "BUY"),
        ("downtrend", [float(80 - i) for i in range(1, 80)], False, "SELL"),
        ("flat range", [100.0] * 60, None, "NONE"),
    ]
    for name, closes, bullish, want in cases:
        got = vote_htf(_ohlcv(closes, bullish=bullish))
        assert got.side == want, name


def test_pullback_climax_and_tag():
    climax = _ohlcv([100.0] * 40 + [100.0 + i * 2.0 for i in range(1, 8)], bullish=True)
    assert pullback_allows(climax, "BUY") is False
    tag = Ohlcv.from_lists(
        opens=[100.0] * 30 + [99.95],
        highs=[100.2] * 30 + [100.25],
        lows=[99.8] * 30 + [99.82],
        closes=[100.0] * 30 + [100.18],
        volumes=[1.0] * 31,
    )
    assert pullback_allows(tag, "BUY") is True


def test_rsi_range_blocks_only_when_htf_flat():
    stretched = _ohlcv([float(i) for i in range(1, 50)], bullish=True)
    cases = [
        ("range buy overbought", "BUY", "NONE", True),
        ("trend buy ignores rsi", "BUY", "BUY", False),
        ("range sell not oversold", "SELL", "NONE", False),
    ]
    for name, direction, htf, want in cases:
        assert rsi_range_blocks(direction, stretched, htf) is want, name


def _four_buy_one(htf_side: str) -> tuple[Vote, ...]:
    return (
        Vote("htf", htf_side),
        Vote("momentum_short", "BUY"),
        Vote("intermarket", "BUY"),
        Vote("vol_bands", "BUY"),
        Vote("structure", "BUY" if htf_side != "BUY" else "NONE"),
    )


def test_rotation_structure_skip_table():
    cases = [
        ("rank first", "IMXUSD!", {"would_veto": True}, "rank_tail"),
        ("vec", "VECUSD!", None, "rank_tail"),
        ("climax", "ETHUSD!", {"would_veto": True}, "murphy_climax"),
        ("keep tag", "ETHUSD!", {"would_veto": False, "would_keep": True}, ""),
        ("no murphy", "ETHUSD!", None, ""),
        ("missing key", "ETHUSD!", {"tag_m15": "climax_unbroken"}, ""),
    ]
    for name, symbol, murphy, want in cases:
        assert rotation_structure_skip(symbol, murphy) == want, name


def test_module_gates_table(monkeypatch):
    import trading.domain.ensemble as ens
    from trading.domain.ensemble import Consensus

    m1 = _ohlcv([100.0] * 40, bullish=True)
    m5 = _ohlcv([100.0] * 20, bullish=True)
    spec = ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.10)
    keep = {"would_veto": False, "tag_m15": "retest_broken"}
    monkeypatch.setattr(ens, "_murphy_if_fired", lambda *_a, **_k: keep)

    four = _four_buy_one("BUY")
    three = (
        Vote("htf", "BUY"),
        Vote("momentum_short", "BUY"),
        Vote("intermarket", "BUY"),
        Vote("vol_bands", "NONE"),
        Vote("structure", "NONE"),
    )
    htf_sell = _four_buy_one("SELL")

    monkeypatch.setattr(ens, "pullback_allows", lambda *_a, **_k: True)
    monkeypatch.setattr(ens, "rsi_range_blocks", lambda *_a, **_k: False)

    cases = [
        ("4/5 fires", four, "FIRE", ""),
        ("3/5 no fire", three, "SKIP", "sin_consenso"),
        ("htf veto", htf_sell, "SKIP", "veto_htf"),
    ]
    for name, votes, action, skip in cases:
        monkeypatch.setattr(ens, "cast_votes", lambda *a, v=votes, **k: v)
        snap = ens.evaluate_symbol("ETHUSD!", m1, m5, m15=m5, contract=spec)
        assert snap.action == action, name
        assert snap.skip_reason == skip, name
        if name == "3/5 no fire":
            assert snap.consensus.near_miss is True, name
        if name == "4/5 fires":
            assert snap.consensus.fired is True, name
            assert snap.size.risk_usd <= 0.50 + 1e-9, name

    monkeypatch.setattr(ens, "cast_votes", lambda *_a, **_k: four)
    monkeypatch.setattr(ens, "pullback_allows", lambda *_a, **_k: False)
    no_pb = ens.evaluate_symbol("ETHUSD!", m1, m5, m15=m5, contract=spec)
    assert no_pb.action == "SKIP"
    assert no_pb.skip_reason == "sin_pullback"

    monkeypatch.setattr(ens, "pullback_allows", lambda *_a, **_k: True)
    monkeypatch.setattr(ens, "rsi_range_blocks", lambda *_a, **_k: True)
    range_rsi = ens.evaluate_symbol("ETHUSD!", m1, m5, m15=m5, contract=spec)
    assert range_rsi.skip_reason == "rsi_rango"

    htf_buy_votes = (
        Vote("htf", "BUY"),
        Vote("momentum_short", "BUY"),
        Vote("intermarket", "BUY"),
        Vote("vol_bands", "BUY"),
        Vote("structure", "NONE"),
    )
    monkeypatch.setattr(ens, "cast_votes", lambda *_a, **_k: htf_buy_votes)
    monkeypatch.setattr(ens, "rsi_range_blocks", lambda d, m, h: h == "NONE")
    trend_ok = ens.evaluate_symbol("ETHUSD!", m1, m5, m15=m5, contract=spec)
    assert trend_ok.action == "FIRE"
    assert trend_ok.skip_reason == ""

    monkeypatch.setattr(ens, "rsi_range_blocks", lambda *_a, **_k: False)
    monkeypatch.setattr(
        ens, "_murphy_if_fired", lambda *_a, **_k: {"would_veto": True, "tag_m15": "climax_unbroken"}
    )
    climax = ens.evaluate_symbol("ETHUSD!", m1, m5, m15=m5, contract=spec)
    assert climax.action == "SKIP"
    assert climax.skip_reason == "murphy_climax"
    assert climax.murphy["would_veto"] is True

    monkeypatch.setattr(ens, "_murphy_if_fired", lambda *_a, **_k: keep)
    ranked = ens.evaluate_symbol("IMXUSD!", m1, m5, m15=m5, contract=spec)
    assert ranked.action == "SKIP"
    assert ranked.skip_reason == "rank_tail"

    consensus = Consensus("BUY", 4, 0, 1, 4, True, False)
    assert rotation_module_skip(htf_sell, consensus, m1) == "veto_htf"
