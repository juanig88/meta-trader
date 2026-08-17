"""
Crawl público de MQL5 Signals (sin login).

- Lista signals desde /en/signals/mt5 (y páginas relacionadas embebidas en HTML).
- Baja cada /en/signals/{id} y extrae métricas + distribución por símbolo.
- NO puede bajar /export/positions (requiere auth) — eso va a mql5_import_positions.py
  cuando el usuario deje CSVs en inbox/.

Uso:
  python docs/mejoras/bots/mql5_signals_public_crawl.py
  python docs/mejoras/bots/mql5_signals_public_crawl.py --max-signals 80 --sleep 1.2
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "mql5_crawl"
INBOX = HERE / "inbox_positions"
RESULTS = OUT_DIR / "signals_public.jsonl"
REPORT = OUT_DIR / "signals_public_report.md"
SEEDS_FILE = OUT_DIR / "seeds.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch(url: str, timeout: float = 30.0) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.mql5.com/en/signals/mt5",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
        return int(r.status), str(r.geturl()), body


def fetch_retry(url: str, sleep_base: float, max_tries: int = 6) -> tuple[int, str, str]:
    """Backoff largo en 403/429/cortes de conexión (MQL5 WAF/rate-limit)."""
    last_err: Exception | None = None
    for attempt in range(1, max_tries + 1):
        try:
            return fetch(url)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (403, 429, 503):
                raise
            wait = min(600.0, sleep_base * (2 ** (attempt - 1)) * 8)
            print(f"  rate-limit {e.code} on {url} — sleep {wait:.0f}s (try {attempt}/{max_tries})")
            time.sleep(wait)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = e
            wait = min(300.0, sleep_base * 6 * attempt)
            print(f"  network err {e} — sleep {wait:.0f}s (try {attempt}/{max_tries})")
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def extract_signal_ids(html: str) -> list[int]:
    ids = {int(x) for x in re.findall(r"/signals/(\d{5,})", html)}
    return sorted(ids)


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    t = unescape(s).replace("\xa0", " ").strip()
    t = t.replace(",", "")
    # 3 442.89 or 20 043%
    t = re.sub(r"\s+", "", t)
    t = t.replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_signal_page(signal_id: int, html: str, final_url: str) -> dict:
    title = None
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    def grab_label(label: str) -> float | None:
        # Trades:</div> ... 758
        pat = rf"{re.escape(label)}\s*:?\s*</[^>]+>\s*<[^>]+>\s*([0-9\s.,%-]+)"
        mm = re.search(pat, html, re.I)
        if mm:
            return _num(mm.group(1))
        # fallback looser
        mm = re.search(rf"{re.escape(label)}[^0-9-]{{0,80}}(-?\d[\d\s.,]*)", html, re.I)
        return _num(mm.group(1)) if mm else None

    # Profit trades often "443 (58.44%)"
    wr = None
    mm = re.search(r"Profit Trades:\s*</[^>]+>\s*<[^>]+>\s*\d+\s*\(([\d.]+)\s*%\)", html, re.I)
    if mm:
        wr = float(mm.group(1))
    else:
        mm = re.search(r"\((\d+(?:\.\d+)?)\s*%\)\s*</", html)
        # weaker — skip

    # Symbol distribution table rows: | EURUSD | 385 |
    sym_counts: dict[str, int] = {}
    for sym, n in re.findall(
        r"<t[dh][^>]*>\s*([A-Z]{3,}(?:USD|EUR|JPY|GBP|AUD|CAD|CHF|NZD|BTC|ETH|XAU|XAG|US\d+)?[A-Z0-9.+-]*)\s*</t[dh]>\s*<t[dh][^>]*>\s*(\d+)\s*</t[dh]>",
        html,
        re.I,
    ):
        sym_u = sym.upper()
        if sym_u in ("SYMBOL", "DEALS", "SELL", "BUY"):
            continue
        if len(sym_u) < 6 and not any(x in sym_u for x in ("USD", "EUR", "JPY", "XAU", "BTC", "US")):
            continue
        sym_counts[sym_u] = int(n)

    # Simpler fallback: markdown-like from converted pages sometimes keep Symbol rows
    if not sym_counts:
        for sym, n in re.findall(r"\|\s*([A-Z0-9.+-]{6,})\s*\|\s*(\d+)\s*\|", html):
            if sym.lower() in ("symbol",):
                continue
            sym_counts[sym] = int(n)

    growth = None
    mm = re.search(r"growth since[^<]{0,40}?([\d\s]+%)", html, re.I)
    if mm:
        growth = _num(mm.group(1))

    rel_dd = grab_label("Relative drawdown") or grab_label("By Equity")
    # Prefer equity DD near "By Equity"
    mm = re.search(r"By Equity:\s*</[^>]+>\s*<[^>]+>\s*([\d.]+)\s*%", html, re.I)
    if mm:
        rel_dd = float(mm.group(1))

    bal_dd = None
    mm = re.search(r"By Balance:\s*</[^>]+>\s*<[^>]+>\s*([\d.]+)\s*%", html, re.I)
    if mm:
        bal_dd = float(mm.group(1))

    row = {
        "signal_id": signal_id,
        "url": f"https://www.mql5.com/en/signals/{signal_id}",
        "final_url": final_url,
        "title": title,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "trades": grab_label("Trades"),
        "winrate_pct": wr,
        "profit_factor": grab_label("Profit Factor"),
        "expected_payoff": grab_label("Expected Payoff"),
        "avg_holding": None,
        "trades_per_week": grab_label("Trades per week"),
        "trading_activity_pct": grab_label("Trading activity"),
        "algo_trading_pct": grab_label("Algo trading"),
        "monthly_growth_pct": grab_label("Monthly growth"),
        "growth_since_pct": growth,
        "max_deposit_load_pct": grab_label("Max deposit load"),
        "recovery_factor": grab_label("Recovery Factor"),
        "sharpe": grab_label("Sharpe Ratio"),
        "relative_dd_equity_pct": rel_dd,
        "relative_dd_balance_pct": bal_dd,
        "long_trades": grab_label("Long Trades"),
        "short_trades": grab_label("Short Trades"),
        "symbol_deals": sym_counts,
        "top_symbol": (max(sym_counts, key=sym_counts.get) if sym_counts else None),
        "n_symbols": len(sym_counts),
        "export_positions_url": f"https://www.mql5.com/en/signals/{signal_id}/export/positions",
        "csv_requires_login": True,
    }
    mm = re.search(r"Avg holding time:\s*</[^>]+>\s*<[^>]+>\s*([^<]+)", html, re.I)
    if mm:
        row["avg_holding"] = unescape(mm.group(1)).strip()
    return row


def score_row(r: dict) -> float:
    """Score heurístico research (no aprobación). Prioriza PF, actividad baja-media, DD acotado, N trades."""
    pf = float(r.get("profit_factor") or 0)
    wr = float(r.get("winrate_pct") or 0)
    dd = float(r.get("relative_dd_equity_pct") or r.get("relative_dd_balance_pct") or 99)
    n = float(r.get("trades") or 0)
    act = float(r.get("trading_activity_pct") or 50)
    # penalizar DD alto y activity casi 100% (libros always-in)
    score = (
        2.0 * min(pf, 3.0)
        + 0.02 * min(wr, 80)
        + 0.002 * min(n, 2000)
        - 0.05 * dd
        - (0.03 * act if act > 80 else 0)
    )
    if r.get("top_symbol") and "XAU" in str(r.get("top_symbol")):
        score += 0.3
    if r.get("top_symbol") and "BTC" in str(r.get("top_symbol")):
        score += 0.2
    return score


def write_report(rows: list[dict]) -> None:
    ok = [r for r in rows if r.get("trades")]
    ranked = sorted(ok, key=lambda r: -score_row(r))
    lines = [
        "# MQL5 Signals — crawl público",
        "",
        f"- Generado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Signals parseadas: {len(rows)} (con trades: {len(ok)})",
        "- CSV `/export/positions` **requiere login** → no descargado aquí.",
        f"- Dejá CSVs manuales en `{INBOX.as_posix()}` y corré `mql5_analyze_inbox.py`.",
        "",
        "## Top candidatos (heurística research)",
        "",
        "| # | ID | Título | Trades | WR% | PF | DD eq% | Act% | Top sym | Score |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for i, r in enumerate(ranked[:40], 1):
        lines.append(
            f"| {i} | [{r['signal_id']}]({r['url']}) | {r.get('title') or '—'} | "
            f"{int(r.get('trades') or 0)} | {float(r.get('winrate_pct') or 0):.1f} | "
            f"{float(r.get('profit_factor') or 0):.2f} | {float(r.get('relative_dd_equity_pct') or 0):.1f} | "
            f"{float(r.get('trading_activity_pct') or 0):.1f} | {r.get('top_symbol') or '—'} | "
            f"{score_row(r):.2f} |"
        )
    lines += [
        "",
        "## Notas risk-quant",
        "",
        "- Growth % alto + DD equity alto = sospecha grid/recovery.",
        "- Activity ~100% + hold días = libro swing / hold-loser (como 2278370).",
        "- Stats públicas no reemplazan CSV de positions para inferir grid/M15 timing.",
        "",
    ]
    # symbol popularity among crawled
    c: Counter[str] = Counter()
    for r in ok:
        for s, n in (r.get("symbol_deals") or {}).items():
            c[s] += int(n)
    lines += ["## Símbolos más operados (suma deals en sample)", ""]
    for s, n in c.most_common(20):
        lines.append(f"- `{s}`: {n}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def done_ids() -> set[int]:
    """Solo IDs parseados OK (errores 403 no cuentan — se reintentan)."""
    if not RESULTS.exists():
        return set()
    out: set[int] = set()
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("error"):
                continue
            if row.get("trades") is None and not row.get("title"):
                continue
            out.add(int(row["signal_id"]))
        except Exception:
            continue
    return out


def purge_error_rows() -> int:
    if not RESULTS.exists():
        return 0
    kept = []
    removed = 0
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            removed += 1
            continue
        if row.get("error"):
            removed += 1
            continue
        kept.append(line)
    RESULTS.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-signals", type=int, default=120)
    ap.add_argument("--sleep", type=float, default=1.25)
    ap.add_argument("--seed-ids", type=str, default="", help="comma ids extra")
    ap.add_argument("--skip-list", action="store_true", help="usar seeds.json sin re-listar")
    ap.add_argument("--max-page", type=int, default=49, help="última página listado (evitar 403)")
    ap.add_argument("--purge-errors", action="store_true", help="borrar filas error del jsonl")
    ap.add_argument("--cooldown", type=float, default=0.0, help="esperar N segundos antes de empezar")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)

    if args.purge_errors:
        n = purge_error_rows()
        print(f"purged {n} error rows")

    if args.cooldown > 0:
        print(f"cooldown {args.cooldown:.0f}s (rate-limit recovery)...")
        time.sleep(args.cooldown)

    seeds: set[int] = set()
    if args.seed_ids.strip():
        for p in args.seed_ids.split(","):
            p = p.strip()
            if p.isdigit():
                seeds.add(int(p))

    if args.skip_list and SEEDS_FILE.exists():
        seeds.update(int(x) for x in json.loads(SEEDS_FILE.read_text(encoding="utf-8")))
        print(f"loaded seeds.json -> {len(seeds)}")
    else:
        list_urls = [
            "https://www.mql5.com/en/signals/mt5",
            "https://www.mql5.com/en/signals/mt5/gold",
            "https://www.mql5.com/en/signals/mt5/crypto",
            "https://www.mql5.com/en/signals/mt5/forex",
            "https://www.mql5.com/en/signals/mt5/high-rating",
            "https://www.mql5.com/en/signals/mt5/subscribers-choice",
            "https://www.mql5.com/en/signals/mt5/trusted",
            "https://www.mql5.com/en/signals/mt5/live",
            "https://www.mql5.com/en/signals/mt5/list",
            "https://www.mql5.com/en/signals/mt5/currency-usd",
        ]
        for p in range(1, max(1, args.max_page) + 1):
            list_urls.append(f"https://www.mql5.com/en/signals/mt5/page{p}")

        for u in list_urls:
            try:
                status, final, html = fetch_retry(u, sleep_base=args.sleep)
                ids = extract_signal_ids(html)
                seeds.update(ids)
                print(f"list {u} -> {len(ids)} ids (status={status}) total_seeds={len(seeds)}")
            except Exception as e:
                print(f"list FAIL {u}: {e}")
            time.sleep(args.sleep)
        if seeds:
            SEEDS_FILE.write_text(json.dumps(sorted(seeds)), encoding="utf-8")
            print(f"wrote {SEEDS_FILE} ({len(seeds)} ids)")
        elif SEEDS_FILE.exists():
            prev = {int(x) for x in json.loads(SEEDS_FILE.read_text(encoding="utf-8"))}
            seeds.update(prev)
            print(f"list vacío — reusando seeds.json ({len(seeds)} ids)")
        else:
            print("WARNING: no seeds discovered and no seeds.json")

    # known from user research
    seeds.update({2234595, 2362973, 2381521, 2278370, 2329290})
    if len(seeds) > 5:  # no pisar un seeds.json rico con solo los 5 hardcodeados
        SEEDS_FILE.write_text(json.dumps(sorted(seeds)), encoding="utf-8")

    already = done_ids()
    todo = [i for i in sorted(seeds) if i not in already][: max(0, args.max_signals)]
    print(f"queue {len(todo)} / seeds {len(seeds)} / already {len(already)}")

    consecutive_soft_fail = 0
    for i, sid in enumerate(todo, 1):
        url = f"https://www.mql5.com/en/signals/{sid}"
        try:
            status, final, html = fetch_retry(url, sleep_base=args.sleep)
            if "auth_login" in final:
                print(f"[{i}/{len(todo)}] {sid} login-wall")
                row = {
                    "signal_id": sid,
                    "url": url,
                    "error": "login_wall",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                consecutive_soft_fail += 1
            else:
                row = parse_signal_page(sid, html, final)
                consecutive_soft_fail = 0
                print(
                    f"[{i}/{len(todo)}] {sid} {row.get('title')} "
                    f"n={row.get('trades')} PF={row.get('profit_factor')} top={row.get('top_symbol')}"
                )
        except Exception as e:
            # no persistir 403 permanentes: solo log, se reintenta en próxima corrida
            print(f"[{i}/{len(todo)}] {sid} ERR {e} (not saved)")
            consecutive_soft_fail += 1
            if consecutive_soft_fail >= 8:
                print("too many consecutive failures — aborting (try later with --cooldown)")
                break
            time.sleep(args.sleep * 3)
            continue
        with RESULTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        time.sleep(args.sleep)

    rows = []
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    write_report(rows)
    print("REPORT", REPORT)
    print("INBOX for CSVs:", INBOX)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
