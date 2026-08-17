"""
Agregación trade-level sobre positions_csv/ + meta pattern_mining.json.
Produce insights JSON + markdown útiles para bot_mt5.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mql5_analyze_inbox import load_csv, _f  # noqa: E402
from mql5_pattern_mining import _norm_sym, _parse_dt  # noqa: E402

CRAWL = HERE / "mql5_crawl"
CSV_DIR = CRAWL / "positions_csv"
META = CRAWL / "pattern_mining.json"
OUT_JSON = CRAWL / "exhaustive_insights.json"
OUT_MD = CRAWL / "exhaustive_insights.md"


def main() -> int:
    meta_rows = json.loads(META.read_text(encoding="utf-8")) if META.exists() else []
    meta_by_id = {int(r["signal_id"]): r for r in meta_rows if r.get("signal_id")}

    # trade-level aggregates
    sym_trades = Counter()
    sym_pnl = defaultdict(float)
    sym_wins = Counter()
    hour_trades = Counter()
    hour_pnl = defaultdict(float)
    dow_trades = Counter()
    dow_pnl = defaultdict(float)
    month_trades = Counter()
    month_pnl = defaultdict(float)

    # split by style
    style_of = {sid: r.get("style") or "unknown" for sid, r in meta_by_id.items()}
    hour_pnl_nongrid = defaultdict(float)
    hour_trades_nongrid = Counter()
    sym_pnl_nongrid = defaultdict(float)
    sym_trades_nongrid = Counter()
    hour_pnl_appl = defaultdict(float)
    hour_trades_appl = Counter()

    # per-signal quality
    signal_net = []
    files = sorted(CSV_DIR.glob("*.csv"))
    n_files = 0
    n_trades_total = 0
    n_parse_skip = 0

    for i, path in enumerate(files, 1):
        m = re.search(r"(\d+)", path.stem)
        sid = int(m.group(1)) if m else 0
        style = style_of.get(sid, "unknown")
        is_grid = style == "grid_or_martingale"
        is_appl = bool(meta_by_id.get(sid, {}).get("applicable"))
        rows = load_csv(path)
        n_files += 1
        sig_pnl = 0.0
        sig_n = 0
        for r in rows:
            sym = _norm_sym(r.get("symbol") or "")
            if sym in ("", "?", "BALANCE", "CREDIT"):
                n_parse_skip += 1
                continue
            pnl = _f(r.get("profit"))
            if pnl is None:
                continue
            dto = _parse_dt(r.get("open time") or r.get("time"))
            n_trades_total += 1
            sig_n += 1
            sig_pnl += pnl
            sym_trades[sym] += 1
            sym_pnl[sym] += pnl
            if pnl > 0:
                sym_wins[sym] += 1
            if dto:
                hour_trades[dto.hour] += 1
                hour_pnl[dto.hour] += pnl
                dow_trades[dto.weekday()] += 1
                dow_pnl[dto.weekday()] += pnl
                ym = f"{dto.year}-{dto.month:02d}"
                month_trades[ym] += 1
                month_pnl[ym] += pnl
                if not is_grid:
                    hour_pnl_nongrid[dto.hour] += pnl
                    hour_trades_nongrid[dto.hour] += 1
                    sym_pnl_nongrid[sym] += pnl
                    sym_trades_nongrid[sym] += 1
                if is_appl:
                    hour_pnl_appl[dto.hour] += pnl
                    hour_trades_appl[dto.hour] += 1
        if sig_n:
            signal_net.append(
                {
                    "signal_id": sid,
                    "n": sig_n,
                    "net": sig_pnl,
                    "style": style,
                    "applicable": is_appl,
                    "title": meta_by_id.get(sid, {}).get("title"),
                    "top_symbol": meta_by_id.get(sid, {}).get("top_symbol"),
                    "pf": meta_by_id.get(sid, {}).get("profit_factor"),
                }
            )
        if i % 200 == 0:
            print(f"scanned {i}/{len(files)}")

    # style stats from meta
    styles = Counter(r.get("style") for r in meta_rows if not r.get("error"))
    appl = [r for r in meta_rows if r.get("applicable")]
    nets = [s["net"] for s in signal_net]
    pos_sigs = sum(1 for x in nets if x > 0)
    neg_sigs = sum(1 for x in nets if x <= 0)
    # profit concentration: top 5% signals share of total positive pnl
    pos_only = sorted([x for x in nets if x > 0], reverse=True)
    total_pos = sum(pos_only) if pos_only else 0.0
    k = max(1, int(math.ceil(0.05 * len(pos_only)))) if pos_only else 1
    top5_share = (sum(pos_only[:k]) / total_pos) if total_pos else 0.0

    grid_nets = [s["net"] for s in signal_net if s["style"] == "grid_or_martingale"]
    nongrid_nets = [s["net"] for s in signal_net if s["style"] != "grid_or_martingale"]
    appl_nets = [s["net"] for s in signal_net if s["applicable"]]

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    # top symbols by trades and by pnl
    top_sym_trades = sym_trades.most_common(25)
    top_sym_pnl = sorted(sym_pnl.items(), key=lambda kv: -kv[1])[:25]
    worst_sym_pnl = sorted(sym_pnl.items(), key=lambda kv: kv[1])[:15]
    top_sym_nongrid = sorted(sym_pnl_nongrid.items(), key=lambda kv: -kv[1])[:20]

    # hour table
    hours = []
    for h in range(24):
        nt = hour_trades[h]
        hours.append(
            {
                "hour": h,
                "trades": nt,
                "pnl": round(hour_pnl[h], 2),
                "pnl_per_trade": round(hour_pnl[h] / nt, 4) if nt else None,
                "nongrid_trades": hour_trades_nongrid[h],
                "nongrid_pnl": round(hour_pnl_nongrid[h], 2),
                "appl_trades": hour_trades_appl[h],
                "appl_pnl": round(hour_pnl_appl[h], 2),
            }
        )

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dows = []
    for d in range(7):
        nt = dow_trades[d]
        dows.append(
            {
                "dow": dow_names[d],
                "trades": nt,
                "pnl": round(dow_pnl[d], 2),
                "pnl_per_trade": round(dow_pnl[d] / nt, 4) if nt else None,
            }
        )

    months = []
    for ym in sorted(month_trades.keys()):
        nt = month_trades[ym]
        months.append(
            {
                "month": ym,
                "trades": nt,
                "pnl": round(month_pnl[ym], 2),
                "pnl_per_trade": round(month_pnl[ym] / nt, 4) if nt else None,
            }
        )

    # applicable hour concentration + RR buckets
    appl_rr = [r.get("rr_avg") for r in appl if r.get("rr_avg") is not None]
    appl_good_rr = [r for r in appl if (r.get("rr_avg") or 0) >= 1.3]
    appl_bad_rr = [r for r in appl if (r.get("rr_avg") or 0) < 0.6]

    # best non-grid signals by expectancy-like net/n
    nongrid_ranked = sorted(
        [s for s in signal_net if s["style"] != "grid_or_martingale" and s["n"] >= 80],
        key=lambda s: -(s["net"] / s["n"]),
    )[:30]
    appl_ranked = sorted(
        [s for s in signal_net if s["applicable"] and s["n"] >= 60],
        key=lambda s: (-(s.get("pf") or 0), -(s["net"] / s["n"])),
    )[:30]

    # actionable windows: hours with positive nongrid pnl_per_trade and enough trades
    good_hours = []
    for h in hours:
        nt = h["nongrid_trades"]
        if nt >= 200 and h["nongrid_pnl"] > 0:
            ppt = h["nongrid_pnl"] / nt
            good_hours.append({**h, "nongrid_ppt": round(ppt, 4)})
    good_hours.sort(key=lambda x: -x["nongrid_ppt"])

    insights = {
        "summary": {
            "csv_files": n_files,
            "trades_parsed": n_trades_total,
            "signals_with_pnl": len(signal_net),
            "styles": dict(styles),
            "grid_share": styles.get("grid_or_martingale", 0) / max(1, sum(styles.values())),
            "signals_net_positive": pos_sigs,
            "signals_net_nonpositive": neg_sigs,
            "pct_signals_profitable": round(100 * pos_sigs / max(1, len(nets)), 2),
            "total_net_pnl_all_signals": round(sum(nets), 2),
            "mean_net_grid": round(mean(grid_nets) or 0, 2),
            "mean_net_nongrid": round(mean(nongrid_nets) or 0, 2),
            "mean_net_applicable": round(mean(appl_nets) or 0, 2),
            "top5pct_pos_signals_share_of_positive_pnl": round(top5_share, 3),
            "n_applicable": len(appl),
            "n_applicable_rr_ge_1_3": len(appl_good_rr),
            "n_applicable_rr_lt_0_6": len(appl_bad_rr),
            "mean_rr_applicable": round(mean(appl_rr) or 0, 3),
        },
        "top_symbols_by_trades": [{"symbol": s, "trades": n, "pnl": round(sym_pnl[s], 2), "wr": round(100 * sym_wins[s] / n, 2)} for s, n in top_sym_trades],
        "top_symbols_by_pnl": [{"symbol": s, "pnl": round(p, 2), "trades": sym_trades[s]} for s, p in top_sym_pnl],
        "worst_symbols_by_pnl": [{"symbol": s, "pnl": round(p, 2), "trades": sym_trades[s]} for s, p in worst_sym_pnl],
        "top_symbols_nongrid_pnl": [{"symbol": s, "pnl": round(p, 2), "trades": sym_trades_nongrid[s]} for s, p in top_sym_nongrid],
        "hours": hours,
        "best_nongrid_hours": good_hours[:12],
        "dows": dows,
        "months": months[-18:],
        "top_nongrid_by_expectancy": nongrid_ranked,
        "top_applicable": appl_ranked,
        "verdict": {},
    }

    # verdict narrative fields
    gold_trades = sym_trades.get("XAUUSD", 0) + sym_trades.get("GOLD", 0)
    btc_trades = sym_trades.get("BTCUSD", 0)
    insights["verdict"] = {
        "dominant_instrument": "XAUUSD/GOLD",
        "gold_trade_share": round(gold_trades / max(1, n_trades_total), 3),
        "btc_trade_share": round(btc_trades / max(1, n_trades_total), 3),
        "profit_is_real_but_skewed": top5_share >= 0.4,
        "mostly_grid": insights["summary"]["grid_share"] >= 0.7,
        "recommended_focus": [
            "XAUUSD single-position scalp/intraday in London-NY overlap hours with RR>=1.5",
            "Avoid cloning high-WR grid curves",
            "Prefer applicable signals with rr_avg>=1.3 over high WR alone",
        ],
    }

    OUT_JSON.write_text(json.dumps(insights, indent=2), encoding="utf-8")

    s = insights["summary"]
    lines = [
        "# MQL5 Exhaustive Insights — qué es realmente útil",
        "",
        f"- Archivos CSV: **{s['csv_files']}** | trades parseados: **{s['trades_parsed']:,}**",
        f"- Signals con PnL: **{s['signals_with_pnl']}** | rentables (net>0): **{s['pct_signals_profitable']}%**",
        f"- Share grid/martingale: **{s['grid_share']:.1%}**",
        f"- Net PnL agregado (todas las curves, unidades nativas mezcladas): **{s['total_net_pnl_all_signals']:,}**",
        f"- Media net grid vs no-grid: **{s['mean_net_grid']}** vs **{s['mean_net_nongrid']}**",
        f"- Concentración: top 5% signals positivos aportan **{s['top5pct_pos_signals_share_of_positive_pnl']:.0%}** del PnL positivo",
        "",
        "## ¿Hay ganancias reales?",
        "",
        f"Sí en el sentido contable del export: ~{s['pct_signals_profitable']}% de signals cierran net>0 en el histórico exportado. "
        f"Pero **no es edge clonable**: {s['grid_share']:.0%} son grid/martingale; el PnL positivo está muy concentrado "
        f"(top 5% ≈ {s['top5pct_pos_signals_share_of_positive_pnl']:.0%}). Mezclar monedas/cuentas hace el total agregado solo orientativo.",
        "",
        f"Entre **aplicables** (sin grid, 1–2 pos): n={s['n_applicable']}, RR medio={s['mean_rr_applicable']}, "
        f"con RR≥1.3: {s['n_applicable_rr_ge_1_3']}, con RR<0.6 (high-WR frágil): {s['n_applicable_rr_lt_0_6']}.",
        "",
        "## Instrumentos más operados (todos los trades)",
        "",
        "| Symbol | Trades | PnL sum | WR% |",
        "|---|---:|---:|---:|",
    ]
    for row in insights["top_symbols_by_trades"][:15]:
        lines.append(f"| {row['symbol']} | {row['trades']} | {row['pnl']} | {row['wr']} |")

    lines += [
        "",
        "### PnL no-grid por símbolo (más limpio para nosotros)",
        "",
        "| Symbol | Trades | PnL |",
        "|---|---:|---:|",
    ]
    for row in insights["top_symbols_nongrid_pnl"][:12]:
        lines.append(f"| {row['symbol']} | {row['trades']} | {row['pnl']} |")

    lines += [
        "",
        "## Horarios (hora del broker del signal = open time del CSV)",
        "",
        "### Mejores horas no-grid (ppt = pnl/trade, ≥200 trades)",
        "",
        "| Hour | nongrid trades | nongrid pnl | ppt |",
        "|---:|---:|---:|---:|",
    ]
    for h in insights["best_nongrid_hours"]:
        lines.append(f"| {h['hour']:02d} | {h['nongrid_trades']} | {h['nongrid_pnl']} | {h['nongrid_ppt']} |")

    lines += [
        "",
        "### Perfil 24h (todos)",
        "",
        "| Hour | trades | pnl | ppt | nongrid_pnl |",
        "|---:|---:|---:|---:|---:|",
    ]
    for h in hours:
        lines.append(
            f"| {h['hour']:02d} | {h['trades']} | {h['pnl']} | {h['pnl_per_trade']} | {h['nongrid_pnl']} |"
        )

    lines += [
        "",
        "## Días de semana",
        "",
        "| Dow | trades | pnl | ppt |",
        "|---|---:|---:|---:|",
    ]
    for d in dows:
        lines.append(f"| {d['dow']} | {d['trades']} | {d['pnl']} | {d['pnl_per_trade']} |")

    lines += [
        "",
        "## Últimos meses (actividad / pnl agregado)",
        "",
        "| Month | trades | pnl |",
        "|---|---:|---:|",
    ]
    for m in insights["months"]:
        lines.append(f"| {m['month']} | {m['trades']} | {m['pnl']} |")

    lines += [
        "",
        "## A qué se debe (interpretación risk-quant)",
        "",
        "1. **XAU domina** el universo rentable público: liquidez + volatilidad intradía + marketing de EAs gold.",
        "2. **Grids fabrican WR y curvas suaves** hasta el evento de cola; por eso el ranking MQL5 engaña.",
        "3. **Overlap Londres–NY (~14–18 en muchos brokers)** concentra actividad y ppt no-grid; Asia (~1–5) aparece en scalps gold de micro-hold.",
        "4. **Muchos 'scalps aplicables' tienen RR<0.6**: ganan seguido, pierden fuerte — frágil bajo costos reales IC Markets.",
        "5. El subconjunto útil es **single-entry XAU/EUR/JPY con RR≥1.3**, no clonar el top growth.",
        "",
        "## Acciones concretas para `bot_mt5`",
        "",
        "1. Mantener **XAU** como núcleo; BTC/EUR solo como tracks satélite con ventanas propias.",
        "2. Priorizar ventanas **14–18** (y validar offset broker vs ART) para gold scalp/intraday.",
        "3. Exigir **RR≥1.5 ATR** en entradas; descartar perfiles tipo WR>80 & RR<0.5 aunque el signal 'aplique'.",
        "4. Deep-dive CSV de aplicables con RR≥1.3 (lista abajo) → hours + minute alignment → mirror en config.",
        "5. No implementar grid/martingale aunque el PnL histórico sea enorme.",
        "",
        "## Top aplicables (para deep-dive)",
        "",
        "| ID | Title | n | net | PF | top sym |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for srow in appl_ranked[:20]:
        lines.append(
            f"| {srow['signal_id']} | {srow.get('title') or '—'} | {srow['n']} | "
            f"{round(srow['net'],2)} | {srow.get('pf')} | {srow.get('top_symbol')} |"
        )

    lines += [
        "",
        "## Top no-grid por expectancy (net/trade)",
        "",
        "| ID | Style | n | net | net/trade | top sym |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for srow in nongrid_ranked[:15]:
        lines.append(
            f"| {srow['signal_id']} | {srow['style']} | {srow['n']} | {round(srow['net'],2)} | "
            f"{round(srow['net']/srow['n'],4)} | {srow.get('top_symbol')} |"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote", OUT_MD)
    print("summary", json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
