"""TP chico C1 on the Exness demo: 0.01, +1 / -10 USD, trend side, skip after 9 wins.

Rule: trading/domain/tp_chico.py. Runbook: trading/docs/TP_CHICO.md

Safety: it attaches to the same demo-only terminal as Impulso (BOT_IMPULSO_MT5_PATH) and refuses
to run on anything but a demo account. Orders only leave with BOT_TPCHICO_DEMO_LIVE=1 and without
--dry-run. Its own magic (BOT_TPCHICO_MAGIC, 261104) keeps its trades apart from Impulso's.

    python tp_chico.py             # loop
    python tp_chico.py --dry-run   # log decisions, send nothing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from impulso_m1 import _connect, _env, _env_float, _load_dotenv
from trading.domain.tp_chico import (
    BUY, DONE, MAX_LOSS_USD, MAX_TRADES, SKIP_CYCLE, SL_PRICE, TP_PRICE, CycleState, decide, ema_last, levels,
    record_close, trend_side,
)
from trading.infrastructure.kill_switch_file import load_halt

RETCODE_UNSUPPORTED_FILLING = 10030
LOTS = 0.01  # fixed: the tested rule


def _make_logger(path: Path):
    def log(ev: dict) -> None:
        ev = {"engine": "tp_chico", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **ev}
        line = json.dumps(ev, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        logging.info(line)
    return log


class Engine:
    def __init__(self, mt5, *, send: bool, log):
        self.mt5 = mt5
        self.send = send
        self.log = log
        self.symbol = _env("BOT_TPCHICO_SYMBOL", "BTCUSDm")
        self.magic = int(_env_float("BOT_TPCHICO_MAGIC", 261104))
        self.max_spread = _env_float("BOT_TPCHICO_MAX_SPREAD", 30.0)  # price units; Oct 2026 ran 6.4-10
        self.state_path = Path(_env("BOT_TPCHICO_STATE", "tp_chico_estado.json"))
        self.halt_path = Path(_env("BOT_TPCHICO_HALT", "tp_chico_kill_switch.halt"))
        self.state, self.ticket, self.last_hour = self._load()
        mt5.symbol_select(self.symbol, True)

    # ── state ──
    def _load(self) -> tuple[CycleState, int, str]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CycleState.from_dict(raw), int(raw.get("ticket") or 0), str(raw.get("last_hour", ""))
        except (OSError, ValueError):
            return CycleState(), 0, ""

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.state.as_dict(), "ticket": self.ticket, "last_hour": self.last_hour},
                                  indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # ── loop ──
    def step(self) -> None:
        self._sync_closed()
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        if hour == self.last_hour or self.ticket:
            return
        self.last_hour = hour
        self._new_hour()
        self._save()

    def _sync_closed(self) -> None:
        if not self.ticket:
            return
        if any(int(p.ticket) == self.ticket for p in self.mt5.positions_get(symbol=self.symbol) or ()):
            return
        deals = self.mt5.history_deals_get(position=self.ticket) or ()
        outs = [d for d in deals if int(d.entry) == 1]
        if not outs:
            return  # history not there yet: try again next poll
        net = round(sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0))
                        for d in deals), 2)
        record_close(self.state, net)
        self.log({"event": "TPCHICO_CLOSED", "ticket": self.ticket, "net_usd": net, "close_price": float(outs[-1].price),
                  "broker_comment": str(outs[-1].comment), **self.state.as_dict()})
        if self.state.finished:
            self.log({"event": "TPCHICO_END", **self.state.as_dict()})
        self.ticket = 0
        self._save()

    def _ema_prev(self):
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_D1, 1, 120)
        return ema_last([float(r["close"]) for r in rates]) if rates is not None and len(rates) else None

    def _new_hour(self) -> None:
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            self.log({"event": "TPCHICO_SKIP", "reason": "sin_tick"})
            return
        bid, ask = float(tick.bid), float(tick.ask)
        ema = self._ema_prev()
        side = trend_side(bid, ema)
        base = {"bid": bid, "ask": ask, "spread": round(ask - bid, 2), "ema20_d1_ayer": round(ema, 2) if ema else None,
                "side": "buy" if side == BUY else "sell" if side else None, "streak": self.state.streak,
                "send_orders": self.send}
        block = ""
        if side == 0:
            block = "sin_ema"
        elif ask - bid > self.max_spread:
            block = "spread_caro"
        elif load_halt(self.halt_path) is not None:
            block = "tpchico_halt"
        if block:
            self.log({"event": "TPCHICO_SKIP", "reason": block, **base})
            return
        what = decide(self.state)  # a cycle skip counts even in dry-run, like the backtest
        if what == DONE:
            return
        if what == SKIP_CYCLE:
            self.log({"event": "TPCHICO_SKIP", "reason": "ciclo_9_ganadoras", **base, "skips": self.state.skips})
            return
        sl, tp = levels(side, bid, ask)
        if not self.send:
            self.log({"event": "TPCHICO_DRY_OPEN", "sl": round(sl, 2), "tp": round(tp, 2), **base})
            return
        res = self._open(side, sl, tp)
        self.log({"event": "TPCHICO_OPEN" if res["ok"] else "TPCHICO_OPEN_FAIL", "magic": self.magic, "lots": LOTS,
                  "sl": round(sl, 2), "tp": round(tp, 2), **base, **res})
        if res["ok"] and res.get("position"):
            self.ticket = int(res["position"])

    # ── orders ──
    def _open(self, side: int, sl: float, tp: float) -> dict:
        m = self.mt5
        tick = m.symbol_info_tick(self.symbol)
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": LOTS,
               "type": m.ORDER_TYPE_BUY if side == BUY else m.ORDER_TYPE_SELL,
               "price": float(tick.ask if side == BUY else tick.bid), "sl": round(float(sl), 2), "tp": round(float(tp), 2),
               "deviation": 50, "magic": self.magic, "comment": "tp_chico_c1", "type_time": m.ORDER_TIME_GTC}
        result = None
        for fill in (m.ORDER_FILLING_IOC, m.ORDER_FILLING_FOK, getattr(m, "ORDER_FILLING_RETURN", m.ORDER_FILLING_IOC)):
            req["type_filling"] = fill
            result = m.order_send(req)
            if result is None:
                return {"ok": False, "retcode": -1, "broker_comment": str(m.last_error())}
            if int(result.retcode) != RETCODE_UNSUPPORTED_FILLING:
                break
        rc = int(result.retcode)
        pos = int(getattr(result, "order", 0) or 0)
        if rc == int(m.TRADE_RETCODE_DONE):
            deal = m.history_deals_get(ticket=int(getattr(result, "deal", 0) or 0)) or ()
            pos = int(deal[0].position_id) if deal else pos
        return {"ok": rc == int(m.TRADE_RETCODE_DONE), "retcode": rc, "fill_price": float(result.price or 0.0),
                "position": pos, "broker_comment": str(result.comment)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="log decisions, send nothing")
    args = ap.parse_args()

    _load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import MetaTrader5 as mt5

    err = _connect(mt5)
    if err:
        logging.error(err)
        return 1
    send = not args.dry_run and _env("BOT_TPCHICO_DEMO_LIVE", "0") == "1"
    log = _make_logger(Path(_env("BOT_TPCHICO_LOG", "tp_chico_trades.log")))
    eng = Engine(mt5, send=send, log=log)
    if not eng.ticket:  # a position of ours that the state file lost (crash between send and save)
        mine = [int(p.ticket) for p in mt5.positions_get(symbol=eng.symbol) or () if int(p.magic) == eng.magic]
        eng.ticket = mine[0] if mine else 0
    # entries only at the turn of an hour, as tested: a start at 14:37 waits for 15:00
    eng.last_hour = max(eng.last_hour, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"))
    a = mt5.account_info()
    log({"event": "TPCHICO_START", "login": a.login, "server": a.server, "send_orders": send, "symbol": eng.symbol,
         "magic": eng.magic, "lots": LOTS, "tp_price": TP_PRICE, "sl_price": SL_PRICE, "max_trades": MAX_TRADES,
         "max_loss_usd": MAX_LOSS_USD, "open_ticket": eng.ticket, **eng.state.as_dict()})
    poll = _env_float("BOT_TPCHICO_POLL_SECONDS", 5.0)
    try:
        while True:
            try:
                eng.step()
            except Exception:
                logging.exception("tp_chico cycle failed; retrying")
                time.sleep(5)
            time.sleep(poll)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
