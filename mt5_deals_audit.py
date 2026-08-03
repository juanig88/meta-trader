"""
Auditoría de deals MT5: lista operaciones reales y separa bot vs resto.

Usa las mismas credenciales que el bot (`MT5_*` en `.env`).
Ejemplos:
  py .\\mt5_deals_audit.py --days 14
  py .\\mt5_deals_audit.py --days 30 --magic 260414
  py .\\mt5_deals_audit.py --days 7 --json
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
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


def _pnl_deal(d: Any) -> float:
    pnl = float(getattr(d, "profit", 0.0) or 0.0)
    pnl += float(getattr(d, "swap", 0.0) or 0.0)
    pnl += float(getattr(d, "commission", 0.0) or 0.0)
    return pnl


def _deal_row(d: Any) -> dict[str, Any]:
    dt = _deal_dt(d)
    return {
        "time": dt.isoformat(timespec="seconds"),
        "deal": int(getattr(d, "ticket", 0) or 0),
        "order": int(getattr(d, "order", 0) or 0),
        "position_id": int(getattr(d, "position_id", 0) or 0),
        "symbol": str(getattr(d, "symbol", "") or ""),
        "magic": int(getattr(d, "magic", 0) or 0),
        "entry": int(getattr(d, "entry", -1)),
        "type": int(getattr(d, "type", -1)),
        "volume": float(getattr(d, "volume", 0.0) or 0.0),
        "price": float(getattr(d, "price", 0.0) or 0.0),
        "pnl": round(_pnl_deal(d), 4),
        "comment": str(getattr(d, "comment", "") or "")[:80],
    }


@dataclass
class Summary:
    window_start: str
    window_end: str
    days: int
    bot_magic: int
    bot_symbols: tuple[str, ...]
    total_deals: int
    bot_deals: int
    other_deals: int
    bot_pnl: float
    other_pnl: float


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="MT5 history deals: bot vs manual/other.")
    ap.add_argument("--days", type=int, default=14, help="Lookback days from now.")
    ap.add_argument("--magic", type=int, default=None, help="Bot magic (default: BOT_MAGIC or 260414).")
    ap.add_argument("--symbols", default="", help="Comma-separated BOT-like filter. Empty = BOT_SYMBOLS from env.")
    ap.add_argument("--limit", type=int, default=200, help="Max deals to print in table mode.")
    ap.add_argument("--json", action="store_true", help="Print full deal list as JSON.")
    args = ap.parse_args()

    sym_arg = args.symbols.strip()
    if sym_arg:
        symbols = tuple(p.strip() for p in sym_arg.split(",") if p.strip())
    else:
        t = _env_str_list("BOT_SYMBOLS")
        symbols = t if t else Config.symbols

    magic_raw = args.magic if args.magic is not None else _env("BOT_MAGIC", "260414")
    try:
        bot_magic = int(magic_raw)
    except (TypeError, ValueError):
        bot_magic = 260414

    cfg = Config(symbols=symbols)
    if not connect_mt5(cfg):
        print(json.dumps({"error": "mt5_connect_failed"}, ensure_ascii=False))
        return 2

    end = datetime.now()
    start = end - timedelta(days=max(1, int(args.days)))
    deals = mt5.history_deals_get(start, end)
    mt5.shutdown()

    if deals is None:
        print(json.dumps({"error": "history_deals_get returned None"}, ensure_ascii=False))
        return 3

    symbol_set = set(symbols)
    rows = [_deal_row(d) for d in deals]
    rows.sort(key=lambda r: r["time"])

    bot_rows = [r for r in rows if r["magic"] == bot_magic and r["symbol"] in symbol_set]
    other_rows = [r for r in rows if not (r["magic"] == bot_magic and r["symbol"] in symbol_set)]

    def _is_balance_row(r: dict[str, Any]) -> bool:
        return int(r.get("type", -1)) == int(mt5.DEAL_TYPE_BALANCE)

    bot_pnl = sum(float(r["pnl"]) for r in bot_rows)
    # Exclude broker balance/credit rows from "trading pnl" subtotals.
    other_pnl = sum(float(r["pnl"]) for r in other_rows if not _is_balance_row(r))

    summary = Summary(
        window_start=start.isoformat(timespec="seconds"),
        window_end=end.isoformat(timespec="seconds"),
        days=int(args.days),
        bot_magic=bot_magic,
        bot_symbols=symbols,
        total_deals=len(rows),
        bot_deals=len(bot_rows),
        other_deals=len(other_rows),
        bot_pnl=round(bot_pnl, 4),
        other_pnl=round(other_pnl, 4),
    )

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary.__dict__,
                    "bot_deals": bot_rows,
                    "other_deals": other_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(
        json.dumps(
            {
                "summary": summary.__dict__,
                "note": "bot_deals = magic match AND symbol in BOT_SYMBOLS; other_deals = everything else (manual, other EAs, balance ops).",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lim = max(0, int(args.limit))
    if other_rows:
        tail = other_rows[-lim:] if len(other_rows) > lim else other_rows
        print("\n--- last other_deals (up to --limit) ---")
        for r in tail:
            print(
                f"{r['time']} | {r['symbol']!s:8} | magic={r['magic']:6} | entry={r['entry']} | pnl={r['pnl']:>10} | {r['comment'][:40]}"
            )
    if bot_rows:
        print("\n--- bot_deals ---")
        for r in bot_rows[-lim:]:
            print(
                f"{r['time']} | {r['symbol']!s:8} | magic={r['magic']:6} | entry={r['entry']} | pnl={r['pnl']:>10} | {r['comment'][:40]}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
