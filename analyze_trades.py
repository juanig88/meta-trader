"""
analyze_trades.py
-----------------
Journal analyzer for trades.log produced by bot_mt5.py.

Parses structured JSON events and produces a KPI report:
  - Overall PnL, win rate, expectancy, drawdown (realized, trade-by-trade)
  - Breakdown by symbol, hour, day-of-week, session (Asia/London/NY)
  - Breakdown by ADX bucket at entry time
  - Filter effectiveness: how often each guard triggered (anti-chase, filter-exit, tight-trail, BE, classic trailing)
  - Signal funnel: how many SIGNAL events became BUY/SELL vs NONE, and why
  - Auto-flag findings (low-winrate hours, bad symbols, ADX buckets, etc.)

Usage (PowerShell):
  py .\\analyze_trades.py
  py .\\analyze_trades.py --log trades.log --since 2026-04-20 --json report.json
  py .\\analyze_trades.py --top-n 10

Runs fully offline (just reads trades.log). Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

try:
    from backtest_extended import win_loss_streaks
except ImportError:  # pragma: no cover

    def win_loss_streaks(pnls: list[float]) -> dict[str, int]:
        max_win = max_loss = cur_w = cur_l = 0
        for p in pnls:
            if p > 0:
                cur_w += 1
                cur_l = 0
                max_win = max(max_win, cur_w)
            elif p < 0:
                cur_l += 1
                cur_w = 0
                max_loss = max(max_loss, cur_l)
            else:
                cur_w = cur_l = 0
        return {"max_win_streak": max_win, "max_loss_streak": max_loss}

# Alineado con analyze_signal_logs.py (ajustar si agregás pares crypto).
_DEFAULT_CRYPTO = frozenset({"BTCUSD", "ETHUSD"})


# --- log parsing ------------------------------------------------------------

# Expected log line format from bot_mt5.py:
#   "YYYY-MM-DD HH:MM:SS | LEVEL | <message>"
# Where <message> for structured events is a JSON object we care about.

_STRUCTURED_EVENTS = {
    "BOT_START",
    "BOT_STOP",
    "RECONCILE_POSITIONS",
    "SIGNAL",
    "TRADE_OPEN",
    "TRADE_CLOSE",
    "BREAK_EVEN",
    "TRAILING_UPDATE",
    "TIGHT_TRAIL",
    "FILTER_EXIT",
    "BAR_EXHAUSTION_SKIP",
    "RATE_LIMIT_SKIP",
    "QUIET_SESSION_SKIP",
    "CORRELATION_SKIP",
    "MONTHLY_LOSS_LIMIT_BLOCK",
    "PORTFOLIO_REBALANCE",
}


def _try_parse_json(text: str) -> Optional[dict]:
    """Try to parse a JSON object from a log message suffix. Returns None if not JSON."""
    s = text.strip()
    if not s or s[0] != "{":
        return None
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def iter_events(path: str, since: Optional[datetime] = None, until: Optional[datetime] = None) -> Iterable[dict]:
    """Yield structured events (dicts with 'event' key) from the log file in order."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split(" | ", 2)
            if len(parts) < 3:
                continue
            ts_str, level, msg = parts[0], parts[1], parts[2]
            obj = _try_parse_json(msg)
            if not obj or obj.get("event") not in _STRUCTURED_EVENTS:
                continue
            ts = obj.get("ts") or ts_str
            try:
                ev_time = datetime.fromisoformat(ts)
            except Exception:
                try:
                    ev_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
            if since and ev_time < since:
                continue
            if until and ev_time > until:
                continue
            obj["_dt"] = ev_time
            yield obj


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _default_trade_log_paths() -> list[str]:
    root = _project_root()
    out: list[str] = []
    p = os.path.join(root, "trades.log")
    if os.path.isfile(p):
        out.append(p)
    pattern = os.path.join(root, "logs", "trades_*.log")
    out.extend(sorted(glob.glob(pattern)))
    return out


def _dedupe_key(ev: dict) -> tuple:
    name = ev.get("event")
    if name == "SIGNAL":
        return ("SIGNAL", ev.get("symbol"), ev.get("ts"))
    if name == "TRADE_CLOSE":
        return (
            "TRADE_CLOSE",
            ev.get("symbol"),
            ev.get("position_id"),
            ev.get("deal_ticket"),
            ev.get("closed_at"),
        )
    if name == "TRADE_OPEN":
        return ("TRADE_OPEN", ev.get("symbol"), ev.get("order"), ev.get("deal"))
    return (name, ev.get("ts"), ev.get("symbol"))


