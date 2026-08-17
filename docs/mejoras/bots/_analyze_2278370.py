"""Analyze multi-instrument MQL5 signal 2278370."""
from __future__ import annotations

import csv
import statistics as stats
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PATH = Path(__file__).resolve().parent / "2278370.positions.csv"


def parse() -> list[dict]:
    rows: list[dict] = []
    with PATH.open(encoding="utf-8-sig") as f:
        r = csv.reader(f, delimiter=";")
        next(r)
        for line in r:
            if len(line) < 11:
                continue
            typ = (line[1] or "").strip()
            if typ.lower() == "balance":
                continue
            if not line[0] or not line[2] or not line[4] or not line[6] or not line[7]:
                continue
            try:
                t0 = datetime.strptime(line[0], "%Y.%m.%d %H:%M:%S")
                t1 = datetime.strptime(line[6], "%Y.%m.%d %H:%M:%S")
                vol_open = float(line[2])
                vol_close = float(line[5]) if line[5] else vol_open
                px0 = float(line[4])
                px1 = float(line[7])
                pnl = float(line[10] or 0)
            except ValueError:
                continue
            side = typ
            sym = line[3]
            swap = float(line[9] or 0)
            hold = (t1 - t0).total_seconds()
            rows.append(
                dict(
                    t0=t0,
                    t1=t1,
                    side=side,
                    sym=sym,
                    vol_open=vol_open,
                    vol_close=vol_close,
                    px0=px0,
                    px1=px1,
                    swap=swap,
                    pnl=pnl,
                    hold=hold,
                    partial=abs(vol_close - vol_open) > 1e-9,
                )
            )
    return rows


def main() -> None:
    rows = parse()
    print("n_trades", len(rows))
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp, gl = sum(wins), abs(sum(losses))
    print(
        f"WR={len(wins)/len(rows)*100:.1f}% PF={gp/gl:.2f} "
        f"E={sum(pnls)/len(pnls):.1f} total={sum(pnls):.0f}"
    )
    print(f"avgW={stats.mean(wins):.1f} avgL={stats.mean(losses):.1f} |W/L|={abs(stats.mean(wins)/stats.mean(losses)):.3f}")
    holds = [r["hold"] for r in rows]
    print(
        f"hold_h: med={stats.median(holds)/3600:.2f} mean={stats.mean(holds)/3600:.2f} "
        f"p90={sorted(holds)[int(0.9*len(holds))-1]/3600:.2f}"
    )
    print("partial_closes", sum(1 for r in rows if r["partial"]), f"({sum(1 for r in rows if r['partial'])/len(rows)*100:.1f}%)")
    print("sides", dict(Counter(r["side"] for r in rows)))

    # per symbol
    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sym[r["sym"]].append(r)
    print("\n=== PER SYMBOL ===")
    print(f"{'sym':12} {'n':>5} {'WR%':>6} {'PF':>6} {'pnl':>12} {'buy%':>6} {'hold_h_med':>10} {'partial%':>8}")
    for sym, rs in sorted(by_sym.items(), key=lambda x: -len(x[1])):
        p = [x["pnl"] for x in rs]
        w = [x for x in p if x > 0]
        l = [x for x in p if x <= 0]
        pf = (sum(w) / abs(sum(l))) if l and sum(l) != 0 else 99
        buy = sum(1 for x in rs if x["side"].lower().startswith("buy")) / len(rs)
        hm = stats.median([x["hold"] for x in rs]) / 3600
        part = sum(1 for x in rs if x["partial"]) / len(rs)
        print(
            f"{sym:12} {len(rs):5d} {len(w)/len(rs)*100:6.1f} {pf:6.2f} {sum(p):12.0f} "
            f"{buy*100:6.1f} {hm:10.2f} {part*100:8.1f}"
        )

    # concurrency / basket closes
    exit_groups: dict = defaultdict(list)
    for r in rows:
        # bucket close to nearest minute
        key = r["t1"].replace(second=0, microsecond=0)
        exit_groups[key].append(r)
    multi = sorted(((t, g) for t, g in exit_groups.items() if len(g) >= 3), key=lambda x: -len(x[1]))
    print(f"\nclose clusters (>=3 same minute): {len(multi)} max={len(multi[0][1]) if multi else 0}")
    if multi:
        eg = multi[0][1]
        print(
            "largest cluster:",
            multi[0][0],
            Counter(x["sym"] for x in eg),
            "pnl",
            round(sum(x["pnl"] for x in eg), 0),
        )

    events = []
    for i, r in enumerate(rows):
        events.append((r["t0"], 1))
        events.append((r["t1"], -1))
    events.sort()
    cur = max_open = 0
    for _, d in events:
        cur += d
        max_open = max(max_open, cur)
    print("max concurrent opens:", max_open)

    # BUY vs SELL pnl
    print(
        "pnl BUY",
        round(sum(r["pnl"] for r in rows if r["side"].lower().startswith("buy")), 0),
        "SELL",
        round(sum(r["pnl"] for r in rows if r["side"].lower().startswith("sell")), 0),
    )

    # BTC buy bias contribution
    btc = by_sym.get("BTCUSD", [])
    if btc:
        btc_buy = [r for r in btc if r["side"].lower().startswith("buy")]
        btc_sell = [r for r in btc if r["side"].lower().startswith("sell")]
        print(
            f"BTC: n={len(btc)} buy_pnl={sum(r['pnl'] for r in btc_buy):.0f} "
            f"sell_pnl={sum(r['pnl'] for r in btc_sell):.0f} "
            f"buy_n={len(btc_buy)} sell_n={len(btc_sell)}"
        )

    # correlation-ish: same-day multi-symbol exposure
    by_day_sym: dict = defaultdict(set)
    for r in rows:
        by_day_sym[r["t0"].date()].add(r["sym"])
    multi_day = sum(1 for s in by_day_sym.values() if len(s) >= 3)
    print(f"days with >=3 symbols traded: {multi_day}/{len(by_day_sym)}")

    # swap burden
    print("total swap", round(sum(r["swap"] for r in rows), 0))

    # edge sensitivity: if WR -5pp
    p = len(wins) / len(rows)
    W, L = stats.mean(wins), abs(stats.mean(losses))
    print(f"E_now={p*W-(1-p)*L:.1f}")
    print(f"E_if_WR-5pp={(p-0.05)*W-(1-(p-0.05))*L:.1f}")

    # worst floating proxy: longest holds that lost
    losers = sorted([r for r in rows if r["pnl"] <= 0], key=lambda x: -x["hold"])[:5]
    print("longest losing holds (days, sym, pnl):")
    for r in losers:
        print(f"  {r['hold']/86400:.1f}d {r['sym']} {r['side']} pnl={r['pnl']:.0f}")


if __name__ == "__main__":
    main()
