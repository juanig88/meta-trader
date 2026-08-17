"""Infer strategy patterns from MQL5 signal position CSVs."""
from __future__ import annotations

import csv
import statistics as stats
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILES = {
    "2234595": BASE / "2234595.positions.csv",
    "2362973": BASE / "2362973.positions.csv",
    "2381521": BASE / "2381521.positions.csv",
}


def parse(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        r = csv.reader(f, delimiter=";")
        next(r)
        for line in r:
            if len(line) < 11:
                continue
            if not line[0] or not line[2] or not line[4] or not line[6] or not line[7]:
                continue
            try:
                t0 = datetime.strptime(line[0], "%Y.%m.%d %H:%M:%S")
                t1 = datetime.strptime(line[6], "%Y.%m.%d %H:%M:%S")
                vol = float(line[2])
                px0 = float(line[4])
                px1 = float(line[7])
                pnl = float(line[10] or 0)
            except ValueError:
                continue
            side = line[1]
            sym = line[3]
            comm = float(line[8] or 0)
            swap = float(line[9] or 0)
            hold = (t1 - t0).total_seconds()
            move = (px1 - px0) if side.lower().startswith("buy") else (px0 - px1)
            rows.append(
                dict(
                    t0=t0,
                    t1=t1,
                    side=side,
                    vol=vol,
                    sym=sym,
                    px0=px0,
                    px1=px1,
                    comm=comm,
                    swap=swap,
                    pnl=pnl,
                    hold=hold,
                    move=move,
                )
            )
    return rows


def summarize(name: str, rows: list[dict]) -> None:
    print("=" * 70)
    print("SIGNAL", name, "n=", len(rows), "symbol=", Counter(r["sym"] for r in rows).most_common(3))
    if not rows:
        return
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl else float("inf")
    holds = [r["hold"] for r in rows]
    moves = [r["move"] for r in rows]
    vols = [r["vol"] for r in rows]
    print(
        f"WR={len(wins)/len(rows)*100:.1f}% PF={pf:.2f} "
        f"expectancy={sum(pnls)/len(pnls):.3f} total_pnl={sum(pnls):.2f}"
    )
    aw = stats.mean(wins) if wins else 0
    al = stats.mean(losses) if losses else 0
    print(f"avg_win={aw:.2f} avg_loss={al:.2f} |avgW/avgL|={abs(aw/al) if al else 0:.3f}")
    sh = sorted(holds)
    print(
        f"hold_sec: med={stats.median(holds):.0f} mean={stats.mean(holds):.0f} "
        f"p10={sh[max(0,int(0.1*len(sh))-1)]:.0f} p90={sh[int(0.9*len(sh))-1]:.0f}"
    )
    print(f"price_move: med={stats.median(moves):.2f} mean={stats.mean(moves):.2f}")
    print(f"vol: min={min(vols)} max={max(vols)} med={stats.median(vols)} unique={len(set(vols))}")
    print("sides", dict(Counter(r["side"] for r in rows)))

    by_h: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by_h[r["t0"].hour].append(r["pnl"])
    top_h = sorted(
        ((h, sum(v), len(v), sum(1 for x in v if x > 0) / len(v)) for h, v in by_h.items()),
        key=lambda x: -x[1],
    )[:8]
    print("top hours by pnl (h,pnl,n,wr):", top_h)

    by_d: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by_d[r["t0"].weekday()].append(r["pnl"])
    print("dow pnl:", {d: round(sum(v), 2) for d, v in sorted(by_d.items())})

    print("most common pnl:", Counter(round(p, 2) for p in pnls).most_common(8))
    print("most common moves:", Counter(round(m, 2) for m in moves).most_common(8))
    print(
        "top loss moves:",
        Counter(round(r["move"], 2) for r in rows if r["pnl"] <= 0).most_common(5),
    )
    print(
        "top win moves:",
        Counter(round(r["move"], 2) for r in rows if r["pnl"] > 0).most_common(5),
    )

    exit_groups: dict = defaultdict(list)
    for r in rows:
        exit_groups[r["t1"]].append(r)
    multi_exit = sorted(
        ((t, g) for t, g in exit_groups.items() if len(g) >= 2),
        key=lambda x: -len(x[1]),
    )
    print(
        f"exit clusters (>=2 same close): {len(multi_exit)} "
        f"max_size={len(multi_exit[0][1]) if multi_exit else 0}"
    )
    if multi_exit:
        eg = sorted(multi_exit[0][1], key=lambda z: z["t0"])
        print(
            "  largest:",
            [
                (
                    x["t0"].strftime("%m-%d %H:%M"),
                    x["side"],
                    x["vol"],
                    round(x["px0"], 2),
                    round(x["pnl"], 2),
                )
                for x in eg[:14]
            ],
            "n=",
            len(eg),
        )

    events = []
    for i, r in enumerate(rows):
        events.append((r["t0"], 1, i))
        events.append((r["t1"], -1, i))
    events.sort()
    cur = 0
    max_open = 0
    for _, d, _ in events:
        cur += d
        max_open = max(max_open, cur)
    print("max concurrent opens:", max_open)

    chron = sorted(rows, key=lambda r: r["t0"])
    print("first5 vols", [r["vol"] for r in chron[:5]], "last5", [r["vol"] for r in chron[-5:]])
    print(
        f"holds<=10s: {sum(1 for h in holds if h<=10)/len(rows)*100:.1f}% "
        f"holds<=60s: {sum(1 for h in holds if h<=60)/len(rows)*100:.1f}% "
        f"holds<=2s: {sum(1 for h in holds if h<=2)}"
    )
    p = len(wins) / len(rows)
    print(f"edge: E=p*W-(1-p)*L = {p:.3f}*{aw:.2f}-{(1-p):.3f}*{abs(al):.2f} = {p*aw-(1-p)*abs(al):.3f}")

    streak = 0
    maxstreak = 0
    for pnl in [r["pnl"] for r in chron]:
        if pnl <= 0:
            streak += 1
            maxstreak = max(maxstreak, streak)
        else:
            streak = 0
    print("max loss streak", maxstreak)
    buy_pnl = sum(r["pnl"] for r in rows if r["side"].lower().startswith("buy"))
    sell_pnl = sum(r["pnl"] for r in rows if r["side"].lower().startswith("sell"))
    print(f"pnl BUY={buy_pnl:.2f} SELL={sell_pnl:.2f}")
    print("top entry minutes:", Counter(r["t0"].minute for r in rows).most_common(8))

    # Grid step inference: within largest cluster, sort by entry, delta price
    if multi_exit and len(multi_exit[0][1]) >= 3:
        eg = sorted(multi_exit[0][1], key=lambda z: z["t0"])
        if all(x["side"] == eg[0]["side"] for x in eg):
            deltas = [round(eg[i]["px0"] - eg[i - 1]["px0"], 2) for i in range(1, len(eg))]
            print("grid entry deltas in largest cluster:", Counter(deltas).most_common(6))


def main() -> None:
    for name, path in FILES.items():
        summarize(name, parse(path))


if __name__ == "__main__":
    main()