def merge_trade_log_events(
    paths: list[str],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[dict]:
    acc: list[dict] = []
    for path in paths:
        if os.path.isfile(path):
            acc.extend(iter_events(path, since=since, until=until))
    acc.sort(key=lambda e: e.get("_dt") or datetime.min)
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for e in acc:
        k = _dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(e)
    return deduped


def _exclude_crypto_events(events: list[dict], exclude_crypto: bool) -> list[dict]:
    if not exclude_crypto:
        return events
    out: list[dict] = []
    for e in events:
        sym = e.get("symbol")
        if sym and sym in _DEFAULT_CRYPTO:
            continue
        out.append(e)
    return out


# --- domain helpers ---------------------------------------------------------


def _session_of_hour(hour_utc_local: int) -> str:
    """
    Rough session classification based on LOCAL clock hour (no TZ conversion).
    This is a first-approximation; sessions overlap. Kathy Lien recommends:
      Asia      ~02-11 local (Buenos Aires UTC-3 ~= Tokyo 14-23)
      London    ~04-13 local
      NY        ~10-17 local
      Overlap LON/NY ~10-13 local (highest liquidity)
      Quiet    17-02 local (end of NY to start of Asia)
    """
    h = hour_utc_local
    if 10 <= h <= 13:
        return "LON_NY_OVERLAP"
    if 4 <= h <= 10:
        return "LONDON"
    if 13 <= h <= 17:
        return "NY"
    if 17 <= h or h <= 2:
        return "QUIET"
    return "ASIA"


def _adx_bucket(adx: Optional[float]) -> str:
    if adx is None:
        return "unknown"
    if adx < 15:
        return "range (<15)"
    if adx < 20:
        return "weak (15-20)"
    if adx < 25:
        return "developing (20-25)"
    if adx < 35:
        return "strong (25-35)"
    return "very strong (>=35)"


def _fmt_money(v: float) -> str:
    return f"${v:+,.2f}" if v != 0 else "$0.00"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


# --- data model -------------------------------------------------------------


@dataclass
class Trade:
    symbol: str
    side: str
    price_open: float
    price_close: Optional[float]
    profit: float
    swap: float
    commission: float
    net: float
    reason_code: Optional[int]
    reason_label: Optional[str]
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    duration_seconds: Optional[int]
    volume: float
    position_id: Optional[int]
    # enriched from nearest SIGNAL at open time
    adx_at_entry: Optional[float] = None
    trend_h4_at_entry: Optional[str] = None
    ema_dir_at_entry: Optional[str] = None
    spread_at_entry: Optional[float] = None
    # Elder-style grades (Study Guide ch.10); all percentages in [-100, 100] or [0, 100].
    entry_grade: Optional[float] = None
    exit_grade: Optional[float] = None
    trade_grade: Optional[float] = None

    @property
    def is_win(self) -> bool:
        return self.net > 0

    @property
    def hour(self) -> Optional[int]:
        dt = self.opened_at or self.closed_at
        return dt.hour if dt else None

    @property
    def weekday(self) -> Optional[int]:
        dt = self.opened_at or self.closed_at
        return dt.weekday() if dt else None

    @property
    def session(self) -> Optional[str]:
        h = self.hour
        return _session_of_hour(h) if h is not None else None

    @property
    def adx_bucket(self) -> str:
        return _adx_bucket(self.adx_at_entry)


# --- aggregation ------------------------------------------------------------


@dataclass
class Bucket:
    label: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.is_win)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if not t.is_win and t.net < 0)

    @property
    def net(self) -> float:
        return sum(t.net for t in self.trades)

    @property
    def gross_profit(self) -> float:
        return sum(t.net for t in self.trades if t.net > 0)

    @property
    def gross_loss(self) -> float:
        return sum(t.net for t in self.trades if t.net < 0)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n * 100.0) if self.n > 0 else 0.0

    @property
    def expectancy(self) -> float:
        return (self.net / self.n) if self.n > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        gl = abs(self.gross_loss)
        return (self.gross_profit / gl) if gl > 0 else float("inf") if self.gross_profit > 0 else 0.0

    @property
    def avg_win(self) -> float:
        winners = [t.net for t in self.trades if t.net > 0]
        return (sum(winners) / len(winners)) if winners else 0.0

    @property
    def avg_loss(self) -> float:
        losers = [t.net for t in self.trades if t.net < 0]
        return (sum(losers) / len(losers)) if losers else 0.0


def _signal_context_at(signal_events: list[dict], symbol: str, at_dt: datetime) -> Optional[dict]:
    """Find the most recent SIGNAL event for `symbol` strictly at-or-before `at_dt`."""
    best: Optional[dict] = None
    for ev in signal_events:
        if ev.get("symbol") != symbol:
            continue
        dt = ev.get("_dt")
        if dt is None or dt > at_dt:
            continue
        if best is None or dt > best["_dt"]:
            best = ev
    return best


