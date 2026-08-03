from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime

import MetaTrader5 as mt5

import bot_mt5
import telegram_notify


def _rotate_watcher_log_on_start(*, src: str = "trades_watcher.log", logs_dir: str = "logs") -> None:
    """
    Rotate watcher log on each watcher start:
    - If `src` exists and has content, move it to `logs/trades_watcher_YYYY-MM-DD_HHMMSS.log`.
    - Ensure a fresh empty `src` exists for the new session.
    Best-effort: never block watcher start because of rotation.
    """
    try:
        if not os.path.isfile(src):
            return
        if os.path.getsize(src) <= 0:
            return
        os.makedirs(logs_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dst = os.path.join(logs_dir, f"trades_watcher_{stamp}.log")
        os.replace(src, dst)
        with open(src, "w", encoding="utf-8"):
            pass
    except Exception:
        try:
            logging.exception("watcher_ladder: failed to rotate trades_watcher.log on start.")
        except Exception:
            pass


def _setup_watcher_logging(filename: str = "trades_watcher.log") -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(filename, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def _wlog_event(payload: dict) -> None:
    """
    Watcher structured log (JSON) to trades_watcher.log.
    Independent from bot_mt5._log_event (which writes to trades.log).
    """
    try:
        logging.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logging.info("%s", payload)


def _find_position_by_ticket(symbol: str, ticket: int):
    ps = mt5.positions_get(symbol=symbol)
    if not ps:
        return None
    for p in ps:
        if int(getattr(p, "ticket", 0) or 0) == int(ticket):
            return p
    return None


def _force_lock_ticket(cfg: bot_mt5.Config, *, ticket: int, lock_usd: float) -> bool:
    """
    Force-set SL to lock `lock_usd` profit for the given ticket (best-effort).
    Returns True if a change was applied.
    """
    positions = mt5.positions_get()
    if not positions:
        _wlog_event({"event": "WATCHER_FORCE_LOCK_NOT_FOUND", "ts": datetime.now().isoformat(timespec="seconds"), "ticket": ticket})
        return False

    target = None
    for p in positions:
        if int(getattr(p, "ticket", 0) or 0) == int(ticket):
            target = p
            break
    if target is None:
        _wlog_event({"event": "WATCHER_FORCE_LOCK_NOT_FOUND", "ts": datetime.now().isoformat(timespec="seconds"), "ticket": ticket})
        return False

    if not bot_mt5._position_is_managed_by_bot(target, cfg):
        _wlog_event(
            {
                "event": "WATCHER_FORCE_LOCK_NOT_MANAGED",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "ticket": ticket,
                "magic": int(getattr(target, "magic", -1) or -1),
                "bot_magic": int(cfg.magic),
            }
        )
        return False

    symbol = str(getattr(target, "symbol", "") or "")
    info = mt5.symbol_info(symbol) if symbol else None
    tick = mt5.symbol_info_tick(symbol) if symbol else None
    if not symbol or info is None or tick is None:
        return False

    side = "BUY" if int(getattr(target, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
    lot = float(getattr(target, "volume", 0.0) or 0.0)
    entry_price = float(getattr(target, "price_open", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 5) or 5)
    profit = float(getattr(target, "profit", 0.0) or 0.0)
    sl_before = float(getattr(target, "sl", 0.0) or 0.0) or None

    lock_sl = bot_mt5._sl_price_to_lock_profit_usd(symbol, side, entry_price, lot, float(lock_usd), digits=digits)
    if lock_sl is None:
        _wlog_event({"event": "WATCHER_FORCE_LOCK_NO_PRICE", "ts": datetime.now().isoformat(timespec="seconds"), "ticket": ticket})
        return False

    if not bot_mt5._sl_is_valid_now(side=side, sl=float(lock_sl), info=info, tick=tick):
        _wlog_event(
            {
                "event": "WATCHER_FORCE_LOCK_INVALID_SL",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "ticket": ticket,
                "symbol": symbol,
                "side": side,
                "profit": round(profit, 4),
                "sl_candidate": lock_sl,
                "bid": float(getattr(tick, "bid", 0.0) or 0.0),
                "ask": float(getattr(tick, "ask", 0.0) or 0.0),
            }
        )
        return False

    ok = bot_mt5._modify_position_sl_tp(symbol, ticket, float(lock_sl), None, side=side, info=info, tick=tick)
    p2 = _find_position_by_ticket(symbol, ticket)
    sl_after = float(getattr(p2, "sl", 0.0) or 0.0) if p2 is not None else float(getattr(target, "sl", 0.0) or 0.0)
    sl_after = sl_after or None
    applied = bool(ok) and (sl_after is not None) and (sl_before is None or abs(float(sl_after) - float(sl_before)) > 0)

    _wlog_event(
        {
            "event": "WATCHER_FORCE_LOCK",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "profit": round(profit, 4),
            "lock_usd": float(lock_usd),
            "sl_before": sl_before,
            "sl_after": sl_after,
            "requested_sl": lock_sl,
            "applied": bool(applied),
        }
    )
    return bool(applied)


def _sync_symbol_config_with_overlay(cfg: bot_mt5.Config) -> bot_mt5.Config:
    """
    Refresca symbol_config desde .env + overlay en disco (nuevo Config: Config es frozen).
    El bot puede escribir el overlay en caliente (régimen automático); el watcher lo re-lee cada ciclo.
    """
    merged, _ = bot_mt5.resolve_symbol_config_with_overlay()
    return replace(cfg, symbol_config=merged)


def _build_cfg_from_env() -> bot_mt5.Config:
    """
    Build a Config using the same env parsing as bot_mt5.main().
    We only need fields relevant to:
      - MT5 connection (handled in bot_mt5.connect_mt5 via env credentials)
      - position ownership (magic / track_zero_magic_positions)
      - ladder settings + per-symbol overrides
    """
    symbols_override = bot_mt5._env_str_list("BOT_SYMBOLS")
    merged, _ = bot_mt5.resolve_symbol_config_with_overlay()
    return bot_mt5.Config(
        symbols=symbols_override or bot_mt5.Config.symbols,
        symbol_config=merged,
        magic=int(float(bot_mt5._env("BOT_MAGIC", str(bot_mt5.Config.magic)))),
        track_zero_magic_positions=bot_mt5._env_bool("BOT_TRACK_ZERO_MAGIC_POSITIONS", True),
        usd_sl_ladder_enabled=bot_mt5._env_bool("BOT_USD_SL_LADDER_ENABLED", True),
        usd_sl_ladder_initial_sl_money=float(bot_mt5._env("BOT_USD_SL_LADDER_INITIAL_SL_USD", "0.8")),
        usd_ladder_wait_be_seconds=int(float(bot_mt5._env("BOT_USD_LADDER_WAIT_BE_SECONDS", "180"))),
        usd_ladder_step_seconds=int(float(bot_mt5._env("BOT_USD_LADDER_STEP_SECONDS", "120"))),
        usd_ladder_max_lock_usd=int(float(bot_mt5._env("BOT_USD_LADDER_MAX_LOCK_USD", "0"))),
        break_even_offset_points=float(bot_mt5._env("BOT_BREAK_EVEN_OFFSET_POINTS", "0")),
        micro_be_trigger_money=float(bot_mt5._env("BOT_MICRO_BE_TRIGGER_USD", "0")),
        hwm_trail_pct=float(bot_mt5._env("BOT_HWM_TRAIL_PCT", "0")),
        stagnant_timeout_minutes=float(bot_mt5._env("BOT_STAGNANT_TIMEOUT_MINUTES", "0")),
        stagnant_max_profit_threshold=float(bot_mt5._env("BOT_STAGNANT_MAX_FP_THRESHOLD", "0")),
        connect_retries=int(float(bot_mt5._env("BOT_CONNECT_RETRIES", str(bot_mt5.Config.connect_retries)))),
        connect_retry_sleep=float(bot_mt5._env("BOT_CONNECT_RETRY_SLEEP", str(bot_mt5.Config.connect_retry_sleep))),
        # keep defaults for the rest
    )


def _apply_ladder_once(cfg: bot_mt5.Config) -> bot_mt5.Config:
    if not bool(cfg.usd_sl_ladder_enabled):
        return cfg

    cfg = _sync_symbol_config_with_overlay(cfg)

    positions = mt5.positions_get()
    if not positions:
        return cfg

    managed = 0
    attempted = 0
    changed = 0

    for p in positions:
        if not bot_mt5._position_is_managed_by_bot(p, cfg):
            continue
        managed += 1
        symbol = str(getattr(p, "symbol", "") or "")
        if not symbol:
            continue
        info = mt5.symbol_info(symbol)
        if info is None:
            continue

        pr = bot_mt5._effective_params(cfg, symbol)
        side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
        lot = float(getattr(p, "volume", 0.0) or 0.0)
        ticket = int(getattr(p, "ticket", 0) or 0)
        entry_price = float(getattr(p, "price_open", 0.0) or 0.0)
        profit = float(getattr(p, "profit", 0.0) or 0.0)
        digits = int(getattr(info, "digits", 5) or 5)
        sl_before = float(getattr(p, "sl", 0.0) or 0.0) or None

        if ticket <= 0 or entry_price <= 0 or lot <= 0:
            continue

        attempted += 1
        bot_mt5._apply_usd_sl_ladder(
            cfg,
            symbol,
            p,
            pr,
            side=side,
            lot=lot,
            digits=digits,
            entry_price=entry_price,
            ticket=ticket,
            profit=profit,
        )

        p2 = _find_position_by_ticket(symbol, ticket)
        sl_after = float(getattr(p2, "sl", 0.0) or 0.0) if p2 is not None else float(getattr(p, "sl", 0.0) or 0.0)
        sl_after = sl_after or None
        sl_changed = (sl_before is None and sl_after is not None) or (
            sl_before is not None and sl_after is not None and abs(float(sl_after) - float(sl_before)) > 0
        )
        if sl_changed:
            changed += 1
            if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_WATCHER_SL", True):
                try:
                    sl_txt = "—" if sl_after is None else f"{float(sl_after):.{digits}f}"
                    profit_txt = f"{float(profit):+.2f}"
                    telegram_notify.notify(
                        "meta-trader (watcher): SL actualizado\n"
                        f"{symbol} {side} · ticket {ticket}\n"
                        f"PnL {profit_txt} · SL {sl_txt}",
                        dedup_key=f"watcher_sl:{symbol}:{ticket}:{sl_txt}",
                        min_interval_sec=10.0,
                    )
                    _wlog_event(
                        {
                            "event": "WATCHER_TELEGRAM_SENT",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol,
                            "ticket": ticket,
                            "side": side,
                            "profit": round(float(profit), 4),
                            "sl_after": sl_after,
                        }
                    )
                except Exception:
                    _wlog_event(
                        {
                            "event": "WATCHER_TELEGRAM_FAIL",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol,
                            "ticket": ticket,
                        }
                    )

        st_lad = bot_mt5._USD_SL_LADDER_STATE.get(ticket, {})
        ot = st_lad.get("open_ts")
        if ot is not None:
            age_sec_log = max(0.0, time.time() - float(ot))
        else:
            age_sec_log = float(bot_mt5._position_age_seconds(p))
        _wlog_event(
            {
                "event": "WATCHER_LADDER_APPLY",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "ticket": ticket,
                "side": side,
                "profit": round(float(profit), 4),
                "entry_price": round(float(entry_price), digits),
                "sl_before": sl_before,
                "sl_after": sl_after,
                "wait_be_s": int(pr.get("usd_ladder_wait_be_seconds", cfg.usd_ladder_wait_be_seconds) or 0),
                "step_s": int(pr.get("usd_ladder_step_seconds", cfg.usd_ladder_step_seconds) or 0),
                "max_lock_usd": int(pr.get("usd_ladder_max_lock_usd", cfg.usd_ladder_max_lock_usd) or 0),
                "age_sec": round(float(age_sec_log), 1),
                "ladder_state": dict(st_lad),
            }
        )

    _wlog_event(
        {
            "event": "WATCHER_TICK",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "positions_total": int(len(positions)),
            "positions_managed": int(managed),
            "attempted": int(attempted),
            "sl_changed": int(changed),
        }
    )

    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch-only USD ladder manager (every N seconds).")
    ap.add_argument("--interval", type=float, default=15.0, help="Seconds between ladder checks (default: 15).")
    ap.add_argument("--force-ticket", type=int, default=0, help="One-shot: force SL update for this position ticket.")
    ap.add_argument("--force-lock-usd", type=float, default=0.0, help="One-shot: lock this USD profit via SL (e.g. 2.0).")
    args = ap.parse_args()

    bot_mt5._load_dotenv()
    _rotate_watcher_log_on_start()
    _setup_watcher_logging()

    cfg = _build_cfg_from_env()
    logging.info(
        "watcher_ladder: starting | interval=%.1fs | magic=%s | symbols=%s | symbol_config_overlay=%s",
        float(args.interval),
        cfg.magic,
        cfg.symbols,
        "off" if bot_mt5._env_bool("BOT_SYMBOL_CONFIG_OVERLAY_DISABLED", False) else str(bot_mt5.SYMBOL_CONFIG_OVERLAY_FILE),
    )
    _wlog_event(
        {
            "event": "WATCHER_START",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "interval_sec": float(args.interval),
            "magic": int(cfg.magic),
            "symbols": list(cfg.symbols),
        }
    )

    if not bot_mt5.connect_mt5(cfg):
        logging.error("watcher_ladder: could not connect to MT5.")
        _wlog_event({"event": "WATCHER_CONNECT_FAIL", "ts": datetime.now().isoformat(timespec="seconds")})
        return 2

    # One-shot mode: force-lock a ticket and exit.
    if int(args.force_ticket or 0) > 0 and float(args.force_lock_usd or 0.0) > 0:
        ok = _force_lock_ticket(cfg, ticket=int(args.force_ticket), lock_usd=float(args.force_lock_usd))
        if ok and telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_WATCHER_SL", True):
            telegram_notify.notify(
                f"meta-trader (watcher): force lock OK\n"
                f"ticket {int(args.force_ticket)} · lock +${float(args.force_lock_usd):.2f}",
                dedup_key=f"watcher_force:{int(args.force_ticket)}:{float(args.force_lock_usd):.2f}",
                min_interval_sec=5.0,
            )
        return 0 if ok else 3

    try:
        while True:
            t0 = time.monotonic()
            try:
                if not mt5.terminal_info():
                    logging.warning("watcher_ladder: MT5 terminal_info unavailable. Reconnecting...")
                    mt5.shutdown()
                    bot_mt5.connect_mt5(cfg)
                    _wlog_event({"event": "WATCHER_RECONNECT", "ts": datetime.now().isoformat(timespec="seconds")})

                cfg = _apply_ladder_once(cfg)
            except Exception:
                logging.exception("watcher_ladder: unhandled exception in loop (non-fatal).")
                _wlog_event({"event": "WATCHER_LOOP_EXCEPTION", "ts": datetime.now().isoformat(timespec="seconds")})

            elapsed = time.monotonic() - t0
            sleep_s = max(0.0, float(args.interval) - elapsed)
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        logging.info("watcher_ladder: stopping (Ctrl+C) at %s", datetime.now().isoformat(timespec="seconds"))
        _wlog_event({"event": "WATCHER_STOP", "ts": datetime.now().isoformat(timespec="seconds")})
    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

