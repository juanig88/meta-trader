from __future__ import annotations

from trading.domain.market_data import Tick
from trading.infrastructure.bus.redis_bus import (
    decode_tick,
    encode_tick,
    tick_channel,
    tick_stream,
)


def test_channel_names():
    assert tick_channel("btcusd") == "md:tick:BTCUSD"
    assert tick_stream(" ethusd ") == "md:stream:ETHUSD"


def test_encode_decode_roundtrip():
    tick = Tick(symbol="USDJPY", ts_msc=1_700_000_000_123, bid=149.1, ask=149.13, last=149.12, volume=3.0, flags=6)
    got = decode_tick(encode_tick(tick))
    assert got is not None
    assert got.symbol == "USDJPY"
    assert got.ts_msc == tick.ts_msc
    assert got.bid == tick.bid
    assert got.ask == tick.ask
    assert got.flags == tick.flags


def test_decode_malformed_table():
    cases = [
        ("empty", ""),
        ("not json", "not-json"),
        ("list", "[1,2]"),
        ("missing symbol", '{"ts_msc": 1}'),
        ("missing ts", '{"symbol": "EURUSD"}'),
        ("nan bid still ok later", None),
    ]
    for name, raw in cases:
        if raw is None:
            continue
        assert decode_tick(raw) is None, name
    tick = decode_tick('{"symbol":"EURUSD","ts_msc":10,"bid":null}')
    assert tick is not None
    assert tick.bid is None
