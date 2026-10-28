"""Pre-registered test: does relative strength across cryptos survive XBTFX costs?

Read-only research. Never sends orders, never calls mt5.shutdown(), never asks
the terminal for more than the cached 5000 M1 bars.

Design fixed BEFORE looking at results (2026-10-05):

- Question: which coin beats which other coin. Not the failed question
  (does this coin go up). The five voting modules are not used.
- Universe: XBTFX CFDs with a Binance spot USDT series that already exists on
  2024-10-07 (listed for the whole sample; a later listing stays out). BTCUSD!
  stays out because its swap (~0.46 USD/day) makes a week-long leg untradable.
  IMX and VEC stay in: their rank_tail skip belongs to the 4/5 scalp, not here.
- Rule, one specification only: every Monday 00:00 UTC, rank by the previous 7
  days' return. Long the 5 strongest, short the 5 weakest, equal weight. Hold
  to the next Monday. No stop. A week with fewer than 20 names is skipped.
  Ties break by symbol name.
- Prices: Binance 1m open at the Monday (bar within 2 hours, or the name is
  absent that week). Entry is the open, so the ranking close is the prior Monday.
- Costs: one XBTFX spread crossing per leg (today's median, applied to the
  whole sample) plus 7 days of that leg's own swap, long or short. The swap
  figure from the terminal is a signed cashflow (negative means the leg pays)
  and is added as-is. Swap mode must be points. rollover3days=7 is ambiguous,
  so Wednesday is not tripled.
- Split: a train week must be closed before 2026-02-01. The week that straddles
  the cut is an embargo and is dropped. The holdout is not printed without
  --reveal-holdout.
- Stats: one number per week (the whole book is one bet). Bootstrap over weeks,
  2000 draws, seed 20261005.
- Decision: advances only if the train book net is > 0 with p < 0.01 AND the
  holdout has the same sign. Otherwise it is discarded.
- Failure scenario: in a crash the coins move together, the short does not
  hedge, and both legs still pay the spread.

    py analyze_cross_section.py
    py analyze_cross_section.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from fetch_binance_1m import PAIRS, OUT_DIR

EXCLUDE = frozenset({"BTCUSD!"})
MIN_NAMES = 20
N_LEGS = 5
HOLD_DAYS = 7
TOLERANCE_SEC = 2 * 3600
SPLIT = "2026-02-01"
FIRST_MONDAY = "2024-10-07"
BOOTSTRAP = 2000
SEED = 20261005
SWAP_MODE_POINTS = 1


def mondays(start: str, end: str) -> np.ndarray:
    d = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    last = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    out = []
    while d <= last:
        out.append(int(d.timestamp()))
        d += timedelta(days=7)
    return np.array(out, dtype=np.int64)


def monday_opens(time: np.ndarray, open_: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Open of the bar at or just after each target. NaN if it falls more than 2h late."""
    idx = np.searchsorted(time, targets, side="left")
    out = np.full(len(targets), np.nan)
    ok = idx < len(time)
    late = np.zeros(len(targets), dtype=bool)
    late[ok] = (time[idx[ok]] - targets[ok]) > TOLERANCE_SEC
    take = ok & ~late
    out[take] = open_[idx[take]]
    return out


def week_book(past: np.ndarray, fwd: np.ndarray, spread: np.ndarray,
              swap_long: np.ndarray, swap_short: np.ndarray, names: list[str]) -> dict | None:
    usable = np.isfinite(past) & np.isfinite(fwd)
    idx = np.flatnonzero(usable)
    if idx.size < MIN_NAMES:
        return None
    order = sorted(idx.tolist(), key=lambda i: (-float(past[i]), names[i]))
    longs, shorts = order[:N_LEGS], order[-N_LEGS:]
    if set(longs) & set(shorts):
        return None
    gross = float(fwd[longs].mean() - fwd[shorts].mean())
    # swap_* is already a signed cashflow (negative means the leg pays).
    long_net = fwd[longs] - spread[longs] + swap_long[longs] * HOLD_DAYS
    short_net = -fwd[shorts] - spread[shorts] + swap_short[shorts] * HOLD_DAYS
    return {
        "gross": gross,
        "net": float(long_net.mean() + short_net.mean()),
        "n_names": int(idx.size),
        "longs": [names[i] for i in longs],
        "shorts": [names[i] for i in shorts],
    }


def week_bootstrap(vals: np.ndarray, rng: np.random.Generator) -> dict:
    if vals.size == 0:
        return {"n_weeks": 0}
    mean = float(vals.mean())
    pick = rng.integers(0, vals.size, size=(BOOTSTRAP, vals.size))
    means = vals[pick].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    side = float(np.mean(means <= 0.0)) if mean > 0 else float(np.mean(means >= 0.0))
    return {
        "n_weeks": int(vals.size),
        "mean_bps": round(1e4 * mean, 2),
        "ci95_bps": [round(1e4 * float(lo), 2), round(1e4 * float(hi), 2)],
        "p": round(min(1.0, 2.0 * side), 4),
        "positive_weeks_pct": round(100.0 * float((vals > 0).mean()), 1),
    }


def load_opens(pair: str, targets: np.ndarray) -> np.ndarray | None:
    path = OUT_DIR / f"{pair}_1m.npz"
    if not path.exists():
        return None
    z = np.load(path)
    t = z["time"].astype(np.int64)
    if t.min() > targets[0] + TOLERANCE_SEC:
        return None  # not listed at the start of the sample
    return monday_opens(t, z["open"].astype(np.float64), targets)


