"""Pre-registered 2-year test: does the live 4/5 consensus have edge at a 1-day hold?

Read-only research. Never sends orders, never calls mt5.shutdown().

Design fixed BEFORE looking at results (see trading/docs/ESTABLISHED_FACTS.md):

- Entry: identical to live (`evaluate_symbol`: 5 voters, HTF/pullback/RSI/Murphy
  gates, Kelly cap 0.50, stop min(money, 5%), 10% spread gate).
- Exit: hold for each horizon in ``--holds`` (minutes); SL/TP at the same stop.
- Data: Binance spot 1m (USDT pairs as proxy for the XBTFX USD CFDs), 24 months.
- Costs: XBTFX spread median per UTC hour (today's snapshot) + swap per rollover
  crossed, both from the live terminal. ``--stress`` uses p90 spread and 1.3x swap.
- Split: train up to ``--split`` (default 2026-02-01), holdout after. The holdout
  is only printed with ``--reveal-holdout``.
- Stats: bootstrap over calendar days (crypto on the same day is one bet).
- Decision rule: advances only if train net > 0 with day-clustered p < 0.01 AND
  the holdout confirms the sign. Nothing goes live without the dual-backtest gate.

Known biases: survivorship (today's universe, no delisted coins), venue mismatch
(Binance spot vs XBTFX CFD), constant spread assumed over 2 years, USDT ~ USD.

    py analyze_horizon_edge_2y.py --holds 1440,240 --step 15
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from analyze_usdjpy_rotation_replay import _closed_window, _ohlcv, _rates, metrics, simulate
from fetch_binance_1m import PAIRS
from trading.domain.ensemble import SIDE_BUY, SIDE_SELL, ContractSpec, evaluate_symbol
from trading.domain.rotation_stops import rotation_spread_skip_reason, rotation_stop_distance

DATA_DIR = Path("logs/binance")
M1_N, M5_N, M15_N, H4_N = 80, 40, 192, 72
MAX_RISK_USD = 0.50
MAX_SPREAD_FRAC = 0.10
PEERS = ("BTCUSD!", "ETHUSD!", "SOLUSD!")
PEER_ONLY = ("BTCUSD!",)  # swap makes BTC unusable at long holds; kept as intermarket peer
BAR = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                ("close", "f8"), ("tick_volume", "f8"), ("spread", "f8")])
BOOTSTRAP = 2000
SEED = 20261005


def load_m1(pair: str) -> np.ndarray | None:
    path = DATA_DIR / f"{pair}_1m.npz"
    if not path.exists():
        return None
    z = np.load(path)
    out = np.zeros(len(z["time"]), dtype=BAR)
    out["time"] = z["time"]
    for k in ("open", "high", "low", "close"):
        out[k] = z[k].astype(np.float64)
    out["tick_volume"] = z["volume"].astype(np.float64)
    return out


def resample(m1: np.ndarray, rule: str) -> np.ndarray:
    idx = pd.to_datetime(m1["time"], unit="s", utc=True)
    df = pd.DataFrame({"open": m1["open"], "high": m1["high"], "low": m1["low"],
                       "close": m1["close"], "volume": m1["tick_volume"]}, index=idx)
    a = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    out = np.zeros(len(a), dtype=BAR)
    out["time"] = a.index.asi8 // 1_000_000_000
    for k in ("open", "high", "low", "close"):
        out[k] = a[k].to_numpy()
    out["tick_volume"] = a["volume"].to_numpy()
    return out


def contract_facts(symbol: str, *, stress: bool) -> dict:
    """Spread per UTC hour and swap per rollover, from the live terminal cache."""
    info = mt5.symbol_info(symbol)
    cached = _rates(symbol, mt5.TIMEFRAME_M1, 5000)
    hours = (cached["time"] // 3600) % 24
    if stress:
        by_hour = {int(h): float(np.percentile(cached["spread"][hours == h], 90)) for h in np.unique(hours)}
    else:
        by_hour = {int(h): float(np.median(cached["spread"][hours == h])) for h in np.unique(hours)}
    point = float(info.point)
    vpp = float(info.trade_tick_value) / float(info.trade_tick_size)
    return {
        "info": info, "point": point, "tick_size": float(info.trade_tick_size),
        "tick_value": float(info.trade_tick_value), "vpp": vpp,
        "spread_by_hour": by_hour,
        "swap_usd_per_day_per_lot": float(info.swap_long) * point * vpp * (1.3 if stress else 1.0),
    }


def attach_spread(m1: np.ndarray, by_hour: dict[int, float]) -> None:
    hours = (m1["time"] // 3600) % 24
    lut = np.array([by_hour.get(h, 0.0) for h in range(24)])
    m1["spread"] = lut[hours]


def rollovers_crossed(t_in: int, t_out: int) -> int:
    """Daily rollovers at 00:00 UTC between entry and exit (XBTFX server clock = UTC)."""
    return max(0, int(t_out // 86400) - int(t_in // 86400))


def day_bootstrap(rows: list[dict], key: str, *, rng: np.random.Generator) -> dict:
    """Mean with a CI from resampling calendar days (one bet per day, all symbols)."""
    if not rows:
        return {}
    by_day: dict[int, list[float]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(float(r[key]))
    days = list(by_day)
    pooled = [v for d in days for v in by_day[d]]
    mean = st.fmean(pooled)
    means = np.empty(BOOTSTRAP)
    for b in range(BOOTSTRAP):
        pick = rng.choice(len(days), size=len(days), replace=True)
        vals = [v for j in pick for v in by_day[days[j]]]
        means[b] = np.mean(vals)
    lo, hi = np.percentile(means, [2.5, 97.5])
    side = float(np.mean(means <= 0.0)) if mean > 0 else float(np.mean(means >= 0.0))
    return {"mean": round(mean, 5), "ci95": [round(float(lo), 5), round(float(hi), 5)],
            "p_two_sided": round(min(1.0, 2.0 * side), 4), "n_days": len(days), "n_trades": len(pooled)}


def run_symbol(symbol: str, pair: str, peers: dict[str, np.ndarray], holds: list[int],
               step: int, stress: bool) -> dict[int, list[dict]]:
    m1 = load_m1(pair)
    if m1 is None:
        return {}
    facts = contract_facts(symbol, stress=stress)
    attach_spread(m1, facts["spread_by_hour"])
    info, point, vpp = facts["info"], facts["point"], facts["vpp"]
    tick_size, tick_value = facts["tick_size"], facts["tick_value"]
    m5, m15, h4 = (resample(m1, r) for r in ("5min", "15min", "4h"))
    ends = {"m5": m5["time"] + 300, "m15": m15["time"] + 900, "h4": h4["time"] + 14400}
    peer_ends = {p: r["time"] + 300 for p, r in peers.items()}
    t1 = m1["time"]
    n = len(m1)
    max_hold = max(holds)

    fires: list[dict] = []
    for i in range(M1_N, n - max_hold - 2, max(1, step)):
        if t1[i + 1] - t1[i] > 120:
            continue
        dt_ = int(t1[i]) + 60
        w5 = _closed_window(m5, ends["m5"], dt_, M5_N)
        w15 = _closed_window(m15, ends["m15"], dt_, M15_N)
        w4 = _closed_window(h4, ends["h4"], dt_, H4_N)
        pw = {p: _closed_window(peers[p], peer_ends[p], dt_, M5_N) for p in peers}
        if w5 is None or w15 is None or any(v is None for v in pw.values()):
            continue
        px = float(m1["close"][i])
        spec = ContractSpec(
            volume_min=float(info.volume_min), volume_step=float(info.volume_step),
            volume_max=float(info.volume_max),
            usd_at_min_lot_min_stop=max(point, tick_size, info.trade_stops_level * point) / tick_size * tick_value * float(info.volume_min),
            price=px, tick_value=tick_value, tick_size=tick_size,
        )
        snap = evaluate_symbol(
            symbol, _ohlcv(m1, i - M1_N + 1, i + 1), _ohlcv(m5, *w5),
            m15=_ohlcv(m15, *w15), h4=_ohlcv(h4, *w4) if w4 else None,
            btc_m5=_ohlcv(peers["BTCUSD!"], *pw["BTCUSD!"]),
            eth_m5=_ohlcv(peers["ETHUSD!"], *pw["ETHUSD!"]),
            sol_m5=_ohlcv(peers["SOLUSD!"], *pw["SOLUSD!"]),
            contract=spec, max_risk_usd=MAX_RISK_USD, m1_time=int(t1[i]),
        )
        if snap.action != "FIRE" or snap.consensus.direction not in (SIDE_BUY, SIDE_SELL):
            continue
        lot = float(snap.size.lot or info.volume_min)
        dist = rotation_stop_distance(price=px, lot=lot, tick_value=tick_value,
                                      tick_size=tick_size, max_risk_usd=MAX_RISK_USD)
        if not dist:
            continue
        risk = float(snap.size.risk_usd or MAX_RISK_USD)
        if rotation_spread_skip_reason(
            spread_px=float(m1["spread"][i + 1]) * point, stop_dist=dist, lot=lot,
            tick_value=tick_value, tick_size=tick_size, risk_usd=risk,
            max_spread_frac_of_risk=MAX_SPREAD_FRAC,
        ):
            continue
        fires.append({"i": i, "side": snap.consensus.direction, "dist": dist, "lot": lot, "risk": risk})

    out: dict[int, list[dict]] = {}
    for hold in holds:
        rows: list[dict] = []
        busy = -1
        for f in fires:
            i = f["i"]
            if i <= busy or i + 1 + hold >= n:
                continue
            if t1[i + 1 + hold] - t1[i + 1] > 60 * hold * 1.15 + 300:
                continue
            kw = dict(dist=f["dist"], lot=f["lot"], contract=0.0, point=point,
                      spread_floor_pts=0, hold=hold, usd_per_px_per_lot=vpp)
            row = simulate(m1, i, f["side"], **kw)
            mirror = simulate(m1, i, SIDE_SELL if f["side"] == SIDE_BUY else SIDE_BUY, **kw)
            swap = facts["swap_usd_per_day_per_lot"] * f["lot"] * rollovers_crossed(
                int(t1[i + 1]), int(t1[i + 1 + hold]))
            rows.append({
                "symbol": symbol, "t": int(t1[i]), "day": int(t1[i] // 86400),
                "net": row["net"] + swap, "net_no_swap": row["net"], "gross": row["gross"],
                "spread_usd": row["spread_usd"], "swap_usd": swap, "how": row["how"],
                "mirror": mirror["net"] + swap, "risk": f["risk"],
            })
            busy = i + hold
        out[hold] = rows
    return out


def summarize(rows: list[dict], rng: np.random.Generator) -> dict:
    if not rows:
        return {"n": 0}
    base = metrics(rows)
    return {
        "n": base["n"], "win_rate_pct": base["win_rate_pct"], "profit_factor": base["profit_factor"],
        "max_drawdown_usd": base["max_drawdown_usd"], "exits": base["exits"],
        "mean_spread_usd": round(st.fmean(r["spread_usd"] for r in rows), 4),
        "mean_swap_usd": round(st.fmean(r["swap_usd"] for r in rows), 4),
        "net": day_bootstrap(rows, "net", rng=rng),
        "gross": day_bootstrap(rows, "gross", rng=rng),
        "net_no_swap": day_bootstrap(rows, "net_no_swap", rng=rng),
        "mirror": day_bootstrap(rows, "mirror", rng=rng),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holds", default="1440,240")
    ap.add_argument("--step", type=int, default=15)
    ap.add_argument("--split", default="2026-02-01")
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--reveal-holdout", action="store_true")
    args = ap.parse_args()
    holds = [int(x) for x in args.holds.split(",") if x.strip()]
    split_t = int(datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc).timestamp())

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    for s in PAIRS:
        mt5.symbol_select(s, True)
    peers = {}
    for p in PEERS:
        m1 = load_m1(PAIRS[p])
        if m1 is None:
            raise SystemExit(f"falta data de peer {p} ({PAIRS[p]}) en {DATA_DIR}")
        peers[p] = resample(m1, "5min")

    rng = np.random.default_rng(SEED)
    pooled: dict[int, list[dict]] = {h: [] for h in holds}
    per_symbol: dict[str, dict] = {}
    tradable = [s for s in sorted(PAIRS) if s not in PEER_ONLY]
    for symbol in tradable:
        res = run_symbol(symbol, PAIRS[symbol], peers, holds, args.step, args.stress)
        if not res:
            print(f"skip {symbol}: sin data", file=sys.stderr, flush=True)
            continue
        for h, rows in res.items():
            pooled[h].extend(rows)
        per_symbol[symbol] = {
            str(h): {"n": len(rows),
                     "train_net": round(st.fmean([r["net"] for r in rows if r["t"] < split_t]), 5) if any(r["t"] < split_t for r in rows) else None}
            for h, rows in res.items()
        }
        print(f"{symbol}: " + " ".join(f"{h}m n={len(r)}" for h, r in res.items()), file=sys.stderr, flush=True)

    out: dict = {
        "design": "pre-registrado; holdout oculto salvo --reveal-holdout",
        "source": "Binance spot 1m (USDT) + spread/swap XBTFX del terminal",
        "stress": bool(args.stress), "step_bars": args.step, "split_utc": args.split,
        "symbols": len(per_symbol), "bootstrap": BOOTSTRAP, "seed": SEED,
        "decision_rule": "train net>0 con p<0.01 por dia Y holdout confirma el signo",
        "train": {}, "per_symbol": per_symbol,
    }
    for h in holds:
        rows = pooled[h]
        train = [r for r in rows if r["t"] < split_t]
        hold_rows = [r for r in rows if r["t"] >= split_t]
        span = (min((r["t"] for r in rows), default=0), max((r["t"] for r in rows), default=0))
        out["train"][str(h)] = {
            "range_utc": [datetime.fromtimestamp(span[0], timezone.utc).strftime("%Y-%m-%d"),
                          datetime.fromtimestamp(span[1], timezone.utc).strftime("%Y-%m-%d")],
            "holdout_trades_reserved": len(hold_rows),
            **summarize(train, rng),
        }
        if args.reveal_holdout:
            out.setdefault("holdout", {})[str(h)] = summarize(hold_rows, rng)
    suffix = "_stress" if args.stress else ""
    Path(f"analyze_horizon_edge_2y{suffix}_out.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    show = {k: v for k, v in out.items() if k in ("train", "holdout")}
    print(json.dumps(show, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
