"""Env-driven settings for the local quant data layer.

Everything here is read from the environment. Credentials are never defaulted
to a real value: MT5_PG_PASSWORD has no fallback, so a missing password fails
loudly at startup instead of silently connecting to something unexpected.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

Env = Mapping[str, str]

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def _get(env: Optional[Env], key: str, default: str = "") -> str:
    src = env if env is not None else os.environ
    raw = src.get(key)
    if raw is None:
        return default
    raw = str(raw).strip()
    return raw if raw else default


def env_bool(env: Optional[Env], key: str, default: bool = False) -> bool:
    raw = _get(env, key).lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def env_int(env: Optional[Env], key: str, default: int) -> int:
    try:
        return int(float(_get(env, key, str(default))))
    except (TypeError, ValueError):
        return default


def env_float(env: Optional[Env], key: str, default: float) -> float:
    try:
        return float(_get(env, key, str(default)))
    except (TypeError, ValueError):
        return default


def env_csv(env: Optional[Env], key: str, default: Sequence[str] = ()) -> list[str]:
    raw = _get(env, key)
    if not raw:
        return [str(x).strip().upper() for x in default if str(x).strip()]
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def shadow_market_symbols(env: Optional[Env] = None) -> list[str]:
    """Tick daemon + alpha engine universe. Never used to place orders.

    ``MT5_TICK_DAEMON_SYMBOLS`` wins. With rotation ON (the live default) the
    empty list means the 79 XBTFX CFDs, not ``BOT_SYMBOLS`` (BTC/ETH/JPY leftover).
    """
    listed = env_csv(env, "MT5_TICK_DAEMON_SYMBOLS")
    if listed:
        return listed
    if env_bool(env, "BOT_ROTATION_ENABLED", True):
        from trading.domain.crypto_universe import CRYPTO_UNIVERSE

        return [str(s).strip().upper() for s in CRYPTO_UNIVERSE if str(s).strip()]
    return env_csv(env, "BOT_SYMBOLS")


class DataLayerConfigError(ValueError):
    """Raised when required data-layer configuration is missing or invalid."""


@dataclass(frozen=True)
class PgSettings:
    host: str = "127.0.0.1"
    port: int = 5433
    user: str = "metatrader"
    password: str = ""
    dbname: str = "metatrader"
    pool_min: int = 1
    pool_max: int = 8
    connect_timeout: int = 5

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "PgSettings":
        password = _get(env, "MT5_PG_PASSWORD")
        if not password:
            raise DataLayerConfigError(
                "MT5_PG_PASSWORD is not set. Add it to .env (see .env.example section F)."
            )
        pool_min = max(1, env_int(env, "MT5_PG_POOL_MIN", 1))
        pool_max = max(pool_min, env_int(env, "MT5_PG_POOL_MAX", 8))
        return cls(
            host=_get(env, "MT5_PG_HOST", "127.0.0.1"),
            port=env_int(env, "MT5_PG_PORT", 5433),
            user=_get(env, "MT5_PG_USER", "metatrader"),
            password=password,
            dbname=_get(env, "MT5_PG_DB", "metatrader"),
            pool_min=pool_min,
            pool_max=pool_max,
            connect_timeout=env_int(env, "MT5_PG_CONNECT_TIMEOUT", 5),
        )

    def conninfo(self) -> str:
        # Built by psycopg so passwords with spaces or quotes are escaped properly.
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            connect_timeout=self.connect_timeout,
            application_name="meta-trader-data-layer",
        )

    def redacted(self) -> str:
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.dbname}"


@dataclass(frozen=True)
class RedisSettings:
    host: str = "127.0.0.1"
    port: int = 6380
    db: int = 0
    stream_maxlen: int = 10_000

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "RedisSettings":
        return cls(
            host=_get(env, "MT5_REDIS_HOST", "127.0.0.1"),
            port=env_int(env, "MT5_REDIS_PORT", 6380),
            db=env_int(env, "MT5_REDIS_DB", 0),
            stream_maxlen=max(0, env_int(env, "MT5_TICK_STREAM_MAXLEN", 10_000)),
        )

    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


@dataclass(frozen=True)
class RetentionSettings:
    """TTL for the ephemeral market-data tier.

    Partitions are daily, so an N-hour TTL is enforced at day granularity:
    rows survive between N hours and N hours + 1 day. Dropping a whole
    partition is orders of magnitude cheaper than DELETE-ing tick rows, and
    that tradeoff is the entire reason the tables are partitioned.
    """

    tick_ttl_hours: int = 48
    bar_ttl_days: int = 30
    janitor_interval_minutes: int = 30

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "RetentionSettings":
        return cls(
            tick_ttl_hours=max(1, env_int(env, "MT5_TICK_TTL_HOURS", 48)),
            bar_ttl_days=max(1, env_int(env, "MT5_BAR_TTL_DAYS", 30)),
            janitor_interval_minutes=max(1, env_int(env, "MT5_JANITOR_INTERVAL_MINUTES", 30)),
        )


@dataclass(frozen=True)
class AlphaSettings:
    """Shadow-mode alpha engine. Nothing here can enable live execution."""

    enabled: bool = False
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "M15"
    interval_seconds: float = 30.0
    bars: int = 300
    w_momentum: float = 0.35
    w_volatility: float = 0.15
    w_zscore: float = 0.30
    w_flow: float = 0.20
    zscore_lookback: int = 50
    commission_points: float = 0.0
    slippage_points: float = 0.0
    min_score: float = 0.15

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "AlphaSettings":
        return cls(
            enabled=env_bool(env, "MT5_ALPHA_ENABLED", False),
            symbols=shadow_market_symbols(env),
            timeframe=_get(env, "MT5_ALPHA_TIMEFRAME", "M15").upper(),
            interval_seconds=max(1.0, env_float(env, "MT5_ALPHA_INTERVAL_SECONDS", 30.0)),
            bars=max(50, env_int(env, "MT5_ALPHA_BARS", 300)),
            w_momentum=max(0.0, env_float(env, "MT5_ALPHA_W_MOMENTUM", 0.35)),
            w_volatility=max(0.0, env_float(env, "MT5_ALPHA_W_VOLATILITY", 0.15)),
            w_zscore=max(0.0, env_float(env, "MT5_ALPHA_W_ZSCORE", 0.30)),
            w_flow=max(0.0, env_float(env, "MT5_ALPHA_W_FLOW", 0.20)),
            zscore_lookback=max(2, env_int(env, "MT5_ALPHA_ZSCORE_LOOKBACK", 50)),
            commission_points=max(0.0, env_float(env, "MT5_ALPHA_COMMISSION_POINTS", 0.0)),
            slippage_points=max(0.0, env_float(env, "MT5_ALPHA_SLIPPAGE_POINTS", 0.0)),
            min_score=max(0.0, env_float(env, "MT5_ALPHA_MIN_SCORE", 0.15)),
        )


def _dashboard_path(env: Optional[Env]) -> str:
    raw = _get(env, "MT5_MANUAL_MINER_DASHBOARD_PATH", "bot_state.json")[:240]
    name = raw.replace("\\", "/").split("/")[-1]
    if not name.lower().endswith(".json"):
        return "bot_state.json"
    return raw or "bot_state.json"


def parse_correlation_groups(raw: str) -> dict[str, tuple[str, ...]]:
    """BOT_CORRELATION_GROUPS JSON. Invalid input yields an empty map."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, value in list(data.items())[:40]:
        name = str(key or "").strip()[:40]
        if not name or not isinstance(value, list):
            continue
        members = tuple(
            str(item).strip().upper()[:32]
            for item in value[:40]
            if str(item).strip()
        )
        if members:
            out[name] = members
    return out