def xbtfx_costs(symbol: str) -> dict | None:
    """Today's median spread and the daily swap, both as a fraction of price."""
    import MetaTrader5 as mt5

    info = mt5.symbol_info(symbol)
    if info is None or int(info.swap_mode) != SWAP_MODE_POINTS:
        return None
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 5000)
    if rates is None or len(rates) < 100:
        return None
    px = float(np.median(rates["close"]))
    point = float(info.point)
    if px <= 0 or point <= 0:
        return None
    return {
        "spread": float(np.median(rates["spread"])) * point / px,
        "swap_long": float(info.swap_long) * point / px,
        "swap_short": float(info.swap_short) * point / px,
    }


def self_test() -> None:
    names = [f"C{i:02d}" for i in range(20)]
    past = np.arange(20, dtype=float)
    fwd = past / 10.0
    zero = np.zeros(20)
    up = week_book(past, fwd, zero, zero, zero, names)
    down = week_book(past, -fwd, zero, zero, zero, names)
    assert up is not None and up["longs"] == ["C19", "C18", "C17", "C16", "C15"]
    assert up["shorts"] == ["C04", "C03", "C02", "C01", "C00"]
    assert up["gross"] > 0 and down is not None and down["gross"] < 0
    thin = week_book(past[:10], fwd[:10], zero[:10], zero[:10], zero[:10], names[:10])
    assert thin is None
    costly = week_book(past, fwd, np.full(20, 1.0), zero, zero, names)
    assert costly is not None and costly["net"] < 0 < costly["gross"]
    print("self-test ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--reveal-holdout", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return 0

    import MetaTrader5 as mt5

    targets = mondays(FIRST_MONDAY, "2026-09-28")
    split_t = int(datetime.fromisoformat(SPLIT).replace(tzinfo=timezone.utc).timestamp())
    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")

    names, opens, spread, swap_l, swap_s = [], [], [], [], []
    left_out: dict[str, str] = {}
    for symbol, pair in sorted(PAIRS.items()):
        if symbol in EXCLUDE:
            left_out[symbol] = "btc_swap"
            continue
        mt5.symbol_select(symbol, True)
        cost = xbtfx_costs(symbol)
        grid = load_opens(pair, targets)
        if cost is None:
            left_out[symbol] = "sin_costo_xbtfx"
            continue
        if grid is None:
            left_out[symbol] = "sin_historia_desde_oct2024"
            continue
        names.append(symbol)
        opens.append(grid)
        spread.append(cost["spread"])
        swap_l.append(cost["swap_long"])
        swap_s.append(cost["swap_short"])
    if len(names) < MIN_NAMES:
        raise SystemExit(f"universo {len(names)} < {MIN_NAMES}: {left_out}")

    O = np.vstack(opens)
    spr = np.array(spread)
    swl, sws = np.array(swap_l), np.array(swap_s)
    train, embargo, hold = [], [], []
    for i in range(1, len(targets) - 1):
        past = O[:, i] / O[:, i - 1] - 1.0
        fwd = O[:, i + 1] / O[:, i] - 1.0
        book = week_book(past, fwd, spr, swl, sws, names)
        if book is None:
            continue
        entry, exit_ = int(targets[i]), int(targets[i + 1])
        book["entry"] = datetime.fromtimestamp(entry, timezone.utc).strftime("%Y-%m-%d")
        if exit_ < split_t:
            train.append(book)
        elif entry >= split_t:
            hold.append(book)
        else:
            embargo.append(book["entry"])

    rng = np.random.default_rng(SEED)
    g = np.array([w["gross"] for w in train])
    n = np.array([w["net"] for w in train])
    out = {
        "design": "pre-registrado; holdout oculto salvo --reveal-holdout",
        "rule": "largo 5 mejores y corto 5 peores por retorno de 7 dias, tenencia 7 dias",
        "split_utc": SPLIT, "embargo_weeks": embargo,
        "universe": names, "left_out": left_out,
        "median_spread_bps": round(1e4 * float(np.median(spr)), 2),
        "train": {
            "gross": week_bootstrap(g, rng),
            "net": week_bootstrap(n, rng),
            "mean_cost_bps": round(1e4 * float((g - n).mean()), 2) if n.size else None,
        },
        "holdout_weeks_reserved": len(hold),
        "decision_rule": "train neto > 0 con p < 0.01 por semana, y el holdout confirma el signo",
    }
    if args.reveal_holdout:
        out["holdout"] = {
            "gross": week_bootstrap(np.array([w["gross"] for w in hold]), rng),
            "net": week_bootstrap(np.array([w["net"] for w in hold]), rng),
        }
    passed = bool(n.size) and float(n.mean()) > 0 and out["train"]["net"]["p"] < 0.01
    out["train_passes"] = passed
    Path("analyze_cross_section_out.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    show = {k: out[k] for k in ("universe", "left_out", "median_spread_bps", "train",
                                "holdout_weeks_reserved", "embargo_weeks", "train_passes")}
    if "holdout" in out:
        show["holdout"] = out["holdout"]
    print(json.dumps(show, indent=2, ensure_ascii=False))
    print(f"universo {len(names)}, train {len(train)} semanas, "
          f"embargo {len(embargo)}, holdout reservado {len(hold)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
