"""Sync Redis tick peek for the rotation log. Fail-open, never on the send path."""

from __future__ import annotations

from trading.domain.market_data import Tick
from trading.infrastructure.bus.redis_bus import decode_tick, tick_stream
from trading.infrastructure.data_layer_config import RedisSettings


def peek_ticks_many(symbols: list[str], *, count: int = 40, timeout: float = 0.15) -> dict[str, list[Tick]]:
    if not symbols:
        return {}
    try:
        import redis
    except ImportError:
        return {}
    settings = RedisSettings.from_env()
    client = None
    out: dict[str, list[Tick]] = {}
    try:
        client = redis.Redis(
            host=settings.host,
            port=int(settings.port),
            db=int(settings.db),
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        pipe = client.pipeline(transaction=False)
        keys = [tick_stream(s) for s in symbols]
        for key in keys:
            pipe.xrevrange(key, count=max(1, int(count)))
        replies = pipe.execute()
        for symbol, entries in zip(symbols, replies):
            ticks: list[Tick] = []
            for _eid, fields in reversed(list(entries or [])):
                if not isinstance(fields, dict):
                    continue
                raw = fields.get("t") or fields.get(b"t") or ""
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                tick = decode_tick(raw)
                if tick is not None:
                    ticks.append(tick)
            out[str(symbol)] = ticks
        return out
    except Exception:
        return {}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
