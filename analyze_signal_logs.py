#!/usr/bin/env python3
"""
Solo lectura: resume eventos SIGNAL de los logs del bot (trades.log, logs/trades_*.log).

Para cada línea con decision NONE, deduce el motivo principal usando la misma lógica que
generate_signal() en bot_mt5.py (ADX, luego alineación M15 / EMA / H4).

No modifica el bot ni toca MT5.

Por defecto deduplica por (symbol, ts) para no inflar totales cuando el mismo intervalo
aparece en trades.log y en varios trades_*.log rotados.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from strategy_core import symbol_uses_crypto_relaxed_m15

# Símbolos que suelen tratarse como crypto en este proyecto (ajustá si agregás pares).
DEFAULT_CRYPTO = frozenset({"BTCUSD", "ETHUSD"})


def _default_log_paths(project_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in (project_root / "trades.log",):
        if p.is_file():
            out.append(p)
    logs_dir = project_root / "logs"
    if logs_dir.is_dir():
        out.extend(sorted(logs_dir.glob("trades_*.log")))
    return out


def iter_signal_events(paths: Iterable[Path]) -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in paths:
        if not path.is_file():
            continue
        try:
            f = path.open(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"Omitido (lectura): {path}: {e}", file=sys.stderr)
            continue
        with f:
            for line in f:
                if '"event": "SIGNAL"' not in line and "'event': 'SIGNAL'" not in line:
                    continue
                idx = line.find("{")
                if idx < 0:
                    continue
                blob = line[idx:].strip()
                try:
                    ev = json.loads(blob)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") != "SIGNAL":
                    continue
                yield path, ev


def classify_none_reason(ev: dict[str, Any]) -> str:
    """Motivo predominante para decision NONE, alineado con generate_signal()."""
    sym = str(ev.get("symbol") or "").strip()
    crd = ev.get("crypto_sell_requires_h4_down")
    require_crypto_h4_down = True if crd is None else bool(crd)

    adx = ev.get("adx")
    adx_min = float(ev.get("adx_min") or 0.0)
    if adx is None:
        return "adx_missing"
    try:
        adx_f = float(adx)
    except (TypeError, ValueError):
        return "adx_missing"
    if adx_f < adx_min:
        return "adx_below_min"

    signal_m15 = ev.get("signal_m15")
    ema_dir = ev.get("ema_dir")
    trend_h4 = ev.get("trend_h4")

    if signal_m15 == "NONE" or signal_m15 is None:
        return "no_m15_setup"

    if signal_m15 == "BUY":
        if ema_dir != "UP":
            return "ema_blocks_buy"
        if trend_h4 == "DOWN":
            return "h4_blocks_buy"
        return "none_other"

    if signal_m15 == "SELL":
        if ema_dir != "DOWN":
            return "ema_blocks_sell"
        if trend_h4 == "UP":
            return "h4_blocks_sell"
        if require_crypto_h4_down and sym and symbol_uses_crypto_relaxed_m15(sym) and trend_h4 != "DOWN":
            return "crypto_short_requires_h4_down"
        return "none_other"

    return "none_other"


REASON_LABEL_ES: dict[str, str] = {
    "adx_missing": "ADX ausente / no numérico",
    "adx_below_min": "ADX por debajo del mínimo",
    "no_m15_setup": "Sin ruptura M15 (signal_m15 NONE)",
    "ema_blocks_buy": "M15 dice BUY pero EMA no confirma (UP)",
    "ema_blocks_sell": "M15 dice SELL pero EMA no confirma (DOWN)",
    "h4_blocks_buy": "M15+EMA alineados al alza pero H4 en DOWN",
    "h4_blocks_sell": "M15+EMA alineados a la baja pero H4 en UP",
    "crypto_short_requires_h4_down": "SELL crypto: H4 debe estar DOWN (BOT_CRYPTO_SELL_REQUIRES_H4_DOWN)",
    "none_other": "NONE (caso raro o datos inconsistentes)",
}


def run(
    paths: list[Path],
    *,
    exclude_crypto: bool,
    only_symbols: frozenset[str] | None,
    dedupe: bool,
) -> int:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    file_counts: dict[str, int] = defaultdict(int)
    total_lines = 0
    skipped_symbol = 0
    duplicates = 0
    seen_ts_sym: set[tuple[str, str]] = set()

    for src, ev in iter_signal_events(paths):
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        if exclude_crypto and sym in DEFAULT_CRYPTO:
            skipped_symbol += 1
            continue
        if only_symbols is not None and sym not in only_symbols:
            skipped_symbol += 1
            continue

        ts = str(ev.get("ts") or "")
        if dedupe and ts:
            k = (sym, ts)
            if k in seen_ts_sym:
                duplicates += 1
                continue
            seen_ts_sym.add(k)

        total_lines += 1
        file_counts[str(src)] += 1

        dec = ev.get("decision")
        if dec in ("BUY", "SELL"):
            totals[sym][f"decision_{dec}"] += 1
            continue
        if dec != "NONE":
            totals[sym]["decision_other"] += 1
            continue

        reason = classify_none_reason(ev)
        totals[sym][reason] += 1

    print("=== Resumen SIGNAL (solo lectura) ===\n")
    print(f"Archivos usados: {len([p for p in paths if p.is_file()])}")
    print(f"Eventos SIGNAL incluidos: {total_lines}")
    if duplicates:
        print(f"Duplicados omitidos (mismo símbolo + ts): {duplicates}")
    if skipped_symbol:
        print(f"Eventos omitidos por filtro de símbolo: {skipped_symbol}")
    print()

    if not totals:
        print("No se encontraron eventos SIGNAL con los filtros indicados.")
        return 0

    # Tabla por símbolo: primero razones NONE, luego BUY/SELL
    reason_order = [
        "adx_missing",
        "adx_below_min",
        "no_m15_setup",
        "ema_blocks_buy",
        "ema_blocks_sell",
        "h4_blocks_buy",
        "h4_blocks_sell",
        "crypto_short_requires_h4_down",
        "none_other",
        "decision_BUY",
        "decision_SELL",
        "decision_other",
    ]

    symbols_sorted = sorted(totals.keys())
    col_w = 16
    print(f"{'Símbolo':<10}", end="")
    for key in reason_order:
        lab = key if len(key) <= 14 else key[:13] + "."
        print(f"{lab:>{col_w}}", end="")
    print(f"{'TOTAL':>10}")

    grand = defaultdict(int)
    for sym in symbols_sorted:
        row = totals[sym]
        tsum = sum(row.values())
        print(f"{sym:<10}", end="")
        for key in reason_order:
            v = row.get(key, 0)
            grand[key] += v
            print(f"{v:>{col_w}}", end="")
        print(f"{tsum:>10}")

    print("-" * (10 + col_w * len(reason_order) + 10))
    print(f"{'TOTAL':<10}", end="")
    for key in reason_order:
        print(f"{grand[key]:>{col_w}}", end="")
    print(f"{sum(grand.values()):>10}")

    print("\n=== Leyenda (NONE) ===")
    for k in reason_order:
        if k.startswith("decision_"):
            continue
        print(f"  {k}: {REASON_LABEL_ES.get(k, k)}")

    print("\n=== Líneas SIGNAL por archivo (muestra fuente) ===")
    for fn, c in sorted(file_counts.items(), key=lambda x: -x[1])[:25]:
        print(f"  {c:6d}  {fn}")
    if len(file_counts) > 25:
        print(f"  ... y {len(file_counts) - 25} archivos más")

    return 0


def main() -> int:
    root = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="Analiza eventos SIGNAL en logs del bot (solo lectura).",
    )
    ap.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Archivos .log (default: trades.log + logs/trades_*.log junto al script)",
    )
    ap.add_argument(
        "--exclude-crypto",
        action="store_true",
        help=f"Omitir símbolos {sorted(DEFAULT_CRYPTO)}",
    )
    ap.add_argument(
        "--only",
        type=str,
        default="",
        help="Solo estos símbolos, separados por coma (ej. GBPUSD,USDJPY)",
    )
    ap.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Contar todas las líneas aunque repitan (símbolo, ts); por defecto se deduplica.",
    )

    args = ap.parse_args()
    paths = [p.resolve() for p in args.paths] if args.paths else _default_log_paths(root)
    only: frozenset[str] | None = None
    if args.only.strip():
        only = frozenset(s.strip().upper() for s in args.only.split(",") if s.strip())

    return run(
        paths,
        exclude_crypto=args.exclude_crypto,
        only_symbols=only,
        dedupe=not args.no_dedupe,
    )


if __name__ == "__main__":
    raise SystemExit(main())
