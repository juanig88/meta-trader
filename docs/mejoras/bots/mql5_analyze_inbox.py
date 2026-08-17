"""
Analiza CSVs de positions exportados desde MQL5 (requieren login en el browser).

Flujo:
  1) Entrá a mql5.com → Signal → Export positions
  2) Guardá el CSV en docs/mejoras/bots/inbox_positions/
  3) python docs/mejoras/bots/mql5_analyze_inbox.py

Detecta heurísticas: grid/martingale, hold corto, SL fijo, concentración por símbolo.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox_positions"
OUT = HERE / "mql5_crawl" / "inbox_analysis.md"
OUT_JSON = HERE / "mql5_crawl" / "inbox_analysis.json"


def _f(x):
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    t = str(x).strip().replace(" ", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def load_csv(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    # MQL5 export: Time;Type;Volume;Symbol;Price;Volume;Time;Price;Commission;Swap;Profit
    # (duplicate header names → parse positional)
    sep = ";" if lines[0].count(";") >= 5 else ","
    header = [h.strip().lower() for h in lines[0].split(sep)]
    mql5_positional = (
        len(header) >= 11
        and header[0] == "time"
        and "symbol" in header
        and header[-1] == "profit"
    )
    out: list[dict] = []
    for ln in lines[1:]:
        cols = ln.split(sep)
        if mql5_positional and len(cols) >= 11:
            out.append(
                {
                    "open time": cols[0].strip(),
                    "type": cols[1].strip(),
                    "volume": cols[2].strip(),
                    "symbol": cols[3].strip(),
                    "open price": cols[4].strip(),
                    "close time": cols[6].strip(),
                    "close price": cols[7].strip(),
                    "commission": cols[8].strip(),
                    "swap": cols[9].strip(),
                    "profit": cols[10].strip(),
                }
            )
            continue
        # generic DictReader fallback for one row
        row = {}
        for i, h in enumerate(header):
            if i < len(cols):
                key = re.sub(r"\s+", " ", h)
                # last occurrence wins for dup keys — avoid for MQL5
                if key not in row:
                    row[key] = cols[i].strip()
        out.append(row)
    return out


def pick(row: dict, *names: str) -> str | None:
    for n in names:
        if n in row and row[n] != "":
            return row[n]
    # fuzzy
    for k, v in row.items():
        for n in names:
            if n in k and v != "":
                return v
    return None


def analyze(path: Path) -> dict:
    rows = load_csv(path)
    if not rows:
        return {"file": path.name, "error": "empty"}

    pnls = []
    lots = []
    holds_min = []
    symbols = Counter()
    open_min_mod = Counter()
    same_exit_clusters = 0
    exit_buckets: dict[str, list] = defaultdict(list)

    for r in rows:
        sym = pick(r, "symbol", "item") or "?"
        symbols[sym] += 1
        pnl = _f(pick(r, "profit", "profit (usd)", "profit(usd)", "pl", "p/l"))
        if pnl is not None:
            pnls.append(pnl)
        lot = _f(pick(r, "volume", "lots", "lot", "size"))
        if lot is not None:
            lots.append(lot)
        # times
        t_open = pick(r, "open time", "time", "open")
        t_close = pick(r, "close time", "close")
        if t_open:
            try:
                # try common formats
                for fmt in (
                    "%Y.%m.%d %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y.%m.%d %H:%M",
                    "%d.%m.%Y %H:%M:%S",
                ):
                    try:
                        dto = datetime.strptime(t_open[:19], fmt)
                        open_min_mod[dto.minute] += 1
                        if t_close:
                            dtc = datetime.strptime(t_close[:19], fmt)
                            holds_min.append(max(0.0, (dtc - dto).total_seconds() / 60.0))
                            exit_buckets[t_close[:16]].append(pnl)
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

    for bucket, vals in exit_buckets.items():
        if len(vals) >= 3:
            same_exit_clusters += 1

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gp / gl) if gl > 0 else None
    wr = (100.0 * len(wins) / len(pnls)) if pnls else None

    # martingale hint: lots increase after losses
    lot_jumps = 0
    if len(lots) >= 5:
        for i in range(1, len(lots)):
            if lots[i] >= 1.8 * lots[i - 1] and lots[i - 1] > 0:
                lot_jumps += 1

    # fixed SL hint: many losses near same magnitude
    loss_mag = Counter(round(abs(p), 1) for p in losses)
    top_loss_bucket = loss_mag.most_common(1)[0] if loss_mag else (None, 0)

    flags = []
    if same_exit_clusters >= 3:
        flags.append("grid_like_same_exit_clusters")
    if lot_jumps >= 5:
        flags.append("lot_scale_up_martingale_hint")
    if wr and wr >= 70 and pf and pf < 1.4:
        flags.append("high_wr_thin_pf")
    if holds_min and (sum(holds_min) / len(holds_min)) < 30:
        flags.append("ultra_short_holds")
    if top_loss_bucket[1] >= max(5, 0.15 * len(losses or [1])):
        flags.append(f"fixed_sl_cluster~{top_loss_bucket[0]}")

    med_hold = sorted(holds_min)[len(holds_min) // 2] if holds_min else None
    return {
        "file": path.name,
        "n_trades": len(rows),
        "n_with_pnl": len(pnls),
        "winrate_pct": wr,
        "profit_factor": pf,
        "net_pnl": sum(pnls) if pnls else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "median_hold_min": med_hold,
        "top_symbols": symbols.most_common(8),
        "open_minute_mod_top": open_min_mod.most_common(5),
        "same_exit_clusters_ge3": same_exit_clusters,
        "lot_jumps_ge1_8x": lot_jumps,
        "flags": flags,
    }


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    (HERE / "mql5_crawl").mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX.glob("*.csv")) + sorted(INBOX.glob("*.CSV"))
    if not files:
        OUT.write_text(
            "# Inbox vacío\n\nPoné CSVs exportados de MQL5 en `inbox_positions/`.\n",
            encoding="utf-8",
        )
        print("No CSVs in", INBOX)
        return 0
    results = [analyze(p) for p in files]
    lines = [
        "# Análisis inbox positions (CSV MQL5)",
        "",
        f"Archivos: {len(results)}",
        "",
    ]
    for r in results:
        lines += [
            f"## {r.get('file')}",
            "",
            f"- trades: {r.get('n_trades')} | WR: {r.get('winrate_pct')} | PF: {r.get('profit_factor')}",
            f"- net: {r.get('net_pnl')} | med hold min: {r.get('median_hold_min')}",
            f"- top symbols: {r.get('top_symbols')}",
            f"- open minute mods: {r.get('open_minute_mod_top')}",
            f"- flags: {r.get('flags')}",
            "",
        ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