@dataclass(frozen=True)
class ManualMinerSettings:
    """Read-only journal of magic-0 closes. Cannot enable live execution."""

    lookback_days: int = 14
    timeframe: str = "M15"
    bars: int = 120
    interval_seconds: float = 300.0
    bot_magics: tuple[int, ...] = (260414, 260415)
    correlation_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    dashboard_path: str = "bot_state.json"
    dashboard_max_lag_seconds: float = 900.0
    pattern_min_n: int = 3
    zscore_lookback: int = 50
    report_every_n: int = 10
    stat_min_winners: int = 30

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "ManualMinerSettings":
        magics: list[int] = []
        for key, default in (("BOT_MAGIC", 260414), ("BOT_XAUUSD_PARALLEL_MAGIC", 260415)):
            magic = env_int(env, key, default)
            if magic > 0 and magic not in magics:
                magics.append(magic)
        return cls(
            lookback_days=max(1, env_int(env, "MT5_MANUAL_MINER_LOOKBACK_DAYS", 14)),
            timeframe=_get(env, "MT5_MANUAL_MINER_TIMEFRAME", "M15").upper()[:8],
            bars=max(30, env_int(env, "MT5_MANUAL_MINER_BARS", 120)),
            interval_seconds=max(5.0, env_float(env, "MT5_MANUAL_MINER_INTERVAL_SECONDS", 300.0)),
            bot_magics=tuple(magics) or (260414, 260415),
            correlation_groups=parse_correlation_groups(_get(env, "BOT_CORRELATION_GROUPS")),
            dashboard_path=_dashboard_path(env),
            dashboard_max_lag_seconds=max(0.0, env_float(env, "MT5_MANUAL_MINER_DASHBOARD_LAG_SECONDS", 900.0)),
            pattern_min_n=max(2, env_int(env, "MT5_MANUAL_MINER_MIN_N", 3)),
            zscore_lookback=max(2, env_int(env, "MT5_ALPHA_ZSCORE_LOOKBACK", 50)),
            report_every_n=max(1, env_int(env, "MT5_MANUAL_MINER_REPORT_EVERY", 10)),
            stat_min_winners=max(1, env_int(env, "MT5_MANUAL_MINER_STAT_MIN", 30)),
        )


@dataclass(frozen=True)
class IngestSettings:
    symbols: list[str] = field(default_factory=list)
    tick_poll_seconds: float = 1.0
    tick_batch_max: int = 5000
    persist_ticks: bool = True
    publish_ticks: bool = True
    bar_timeframes: list[str] = field(default_factory=lambda: ["M1", "M15"])
    bar_poll_seconds: float = 30.0
    bar_backfill: int = 500

    @classmethod
    def from_env(cls, env: Optional[Env] = None) -> "IngestSettings":
        return cls(
            symbols=shadow_market_symbols(env),
            tick_poll_seconds=max(0.1, env_float(env, "MT5_TICK_POLL_SECONDS", 1.0)),
            tick_batch_max=max(100, env_int(env, "MT5_TICK_BATCH_MAX", 5000)),
            persist_ticks=env_bool(env, "MT5_TICK_PERSIST_ENABLED", True),
            publish_ticks=env_bool(env, "MT5_TICK_PUBLISH_ENABLED", True),
            bar_timeframes=env_csv(env, "MT5_BAR_TIMEFRAMES", ("M1", "M15")),
            bar_poll_seconds=max(1.0, env_float(env, "MT5_BAR_POLL_SECONDS", 30.0)),
            bar_backfill=max(0, env_int(env, "MT5_BAR_BACKFILL", 500)),
        )
