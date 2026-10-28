"""Manual-trade journal: pair MT5 deals, snapshot context, describe patterns.

Pure. No MT5, no database, no order path. Patterns are counts with a minimum
sample. They are not a licence to automate the signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from trading.domain.quant_factors import (
    bollinger_bandwidth,
    directional_probability,
    normal_two_tailed_survival,
    price_zscore,
    relative_volume,
    rsi_state,
)

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2
DEAL_TYPE_CREDIT = 3

_CLOSE_ENTRIES = frozenset({DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY})
_NON_TRADE_TYPES = frozenset({DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT})

_DASHBOARD_KEYS = ("running", "ts", "mode", "symbols")


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def deal_time(raw: Any) -> Optional[datetime]:
    """MT5 deal time as an aware datetime in the server frame labeled UTC.

    Same convention as md_bars.ts: the broker epoch is stored with a UTC
    tzinfo and is not shifted to the host timezone. Joining on opened_at
    then lines up with bars.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        secs = float(raw)
        if secs > 1e12:
            secs /= 1000.0
        if secs <= 0:
            return None
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_balance_deal(deal: Mapping[str, Any]) -> bool:
    return _i(deal.get("type"), -1) in _NON_TRADE_TYPES


def is_close_deal(deal: Mapping[str, Any]) -> bool:
    return _i(deal.get("entry"), -1) in _CLOSE_ENTRIES and not is_balance_deal(deal)


def is_entry_deal(deal: Mapping[str, Any]) -> bool:
    return _i(deal.get("entry"), -1) == DEAL_ENTRY_IN and not is_balance_deal(deal)


def is_manual_magic(magic: int, bot_magics: Sequence[int]) -> bool:
    """Manual means magic 0. Any positive magic owned by the bot is excluded.

    Other positive magics belong to some other EA and are not this operator's
    discretionary trades.
    """
    if int(magic) != 0:
        return False
    owned = {int(m) for m in bot_magics if int(m) > 0}
    return int(magic) not in owned


def deal_net(deal: Mapping[str, Any]) -> float:
    profit = _f(deal.get("profit")) or 0.0
    swap = _f(deal.get("swap")) or 0.0
    commission = _f(deal.get("commission")) or 0.0
    return profit + swap + commission


def _position_side(entries: Sequence[Mapping[str, Any]], closes: Sequence[Mapping[str, Any]]) -> str:
    if entries:
        dtype = _i(entries[0].get("type"), -1)
        if dtype == DEAL_TYPE_BUY:
            return "BUY"
        if dtype == DEAL_TYPE_SELL:
            return "SELL"
    if closes:
        dtype = _i(closes[-1].get("type"), -1)
        if dtype == DEAL_TYPE_BUY:
            return "SELL"
        if dtype == DEAL_TYPE_SELL:
            return "BUY"
    return "?"


@dataclass(frozen=True)
class ManualRoundTrip:
    position_id: int
    symbol: str
    side: str
    magic: int
    comment: str
    volume: Optional[float]
    price_open: Optional[float]
    price_close: Optional[float]
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    net_profit: float
    close_deal: int

    @property
    def winner(self) -> bool:
        return self.net_profit > 0.0


