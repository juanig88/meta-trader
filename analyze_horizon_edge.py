"""Does the live 4/5 consensus carry any edge at longer holds? Read-only research.

Same entry decision as live (`evaluate_symbol`: 5 voters, HTF/pullback/RSI/Murphy
gates, Kelly sizing, ±5%-or-money stop). Only the exit changes: instead of the
5-minute time stop, hold for each horizon in ``--holds`` (minutes).

Why: XBTFX spread is ~0.20% of price while the median 5-minute move is ~0.15%,
so at 5 minutes the cost exceeds the move no matter how good the signal is. At
4 hours the median move is ~1.1%, so the same cost is ~18% of the move. This
measures whether the existing votes hold slow information once cost stops
dominating.

Data: Yahoo 1m CSVs in ``logs/`` (M5/M15/H4 resampled); spread is the XBTFX
median per UTC hour from the 5000 M1 bars already cached in the terminal, so it
never triggers a history download (that freezes the live bot).

Never sends orders, never calls mt5.shutdown().

    py analyze_horizon_edge.py --holds 5,240,1440
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np

from analyze_usdjpy_rotation_replay import (
    _closed_window,
    _ohlcv,
    _rates,
    _resample,
    _to_bars,
    _yf_frame,
    metrics,
    simulate,
)
from trading.domain.ensemble import SIDE_BUY, SIDE_SELL, ContractSpec, evaluate_symbol
from trading.domain.rotation_stops import rotation_spread_skip_reason, rotation_stop_distance

M1_N, M5_N, M15_N, H4_N = 80, 40, 192, 72
MAX_RISK_USD = 0.50
MAX_SPREAD_FRAC = 0.10
PEERS = {"BTCUSD!": "BTC_USD", "ETHUSD!": "ETH_USD", "SOLUSD!": "SOL_USD"}
UNIVERSE = {
    "ADAUSD!": "ADA_USD", "XRPUSD!": "XRP_USD", "DOGUSD!": "DOGE_USD", "LNKUSD!": "LINK_USD",
    "DOTUSD!": "DOT_USD", "LTCUSD!": "LTC_USD", "BCHUSD!": "BCH_USD", "ATMUSD!": "ATOM_USD",
    "AVAUSD!": "AVAX_USD", "FILUSD!": "FIL_USD", "ETHUSD!": "ETH_USD", "SOLUSD!": "SOL_USD",
}


def csv_path(stem: str) -> Path:
    return Path(f"logs/{stem}_1m_yf.csv")


def spread_by_hour(symbol: str) -> dict[int, float]:
    cached = _rates(symbol, mt5.TIMEFRAME_M1, 5000)
    hours = (cached["time"] // 3600) % 24
    return {int(h): float(np.median(cached["spread"][hours == h])) for h in np.unique(hours)}


def run_symbol(symbol: str, stem: str, peers: dict[str, np.ndarray], holds: list[int], step: int) -> dict:
    info = mt5.symbol_info(symbol)
    point, tick_size, tick_value = float(info.point), float(info.trade_tick_size), float(info.trade_tick_value)
    vpp = tick_value / tick_size
    own = _yf_frame(csv_path(stem))
    m1 = _to_bars(own, spread_by_hour(symbol))
    m5, m15, h4 = (_to_bars(_resample(own, r)) for r in ("5min", "15min", "4h"))
    ends = {"m5": m5["time"] + 300, "m15": m15["time"] + 900, "h4": h4["time"] + 14400}
    peer_ends = {p: r["time"] + 300 for p, r in peers.items()}
    t1 = m1["time"].astype(np.int64)
    n = len(m1)
    max_hold = max(holds)

    fires: list[dict] = []
    outcome: dict[str, int] = {}
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
            symbol,
            _ohlcv(m1, i - M1_N + 1, i + 1), _ohlcv(m5, *w5),
            m15=_ohlcv(m15, *w15), h4=_ohlcv(h4, *w4) if w4 else None,
            btc_m5=_ohlcv(peers["BTCUSD!"], *pw["BTCUSD!"]),
            eth_m5=_ohlcv(peers["ETHUSD!"], *pw["ETHUSD!"]),
            sol_m5=_ohlcv(peers["SOLUSD!"], *pw["SOLUSD!"]),
            contract=spec, max_risk_usd=MAX_RISK_USD, m1_time=int(t1[i]),
        )
        if not snap.consensus.fired or snap.consensus.direction not in (SIDE_BUY, SIDE_SELL):
            continue
        key = snap.skip_reason or "dispara"
        outcome[key] = outcome.get(key, 0) + 1
        if snap.action != "FIRE":
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

    res: dict = {"m1_bars": int(n), "days": round((t1[-1] - t1[0]) / 86400.0, 1),
                 "fires_4_5": int(sum(outcome.values())), "fire_outcome": outcome,
                 "sendable": len(fires), "by_hold": {}}
    for hold in holds:
        rows: list[dict] = []
        busy = -1
        for f in fires:
            i = f["i"]
            if i <= busy:
                continue
            if i + 1 + hold >= n or t1[i + 1 + hold] - t1[i + 1] > 60 * hold * 1.15 + 300:
                continue
            kw = dict(dist=f["dist"], lot=f["lot"], contract=0.0, point=point,
                      spread_floor_pts=0, hold=hold, usd_per_px_per_lot=vpp)
            row = simulate(m1, i, f["side"], **kw)
            mirror = simulate(m1, i, SIDE_SELL if f["side"] == SIDE_BUY else SIDE_BUY, **kw)
            rows.append({**row, "mirror": mirror["net"], "risk": f["risk"]})
            busy = i + hold
        res["by_hold"][str(hold)] = {"rows": rows}
    return res


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    net, gross = metrics(rows), metrics(rows, "gross")
    half = len(rows) // 2
    return {
        "n": net["n"], "net_per_trade": net["mean_usd"], "win_rate_pct": net["win_rate_pct"],
        "profit_factor": net["profit_factor"], "t_stat_net": net["t_stat"],
        "gross_per_trade": gross["mean_usd"], "t_stat_gross": gross["t_stat"],
        "mean_spread_usd": round(st.fmean(r["spread_usd"] for r in rows), 4),
        "mirror_per_trade": metrics([{**r, "net": r["mirror"]} for r in rows])["mean_usd"],
        "first_half_net": metrics(rows[:half]).get("mean_usd"),
        "second_half_net": metrics(rows[half:]).get("mean_usd"),
        "max_drawdown_usd": net["max_drawdown_usd"], "exits": net["exits"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holds", default="5,240,1440", help="minutos de tenencia")
    ap.add_argument("--step", type=int, default=5, help="cada cuantas velas M1 se evalua")
    args = ap.parse_args()
    holds = [int(x) for x in args.holds.split(",") if x.strip()]

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    for s in set(UNIVERSE) | set(PEERS):
        mt5.symbol_select(s, True)
    peers = {p: _to_bars(_resample(_yf_frame(csv_path(stem)), "5min")) for p, stem in PEERS.items()}

    per_symbol: dict[str, dict] = {}
    pooled: dict[str, list[dict]] = {str(h): [] for h in holds}
    for symbol, stem in sorted(UNIVERSE.items()):
        if not csv_path(stem).exists():
            print(f"skip {symbol}: sin CSV", file=sys.stderr, flush=True)
            continue
        r = run_symbol(symbol, stem, peers, holds, args.step)
        per_symbol[symbol] = {
            "days": r["days"], "fires_4_5": r["fires_4_5"], "sendable": r["sendable"],
            "by_hold": {h: summarize(v["rows"]) for h, v in r["by_hold"].items()},
        }
        for h, v in r["by_hold"].items():
            pooled[h].extend(v["rows"])
        print(f"{symbol}: fires {r['fires_4_5']} sendables {r['sendable']} "
              + " ".join(f"{h}m n={len(v['rows'])}" for h, v in r["by_hold"].items()),
              file=sys.stderr, flush=True)

    out = {
        "question": "tiene el consenso 4/5 ventaja bruta a horizontes mas largos?",
        "source": "Yahoo 1m (M5/M15/H4 resampleados) + spread XBTFX por hora UTC",
        "entry": "evaluate_symbol vivo, Kelly 0.50, stop min(money, 5%), gate de spread 10%",
        "symbols": len(per_symbol),
        "pooled": {h: summarize(rows) for h, rows in pooled.items()},
        "per_symbol": per_symbol,
    }
    Path("analyze_horizon_edge_out.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["pooled"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
