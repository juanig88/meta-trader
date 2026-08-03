"""
Extended backtest statistics, bootstrap confidence intervals, walk-forward splits,
and optional benchmark (buy & hold) via yfinance.

Designed to consume closed trades with .pnl_money, .entry_idx, .exit_idx (backtest.Trade).
Interpretation: bootstrap assumes trade outcomes are (roughly) i.i.d.; path-dependent sizing
makes this approximate — still useful as a sanity check vs. a single realized run.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional


def bars_per_year_m15(*, trading_days_per_year: float) -> float:
    """M15 bars ≈ 96 per calendar trading day (24h forex) × trading days; crypto often 365."""
    return float(trading_days_per_year) * 96.0


def pnls_and_holding_bars(trades: list[Any]) -> tuple[list[float], list[int]]:
    pnls: list[float] = []
    holds: list[int] = []
    for t in trades:
        pnl, hb = _pnl_from_trade(t)
        pnls.append(pnl)
        if hb is not None:
            holds.append(hb)
    return pnls, holds


def _pnl_from_trade(t: Any) -> tuple[float, Optional[int]]:
    pnl_box = t.pnl_money
    pnl = float(pnl_box) if pnl_box is not None else 0.0
    e = getattr(t, "exit_idx", None)
    eb = getattr(t, "entry_idx", None)
    if e is not None and eb is not None:
        try:
            hb = max(0, int(e) - int(eb))
        except (TypeError, ValueError):
            hb = None
    else:
        hb = None
    return pnl, hb


def gross_profit_loss(pnls: list[float]) -> tuple[float, float]:
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    return gp, gl


def profit_factor(pnls: list[float]) -> Optional[float]:
    gp, gl = gross_profit_loss(pnls)
    if gl > 0:
        return gp / gl
    if gp > 0:
        return float("inf")
    return None


def _max_drawdown_along_equity(equity: list[float]) -> float:
    """Largest peak-to-trough drop (negative number or 0)."""
    if not equity:
        return 0.0
    peak = equity[0]
    m = 0.0
    for eq in equity:
        peak = max(peak, eq)
        m = min(m, eq - peak)
    return float(m)


def equity_path(initial: float, pnls: Iterable[float]) -> list[float]:
    out = [float(initial)]
    eq = float(initial)
    for p in pnls:
        eq += float(p)
        out.append(eq)
    return out


def monte_carlo_trade_order_drawdown(
    pnls: list[float],
    *,
    initial_balance: float,
    n_sims: int,
    seed: Optional[int],
) -> dict[str, Any]:
    """
    Shuffle the sequence of trade PnL (same multiset) and rebuild a simple equity curve.
    Summarizes how "ordering risk" affects drawdown when lot sizes are treated as fixed ex-post.
    """
    if not pnls or n_sims <= 0:
        return {"n_sims": n_sims, "skipped": True}
    rng = random.Random(int(seed)) if seed is not None else random.Random()
    obs = _max_drawdown_along_equity(equity_path(initial_balance, pnls))
    sim_dds: list[float] = []
    as_bad = 0
    perm = pnls[:]
    for _ in range(n_sims):
        rng.shuffle(perm)
        dd = _max_drawdown_along_equity(equity_path(initial_balance, perm))
        sim_dds.append(dd)
        if dd <= obs:
            as_bad += 1

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return float("nan")
        ys = sorted(xs)
        k = max(0, min(len(ys) - 1, int(q * (len(ys) - 1))))
        return ys[k]

    return {
        "n_sims": n_sims,
        "note": "Uses realized $PnL per trade; ignores path-dependent lot sizing.",
        "observed_max_drawdown_money": round(obs, 4),
        "shuffle_median_dd_money": round(pct(sim_dds, 0.5), 4),
        "shuffle_p05_dd_money": round(pct(sim_dds, 0.05), 4),
        "shuffle_p95_dd_money": round(pct(sim_dds, 0.95), 4),
        "fraction_dd_as_bad_or_worse_than_observed": round(as_bad / n_sims, 4),
    }


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


def _sharpe_trade_simple_returns(pnls: list[float], initial_balance: float) -> Optional[float]:
    if len(pnls) < 2 or initial_balance <= 0:
        return None
    eq = float(initial_balance)
    rets: list[float] = []
    for p in pnls:
        if eq <= 0:
            return None
        rets.append(p / eq)
        eq += p
    m = sum(rets) / len(rets)
    v = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    sd = math.sqrt(v) if v > 0 else 0.0
    if sd <= 0:
        return None
    return m / sd


def annualized_sharpe_from_trades(
    sharpe_per_trade: float,
    *,
    n_trades: int,
    bars_per_year: float,
    avg_holding_bars: float,
) -> Optional[float]:
    """Rough scaling: treat trades as spaced by average holding in M15 bars."""
    if sharpe_per_trade is None or avg_holding_bars <= 0 or n_trades < 2:
        return None
    trades_per_year = bars_per_year / avg_holding_bars
    if trades_per_year <= 0:
        return None
    return sharpe_per_trade * math.sqrt(trades_per_year)


def extended_trade_block(
    trades: list[Any],
    *,
    initial_balance: float,
    trading_days_per_year: float,
) -> dict[str, Any]:
    pnls, holds = pnls_and_holding_bars(trades)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gp, gl = gross_profit_loss(pnls)
    pf = profit_factor(pnls)
    streaks = win_loss_streaks(pnls)
    avg_hold = (sum(holds) / len(holds)) if holds else 0.0
    bars_py = bars_per_year_m15(trading_days_per_year=trading_days_per_year)
    sharpe_pt = _sharpe_trade_simple_returns(pnls, initial_balance)
    sharpe_ann = None
    if sharpe_pt is not None and avg_hold > 0:
        sharpe_ann = annualized_sharpe_from_trades(
            sharpe_pt,
            n_trades=n,
            bars_per_year=bars_py,
            avg_holding_bars=avg_hold,
        )
    return {
        "n_trades": n,
        "gross_profit": round(gp, 4),
        "gross_loss": round(gl, 4),
        "profit_factor": None if pf is None or pf == float("inf") else round(float(pf), 4),
        "profit_factor_inf": bool(pf == float("inf")),
        "expectancy": round(sum(pnls) / n, 6) if n else 0.0,
        "avg_holding_bars_m15": round(avg_hold, 2),
        "max_win_streak": streaks["max_win_streak"],
        "max_loss_streak": streaks["max_loss_streak"],
        "winrate_trades": round(wins / n * 100.0, 2) if n else 0.0,
        "sharpe_per_trade_return_simple": None if sharpe_pt is None else round(sharpe_pt, 4),
        "sharpe_annualized_approx": None if sharpe_ann is None else round(sharpe_ann, 4),
        "trading_days_per_year_used": trading_days_per_year,
        "bars_per_year_m15_assumption": round(bars_py, 2),
    }


def bootstrap_trade_pnl_stats(
    pnls: list[float],
    *,
    initial_balance: float,
    n_boot: int,
    seed: Optional[int],
) -> dict[str, Any]:
    if seed is not None:
        rng = random.Random(int(seed))
    else:
        rng = random.Random()
    n = len(pnls)
    if n == 0 or n_boot <= 0:
        return {"n_boot": n_boot, "skipped": True}
    totals: list[float] = []
    sharpes: list[float] = []
    for _ in range(n_boot):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        totals.append(sum(sample))
        s = _sharpe_trade_simple_returns(sample, initial_balance)
        if s is not None:
            sharpes.append(s)

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return float("nan")
        ys = sorted(xs)
        k = max(0, min(len(ys) - 1, int(q * (len(ys) - 1))))
        return ys[k]

    out: dict[str, Any] = {
        "n_boot": n_boot,
        "total_pnl_money": {
            "mean": round(sum(totals) / len(totals), 4),
            "p05": round(pct(totals, 0.05), 4),
            "p95": round(pct(totals, 0.95), 4),
        },
    }
    if sharpes:
        out["sharpe_per_trade_simple"] = {
            "mean": round(sum(sharpes) / len(sharpes), 4),
            "p05": round(pct(sharpes, 0.05), 4),
            "p95": round(pct(sharpes, 0.95), 4),
        }
    return out


def walk_forward_trade_splits(pnls: list[float], *, n_splits: int) -> list[dict[str, Any]]:
    if n_splits < 2 or not pnls:
        return []
    n = len(pnls)
    base = n // n_splits
    out: list[dict[str, Any]] = []
    i0 = 0
    for k in range(n_splits):
        extra = 1 if k < (n % n_splits) else 0
        size = base + extra
        chunk = pnls[i0 : i0 + size]
        i0 += size
        if not chunk:
            continue
        wins = sum(1 for p in chunk if p > 0)
        pf = profit_factor(chunk)
        out.append(
            {
                "split": k + 1,
                "n": len(chunk),
                "sum_pnl_money": round(sum(chunk), 4),
                "winrate_pct": round(wins / len(chunk) * 100.0, 2),
                "profit_factor": None if pf is None or pf == float("inf") else round(float(pf), 4),
            }
        )
    return out


def default_benchmark_ticker(symbol: str) -> Optional[str]:
    u = symbol.upper().replace(".", "")
    if "XAU" in u or u == "GOLD":
        return "GC=F"
    if "BTC" in u:
        return "BTC-USD"
    if "ETH" in u:
        return "ETH-USD"
    return None


def benchmark_buy_hold_return(
    ticker: str,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return {"ok": False, "error": "yfinance not installed", "ticker": ticker}

    if start >= end:
        return {"ok": False, "error": "invalid range", "ticker": ticker}
    try:
        df = yf.download(
            ticker,
            start=start.date(),
            end=(end + timedelta(days=1)).date(),
            progress=False,
            auto_adjust=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "ticker": ticker}
    if df is None or getattr(df, "empty", True):
        return {"ok": False, "error": "no price rows", "ticker": ticker}
    try:
        col = "Adj Close" if "Adj Close" in df.columns else "Close"
        series = df[col]
        if hasattr(series, "iloc") is False:
            return {"ok": False, "error": "unexpected frame shape", "ticker": ticker}
        if getattr(series, "ndim", 1) == 2:
            series = series.iloc[:, 0]
        first = float(series.iloc[0])
        last = float(series.iloc[-1])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "ticker": ticker}
    if first <= 0:
        return {"ok": False, "error": "non-positive first close", "ticker": ticker}
    total_return = (last / first) - 1.0
    return {
        "ok": True,
        "ticker": ticker,
        "total_return": round(total_return, 6),
        "first_close": round(first, 6),
        "last_close": round(last, 6),
        "n_rows": int(len(series)),
    }