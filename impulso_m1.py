"""M1 impulse engine on the Exness demo: same signal, two exits side by side.

Rule: trading/domain/impulso_m1.py. Runbook: trading/docs/IMPULSO_M1.md

- magic BOT_IMPULSO_MAGIC_FIXED (261030): stop at the range, fixed USD take-profit
- magic BOT_IMPULSO_MAGIC_FADE  (261031): same stop, wider USD take-profit, closed early
  by ``fade_exit`` when momentum dies while in profit
- magic BOT_IMPULSO_MAGIC_FALSE_BREAK (261033): the return after a false break of a floor or
  ceiling, only with the held trend (``Regime.on_h1_bar``); the only entry on since 2026-10-31

Safety: it only attaches to the terminal at BOT_IMPULSO_MT5_PATH (a second MT5 install,
never the one you trade the real account on) and refuses to run unless that account is
demo. Orders only leave with BOT_IMPULSO_DEMO_LIVE=1 and without --dry-run.

    python impulso_m1.py             # loop
    python impulso_m1.py --dry-run   # log signals, send nothing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from trading.domain.ath import drawdown_from_ath
from trading.domain.demo_gate import account_is_mt5_demo
from trading.domain.impulso_m1 import (
    BUY, Bar, ImpulseParams, RejectionParams, detect_impulse, detect_rejection, fade_exit,
)
from trading.domain.regime import (
    RANGE, Regime, allowed_sides, held_trend, initial_trend, market_trend, return_target, risk_cap, status_key,
    status_text, structure_trend, zone_stop,
)
from trading.domain.rebound_zones import Zone, find_recent_zones, find_zones, parse_manual_zones, zone_block
from trading.domain.zone_book import (
    BLOCKING, H1, ZoneRecord, blocking_zones, change_messages, merge_candidates, prune, replay, sync_manual,
)
from trading.domain.volume import relative_volume
from trading.infrastructure.binance_klines import update_csv
from trading.infrastructure.kill_switch_file import load_halt

RETCODE_UNSUPPORTED_FILLING = 10030
MAX_LOTS = 0.01
HTF = ("TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_M30", "TIMEFRAME_H1", "TIMEFRAME_H4")
TREND_TF = ("TIMEFRAME_H4", "TIMEFRAME_D1", "TIMEFRAME_W1", "TIMEFRAME_MN1")


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


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


def _make_logger(path: Path):
    def log(ev: dict) -> None:
        ev = {"engine": "impulso_m1", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **ev}
        line = json.dumps(ev, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        logging.info(line)
    return log


def _connect(mt5) -> str:
    """Empty string when attached to a demo account through the dedicated terminal."""
    path = _env("BOT_IMPULSO_MT5_PATH")
    if not path:
        return "falta BOT_IMPULSO_MT5_PATH (segunda MT5 solo demo; nunca la terminal de la cuenta real)"
    kw: dict[str, Any] = {"path": path}
    login = _env("BOT_IMPULSO_MT5_LOGIN") or _env("MT5_LOGIN")
    if login:
        kw.update(login=int(login), password=_env("BOT_IMPULSO_MT5_PASSWORD") or _env("MT5_PASSWORD", ""),
                  server=_env("BOT_IMPULSO_MT5_SERVER") or _env("MT5_SERVER", ""))
    if not mt5.initialize(**kw):
        return f"mt5.initialize failed: {mt5.last_error()}"
    a = mt5.account_info()
    if a is None or not account_is_mt5_demo(int(a.trade_mode), str(a.server)):
        mt5.shutdown()
        return f"la cuenta {getattr(a, 'login', '?')} ({getattr(a, 'server', '?')}) no es demo: no corro"
    return ""


def _bars(rates) -> list[Bar]:
    return [Bar(float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rates]


class Engine:
    def __init__(self, mt5, *, send: bool, log):
        self.mt5 = mt5
        self.send = send
        self.log = log
        self.symbol = _env("BOT_IMPULSO_SYMBOL", "BTCUSDm")
        self.lots = min(_env_float("BOT_IMPULSO_LOT", 0.01), MAX_LOTS)  # never more than 0.01
        self.tp_fixed_usd = _env_float("BOT_IMPULSO_TP_FIXED_USD", 2.0)
        self.tp_fade_usd = _env_float("BOT_IMPULSO_TP_FADE_USD", 5.0)
        self.fade_min_usd = _env_float("BOT_IMPULSO_FADE_MIN_PROFIT_USD", 0.30)
        self.max_spread = _env_float("BOT_IMPULSO_MAX_SPREAD", 30.0)  # price units (USD per BTC)
        self.cooldown_s = _env_float("BOT_IMPULSO_COOLDOWN_MIN", 15.0) * 60
        self.magic_fixed = int(_env_float("BOT_IMPULSO_MAGIC_FIXED", 261030))
        self.magic_fade = int(_env_float("BOT_IMPULSO_MAGIC_FADE", 261031))
        self.magic_rej = int(_env_float("BOT_IMPULSO_MAGIC_REJECTION", 261032))
        self.rej_enabled = _env("BOT_IMPULSO_REJECTION_ENABLED", "0") == "1"
        self.impulse_enabled = _env("BOT_IMPULSO_IMPULSE_ENABLED", "0") == "1"
        self.fb_enabled = _env("BOT_IMPULSO_FALSE_BREAK_ENABLED", "1") == "1"
        self.magic_fb = int(_env_float("BOT_IMPULSO_MAGIC_FALSE_BREAK", 261033))
        self.start_balance = _env_float("BOT_IMPULSO_START_BALANCE", 200.0)
        self.risk_floor_usd = _env_float("BOT_IMPULSO_RISK_FLOOR_USD", 4.0)
        self.risk_share = _env_float("BOT_IMPULSO_RISK_SHARE_OF_PROFIT", 0.5)
        self.vol_path = Path(_env("BOT_IMPULSO_VOLUME_FILE", "data/binance/BTCUSDT_1h.csv"))
        self.vol_symbol = _env("BOT_IMPULSO_VOLUME_SYMBOL", "BTCUSDT")
        self.vol_rows: list[dict] = []
        self.ath_highs: list[float] = []  # Binance H1 highs since 2017-08, for dd_from_ath
        self.tp_rej_usd = _env_float("BOT_IMPULSO_TP_REJECTION_USD", 2.0)
        self.last_rej_at = 0.0
        self.spike_mult = _env_float("BOT_IMPULSO_SPIKE_MULT", 3.0)
        self.zone_near_pct = _env_float("BOT_IMPULSO_ZONE_NEAR_PCT", 0.3)
        self.zone_days = int(_env_float("BOT_IMPULSO_ZONE_LOOKBACK_DAYS", 60))
        self.env_zones = parse_manual_zones(_env("BOT_IMPULSO_ZONES", ""))
        self.zones_file = Path(_env("BOT_IMPULSO_ZONES_FILE", "impulso_zonas.txt"))
        self.manual_zones = list(self.env_zones)
        self._manual_at = 0.0
        self._manual_text: Optional[str] = None
        self.recent_zones = _env("BOT_IMPULSO_RECENT_ZONES", "1") == "1"
        self._zones_at = 0.0
        self.zone_telegram = _env("BOT_IMPULSO_ZONE_TELEGRAM", "1") == "1"
        self.book_path = Path(_env("BOT_IMPULSO_ZONE_BOOK", "impulso_zonas_estado.json"))
        self.book: dict[str, ZoneRecord] = self._load_book()
        self.halt_path = Path(_env("BOT_IMPULSO_HALT", "impulso_kill_switch.halt"))
        self.regime_path = Path(_env("BOT_IMPULSO_REGIME_FILE", "impulso_regimen.json"))
        self.regime = self._load_regime()
        self.rej_max_risk_usd = _env_float("BOT_IMPULSO_REJ_MAX_RISK_USD", 8.0)
        self.trend, self.trend_votes, self.trend_now, self.trend_since = RANGE, [], RANGE, ""
        try:
            saved = json.loads(self.regime_path.read_text(encoding="utf-8"))
            self.trend, self.trend_since = saved.get("held_trend", RANGE), saved.get("held_since", "")
        except (OSError, ValueError):
            pass
        self._regime_h1: Optional[int] = None
        self._status_sent: Optional[tuple] = None
        self.params = ImpulseParams(min_tf_agree=int(_env_float("BOT_IMPULSO_MIN_TF_AGREE", 4)))
        self.last_signal_at = 0.0
        self.open_tickets: dict[int, int] = {}  # ticket -> magic
        mt5.symbol_select(self.symbol, True)

    # ── market data ──
    def _usd_per_price(self) -> float:
        i = self.mt5.symbol_info(self.symbol)
        return float(i.trade_tick_value) / float(i.trade_tick_size)

    def _forming_opens(self) -> list[float]:
        out = []
        for name in HTF:
            r = self.mt5.copy_rates_from_pos(self.symbol, getattr(self.mt5, name), 0, 1)
            if r is None or len(r) == 0:
                return []
            out.append(float(r[0]["open"]))
        return out

    def zones(self) -> list[Zone]:
        """Zones that block entries right now, from the zone book (``zone_book.py``).

        Manual zones = BOT_IMPULSO_ZONES plus ``impulso_zonas.txt`` (one ``low-high`` per line,
        ``#`` comments), re-read every minute. Every 10 minutes (or when the file changes) the
        book adds newly detected zones (60d H1 + 48h M30), replays each zone's story on 60 days
        of closed H1, retires dead ones, saves ``impulso_zonas_estado.json`` and tells Telegram
        what broke, flipped, expired or appeared.
        """
        if time.time() - self._manual_at >= 60:
            self._manual_at = time.time()
            try:
                text = self.zones_file.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if text != self._manual_text:
                self._manual_text = text
                lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
                self.manual_zones = self.env_zones + parse_manual_zones(",".join(ln for ln in lines if ln))
                self._zones_at = 0.0  # refresh the book right away
        if time.time() - self._zones_at >= 600:
            self._refresh_book()
        return blocking_zones(self.book)

    def _refresh_book(self) -> None:
        h1 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_H1, 1, self.zone_days * 24)
        if h1 is None or len(h1) == 0:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        first = not self.book
        before = {k: (r.status, r.role) for k, r in self.book.items()}
        added, removed = sync_manual(self.book, self.manual_zones, now)
        candidates = find_zones(_bars(h1))
        if self.recent_zones:
            m30 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M30, 1, 96)
            if m30 is not None and len(m30) > 0:
                candidates += find_recent_zones(_bars(m30))
        added += merge_candidates(self.book, candidates, now)
        bars = [H1(datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).replace(tzinfo=None),
                   float(r["high"]), float(r["low"]), float(r["close"])) for r in h1]
        for rec in self.book.values():
            replay(rec, bars, now)
        dropped = prune(self.book, now)
        self._zones_at = time.time()
        self._save_book()
        lines = change_messages(before, self.book)
        lines += [f"Zona nueva {round(self.book[k].low):,}–{round(self.book[k].high):,} ({self.book[k].origin}): "
                  f"{self.book[k].role}, {self.book[k].touches} toques, {self.book[k].bounces} rebotes / "
                  f"{self.book[k].breaks} rupturas en 60 días." for k in added
                  if k in self.book and self.book[k].status in BLOCKING]  # born expired: not news
        lines += [f"Zona manual {k.split(':', 1)[1]} sacada del archivo." for k in removed]
        self.log({"event": "IMPULSO_ZONES", "changes": lines, "dropped": dropped,
                  "blocking": [z.as_dict() for z in blocking_zones(self.book)],
                  "not_blocking": [{"zid": r.zid, "status": r.status} for r in self.book.values()
                                   if r.status not in BLOCKING]})
        if lines and not first and self.zone_telegram:
            try:
                from telegram_notify import send_message_now

                send_message_now("🧱 Zonas BTC (bot demo)\n\n" + "\n".join(lines))
            except Exception:
                logging.exception("zone telegram failed")

    def _load_book(self) -> dict[str, ZoneRecord]:
        try:
            raw = json.loads(self.book_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out = {}
        for d in raw.get("zonas", []):
            d.pop("bounce_rate", None)
            try:
                out[d["zid"]] = ZoneRecord(**d)
            except TypeError:
                continue
        return out

    def _save_book(self) -> None:
        rows = sorted((r.as_dict() for r in self.book.values()), key=lambda d: -d["high"])
        tmp = self.book_path.with_name(self.book_path.name + ".tmp")
        tmp.write_text(json.dumps({"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "zonas": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.book_path)

    def _zone_reason(self, side: int, price: float) -> tuple[str, Optional[dict]]:
        h1 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_H1, 1, 1)
        if h1 is None or len(h1) == 0:
            return "sin_h1_para_zonas", None
        z = zone_block(side, price, self.zones(), float(h1[-1]["close"]), self.zone_near_pct)
        if z is None:
            return "", None
        return ("zona_piso" if side != BUY else "zona_techo"), z.as_dict()

    # ── cycle ──
    def step(self) -> None:
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None or float(tick.bid) <= 0:
            return
        self._sync_closed()
        self._manage_fade(tick)
        self._update_regime()
        if self.impulse_enabled:
            self._maybe_enter(tick)
        if self.rej_enabled:
            self._maybe_reject(tick)

    # ── regime (trading/domain/regime.py) ──
    def _load_regime(self) -> Regime:
        try:
            return Regime.from_dict(json.loads(self.regime_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return Regime()

    def _save_regime(self) -> None:
        tmp = self.regime_path.with_name(self.regime_path.name + ".tmp")
        tmp.write_text(json.dumps({**self.regime.as_dict(), "held_trend": self.trend, "held_since": self.trend_since,
                                   "trend_now": self.trend_now, "votes": self.trend_votes},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.regime_path)

    def _regime_event(self, what: str, price: Optional[float] = None) -> None:
        """Log + save every hour; Telegram when trend, floor, ceiling or pause changed (and at start)."""
        r = self.regime
        self.log({"event": "IMPULSO_REGIME", "what": what, "trend": self.trend, "trend_since": self.trend_since,
                  "trend_now": self.trend_now, "votes": self.trend_votes,
                  **r.as_dict()})
        self._save_regime()
        key = status_key(self.trend, r)
        if self.zone_telegram and key != self._status_sent:
            if price is None:
                tick = self.mt5.symbol_info_tick(self.symbol)
                price = float(tick.bid) if tick is not None else 0.0
            header = {"pausa": "🛑 Bot demo: ruptura confirmada o una orden perdió contra la zona.",
                      "reanuda": "✅ Bot demo: hay piso/techo nuevo, vuelve a operar.",
                      "sospecha": "🔎 Bot demo: posible ruptura, entra en evaluación (no abre nada).",
                      "falsa": "↩️ Bot demo: la ruptura fue falsa, el precio volvió a la zona."}.get(
                what, "🧭 Bot demo: cambió el mapa")
            if what == "tendencia":
                header = f"🔄 Bot demo: CAMBIO DE TENDENCIA de fondo, ahora {self.trend.upper()}"
            text = status_text(price, self.trend, self.trend_votes, r, header, self.trend_since)
            self._status_sent = key
            try:
                from telegram_notify import send_message_now

                send_message_now(text)
            except Exception:
                logging.exception("regime telegram failed")

    def _refresh_volume(self) -> None:
        """Append the Binance H1 candles closed since the last run to the local history file."""
        try:
            rows = update_csv(self.vol_path, self.vol_symbol, "1h", 1502942400000)
            self.ath_highs = [max(r["high"] for r in rows)] if rows else []
            self.vol_rows = rows[-200:]
        except Exception as exc:  # network down: keep what we had, the rule does not need it
            self.log({"event": "VOLUME_ERROR", "error": str(exc)[:200]})

    def _break_volume(self, open_s: int) -> Optional[float]:
        """Relative USDT volume of the H1 that opened at ``open_s`` vs the 24 before it."""
        if not any(r["open_ms"] == open_s * 1000 for r in self.vol_rows[-3:]):
            self._refresh_volume()
        idx = next((k for k in range(len(self.vol_rows) - 1, -1, -1) if self.vol_rows[k]["open_ms"] == open_s * 1000), None)
        if idx is None or idx < 24:
            return None
        rel = relative_volume([r["quote_volume"] for r in self.vol_rows[idx - 24:idx]], self.vol_rows[idx]["quote_volume"])
        return round(rel, 2) if rel is not None else None

    def _initial_trend(self) -> str:
        """No memory yet: replay the 3-of-4 vote day by day over the last 120 days."""
        tfs = {name: self.mt5.copy_rates_from_pos(self.symbol, getattr(self.mt5, name), 1, n)
               for name, n in zip(TREND_TF, (1000, 400, 120, 60))}
        if any(r is None or len(r) == 0 for r in tfs.values()):
            return RANGE
        now = int(time.time())
        days = []
        for d in range(120, 0, -1):
            t = now - now % 86400 - d * 86400
            days.append([structure_trend(_bars([x for x in r if int(x["time"]) < t][:-1][-200:]))  # drop the unfinished one
                         for r in tfs.values()])
        return initial_trend(days)

    def _update_regime(self) -> None:
        """Once per closed H1: trend vote, false-break evaluation, resume check."""
        h1 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_H1, 1, 2)
        if h1 is None or len(h1) < 2 or int(h1[-1]["time"]) == self._regime_h1:
            return
        self._regime_h1 = int(h1[-1]["time"])
        votes = []
        for name in TREND_TF:
            r = self.mt5.copy_rates_from_pos(self.symbol, getattr(self.mt5, name), 1, 200)
            votes.append(structure_trend(_bars(r)) if r is not None and len(r) else 0)
        self.trend_now, self.trend_votes = market_trend(votes), votes
        if self.trend == RANGE and not self.trend_since:
            self.trend = self._initial_trend()
            self.trend_since = "historia"
        changed = held_trend(self.trend, self.trend_now) != self.trend
        if changed:
            self.trend = self.trend_now
            self.trend_since = datetime.now(timezone.utc).isoformat(timespec="minutes")
        close = float(h1[-1]["close"])
        was_paused = self.regime.paused
        zones = self.zones()
        ev = self.regime.on_h1_bar(datetime.fromtimestamp(self._regime_h1 + 3600, tz=timezone.utc),
                                   float(h1[-1]["high"]), float(h1[-1]["low"]), close, float(h1[-2]["close"]), zones)
        why = ev[0] if ev and ev[0] == "confirmada" else None
        self._refresh_volume()
        if ev and ev[0] == "sospecha":
            self.regime.watch["vol_rel"] = self._break_volume(self._regime_h1)
        if self.regime.paused:
            since = datetime.fromisoformat(self.regime.paused_since)
            rates = self.mt5.copy_rates_range(self.symbol, self.mt5.TIMEFRAME_H1, since,
                                              datetime.fromtimestamp(self._regime_h1 + 1, tz=timezone.utc))
            if rates is not None and len(rates):
                self.regime.try_resume(_bars(rates), close)
        if why or was_paused != self.regime.paused:
            self._regime_event("pausa" if self.regime.paused else "reanuda", close)
        elif ev:
            self._regime_event(ev[0], close)
            if ev[0] == "falsa" and self.fb_enabled:
                self._enter_false_break(-ev[1], ev[2], zones, ev[3].get("vol_rel"))
        elif changed:
            self._regime_event("tendencia", close)
        else:
            self._regime_event("hora", close)

    def _enter_false_break(self, side: int, extreme: float, zones: list[Zone], vol_rel: Optional[float] = None) -> None:
        """Trade the return after a false break: stop past the false extreme, target the next zone."""
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return
        bid, ask = float(tick.bid), float(tick.ask)
        entry = ask if side == BUY else bid
        sl = extreme * (0.999 if side == BUY else 1.001)
        tp = return_target(side, entry, self.regime.zones(zones))
        per_usd = 1.0 / (self.lots * self._usd_per_price())
        a = self.mt5.account_info()
        profit = float(a.balance) - self.start_balance if a is not None else 0.0
        cap = risk_cap(profit, self.risk_floor_usd, self.risk_share)
        risk = abs(entry - sl) / per_usd
        dd_ath = drawdown_from_ath(self.ath_highs, entry)  # telemetry only, Binance ATH vs Exness price
        block = ""
        if side not in allowed_sides(self.trend):
            block = "contra_tendencia"
        elif (entry - sl) * side <= 0:
            block = "precio_pasado_el_extremo"
        elif tp is None:
            block = "sin_zona_objetivo"
        elif risk > cap:
            block = "riesgo_mayor_al_tope"
        elif ask - bid > self.max_spread:
            block = "spread_caro"
        elif load_halt(self.halt_path) is not None:
            block = "impulso_halt"
        elif any(int(p.magic) == self.magic_fb for p in self.mt5.positions_get(symbol=self.symbol) or ()):
            block = "ya_hay_posicion"
        self.log({"event": "FALSA_SIGNAL", "side": side, "entry": entry, "sl": round(sl, 2),
                  "tp": round(tp, 2) if tp else None, "extreme": extreme, "risk_usd": round(risk, 2),
                  "cap_usd": round(cap, 2), "profit_usd": round(profit, 2), "trend": self.trend,
                  "vol_rel": vol_rel, "dd_from_ath": dd_ath, "send_orders": self.send, "block_reason": block})
        if block or not self.send:
            return
        res = self._open(side, sl, tp, self.magic_fb, "imp_falsa")
        self.log({"event": "IMPULSO_OPEN", "magic": self.magic_fb, "variant": "imp_falsa", "entry": entry,
                  "sl": round(sl, 2), "tp": round(tp, 2), "risk_usd": round(risk, 2), "cap_usd": round(cap, 2),
                  "trend": self.trend, "vol_rel": vol_rel, "dd_from_ath": dd_ath, "lots": self.lots, **res})
        if res.get("ok") and res.get("position"):
            self.open_tickets[int(res["position"])] = self.magic_fb

    def _maybe_reject(self, tick) -> None:
        """Pullback-exhaustion entry (user's 390117817): own magic, TP in USD plus fade exit."""
        if time.time() - self.last_rej_at < self.cooldown_s:
            return
        m1 = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 0, 5)
        opens = self._forming_opens()
        if m1 is None or len(m1) < 5 or not opens:
            return
        bid, ask = float(tick.bid), float(tick.ask)
        spread = ask - bid
        sig = detect_rejection(_bars(m1[:-1]), (bid + ask) / 2, float(m1[-1]["open"]), opens,
                               RejectionParams(sl_buffer=spread))
        if sig is None:
            return
        self.last_rej_at = time.time()
        block, zone = self._zone_reason(sig.side, sig.price)
        per_usd = 1.0 / (self.lots * self._usd_per_price())
        entry = ask if sig.side == BUY else bid
        sl = zone_stop(sig.side, self.regime.floor, self.regime.ceiling)
        if self.regime.paused:
            block = "regimen_pausa"
        elif sig.side not in allowed_sides(self.trend):
            block = "contra_tendencia"
        elif sl is None or (entry - sl) * sig.side <= 0:
            block = "sin_piso_techo"
        elif abs(entry - sl) / per_usd > self.rej_max_risk_usd:
            block = "riesgo_mayor_al_tope"
        if spread > self.max_spread:
            block = "spread_caro"
        elif load_halt(self.halt_path) is not None:
            block = "impulso_halt"
        self.log({"event": "RECHAZO_SIGNAL", "symbol": self.symbol, "bid": bid, "ask": ask,
                  "spread": round(spread, 2), **sig.as_dict(), "send_orders": self.send, "block_reason": block,
                  "zone": zone, "trend": self.trend, "zone_sl": round(sl, 2) if sl else None})
        if block or not self.send:
            return
        if any(int(p.magic) == self.magic_rej for p in self.mt5.positions_get(symbol=self.symbol) or ()):
            self.log({"event": "IMPULSO_SKIP", "magic": self.magic_rej, "reason": "ya_hay_posicion"})
            return
        tp = entry + sig.side * self.tp_rej_usd * per_usd
        res = self._open(sig.side, sl, tp, self.magic_rej, "imp_rechazo")
        self.log({"event": "IMPULSO_OPEN", "magic": self.magic_rej, "variant": "imp_rechazo", "entry": entry,
                  "sl": round(sl, 2), "tp": round(tp, 2), "risk_usd": round(abs(entry - sl) / per_usd, 2),
                  "trend": self.trend,
                  "lots": self.lots, **res})
        if res.get("ok") and res.get("position"):
            self.open_tickets[int(res["position"])] = self.magic_rej

    def _sync_closed(self) -> None:
        """Log positions the broker closed (SL/TP) or we closed, once each."""
        live = {int(p.ticket) for p in self.mt5.positions_get(symbol=self.symbol) or ()}
        for ticket in [t for t in self.open_tickets if t not in live]:
            magic = self.open_tickets.pop(ticket)
            deals = self.mt5.history_deals_get(position=ticket) or ()
            outs = [d for d in deals if int(d.entry) == 1]
            net = sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0)) for d in deals)
            self.log({"event": "IMPULSO_CLOSED", "ticket": ticket, "magic": magic, "net_usd": round(net, 2),
                      "close_price": float(outs[-1].price) if outs else None,
                      "close_comment": str(outs[-1].comment) if outs else ""})
            if magic in (self.magic_rej, self.magic_fb) and outs and str(outs[-1].comment).startswith("[sl"):
                self.regime.pause(datetime.now(timezone.utc), "sl_contra_zona")
                self._regime_event("pausa")

    def _manage_fade(self, tick) -> None:
        for p in self.mt5.positions_get(symbol=self.symbol) or ():
            if int(p.magic) not in (self.magic_fade, self.magic_rej):
                continue
            side = BUY if int(p.type) == 0 else -1
            now_s = int(tick.time)
            rates = self.mt5.copy_rates_range(self.symbol, self.mt5.TIMEFRAME_M1,
                                              datetime.fromtimestamp(int(p.time) - int(p.time) % 60, tz=timezone.utc),
                                              datetime.fromtimestamp(now_s, tz=timezone.utc))
            closed = [r for r in (rates if rates is not None else ()) if int(r["time"]) + 60 <= now_s]
            why = fade_exit(side, float(p.price_open), _bars(closed), float(p.profit), self.fade_min_usd)
            if why:
                res = self._close(p, tick, why)
                self.log({"event": "IMPULSO_FADE_CLOSE", "ticket": int(p.ticket), "reason": why,
                          "profit_usd": float(p.profit), "bars_held": len(closed), **res})

    def _maybe_enter(self, tick) -> None:
        if time.time() - self.last_signal_at < self.cooldown_s:
            return
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1,
                                             self.params.push_bars + self.params.range_bars)
        opens = self._forming_opens()
        if rates is None or len(rates) == 0 or not opens:
            return
        bid, ask = float(tick.bid), float(tick.ask)
        spread = ask - bid
        sig = detect_impulse(_bars(rates), (bid + ask) / 2, opens,
                             ImpulseParams(self.params.push_bars, self.params.range_bars,
                                           self.params.min_tf_agree, sl_buffer=spread, spike_mult=self.spike_mult))
        if sig is None:
            return
        self.last_signal_at = time.time()
        ev = {"event": "IMPULSO_SIGNAL", "symbol": self.symbol, "bid": bid, "ask": ask, "spread": round(spread, 2),
              **sig.as_dict(), "send_orders": self.send}
        block, zone = self._zone_reason(sig.side, sig.price)
        if self.regime.paused:
            block = "regimen_pausa"
        elif sig.side not in allowed_sides(self.trend):
            block = "contra_tendencia"
        if spread > self.max_spread:
            block = "spread_caro"
        elif load_halt(self.halt_path) is not None:
            block = "impulso_halt"
        ev.update(block_reason=block, zone=zone, trend=self.trend)
        self.log(ev)
        if block or not self.send:
            return
        busy = {int(p.magic) for p in self.mt5.positions_get(symbol=self.symbol) or ()}
        per_usd = 1.0 / (self.lots * self._usd_per_price())
        for magic, tp_usd, tag in ((self.magic_fixed, self.tp_fixed_usd, "imp_fijo"),
                                   (self.magic_fade, self.tp_fade_usd, "imp_fade")):
            if magic in busy:
                self.log({"event": "IMPULSO_SKIP", "magic": magic, "reason": "ya_hay_posicion"})
                continue
            entry = ask if sig.side == BUY else bid
            tp = entry + sig.side * tp_usd * per_usd
            res = self._open(sig.side, sig.sl, tp, magic, tag)
            self.log({"event": "IMPULSO_OPEN", "magic": magic, "variant": tag, "entry": entry, "sl": sig.sl,
                      "tp": round(tp, 2), "risk_usd": round(abs(entry - sig.sl) / per_usd, 2), "lots": self.lots, **res})
            if res.get("ok") and res.get("position"):
                self.open_tickets[int(res["position"])] = magic

    # ── orders ──
    def _open(self, side: int, sl: float, tp: float, magic: int, comment: str) -> dict:
        tick = self.mt5.symbol_info_tick(self.symbol)
        m = self.mt5
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": self.lots,
               "type": m.ORDER_TYPE_BUY if side == BUY else m.ORDER_TYPE_SELL,
               "price": float(tick.ask if side == BUY else tick.bid), "sl": float(sl), "tp": float(tp),
               "deviation": 50, "magic": magic, "comment": comment, "type_time": m.ORDER_TIME_GTC}
        return self._send(req)

    def _close(self, p, tick, comment: str) -> dict:
        m = self.mt5
        buy = int(p.type) == 0
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": float(p.volume),
               "type": m.ORDER_TYPE_SELL if buy else m.ORDER_TYPE_BUY, "position": int(p.ticket),
               "price": float(tick.bid if buy else tick.ask), "deviation": 50, "magic": int(p.magic),
               "comment": comment[:31], "type_time": m.ORDER_TIME_GTC}
        return self._send(req)

    def _send(self, req: dict) -> dict:
        m = self.mt5
        result = None
        for fill in (m.ORDER_FILLING_IOC, m.ORDER_FILLING_FOK, getattr(m, "ORDER_FILLING_RETURN", m.ORDER_FILLING_IOC)):
            req["type_filling"] = fill
            result = m.order_send(req)
            if result is None:
                return {"ok": False, "retcode": -1, "comment": str(m.last_error())}
            if int(result.retcode) != RETCODE_UNSUPPORTED_FILLING:
                break
        rc = int(result.retcode)
        pos = int(getattr(result, "order", 0) or 0)
        if rc == int(m.TRADE_RETCODE_DONE) and req.get("position") is None:
            deal = m.history_deals_get(ticket=int(getattr(result, "deal", 0) or 0)) or ()
            pos = int(deal[0].position_id) if deal else pos
        return {"ok": rc == int(m.TRADE_RETCODE_DONE), "retcode": rc, "fill_price": float(result.price or 0.0),
                "position": pos, "broker_comment": str(result.comment)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="log signals, send nothing")
    args = ap.parse_args()

    _load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import MetaTrader5 as mt5

    err = _connect(mt5)
    if err:
        logging.error(err)
        return 1
    send = not args.dry_run and _env("BOT_IMPULSO_DEMO_LIVE", "0") == "1"
    log = _make_logger(Path(_env("BOT_IMPULSO_LOG", "impulso_trades.log")))
    a = mt5.account_info()
    eng = Engine(mt5, send=send, log=log)
    eng.open_tickets = {int(p.ticket): int(p.magic) for p in mt5.positions_get(symbol=eng.symbol) or ()
                        if int(p.magic) in (eng.magic_fixed, eng.magic_fade, eng.magic_rej, eng.magic_fb)}
    eng.zones()  # log the zones at start, not only at the first signal
    eng._update_regime()
    log({"event": "IMPULSO_START", "login": a.login, "server": a.server, "send_orders": send,
         "symbol": eng.symbol, "lots": eng.lots, "tp_fixed_usd": eng.tp_fixed_usd, "tp_fade_usd": eng.tp_fade_usd,
         "rejection": eng.rej_enabled, "tp_rejection_usd": eng.tp_rej_usd, "impulse": eng.impulse_enabled,
         "false_break": eng.fb_enabled, "start_balance": eng.start_balance, "risk_floor_usd": eng.risk_floor_usd,
         "risk_share": eng.risk_share})
    poll = _env_float("BOT_IMPULSO_POLL_SECONDS", 2.0)
    try:
        while True:
            try:
                eng.step()
            except Exception:
                logging.exception("impulso cycle failed; retrying")
                time.sleep(5)
            time.sleep(poll)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