def enrich_trade_closes_from_logged_opens(events: list[dict]) -> None:
    """
    Para TRADE_CLOSE con price_open / opened_at faltantes: cruza el primer TRADE_OPEN
    con el mismo position_id (válido cuando el bot ya loguea position_id en apertura).
    No requiere MT5; útil al analizar trades.log offline.
    """
    by_pid: dict[int, dict[str, Any]] = {}
    for ev in events:
        if ev.get("event") != "TRADE_OPEN":
            continue
        pid = ev.get("position_id")
        try:
            pi = int(pid) if pid is not None else 0
        except (TypeError, ValueError):
            pi = 0
        if pi <= 0 or pi in by_pid:
            continue
        raw_price = ev.get("price_open")
        if raw_price is None:
            raw_price = ev.get("price")
        ts = ev.get("opened_at") or ev.get("ts")
        lot_raw = ev.get("lot")
        if lot_raw is None:
            lot_raw = ev.get("volume")
        lot_f: Optional[float] = None
        if lot_raw is not None:
            try:
                lot_f = float(lot_raw)
            except (TypeError, ValueError):
                lot_f = None
        by_pid[pi] = {"price_open": raw_price, "opened_at": ts, "lot": lot_f}

    for ev in events:
        if ev.get("event") != "TRADE_CLOSE":
            continue
        pid = ev.get("position_id")
        try:
            pi = int(pid) if pid is not None else 0
        except (TypeError, ValueError):
            pi = 0
        if pi <= 0 or pi not in by_pid:
            continue
        src = by_pid[pi]
        po = ev.get("price_open")
        if (po is None or po == 0 or po == 0.0) and src.get("price_open") is not None:
            try:
                ev["price_open"] = float(src["price_open"])
            except (TypeError, ValueError):
                pass
        if not ev.get("opened_at") and src.get("opened_at"):
            ev["opened_at"] = str(src["opened_at"])
        if ev.get("volume_in") is None and src.get("lot") is not None:
            ev["volume_in"] = float(src["lot"])
        if ev.get("opened_at") and ev.get("closed_at"):
            try:
                odt = datetime.fromisoformat(str(ev["opened_at"]))
                cdt = datetime.fromisoformat(str(ev["closed_at"]))
                ev["duration_seconds"] = int((cdt - odt).total_seconds())
            except Exception:
                pass


def build_trades(events: list[dict]) -> list[Trade]:
    """
    Build Trade objects from TRADE_CLOSE events (authoritative for PnL).
    Enrich each with context from the nearest prior SIGNAL event for same symbol.
    """
    closes = [e for e in events if e.get("event") == "TRADE_CLOSE"]
    signals = [e for e in events if e.get("event") == "SIGNAL" and e.get("decision") in ("BUY", "SELL")]
    # Also fall back to NONE-decision SIGNAL events (they still have ADX/EMA context)
    signals_all = [e for e in events if e.get("event") == "SIGNAL"]

    trades: list[Trade] = []
    for ev in closes:
        try:
            opened_at = (
                datetime.fromisoformat(ev["opened_at"])
                if ev.get("opened_at")
                else None
            )
        except Exception:
            opened_at = None
        try:
            closed_at = (
                datetime.fromisoformat(ev["closed_at"])
                if ev.get("closed_at")
                else ev.get("_dt")
            )
        except Exception:
            closed_at = ev.get("_dt")

        t = Trade(
            symbol=ev.get("symbol", "?"),
            side=ev.get("side", "?"),
            price_open=float(ev.get("price_open", 0.0) or 0.0),
            price_close=float(ev["price_close"]) if ev.get("price_close") is not None else None,
            profit=float(ev.get("profit", 0.0) or 0.0),
            swap=float(ev.get("swap", 0.0) or 0.0),
            commission=float(ev.get("commission", 0.0) or 0.0),
            net=float(ev.get("net", 0.0) or 0.0),
            reason_code=ev.get("reason_code"),
            reason_label=ev.get("reason_label"),
            opened_at=opened_at,
            closed_at=closed_at,
            duration_seconds=ev.get("duration_seconds"),
            volume=float(ev.get("volume", 0.0) or 0.0),
            position_id=ev.get("position_id"),
            entry_grade=(float(ev["entry_grade"]) if ev.get("entry_grade") is not None else None),
            exit_grade=(float(ev["exit_grade"]) if ev.get("exit_grade") is not None else None),
            trade_grade=(float(ev["trade_grade"]) if ev.get("trade_grade") is not None else None),
        )

        anchor_dt = opened_at or (closed_at or ev.get("_dt"))
        if anchor_dt is not None:
            ctx = _signal_context_at(signals, t.symbol, anchor_dt) or _signal_context_at(
                signals_all, t.symbol, anchor_dt
            )
            if ctx:
                t.adx_at_entry = ctx.get("adx")
                t.trend_h4_at_entry = ctx.get("trend_h4")
                t.ema_dir_at_entry = ctx.get("ema_dir")
        trades.append(t)
    return trades


def group_by(trades: list[Trade], key_fn) -> dict[Any, Bucket]:
    buckets: dict[Any, Bucket] = {}
    for t in trades:
        k = key_fn(t)
        if k is None:
            k = "unknown"
        buckets.setdefault(k, Bucket(label=str(k))).trades.append(t)
    return buckets


def max_drawdown(trades: list[Trade]) -> tuple[float, float]:
    """Return (max_equity_peak_to_trough_$, as_pct_of_peak). Assumes starting equity=0 cumulative."""
    if not trades:
        return 0.0, 0.0
    ordered = sorted(trades, key=lambda t: t.closed_at or datetime.min)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        equity += t.net
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    pct = (max_dd / peak * 100.0) if peak > 0 else 0.0
    return max_dd, pct


# --- report rendering -------------------------------------------------------


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _table(rows: list[list[Any]], headers: list[str]) -> None:
    if not rows:
        print("  (no data)")
        return
    widths = [len(h) for h in headers]
    str_rows = [[str(c) for c in r] for r in rows]
    for r in str_rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    fmt = "  " + "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    for r in str_rows:
        print(fmt.format(*r))


