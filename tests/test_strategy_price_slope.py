from strategy_core import Features, StrategyParams, decide, price_slope_m15


def test_price_slope_m15_detects_up():
    closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]
    assert price_slope_m15(closes, n=6) == "UP"


def test_price_slope_m15_detects_down():
    closes = [102.5, 102.0, 101.5, 101.0, 100.5, 100.0]
    assert price_slope_m15(closes, n=6) == "DOWN"


def test_decide_blocks_buy_when_slope_not_up():
    p = StrategyParams(adx_min=10.0, allow_flat_h4=True, price_slope_filter_enabled=True)
    for slope in ("DOWN", "FLAT"):
        f = Features(
            trend_h4="FLAT",
            signal_m15="BUY",
            ema_dir="UP",
            ema_fast=1.0,
            ema_slow=0.0,
            adx=20.0,
            price_slope_m15=slope,  # type: ignore[arg-type]
        )
        assert decide(p, f) == "NONE", slope


def test_decide_allows_buy_when_slope_up():
    p = StrategyParams(adx_min=10.0, allow_flat_h4=True, price_slope_filter_enabled=True)
    f = Features(
        trend_h4="FLAT",
        signal_m15="BUY",
        ema_dir="UP",
        ema_fast=1.0,
        ema_slow=0.0,
        adx=20.0,
        price_slope_m15="UP",
    )
    assert decide(p, f) == "BUY"


def test_decide_allows_buy_when_slope_flat_but_filter_disabled():
    p = StrategyParams(adx_min=10.0, allow_flat_h4=True, price_slope_filter_enabled=False)
    f = Features(
        trend_h4="FLAT",
        signal_m15="BUY",
        ema_dir="UP",
        ema_fast=1.0,
        ema_slow=0.0,
        adx=20.0,
        price_slope_m15="FLAT",
    )
    assert decide(p, f) == "BUY"


def test_decide_blocks_sell_when_slope_up_crypto():
    p = StrategyParams(
        adx_min=10.0,
        allow_flat_h4=True,
        crypto_sell_requires_h4_down=True,
        price_slope_filter_enabled=True,
    )
    f = Features(
        trend_h4="DOWN",
        signal_m15="SELL",
        ema_dir="DOWN",
        ema_fast=0.0,
        ema_slow=1.0,
        adx=20.0,
        price_slope_m15="UP",
    )
    assert decide(p, f, symbol="ETHUSD") == "NONE"


def test_decide_blocks_sell_when_slope_flat_late_entry_case():
    """Replica BTC 2026-08-10 16:15: H4 DOWN + SELL M15 pero slope FLAT tras bounce."""
    p = StrategyParams(
        adx_min=10.0,
        allow_flat_h4=True,
        crypto_sell_requires_h4_down=True,
        price_slope_filter_enabled=True,
        rsi_h4_filter_enabled=True,
        rsi_h4_oversold_threshold=35.0,
    )
    f = Features(
        trend_h4="DOWN",
        signal_m15="SELL",
        ema_dir="DOWN",
        ema_fast=0.0,
        ema_slow=1.0,
        adx=43.0,
        price_slope_m15="FLAT",
        rsi_h4=35.73,
    )
    assert decide(p, f, symbol="BTCUSD") == "NONE"


def test_decide_allows_sell_when_slope_down():
    p = StrategyParams(
        adx_min=10.0,
        allow_flat_h4=True,
        crypto_sell_requires_h4_down=True,
        price_slope_filter_enabled=True,
    )
    f = Features(
        trend_h4="DOWN",
        signal_m15="SELL",
        ema_dir="DOWN",
        ema_fast=0.0,
        ema_slow=1.0,
        adx=20.0,
        price_slope_m15="DOWN",
    )
    assert decide(p, f, symbol="BTCUSD") == "SELL"