def manual_round_trips(
    deals: Sequence[Mapping[str, Any]],
    *,
    bot_magics: Sequence[int] = (),
) -> list[ManualRoundTrip]:
    """Closed manual round-trips. Still-open positions are omitted."""
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for deal in deals or ():
        if not isinstance(deal, Mapping) or is_balance_deal(deal):
            continue
        pid = _i(deal.get("position_id"), 0)
        if pid <= 0:
            continue
        grouped.setdefault(pid, []).append(deal)

    trips: list[ManualRoundTrip] = []
    for pid, group in grouped.items():
        entries = [d for d in group if is_entry_deal(d)]
        closes = [d for d in group if is_close_deal(d)]
        if not closes:
            continue
        magic_source = entries[0] if entries else closes[-1]
        magic = _i(magic_source.get("magic"), 0)
        if not is_manual_magic(magic, bot_magics):
            continue
        entry_times = [t for t in (deal_time(d.get("time")) for d in entries) if t is not None]
        close_times = [t for t in (deal_time(d.get("time")) for d in closes) if t is not None]
        opened = min(entry_times) if entry_times else (min(close_times) if close_times else None)
        closed = max(close_times) if close_times else None
        price_open = _f(entries[0].get("price")) if entries else None
        price_close = _f(closes[-1].get("price"))
        volume = _f(entries[0].get("volume")) if entries else _f(closes[-1].get("volume"))
        comment = str((entries[0] if entries else closes[-1]).get("comment") or "")[:120]
        symbol = str((entries[0] if entries else closes[-1]).get("symbol") or "").strip().upper()
        if not symbol or len(symbol) > 32:
            continue
        close_deal = _i(closes[-1].get("deal") or closes[-1].get("ticket"), 0)
        trips.append(
            ManualRoundTrip(
                position_id=pid,
                symbol=symbol,
                side=_position_side(entries, closes),
                magic=magic,
                comment=comment,
                volume=volume,
                price_open=price_open,
                price_close=price_close,
                opened_at=opened,
                closed_at=closed,
                net_profit=sum(deal_net(d) for d in group if not is_balance_deal(d)),
                close_deal=close_deal,
            )
        )
    trips.sort(key=lambda t: t.closed_at or datetime.min.replace(tzinfo=timezone.utc))
    return trips


def count_open_manual(positions: Sequence[Mapping[str, Any]]) -> int:
    """Open positions with magic 0. Other magics are not this operator's manual book."""
    n = 0
    for pos in positions or ():
        if not isinstance(pos, Mapping):
            continue
        if _i(pos.get("magic"), 0) != 0:
            continue
        if not str(pos.get("symbol") or "").strip():
            continue
        n += 1
    return n


def groups_for_symbol(symbol: str, groups: Mapping[str, Sequence[str]]) -> list[str]:
    sym = str(symbol or "").strip().upper()
    found: list[str] = []
    for name, members in (groups or {}).items():
        member_set = {str(m).strip().upper() for m in members or () if str(m).strip()}
        if sym in member_set:
            found.append(str(name))
    return sorted(found)


def bars_asof(bars: Sequence[Any], opened_at: Optional[datetime]) -> list[Any]:
    """Bars whose timestamp is at or before the open. No future bars."""
    if opened_at is None:
        return []
    when = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc)
    kept = []
    for bar in bars or ():
        ts = getattr(bar, "ts", None)
        if not isinstance(ts, datetime):
            continue
        bar_ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if bar_ts <= when:
            kept.append(bar)
    return kept


@dataclass(frozen=True)
class TradeContext:
    hour_of_day: Optional[int]
    correlation_groups: str
    zscore: Optional[float]
    z_tail: Optional[float]
    rsi: Optional[float]
    rsi_velocity: Optional[float]
    rsi_velocity_atr: Optional[float]
    atr: Optional[float]
    bandwidth: Optional[float]
    bandwidth_percentile: Optional[float]
    squeeze: Optional[bool]
    rvol: Optional[float]
    q: Optional[float]
    alpha_direction: Optional[str]
    alpha_score: Optional[float]
    dashboard: Optional[dict]

    def as_dict(self) -> dict:
        return {
            "hour_of_day": self.hour_of_day,
            "correlation_groups": self.correlation_groups,
            "zscore": self.zscore,
            "z_tail": self.z_tail,
            "rsi": self.rsi,
            "rsi_velocity": self.rsi_velocity,
            "rsi_velocity_atr": self.rsi_velocity_atr,
            "atr": self.atr,
            "bandwidth": self.bandwidth,
            "bandwidth_percentile": self.bandwidth_percentile,
            "squeeze": self.squeeze,
            "rvol": self.rvol,
            "q": self.q,
            "alpha_direction": self.alpha_direction,
            "alpha_score": self.alpha_score,
            "dashboard": self.dashboard,
        }