def _bucket_row(label: str, b: Bucket) -> list[Any]:
    pf = b.profit_factor
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    return [
        label,
        b.n,
        b.wins,
        b.losses,
        _fmt_pct(b.win_rate),
        _fmt_money(b.net),
        _fmt_money(b.expectancy),
        pf_s,
        _fmt_money(b.avg_win),
        _fmt_money(b.avg_loss),
    ]


_BUCKET_HEADERS = [
    "bucket",
    "n",
    "W",
    "L",
    "winrate",
    "net",
    "expect.",
    "PF",
    "avg_win",
    "avg_loss",
]


_SESSION_ORDER = ["LONDON", "LON_NY_OVERLAP", "NY", "QUIET", "ASIA", "unknown"]
_ADX_ORDER = [
    "range (<15)",
    "weak (15-20)",
    "developing (20-25)",
    "strong (25-35)",
    "very strong (>=35)",
    "unknown",
]
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def compute_summary(events: list[dict], trades: list[Trade], top_n: int = 20) -> dict:
    """Compute a JSON-serializable KPI summary from events + trades. No side effects."""
    summary: dict[str, Any] = {}

    overall = Bucket(label="overall", trades=list(trades))
    max_dd_abs, max_dd_pct = max_drawdown(trades)
    ordered = sorted(trades, key=lambda t: t.closed_at or datetime.min)
    streaks = win_loss_streaks([t.net for t in ordered])
    dur_secs = [float(t.duration_seconds) for t in trades if t.duration_seconds is not None]
    avg_hold_m15 = None
    if dur_secs:
        avg_hold_m15 = round(sum(dur_secs) / len(dur_secs) / (15 * 60), 2)
    summary["overall"] = {
        "n_trades": overall.n,
        "wins": overall.wins,
        "losses": overall.losses,
        "win_rate_pct": round(overall.win_rate, 2),
        "net": round(overall.net, 2),
        "expectancy": round(overall.expectancy, 4),
        "profit_factor": None if overall.profit_factor == float("inf") else round(overall.profit_factor, 3),
        "avg_win": round(overall.avg_win, 2),
        "avg_loss": round(overall.avg_loss, 2),
        "max_drawdown_abs": round(max_dd_abs, 2),
        "max_drawdown_pct_peak": round(max_dd_pct, 2),
        "max_win_streak": streaks["max_win_streak"],
        "max_loss_streak": streaks["max_loss_streak"],
        "avg_holding_bars_m15_approx": avg_hold_m15,
    }

    by_sym = group_by(trades, lambda t: t.symbol)
    summary["by_symbol"] = {k: _bucket_to_dict(v) for k, v in by_sym.items()}

    by_side = group_by(trades, lambda t: t.side)
    summary["by_side"] = {k: _bucket_to_dict(v) for k, v in by_side.items()}

    by_hour = group_by(trades, lambda t: t.hour)
    summary["by_hour"] = {str(k): _bucket_to_dict(v) for k, v in by_hour.items()}

    by_session = group_by(trades, lambda t: t.session)
    summary["by_session"] = {k: _bucket_to_dict(v) for k, v in by_session.items()}

    by_wd = group_by(trades, lambda t: t.weekday)
    summary["by_weekday"] = {str(k): _bucket_to_dict(v) for k, v in by_wd.items()}

    by_adx = group_by(trades, lambda t: t.adx_bucket)
    summary["by_adx_bucket"] = {k: _bucket_to_dict(v) for k, v in by_adx.items()}

    by_reason = group_by(trades, lambda t: t.reason_label or f"code_{t.reason_code}")
    summary["by_close_reason"] = {k: _bucket_to_dict(v) for k, v in by_reason.items()}

    ev_counts: dict[str, int] = defaultdict(int)
    for e in events:
        ev_counts[e.get("event", "?")] += 1
    summary["event_counts"] = dict(ev_counts)

    signal_events = [e for e in events if e.get("event") == "SIGNAL"]
    summary["signal_funnel"] = _signal_funnel(signal_events)

    summary["trade_grading"] = _trade_grading_summary(trades)

    summary["equity_curve"] = _equity_curve(trades)

    dt_min = min((e["_dt"] for e in events), default=None)
    dt_max = max((e["_dt"] for e in events), default=None)
    summary["range"] = {
        "from": dt_min.isoformat(timespec="seconds") if dt_min else None,
        "to": dt_max.isoformat(timespec="seconds") if dt_max else None,
        "n_events": len(events),
        "n_trades": len(trades),
    }

    summary["findings"] = _auto_findings(summary, trades, top_n)
    return summary


def analyze_log(
    log_path: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    top_n: int = 20,
) -> dict:
    """
    High-level entry point: read a log file and return the summary dict.
    Raises FileNotFoundError if the log does not exist.
    """
    events = list(iter_events(log_path, since=since, until=until))
    enrich_trade_closes_from_logged_opens(events)
    trades = build_trades(events)
    summary = compute_summary(events, trades, top_n=top_n)
    return summary


