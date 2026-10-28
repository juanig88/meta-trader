from __future__ import annotations

import pytest

from trading.domain.crypto_universe import CRYPTO_UNIVERSE
from trading.infrastructure.data_layer_config import (
    AlphaSettings,
    DataLayerConfigError,
    IngestSettings,
    PgSettings,
    RedisSettings,
    RetentionSettings,
    shadow_market_symbols,
)


def test_pg_settings_requires_password():
    with pytest.raises(DataLayerConfigError):
        PgSettings.from_env({})


def test_pg_and_redis_from_env():
    env = {
        "MT5_PG_PASSWORD": "secret",
        "MT5_PG_HOST": "127.0.0.1",
        "MT5_PG_PORT": "5433",
        "MT5_PG_USER": "metatrader",
        "MT5_PG_DB": "metatrader",
        "MT5_REDIS_PORT": "6380",
        "MT5_TICK_STREAM_MAXLEN": "100",
    }
    pg = PgSettings.from_env(env)
    assert pg.port == 5433
    assert "***" in pg.redacted()
    assert "secret" not in pg.redacted()
    redis = RedisSettings.from_env(env)
    assert redis.port == 6380
    assert redis.stream_maxlen == 100
    assert redis.url() == "redis://127.0.0.1:6380/0"


def test_retention_and_ingest_defaults():
    env = {"BOT_SYMBOLS": "BTCUSD,ETHUSD,USDJPY", "BOT_ROTATION_ENABLED": "0"}
    ret = RetentionSettings.from_env(env)
    assert ret.tick_ttl_hours == 48
    ingest = IngestSettings.from_env(env)
    assert ingest.symbols == ["BTCUSD", "ETHUSD", "USDJPY"]
    assert ingest.persist_ticks is True
    alpha = AlphaSettings.from_env({**env, "MT5_ALPHA_ENABLED": "0"})
    assert alpha.enabled is False
    assert alpha.symbols == ingest.symbols


def test_shadow_market_symbols_rotation_table():
    cases = [
        ("rotation default 79", {}, 79, "ADAUSD!"),
        (
            "explicit daemon list wins",
            {"BOT_ROTATION_ENABLED": "1", "MT5_TICK_DAEMON_SYMBOLS": "AAAUSD!,BBBUSD!"},
            2,
            "AAAUSD!",
        ),
        (
            "rotation off uses BOT_SYMBOLS",
            {"BOT_ROTATION_ENABLED": "0", "BOT_SYMBOLS": "BTCUSD!,ETHUSD!"},
            2,
            "BTCUSD!",
        ),
    ]
    for name, env, n, first in cases:
        got = shadow_market_symbols(env)
        assert len(got) == n, name
        assert got[0] == first, name
    rot = shadow_market_symbols({"BOT_ROTATION_ENABLED": "1"})
    assert rot == [str(s) for s in CRYPTO_UNIVERSE]
    assert "USDJPY." not in rot
