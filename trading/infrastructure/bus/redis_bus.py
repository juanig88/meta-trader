"""Redis fan-out for live MT5 market data.

Two delivery paths per symbol:
  - pub/sub on  md:tick:{SYMBOL}   fire-and-forget, for live consumers
  - a capped stream md:stream:{SYMBOL} (XADD MAXLEN ~ N) so a consumer that
    reconnects can replay the recent past instead of waiting for the next tick

Redis holds nothing durable here. Postgres is the store; if Redis is lost the
worst case is a gap in the live feed, and the alpha engine recovers on the
next tick.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional, Sequence

from trading.domain.market_data import Tick
from trading.infrastructure.data_layer_config import RedisSettings

logger = logging.getLogger(__name__)

TICK_CHANNEL_PREFIX = "md:tick:"
TICK_STREAM_PREFIX = "md:stream:"


class BusUnavailable(RuntimeError):
    """Redis could not be reached."""


def tick_channel(symbol: str) -> str:
    return f"{TICK_CHANNEL_PREFIX}{str(symbol).strip().upper()}"


def tick_stream(symbol: str) -> str:
    return f"{TICK_STREAM_PREFIX}{str(symbol).strip().upper()}"


def encode_tick(tick: Tick) -> str:
    return json.dumps(
        {
            "symbol": tick.symbol,
            "ts_msc": int(tick.ts_msc),
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "flags": tick.flags,
        },
        separators=(",", ":"),
    )


def decode_tick(raw: str | bytes) -> Optional[Tick]:
    """Returns None for anything that is not a well-formed tick.

    A malformed message must not kill a long-running subscriber loop.
    """
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        symbol = str(payload.get("symbol") or "").strip().upper()
        ts_msc = payload.get("ts_msc")
        if not symbol or ts_msc is None:
            return None
        return Tick(
            symbol=symbol,
            ts_msc=int(ts_msc),
            bid=_f(payload.get("bid")),
            ask=_f(payload.get("ask")),
            last=_f(payload.get("last")),
            volume=_f(payload.get("volume")),
            flags=_i(payload.get("flags")),
        )
    except Exception:
        logger.debug("bus: dropping malformed tick payload", exc_info=True)
        return None


class RedisBus:
    def __init__(self, settings: RedisSettings) -> None:
        self._settings = settings
        self._client = None

    @property
    def settings(self) -> RedisSettings:
        return self._settings

    @property
    def is_open(self) -> bool:
        return self._client is not None

    async def open(self) -> None:
        if self._client is not None:
            return
        try:
            import redis.asyncio as redis

            client = redis.Redis(
                host=self._settings.host,
                port=self._settings.port,
                db=self._settings.db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,
            )
            await client.ping()
        except Exception as exc:
            raise BusUnavailable(f"cannot connect to {self._settings.url()}: {exc}") from exc
        self._client = client
        logger.info("bus: connected to %s", self._settings.url())

    async def ensure_open(self) -> bool:
        if self._client is not None:
            return True
        try:
            await self.open()
            return True
        except BusUnavailable as exc:
            logger.warning("bus: unavailable (%s)", exc)
            return False

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("bus: error while closing client", exc_info=True)

    async def health(self) -> bool:
        try:
            if self._client is None:
                await self.open()
            await self._client.ping()  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    async def publish_ticks(self, ticks: Sequence[Tick]) -> int:
        """Publish a batch in one pipeline round-trip. Returns ticks published."""
        if not ticks:
            return 0
        if self._client is None:
            await self.open()
        client = self._client
        if client is None:
            raise BusUnavailable("redis client is not open")

        maxlen = self._settings.stream_maxlen
        try:
            pipe = client.pipeline(transaction=False)
            for tick in ticks:
                payload = encode_tick(tick)
                pipe.publish(tick_channel(tick.symbol), payload)
                if maxlen > 0:
                    pipe.xadd(
                        tick_stream(tick.symbol),
                        {"t": payload},
                        maxlen=maxlen,
                        approximate=True,
                    )
            await pipe.execute()
        except Exception as exc:
            await self.close()  # force a clean reconnect on the next call
            raise BusUnavailable(str(exc)) from exc
        return len(ticks)

    async def subscribe_ticks(
        self,
        symbols: Sequence[str],
        *,
        poll_timeout: float = 1.0,
    ) -> AsyncIterator[Tick]:
        """Yield ticks for `symbols` forever, resubscribing across dropouts."""
        channels = [tick_channel(s) for s in symbols]
        if not channels:
            return

        while True:
            if self._client is None:
                try:
                    await self.open()
                except BusUnavailable as exc:
                    logger.warning("bus: subscribe waiting on redis (%s)", exc)
                    await asyncio.sleep(2.0)
                    continue

            pubsub = self._client.pubsub()  # type: ignore[union-attr]
            try:
                await pubsub.subscribe(*channels)
                logger.info("bus: subscribed to %d channel(s)", len(channels))
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=poll_timeout
                    )
                    if message is None:
                        continue
                    tick = decode_tick(message.get("data"))
                    if tick is not None:
                        yield tick
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("bus: subscription dropped (%s); reconnecting", exc)
                await self.close()
                await asyncio.sleep(2.0)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    async def read_recent_ticks(self, symbol: str, count: int = 100) -> list[Tick]:
        """Replay the tail of the capped stream (empty if streams are disabled)."""
        if self._client is None:
            await self.open()
        client = self._client
        if client is None:
            raise BusUnavailable("redis client is not open")
        try:
            entries = await client.xrevrange(tick_stream(symbol), count=max(1, int(count)))
        except Exception as exc:
            raise BusUnavailable(str(exc)) from exc
        ticks: list[Tick] = []
        for _entry_id, fields in reversed(list(entries or [])):
            tick = decode_tick(fields.get("t", ""))
            if tick is not None:
                ticks.append(tick)
        return ticks


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _i(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