def context_from_bars(
    bars: Sequence[Any],
    *,
    opened_at: Optional[datetime],
    symbol: str,
    groups: Optional[Mapping[str, Sequence[str]]] = None,
    atr_value: Optional[float] = None,
    alpha_direction: Optional[str] = None,
    alpha_score: Optional[float] = None,
    alpha_q: Optional[float] = None,
    dashboard: Optional[dict] = None,
    zscore_lookback: int = 50,
    flow_lookback: int = 20,
) -> TradeContext:
    """Snapshot of the open, using only bars at or before opened_at."""
    usable = bars_asof(bars, opened_at)
    closes = [float(b.close) for b in usable]
    volumes = [float(b.tick_volume or 0.0) for b in usable]
    z = price_zscore(closes, lookback=zscore_lookback)
    bb = bollinger_bandwidth(closes)
    rvol = relative_volume(volumes, lookback=flow_lookback)
    rsi = rsi_state(closes, atr_value)
    z_tail = normal_two_tailed_survival(z) if z is not None else None
    hour = opened_at.hour if isinstance(opened_at, datetime) else None
    names = groups_for_symbol(symbol, groups or {})
    q = alpha_q
    if q is None and alpha_score is not None:
        q = directional_probability(float(alpha_score))
    return TradeContext(
        hour_of_day=hour,
        correlation_groups=",".join(names),
        zscore=z,
        z_tail=z_tail,
        rsi=rsi.rsi,
        rsi_velocity=rsi.velocity,
        rsi_velocity_atr=rsi.velocity_atr,
        atr=atr_value,
        bandwidth=None if bb is None else bb.bandwidth,
        bandwidth_percentile=None if bb is None else bb.bandwidth_percentile,
        squeeze=None if bb is None else bb.squeeze,
        rvol=rvol,
        q=q,
        alpha_direction=(str(alpha_direction).upper() if alpha_direction else None),
        alpha_score=alpha_score,
        dashboard=dashboard,
    )


