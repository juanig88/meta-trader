from trading.domain.crypto_universe import (
    CRYPTO_UNIVERSE,
    ROTATION_RANK_TAIL,
    rotation_rank_skip,
)


def test_crypto_universe_has_seventy_nine_xbtfx_names():
    assert len(CRYPTO_UNIVERSE) == 79
    assert CRYPTO_UNIVERSE[0] == "ADAUSD!"
    assert "BTCUSD!" in CRYPTO_UNIVERSE
    assert "USDJPY." not in CRYPTO_UNIVERSE
    assert len(set(CRYPTO_UNIVERSE)) == 79


def test_rotation_rank_skip_table():
    cases = [
        ("imx", "IMXUSD!", "rank_tail"),
        ("vec", "vecusd!", "rank_tail"),
        ("trx zero winners", "TRXUSD!", "rank_tail"),
        ("zrx zero winners", "ZRXUSD!", "rank_tail"),
        ("rsr zero winners", "rsrusd!", "rank_tail"),
        ("axs zero winners", "AXSUSD!", "rank_tail"),
        ("eth kept", "ETHUSD!", ""),
        ("zen kept", "ZENUSD!", ""),
        ("empty", "", ""),
        ("none-ish", "   ", ""),
    ]
    for name, symbol, want in cases:
        assert rotation_rank_skip(symbol) == want, name
    assert ROTATION_RANK_TAIL <= set(CRYPTO_UNIVERSE)
    assert "BTCUSD!" not in ROTATION_RANK_TAIL

