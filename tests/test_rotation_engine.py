from __future__ import annotations

from trading.application.rotation_engine import RotationSettings, run_rotation_cycle
from trading.domain.ensemble import ContractSpec, Ohlcv


def _up_bars(n: int) -> Ohlcv:
    closes = [100.0 + i for i in range(n)]
    opens = [c - 0.4 for c in closes]
    highs = [c + 0.2 for c in closes]
    lows = [o - 0.2 for o in opens]
    vols = [50.0 + i for i in range(n)]
    return Ohlcv.from_lists(opens, highs, lows, closes, vols)


def _keep_murphy(monkeypatch) -> None:
    import trading.domain.ensemble as ens

    monkeypatch.setattr(
        ens,
        "_murphy_if_fired",
        lambda *_a, **_k: {
            "would_veto": False,
            "would_keep": True,
            "m15": {},
            "h4": {},
            "tag_m15": "retest_broken",
        },
    )


def test_run_cycle_dry_run_does_not_need_broker(monkeypatch):
    _keep_murphy(monkeypatch)
    m1 = _up_bars(80)
    m5 = _up_bars(40)
    m15 = _up_bars(60)
    bars = {"BTCUSD!": (m1, m5, m15, 1), "ETHUSD!": (m1, m5, m15, 1)}

    def fetch_bars(symbol: str):
        return bars.get(symbol)

    def fetch_contract(symbol: str):
        return ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.05)

    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("BTCUSD!", "ETHUSD!"), dry_run=True, hour_cap=60),
        fetch_bars=fetch_bars,
        fetch_contract=fetch_contract,
        open_symbols=(),
        opens_this_hour=0,
    )
    assert cycle.summary["scanned"] == 2
    assert cycle.summary["dry_run"] is True
    for snap in cycle.intended:
        assert snap.action == "DRY_RUN"


def test_cycle_rank_tail_skips_imx_after_four_five(monkeypatch):
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
    _keep_murphy(monkeypatch)
    m1 = _up_bars(80)
    m5 = _up_bars(40)
    m15 = _up_bars(60)

    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("IMXUSD!", "ETHUSD!"), dry_run=True, hour_cap=60),
        fetch_bars=lambda s: (m1, m5, m15, 1),
        fetch_contract=lambda s: ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.05),
        open_symbols=(),
        opens_this_hour=0,
    )
    by_sym = {s.symbol: s for s in cycle.snapshots}
    assert by_sym["IMXUSD!"].skip_reason == "rank_tail"
    assert by_sym["IMXUSD!"].action == "SKIP"
    assert by_sym["ETHUSD!"].action == "DRY_RUN"
    assert all(s.symbol != "IMXUSD!" for s in cycle.intended)


def test_dry_run_is_the_default():
    assert RotationSettings().dry_run is True
    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=(), dry_run=True),
        fetch_bars=lambda s: None,
        fetch_contract=lambda s: None,
        open_symbols=(),
        opens_this_hour=0,
    )
    assert cycle.intended == ()
    assert cycle.summary["dry_run"] is True
    m1 = _up_bars(80)
    m5 = _up_bars(40)
    m15 = _up_bars(60)

    def fetch_bars(symbol: str):
        return (m1, m5, m15, 1)

    def fetch_contract(symbol: str):
        return ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.05)

    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("BTCUSD!", "ETHUSD!"), dry_run=True, hour_cap=60),
        fetch_bars=fetch_bars,
        fetch_contract=fetch_contract,
        open_symbols=(),
        opens_this_hour=60,
    )
    assert all(s.action != "DRY_RUN" or s.skip_reason == "cuota_hora" for s in cycle.snapshots)
    assert cycle.intended == ()
    for s in cycle.snapshots:
        if s.action in ("FIRE", "DRY_RUN", "LIVE"):
            raise AssertionError("hour cap must block fills")
        if s.consensus.fired and s.skip_reason not in (
            "cuota_hora",
            "veto_htf",
            "sin_pullback",
            "rsi_rango",
            "murphy_climax",
            "rank_tail",
        ):
            raise AssertionError(s.skip_reason)


def test_missing_bars_are_not_scanned():
    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("BTCUSD!",), dry_run=True),
        fetch_bars=lambda s: None,
        fetch_contract=lambda s: ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.05),
        open_symbols=(),
        opens_this_hour=0,
    )
    assert cycle.summary["scanned"] == 0
    assert cycle.intended == ()


def test_optional_h4_tuple_is_unpacked():
    m1 = _up_bars(80)
    m5 = _up_bars(40)
    m15 = _up_bars(60)
    h4 = _up_bars(72)

    def fetch_bars(symbol: str):
        return (m1, m5, m15, 1, h4)

    def fetch_contract(symbol: str):
        return ContractSpec(0.01, 0.01, usd_at_min_lot_min_stop=0.05)

    cycle = run_rotation_cycle(
        settings=RotationSettings(symbols=("BTCUSD!",), dry_run=True, hour_cap=60),
        fetch_bars=fetch_bars,
        fetch_contract=fetch_contract,
        open_symbols=(),
        opens_this_hour=0,
    )
    assert cycle.summary["scanned"] == 1
    for snap in cycle.snapshots:
        if snap.consensus.fired:
            assert snap.murphy is not None
            assert "m15" in snap.murphy
            assert "h4" in snap.murphy