def dashboard_slice(
    state: Optional[Mapping[str, Any]],
    symbol: str,
    *,
    observed_at: Optional[datetime],
    closed_at: Optional[datetime],
    max_lag_seconds: float = 900.0,
) -> Optional[dict]:
    """Whitelist a few dashboard fields when the file is close to the close time.

    bot_state.json is a live snapshot. Attaching it to a close from yesterday
    would invent context the operator did not have. Far snapshots are dropped.
    """
    if not isinstance(state, Mapping) or observed_at is None or closed_at is None:
        return None
    left = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    right = closed_at if closed_at.tzinfo else closed_at.replace(tzinfo=timezone.utc)
    if abs((left - right).total_seconds()) > float(max_lag_seconds):
        return None
    out: dict[str, Any] = {}
    for key in _DASHBOARD_KEYS:
        if key in state:
            value = state.get(key)
            if key == "symbols" and isinstance(value, list):
                out[key] = [str(s)[:32] for s in value[:40]]
            elif key == "running":
                out[key] = bool(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value if not isinstance(value, str) else value[:80]
    regimes = state.get("market_regimes")
    sym = str(symbol or "").strip().upper()
    if isinstance(regimes, Mapping) and sym in regimes and isinstance(regimes.get(sym), Mapping):
        regime = regimes[sym]
        out["symbol_regime"] = {
            str(k)[:40]: (str(v)[:80] if not isinstance(v, (int, float, bool)) else v)
            for k, v in list(regime.items())[:12]
            if k not in {"password", "token", "secret", "login"}
        }
    return out or None


def journal_record(trip: ManualRoundTrip, context: TradeContext) -> dict:
    """One persistent row. Factor fields may be null when bars were missing."""
    return {
        "position_id": int(trip.position_id),
        "close_deal": int(trip.close_deal) if trip.close_deal else None,
        "symbol": trip.symbol,
        "side": trip.side,
        "magic": int(trip.magic),
        "comment": trip.comment[:120],
        "volume": trip.volume,
        "price_open": trip.price_open,
        "price_close": trip.price_close,
        "opened_at": trip.opened_at,
        "closed_at": trip.closed_at,
        "net_profit": float(trip.net_profit),
        "winner": bool(trip.net_profit > 0.0),
        "hour_of_day": context.hour_of_day,
        "correlation_groups": context.correlation_groups[:120],
        "zscore": context.zscore,
        "rsi": context.rsi,
        "rsi_velocity": context.rsi_velocity,
        "atr": context.atr,
        "bandwidth": context.bandwidth,
        "squeeze": context.squeeze,
        "rvol": context.rvol,
        "alpha_direction": context.alpha_direction,
        "alpha_score": context.alpha_score,
        "q": context.q,
        "dashboard": context.dashboard,
        "context": context.as_dict(),
    }


def hour_bucket(hour: Optional[int]) -> str:
    if hour is None:
        return "unknown"
    h = int(hour)
    if h < 0 or h > 23:
        return "unknown"
    if h < 6:
        return "00-05"
    if h < 12:
        return "06-11"
    if h < 18:
        return "12-17"
    return "18-23"


def z_bucket(z: Optional[float]) -> str:
    if z is None:
        return "unknown"
    if z >= 1.5:
        return "stretched_up"
    if z <= -1.5:
        return "stretched_down"
    return "near_mean"


def rvol_bucket(rvol: Optional[float]) -> str:
    if rvol is None:
        return "unknown"
    if rvol < 0.7:
        return "thin"
    if rvol > 1.5:
        return "heavy"
    return "normal"


def squeeze_bucket(squeeze: Optional[bool]) -> str:
    if squeeze is None:
        return "unknown"
    return "squeeze" if squeeze else "open"


@dataclass(frozen=True)
class PatternRow:
    signature: str
    n: int
    n_winners: int
    win_rate: float
    avg_net_winners: float
    lift: float


@dataclass(frozen=True)
class PatternReport:
    n: int
    n_winners: int
    n_losers: int
    base_win_rate: float
    min_n: int
    patterns: list[PatternRow] = field(default_factory=list)
    by_symbol: list[dict] = field(default_factory=list)
    note: str = (
        "Descriptive counts only. n under 30 is noise. "
        "These signatures are not a validated edge and must not be auto-traded."
    )

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "n_winners": self.n_winners,
            "n_losers": self.n_losers,
            "base_win_rate": self.base_win_rate,
            "min_n": self.min_n,
            "patterns": [p.__dict__ for p in self.patterns],
            "by_symbol": list(self.by_symbol),
            "note": self.note,
        }