def render_summary(events: list[dict], trades: list[Trade], top_n: int) -> dict:
    """Print full report to stdout and return the JSON-serializable summary."""
    summary = compute_summary(events, trades, top_n=top_n)
    overall = Bucket(label="overall", trades=list(trades))
    max_dd_abs, max_dd_pct = max_drawdown(trades)

    _print_header("OVERALL")
    if overall.n == 0:
        print("  No closed trades in log.")
    else:
        _table([_bucket_row("overall", overall)], _BUCKET_HEADERS)
        print(f"  Max realized drawdown: {_fmt_money(-max_dd_abs)} ({_fmt_pct(max_dd_pct)} of peak equity)")

    by_sym = group_by(trades, lambda t: t.symbol)
    _print_header("BY SYMBOL")
    rows = sorted(
        [_bucket_row(s, b) for s, b in by_sym.items()], key=lambda r: -_parse_money(r[5])
    )
    _table(rows, _BUCKET_HEADERS)

    by_side = group_by(trades, lambda t: t.side)
    _print_header("BY SIDE")
    _table(
        [_bucket_row(s, b) for s, b in sorted(by_side.items())],
        _BUCKET_HEADERS,
    )

    by_hour = group_by(trades, lambda t: t.hour)
    _print_header("BY HOUR OF DAY (open)")
    _table(
        [_bucket_row(f"{h:02d}h", b) for h, b in sorted(by_hour.items(), key=lambda x: (x[0] is None, x[0]))],
        _BUCKET_HEADERS,
    )

    by_session = group_by(trades, lambda t: t.session)
    _print_header("BY SESSION (Buenos Aires local)")
    _table(
        [_bucket_row(s, by_session[s]) for s in _SESSION_ORDER if s in by_session],
        _BUCKET_HEADERS,
    )

    by_wd = group_by(trades, lambda t: t.weekday)
    _print_header("BY WEEKDAY")
    _table(
        [
            _bucket_row(_WEEKDAY_NAMES[w] if isinstance(w, int) and 0 <= w <= 6 else str(w), b)
            for w, b in sorted(by_wd.items(), key=lambda x: (x[0] is None, x[0]))
        ],
        _BUCKET_HEADERS,
    )

    by_adx = group_by(trades, lambda t: t.adx_bucket)
    _print_header("BY ADX AT ENTRY")
    _table(
        [_bucket_row(k, by_adx[k]) for k in _ADX_ORDER if k in by_adx],
        _BUCKET_HEADERS,
    )

    by_reason = group_by(trades, lambda t: t.reason_label or f"code_{t.reason_code}")
    _print_header("BY CLOSE REASON")
    _table(
        [_bucket_row(k, v) for k, v in sorted(by_reason.items(), key=lambda x: -x[1].n)],
        _BUCKET_HEADERS,
    )

    _print_header("GUARD / FILTER ACTIVITY")
    interesting = [
        "SIGNAL",
        "TRADE_OPEN",
        "TRADE_CLOSE",
        "BREAK_EVEN",
        "TRAILING_UPDATE",
        "TIGHT_TRAIL",
        "FILTER_EXIT",
        "BAR_EXHAUSTION_SKIP",
        "QUIET_SESSION_SKIP",
        "CORRELATION_SKIP",
        "PORTFOLIO_REBALANCE",
        "RECONCILE_POSITIONS",
    ]
    _table([[e, summary["event_counts"].get(e, 0)] for e in interesting], ["event", "count"])

    _print_header("SIGNAL FUNNEL (decision distribution per symbol)")
    funnel_rows = []
    for sym, d in sorted(summary["signal_funnel"].items()):
        total = d["total"]
        if total == 0:
            continue
        funnel_rows.append(
            [
                sym,
                total,
                d["NONE"],
                d["BUY"],
                d["SELL"],
                f"{(d['BUY'] + d['SELL']) / total * 100.0:.1f}%",
            ]
        )
    _table(funnel_rows, ["symbol", "signals", "NONE", "BUY", "SELL", "act_rate"])

    _print_header("TRADE GRADING (Elder)")
    tg = summary["trade_grading"]
    avg = tg["avg"]
    print(f"  Entry grade avg: {avg['entry_grade_pct']}% (n={tg['n_graded']['entry']})")
    print(f"  Exit  grade avg: {avg['exit_grade_pct']}% (n={tg['n_graded']['exit']})")
    print(f"  Trade grade avg: {avg['trade_grade_pct']}% (n={tg['n_graded']['trade']})")
    dist = tg["letter_distribution"]
    print(f"  Entry letters A/B/C/D = {dist['entry']['A']}/{dist['entry']['B']}/{dist['entry']['C']}/{dist['entry']['D']}")
    print(f"  Exit  letters A/B/C/D = {dist['exit']['A']}/{dist['exit']['B']}/{dist['exit']['C']}/{dist['exit']['D']}")
    tdist = dist["trade"]
    print(f"  Trade letters A/B/C/D/F = {tdist['A']}/{tdist['B']}/{tdist['C']}/{tdist['D']}/{tdist['F']}")
    _table(
        [
            [letter, d["n"], _fmt_money(d["net"]), _fmt_money(d["avg"])]
            for letter, d in tg["pnl_by_trade_grade_letter"].items()
            if d["n"] > 0
        ],
        ["letter", "n", "net", "avg"],
    )

    _print_header("AUTO FINDINGS")
    findings = summary["findings"]
    if not findings:
        print("  (nothing stands out yet; need more closed trades)")
    for i, f in enumerate(findings, 1):
        print(f"  {i:>2}. [{f['severity'].upper()}] {f['message']}")

    return summary


