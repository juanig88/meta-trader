"""
Métricas de ejecución REAL (MT5 history_deals) alineadas al bot (magic + símbolos).

Objetivo: comparar forward vs portfolio backtest en las mismas ideas de negocio:
- frecuencia (cierres ≈ deals OUT / semana)
- PnL (profit+swap+commission) total y por símbolo
- winrate y distribución de PnL por cierre (deals OUT)
- opcional: spread al abrir desde `trades.log` (eventos TRADE_OPEN)

Ejemplos:
  py .\\forward_metrics.py --days 14
  py .\\forward_metrics.py --days 14 --baseline portfolio_results.jsonl --log-trades trades.log
  py .\\forward_metrics.py --days 7 --out forward_results.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

import MetaTrader5 as mt5

from bot_mt5 import Config, connect_mt5, _env, _env_str_list


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _deal_dt(d: Any) -> datetime:
    t = getattr(d, "time", None)
    if isinstance(t, datetime):
        return t
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(int(t))
    return datetime.now()


def _week_key(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _pnl_deal(d: Any) -> float:
    pnl = float(getattr(d, "profit", 0.0) or 0.0)
    pnl += float(getattr(d, "swap", 0.0) or 0.0)
    pnl += float(getattr(d, "commission", 0.0) or 0.0)
    return pnl


def _read_jsonl_last(path: str) -> Optional[dict]:
    """Última línea JSON válida (fallback)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            return json.loads(ln)
        except json.JSONDecodeError:
            continue
    return None


def _read_jsonl_all(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            out = []
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
            return out
    except OSError:
        return []


def _parse_weights_env(raw: Optional[str]) -> dict[str, float]:
    """
    Accepts:
      - JSON dict: {"GBPUSD":0.3,"USDJPY":0.3,"NZDJPY":0.2,"XAUUSD":0.2}
      - "GBPUSD=0.3,USDJPY=0.3,..."  (fallback)
    Returns raw floats (not necessarily normalized).
    """
    if raw is None or raw.strip() == "":
        return {}
    raw = raw.strip()
    out: dict[str, float] = {}
    try:
        if raw.startswith("{"):
            obj = json.loads(raw)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(k, str):
                        try:
                            out[k.strip()] = float(v)
                        except Exception:
                            continue
                return out
    except Exception:
        pass

    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        try:
            out[k] = float(v.strip())
        except Exception:
            continue
    return out


def _weights_close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _pick_baseline_row(
    *,
    rows: list[dict],
    symbols: set[str],
    weights_target: dict[str, float],
    risk_pct_target: Optional[float],
    sl_points_target: Optional[float],
    tp_points_target: Optional[float],
) -> Optional[dict]:
    """
    Pick the most relevant portfolio backtest row.
    Priority:
      1) same symbol set
      2) risk_pct match (if provided)
      3) SL/TP match (if provided)
      4) weights match (if provided)
      5) otherwise: newest by ts
    """
    if not rows:
        return None

    def sym_set(r: dict) -> set[str]:
        s = r.get("symbols") or []
        if isinstance(s, list):
            return {str(x) for x in s}
        return set()

    candidates = [r for r in rows if sym_set(r) == set(symbols)]
    if not candidates:
        candidates = rows[:]

    def score(r: dict) -> tuple:
        # higher is better
        sc = 0
        if sym_set(r) == set(symbols):
            sc += 100
        if risk_pct_target is not None and _weights_close(r.get("risk_pct"), risk_pct_target, tol=1e-9):
            sc += 20
        if sl_points_target is not None and _weights_close(r.get("sl_points"), sl_points_target, tol=1e-6):
            sc += 10
        if tp_points_target is not None and _weights_close(r.get("tp_points"), tp_points_target, tol=1e-6):
            sc += 10
        w = r.get("weights") or {}
        if weights_target and isinstance(w, dict):
            ok = True
            for k, v in weights_target.items():
                if not _weights_close(w.get(k), v, tol=1e-6):
                    ok = False
                    break
            if ok:
                sc += 15
        ts = str(r.get("ts") or "")
        return (sc, ts)

    return max(candidates, key=score)


def _baseline_days(row: dict) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(row.get("start", "")))
        b = datetime.fromisoformat(str(row.get("end", "")))
        return max((b - a).total_seconds() / 86400.0, 1e-9)
    except (TypeError, ValueError):
        return None