def _row_flag(row: Mapping[str, Any], key: str) -> Optional[bool]:
    raw = row.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def pattern_report(rows: Sequence[Mapping[str, Any]], *, min_n: int = 3) -> PatternReport:
    """Winner signatures versus the manual-trade base rate.

    A bucket is reported only when both the bucket and its winner count clear
    min_n. Lift is bucket win rate divided by the base win rate.
    """
    usable = [r for r in rows or () if isinstance(r, Mapping)]
    n = len(usable)
    winners = [r for r in usable if bool(r.get("winner"))]
    n_winners = len(winners)
    n_losers = n - n_winners
    base = (n_winners / n) if n else 0.0
    floor = max(1, int(min_n))

    buckets: dict[str, list[Mapping[str, Any]]] = {}
    symbols: dict[str, list[Mapping[str, Any]]] = {}
    for row in usable:
        side = str(row.get("side") or "?").upper()
        signature = "|".join(
            (
                side,
                z_bucket(_f(row.get("zscore"))),
                rvol_bucket(_f(row.get("rvol"))),
                squeeze_bucket(_row_flag(row, "squeeze")),
                hour_bucket(_i(row.get("hour_of_day"), -1) if row.get("hour_of_day") is not None else None),
            )
        )
        buckets.setdefault(signature, []).append(row)
        symbols.setdefault(str(row.get("symbol") or "?").upper(), []).append(row)

    patterns: list[PatternRow] = []
    for signature, group in buckets.items():
        wins = [r for r in group if bool(r.get("winner"))]
        if len(group) < floor or len(wins) < floor:
            continue
        win_rate = len(wins) / len(group)
        avg = sum(_f(r.get("net_profit")) or 0.0 for r in wins) / len(wins)
        lift = (win_rate / base) if base > 0.0 else 0.0
        patterns.append(
            PatternRow(
                signature=signature,
                n=len(group),
                n_winners=len(wins),
                win_rate=win_rate,
                avg_net_winners=avg,
                lift=lift,
            )
        )
    patterns.sort(key=lambda p: (p.lift, p.n_winners), reverse=True)

    by_symbol = []
    for symbol, group in sorted(symbols.items()):
        wins = [r for r in group if bool(r.get("winner"))]
        by_symbol.append(
            {
                "symbol": symbol,
                "n": len(group),
                "n_winners": len(wins),
                "win_rate": (len(wins) / len(group)) if group else 0.0,
                "avg_net": (sum(_f(r.get("net_profit")) or 0.0 for r in group) / len(group)) if group else 0.0,
            }
        )
    by_symbol.sort(key=lambda r: r["n"], reverse=True)
    return PatternReport(
        n=n,
        n_winners=n_winners,
        n_losers=n_losers,
        base_win_rate=base,
        min_n=floor,
        patterns=patterns,
        by_symbol=by_symbol,
    )


@dataclass(frozen=True)
class AlphaComparison:
    """Manual winners versus the shadow alpha direction stored at the open.

    refit_applied is always False. A small discretionary sample is not a
    licence to rewrite live weights or the Kelly input.
    """

    n_winners: int
    n_with_alpha: int
    n_agree: int
    n_oppose: int
    n_alpha_skipped: int
    agreement: Optional[float]
    winner_mean_abs_z: Optional[float]
    loser_mean_abs_z: Optional[float]
    winner_mean_rvol: Optional[float]
    loser_mean_rvol: Optional[float]
    winner_squeeze_rate: Optional[float]
    refit_allowed: bool
    refit_applied: bool = False
    note: str = (
        "Descriptive contrast only. Weights and Kelly are unchanged. "
        "n under 30 is noise and must not retune the alpha formula."
    )

    def as_dict(self) -> dict:
        return {
            "n_winners": self.n_winners,
            "n_with_alpha": self.n_with_alpha,
            "n_agree": self.n_agree,
            "n_oppose": self.n_oppose,
            "n_alpha_skipped": self.n_alpha_skipped,
            "agreement": self.agreement,
            "winner_mean_abs_z": self.winner_mean_abs_z,
            "loser_mean_abs_z": self.loser_mean_abs_z,
            "winner_mean_rvol": self.winner_mean_rvol,
            "loser_mean_rvol": self.loser_mean_rvol,
            "winner_squeeze_rate": self.winner_squeeze_rate,
            "refit_allowed": self.refit_allowed,
            "refit_applied": self.refit_applied,
            "note": self.note,
        }


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / float(len(values))