def _letter_from_entry_exit_grade(pct: Optional[float]) -> str:
    """Elder: 0-80% = D/C/B (progresivo), 80-100% = A. Usamos percentiles fijos."""
    if pct is None:
        return "?"
    if pct >= 80:
        return "A"
    if pct >= 60:
        return "B"
    if pct >= 40:
        return "C"
    return "D"


def _letter_from_trade_grade(pct: Optional[float]) -> str:
    """Elder: |trade_grade| >= 33% = A. Negativos siempre son F (loser)."""
    if pct is None:
        return "?"
    if pct < 0:
        return "F"
    if pct >= 33:
        return "A"
    if pct >= 22:
        return "B"
    if pct >= 10:
        return "C"
    return "D"


def _trade_grading_summary(trades: list[Trade]) -> dict:
    """
    Aggregate grading metrics across closed trades:
      - averages of entry/exit/trade grades
      - A/B/C/D/F distribution
      - correlation with net pnl (A-grades should out-earn D-grades)
    """
    entry_pcts = [t.entry_grade for t in trades if t.entry_grade is not None]
    exit_pcts = [t.exit_grade for t in trades if t.exit_grade is not None]
    trade_pcts = [t.trade_grade for t in trades if t.trade_grade is not None]

    def _avg(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 2) if xs else None

    def _dist(letter_fn, pcts: list[float]) -> dict[str, int]:
        buckets = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0, "?": 0}
        for p in pcts:
            buckets[letter_fn(p)] += 1
        return buckets

    # Net PnL per trade-grade letter (are A-graded trades actually profitable?)
    pnl_by_letter: dict[str, list[float]] = {"A": [], "B": [], "C": [], "D": [], "F": [], "?": []}
    for t in trades:
        letter = _letter_from_trade_grade(t.trade_grade)
        pnl_by_letter[letter].append(t.net)

    return {
        "n_graded": {
            "entry": len(entry_pcts),
            "exit": len(exit_pcts),
            "trade": len(trade_pcts),
        },
        "avg": {
            "entry_grade_pct": _avg(entry_pcts),
            "exit_grade_pct": _avg(exit_pcts),
            "trade_grade_pct": _avg(trade_pcts),
        },
        "letter_distribution": {
            "entry": _dist(_letter_from_entry_exit_grade, entry_pcts),
            "exit": _dist(_letter_from_entry_exit_grade, exit_pcts),
            "trade": _dist(_letter_from_trade_grade, trade_pcts),
        },
        "pnl_by_trade_grade_letter": {
            letter: {
                "n": len(xs),
                "net": round(sum(xs), 2),
                "avg": round(sum(xs) / len(xs), 2) if xs else 0.0,
            }
            for letter, xs in pnl_by_letter.items()
        },
    }


def _signal_funnel(signal_events: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "NONE": 0, "BUY": 0, "SELL": 0})
    for e in signal_events:
        sym = e.get("symbol", "?")
        dec = e.get("decision", "NONE")
        out[sym]["total"] += 1
        out[sym][dec] = out[sym].get(dec, 0) + 1
    # normalize keys we care about exist
    for sym, d in out.items():
        for k in ("NONE", "BUY", "SELL"):
            d.setdefault(k, 0)
    return dict(out)


def _equity_curve(trades: list[Trade]) -> list[dict]:
    ordered = sorted(trades, key=lambda t: t.closed_at or datetime.min)
    eq = 0.0
    out = []
    for t in ordered:
        eq += t.net
        out.append(
            {
                "ts": (t.closed_at or t.opened_at).isoformat(timespec="seconds") if (t.closed_at or t.opened_at) else None,
                "symbol": t.symbol,
                "side": t.side,
                "net": round(t.net, 2),
                "equity": round(eq, 2),
            }
        )
    return out


def _bucket_to_dict(b: Bucket) -> dict:
    pf = b.profit_factor
    return {
        "n": b.n,
        "wins": b.wins,
        "losses": b.losses,
        "win_rate_pct": round(b.win_rate, 2),
        "net": round(b.net, 2),
        "expectancy": round(b.expectancy, 4),
        "profit_factor": None if pf == float("inf") else round(pf, 3),
        "avg_win": round(b.avg_win, 2),
        "avg_loss": round(b.avg_loss, 2),
    }


def _parse_money(s: str) -> float:
    """Parse a formatted '$+1,234.56' back to float for sorting."""
    try:
        return float(s.replace("$", "").replace(",", "").replace("+", ""))
    except Exception:
        return 0.0


# --- auto-findings ----------------------------------------------------------


