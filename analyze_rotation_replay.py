"""Offline replay of the live rotation 4/5 on one crypto CFD, with risk raised until the spread gate passes.

Read-only research: never sends orders, never calls mt5.shutdown(). Uses the
live `evaluate_symbol` on bars closed at decision time. Sizing question: what if
the per-trade risk cap were raised just enough for the 10% spread gate to pass
(R = max(0.50, spread_usd_at_min_lot / 0.10 * 1.05))? Lot stays at volume_min,
so the stop sits at R / (usd per price per lot). Exits are symmetric SL/TP at
that distance plus a time stop at each horizon in ``--holds``.

Sources (same caveats as `analyze_usdjpy_rotation_replay.py`):
- ``yf``: Yahoo 1m for BTC/ETH/SOL in ``logs/``; spread = XBTFX median per UTC
  hour from the cached M1 bars of the symbol.
- ``mt5``: the XBTFX bars already in the terminal cache (keep ``--bars`` ≤ 5000;
  more makes the terminal download history and freezes the live bot).

    py analyze_rotation_replay.py --symbol BTCUSD! --source yf
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
from trading.domain.rotation_stops import rotation_spread_skip_reason

M1_N, M5_N, M15_N, H4_N = 80, 40, 192, 72
BASE_RISK_USD = 0.50
MAX_SPREAD_FRAC = 0.10
GATE_MARGIN = 1.05
YF = {"BTCUSD!": "logs/BTC_USD_1m_yf.csv", "ETHUSD!": "logs/ETH_USD_1m_yf.csv", "SOLUSD!": "logs/SOL_USD_1m_yf.csv"}


def load(source: str, symbol: str, bars: int, spread_by_hour: dict[int, float]) -> dict[str, np.ndarray]:
    if source == "yf":
        own = _yf_frame(Path(YF[symbol]))
        out = {
            "m1": _to_bars(own, spread_by_hour),
            "m5": _to_bars(_resample(own, "5min")),
            "m15": _to_bars(_resample(own, "15min")),
            "h4": _to_bars(_resample(own, "4h")),
        }
        for peer in YF:
            out["peer:" + peer] = _to_bars(_resample(_yf_frame(Path(YF[peer])), "5min"))
        return out
    m1 = _rates(symbol, mt5.TIMEFRAME_M1, bars)
    span = int(m1["time"][-1] - m1["time"][0])
    out = {
        "m1": m1,
        "m5": _rates(symbol, mt5.TIMEFRAME_M5, span // 300 + 500),
        "m15": _rates(symbol, mt5.TIMEFRAME_M15, span // 900 + 500),
        "h4": _rates(symbol, mt5.TIMEFRAME_H4, span // 14400 + 200),
    }
    for peer in YF:
        out["peer:" + peer] = _rates(peer, mt5.TIMEFRAME_M5, span // 300 + 500)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSD!", choices=sorted(YF))
    ap.add_argument("--source", choices=("mt5", "yf"), default="yf")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--holds", default="5,15,60")
    args = ap.parse_args()
    holds = [int(x) for x in args.holds.split(",") if x.strip()]
    sym = args.symbol

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    for s in YF:
        mt5.symbol_select(s, True)
    info = mt5.symbol_info(sym)
    point, tick_size, tick_value = float(info.point), float(info.trade_tick_size), float(info.trade_tick_value)
    vpp = tick_value / tick_size
    lot = float(info.volume_min)
    cached = _rates(sym, mt5.TIMEFRAME_M1, 5000)
    hours = (cached["time"] // 3600) % 24
    spread_by_hour = {int(h): float(np.median(cached["spread"][hours == h])) for h in np.unique(hours)}

    data = load(args.source, sym, args.bars, spread_by_hour)
    m1, m5, m15, h4 = data["m1"], data["m5"], data["m15"], data["h4"]
    peers = {p: data["peer:" + p] for p in YF}
    ends = {"m5": m5["time"] + 300, "m15": m15["time"] + 900, "h4": h4["time"] + 14400}
    peer_ends = {p: r["time"] + 300 for p, r in peers.items()}
    t1 = m1["time"].astype(np.int64)
    n = len(m1)
    max_hold = max(holds)

    decisions: list[dict] = []
    skips: dict[str, int] = {}
    for i in range(M1_N, n - max_hold - 2):
        if t1[i + 1] - t1[i] > 120:
            continue
        decision_t = int(t1[i]) + 60
        w5 = _closed_window(m5, ends["m5"], decision_t, M5_N)
        w15 = _closed_window(m15, ends["m15"], decision_t, M15_N)
        w4 = _closed_window(h4, ends["h4"], decision_t, H4_N)
        pw = {p: _closed_window(peers[p], peer_ends[p], decision_t, M5_N) for p in peers}
        if w5 is None or w15 is None or any(v is None for v in pw.values()):
            continue
        px = float(m1["close"][i])
        spread_usd = float(m1["spread"][i]) * point * vpp * lot
        risk = max(BASE_RISK_USD, spread_usd / MAX_SPREAD_FRAC * GATE_MARGIN)
        spec = ContractSpec(
            volume_min=lot, volume_step=float(info.volume_step), volume_max=float(info.volume_max),
            usd_at_min_lot_min_stop=max(point, tick_size, info.trade_stops_level * point) / tick_size * tick_value * lot,
            price=px, tick_value=tick_value, tick_size=tick_size,
        )
        snap = evaluate_symbol(
            sym,
            _ohlcv(m1, i - M1_N + 1, i + 1),
            _ohlcv(m5, *w5),
            m15=_ohlcv(m15, *w15),
            h4=_ohlcv(h4, *w4) if w4 else None,
            btc_m5=_ohlcv(peers["BTCUSD!"], *pw["BTCUSD!"]),
            eth_m5=_ohlcv(peers["ETHUSD!"], *pw["ETHUSD!"]),
            sol_m5=_ohlcv(peers["SOLUSD!"], *pw["SOLUSD!"]),
            contract=spec,
            max_risk_usd=risk,
            m1_time=int(t1[i]),
        )
        if not snap.consensus.fired or snap.consensus.direction not in (SIDE_BUY, SIDE_SELL):
            continue
        k = snap.skip_reason or "dispara"
        skips[k] = skips.get(k, 0) + 1
        if snap.action != "FIRE":
            continue
        trade_lot = float(snap.size.lot or lot)
        dist = risk / (vpp * trade_lot)
        gate = rotation_spread_skip_reason(
            spread_px=float(m1["spread"][i + 1]) * point, stop_dist=dist, lot=trade_lot,
            tick_value=tick_value, tick_size=tick_size, risk_usd=risk, max_spread_frac_of_risk=MAX_SPREAD_FRAC,
        )
        decisions.append({"i": i, "side": snap.consensus.direction, "agree": snap.consensus.agree,
                          "risk": risk, "dist": dist, "lot": trade_lot, "gate": gate or "pasa"})
        if len(decisions) % 200 == 0:
            print(f"  {len(decisions)} fires, bar {i}/{n}", file=sys.stderr, flush=True)

    out: dict = {
        "symbol": sym,
        "source": args.source,
        "m1_bars": int(n),
        "calendar_days": round((t1[-1] - t1[0]) / 86400.0, 1),
        "spread_pts_by_utc_hour_xbtfx": spread_by_hour,
        "fires_4_5": int(sum(skips.values())),
        "fire_outcome": skips,
        "gate_pass_with_raised_risk": sum(1 for d in decisions if d["gate"] == "pasa"),
        "risk_usd_median": round(st.median(d["risk"] for d in decisions), 2) if decisions else None,
        "stop_pct_median": round(100 * st.median(d["dist"] / float(m1["close"][d["i"]]) for d in decisions), 3) if decisions else None,
        "by_hold_minutes": {},
    }
    for hold in holds:
        rows: list[dict] = []
        busy_until = -1
        for d in decisions:
            i = d["i"]
            if i <= busy_until or d["gate"] != "pasa":
                continue
            if t1[i + 1 + hold] - t1[i + 1] > 60 * hold * 1.1 + 120:
                continue
            kw = dict(dist=d["dist"], lot=d["lot"], contract=0.0, point=point, spread_floor_pts=0,
                      hold=hold, usd_per_px_per_lot=vpp)
            row = simulate(m1, i, d["side"], **kw)
            mirror = simulate(m1, i, SIDE_SELL if d["side"] == SIDE_BUY else SIDE_BUY, **kw)
            rows.append({**row, "mirror": mirror["net"], "risk": d["risk"], "r_mult": row["net"] / d["risk"]})
            busy_until = i + hold
        half = len(rows) // 2
        out["by_hold_minutes"][str(hold)] = {
            "net": metrics(rows),
            "gross_before_spread": metrics(rows, "gross"),
            "mirror_side_net": metrics([{**r, "net": r["mirror"]} for r in rows]),
            "first_half_net": metrics(rows[:half]),
            "second_half_net": metrics(rows[half:]),
            "mean_spread_usd": round(st.fmean(r["spread_usd"] for r in rows), 3) if rows else None,
            "mean_r_multiple": round(st.fmean(r["r_mult"] for r in rows), 4) if rows else None,
        }
    path = Path(f"analyze_rotation_replay_{sym.rstrip('!').lower()}_{args.source}_out.json")
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