def compare_winners_to_alpha(rows: Sequence[Mapping[str, Any]]) -> AlphaComparison:
    """Contrast closed manual winners with the shadow score captured at entry."""
    usable = [r for r in rows or () if isinstance(r, Mapping)]
    winners = [r for r in usable if bool(r.get("winner"))]
    losers = [r for r in usable if not bool(r.get("winner"))]
    with_alpha = [r for r in winners if str(r.get("alpha_direction") or "").strip()]
    agree = 0
    oppose = 0
    skipped = 0
    for row in with_alpha:
        direction = str(row.get("alpha_direction") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        if direction in {"", "NONE"}:
            skipped += 1
        elif direction == side:
            agree += 1
        else:
            oppose += 1
    n_with = len(with_alpha)
    decided = agree + oppose
    agreement = (agree / decided) if decided else None
    win_z = [abs(z) for z in (_f(r.get("zscore")) for r in winners) if z is not None]
    lose_z = [abs(z) for z in (_f(r.get("zscore")) for r in losers) if z is not None]
    win_r = [v for v in (_f(r.get("rvol")) for r in winners) if v is not None]
    lose_r = [v for v in (_f(r.get("rvol")) for r in losers) if v is not None]
    squeeze_known = [r for r in winners if _row_flag(r, "squeeze") is not None]
    squeeze_rate = None
    if squeeze_known:
        squeeze_rate = sum(1 for r in squeeze_known if _row_flag(r, "squeeze")) / float(len(squeeze_known))
    return AlphaComparison(
        n_winners=len(winners),
        n_with_alpha=n_with,
        n_agree=agree,
        n_oppose=oppose,
        n_alpha_skipped=skipped,
        agreement=agreement,
        winner_mean_abs_z=_mean(win_z),
        loser_mean_abs_z=_mean(lose_z),
        winner_mean_rvol=_mean(win_r),
        loser_mean_rvol=_mean(lose_r),
        winner_squeeze_rate=squeeze_rate,
        refit_allowed=len(winners) >= 30 and n_with >= 30,
        refit_applied=False,
    )


@dataclass(frozen=True)
class ReportCursor:
    """Last persisted analytical summary. In-memory until the daemon writes one."""

    n_trades: int = 0
    n_winners: int = 0
    stat_emitted: bool = False


def should_emit_report(
    cursor: ReportCursor,
    n_trades: int,
    n_winners: int,
    *,
    every_n: int,
    stat_min: int,
) -> str:
    """Trigger name, or "" when this poll should stay quiet.

    every_n counts new journal rows since the last summary. stat_min fires
    once, the first time winners reach the validation floor. Neither path
    rewrites alpha weights.
    """
    every = max(1, int(every_n))
    floor = max(1, int(stat_min))
    total = max(0, int(n_trades))
    wins = max(0, int(n_winners))
    previous = max(0, int(cursor.n_trades))
    fresh = max(0, total - previous)
    crossed_stat = (not cursor.stat_emitted) and wins >= floor
    crossed_batch = fresh >= every and total > previous
    if crossed_stat and crossed_batch:
        return "stat_min+every_n"
    if crossed_stat:
        return "stat_min"
    if crossed_batch:
        return "every_n"
    return ""


def format_alpha_comparison(report: AlphaComparison) -> str:
    agree = "n/a" if report.agreement is None else f"{report.agreement:.1%}"
    lines = [
        f"manual winners vs alpha: winners={report.n_winners} with_alpha={report.n_with_alpha} "
        f"agree={report.n_agree} oppose={report.n_oppose} alpha_skipped={report.n_alpha_skipped} "
        f"agreement={agree}",
        report.note,
        f"refit_allowed={report.refit_allowed} refit_applied={report.refit_applied}",
    ]
    return "\n".join(lines)


def format_pattern_report(report: PatternReport) -> str:
    lines = [
        f"manual trades: {report.n}  winners: {report.n_winners}  losers: {report.n_losers}  "
        f"base win rate: {report.base_win_rate:.1%}",
        report.note,
        f"patterns with n>={report.min_n} and n_winners>={report.min_n}:",
    ]
    if not report.patterns:
        lines.append("  (none — sample too small or no repeated signature)")
    for row in report.patterns:
        lines.append(
            f"  {row.signature}  n={row.n}  winners={row.n_winners}  "
            f"win_rate={row.win_rate:.1%}  lift={row.lift:.2f}  "
            f"avg_win_net={row.avg_net_winners:.2f}"
        )
    if report.by_symbol:
        lines.append("by symbol:")
        for row in report.by_symbol:
            lines.append(
                f"  {row['symbol']}  n={row['n']}  winners={row['n_winners']}  "
                f"win_rate={row['win_rate']:.1%}  avg_net={row['avg_net']:.2f}"
            )
    return "\n".join(lines)
