"""Daily Donchian 20/10 + SMA200 swing engine for an MT5 demo (separate from rotation).

Dry run by default. Orders only leave with BOT_SWING_DRY_RUN=0 and BOT_SWING_DEMO_LIVE=1
on an XBTFX demo account where the rotation magic has no positions or recent deals.
On Exness it reads BTCUSDm / ETHUSDm and stays dry-run: the risk-quant approval covers
XBTFX only. Runbook: trading/docs/SWING_ENGINE.md

    python swing_daily.py          # loop: one decision per UTC day after 00:BOT_SWING_RUN_AFTER_MIN
    python swing_daily.py --once   # decide now for today (skips if already ran today)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from trading.application.swing_engine import run_swing_day
from trading.domain.crypto_universe import CRYPTO_UNIVERSE
from trading.domain.demo_gate import account_is_mt5_demo, resolve_demo_execution
from trading.domain.murphy_universe import MURPHY_ROOTS, resolve_universe
from trading.domain.swing_donchian import SwingParams
from trading.infrastructure.kill_switch_file import load_halt, save_halt
from trading.infrastructure.mt5_swing_broker import Mt5SwingBroker


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _params() -> SwingParams:
    return SwingParams(
        risk_frac=_env_float("BOT_SWING_RISK_FRAC", 0.005),
        max_positions=int(_env_float("BOT_SWING_MAX_POSITIONS", 10)),
        max_drawdown_halt=_env_float("BOT_SWING_MAX_DD", 0.35),
    )


def _read_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def _paths(exness: bool) -> tuple[Path, Path, Path]:
    """State, halt and log. Exness gets its own files: its equity is not the XBTFX peak."""
    pre = "swing_exness" if exness else "swing"
    return (Path(_env("BOT_SWING_STATE", f"{pre}_state.json")),
            Path(_env("BOT_SWING_HALT", f"{pre}_kill_switch.halt")),
            Path(_env("BOT_SWING_LOG", f"{pre}_trades.log")))


def _heartbeat(now: datetime, run_after: int, state_path: Path) -> str:
    state = _read_state(state_path)
    last = state.get("last_run_day") or "-"
    nxt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=run_after)
    if last == now.date().isoformat():
        nxt += timedelta(days=1)
    when = f"{nxt:%Y-%m-%d %H:%M} UTC" if nxt > now else "en el proximo minuto"
    summary = state.get("last_summary") or {}
    return (f"swing vivo | ultima decision {last} (entradas {summary.get('opened', 0)}, "
            f"salidas {summary.get('closed', 0)}) | proxima {when}")


def _make_logger(path: Path):
    def log(ev: dict) -> None:
        line = json.dumps(ev, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        logging.info(line)
    return log


def _connect(mt5) -> bool:
    kw: dict[str, Any] = {}
    if _env("BOT_SWING_MT5_PATH"):
        kw["path"] = _env("BOT_SWING_MT5_PATH")
    if _env("BOT_SWING_MT5_LOGIN"):
        kw.update(login=int(_env("BOT_SWING_MT5_LOGIN")), password=_env("BOT_SWING_MT5_PASSWORD", ""),
                  server=_env("BOT_SWING_MT5_SERVER", ""))
    if not mt5.initialize(**kw):
        logging.error("MT5 initialize failed: %s", mt5.last_error())
        return False
    return True


def _is_exness(mt5) -> bool:
    a = mt5.account_info()
    blob = f"{getattr(a, 'server', '') or ''} {getattr(a, 'company', '') or ''}".upper() if a else ""
    return "EXNESS" in blob


def _default_symbols(mt5, exness: bool) -> list[str]:
    if not exness:
        return list(CRYPTO_UNIVERSE)
    names = [str(s.name) for s in (mt5.symbols_get() or ())]
    resolved = resolve_universe(names)
    return [str(resolved[r]) for r in MURPHY_ROOTS if resolved.get(r)]


def run_once(mt5, *, force_dry: bool) -> None:
    params = _params()
    magic = int(_env_float("BOT_SWING_MAGIC", 260501))
    rotation_magic = int(_env_float("BOT_ROTATION_MAGIC", 260414))
    exness = _is_exness(mt5)
    state_path, halt_path, log_path = _paths(exness)
    log = _make_logger(log_path)
    symbols = [s.strip() for s in (_env("BOT_SWING_SYMBOLS", "") or "").split(",") if s.strip()] or _default_symbols(mt5, exness)

    broker = Mt5SwingBroker(mt5, symbols=symbols)
    now = datetime.now(timezone.utc)
    today = now.date()
    state = _read_state(state_path)
    if state.get("last_run_day") == today.isoformat():
        return

    acct = broker.account()
    is_demo = acct is not None and account_is_mt5_demo(acct.trade_mode, acct.server)
    gate = resolve_demo_execution(
        # Exness is dry-run only until a risk-quant note covers it.
        dry_run_env=force_dry or exness or _env_bool("BOT_SWING_DRY_RUN", True),
        paper_demo_live=_env_bool("BOT_SWING_DEMO_LIVE", False),
        is_xbtfx=bool(acct and acct.is_xbtfx),
        is_demo=is_demo,
    )
    block = ""
    if load_halt(halt_path) is not None:
        block = "swing_halt"
    elif _env_bool("BOT_SWING_HONOR_ROTATION_HALT", True) and load_halt(Path("kill_switch.halt")) is not None:
        block = "kill_switch_halt"
    elif broker.magic_active(rotation_magic, hours=_env_float("BOT_SWING_ROTATION_LOOKBACK_H", 2.0)):
        block = "cuenta_compartida_con_rotacion"
    send = gate.send_orders

    log({"engine": "swing", "event": "SWING_GATE", "ts": now.isoformat(timespec="seconds"),
         "login": acct.login if acct else None, "server": acct.server if acct else None,
         "gate": gate.as_dict(), "block_reason": block, "send_orders": send,
         "broker": "exness" if exness else "xbtfx", "symbols": symbols,
         "risk_frac": params.risk_frac, "max_positions": params.max_positions})

    out = run_swing_day(broker, today=today, params=params, magic=magic, send_orders=send,
                        block_reason=block, equity_peak=float(state.get("equity_peak") or 0.0), log=log, now=now)
    if not out.get("ran") or out["stale"] * 2 > max(out["universe"], 1):
        return
    if out["halt"] and load_halt(halt_path) is None:
        save_halt({"active": True, "reason": "swing_max_drawdown", "ts": now.isoformat(timespec="seconds"),
                   "equity_peak": out["equity_peak"]}, halt_path)
    state.update(last_run_day=today.isoformat(), equity_peak=out["equity_peak"],
                 last_summary={k: v for k, v in out.items() if k != "equity_peak"})
    _write_state(state_path, state)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="decide once for today and exit")
    ap.add_argument("--dry-run", action="store_true", help="force dry run regardless of env")
    args = ap.parse_args()

    _load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import MetaTrader5 as mt5

    if not _connect(mt5):
        return 1
    run_after = int(_env_float("BOT_SWING_RUN_AFTER_MIN", 10))
    try:
        if args.once:
            run_once(mt5, force_dry=args.dry_run)
            return 0
        last_beat = None
        while True:
            now = datetime.now(timezone.utc)
            if last_beat is None or now - last_beat >= timedelta(hours=1):
                logging.info(_heartbeat(now, run_after, _paths(_is_exness(mt5))[0]))
                last_beat = now
            if now.hour * 60 + now.minute >= run_after:
                try:
                    run_once(mt5, force_dry=args.dry_run)
                except Exception:
                    logging.exception("swing cycle failed; retrying next minute")
                    mt5.shutdown()
                    time.sleep(5)
                    _connect(mt5)
            time.sleep(60)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
