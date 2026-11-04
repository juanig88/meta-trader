"""TP chico lateral on the Exness demo: 0.01, +1 / -10 USD, near zones, calm market, no Mondays or 00-05 UTC.

Rule: trading/domain/tp_lateral.py. Pre-registration: trading/docs/PREREG_TP_LATERAL_DEMO.md.
Runbook: trading/docs/TP_CHICO.md

Same safety as tp_chico.py: the demo-only terminal at BOT_IMPULSO_MT5_PATH, refuses a non-demo
account, orders only with BOT_TPLATERAL_DEMO_LIVE=1 and without --dry-run. Magic 261105.

    python tp_lateral.py             # loop
    python tp_lateral.py --dry-run   # log decisions, send nothing
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
from tp_chico import Engine as ChicoEngine
from trading.domain.impulso_m1 import Bar
from trading.domain.rebound_zones import find_zones
from trading.domain.tp_chico import BUY, levels, trend_side
from trading.domain.tp_lateral import (
    DAY_LOSS_USD, MAX_LOSS_USD, MAX_TRADES, LateralState, calm_block, entry_block, record_close, zone_distance,
)
from trading.infrastructure.kill_switch_file import load_halt

LOTS = 0.01


def _make_logger(path: Path):
    def log(ev: dict) -> None:
        ev = {"engine": "tp_lateral", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **ev}
        line = json.dumps(ev, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        logging.info(line)
    return log


class Engine(ChicoEngine):
    """Reuses tp_chico's order sending and daily EMA; its own state, gates and magic."""

    def __init__(self, mt5, *, send: bool, log):
        self.mt5 = mt5
        self.send = send
        self.log = log
        self.symbol = _env("BOT_TPLATERAL_SYMBOL", "BTCUSDm")
        self.magic = int(_env_float("BOT_TPLATERAL_MAGIC", 261105))
        self.max_spread = _env_float("BOT_TPLATERAL_MAX_SPREAD", 30.0)
        self.state_path = Path(_env("BOT_TPLATERAL_STATE", "tp_lateral_estado.json"))
        self.halt_path = Path(_env("BOT_TPLATERAL_HALT", "tp_lateral_kill_switch.halt"))
        self.comment = "tp_lateral"
        self.state, self.ticket, self.last_bar = self._load()
        self._zones_day, self._zones = "", []
        mt5.symbol_select(self.symbol, True)

    def _load(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return LateralState.from_dict(raw), int(raw.get("ticket") or 0), int(raw.get("last_bar") or 0)
        except (OSError, ValueError):
            return LateralState(), 0, 0

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.state.as_dict(), "ticket": self.ticket, "last_bar": self.last_bar},
                                  indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def step(self) -> None:
        self._sync_closed()
        m15 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M15, 1, 120)
        if m15 is None or not len(m15) or int(m15[-1]["time"]) == self.last_bar:
            return
        self.last_bar = int(m15[-1]["time"])
        if not self.ticket:
            self._evaluate(m15)
        self._save()

    def _sync_closed(self) -> None:
        if not self.ticket:
            return
        if any(int(p.ticket) == self.ticket for p in self.mt5.positions_get(symbol=self.symbol) or ()):
            return
        deals = self.mt5.history_deals_get(position=self.ticket) or ()
        outs = [d for d in deals if int(d.entry) == 1]
        if not outs:
            return
        net = round(sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0))
                        for d in deals), 2)
        day = datetime.fromtimestamp(int(outs[-1].time), tz=timezone.utc).strftime("%Y-%m-%d")
        record_close(self.state, net, day)
        self.log({"event": "TPLAT_CLOSED", "ticket": self.ticket, "close_price": float(outs[-1].price),
                  "broker_comment": str(outs[-1].comment), **self.state.as_dict(), "trade_net_usd": net})
        if self.state.finished:
            self.log({"event": "TPLAT_END", **self.state.as_dict()})
        self.ticket = 0
        self._save()

    def _zones_today(self, now: datetime) -> list:
        day = now.strftime("%Y-%m-%d")
        if day != self._zones_day:
            start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp())
            h1 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_H1, 1, 1700)
            rows = [r for r in (h1 if h1 is not None else ()) if int(r["time"]) < start][-1440:]
            bars = [Bar(float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]
            self._zones = find_zones(bars) if len(bars) >= 500 else []
            self._zones_day = day
        return self._zones

    def _evaluate(self, m15) -> None:
        now = datetime.now(timezone.utc)
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return
        bid, ask = float(tick.bid), float(tick.ask)
        dist = zone_distance(bid, self._zones_today(now))
        calm = calm_block([float(r["high"]) for r in m15], [float(r["low"]) for r in m15],
                          [float(r["close"]) for r in m15])
        block = entry_block(self.state, now, dist, calm)
        ema = self._ema_prev()
        side = trend_side(bid, ema)
        if not block and side == 0:
            block = "sin_ema"
        if not block and ask - bid > self.max_spread:
            block = "spread_caro"
        if not block and load_halt(self.halt_path) is not None:
            block = "tplateral_halt"
        base = {"bid": bid, "ask": ask, "spread": round(ask - bid, 2), "zone_dist_pct": round(dist * 100, 3) if dist is not None else None,
                "ema20_d1_ayer": round(ema, 2) if ema else None, "side": "buy" if side == BUY else "sell" if side else None,
                "day_net_usd": self.state.day_net_usd, "send_orders": self.send}
        if block:
            self.log({"event": "TPLAT_SKIP", "reason": block, **base})
            return
        sl, tp = levels(side, bid, ask)
        if not self.send:
            self.log({"event": "TPLAT_DRY_OPEN", "sl": round(sl, 2), "tp": round(tp, 2), **base})
            return
        res = self._open(side, sl, tp)
        self.log({"event": "TPLAT_OPEN" if res["ok"] else "TPLAT_OPEN_FAIL", "magic": self.magic, "lots": LOTS,
                  "sl": round(sl, 2), "tp": round(tp, 2), **base, **res})
        if res["ok"] and res.get("position"):
            self.ticket = int(res["position"])


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
    send = not args.dry_run and _env("BOT_TPLATERAL_DEMO_LIVE", "0") == "1"
    log = _make_logger(Path(_env("BOT_TPLATERAL_LOG", "tp_lateral_trades.log")))
    eng = Engine(mt5, send=send, log=log)
    if not eng.ticket:
        mine = [int(p.ticket) for p in mt5.positions_get(symbol=eng.symbol) or () if int(p.magic) == eng.magic]
        eng.ticket = mine[0] if mine else 0
    a = mt5.account_info()
    log({"event": "TPLAT_START", "login": a.login, "server": a.server, "send_orders": send, "symbol": eng.symbol,
         "magic": eng.magic, "lots": LOTS, "day_loss_usd": DAY_LOSS_USD, "max_trades": MAX_TRADES,
         "max_loss_usd": MAX_LOSS_USD, "open_ticket": eng.ticket, **eng.state.as_dict()})
    poll = _env_float("BOT_TPLATERAL_POLL_SECONDS", 5.0)
    try:
        while True:
            try:
                eng.step()
            except Exception:
                logging.exception("tp_lateral cycle failed; retrying")
                time.sleep(5)
            time.sleep(poll)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