def _parse_log_trade_opens(path: str, *, symbols: set[str], start: datetime, end: datetime) -> dict[str, Any]:
    """
    Lee líneas tipo: ... | INFO | {"event":"TRADE_OPEN",...}
    Agrupa spread_points por símbolo y por semana ISO (campo ts).
    """
    by_sym: dict[str, list[float]] = defaultdict(list)
    by_week: dict[str, list[float]] = defaultdict(list)
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"event"' not in line or "TRADE_OPEN" not in line:
                    continue
                i = line.find("{")
                if i < 0:
                    continue
                try:
                    obj = json.loads(line[i:])
                except json.JSONDecodeError:
                    continue
                if obj.get("event") != "TRADE_OPEN":
                    continue
                sym = str(obj.get("symbol") or "")
                if symbols and sym not in symbols:
                    continue
                sp = obj.get("spread_points")
                if sp is None:
                    continue
                try:
                    spf = float(sp)
                except (TypeError, ValueError):
                    continue
                ts_raw = str(obj.get("ts") or "")
                try:
                    dt = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                if dt < start or dt > end:
                    continue
                by_sym[sym].append(spf)
                by_week[_week_key(dt)].append(spf)
                n += 1
    except OSError as e:
        return {"error": str(e), "opens_matched": 0}

    def _summ(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {"count": 0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
        return {
            "count": len(xs),
            "mean": round(statistics.mean(xs), 4),
            "median": round(statistics.median(xs), 4),
            "min": round(min(xs), 4),
            "max": round(max(xs), 4),
        }

    return {
        "opens_matched": n,
        "spread_points_by_symbol": {k: _summ(v) for k, v in sorted(by_sym.items())},
        "spread_points_by_week": {k: _summ(v) for k, v in sorted(by_week.items())},
    }


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Forward metrics from MT5 deals (+ optional log spread stats).")
    ap.add_argument("--days", type=int, default=7, help="Lookback days from now.")
    ap.add_argument("--magic", type=int, default=None, help="Override BOT_MAGIC (default 260414).")
    ap.add_argument("--symbols", help="Comma-separated filter. Default: BOT_SYMBOLS.")
    ap.add_argument("--baseline", default="", help="JSONL del portfolio backtest (se elige la fila que matchee con tu config).")
    ap.add_argument("--log-trades", default="", help="trades.log: stats de spread en TRADE_OPEN.")
    ap.add_argument("--out", default="", help="Append JSON summary line.")
    args = ap.parse_args()

    sym_arg = args.symbols.strip() if args.symbols else None
    if sym_arg:
        symbols = {p.strip() for p in sym_arg.split(",") if p.strip()}
        symbols_tuple = tuple(sorted(symbols))
    else:
        t = _env_str_list("BOT_SYMBOLS")
        symbols_tuple = t if t else Config.symbols
        symbols = set(symbols_tuple)

    magic_raw = args.magic if args.magic is not None else _env("BOT_MAGIC", "260414")
    try:
        magic = int(magic_raw)
    except (TypeError, ValueError):
        magic = 260414

    cfg = Config(symbols=symbols_tuple)
    if not connect_mt5(cfg):
        return 2

    end = datetime.now()
    start = end - timedelta(days=int(args.days))

    deals = mt5.history_deals_get(start, end)
    if deals is None:
        print(json.dumps({"error": "history_deals_get returned None"}, ensure_ascii=False, indent=2))
        mt5.shutdown()
        return 3

    out_pnls: list[float] = []
    out_pnls_by_sym: dict[str, list[float]] = defaultdict(list)
    by_sym_totals: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "deals": 0, "outs": 0, "out_wins": 0})
    total_pnl = 0.0
    n_deals = 0
    n_out = 0

    weeks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pnl": 0.0, "deals": 0, "outs": 0, "out_wins": 0, "by_symbol": defaultdict(lambda: {"pnl": 0.0, "outs": 0, "out_wins": 0})}
    )

    for d in deals:
        if int(getattr(d, "magic", 0) or 0) != magic:
            continue
        sym = str(getattr(d, "symbol", "") or "")
        if sym not in symbols:
            continue

        pnl = _pnl_deal(d)
        total_pnl += pnl
        n_deals += 1
        by_sym_totals[sym]["pnl"] += pnl
        by_sym_totals[sym]["deals"] += 1
        dt = _deal_dt(d)
        wk = _week_key(dt)
        weeks[wk]["pnl"] += pnl
        weeks[wk]["deals"] += 1
        weeks[wk]["by_symbol"][sym]["pnl"] += pnl

        if int(getattr(d, "entry", -1)) == mt5.DEAL_ENTRY_OUT:
            n_out += 1
            out_pnls.append(pnl)
            out_pnls_by_sym[sym].append(pnl)
            by_sym_totals[sym]["outs"] += 1
            if pnl > 0:
                by_sym_totals[sym]["out_wins"] += 1
            weeks[wk]["outs"] += 1
            weeks[wk]["by_symbol"][sym]["outs"] += 1
            if pnl > 0:
                weeks[wk]["out_wins"] += 1
                weeks[wk]["by_symbol"][sym]["out_wins"] += 1

    days = max(float(args.days), 1e-9)
    wins = sum(1 for x in out_pnls if x > 0)
    winrate = (wins / n_out) if n_out else 0.0

    def _dist(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {"n": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0, "stdev": 0.0}
        out: dict[str, float] = {
            "n": len(xs),
            "min": round(min(xs), 4),
            "max": round(max(xs), 4),
            "mean": round(statistics.mean(xs), 4),
            "median": round(statistics.median(xs), 4),
        }
        if len(xs) > 1:
            out["stdev"] = round(statistics.stdev(xs), 4)
        else:
            out["stdev"] = 0.0
        return out

    by_sym_out: dict[str, Any] = {}
    for sym in sorted(symbols):
        t0 = by_sym_totals[sym]
        ow = int(t0["out_wins"])
        oc = int(t0["outs"])
        by_sym_out[sym] = {
            "pnl": round(t0["pnl"], 4),
            "deals": int(t0["deals"]),
            "outs": oc,
            "winrate_out": round((ow / oc) if oc else 0.0, 4),
            "out_pnl_distribution": _dist(out_pnls_by_sym.get(sym, [])),
        }

    weeks_out: dict[str, Any] = {}
    for wk, data in sorted(weeks.items()):
        oc = int(data["outs"])
        ow = int(data["out_wins"])
        bs = {}
        for sym, row in sorted(data["by_symbol"].items()):
            ocs = int(row["outs"])
            ows = int(row["out_wins"])
            bs[sym] = {
                "pnl": round(row["pnl"], 4),
                "outs": ocs,
                "winrate_out": round((ows / ocs) if ocs else 0.0, 4),
            }
        weeks_out[wk] = {
            "pnl": round(data["pnl"], 4),
            "deals": data["deals"],
            "outs": oc,
            "winrate_out": round((ow / oc) if oc else 0.0, 4),
            "by_symbol": bs,
        }

    summary: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": end.isoformat(timespec="seconds"),
        "days": int(args.days),
        "magic": magic,
        "symbols": sorted(symbols),
        "deals_matched": n_deals,
        "deals_out_matched": n_out,
        "outs_per_day": round(n_out / days, 4),
        "pnl_total": round(total_pnl, 4),
        "winrate_out_deals": round(winrate, 4),
        "out_pnl_distribution_all": _dist(out_pnls),
        "by_symbol": by_sym_out,
        "by_iso_week": weeks_out,
    }

    if args.baseline:
        rows = _read_jsonl_all(args.baseline)
        # target config inferred from env (best-effort)
        weights_target = _parse_weights_env(_env("BOT_PORTFOLIO_WEIGHTS"))
        try:
            risk_pct_target = float(_env("BOT_RISK_PER_TRADE_PCT")) if _env("BOT_RISK_PER_TRADE_PCT") is not None else None
        except ValueError:
            risk_pct_target = None
        try:
            sl_points_target = float(_env("BOT_SL_POINTS")) if _env("BOT_SL_POINTS") is not None else None
        except ValueError:
            sl_points_target = None
        try:
            tp_points_target = float(_env("BOT_TP_POINTS")) if _env("BOT_TP_POINTS") is not None else None
        except ValueError:
            tp_points_target = None

        base = _pick_baseline_row(
            rows=rows,
            symbols=symbols,
            weights_target=weights_target,
            risk_pct_target=risk_pct_target,
            sl_points_target=sl_points_target,
            tp_points_target=tp_points_target,
        ) or _read_jsonl_last(args.baseline)
        if base:
            bd = _baseline_days(base)
            bt = int(base.get("trades_total", 0) or 0)
            summary["baseline_note"] = "Fila seleccionada por match con tu config (símbolos/pesos/riesgo/SL/TP cuando están disponibles). Trades/día vs forward es orientativo."
            summary["baseline"] = {
                "trades_total": bt,
                "pnl_money": base.get("pnl_money"),
                "days": round(bd, 4) if bd else None,
                "trades_per_day_backtest": round(bt / bd, 4) if bd else None,
                "outs_per_day_forward": round(n_out / days, 4),
                "spread_points_by_symbol_backtest": base.get("spread_points_by_symbol"),
                "symbols": base.get("symbols"),
                "weights": base.get("weights"),
                "risk_pct": base.get("risk_pct"),
                "sl_points": base.get("sl_points"),
                "tp_points": base.get("tp_points"),
            }
        else:
            summary["baseline"] = {"error": "Could not read baseline JSONL."}

    if args.log_trades:
        summary["from_log"] = _parse_log_trade_opens(args.log_trades, symbols=symbols, start=start, end=end)

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
