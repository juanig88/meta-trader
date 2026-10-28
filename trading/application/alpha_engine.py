"""Shadow-mode multi-factor alpha engine.

Subscribes to Redis tick fan-out, scores each symbol from persisted bars,
and writes the result to alpha_scores. It has no import path to bot_mt5,
open_trade, watcher_ladder, or any other execution function: that is the
structural guarantee that shadow mode holds.

Routing these scores to live orders is out of scope. Per the repo risk-quant
rule it would need a dual-backtest (Vibe-Trading + QuantConnect) approval
note first.

Usage:
    py -m trading.application.alpha_engine
    py -m trading.application.alpha_engine --once
    py -m trading.application.alpha_engine --symbols BTCUSD,USDJPY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

from trading.domain.alpha_score import (
    AlphaScore,
    AlphaWeights,
    CostModel,
    score_bars,
)
from trading.domain.market_data import Bar, Tick
from trading.domain.xbtfx import root_symbol as _root_symbol
from trading.infrastructure.async_runtime import run as run_async
from trading.infrastructure.bus.redis_bus import BusUnavailable, RedisBus
from trading.infrastructure.data_layer_config import (
    AlphaSettings,
    DataLayerConfigError,
    PgSettings,
    RedisSettings,
)
from trading.infrastructure.persistence.pg import PgStore, PgUnavailable

logger = logging.getLogger(__name__)

LOG_FILE = "mt5_alpha.log"
_SUBSCRIBE_RETRY_SECONDS = 2.0

# Heuristic point sizes when the broker quote is not available. The engine
# never talks to MT5; these are only used to convert ATR into points so costs
# and edge share a unit. Override via MT5_ALPHA_POINTS JSON if needed.
_POINT_BY_SUFFIX = {
    "JPY": 0.001,
}
_POINT_BY_SYMBOL = {
    "BTCUSD": 0.01,
    "ETHUSD": 0.01,
    "XAUUSD": 0.01,
    "XAGUSD": 0.001,
}


def _setup_logging(level: str = "INFO", filename: str = LOG_FILE) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        fh = logging.FileHandler(filename, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        logger.warning("could not open %s; logging to stdout only", filename)


def _log_event(payload: dict) -> None:
    try:
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logger.info("%s", payload)


def infer_point(symbol: str, price: Optional[float] = None, overrides: Optional[dict] = None) -> float:
    """Best-effort broker point. Prefer an explicit override."""
    sym = str(symbol or "").strip().upper()
    root = _root_symbol(sym)
    for key in (sym, root):
        if overrides and key in overrides:
            try:
                value = float(overrides[key])
                if value > 0.0:
                    return value
            except (TypeError, ValueError):
                pass
    if sym in _POINT_BY_SYMBOL:
        return _POINT_BY_SYMBOL[sym]
    if root in _POINT_BY_SYMBOL:
        return _POINT_BY_SYMBOL[root]
    for suffix, point in _POINT_BY_SUFFIX.items():
        if root.endswith(suffix):
            return point
    if price is not None:
        try:
            p = abs(float(price))
        except (TypeError, ValueError):
            p = 0.0
        if p >= 100.0:
            return 0.01
        if p >= 10.0:
            return 0.001
    return 0.00001


def parse_point_overrides(raw: str | None) -> dict[str, float]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in data.items():
        try:
            point = float(value)
        except (TypeError, ValueError):
            continue
        symbol = str(key or "").strip().upper()
        if symbol and point > 0.0:
            out[symbol] = point
    return out


def spread_points_from_tick(tick: Optional[Tick], point: float) -> float:
    if tick is None or point <= 0.0:
        return 0.0
    pts = tick.spread_points(point)
    if pts is None or pts != pts or pts < 0.0:
        return 0.0
    return float(pts)


def bars_to_series(bars: Sequence[Bar]) -> dict[str, list[float]]:
    return {
        "opens": [float(b.open) for b in bars],
        "highs": [float(b.high) for b in bars],
        "lows": [float(b.low) for b in bars],
        "closes": [float(b.close) for b in bars],
        "volumes": [float(b.tick_volume or 0.0) for b in bars],
    }


class AlphaEngine:
    """Score symbols from Redis ticks + Postgres bars. Persist only."""

    def __init__(
        self,
        *,
        settings: AlphaSettings,
        store: PgStore,
        bus: Optional[RedisBus] = None,
        point_overrides: Optional[dict[str, float]] = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._bus = bus
        self._point_overrides = dict(point_overrides or {})
        self._stop = asyncio.Event()
        self._dirty: set[str] = set(settings.symbols)
        self.stats = {"scored": 0, "written": 0, "skipped": 0}

    @property
    def settings(self) -> AlphaSettings:
        return self._settings

    def request_stop(self) -> None:
        self._stop.set()

    def _weights(self) -> AlphaWeights:
        return AlphaWeights(
            momentum=self._settings.w_momentum,
            volatility=self._settings.w_volatility,
            zscore=self._settings.w_zscore,
            flow=self._settings.w_flow,
        )

    async def score_symbol(
        self,
        symbol: str,
        *,
        bars: Optional[Sequence[Bar]] = None,
        ticks: Optional[Sequence[Tick]] = None,
    ) -> Optional[AlphaScore]:
        """Score one symbol. Returns None when there is not enough honest data."""
        sym = str(symbol).strip().upper()
        tf = self._settings.timeframe
        if bars is None:
            try:
                bars = await self._store.fetch_bars(sym, tf, self._settings.bars)
            except PgUnavailable as exc:
                logger.warning("alpha: bars unavailable for %s (%s)", sym, exc)
                return None
        if not bars or len(bars) < 30:
            return None

        if ticks is None:
            try:
                ticks = await self._store.fetch_recent_ticks(sym, 200)
            except PgUnavailable:
                ticks = []

        last = bars[-1]
        point = infer_point(sym, last.close, self._point_overrides)
        last_tick = ticks[-1] if ticks else None
        spread_pts = spread_points_from_tick(last_tick, point)
        if spread_pts <= 0.0 and last.spread is not None:
            spread_pts = max(0.0, float(last.spread))

        series = bars_to_series(bars)
        bids = [t.bid for t in ticks or () if t.bid is not None]
        asks = [t.ask for t in ticks or () if t.ask is not None]
        tape = [t for t in ticks or () if t.volume is not None and t.flags is not None]
        return score_bars(
            symbol=sym,
            timeframe=tf,
            opens=series["opens"],
            highs=series["highs"],
            lows=series["lows"],
            closes=series["closes"],
            volumes=series["volumes"],
            point=point,
            costs=CostModel(
                spread_points=spread_pts,
                commission_points=self._settings.commission_points,
                slippage_points=self._settings.slippage_points,
            ),
            weights=self._weights(),
            zscore_lookback=self._settings.zscore_lookback,
            min_score=self._settings.min_score,
            tick_bids=bids,
            tick_asks=asks,
            tick_volumes=[float(t.volume or 0.0) for t in tape],
            tick_flags=[int(t.flags or 0) for t in tape],
        )

    async def persist(self, score: AlphaScore) -> int:
        row = score.as_row()
        row["ts"] = datetime.now(timezone.utc)
        return await self._store.insert_alpha_scores([row])

    async def run_once(self, symbols: Optional[Sequence[str]] = None) -> list[AlphaScore]:
        targets = [str(s).strip().upper() for s in (symbols or self._settings.symbols) if str(s).strip()]
        out: list[AlphaScore] = []
        for symbol in targets:
            score = await self.score_symbol(symbol)
            if score is None:
                self.stats["skipped"] += 1
                continue
            self.stats["scored"] += 1
            try:
                self.stats["written"] += await self.persist(score)
            except PgUnavailable as exc:
                logger.warning("alpha: persist failed for %s (%s)", symbol, exc)
                continue
            out.append(score)
            _log_event(
                {
                    "event": "ALPHA_SCORE",
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "symbol": score.symbol,
                    "timeframe": score.timeframe,
                    "direction": score.direction,
                    "score": round(score.score, 6),
                    "q": round(score.q, 6),
                    "net_edge": round(score.net_edge, 6),
                    "gross_edge": round(score.gross_edge, 6),
                    "rvol": score.features.get("rvol"),
                    "squeeze": score.features.get("squeeze"),
                }
            )
        return out

    async def _subscribe_loop(self) -> None:
        if self._bus is None or not self._settings.symbols:
            return
        while not self._stop.is_set():
            try:
                async for tick in self._bus.subscribe_ticks(self._settings.symbols):
                    if self._stop.is_set():
                        return
                    if tick.symbol:
                        self._dirty.add(tick.symbol)
            except asyncio.CancelledError:
                raise
            except BusUnavailable as exc:
                logger.warning("alpha: redis subscribe ended (%s); retrying", exc)
                await _sleep_or_stop(self._stop, _SUBSCRIBE_RETRY_SECONDS)

    async def _score_loop(self) -> None:
        interval = max(1.0, float(self._settings.interval_seconds))
        while not self._stop.is_set():
            symbols = sorted(self._dirty) or list(self._settings.symbols)
            self._dirty.clear()
            try:
                await self.run_once(symbols)
            except Exception:
                logger.exception("alpha: score cycle failed")
            await _sleep_or_stop(self._stop, interval)

    async def start(self, *, once: bool = False) -> int:
        if not self._settings.symbols:
            logger.error("no symbols configured (set MT5_TICK_DAEMON_SYMBOLS or enable rotation universe)")
            return 2

        # Same contract as the tick daemon: a Docker outage must not kill the
        # process. Persist/subscribe retry once Postgres and Redis are back.
        if not await self._store.ensure_open():
            logger.warning("postgres unavailable at start; will persist when it returns")
        if self._bus is not None:
            await self._bus.ensure_open()

        _log_event(
            {
                "event": "ALPHA_START",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbols": list(self._settings.symbols),
                "timeframe": self._settings.timeframe,
                "shadow": True,
            }
        )
        try:
            if once:
                await self.run_once()
                _log_event({"event": "ALPHA_ONCE", **self.stats})
                return 0
            await asyncio.gather(self._subscribe_loop(), self._score_loop())
            return 0
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        await self._store.close()
        if self._bus is not None:
            await self._bus.close()
        _log_event({"event": "ALPHA_STOP", **self.stats})


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, float(seconds)))
    except (asyncio.TimeoutError, TimeoutError):
        pass


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Shadow-only multi-factor alpha engine.")
    ap.add_argument("--symbols", default=None, help="Comma-separated override of the symbol list.")
    ap.add_argument("--once", action="store_true", help="Score each symbol once and exit.")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    _load_dotenv()
    _setup_logging(args.log_level)

    settings = AlphaSettings.from_env()
    if args.symbols:
        settings = AlphaSettings(
            **{
                **settings.__dict__,
                "symbols": [s.strip().upper() for s in args.symbols.split(",") if s.strip()],
            }
        )

    try:
        pg = PgSettings.from_env()
    except DataLayerConfigError as exc:
        logger.error("postgres disabled: %s", exc)
        return 2

    import os

    engine = AlphaEngine(
        settings=settings,
        store=PgStore(pg),
        bus=RedisBus(RedisSettings.from_env()),
        point_overrides=parse_point_overrides(os.getenv("MT5_ALPHA_POINTS")),
    )

    async def _run() -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, engine.request_stop)
            except (NotImplementedError, AttributeError):
                pass
        return await engine.start(once=args.once)

    try:
        return run_async(_run)
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
