"""Information audit: does any of the 5 voting modules predict direction at all?

Read-only research. Never sends orders, never calls mt5.shutdown().

This is a diagnostic, not a strategy search. It answers one question: do the
inputs the engine already computes carry *any* directional information before
costs? If every module is flat at every horizon, no recombination of them can
work and we stop looking here.

Design fixed BEFORE looking at results:

- Data: the same Binance spot 1m, 24 months, 12 symbols as the 2y horizon test
  (BTC is a peer only). Train period only; the 2026-02 -> 09 holdout stays
  reserved and is never read by this script.
- Votes: live ``cast_votes`` on the same closed windows the engine uses.
  No gates, no sizing, no spread filter -- we want raw information.
- Outcome: signed mid-price return, entry at the open of bar i+1 and exit at the
  open of bar i+1+h. BUY keeps the sign, SELL flips it. No costs and no stop,
  so this is an upper bound on what the module knows.
- Rows: the 5 modules plus the raw 4/5 tally, at 5 / 15 / 60 / 240 / 1440 min.
- Stats: bootstrap over calendar days (crypto on the same day is one bet).
- Multiple testing: 6 rows x 5 horizons = 30 tests, so the bar is Bonferroni on
  0.01, i.e. p < 0.00033. Anything above that is noise.

    py analyze_module_information.py --step 15
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

from analyze_horizon_edge_2y import (
    H4_N, M1_N, M5_N, M15_N, PAIRS, PEER_ONLY, PEERS, load_m1, resample,
)
from analyze_usdjpy_rotation_replay import _closed_window, _ohlcv
from trading.domain.ensemble import (
    MIN_AGREE, SIDE_BUY, SIDE_NONE, SIDE_SELL, VOTER_ORDER, cast_votes,
)

HORIZONS = (5, 15, 60, 240, 1440)
BOOTSTRAP = 2000
SEED = 20261005
ALPHA = 0.01
N_TESTS = (len(VOTER_ORDER) + 1) * len(HORIZONS)
BONFERRONI = ALPHA / N_TESTS


def day_boot(vals: np.ndarray, days: np.ndarray, rng: np.random.Generator) -> dict:
    """Mean with a CI from resampling calendar days, weighting days by their size."""
    if vals.size == 0:
        return {"n": 0}
    order = np.argsort(days, kind="stable")
    v, d = vals[order], days[order]
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    sums = np.add.reduceat(v, starts)
    counts = np.diff(np.r_[starts, v.size])
    mean = float(v.mean())
    pick = rng.integers(0, sums.size, size=(BOOTSTRAP, sums.size))
    means = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    side = float(np.mean(means <= 0.0)) if mean > 0 else float(np.mean(means >= 0.0))
    p = min(1.0, 2.0 * side)
    return {
        "n": int(v.size), "n_days": int(sums.size),
        "mean_bps": round(1e4 * mean, 3),
        "ci95_bps": [round(1e4 * float(lo), 3), round(1e4 * float(hi), 3)],
        "p": round(p, 5), "hit_rate_pct": round(100.0 * float((v > 0).mean()), 2),
        "pasa": bool(p < BONFERRONI and mean > 0),
    }


def collect(symbol: str, pair: str, peers: dict[str, np.ndarray], step: int,
            split_t: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Returns (votes[n,6] in {-1,0,1}, forward returns[n,len(HORIZONS)], day[n])."""
    m1 = load_m1(pair)
    if m1 is None:
        return None
    m5, m15, h4 = (resample(m1, r) for r in ("5min", "15min", "4h"))
    ends = {"m5": m5["time"] + 300, "m15": m15["time"] + 900, "h4": h4["time"] + 14400}
    peer_ends = {p: r["time"] + 300 for p, r in peers.items()}
    t1, op = m1["time"], m1["open"]
    n = len(m1)
    max_h = max(HORIZONS)
    sign = {SIDE_BUY: 1, SIDE_SELL: -1, SIDE_NONE: 0}

    votes, fwd, days = [], [], []
    for i in range(M1_N, n - max_h - 2, max(1, step)):
        if t1[i] >= split_t:  # holdout stays reserved
            break
        if t1[i + 1] - t1[i] > 120:
            continue
        dt_ = int(t1[i]) + 60
        w5 = _closed_window(m5, ends["m5"], dt_, M5_N)
        w15 = _closed_window(m15, ends["m15"], dt_, M15_N)
        pw = {p: _closed_window(peers[p], peer_ends[p], dt_, M5_N) for p in peers}
        if w5 is None or w15 is None or any(v is None for v in pw.values()):
            continue
        vs = cast_votes(
            symbol, _ohlcv(m1, i - M1_N + 1, i + 1), _ohlcv(m5, *w5), m15=_ohlcv(m15, *w15),
            btc_m5=_ohlcv(peers["BTCUSD!"], *pw["BTCUSD!"]),
            eth_m5=_ohlcv(peers["ETHUSD!"], *pw["ETHUSD!"]),
            sol_m5=_ohlcv(peers["SOLUSD!"], *pw["SOLUSD!"]),
        )
        row = [sign[v.side] for v in vs]
        buy, sell = row.count(1), row.count(-1)
        row.append(1 if buy >= MIN_AGREE else (-1 if sell >= MIN_AGREE else 0))
        entry = float(op[i + 1])
        if entry <= 0:
            continue
        votes.append(row)
        fwd.append([(float(op[i + 1 + h]) - entry) / entry for h in HORIZONS])
        days.append(int(t1[i]) // 86400)
    if not votes:
        return None
    return (np.array(votes, dtype=np.int8), np.array(fwd, dtype=np.float64),
            np.array(days, dtype=np.int64))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=15)
    ap.add_argument("--split", default="2026-02-01")
    args = ap.parse_args()
    split_t = int(datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc).timestamp())

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    peers = {}
    for p in PEERS:
        m1 = load_m1(PAIRS[p])
        if m1 is None:
            raise SystemExit(f"falta data de peer {p} ({PAIRS[p]})")
        peers[p] = resample(m1, "5min")

    names = list(VOTER_ORDER) + ["consenso_4_5"]
    all_votes, all_fwd, all_days = [], [], []
    for symbol in sorted(PAIRS):
        if symbol in PEER_ONLY:
            continue
        got = collect(symbol, PAIRS[symbol], peers, args.step, split_t)
        if got is None:
            print(f"skip {symbol}", file=sys.stderr, flush=True)
            continue
        v, f, d = got
        all_votes.append(v)
        all_fwd.append(f)
        all_days.append(d)
        print(f"{symbol}: {len(v)} barras", file=sys.stderr, flush=True)

    V = np.vstack(all_votes)
    F = np.vstack(all_fwd)
    D = np.concatenate(all_days)
    rng = np.random.default_rng(SEED)
    out: dict = {
        "design": "auditoria de informacion, pre-registrada; holdout no leido",
        "split_utc": args.split, "step_bars": args.step, "bars": int(len(V)),
        "horizons_min": list(HORIZONS), "bootstrap": BOOTSTRAP, "seed": SEED,
        "n_tests": N_TESTS, "bonferroni_p": round(BONFERRONI, 6),
        "note": "retorno medio firmado por el voto, sin costos y sin stop (cota superior)",
        "modules": {},
    }
    for k, name in enumerate(names):
        side = V[:, k].astype(np.float64)
        mask = side != 0
        out["modules"][name] = {
            "vote_rate_pct": round(100.0 * float(mask.mean()), 1),
            "by_horizon": {
                str(h): day_boot(side[mask] * F[mask, j], D[mask], rng)
                for j, h in enumerate(HORIZONS)
            },
        }
    passes = [(n, h) for n, m in out["modules"].items()
              for h, r in m["by_horizon"].items() if r.get("pasa")]
    out["pasan_bonferroni"] = passes
    with open("analyze_module_information_out.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    print(f"\n{'modulo':16} {'voto%':>6} " +
          " ".join(f"{str(h)+'m':>16}" for h in HORIZONS))
    for name in names:
        m = out["modules"][name]
        cells = []
        for h in HORIZONS:
            r = m["by_horizon"][str(h)]
            flag = "*" if r.get("pasa") else " "
            cells.append(f"{r['mean_bps']:+8.2f}{flag}p{r['p']:<6.3f}")
        print(f"{name:16} {m['vote_rate_pct']:6.1f} " + " ".join(f"{c:>16}" for c in cells))
    print(f"\nvara Bonferroni p < {BONFERRONI:.5f}; pasan: {passes or 'ninguno'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
