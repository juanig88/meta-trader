"""
Minería de patrones sobre CSVs MQL5 positions (carpeta positions_csv/).

Clasifica estrategias (grid / scalp / swing / mixed), calcula RR empírico,
ventanas horarias, alineación a velas, y shortlist NO-grid candidatos
aplicables a un bot con 1 posición / símbolo y riesgo acotado.

  python docs/mejoras/bots/mql5_pattern_mining.py
  python docs/mejoras/bots/mql5_pattern_mining.py --min-trades 80
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mql5_analyze_inbox import load_csv, _f  # noqa: E402

CRAWL = HERE / "mql5_crawl"
CSV_DIR = CRAWL / "positions_csv"
JSONL = CRAWL / "signals_public.jsonl"
OUT_JSON = CRAWL / "pattern_mining.json"
OUT_MD = CRAWL / "pattern_mining_report.md"
OUT_APPLICABLE = CRAWL / "applicable_candidates.json"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ys[int(k)]
    return ys[f] * (c - k) + ys[c] * (k - f)


def _norm_sym(s: str) -> str:
    s = (s or "").upper().strip()
    s = re.sub(r"[^A-Z0-9]", "", s)
    for suf in ("USCENT", "RAW", "ECN", "VIP", "STD", "CASH", "ZERO", "LMX", "XM", "VX", "I", "M", "S", "C"):
        if s.endswith(suf) and len(s) > len(suf) + 3:
            s = s[: -len(suf)]
    # gold variants
    if "XAU" in s:
        return "XAUUSD"
    if "BTC" in s:
        return "BTCUSD"
    if "ETH" in s and "USD" in s:
        return "ETHUSD"
    return s


def analyze_deep(path: Path, meta: dict | None = None) -> dict:
    rows = load_csv(path)
    sid = int(re.search(r"(\d+)", path.stem).group(1)) if re.search(r"(\d+)", path.stem) else 0
    if not rows:
        return {"signal_id": sid, "file": path.name, "error": "empty"}

    opens: list[datetime] = []
    holds: list[float] = []
    pnls: list[float] = []
    lots: list[float] = []
    sides: list[str] = []
    symbols: list[str] = []
    pairs: list[tuple[datetime, datetime]] = []
    hour_c: Counter[int] = Counter()
    dow_c: Counter[int] = Counter()
    min_mod: Counter[int] = Counter()
    exit_bucket: dict[str, int] = defaultdict(int)
    open_bucket: dict[str, int] = defaultdict(int)

    for r in rows:
        dto = _parse_dt(r.get("open time") or r.get("time"))
        dtc = _parse_dt(r.get("close time") or r.get("close"))
        pnl = _f(r.get("profit"))
        lot = _f(r.get("volume"))
        side = (r.get("type") or "").strip().lower()
        sym = _norm_sym(r.get("symbol") or "")
        if sym in ("", "?", "BALANCE", "CREDIT"):
            continue
        if pnl is None:
            continue
        symbols.append(sym)
        pnls.append(pnl)
        if lot is not None:
            lots.append(lot)
        if side in ("buy", "sell"):
            sides.append(side)
        if dto:
            opens.append(dto)
            hour_c[dto.hour] += 1
            dow_c[dto.weekday()] += 1
            min_mod[dto.minute] += 1
            open_bucket[dto.strftime("%Y.%m.%d %H:%M")] += 1
        if dto and dtc:
            pairs.append((dto, dtc))
            holds.append(max(0.0, (dtc - dto).total_seconds() / 60.0))
            exit_bucket[dtc.strftime("%Y.%m.%d %H:%M")] += 1

    n = len(pnls)
    if n < 5:
        return {"signal_id": sid, "file": path.name, "n_trades": n, "error": "too_few"}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins)
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gp / gl) if gl > 0 else None
    wr = 100.0 * len(wins) / n
    avg_w = statistics.mean(wins) if wins else None
    avg_l = statistics.mean(losses) if losses else None
    # payoff ratio |avg_win|/|avg_loss|
    rr = (abs(avg_w) / abs(avg_l)) if avg_w and avg_l and avg_l != 0 else None
    # expectancy per trade
    exp = statistics.mean(pnls)

    lot_jumps = 0
    for i in range(1, len(lots)):
        if lots[i - 1] > 0 and lots[i] >= 1.8 * lots[i - 1]:
            lot_jumps += 1
    lot_cv = (statistics.pstdev(lots) / statistics.mean(lots)) if len(lots) >= 5 and statistics.mean(lots) > 0 else 0.0

    exit_clusters = sum(1 for v in exit_bucket.values() if v >= 3)
    open_clusters = sum(1 for v in open_bucket.values() if v >= 3)
    max_same_exit = max(exit_bucket.values()) if exit_bucket else 0
    max_same_open = max(open_bucket.values()) if open_bucket else 0

    # approx max concurrent: sweep open/close events
    max_concurrent = 0
    if pairs:
        ev = []
        for o, c in pairs:
            ev.append((o, 1))
            ev.append((c, -1))
        ev.sort(key=lambda x: (x[0], x[1]))
        cur = 0
        for _, d in ev:
            cur += d
            if cur > max_concurrent:
                max_concurrent = cur

    med_hold = statistics.median(holds) if holds else None
    p90_hold = _pct(holds, 0.9)
    # bar alignment: fraction of opens on :00/:15/:30/:45
    if opens:
        align15 = sum(1 for o in opens if o.minute in (0, 15, 30, 45)) / len(opens)
        align5 = sum(1 for o in opens if o.minute % 5 == 0) / len(opens)
    else:
        align15 = align5 = 0.0

    # top hours (share)
    top_hours = hour_c.most_common(5)
    hour_conc = (top_hours[0][1] / n) if top_hours else 0.0
    # best 4-hour window by open count
    best_window = None
    best_window_n = 0
    for start in range(24):
        cnt = sum(hour_c[(start + k) % 24] for k in range(4))
        if cnt > best_window_n:
            best_window_n = cnt
            best_window = (start, (start + 4) % 24, cnt / n if n else 0)

    sym_c = Counter(symbols)
    top_sym, top_sym_n = sym_c.most_common(1)[0]
    sym_conc = top_sym_n / n
    buy_share = (sides.count("buy") / len(sides)) if sides else None

    # classification
    flags = []
    style = "mixed"
    if exit_clusters >= 3 or max_same_exit >= 5 or max_concurrent >= 4:
        flags.append("grid_basket")
    if lot_jumps >= 5 or lot_cv > 1.2:
        flags.append("martingale_lots")
    if med_hold is not None and med_hold < 20:
        flags.append("scalp_holds")
    if med_hold is not None and med_hold > 240:
        flags.append("swing_holds")
    if wr >= 75 and rr is not None and rr < 0.6:
        flags.append("high_wr_bad_rr")
    if align15 >= 0.55:
        flags.append("m15_bar_aligned")
    if align5 >= 0.7 and align15 < 0.55:
        flags.append("m5_bar_aligned")

    if "grid_basket" in flags or "martingale_lots" in flags:
        style = "grid_or_martingale"
    elif "scalp_holds" in flags and max_concurrent <= 2:
        style = "scalp_single"
    elif "swing_holds" in flags and max_concurrent <= 2:
        style = "swing_single"
    elif max_concurrent <= 2 and med_hold is not None and 20 <= med_hold <= 240:
        style = "intraday_single"

    # applicable to our bot constraints?
    applicable = (
        style in ("scalp_single", "intraday_single", "swing_single")
        and pf is not None
        and pf >= 1.25
        and n >= 80
        and max_concurrent <= 2
        and "martingale_lots" not in flags
        and "grid_basket" not in flags
        and wr < 92  # ultra WR often hidden grid
    )
    # tier-2: un poco más permisivo (para shortlist de estudio)
    soft_applicable = (
        pf is not None
        and pf >= 1.2
        and n >= 80
        and max_concurrent <= 3
        and "martingale_lots" not in flags
        and lot_jumps < 5
        and wr < 95
        and style != "grid_or_martingale"
    )

    title = (meta or {}).get("title")
    pub_pf = (meta or {}).get("profit_factor")
    pub_dd = (meta or {}).get("relative_dd_equity_pct")

    return {
        "signal_id": sid,
        "file": path.name,
        "title": title,
        "url": f"https://www.mql5.com/en/signals/{sid}",
        "n_trades": n,
        "winrate_pct": round(wr, 2),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "pub_profit_factor": pub_pf,
        "pub_dd_equity_pct": pub_dd,
        "expectancy": round(exp, 4),
        "avg_win": round(avg_w, 4) if avg_w is not None else None,
        "avg_loss": round(avg_l, 4) if avg_l is not None else None,
        "rr_avg": round(rr, 3) if rr is not None else None,
        "median_hold_min": round(med_hold, 2) if med_hold is not None else None,
        "p90_hold_min": round(p90_hold, 2) if p90_hold is not None else None,
        "max_concurrent": max_concurrent,
        "exit_clusters_ge3": exit_clusters,
        "open_clusters_ge3": open_clusters,
        "max_same_exit": max_same_exit,
        "lot_jumps_ge1_8x": lot_jumps,
        "lot_cv": round(lot_cv, 3),
        "align15": round(align15, 3),
        "align5": round(align5, 3),
        "top_symbol": top_sym,
        "symbol_concentration": round(sym_conc, 3),
        "n_symbols": len(sym_c),
        "buy_share": round(buy_share, 3) if buy_share is not None else None,
        "top_hours": top_hours,
        "hour_top_share": round(hour_conc, 3),
        "best_4h_window": {
            "start_hour": best_window[0] if best_window else None,
            "end_hour": best_window[1] if best_window else None,
            "share": round(best_window[2], 3) if best_window else None,
        },
        "dow_counts": dict(dow_c),
        "open_minute_top": min_mod.most_common(6),
        "style": style,
        "flags": flags,
        "applicable": applicable,
        "soft_applicable": soft_applicable,
        "net_pnl": round(sum(pnls), 2),
    }


def load_meta() -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not JSONL.exists():
        return out
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            out[int(r["signal_id"])] = r
        except Exception:
            continue
    return out


def write_report(rows: list[dict], min_trades: int) -> None:
    ok = [r for r in rows if not r.get("error") and (r.get("n_trades") or 0) >= min_trades]
    by_style = Counter(r["style"] for r in ok)
    appl = [r for r in ok if r.get("applicable")]
    appl = sorted(appl, key=lambda r: (-(r.get("profit_factor") or 0), -(r.get("n_trades") or 0)))
    soft = [r for r in ok if r.get("soft_applicable") and not r.get("applicable")]
    soft = sorted(soft, key=lambda r: (-(r.get("profit_factor") or 0), -(r.get("n_trades") or 0)))

    # cross patterns among applicable
    hour_votes: Counter[int] = Counter()
    sym_votes: Counter[str] = Counter()
    align15_vals = []
    rr_vals = []
    for r in appl:
        for h, c in r.get("top_hours") or []:
            hour_votes[h] += c
        sym_votes[r.get("top_symbol") or "?"] += 1
        if r.get("align15") is not None:
            align15_vals.append(r["align15"])
        if r.get("rr_avg") is not None:
            rr_vals.append(r["rr_avg"])

    # grids for contrast
    grids = [r for r in ok if r["style"] == "grid_or_martingale"]
    grid_share = len(grids) / len(ok) if ok else 0

    # XAU specialists non-grid
    xau_app = [r for r in appl if "XAU" in str(r.get("top_symbol"))]
    btc_app = [r for r in appl if "BTC" in str(r.get("top_symbol"))]

    lines = [
        "# MQL5 Positions — Pattern Mining (exhaustivo)",
        "",
        f"- CSVs analizados (filtrados n≥{min_trades}): **{len(ok)}** / raw {len(rows)}",
        f"- Estilos: {dict(by_style)}",
        f"- Share grid/martingale: **{grid_share:.1%}** ({len(grids)})",
        f"- Candidatos aplicables (1–2 pos, PF≥1.25, sin grid/martingale): **{len(appl)}**",
        "",
        "## Qué NO copiar (riesgo)",
        "",
        "- La mayoría del ranking público es **grid / basket / martingale** (WR alto, muchos cierres simultáneos, lotes escalados).",
        "- PF público inflado + DD equity bajo en la ficha a menudo **no** sobrevive un stop de cuenta real.",
        "- Ultra-WR (>92%) con holds cortos suele ser hedged grid o scalp de micro-TP.",
        "",
        "## Patrones transferibles (si aparecen en no-grid)",
        "",
    ]
    if appl:
        lines += [
            f"- Horas más frecuentes (entre aplicables): {hour_votes.most_common(8)}",
            f"- Símbolos dominantes (aplicables): {sym_votes.most_common(12)}",
            f"- Align M15 medio: {statistics.mean(align15_vals):.2f}" if align15_vals else "- Align M15: n/a",
            f"- RR empírico medio (|AW|/|AL|): {statistics.mean(rr_vals):.2f}" if rr_vals else "- RR: n/a",
            f"- XAU aplicables: {len(xau_app)} | BTC aplicables: {len(btc_app)}",
            "",
        ]
    else:
        lines += ["- Aún pocos no-grid con muestra; ampliar descarga o bajar umbrales.", ""]

    lines += [
        "## Shortlist aplicable (priorizar deep-dive / forward demo)",
        "",
        "| ID | Título | Style | n | WR | PF | RR | med hold | max pos | Top sym | Align15 | Ventana 4h |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for r in appl[:40]:
        w = r.get("best_4h_window") or {}
        win = f"{w.get('start_hour')}-{w.get('end_hour')} ({w.get('share')})"
        lines.append(
            f"| [{r['signal_id']}]({r['url']}) | {r.get('title') or '—'} | {r['style']} | {r['n_trades']} | "
            f"{r.get('winrate_pct')} | {r.get('profit_factor')} | {r.get('rr_avg')} | {r.get('median_hold_min')} | "
            f"{r.get('max_concurrent')} | {r.get('top_symbol')} | {r.get('align15')} | {win} |"
        )

    lines += [
        "",
        f"## Soft shortlist (estudio, n={len(soft)})",
        "",
        "| ID | Style | n | WR | PF | RR | med hold | max pos | Top sym | Ventana 4h |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in soft[:25]:
        w = r.get("best_4h_window") or {}
        win = f"{w.get('start_hour')}-{w.get('end_hour')} ({w.get('share')})"
        lines.append(
            f"| [{r['signal_id']}]({r['url']}) | {r['style']} | {r['n_trades']} | "
            f"{r.get('winrate_pct')} | {r.get('profit_factor')} | {r.get('rr_avg')} | "
            f"{r.get('median_hold_min')} | {r.get('max_concurrent')} | {r.get('top_symbol')} | {win} |"
        )

    # recommendations for our bot
    lines += [
        "",
        "## Implicaciones para `bot_mt5` (acciones concretas)",
        "",
        "1. **Filtro anti-grid en research**: ignorar signals con max_concurrent≥3 o lot_jumps altos — no intentar clonar su curva.",
        "2. **Ventanas horarias**: si los aplicables concentran opens, alinear `TRADING_WINDOWS_*` a esas horas (broker time), no a growth marketing.",
        "3. **Alineación a vela**: si `align15` alto en no-grid → confirmar entradas en close M15 (ya cercano a nuestro diseño).",
        "4. **RR objetivo**: usar mediana de `rr_avg` de aplicables (≥~1.5–2) con ATR SL; evitar perfiles WR>80 con RR<0.5.",
        "5. **Un símbolo dominante**: preferir specialists (XAU/BTC/EUR) sobre portfolios multi-asset tipo 2278370.",
        "6. **Forward test**: tomar top 5–10 aplicables y paper/demo en paralelo con kill-switch; no optimizar IS sobre sus CSV.",
        "",
        "## Top grids (solo para estudio / no clonar)",
        "",
        "| ID | n | WR | PF | max_pos | lot_jumps | Top sym |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    grids_sorted = sorted(grids, key=lambda r: (-(r.get("n_trades") or 0),))[:15]
    for r in grids_sorted:
        lines.append(
            f"| [{r['signal_id']}]({r['url']}) | {r['n_trades']} | {r.get('winrate_pct')} | "
            f"{r.get('profit_factor')} | {r.get('max_concurrent')} | {r.get('lot_jumps_ge1_8x')} | {r.get('top_symbol')} |"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_APPLICABLE.write_text(json.dumps(appl[:80], indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-trades", type=int, default=60)
    ap.add_argument("--dir", type=str, default=str(CSV_DIR))
    args = ap.parse_args()

    d = Path(args.dir)
    files = sorted(d.glob("*.positions.csv")) + sorted(d.glob("*.csv"))
    # dedupe paths
    files = sorted(set(files), key=lambda p: p.name)
    if not files:
        print("No CSVs in", d)
        return 1

    meta = load_meta()
    rows = []
    for i, p in enumerate(files, 1):
        m = meta.get(int(re.search(r"(\d+)", p.stem).group(1))) if re.search(r"(\d+)", p.stem) else None
        rows.append(analyze_deep(p, m))
        if i % 50 == 0:
            print(f"analyzed {i}/{len(files)}")

    OUT_JSON.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    write_report(rows, args.min_trades)
    appl_n = sum(1 for r in rows if r.get("applicable"))
    print(f"Wrote {OUT_MD} | signals={len(rows)} applicable={appl_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
