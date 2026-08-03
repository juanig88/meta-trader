"""
Parse ReportHistory-52848914.html (MT5 export, UTF-16) and compute
loss-focused statistics for the meta-trader bot.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

REPORT = Path(r"C:\Users\juani\Projects\meta-trader\Files\ReportHistory-52848914.html")


def load_html() -> str:
    raw = REPORT.read_bytes()
    # MT5 exports UTF-16 LE with BOM
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


class RowCollector(HTMLParser):
    """
    Collect each <tr>'s cell texts. The MT5 report has the "Posiciones"
    section: rows with 14 columns including Fecha/Hora open, ticket, symbol,
    type, volume, price open, S/L, T/P, Fecha/Hora close, price close,
    commission, swap, profit (last col).
    """

    def __init__(self) -> None:
        super().__init__()
        self.in_tr = False
        self.in_td = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.in_tr = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_tr:
            self.in_td = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_td:
            self.current_row.append("".join(self.current_cell).strip())
            self.in_td = False
        elif tag == "tr" and self.in_tr:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_tr = False

    def handle_data(self, data):
        if self.in_td:
            self.current_cell.append(data)


def parse_rows():
    html = load_html()
    parser = RowCollector()
    parser.feed(html)
    return parser.rows


def parse_money(s: str) -> float | None:
    s = s.replace("\xa0", " ").strip()
    if not s:
        return None
    s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_dt(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def find_position_rows(rows: list[list[str]]):
    """
    Position rows have:
      [open_dt, ticket, symbol, type, lot, open_price, sl, tp,
       close_dt, close_price, commission, swap, profit]
    But the 5th column is sometimes 'meta-trader-bot' (comment, hidden).
    The robust shape: index 0 looks like a datetime; col 3 in {'buy','sell'};
    last cell is the profit.
    """
    out = []
    for r in rows:
        if len(r) < 13:
            continue
        dt = parse_dt(r[0])
        if dt is None:
            continue
        # Detect rows where column 4 is the hidden "meta-trader-bot" comment.
        # In that case shift right by 1 for numeric cols.
        if len(r) >= 14 and r[4] and "trader" in r[4].lower():
            # Layout: [open_dt, ticket, symbol, type, comment, lot, open_px,
            #         sl, tp, close_dt, close_px, commission, swap, profit]
            try:
                symbol = r[2]
                side = r[3].lower()
                if side not in ("buy", "sell"):
                    continue
                lot = parse_money(r[5])
                open_px = parse_money(r[6])
                sl = parse_money(r[7])
                tp = parse_money(r[8])
                close_dt = parse_dt(r[9])
                close_px = parse_money(r[10])
                commission = parse_money(r[11]) or 0.0
                swap = parse_money(r[12]) or 0.0
                profit = parse_money(r[-1])
            except (IndexError, ValueError):
                continue
        else:
            try:
                symbol = r[2]
                side = r[3].lower()
                if side not in ("buy", "sell"):
                    continue
                lot = parse_money(r[4])
                open_px = parse_money(r[5])
                sl = parse_money(r[6])
                tp = parse_money(r[7])
                close_dt = parse_dt(r[8])
                close_px = parse_money(r[9])
                commission = parse_money(r[10]) or 0.0
                swap = parse_money(r[11]) or 0.0
                profit = parse_money(r[-1])
            except (IndexError, ValueError):
                continue

        if profit is None or close_dt is None or lot is None or open_px is None or close_px is None:
            continue

        out.append({
            "open_dt": dt,
            "symbol": symbol,
            "side": side,
            "lot": lot,
            "open_px": open_px,
            "sl": sl,
            "tp": tp,
            "close_dt": close_dt,
            "close_px": close_px,
            "commission": commission,
            "swap": swap,
            "profit": profit,
            "net": profit + commission + swap,
            "duration_min": (close_dt - dt).total_seconds() / 60.0,
        })
    return out


def summarize(trades):
    total = len(trades)
    if total == 0:
        return None
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    sum_net = sum(t["net"] for t in trades)
    sum_wins = sum(t["net"] for t in wins)
    sum_losses = sum(t["net"] for t in losses)
    pf = (sum_wins / -sum_losses) if sum_losses < 0 else float("inf")

    avg_win = statistics.mean(t["net"] for t in wins) if wins else 0.0
    avg_loss = statistics.mean(t["net"] for t in losses) if losses else 0.0

    avg_dur_win = statistics.mean(t["duration_min"] for t in wins) if wins else 0.0
    avg_dur_loss = statistics.mean(t["duration_min"] for t in losses) if losses else 0.0

    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100.0 * len(wins) / total,
        "net_pnl": sum_net,
        "sum_wins": sum_wins,
        "sum_losses": sum_losses,
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_dur_min_win": avg_dur_win,
        "avg_dur_min_loss": avg_dur_loss,
        "expectancy": sum_net / total,
        "biggest_loss": min(t["net"] for t in trades),
        "biggest_win": max(t["net"] for t in trades),
    }


def by_symbol(trades):
    g = defaultdict(list)
    for t in trades:
        g[t["symbol"]].append(t)
    return {s: summarize(ts) for s, ts in g.items()}


def by_side(trades):
    g = defaultdict(list)
    for t in trades:
        g[t["side"]].append(t)
    return {s: summarize(ts) for s, ts in g.items()}


def by_symbol_side(trades):
    g = defaultdict(list)
    for t in trades:
        g[(t["symbol"], t["side"])].append(t)
    return {k: summarize(ts) for k, ts in g.items()}


def by_hour(trades):
    g = defaultdict(list)
    for t in trades:
        g[t["open_dt"].hour].append(t)
    return {h: summarize(ts) for h, ts in g.items()}


def by_weekday(trades):
    g = defaultdict(list)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for t in trades:
        g[names[t["open_dt"].weekday()]].append(t)
    return {k: summarize(ts) for k, ts in g.items()}


def equity_curve(trades):
    trades = sorted(trades, key=lambda t: t["close_dt"])
    eq = 0.0
    curve = []
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        eq += t["net"]
        peak = max(peak, eq)
        dd = eq - peak  # negative or zero
        max_dd = min(max_dd, dd)
        curve.append((t["close_dt"], eq))
    return curve, max_dd


def streaks(trades):
    trades = sorted(trades, key=lambda t: t["close_dt"])
    cur_w, cur_l, max_w, max_l = 0, 0, 0, 0
    for t in trades:
        if t["net"] > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        else:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
    return max_w, max_l


def hit_sl_or_tp(t):
    """
    Best-effort: tag closures as SL / TP / OTHER by checking which level
    is closer to the actual close price (with a small tolerance).
    """
    if t["sl"] is None and t["tp"] is None:
        return "OTHER"
    close_px = t["close_px"]
    side = t["side"]
    # Heuristic: profit < 0 + close near SL => SL hit; profit > 0 + close near TP => TP hit.
    tol = 0.0005 * close_px  # 5 bps
    near_sl = t["sl"] is not None and abs(close_px - t["sl"]) <= tol
    near_tp = t["tp"] is not None and abs(close_px - t["tp"]) <= tol
    if near_sl and t["net"] <= 0:
        return "SL"
    if near_tp and t["net"] > 0:
        return "TP"
    # SL might have been moved (BE ladder). If profit ~ 0 with tight range, call BE.
    if t["sl"] is not None and abs(close_px - t["sl"]) <= 2 * tol:
        if abs(t["net"]) < 0.20:
            return "BE/SL-MOVED"
        return "SL?"
    return "OTHER"


def main():
    rows = parse_rows()
    trades = find_position_rows(rows)
    print(f"Parsed {len(trades)} closed trades")
    if not trades:
        return

    trades.sort(key=lambda t: t["open_dt"])
    first = trades[0]["open_dt"]
    last = trades[-1]["close_dt"]
    print(f"Period: {first} -> {last}")
    print()

    s = summarize(trades)
    print("=== OVERALL ===")
    for k, v in s.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")

    print("\n=== BY SYMBOL ===")
    for sym, stats in sorted(by_symbol(trades).items(), key=lambda x: x[1]["net_pnl"]):
        print(f"  {sym}: trades={stats['trades']} winrate={stats['win_rate_pct']:.1f}% "
              f"net={stats['net_pnl']:.2f} PF={stats['profit_factor']:.2f} "
              f"avgW={stats['avg_win']:.2f} avgL={stats['avg_loss']:.2f}")

    print("\n=== BY SIDE ===")
    for side, stats in by_side(trades).items():
        print(f"  {side}: trades={stats['trades']} winrate={stats['win_rate_pct']:.1f}% "
              f"net={stats['net_pnl']:.2f} PF={stats['profit_factor']:.2f}")

    print("\n=== BY SYMBOL + SIDE ===")
    for (sym, side), stats in sorted(by_symbol_side(trades).items(), key=lambda x: x[1]["net_pnl"]):
        print(f"  {sym} {side}: trades={stats['trades']} winrate={stats['win_rate_pct']:.1f}% "
              f"net={stats['net_pnl']:.2f} PF={stats['profit_factor']:.2f} "
              f"avgW={stats['avg_win']:.2f} avgL={stats['avg_loss']:.2f}")

    print("\n=== BY HOUR OF DAY (open) ===")
    for h in sorted(by_hour(trades).keys()):
        stats = by_hour(trades)[h]
        print(f"  {h:02d}h: n={stats['trades']:3d} winrate={stats['win_rate_pct']:5.1f}% "
              f"net={stats['net_pnl']:8.2f}")

    print("\n=== BY WEEKDAY (open) ===")
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    bw = by_weekday(trades)
    for d in order:
        if d in bw:
            stats = bw[d]
            print(f"  {d}: n={stats['trades']:3d} winrate={stats['win_rate_pct']:5.1f}% "
                  f"net={stats['net_pnl']:8.2f}")

    print("\n=== STREAKS ===")
    mw, ml = streaks(trades)
    print(f"  max_consecutive_wins={mw}  max_consecutive_losses={ml}")

    print("\n=== EQUITY / DRAWDOWN ===")
    curve, max_dd = equity_curve(trades)
    print(f"  max_drawdown={max_dd:.2f}  final_equity={curve[-1][1]:.2f}")

    print("\n=== EXIT REASON (heuristic) ===")
    reasons = defaultdict(list)
    for t in trades:
        reasons[hit_sl_or_tp(t)].append(t)
    for r, ts in reasons.items():
        sub = summarize(ts)
        print(f"  {r}: n={sub['trades']} winrate={sub['win_rate_pct']:.1f}% "
              f"net={sub['net_pnl']:.2f}")

    print("\n=== 15 WORST TRADES ===")
    worst = sorted(trades, key=lambda t: t["net"])[:15]
    for t in worst:
        print(f"  {t['open_dt']} -> {t['close_dt']} {t['symbol']} {t['side']:4s} "
              f"lot={t['lot']:.3f} open={t['open_px']:.2f} close={t['close_px']:.2f} "
              f"sl={t['sl']} tp={t['tp']} net={t['net']:.2f} dur={t['duration_min']:.1f}m")

    print("\n=== 15 BEST TRADES ===")
    best = sorted(trades, key=lambda t: -t["net"])[:15]
    for t in best:
        print(f"  {t['open_dt']} -> {t['close_dt']} {t['symbol']} {t['side']:4s} "
              f"lot={t['lot']:.3f} open={t['open_px']:.2f} close={t['close_px']:.2f} "
              f"sl={t['sl']} tp={t['tp']} net={t['net']:.2f} dur={t['duration_min']:.1f}m")

    # Save parsed trades to JSON for next-step analysis
    import json
    out_path = Path(r"C:\Users\juani\Projects\meta-trader\Files\parsed_trades.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [{**t, "open_dt": t["open_dt"].isoformat(), "close_dt": t["close_dt"].isoformat()} for t in trades],
            f, indent=2, ensure_ascii=False,
        )
    print(f"\nSaved parsed trades to {out_path}")


if __name__ == "__main__":
    main()
