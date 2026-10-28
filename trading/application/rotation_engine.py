"""Scan M1/M5/M15 bars, vote, apply account gates. No MT5 imports.

The bot supplies bars, open symbols, hourly counts, and a dry-run flag.
This module never calls order_send.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from trading.domain.crypto_universe import BTC_SYMBOL, ETH_SYMBOL, SOL_SYMBOL, CRYPTO_UNIVERSE
from trading.domain.decision_state import overlay_tape
from trading.domain.ensemble import (
    ContractSpec,
    EnsembleSnapshot,
    MAX_RISK_USD,
    MIN_AGREE,
    Ohlcv,
    apply_account_gates,
    cycle_summary,
    evaluate_symbol,
)

PackedBars = tuple[Ohlcv, Ohlcv, Optional[Ohlcv], Optional[int]]
PackedBarsH4 = tuple[Ohlcv, Ohlcv, Optional[Ohlcv], Optional[int], Optional[Ohlcv]]
FetchBars = Callable[[str], Optional[Union[PackedBars, PackedBarsH4]]]
FetchContract = Callable[[str], Optional[ContractSpec]]


def _unpack_bars(
    packed: Union[PackedBars, PackedBarsH4],
) -> tuple[Ohlcv, Ohlcv, Optional[Ohlcv], Optional[int], Optional[Ohlcv]]:
    m1, m5, m15, m1_time = packed[0], packed[1], packed[2], packed[3]
    h4 = packed[4] if len(packed) > 4 else None
    return m1, m5, m15, m1_time, h4


@dataclass(frozen=True)
class RotationSettings:
    symbols: tuple[str, ...] = CRYPTO_UNIVERSE
    min_agree: int = MIN_AGREE
    max_risk_usd: float = MAX_RISK_USD
    hour_cap: int = 60
    dry_run: bool = True


@dataclass(frozen=True)
class RotationCycle:
    ts: str
    snapshots: tuple[EnsembleSnapshot, ...]
    summary: dict
    intended: tuple[EnsembleSnapshot, ...]


def tape_symbols(cycle: RotationCycle) -> list[str]:
    """Symbols whose snapshot carries a decision state (every 4/5, sent or vetoed)."""
    return [s.symbol for s in cycle.snapshots if s.state is not None]


def overlay_cycle_tape(
    cycle: RotationCycle,
    ticks_by_symbol: Mapping[str, Sequence[Any]],
) -> RotationCycle:
    """Add tape imbalance to each 4/5 state. Never changes votes, gates, or action.

    Missing ticks leave ``tape_imbalance`` null. The cutoff already stored in the
    state drops any tick at or after the quote the bot sized on.
    """
    if not ticks_by_symbol:
        return cycle

    def _one(snap: EnsembleSnapshot) -> EnsembleSnapshot:
        if snap.state is None:
            return snap
        ticks = ticks_by_symbol.get(snap.symbol) or ticks_by_symbol.get(
            str(snap.symbol).strip().upper()
        )
        if not ticks:
            return snap
        cutoff = snap.state.get("decision_ts_msc")
        return replace(snap, state=overlay_tape(snap.state, ticks, decision_ts_msc=cutoff))

    return replace(
        cycle,
        snapshots=tuple(_one(s) for s in cycle.snapshots),
        intended=tuple(_one(s) for s in cycle.intended),
    )


def _peer_m5(
    fetch_bars: FetchBars,
    symbol: str,
    cache: dict[str, Optional[Union[PackedBars, PackedBarsH4]]],
) -> Optional[Ohlcv]:
    if symbol not in cache:
        cache[symbol] = fetch_bars(symbol)
    packed = cache[symbol]
    if packed is None:
        return None
    return packed[1]


def run_rotation_cycle(
    *,
    settings: RotationSettings,
    fetch_bars: FetchBars,
    fetch_contract: FetchContract,
    open_symbols: Sequence[str],
    opens_this_hour: int,
    now: Optional[datetime] = None,
) -> RotationCycle:
    wall = now or datetime.now(timezone.utc)
    ts = wall.isoformat(timespec="seconds")
    cache: dict[str, Optional[Union[PackedBars, PackedBarsH4]]] = {}
    btc_m5 = _peer_m5(fetch_bars, BTC_SYMBOL, cache)
    eth_m5 = _peer_m5(fetch_bars, ETH_SYMBOL, cache)
    sol_m5 = _peer_m5(fetch_bars, SOL_SYMBOL, cache)

    snaps: list[EnsembleSnapshot] = []
    open_now = list(open_symbols)
    hour_used = int(opens_this_hour)
    intended: list[EnsembleSnapshot] = []

    for symbol in settings.symbols:
        packed = cache.get(symbol)
        if symbol not in cache:
            packed = fetch_bars(symbol)
            cache[symbol] = packed
        if packed is None:
            continue
        m1, m5, m15, m1_time, h4 = _unpack_bars(packed)
        contract = fetch_contract(symbol) or ContractSpec(
            volume_min=0.01, volume_step=0.01, usd_at_min_lot_min_stop=0.01
        )
        raw = evaluate_symbol(
            symbol,
            m1,
            m5,
            m15=m15,
            h4=h4,
            btc_m5=btc_m5,
            eth_m5=eth_m5,
            sol_m5=sol_m5,
            contract=contract,
            min_agree=settings.min_agree,
            max_risk_usd=settings.max_risk_usd,
            m1_time=m1_time,
        )
        gated = apply_account_gates(
            raw,
            open_symbols=open_now,
            opens_this_hour=hour_used,
            hour_cap=settings.hour_cap,
            dry_run=settings.dry_run,
        )
        snaps.append(gated)
        if gated.action in ("DRY_RUN", "LIVE"):
            intended.append(gated)
            open_now.append(gated.symbol)
            hour_used += 1

    summary = cycle_summary(
        snaps,
        dry_run=settings.dry_run,
        opens_this_hour=int(opens_this_hour),
        hour_cap=settings.hour_cap,
    )
    summary["ts"] = ts
    return RotationCycle(
        ts=ts,
        snapshots=tuple(snaps),
        summary=summary,
        intended=tuple(intended),
    )
