from trading.domain.htf_bias import effective_max_spread_points


def test_effective_max_spread_points_table():
    cases = [
        ("btc cap binds", "BTCUSD", 5000.0, 1000.0, 1000.0),
        ("btc broker suffix", "BTCUSD!", 5000.0, 1000.0, 1000.0),
        ("btc symbol already tighter", "BTCUSD", 400.0, 1000.0, 400.0),
        ("eth uncapped", "ETHUSD", 1500.0, 1000.0, 1500.0),
        ("cap off", "BTCUSD", 5000.0, 0.0, 5000.0),
        ("negative cap off", "BTCUSD", 5000.0, -1.0, 5000.0),
    ]
    for name, sym, symbol_max, btc_cap, want in cases:
        got = effective_max_spread_points(sym, symbol_max, btcusd_cap=btc_cap)
        assert got == want, name
