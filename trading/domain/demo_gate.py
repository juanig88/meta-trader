"""Fail-closed gate for sending rotation orders to an MT5 demo account.

Orders leave this process only when:

- the operator opted in with PAPER_DEMO_LIVE
- BOT_DRY_RUN is off
- the attached terminal is XBTFX
- the account trade mode is demo (not real money)

Per-symbol fire still needs 4/5 modules and the $0.50 size check in the
rotation engine. Postgres is not part of this gate.
"""

from __future__ import annotations

from dataclasses import dataclass

# MetaTrader5.ACCOUNT_TRADE_MODE_*
TRADE_MODE_DEMO = 0
TRADE_MODE_CONTEST = 1
TRADE_MODE_REAL = 2

MODE_SIM = "SIM"
MODE_DEMO = "DEMO_XBTFX"
MODE_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class DemoExecution:
    send_orders: bool
    dry_run: bool
    mode: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "send_orders": self.send_orders,
            "dry_run": self.dry_run,
            "mode": self.mode,
            "reason": self.reason,
        }


def account_is_mt5_demo(trade_mode: int, server: str = "") -> bool:
    """True only for MT5 demo accounts. Real and contest stay closed."""
    try:
        mode = int(trade_mode)
    except (TypeError, ValueError):
        return False
    if mode == TRADE_MODE_REAL or mode == TRADE_MODE_CONTEST:
        return False
    if mode == TRADE_MODE_DEMO:
        return True
    blob = str(server or "").upper()
    return "DEMO" in blob


def resolve_demo_execution(
    *,
    dry_run_env: bool,
    paper_demo_live: bool,
    is_xbtfx: bool,
    is_demo: bool,
) -> DemoExecution:
    """Decide whether rotation may call order_send. Default is dry-run."""
    if bool(dry_run_env):
        return DemoExecution(False, True, MODE_SIM, "BOT_DRY_RUN")
    if not bool(paper_demo_live):
        return DemoExecution(False, True, MODE_BLOCKED, "falta_PAPER_DEMO_LIVE")
    if not bool(is_xbtfx):
        return DemoExecution(False, True, MODE_BLOCKED, "terminal_no_es_XBTFX")
    if not bool(is_demo):
        return DemoExecution(False, True, MODE_BLOCKED, "cuenta_no_es_demo")
    return DemoExecution(True, False, MODE_DEMO, "ok")