def _auto_findings(summary: dict, trades: list[Trade], top_n: int) -> list[dict]:
    """
    Heuristic rules to flag actionable patterns. Only surfaces issues when
    sample size is big enough to be meaningful.
    """
    findings: list[dict] = []
    overall_n = summary["overall"]["n_trades"]
    if overall_n < 5:
        return findings

    MIN_N = 5  # per-bucket minimum to make a claim

    # 1) Low win-rate symbols
    for sym, d in summary["by_symbol"].items():
        if d["n"] >= MIN_N and d["win_rate_pct"] < 35 and d["net"] < 0:
            findings.append(
                {
                    "severity": "high",
                    "message": f"{sym}: win rate {d['win_rate_pct']:.1f}% on {d['n']} trades, net {_fmt_money(d['net'])}. Considerá subir ADX_MIN para este símbolo o blacklistearlo temporalmente.",
                }
            )

    # 2) Bad hours (high volume, negative net)
    for h, d in summary["by_hour"].items():
        try:
            h_int = int(h)
        except Exception:
            continue
        if d["n"] >= MIN_N and d["net"] < 0 and d["win_rate_pct"] < 40:
            findings.append(
                {
                    "severity": "medium",
                    "message": f"Hora {h_int:02d}h: {d['n']} trades con net {_fmt_money(d['net'])} (winrate {d['win_rate_pct']:.1f}%). Evaluá bloquear esa franja con BOT_NEWS_BLOCK_HOURS.",
                }
            )

    # 3) Session-level loss (Asia / Quiet expected to be bad; Kathy Lien)
    for sess, d in summary["by_session"].items():
        if d["n"] >= MIN_N and d["net"] < 0 and sess in ("ASIA", "QUIET"):
            findings.append(
                {
                    "severity": "high",
                    "message": f"Sesión {sess}: {d['n']} trades con net {_fmt_money(d['net'])}. Kathy Lien advierte de fake-outs en Asia; considerá session-filter.",
                }
            )

    # 4) ADX bucket performance
    for k, d in summary["by_adx_bucket"].items():
        if d["n"] >= MIN_N and d["net"] < 0 and k in ("range (<15)", "weak (15-20)"):
            findings.append(
                {
                    "severity": "medium",
                    "message": f"ADX {k}: {d['n']} trades con net {_fmt_money(d['net'])}. Subir ADX_MIN para filtrar rango lateral.",
                }
            )

    # 5) Profit factor alert
    pf = summary["overall"]["profit_factor"]
    if pf is not None and pf < 1.0 and overall_n >= 10:
        findings.append(
            {
                "severity": "high",
                "message": f"Profit factor global {pf:.2f} < 1.0 sobre {overall_n} trades: el sistema está perdiendo plata. Pausá apertura de trades nuevos y revisá filtros.",
            }
        )

    # 6) Drawdown alert
    dd_pct = summary["overall"]["max_drawdown_pct_peak"]
    if dd_pct > 30 and overall_n >= 10:
        findings.append(
            {
                "severity": "medium",
                "message": f"Drawdown realizado {dd_pct:.1f}% del peak. Considerá bajar risk_per_trade_pct o max_open_trades.",
            }
        )

    # 7) Close-reason breakdown
    by_reason = summary.get("by_close_reason", {})
    if by_reason:
        sl_d = by_reason.get("Stop loss") or by_reason.get("Stop Loss") or by_reason.get("stop loss")
        tp_d = by_reason.get("Take profit") or by_reason.get("Take Profit") or by_reason.get("take profit")
        if sl_d and tp_d and sl_d["n"] + tp_d["n"] >= MIN_N:
            ratio = sl_d["n"] / max(1, tp_d["n"])
            if ratio > 3:
                findings.append(
                    {
                        "severity": "high",
                        "message": f"Cierres: {sl_d['n']} SL vs {tp_d['n']} TP (ratio {ratio:.1f}x). TP demasiado lejos o SL demasiado cerca; revisá SL/TP en points.",
                    }
                )

    # 8) Filter utility hints
    ec = summary.get("event_counts", {})
    sigs = ec.get("SIGNAL", 0)
    skips = ec.get("BAR_EXHAUSTION_SKIP", 0)
    if sigs > 50 and skips == 0:
        findings.append(
            {
                "severity": "low",
                "message": "Anti-chase nunca se activó: o el mercado es tranquilo, o BOT_ANTI_CHASE_ATR_MULT es muy alto. Considerá bajar a 1.7-1.8.",
            }
        )
    fe = ec.get("FILTER_EXIT", 0)
    if fe > 0 and overall_n >= 5:
        findings.append(
            {
                "severity": "info",
                "message": f"Filter-exit se activó {fe} vez(es). Revisá en detalle el P&L de esos trades vs los que hubieran llegado a SL/TP.",
            }
        )

    # 8b) Correlation cap activity
    cskips = ec.get("CORRELATION_SKIP", 0)
    if cskips > 0:
        findings.append(
            {
                "severity": "info",
                "message": f"Correlation cap bloqueó {cskips} entrada(s). Correcto: evita clusterizar riesgo por grupo. Si son demasiadas, revisá BOT_MAX_POSITIONS_PER_GROUP.",
            }
        )

    # 8c) Trade grading: chequeo de calidad de ejecución
    tg = summary.get("trade_grading", {})
    tg_avg = tg.get("avg", {})
    if tg.get("n_graded", {}).get("entry", 0) >= MIN_N:
        eavg = tg_avg.get("entry_grade_pct")
        if eavg is not None and eavg < 40:
            findings.append(
                {
                    "severity": "medium",
                    "message": f"Entry grade promedio {eavg:.1f}% (<40%): estamos entrando cerca del techo de la vela en longs / piso en shorts. Señal posible: confirmación tardía, filtro M5 o pendientes podrían mejorar el fill.",
                }
            )
    if tg.get("n_graded", {}).get("exit", 0) >= MIN_N:
        xavg = tg_avg.get("exit_grade_pct")
        if xavg is not None and xavg < 40:
            findings.append(
                {
                    "severity": "medium",
                    "message": f"Exit grade promedio {xavg:.1f}% (<40%): estamos cerrando lejos del extremo favorable de la vela de salida. TP o trailing podrían estar devolviendo demasiada ganancia.",
                }
            )
    if tg.get("n_graded", {}).get("trade", 0) >= MIN_N:
        tavg = tg_avg.get("trade_grade_pct")
        if tavg is not None and tavg < 10:
            findings.append(
                {
                    "severity": "high",
                    "message": f"Trade grade promedio {tavg:.1f}% (<10% del canal ATR*4): estamos tomando una fracción muy chica del movimiento disponible. Revisar TP/trailing (dejar correr ganadoras) o cortar antes perdedoras.",
                }
            )
        # A-graded trades should clearly out-earn D-graded trades
        pnl_by = tg.get("pnl_by_trade_grade_letter", {})
        a = pnl_by.get("A", {}) or {}
        f = pnl_by.get("F", {}) or {}
        if a.get("n", 0) >= 3 and f.get("n", 0) >= 3 and a.get("avg", 0.0) < abs(f.get("avg", 0.0)):
            findings.append(
                {
                    "severity": "medium",
                    "message": f"Grading: trades 'A' (avg {_fmt_money(a.get('avg', 0.0))}) no compensan a los 'F' (avg {_fmt_money(f.get('avg', 0.0))}). Las ganadoras son más chicas que las perdedoras — hay que dejarlas correr más.",
                }
            )

    # 9) Top 3 worst trades (information)
    worst = sorted(trades, key=lambda t: t.net)[:3]
    for t in worst:
        if t.net < 0:
            when = t.opened_at.strftime("%Y-%m-%d %H:%M") if t.opened_at else "?"
            findings.append(
                {
                    "severity": "info",
                    "message": f"Worst trade: {t.symbol} {t.side} abrió {when}, net {_fmt_money(t.net)}, cerró por '{t.reason_label or t.reason_code}'.",
                }
            )

    return findings[:top_n]


# --- CLI --------------------------------------------------------------------


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    raise argparse.ArgumentTypeError(f"Unrecognized datetime: {s}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze trades.log and print a KPI report.")
    parser.add_argument("--log", default="trades.log", help="Path to trades.log (default: ./trades.log); ignorado si --all-logs")
    parser.add_argument(
        "--all-logs",
        action="store_true",
        help="Fusionar trades.log + logs/trades_*.log junto al script (ordenado por tiempo).",
    )
    parser.add_argument("--since", type=_parse_dt, default=None, help="Only include events on/after this datetime (YYYY-MM-DD or full ISO)")
    parser.add_argument("--until", type=_parse_dt, default=None, help="Only include events on/before this datetime")
    parser.add_argument("--last-days", type=int, default=None, help="Shortcut: include only last N days")
    parser.add_argument(
        "--exclude-crypto",
        action="store_true",
        help=f"Excluir eventos con symbol en {sorted(_DEFAULT_CRYPTO)} (FX/metales solamente).",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="If set, export full summary as JSON to this path")
    parser.add_argument("--top-n", type=int, default=20, help="Max findings to list (default 20)")
    args = parser.parse_args(argv)

    since = args.since
    until = args.until
    if args.last_days is not None:
        since = datetime.now() - timedelta(days=int(args.last_days))

    try:
        if args.all_logs:
            paths = _default_trade_log_paths()
            if not paths:
                print("[error] --all-logs: no se encontró trades.log ni logs/trades_*.log", file=sys.stderr)
                return 2
            events = merge_trade_log_events(paths, since=since, until=until)
            log_label = f"{len(paths)} archivos (trades.log + logs/trades_*.log)"
        else:
            events = list(iter_events(args.log, since=since, until=until))
            log_label = args.log
    except FileNotFoundError:
        print(f"[error] log file not found: {args.log}", file=sys.stderr)
        return 2

    events = _exclude_crypto_events(events, args.exclude_crypto)

    if not events:
        print(f"[warn] sin eventos estructurados en {log_label}" + (f" desde {since}" if since else ""))
        return 0

    enrich_trade_closes_from_logged_opens(events)

    trades = build_trades(events)

    # Header
    dt_min = min((e["_dt"] for e in events), default=None)
    dt_max = max((e["_dt"] for e in events), default=None)
    print()
    print(f"Log: {log_label}" + ("  |  exclude-crypto" if args.exclude_crypto else ""))
    print(f"Events: {len(events)}  |  Closed trades: {len(trades)}")
    if dt_min and dt_max:
        print(f"Range: {dt_min.isoformat(timespec='seconds')} .. {dt_max.isoformat(timespec='seconds')}")

    summary = render_summary(events, trades, top_n=args.top_n)

    if args.json_path:
        clean = _json_clean(summary)
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nSummary exported to {args.json_path}")

    return 0


def _json_clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_clean(x) for x in obj]
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if obj == float("inf"):
        return None
    return obj


if __name__ == "__main__":
    sys.exit(main())
