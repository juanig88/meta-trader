import os
import sys
import time
import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Optional, Tuple, Literal, Any

import MetaTrader5 as mt5

import telegram_notify
from trading.domain.position_sizing import clamp_lot_volume
from trading.domain.htf_bias import (
    effective_max_spread_points,
)
from trading.domain.currency_exposure import (
    ExposureLimits,
    PositionLot,
    check_new_trade,
    parse_currency_overrides,
)
from trading.domain.xbtfx import connection_decision
from trading.infrastructure.xbtfx_session import (
    account_margin_status,
    enforcement_enabled,
    missing_symbol_message,
    server_markers,
    snapshot_symbol,
)
from trading.infrastructure.manual_whitelist_file import ticket_is_manually_excepted


Signal = Literal["BUY", "SELL", "NONE"]
Trend = Literal["UP", "DOWN", "FLAT"]

_last_trade_time: dict[str, datetime] = {}
# Tras rechazo de order_send (broker): no reintentar abrir hasta pasado failed_attempt_cooldown_seconds.
_last_failed_order_attempt: dict[str, datetime] = {}
# Ventana deslizante de aperturas por símbolo (rate-limit / hora reales).
# First time manage_positions saw a rotation ticket (wall clock; broker time can be skewed).
_ROTATION_FIRST_SEEN: dict[int, float] = {}
# Last rotation open audit by position_id / symbol (joined onto TRADE_CLOSE).
_ROTATION_OPEN_AUDIT: dict[int, dict[str, Any]] = {}
_ROTATION_OPEN_AUDIT_SYM: dict[str, dict[str, Any]] = {}
# In-process peak/trough of magic-book floating net (reset on process start).
_ROTATION_BOOK_WATERMARKS: dict[str, Any] = {}
_ROTATION_BOOK_LAST_MONO: float = 0.0
# Escalera USD (BE + locks +1..+N) con tiempos entre pasos; se limpia al cerrar la posición.

# Deals de cierre (DEAL_ENTRY_OUT) ya notificados por Telegram (evita duplicados).
_TELEGRAM_SEEN_OUT_DEAL_TICKETS: set[int] = set()
_TELEGRAM_CLOSE_TRACKER_SEEDED: bool = False

_CLOSE_LOG_SEEN_DEAL_TICKETS: set[int] = set()
_CLOSE_LOG_TRACKER_SEEDED: bool = False
_CLOSE_LOG_BACKFILL_DONE: bool = False
# How far back to look at startup when backfilling missing TRADE_CLOSE events.
# Covers bot restarts / code upgrades mid-session without flooding the log.
_CLOSE_LOG_BACKFILL_DAYS: int = 7
# Each TRADE_CLOSE pulls candles for grades (~2 s); a large backlog must not stall the cycle.
_CLOSE_LOG_MAX_PER_CALL: int = 5
_spread_ema_points: dict[str, float] = {}
_ROTATION_OVERRIDE: dict[str, dict] = {}
_ROTATION_LAST_M1: dict[str, int] = {}
_LAST_ROTATION_OPEN_SKIP: dict[str, str] = {}
_portfolio_dynamic_weights: dict[str, float] = {}
_portfolio_last_rebalance: Optional[datetime] = None
_TRADES_LOG_FILENAME = "trades.log"

_XBTFX_CONTRACT_LOGGED: set[str] = set()
# position_id → magic del deal de entrada, para cierres fuera de la ventana de historial.
_POSITION_IN_MAGIC_CACHE: dict[int, int] = {}


@dataclass(frozen=True)
class Config:
    symbols: Tuple[str, ...] = ("EURUSD", "USDJPY")
    lot: float = 0.01
    poll_seconds: int = 30
    # Durante el tiempo entre ciclos completos, volver a gestionar SL/TP al menos cada N segundos
    # (solo `manage_positions`; el resto del loop sigue en `poll_seconds`).
    manage_positions_interval_seconds: float = 5.0
    symbol_config: dict[str, dict] = field(default_factory=dict)

    # Money-based targets in account currency (commonly EUR/USD depending on account).
    sl_money: float = -0.80
    tp_money: float = 1.00

    max_spread_points: float = 25.0
    spread_dynamic_enabled: bool = False
    spread_dynamic_alpha: float = 0.15  # EMA smoothing for avg spread
    spread_dynamic_mult: float = 1.5  # current spread must be <= mult * avg spread
    magic: int = 260414
    deviation_points: int = 20

    # Global risk controls
    max_open_trades: int = 5
    daily_loss_limit: float = -60.0  # stop opening new trades if daily PnL <= this (account currency)
    # Máximo de órdenes de apertura por símbolo por hora (0 = sin límite global). Crypto usa `max_trades_per_hour_crypto_default` si esto es 0.
    max_trades_per_hour_per_symbol: int = 0
    max_trades_per_hour_crypto_default: int = 8
    # Rotation engine (5 modules, 4/5 + HTF veto).
    dry_run: bool = True
    paper_demo_live: bool = False
    ensemble_min_agree: int = 4
    max_risk_usd: float = 0.50
    max_opens_per_hour: int = 60
    # After this many seconds, close if profit < tiny_loss floor or > take.
    # Fill MTM 2026-10-03: +5m least-bad EV on pre-invert fills vs 15/30/60m.
    rotation_stale_seconds: float = 300.0
    rotation_stale_tiny_loss_usd: float = -0.0001
    rotation_stale_take_usd: float = 0.01
    rotation_stale_close_all: bool = True
    # Skip when one-way spread USD exceeds this fraction of the $ risk (rotation only).
    rotation_max_spread_usd_frac: float = 0.10
    # JSON ROTATION_BOOK in trades.log. 0 = off.
    rotation_book_snapshot_seconds: float = 60.0
    # Flatten the whole rotation book when floating net (profit+swap+comm) clears this USD.
    # 0 = off. Min age is the youngest ticket (open time). Min open avoids a single 0.50 TP.
    rotation_book_harvest_usd: float = 0.50
    rotation_book_harvest_min_age_seconds: float = 600.0
    rotation_book_harvest_min_open: int = 2
    kill_switch_flatten: bool = False
    kill_switch_armed: bool = True
    # Tras order_send rechazado (retcode ≠ DONE): espera antes de otra entrada en ese símbolo. 0 = desactivado.
    failed_attempt_cooldown_seconds: float = 120.0

    # --- Monthly loss limit (Elder's 6% Rule, New Trading for a Living ch.9) ---
    # When total "at risk" this month exceeds `monthly_loss_limit_pct` of the
    # balance at the 1st of the month, STOP opening new trades for the rest of
    # the month. "At risk" = realized losses so far + potential loss if every
    # currently open position hit its SL (if `include_open_risk` is True).
    # This is Elder's explicit rule to escape cascades of small losses that
    # add up to a serious drawdown before the 2%-per-trade cap catches it.
    monthly_loss_limit_enabled: bool = False
    monthly_loss_limit_pct: float = 6.0
    monthly_loss_limit_include_open_risk: bool = True

    # Pyramiding (multiple positions per symbol)
    # max_positions_per_symbol = 1 disables pyramiding (default).
    # When > 1, additional entries on the same symbol are allowed under guards below.
    max_positions_per_symbol: int = 1
    # If True, only allow a new entry on the same symbol when ALL existing positions
    # on that symbol already have SL at break-even (or better). Highly recommended.
    pyramiding_require_be: bool = True
    # Minimum price separation (in broker points) from the most recent same-side entry
    # before allowing another entry. 0 disables this guard.
    pyramiding_min_separation_points: float = 0.0

    # Portfolio weights: only fill the `portfolio` block of bot_state.json (rotation lot ignores them).
    portfolio_enabled: bool = True
    portfolio_weights: dict[str, float] = field(default_factory=dict)

    # Portfolio weights dynamic rebalance (live): compute weights from realized PnL per symbol (history deals)
    portfolio_dynamic_enabled: bool = False
    portfolio_dynamic_lookback_days: int = 14
    portfolio_dynamic_rebalance_minutes: int = 60
    portfolio_dynamic_smoothing_alpha: float = 0.35  # EMA of weights (0..1). Higher = react faster.
    portfolio_dynamic_min_weight: float = 0.05  # floor so a symbol doesn't get fully disabled
    portfolio_dynamic_max_weight: float = 0.60  # cap to avoid concentration
    portfolio_dynamic_use_positive_only: bool = True  # if True: clamp negative pnl to 0 before scoring

    # Trading window (local PC clock by default; see BOT_TRADING_WINDOW_SESSIONS / _TIMEZONE)
    start_hour: int = 11
    end_hour: int = 16  # inclusive end: trades allowed up to 16:00:00 (legacy hour-only gate)
    # Extra cutoff to avoid opening trades close to end of session
    no_new_trades_after_hour: int = 15
    no_new_trades_after_minute: int = 30
    trading_window_enabled: bool = False
    # Rangos HH:MM–HH:MM separados por coma (ej. 06:00-12:00,13:00-18:00). Vacío ⇒ solo start_hour/end_hour.
    trading_window_sessions: Tuple[Tuple[dt_time, dt_time], ...] = ()
    # LOCAL = mismo reloj que las demás barreras (now del loop). UTC/GMT = reloj de pared UTC (broker-independiente).
    trading_window_timezone: str = "LOCAL"
    weekend_block_enabled: bool = True
    # Símbolos que ignoran el bloqueo de fin de semana (ej. crypto CFD 24/7) cuando `weekend_block_enabled`.
    weekend_trade_symbols: Tuple[str, ...] = ()
    friday_cutoff_enabled: bool = True
    new_trade_cutoff_enabled: bool = True

    # Fracción máxima del margen libre que puede consumir UNA orden (0 = no capar por margen).
    # Evita 0.05/0.10 en cuentas chicas (margen > equity) y deja aire ante ruido M1.
    lot_max_margin_frac: float = 0.50

    # Connection retry
    connect_retries: int = 5
    connect_retry_sleep: float = 2.0

    # News-time block (simple)
    news_block_hours: Tuple[int, ...] = ()  # e.g. (14, 15)

    # --- Quiet-session filter (FX) ---
    # Block new entries during low-liquidity hours (post-NY close + Asia-only hours)
    # for symbols NOT in `quiet_session_exempt_symbols`. Crypto 24/7 should be in the
    # exempt list. Hours are in the bot's LOCAL timezone (same clock as now()).
    # Default off so existing deployments behave unchanged.
    quiet_session_block_enabled: bool = False
    quiet_session_hours: Tuple[int, ...] = (23, 0, 1, 2, 3)
    quiet_session_exempt_symbols: Tuple[str, ...] = ()

    # BTCUSD spread 1350 pts on 25-Aug 21:05. Caps per-symbol max_spread_points. 0 = off.
    btcusd_max_spread_points: float = 1000.0

    # Base/quote net/gross lot caps. Off by default; see trading/domain/currency_exposure.py.
    currency_exposure_enabled: bool = False
    max_net_exposure_per_currency: float = 0.0  # lots; 0 = no net cap
    max_gross_exposure_per_currency: float = 0.0  # lots; 0 = no gross cap
    currency_symbol_overrides: dict[str, tuple[str, str]] = field(default_factory=dict)

    # Reconciliación tras reinicio: muchas posiciones manuales / recovery vienen con magic=0.
    track_zero_magic_positions: bool = True
    # Si 1: al arrancar cancela órdenes pendientes (limit/stop) con nuestro magic en símbolos del bot.
    cancel_orphan_pending_on_start: bool = False


def _log_event(payload: dict) -> None:
    """
    Structured log line (JSON) so it can be parsed later for stats.
    """
    try:
        logging.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logging.info("%s", payload)
    try:
        from trading.infrastructure.persistence.ensemble_store import insert_trade_event

        insert_trade_event(payload)
    except Exception:
        pass


def _log_rotation_reject(symbol: str, side: str, reason: str, **extra: Any) -> None:
    """Why a 4/5 fire did not become a fill."""
    payload: dict[str, Any] = {
        "event": "ROTATION_OPEN_REJECT",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "engine": "rotation",
        "symbol": symbol,
        "side": side,
        "reason": reason,
        "why_open": "consenso_4_5",
        "why_not_filled": reason,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    _LAST_ROTATION_OPEN_SKIP[str(symbol).strip().upper()] = str(reason)
    _log_event(payload)


def _spread_usd_now(symbol: str, lot: float) -> Optional[float]:
    """One-way bid/ask hole in USD for ``lot`` at the current tick. None if no quote."""
    from trading.domain.rotation_stops import spread_usd_for_lot

    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return None
    spread_px = abs(float(getattr(tick, "ask", 0.0) or 0.0) - float(getattr(tick, "bid", 0.0) or 0.0))
    hole = spread_usd_for_lot(
        spread_px=spread_px,
        lot=float(lot),
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
    )
    if hole is None:
        return None
    return round(float(hole), 6)


def _patch_ensemble_skip(state: dict, symbol: str, reason: str) -> None:
    """Home/Lab still showed LIVE after a 4/5 fill was rejected (spread, SL)."""
    ens = state.get("ensemble")
    if not isinstance(ens, dict):
        return
    key = str(symbol).strip().upper()
    why = str(reason or "")
    for row in ens.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").strip().upper() != key:
            continue
        row["action"] = "SKIP"
        row["skip_reason"] = why
    fired = ens.get("fired")
    if isinstance(fired, list):
        kept = []
        for row in fired:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").strip().upper() == key:
                row["action"] = "SKIP"
                row["skip_reason"] = why
                continue
            kept.append(row)
        ens["fired"] = kept
        ens["fired_count"] = len(kept)
    counts: dict[str, int] = {}
    for row in ens.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        r = str(row.get("skip_reason") or "")
        if r:
            counts[r] = counts.get(r, 0) + 1
    ens["skip_counts"] = counts


def _save_state(state: dict) -> None:
    """
    Writes bot state atomically to bot_state.json (used by dashboard/backtest tooling).
    """
    target = "bot_state.json"
    # Use a unique tmp name to avoid collisions if multiple processes run.
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

        # On Windows, replacing can fail transiently if another process is reading the file.
        # We retry a few times; if it still fails, fall back to direct write.
        last_exc: Exception | None = None
        for _ in range(8):
            try:
                os.replace(tmp, target)
                return
            except PermissionError as e:
                last_exc = e
                time.sleep(0.05)
            except OSError as e:
                last_exc = e
                time.sleep(0.05)

        try:
            with open(target, "w", encoding="utf-8") as f2:
                json.dump(state, f2, ensure_ascii=False, indent=2, default=str)
        except Exception:
            if last_exc is not None:
                logging.warning("Could not atomically replace %s (will continue). err=%r", target, last_exc)
            raise
        if last_exc is not None:
            logging.warning("Atomic replace failed for %s; used direct write instead. err=%r", target, last_exc)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _save_stopped_state(reason: str) -> None:
    """Mark bot as not running (Ctrl+C, error exit, etc.)."""
    try:
        _save_state(
            {
                "running": False,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": "STOPPED",
                "stop_reason": reason,
                "pid": os.getpid(),
            }
        )
    except Exception:
        logging.exception("Could not write stopped state to bot_state.json.")


def _parse_symbol_config(raw: Optional[str]) -> dict[str, dict]:
    """
    Parse per-symbol overrides from JSON.
    Example:
      {"BTCUSD!": {"max_spread_points": 5000, "max_positions_per_symbol": 1}}
    """
    if raw is None or raw.strip() == "":
        return {}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, dict] = {}
        for k, v in obj.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            out[k.strip()] = v
        return out
    except Exception:
        logging.exception("Failed to parse BOT_SYMBOL_CONFIG JSON.")
        return {}


def _parse_portfolio_weights(
    raw: Optional[str],
    symbols: Tuple[str, ...],
    *,
    renormalize: bool = True,
) -> dict[str, float]:
    """
    Accepts:
      - "GBPUSD=0.4,USDJPY=0.4,NZDJPY=0.2"
      - JSON dict: {"GBPUSD":0.4,"USDJPY":0.4,"NZDJPY":0.2}
    Default: normalized weights for `symbols` (sum to 1).
    Set renormalize=False to keep raw sleeves (unused budget stays unused).
    """
    if not symbols:
        return {}

    def _equal() -> dict[str, float]:
        w = 1.0 / float(len(symbols))
        return {s: w for s in symbols}

    if raw is None or raw.strip() == "":
        return _equal()

    raw = raw.strip()
    parsed: dict[str, float] = {}
    try:
        if raw.startswith("{"):
            obj = json.loads(raw)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(k, str):
                        try:
                            parsed[k.strip()] = float(v)
                        except Exception:
                            continue
        else:
            for part in raw.split(","):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip()
                try:
                    parsed[k] = float(v.strip())
                except Exception:
                    continue
    except Exception:
        return _equal()

    cleaned = {s: max(0.0, float(parsed.get(s, 0.0))) for s in symbols}
    total = sum(cleaned.values())
    if total <= 0:
        return _equal()
    if not renormalize:
        return cleaned
    return {s: float(cleaned[s] / total) for s in symbols}


def _normalize_weights(
    symbols: Tuple[str, ...],
    raw: dict[str, float],
    *,
    min_w: float,
    max_w: float,
) -> dict[str, float]:
    """
    Normalize and clamp weights for the configured symbols.
    - Ensures all symbols exist in output
    - Applies min/max clamps
    - Renormalizes to sum to 1.0
    """
    if not symbols:
        return {}
    cleaned = {s: float(raw.get(s, 0.0) or 0.0) for s in symbols}
    # clamp to [0, +inf) first
    cleaned = {s: max(0.0, v) for s, v in cleaned.items()}
    total = sum(cleaned.values())
    if total <= 0:
        eq = 1.0 / float(len(symbols))
        cleaned = {s: eq for s in symbols}
    else:
        cleaned = {s: cleaned[s] / total for s in symbols}

    # clamp min/max, then renormalize again
    min_w = max(0.0, float(min_w))
    max_w = max(min_w, float(max_w))
    clamped = {s: min(max_w, max(min_w, float(cleaned[s]))) for s in symbols}
    tot2 = sum(clamped.values())
    if tot2 <= 0:
        eq = 1.0 / float(len(symbols))
        return {s: eq for s in symbols}
    return {s: float(clamped[s] / tot2) for s in symbols}


def _maybe_rebalance_portfolio_weights(cfg: Config, now: Optional[datetime] = None) -> None:
    """
    Live-only helper: compute portfolio weights from realized PnL per symbol over a lookback window.
    Uses history_deals_get filtered by cfg.magic and cfg.symbols.
    Writes to global _portfolio_dynamic_weights.
    """
    global _portfolio_last_rebalance
    now = now or datetime.now()
    if not bool(cfg.portfolio_enabled) or not bool(cfg.portfolio_dynamic_enabled):
        return
    if not cfg.symbols:
        return

    # throttle
    if _portfolio_last_rebalance is not None:
        mins = float(cfg.portfolio_dynamic_rebalance_minutes)
        if mins > 0 and (now - _portfolio_last_rebalance).total_seconds() < mins * 60.0:
            return

    start = now - timedelta(days=int(max(1, cfg.portfolio_dynamic_lookback_days)))
    deals = mt5.history_deals_get(start, now)
    if deals is None:
        return

    pnl_by_sym: dict[str, float] = {s: 0.0 for s in cfg.symbols}
    for d in deals:
        if int(getattr(d, "magic", 0) or 0) != int(cfg.magic):
            continue
        sym = str(getattr(d, "symbol", "") or "")
        if sym not in pnl_by_sym:
            continue
        pnl = float(getattr(d, "profit", 0.0) or 0.0)
        pnl += float(getattr(d, "swap", 0.0) or 0.0)
        pnl += float(getattr(d, "commission", 0.0) or 0.0)
        pnl_by_sym[sym] += pnl

    # scores: either positive-only (\"winners get more\") or shift to non-negative
    if bool(cfg.portfolio_dynamic_use_positive_only):
        scores = {s: max(0.0, float(pnl_by_sym.get(s, 0.0))) for s in cfg.symbols}
    else:
        # shift so min is 0
        mn = min(float(pnl_by_sym.get(s, 0.0)) for s in cfg.symbols)
        scores = {s: float(pnl_by_sym.get(s, 0.0)) - mn for s in cfg.symbols}

    # if all are zero, fall back to configured static weights
    if sum(scores.values()) <= 0:
        target = dict(cfg.portfolio_weights)
    else:
        target = dict(scores)

    target = _normalize_weights(
        cfg.symbols,
        target,
        min_w=float(cfg.portfolio_dynamic_min_weight),
        max_w=float(cfg.portfolio_dynamic_max_weight),
    )

    # smooth (EMA over weights)
    alpha = float(cfg.portfolio_dynamic_smoothing_alpha)
    alpha = max(0.0, min(alpha, 1.0))
    if not _portfolio_dynamic_weights:
        _portfolio_dynamic_weights.update(target)
    else:
        blended = {}
        for s in cfg.symbols:
            prev = float(_portfolio_dynamic_weights.get(s, cfg.portfolio_weights.get(s, 0.0)) or 0.0)
            blended[s] = (alpha * float(target.get(s, 0.0))) + ((1.0 - alpha) * prev)
        _portfolio_dynamic_weights.clear()
        _portfolio_dynamic_weights.update(
            _normalize_weights(
                cfg.symbols,
                blended,
                min_w=float(cfg.portfolio_dynamic_min_weight),
                max_w=float(cfg.portfolio_dynamic_max_weight),
            )
        )

    _portfolio_last_rebalance = now


def _sym_get(cfg: Config, symbol: str, key: str, default):
    o = cfg.symbol_config.get(symbol, {})
    if isinstance(o, dict) and key in o:
        return o[key]
    return default


# Symbols whose names match these substrings use the relaxed crypto per-symbol defaults.
_CRYPTO_RELAXED_MARKERS: tuple[str, ...] = ("BTC", "ETH", "XBT", "SOL", "LTC", "XRP", "DOGE", "BNB")


def symbol_uses_crypto_relaxed_m15(symbol: str) -> bool:
    u = (symbol or "").upper()
    return any(m in u for m in _CRYPTO_RELAXED_MARKERS)


def _effective_max_trades_per_hour(cfg: Config, symbol: str) -> int:
    o = cfg.symbol_config.get(symbol, {})
    if isinstance(o, dict) and "max_trades_per_hour_per_symbol" in o:
        return max(0, int(float(o["max_trades_per_hour_per_symbol"])))
    base = int(getattr(cfg, "max_trades_per_hour_per_symbol", 0) or 0)
    if base > 0:
        return base
    if symbol_uses_crypto_relaxed_m15(symbol):
        return max(0, int(getattr(cfg, "max_trades_per_hour_crypto_default", 0) or 0))
    return 0


def _effective_params(cfg: Config, symbol: str) -> dict:
    """
    Returns effective parameters for a given symbol (cfg defaults overridden by cfg.symbol_config,
    then by `_ROTATION_OVERRIDE`).
    """
    p = {
        "sl_money": float(_sym_get(cfg, symbol, "sl_money", cfg.sl_money)),
        "tp_money": float(_sym_get(cfg, symbol, "tp_money", cfg.tp_money)),
        "max_spread_points": float(_sym_get(cfg, symbol, "max_spread_points", cfg.max_spread_points)),
        "max_positions_per_symbol": int(float(_sym_get(cfg, symbol, "max_positions_per_symbol", cfg.max_positions_per_symbol))),
        "pyramiding_require_be": bool(_sym_get(cfg, symbol, "pyramiding_require_be", cfg.pyramiding_require_be)),
        "pyramiding_min_separation_points": float(_sym_get(cfg, symbol, "pyramiding_min_separation_points", cfg.pyramiding_min_separation_points)),
        # lot>0 en BOT_SYMBOL_CONFIG fuerza volumen fijo (ej. XNGUSD volume_min=0.1).
        "lot": float(_sym_get(cfg, symbol, "lot", 0.0) or 0.0),
        "max_trades_per_hour_per_symbol": _effective_max_trades_per_hour(cfg, symbol),
    }
    ov = _ROTATION_OVERRIDE.get(str(symbol or "").strip().upper())
    if ov:
        p.update(ov)
    return p


def _setup_logging() -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(_TRADES_LOG_FILENAME, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def _rotate_trades_log_on_start(
    *, logs_dir: str = "logs", attempts: int = 10, retry_sleep_s: float = 0.5
) -> Optional[str]:
    """
    Rotate `trades.log` on each bot start:
    - If `trades.log` exists and has content, move it to `logs/trades_YYYY-MM-DD_HHMMSS.log`.
    - Create a fresh empty `trades.log` for the new session.

    Runs before `_setup_logging`, so it must not log: any logging call here makes
    Python attach its default root handler and `_setup_logging` would then skip the
    trades.log FileHandler. Returns an error message for the caller to log, or None.
    """
    src = _TRADES_LOG_FILENAME
    try:
        if not os.path.isfile(src):
            return None
        if os.path.getsize(src) <= 0:
            return None

        os.makedirs(logs_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dst = os.path.join(logs_dir, f"trades_{stamp}.log")

        # On Windows the rename fails while a previous bot process still holds the file.
        for i in range(max(1, int(attempts))):
            try:
                os.replace(src, dst)
                break
            except PermissionError:
                if i + 1 >= max(1, int(attempts)):
                    raise
                time.sleep(retry_sleep_s)

        # Ensure the new session has a fresh file ready before FileHandler attaches.
        with open(src, "w", encoding="utf-8"):
            pass
        return None
    except Exception as e:
        # Best-effort: never block bot start because of log rotation.
        return f"{type(e).__name__}: {e}"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int_list(name: str) -> Tuple[int, ...]:
    v = _env(name)
    if v is None:
        return ()
    out: list[int] = []
    for part in v.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out)


def _env_str_list(name: str) -> Optional[Tuple[str, ...]]:
    v = _env(name)
    if v is None:
        return None
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if not parts:
        return None
    return tuple(parts)


def _parse_hh_mm_token(tok: str) -> dt_time:
    tok = tok.strip()
    comps = tok.split(":")
    h = int(comps[0])
    mi = int(comps[1]) if len(comps) > 1 else 0
    return dt_time(h % 24, mi % 60)


def parse_trading_window_sessions(raw: str) -> Tuple[Tuple[dt_time, dt_time], ...]:
    """
    ``"06:00-12:00,13:00-18:00"`` → intervalos inclusivos por minuto (igual que start/end hora entera legacy).
    """
    s = raw.strip()
    if not s:
        return ()
    out: list[Tuple[dt_time, dt_time]] = []
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        left, dash, right = chunk.partition("-")
        if dash != "-":
            raise ValueError(f"segment without '-': {chunk!r}")
        out.append((_parse_hh_mm_token(left), _parse_hh_mm_token(right)))
    return tuple(out)


def _load_trading_window_sessions_from_env() -> Tuple[Tuple[dt_time, dt_time], ...]:
    raw = os.getenv("BOT_TRADING_WINDOW_SESSIONS")
    if raw is None or raw.strip() == "":
        return ()
    try:
        return parse_trading_window_sessions(raw)
    except Exception:
        logging.exception("BOT_TRADING_WINDOW_SESSIONS inválido; usando start_hour/end_hour sin franjas.")
        return ()


def _clock_minutes_of_day_for_trading_window(
    cfg: Config,
    wall_now: datetime,
    *,
    tz_override: Optional[str] = None,
) -> float:
    """
    Minutos desde medianoche (float) según ``trading_window_timezone`` (o ``tz_override``).
    UTC/GMT usa el reloj UTC actual (pares de franjas alineadas al meridiano de Greenwich).
    """
    z = (tz_override if tz_override is not None else cfg.trading_window_timezone) or "LOCAL"
    z = str(z).strip().upper()
    if z in ("UTC", "GMT", "GREENWICH"):
        u = datetime.now(timezone.utc)
        return u.hour * 60.0 + u.minute + u.second / 60.0 + u.microsecond / 60_000_000.0
    return wall_now.hour * 60.0 + wall_now.minute + wall_now.second / 60.0 + wall_now.microsecond / 60_000_000.0


def _local_utc_offset_hours(wall_now: Optional[datetime] = None) -> int:
    """Offset entero horas (local − UTC). Argentina permanente: −3."""
    wall = wall_now or datetime.now()
    if wall.tzinfo is not None:
        off = wall.utcoffset()
        if off is not None:
            return int(round(off.total_seconds() / 3600.0))
    utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    local_naive = datetime.now()
    return int(round((local_naive - utc_naive).total_seconds() / 3600.0))


def _shift_hhmm(hour: int, minute: int, delta_hours: int) -> Tuple[int, int]:
    total = (hour * 60 + minute + int(delta_hours) * 60) % (24 * 60)
    return total // 60, total % 60


def _format_session_ranges_shifted(
    sessions: Tuple[Tuple[dt_time, dt_time], ...],
    delta_hours: int,
) -> str:
    if not sessions:
        return "—"
    parts: list[str] = []
    for a, b in sessions:
        ah, am = _shift_hhmm(a.hour, a.minute, delta_hours)
        bh, bm = _shift_hhmm(b.hour, b.minute, delta_hours)
        parts.append(f"{ah:02d}:{am:02d}–{bh:02d}:{bm:02d}")
    return ", ".join(parts)


def _session_window_range_caption(item: dict[str, Any], wall_now: datetime) -> str:
    """
    Etiqueta legible para Telegram: si la ventana está en UTC, también muestra equivalente local.
    """
    rng = str(item.get("range_label") or "—")
    tz = str(item.get("tz") or "LOCAL").strip().upper()
    if tz in ("UTC", "GMT", "GREENWICH"):
        sessions = tuple(item.get("sessions") or ())
        local_rng = _format_session_ranges_shifted(sessions, _local_utc_offset_hours(wall_now))
        return f"{rng} (UTC) · {local_rng} (local)"
    if bool(item.get("legacy_hour_inclusive")):
        return f"{rng} (local)"
    return f"{rng} ({tz if tz else 'LOCAL'})"


def _session_minute_inclusive_ranges(sessions: Tuple[Tuple[dt_time, dt_time], ...]) -> list[Tuple[float, float]]:
    bounds: list[Tuple[float, float]] = []
    for ta, tb in sessions:
        a = ta.hour * 60.0 + ta.minute
        b = tb.hour * 60.0 + tb.minute
        bounds.append((a, b))
    return bounds


def _within_trading_window_sessions(cfg: Config, wall_now: datetime) -> bool:
    if not cfg.trading_window_sessions:
        return True
    mm = _clock_minutes_of_day_for_trading_window(cfg, wall_now)
    for lo, hi in _session_minute_inclusive_ranges(cfg.trading_window_sessions):
        if lo <= mm <= hi:
            return True
    return False


def _within_symbol_sessions(
    sessions_str: str,
    cfg: Config,
    wall_now: datetime,
) -> bool:
    """
    Evalúa si ``wall_now`` cae dentro de las franjas definidas en ``sessions_str``
    (formato idéntico a BOT_TRADING_WINDOW_SESSIONS: ``"HH:MM-HH:MM,HH:MM-HH:MM"``).
    Soporta franjas que cruzan medianoche (ej. ``"21:00-09:00"``):
    si el inicio > fin en minutos, se interpreta como wrap overnight.
    Usa la misma timezone que cfg.trading_window_timezone (UTC o LOCAL).
    """
    try:
        sessions = parse_trading_window_sessions(sessions_str)
    except Exception:
        logging.warning("trading_window_sessions inválido para símbolo: %r — ignorado.", sessions_str)
        return True  # fail-open: no bloquear por config rota
    if not sessions:
        return True
    return _within_parsed_sessions(sessions, cfg, wall_now)


def _within_parsed_sessions(
    sessions: Tuple[Tuple[dt_time, dt_time], ...],
    cfg: Config,
    wall_now: datetime,
    *,
    tz_override: Optional[str] = None,
) -> bool:
    """True si wall_now está dentro de alguna franja (soporta overnight)."""
    if not sessions:
        return True
    mm = _clock_minutes_of_day_for_trading_window(cfg, wall_now, tz_override=tz_override)
    for ta, tb in sessions:
        lo = ta.hour * 60.0 + ta.minute
        hi = tb.hour * 60.0 + tb.minute
        if lo <= hi:
            if lo <= mm <= hi:
                return True
        else:
            if mm >= lo or mm <= hi:
                return True
    return False


def _format_session_ranges(sessions: Tuple[Tuple[dt_time, dt_time], ...]) -> str:
    if not sessions:
        return "—"
    parts: list[str] = []
    for a, b in sessions:
        parts.append(f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}")
    return ", ".join(parts)


# Estado previo "dentro de ventana" por instrumento (Telegram session start/end).
_SESSION_WINDOW_PREV_INSIDE: dict[str, bool] = {}


def _instrument_session_windows(cfg: Config) -> list[dict[str, Any]]:
    """
    Ventanas horarias notificables por instrumento/estrategia.
    - Símbolos del loop: trading_window_sessions por símbolo o global.
    """
    out: list[dict[str, Any]] = []
    tz = str(getattr(cfg, "trading_window_timezone", "LOCAL") or "LOCAL")

    if bool(getattr(cfg, "trading_window_enabled", False)):
        for symbol in cfg.symbols:
            sym = str(symbol or "").strip().upper()
            if not sym:
                continue
            sym_sessions_str = str(_sym_get(cfg, symbol, "trading_window_sessions", "") or "").strip()
            sessions: Tuple[Tuple[dt_time, dt_time], ...] = ()
            source = ""
            if sym_sessions_str:
                try:
                    sessions = parse_trading_window_sessions(sym_sessions_str)
                    source = "symbol"
                except Exception:
                    logging.warning(
                        "[%s] trading_window_sessions inválido para notify: %r",
                        sym,
                        sym_sessions_str,
                    )
                    continue
            elif symbol in getattr(cfg, "weekend_trade_symbols", ()):
                # Crypto 24/7: sin ventana que notificar.
                continue
            elif cfg.trading_window_sessions:
                sessions = cfg.trading_window_sessions
                source = "global"
            else:
                # Legacy start_hour/end_hour enteros: siempre hora LOCAL del PC
                # (igual que `_session_allows_new_trade`), aunque BOT_TRADING_WINDOW_TIMEZONE=UTC.
                sh = int(getattr(cfg, "start_hour", 0) or 0)
                eh = int(getattr(cfg, "end_hour", 23) or 23)
                sessions = ((dt_time(sh % 24, 0), dt_time(eh % 24, 0)),)
                source = "legacy_hours"
                out.append(
                    {
                        "key": f"symbol:{sym}:{source}",
                        "symbol": sym,
                        "label": sym,
                        "kind": "symbol",
                        "sessions": sessions,
                        "end_exclusive": False,
                        "legacy_hour_inclusive": True,
                        "tz": "LOCAL",
                        "range_label": f"{sh:02d}:00–{eh:02d}:00",
                    }
                )
                continue

            if not sessions:
                continue
            out.append(
                {
                    "key": f"symbol:{sym}:{source}",
                    "symbol": sym,
                    "label": sym,
                    "kind": "symbol",
                    "sessions": sessions,
                    "end_exclusive": False,
                    "tz": tz,
                    "range_label": _format_session_ranges(sessions),
                }
            )
    return out


def _instrument_session_inside(item: dict[str, Any], cfg: Config, wall_now: datetime) -> bool:
    sessions: Tuple[Tuple[dt_time, dt_time], ...] = tuple(item.get("sessions") or ())
    if not sessions:
        return True
    if bool(item.get("legacy_hour_inclusive")) and len(sessions) == 1:
        # Misma semántica que `_session_allows_new_trade` legacy: start_hour <= hour <= end_hour (local).
        a, b = sessions[0]
        hour = int(wall_now.hour)
        return bool(int(a.hour) <= hour <= int(b.hour))
    if bool(item.get("end_exclusive")) and len(sessions) == 1:
        # start_hour <= hour < end_hour
        a, b = sessions[0]
        start_h = int(a.hour)
        end_h = int(b.hour)
        # Usar el mismo reloj que trading_window_timezone solo si tz!=LOCAL forzado;
        # el scalp paralelo siempre usa hora local del bot.
        hour = wall_now.hour
        if str(item.get("tz") or "LOCAL").upper() in ("UTC", "GMT", "GREENWICH"):
            hour = datetime.now(timezone.utc).hour
        return bool(start_h <= hour < end_h)
    return _within_parsed_sessions(
        sessions,
        cfg,
        wall_now,
        tz_override=str(item.get("tz") or "") or None,
    )


def _telegram_notify_session_windows(cfg: Config, now: Optional[datetime] = None) -> None:
    """
    Avisa por Telegram al cruzar el inicio/fin de la ventana horaria de cada instrumento.
    FX legacy start/end hour → siempre reloj local (aunque TIMEZONE=UTC para stocks).
    """
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_NOTIFY_SESSION_WINDOW", False):
        return

    wall = now or datetime.now()
    for item in _instrument_session_windows(cfg):
        key = str(item["key"])
        inside = _instrument_session_inside(item, cfg, wall)
        prev = _SESSION_WINDOW_PREV_INSIDE.get(key)
        _SESSION_WINDOW_PREV_INSIDE[key] = inside
        if prev is None:
            # Primer poll tras arranque: solo inicializa estado (evita spam al reiniciar).
            continue

        label = str(item.get("label") or item.get("symbol") or key)
        caption = _session_window_range_caption(item, wall)
        day = wall.strftime("%Y-%m-%d")

        if inside and not prev:
            msg = (
                f"meta-trader: inicio de sesión\n"
                f"{label} · {caption}\n"
                f"Nuevas entradas permitidas."
            )
            telegram_notify.notify(
                msg,
                dedup_key=f"session_start:{key}:{day}",
                min_interval_sec=3600.0,
            )
            logging.info("[Session] START %s | %s", label, caption)
        elif (not inside) and prev:
            msg = (
                f"meta-trader: fin de sesión\n"
                f"{label} · {caption}\n"
                f"Sin nuevas entradas hasta la próxima ventana."
            )
            telegram_notify.notify(
                msg,
                dedup_key=f"session_end:{key}:{day}",
                min_interval_sec=3600.0,
            )
            logging.info("[Session] END %s | %s", label, caption)


def _session_window_prev_clear_for_tests() -> None:
    _SESSION_WINDOW_PREV_INSIDE.clear()

def _is_news_time(cfg: Config, now: Optional[datetime] = None) -> bool:
    if not cfg.news_block_hours:
        return False
    now = now or datetime.now()
    return now.hour in cfg.news_block_hours


def _update_spread_stats(cfg: Config, symbol: str, spread_points: float) -> float:
    prev = _spread_ema_points.get(symbol)
    if prev is None:
        _spread_ema_points[symbol] = float(spread_points)
        return float(spread_points)
    alpha = max(0.01, min(cfg.spread_dynamic_alpha, 1.0))
    ema = (alpha * float(spread_points)) + ((1.0 - alpha) * float(prev))
    _spread_ema_points[symbol] = float(ema)
    return float(ema)


def _effective_max_spread_points(cfg: Config, symbol: str, p: Optional[dict] = None) -> float:
    symbol_max = float((p or {}).get("max_spread_points") or cfg.max_spread_points)
    return effective_max_spread_points(
        symbol,
        symbol_max,
        btcusd_cap=float(getattr(cfg, "btcusd_max_spread_points", 0.0) or 0.0),
    )


def _is_spread_ok(cfg: Config, symbol: str, *, max_spread_points: Optional[float] = None) -> tuple[bool, Optional[float], Optional[float]]:
    """
    Returns (ok, spread_now_points, avg_spread_points).
    Always enforces hard cap cfg.max_spread_points.
    If dynamic enabled, also enforces spread_now <= mult * avg_spread (EMA).
    """
    spread = _get_spread_points(symbol)
    if spread is None:
        return False, None, None

    avg = _update_spread_stats(cfg, symbol, spread)

    hard_max = float(max_spread_points) if max_spread_points is not None else float(cfg.max_spread_points)
    if spread > hard_max:
        return False, spread, avg

    if cfg.spread_dynamic_enabled and avg > 0:
        if spread > (cfg.spread_dynamic_mult * avg):
            return False, spread, avg

    return True, spread, avg


def _record_failed_order_attempt(symbol: str, cfg: Optional[Config] = None, result: Any = None) -> None:
    _last_failed_order_attempt[symbol] = datetime.now()
    if cfg is not None:
        logging.info(
            "[%s] Failed order cooldown activado (retcode=%s). Próximo intento en %.0fs.",
            symbol,
            getattr(result, "retcode", "?") if result is not None else "?",
            float(cfg.failed_attempt_cooldown_seconds or 120),
        )


def _clamp_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return clamp_lot_volume(volume)
    return clamp_lot_volume(
        volume,
        volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
        volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
        volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
    )


def _clip_lot_to_margin(symbol: str, lot: float, *, max_margin_frac: float) -> float:
    frac = float(max_margin_frac or 0.0)
    if frac <= 0.0 or lot <= 0.0:
        return lot
    acc = mt5.account_info()
    if acc is None:
        return lot
    free = float(getattr(acc, "margin_free", 0.0) or 0.0)
    cap = free * frac
    if cap <= 0.0:
        return lot
    used = _margin_at_volume(symbol, lot)
    if used is None or used <= cap:
        return lot
    scaled = lot * (cap / used)
    return _clamp_volume(symbol, scaled)


def _margin_at_volume(symbol: str, volume: float) -> Optional[float]:
    if volume <= 0:
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    price = float(getattr(tick, "ask", 0.0) or 0.0)
    if price <= 0:
        return None
    try:
        raw = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, float(volume), price)
    except Exception:
        logging.debug("[%s] order_calc_margin failed.", symbol, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        margin = float(raw)
    except (TypeError, ValueError):
        return None
    return margin if margin > 0 else None


def _unpack_mt5_error(err: object) -> tuple[object, str]:
    if isinstance(err, tuple) and len(err) >= 2:
        return err[0], str(err[1])
    return err, str(err or "")


def connect_mt5(cfg: Config) -> bool:
    """
    Connects to the running MT5 terminal and refuses anything that is not XBTFX.

    Credentials are read from environment variables:
      MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
    MT5_XBTFX_ONLY=0 disables the server gate. The default is fail-closed.
    """
    login_raw = _env("MT5_LOGIN")
    password = _env("MT5_PASSWORD")
    server = _env("MT5_SERVER")

    if login_raw is None or password is None or server is None:
        logging.error(
            "Missing env vars. Please set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER (use .env.example)."
        )
        return False

    try:
        login = int(login_raw)
    except ValueError:
        logging.error("MT5_LOGIN must be an integer (account number). Got: %r", login_raw)
        return False

    markers = server_markers()
    enforced = enforcement_enabled()
    last_err: object = None
    for attempt in range(1, cfg.connect_retries + 1):
        ok = False
        try:
            ok = bool(mt5.initialize(login=login, password=password, server=server))
            if not ok:
                last_err = mt5.last_error()
        except Exception as exc:
            last_err = exc
            logging.exception("Exception during MT5 initialize (attempt %d/%d).", attempt, cfg.connect_retries)

        if ok:
            acc = mt5.account_info()
            action, _sleep_s = connection_decision(
                initialized=True,
                server=str(getattr(acc, "server", "") or ""),
                company=str(getattr(acc, "company", "") or ""),
                markers=markers,
                enforced=enforced,
                attempt=attempt,
                max_attempts=cfg.connect_retries,
                base_sleep=cfg.connect_retry_sleep,
            )
            if action == "reject_broker":
                logging.error(
                    "XBTFX gate refused the terminal. server=%s company=%s markers=%s",
                    getattr(acc, "server", ""),
                    getattr(acc, "company", ""),
                    ",".join(markers),
                )
                mt5.shutdown()
                return False
            term = mt5.terminal_info()
            level, band = account_margin_status(mt5)
            logging.info(
                "Connected to XBTFX. Account=%s | Name=%s | Server=%s | Currency=%s | Terminal=%s | margin_level=%s | margin_band=%s",
                getattr(acc, "login", "?"),
                getattr(acc, "name", "?"),
                getattr(acc, "server", "?"),
                getattr(acc, "currency", "?"),
                getattr(term, "name", "?"),
                "n/a" if level is None else f"{level:.1f}",
                band,
            )
            return True

        code, message = _unpack_mt5_error(last_err)
        action, sleep_s = connection_decision(
            initialized=False,
            server="",
            company="",
            markers=markers,
            enforced=enforced,
            error_code=code,
            error_message=message,
            attempt=attempt,
            max_attempts=cfg.connect_retries,
            base_sleep=cfg.connect_retry_sleep,
        )
        logging.warning(
            "MT5 initialize failed (attempt %d/%d, action=%s, sleep=%.1fs). last_error=%s",
            attempt,
            cfg.connect_retries,
            action,
            sleep_s,
            last_err,
        )
        if action != "retry":
            break
        time.sleep(sleep_s)

    logging.error("Could not connect to XBTFX after retries. Last error: %r", last_err)
    return False


def _weekend_blocks_new_trade(cfg: Config, symbol: str, now: Optional[datetime] = None) -> bool:
    """True if weekend_block is on, now is Sat/Sun, and symbol is not in weekend_trade_symbols."""
    wall = now or datetime.now()
    if not bool(getattr(cfg, "weekend_block_enabled", False)):
        return False
    if wall.weekday() not in (5, 6):
        return False
    return symbol not in (getattr(cfg, "weekend_trade_symbols", ()) or ())


def _session_allows_new_trade(cfg: Config, symbol: str, now: Optional[datetime] = None) -> bool:
    """
    Calendar / session gate per symbol for **new** entries.
    Weekend: blocked for all except symbols listed in `cfg.weekend_trade_symbols` (e.g. BTC CFD).
    Friday cut applies when enabled.

    Trading window (`trading_window_enabled`):
      - Por símbolo: si ``BOT_SYMBOL_CONFIG[symbol]["trading_window_sessions"]`` está definido,
        se usa esa ventana en lugar de la global. Soporta franjas overnight (ej. "21:00-09:00").
      - Global: si el símbolo no tiene ventana propia y no es crypto 24/7, aplica
        ``cfg.trading_window_sessions`` (BOT_TRADING_WINDOW_SESSIONS). Soporta múltiples
        franjas separadas por coma.
      - TZ: ``trading_window_timezone`` aplica a ambos niveles (UTC o LOCAL).

    Quiet-session block: low-liquidity hours; crypto and exempt symbols keep trading.
    """
    now = now or datetime.now()
    wd = now.weekday()  # Mon=0 .. Sun=6

    if _weekend_blocks_new_trade(cfg, symbol, now):
        return False

    if cfg.friday_cutoff_enabled:
        if wd == 4 and now.hour >= cfg.end_hour and (now.hour > cfg.end_hour or now.minute >= 0):
            return False

    if cfg.trading_window_enabled:
        # Ventana por símbolo tiene prioridad sobre la ventana global.
        sym_sessions_str = str(_sym_get(cfg, symbol, "trading_window_sessions", "") or "").strip()
        if sym_sessions_str:
            # El símbolo define su propia ventana → evaluarla (soporta midnight-wrap).
            if not _within_symbol_sessions(sym_sessions_str, cfg, now):
                return False
        elif symbol in cfg.weekend_trade_symbols:
            # Crypto 24/7: no aplicar ventana global de FX.
            pass
        elif cfg.trading_window_sessions:
            if not _within_trading_window_sessions(cfg, now):
                return False
        else:
            if now.hour < cfg.start_hour:
                return False
            if now.hour > cfg.end_hour:
                return False

    # Quiet-session block: low-liquidity hours (post-NY, Asia-only).
    # Crypto and any symbol explicitly exempted keeps trading.
    if cfg.quiet_session_block_enabled and cfg.quiet_session_hours:
        if now.hour in cfg.quiet_session_hours and symbol not in cfg.quiet_session_exempt_symbols:
            return False

    return True


def _is_new_trade_cutoff(cfg: Config, now: Optional[datetime] = None) -> bool:
    if not cfg.new_trade_cutoff_enabled:
        return False
    now = now or datetime.now()
    if now.hour > cfg.no_new_trades_after_hour:
        return True
    if now.hour == cfg.no_new_trades_after_hour and now.minute >= cfg.no_new_trades_after_minute:
        return True
    return False


def _ensure_symbol(symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        logging.error(missing_symbol_message(mt5, symbol))
        return False
    snap = snapshot_symbol(mt5, symbol)
    if snap is not None and snap.reason in {"point", "trade_disabled"}:
        logging.error("XBTFX contract refused: %s", snap.summary())
        return False
    if snap is not None and not snap.tradable:
        logging.warning("XBTFX contract limited: %s", snap.summary())
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logging.error("Could not select/enable symbol: %s", symbol)
            return False
    key = str(symbol).strip().upper()
    if snap is not None and key not in _XBTFX_CONTRACT_LOGGED:
        _XBTFX_CONTRACT_LOGGED.add(key)
        logging.info("XBTFX contract %s", snap.summary())
    return True


def _get_spread_points(symbol: str) -> Optional[float]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None or info.point == 0:
        return None
    return (tick.ask - tick.bid) / info.point


def mt5_rates_oldest_to_newest(rates) -> list:
    """
    ``copy_rates_from_pos`` order is not stable across brokers/builds
    (this terminal returns chronological; docs often describe newest-first).
    Always sort by bar open time so last-closed is ``[-1]``.
    """
    if rates is None:
        return []
    rows = list(rates)
    rows.sort(key=lambda r: int(r["time"]))
    return rows


def _mt5_timeframe(tf: str):
    """Resolve a timeframe string like 'M15' / 'H4' to the MT5 enum. Defaults to M15."""
    tf = (tf or "").strip().upper()
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    return mapping.get(tf, mt5.TIMEFRAME_M15)


def get_atr(symbol: str, timeframe, period: int = 14) -> Optional[float]:
    """
    ATR Wilder on closed candles. Returns ATR in price units (not points).
    """
    count = max(period + 20, period + 5)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) < period + 1:
        return None

    trs: list[float] = []
    prev_close = float(rates[0]["close"])
    for r in rates[1:]:
        h = float(r["high"])
        l = float(r["low"])
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = float(r["close"])

    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / float(period)
    for tr in trs[period:]:
        atr = ((atr * (period - 1)) + tr) / float(period)
    return float(atr) if atr > 0 else None


def _m15_bar_at_time(symbol: str, ts: datetime) -> Optional[dict]:
    """
    Return OHLC of the M15 bar that contained `ts`. Uses copy_rates_range
    with a 1-hour window around ts. Returns None if the bar is not found
    (can happen if data is very old or symbol not subscribed).
    """
    if ts is None:
        return None
    from_ts = ts - timedelta(minutes=30)
    to_ts = ts + timedelta(minutes=30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, from_ts, to_ts)
    if rates is None or len(rates) == 0:
        return None
    target = int(ts.timestamp())
    # Prefer the bar whose [start, start+15min) contains ts.
    for r in rates:
        bar_start = int(r["time"])
        if bar_start <= target < bar_start + 15 * 60:
            return {
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "time": bar_start,
            }
    # Fallback: most recent bar at or before ts.
    rates_sorted = sorted(rates, key=lambda r: int(r["time"]))
    for r in reversed(rates_sorted):
        if int(r["time"]) <= target:
            return {
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "time": int(r["time"]),
            }
    return None


def _compute_trade_grades(
    symbol: str,
    side: str,
    price_open: Optional[float],
    price_close: Optional[float],
    opened_at_iso: Optional[str],
    closed_at_iso: Optional[str],
) -> dict:
    """
    Elder-style grading (Study Guide ch.10, "Practical Details"):

      entry_grade : % de la vela de entrada a favor del entry.
          BUY  -> (high - entry) / (high - low)   (100% = compraste al mínimo)
          SELL -> (entry - low)  / (high - low)   (100% = vendiste al máximo)

      exit_grade  : % de la vela de salida a favor del exit.
          BUY  -> (exit - low)   / (high - low)   (100% = vendiste al máximo)
          SELL -> (high - exit)  / (high - low)   (100% = recompraste al mínimo)

      trade_grade : profit captured / channel-height.
          Elder usa un canal con 90-95% de las barras dentro. Como proxy
          volatility-adaptive usamos ATR(20, M15) * 4 (~4*ATR cubre la altura típica del canal
          para la mayoría de los símbolos FX/crypto). Grade > 33% = A para Elder.
          Devuelve porcentaje con signo: negativo en trades perdedores.

    Todos los grades son None si no se puede computar (barra ausente, rango 0, ATR nulo, etc.).
    Los porcentajes se devuelven como número en [-100, 100] redondeado a 1 decimal.
    """
    out: dict[str, Any] = {"entry_grade": None, "exit_grade": None, "trade_grade": None}
    if side not in ("BUY", "SELL"):
        return out
    if price_open is None or price_close is None:
        return out

    def _parse(iso: Optional[str]) -> Optional[datetime]:
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso)
        except Exception:
            return None

    open_dt = _parse(opened_at_iso)
    close_dt = _parse(closed_at_iso)

    if open_dt is not None:
        bar = _m15_bar_at_time(symbol, open_dt)
        if bar is not None:
            rng = bar["high"] - bar["low"]
            if rng > 0:
                if side == "BUY":
                    grade = (bar["high"] - float(price_open)) / rng
                else:
                    grade = (float(price_open) - bar["low"]) / rng
                out["entry_grade"] = round(max(0.0, min(1.0, grade)) * 100.0, 1)

    if close_dt is not None:
        bar = _m15_bar_at_time(symbol, close_dt)
        if bar is not None:
            rng = bar["high"] - bar["low"]
            if rng > 0:
                if side == "BUY":
                    grade = (float(price_close) - bar["low"]) / rng
                else:
                    grade = (bar["high"] - float(price_close)) / rng
                out["exit_grade"] = round(max(0.0, min(1.0, grade)) * 100.0, 1)

    # trade_grade: profit in price / (ATR * 4). Signed.
    try:
        atr = get_atr(symbol, mt5.TIMEFRAME_M15, 20)
    except Exception:
        atr = None
    if atr is not None and atr > 0:
        channel_height = atr * 4.0
        profit_price = (float(price_close) - float(price_open)) if side == "BUY" else (float(price_open) - float(price_close))
        grade = profit_price / channel_height
        out["trade_grade"] = round(max(-1.0, min(1.0, grade)) * 100.0, 1)

    return out


def _position_age_seconds(p) -> float:
    """Seconds since position open (MT5 `time` is unix int)."""
    t = getattr(p, "time", None)
    tm = getattr(p, "time_msc", None)
    # Some MT5 builds/brokers can return time=0 but provide time_msc.
    try:
        if (t is None or int(t) <= 0) and tm is not None:
            t = int(int(tm) // 1000)
    except Exception:
        pass
    if t is None:
        return 0.0
    if isinstance(t, datetime):
        return max(0.0, (datetime.now() - t).total_seconds())
    try:
        ti = int(t)
        if ti <= 0:
            return 0.0
        return max(0.0, time.time() - float(ti))
    except Exception:
        return 0.0


def _position_magic_int(p) -> int:
    """Broker magic; keep 0 (manual). Do not coerce 0 → -1 via `or`."""
    raw = getattr(p, "magic", None)
    if raw is None:
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _bot_trade_magics(cfg: Config) -> tuple[int, ...]:
    """Magic numbers whose deals se registran en trades.log."""
    magics: list[int] = [int(cfg.magic)]
    return tuple(m for m in magics if m > 0)


def _deal_magic_matches_bot(d, cfg: Config) -> bool:
    return int(getattr(d, "magic", -1) or -1) in _bot_trade_magics(cfg)


def _deal_entry(d) -> int:
    """DEAL_ENTRY_IN is 0 in MT5, so `entry or -1` would turn every IN deal into -1."""
    v = getattr(d, "entry", None)
    try:
        return int(v) if v is not None else -1
    except (TypeError, ValueError):
        return -1


def _in_deal_magic_by_position(deals) -> dict[int, int]:
    """position_id → magic del DEAL_ENTRY_IN (cualquier magic, incluido 0 manual)."""
    found: dict[int, int] = {}
    for d in deals or ():
        if _deal_entry(d) != int(mt5.DEAL_ENTRY_IN):
            continue
        pid = int(getattr(d, "position_id", 0) or 0)
        if pid > 0:
            found.setdefault(pid, int(getattr(d, "magic", 0) or 0))
    return found


def _out_deal_owner_magic(d, cfg: Config, in_magic_by_pos: dict[int, int]) -> Optional[int]:
    """
    Magic del bot dueño de un deal de cierre, o None si no es del bot.
    Los cierres a mercado viejos salieron con magic 0: se atribuyen por el deal de entrada.
    """
    magics = _bot_trade_magics(cfg)
    m = int(getattr(d, "magic", 0) or 0)
    if m in magics:
        return m
    pid = int(getattr(d, "position_id", 0) or 0)
    if pid <= 0:
        return None
    in_m = in_magic_by_pos.get(pid)
    if in_m is None:
        in_m = _POSITION_IN_MAGIC_CACHE.get(pid)
    if in_m is None:
        fin = _first_in_deal_for_position_from_broker(pid, end=datetime.now())
        in_m = int(getattr(fin, "magic", 0) or 0) if fin is not None else 0
        _POSITION_IN_MAGIC_CACHE[pid] = in_m
    return in_m if in_m in magics else None


def _strategy_label_for_magic(cfg: Config, magic: int) -> str:
    if int(magic) == int(cfg.magic):
        return "main"
    return f"magic_{magic}"


def _position_is_managed_by_bot(p, cfg: Config) -> bool:
    """
    Posiciones que el bot debe trailing/BE (mismo magic, o magic 0 si está habilitado).
    Solo símbolos listados en cfg.symbols.
    """
    sym = getattr(p, "symbol", None)
    if not sym or sym not in cfg.symbols:
        return False
    m = _position_magic_int(p)
    if m == int(cfg.magic):
        return True
    if m == 0 and bool(getattr(cfg, "track_zero_magic_positions", True)):
        return True
    return False


def _position_side(p) -> Signal | None:
    """Returns 'BUY' or 'SELL' for an MT5 position object, or None if unknown."""
    t = int(getattr(p, "type", -1))
    if t == int(getattr(mt5, "POSITION_TYPE_BUY", 0)):
        return "BUY"
    if t == int(getattr(mt5, "POSITION_TYPE_SELL", 1)):
        return "SELL"
    return None


def _position_at_break_even(p, *, tolerance_points: float = 0.0) -> bool:
    """
    True if the position's current SL is at break-even or better
    (i.e. on the favorable side of the entry).
    """
    sl = float(getattr(p, "sl", 0.0) or 0.0)
    if sl == 0.0:
        return False
    entry = float(getattr(p, "price_open", 0.0) or 0.0)
    if entry == 0.0:
        return False
    side = _position_side(p)
    sym = str(getattr(p, "symbol", "") or "")
    info = mt5.symbol_info(sym) if sym else None
    point = float(getattr(info, "point", 0.0) or 0.0) if info is not None else 0.0
    tol = tolerance_points * point if point > 0 else 0.0
    if side == "BUY":
        return sl >= (entry - tol)
    if side == "SELL":
        return sl <= (entry + tol)
    return False


def _can_open_additional_position(
    cfg: Config, symbol: str, side: Signal, p: dict
) -> tuple[bool, str]:
    """
    Decide whether `open_trade` may place a new market order on `symbol`/`side`.

    Returns (allowed, reason). When `allowed` is False, `reason` is a short
    human-readable string suitable for logging.
    """
    pos = mt5.positions_get(symbol=symbol) or ()
    n_open = len(pos)
    max_per_symbol = max(1, int(p.get("max_positions_per_symbol", 1)))

    if n_open == 0:
        return True, ""

    if max_per_symbol <= 1:
        return False, f"already 1 position on symbol (max_per_symbol={max_per_symbol})"

    if n_open >= max_per_symbol:
        return False, f"reached max_positions_per_symbol ({n_open}/{max_per_symbol})"

    sides = {_position_side(x) for x in pos if _position_side(x) is not None}
    if sides and side not in sides:
        return False, f"opposite-side position open ({sorted(s for s in sides if s)}); refusing to hedge"

    if bool(p.get("pyramiding_require_be", True)):
        not_be = [int(getattr(x, "ticket", 0)) for x in pos if not _position_at_break_even(x)]
        if not_be:
            return False, f"existing positions not at break-even yet (tickets={not_be})"

    min_sep_pts = float(p.get("pyramiding_min_separation_points", 0.0) or 0.0)
    if min_sep_pts > 0:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            return False, "missing tick/info for separation check"
        point = float(getattr(info, "point", 0.0) or 0.0)
        if point <= 0:
            return False, "invalid point for separation check"
        same_side = [x for x in pos if _position_side(x) == side]
        if same_side:
            ref_price = float(tick.ask if side == "BUY" else tick.bid)
            entries = [float(getattr(x, "price_open", 0.0) or 0.0) for x in same_side]
            if side == "BUY":
                last_entry = min(entries)
                dist_pts = (last_entry - ref_price) / point
            else:
                last_entry = max(entries)
                dist_pts = (ref_price - last_entry) / point
            if dist_pts < min_sep_pts:
                return False, (
                    f"too close to last same-side entry ({dist_pts:.0f} pts < {min_sep_pts:.0f} pts)"
                )

    return True, ""


def _count_managed_open_positions(cfg: Config) -> int:
    pos = mt5.positions_get()
    if pos is None:
        return 0
    n = 0
    for p in pos:
        if _position_is_managed_by_bot(p, cfg):
            n += 1
    return n


def total_open_positions() -> int:
    pos = mt5.positions_get()
    return len(pos) if pos else 0


def _managed_position_lots(cfg: Config) -> list[PositionLot]:
    """Open managed positions as (symbol, side, volume) for currency exposure."""
    pos = mt5.positions_get()
    if not pos:
        return []
    out: list[PositionLot] = []
    for p in pos:
        if not _position_is_managed_by_bot(p, cfg):
            continue
        side = _position_side(p)
        if side not in ("BUY", "SELL"):
            continue
        out.append(
            PositionLot(
                symbol=str(getattr(p, "symbol", "") or ""),
                side=side,
                volume=float(getattr(p, "volume", 0.0) or 0.0),
            )
        )
    return out


def _currency_exposure_blocks_open(cfg: Config, symbol: str, side: Signal, lot: float) -> bool:
    """True when the new fill would breach a currency cap. Fail-open on errors."""
    if not bool(getattr(cfg, "currency_exposure_enabled", False)):
        return False
    try:
        verdict = check_new_trade(
            _managed_position_lots(cfg),
            symbol,
            side,
            lot,
            limits=ExposureLimits(
                max_net=float(getattr(cfg, "max_net_exposure_per_currency", 0.0) or 0.0),
                max_gross=float(getattr(cfg, "max_gross_exposure_per_currency", 0.0) or 0.0),
            ),
            overrides=getattr(cfg, "currency_symbol_overrides", {}) or {},
        )
    except Exception:
        logging.exception("[%s] currency exposure check failed; fail-open", symbol)
        return False
    if verdict.allowed:
        return False
    logging.info("[%s] Currency exposure: %s. Skipping.", symbol, verdict.reason)
    _log_event(
        {
            "event": "CURRENCY_EXPOSURE_SKIP",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "side": side,
            "lot": lot,
            **verdict.as_dict(),
        }
    )
    return True


def _startup_reconciliation(cfg: Config) -> dict[str, int]:
    """
    Tras un corte / reinicio: inventario de posiciones y órdenes pendientes en los símbolos del bot.
    Opcionalmente cancela pendientes con nuestro magic (BOT_CANCEL_ORPHAN_PENDING_ON_START=1).
    """
    symset = set(cfg.symbols)
    pos_all = mt5.positions_get()
    rows: list[dict] = []
    if pos_all:
        for p in pos_all:
            sym = getattr(p, "symbol", "")
            if sym not in symset:
                continue
            magic = _position_magic_int(p)
            rows.append(
                {
                    "ticket": int(getattr(p, "ticket", 0) or 0),
                    "symbol": sym,
                    "magic": magic,
                    "type": int(getattr(p, "type", -1) or -1),
                    "volume": float(getattr(p, "volume", 0.0) or 0.0),
                    "price_open": float(getattr(p, "price_open", 0.0) or 0.0),
                    "sl": float(getattr(p, "sl", 0.0) or 0.0),
                    "tp": float(getattr(p, "tp", 0.0) or 0.0),
                    "profit": float(getattr(p, "profit", 0.0) or 0.0),
                    "managed_by_bot": _position_is_managed_by_bot(p, cfg),
                    "strategy": _strategy_label_for_magic(cfg, magic),
                }
            )

    _log_event(
        {
            "event": "RECONCILE_POSITIONS",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "bot_magic": int(cfg.magic),
            "track_zero_magic": bool(cfg.track_zero_magic_positions),
            "count_on_symbols": len(rows),
            "positions": rows,
        }
    )

    n_managed_pos = sum(1 for r in rows if r.get("managed_by_bot"))

    if rows:
        logging.warning(
            "Reconciliación: %d posición(es) abierta(s) en símbolos del bot. Ver evento RECONCILE_POSITIONS en trades.log.",
            len(rows),
        )

    # Órdenes pendientes (limit/stop) — el bot normalmente no las usa; si quedaron colgadas, listar / cancelar.
    orders = mt5.orders_get()
    pend: list[dict] = []
    if orders:
        for o in orders:
            sym = getattr(o, "symbol", "")
            if sym not in symset:
                continue
            try:
                om = int(getattr(o, "magic", -1) or -1)
            except (TypeError, ValueError):
                om = -1
            pend.append(
                {
                    "order": int(getattr(o, "ticket", 0) or 0),
                    "symbol": sym,
                    "magic": om,
                    "type": int(getattr(o, "type", -1) or -1),
                    "volume_initial": float(getattr(o, "volume_initial", 0.0) or 0.0),
                    "price_open": float(getattr(o, "price_open", 0.0) or 0.0),
                }
            )

    if pend:
        _log_event(
            {
                "event": "RECONCILE_PENDING_ORDERS",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "count": len(pend),
                "orders": pend,
            }
        )
        logging.warning(
            "Reconciliación: %d orden(es) pendiente(s) en símbolos del bot. Ver RECONCILE_PENDING_ORDERS.",
            len(pend),
        )

    if bool(getattr(cfg, "cancel_orphan_pending_on_start", False)) and pend:
        for o in pend:
            if int(o.get("magic", -1)) != int(cfg.magic):
                continue
            oid = int(o.get("order", 0) or 0)
            if oid <= 0:
                continue
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": oid}
            res = mt5.order_send(req)
            ok = res is not None and int(getattr(res, "retcode", -1)) == int(mt5.TRADE_RETCODE_DONE)
            _log_event(
                {
                    "event": "CANCEL_PENDING_ON_START",
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "order": oid,
                    "symbol": o.get("symbol"),
                    "ok": ok,
                    "retcode": int(getattr(res, "retcode", -1)) if res is not None else None,
                    "comment": getattr(res, "comment", "") if res is not None else str(mt5.last_error()),
                }
            )
            if ok:
                logging.info("Cancelada orden pendiente huérfana: order=%s %s", oid, o.get("symbol"))
            else:
                logging.error(
                    "No se pudo cancelar orden pendiente order=%s retcode=%s",
                    oid,
                    getattr(res, "retcode", None) if res is not None else mt5.last_error(),
                )

    return {
        "positions_on_bot_symbols": len(rows),
        "positions_managed": int(n_managed_pos),
        "pending_orders": len(pend),
        "positions": rows,
    }


def daily_loss_limit_reached(max_loss: float) -> tuple[bool, float]:
    """
    Returns (reached, pnl_today). Reached when pnl_today <= max_loss.
    `max_loss` should be negative (e.g. -5.0).
    """
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # history_deals_get uses broker server time; pad so local now does not cut today's closes.
    deals = mt5.history_deals_get(start, now + timedelta(hours=12))
    if deals is None:
        return False, 0.0
    pnl = 0.0
    for d in deals:
        pnl += float(getattr(d, "profit", 0.0) or 0.0)
    return pnl <= max_loss, pnl


def _compute_monthly_realized_pnl() -> float:
    """
    Sum of realized PnL (deal.profit + swap + commission) for every deal
    between the 1st of the current month (local 00:00) and now. Returns
    a signed value (negative = net loss so far this month).
    """
    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(start, now + timedelta(hours=12))
    if deals is None:
        return 0.0
    total = 0.0
    for d in deals:
        total += float(getattr(d, "profit", 0.0) or 0.0)
        total += float(getattr(d, "swap", 0.0) or 0.0)
        total += float(getattr(d, "commission", 0.0) or 0.0)
    return total


def _compute_open_risk_money() -> float:
    """
    Sum of potential dollar loss if EVERY currently open position hit its
    stop loss, using tick_value / tick_size to convert price distance to
    account-currency terms. Positions without SL are skipped (conservative:
    ignored rather than assuming a huge risk).

    NOTE: includes positions from any magic / manual entries, because the
    6% rule is about total account exposure, not just the bot's slice.
    """
    positions = mt5.positions_get()
    if not positions:
        return 0.0
    total_risk = 0.0
    for p in positions:
        sl = float(getattr(p, "sl", 0.0) or 0.0)
        po = float(getattr(p, "price_open", 0.0) or 0.0)
        vol = float(getattr(p, "volume", 0.0) or 0.0)
        if sl <= 0.0 or po <= 0.0 or vol <= 0.0:
            continue
        sym = str(getattr(p, "symbol", "") or "")
        info = mt5.symbol_info(sym)
        if info is None:
            continue
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
        if tick_size <= 0 or tick_value <= 0:
            continue
        if int(getattr(p, "type", -1)) == int(mt5.POSITION_TYPE_BUY):
            dist_price = max(po - sl, 0.0)
        else:
            dist_price = max(sl - po, 0.0)
        money = (dist_price / tick_size) * tick_value * vol
        if money > 0:
            total_risk += money
    return total_risk


# Global: remember which (YYYY-MM) we already logged a MONTHLY_LOSS_LIMIT_BLOCK for,
# so we don't spam the log on every poll once the block fires.
_MONTHLY_LOSS_LIMIT_LOGGED_MONTH: str | None = None


def monthly_loss_limit_reached(cfg: Config) -> tuple[bool, dict]:
    """
    Elder's 6% Rule check. Returns (reached, info) where `info` is a dict
    safe to embed in bot_state.json / log events.

    Reached when: realized_loss_this_month + open_risk >= pct * month_start_balance.
    `month_start_balance` is estimated as (current_balance - realized_pnl_this_month),
    which works even if we didn't snapshot at month rollover.
    """
    if not bool(cfg.monthly_loss_limit_enabled):
        return False, {"enabled": False}

    acc = mt5.account_info()
    if acc is None:
        return False, {"enabled": True, "error": "no_account_info"}
    balance_now = float(getattr(acc, "balance", 0.0) or 0.0)
    # Defensive: some transient MT5 states can report 0 balance. Never block trading on invalid balance.
    if balance_now <= 0:
        return False, {"enabled": True, "error": "non_positive_balance_now", "balance_now": round(balance_now, 2)}
    realized = _compute_monthly_realized_pnl()
    month_start_balance = balance_now - realized
    # Defensive: avoid threshold=0 due to tiny/invalid start-balance estimates (would block all entries).
    if month_start_balance <= 0 or abs(month_start_balance) < 1e-6:
        return False, {
            "enabled": True,
            "error": "non_positive_start_balance",
            "month_start_balance": round(month_start_balance, 2),
            "balance_now": round(balance_now, 2),
            "realized_pnl": round(realized, 2),
        }

    pct = float(cfg.monthly_loss_limit_pct)
    threshold_money = month_start_balance * (pct / 100.0)
    realized_loss = max(-realized, 0.0)
    open_risk = _compute_open_risk_money() if bool(cfg.monthly_loss_limit_include_open_risk) else 0.0
    total_at_risk = realized_loss + open_risk
    reached = total_at_risk >= threshold_money
    return reached, {
        "enabled": True,
        "pct": pct,
        "month_start_balance": round(month_start_balance, 2),
        "realized_pnl": round(realized, 2),
        "realized_loss": round(realized_loss, 2),
        "open_risk": round(open_risk, 2),
        "total_at_risk": round(total_at_risk, 2),
        "threshold_money": round(threshold_money, 2),
        "reached": bool(reached),
        "include_open_risk": bool(cfg.monthly_loss_limit_include_open_risk),
    }


def _deal_reason_label_es(reason: int) -> str:
    """Etiqueta legible para DEAL_REASON_* (MetaTrader5)."""
    mapping = {
        int(mt5.DEAL_REASON_CLIENT): "Manual / cliente",
        int(mt5.DEAL_REASON_MOBILE): "App móvil",
        int(mt5.DEAL_REASON_WEB): "Web",
        int(mt5.DEAL_REASON_EXPERT): "Expert / bot",
        int(mt5.DEAL_REASON_SL): "Stop loss",
        int(mt5.DEAL_REASON_TP): "Take profit",
        int(mt5.DEAL_REASON_SO): "Stop out",
        int(mt5.DEAL_REASON_ROLLOVER): "Rollover",
        int(mt5.DEAL_REASON_VMARGIN): "Margen",
        int(mt5.DEAL_REASON_SPLIT): "Split",
    }
    return mapping.get(int(reason), f"Motivo código {reason}")


def _telegram_seed_seen_close_deals(cfg: Config) -> None:
    """
    Marca deals de salida recientes como ya vistos para no spamear al arrancar el bot.
    Magic principal.
    """
    global _TELEGRAM_SEEN_OUT_DEAL_TICKETS, _TELEGRAM_CLOSE_TRACKER_SEEDED
    if _TELEGRAM_CLOSE_TRACKER_SEEDED:
        return
    now = datetime.now()
    start = now - timedelta(days=2)
    deals = mt5.history_deals_get(start, now + timedelta(seconds=2))
    if deals:
        in_magic = _in_deal_magic_by_position(deals)
        for d in deals:
            if _deal_entry(d) != int(mt5.DEAL_ENTRY_OUT):
                continue
            if _out_deal_owner_magic(d, cfg, in_magic) is None:
                continue
            t = int(getattr(d, "ticket", 0) or 0)
            if t > 0:
                _TELEGRAM_SEEN_OUT_DEAL_TICKETS.add(t)
    _TELEGRAM_CLOSE_TRACKER_SEEDED = True


def _telegram_format_startup_message(
    cfg: Config,
    *,
    pid: int,
    recon: dict[str, Any],
    now: Optional[datetime] = None,
) -> str:
    """Texto de arranque para Telegram (testable sin MT5)."""
    rows = list(recon.get("positions") or [])
    return "\n".join(
        [
            "meta-trader: conectado (rotación crypto demo)",
            f"PID {pid}",
            f"{len(cfg.symbols)} símbolos · magic {int(cfg.magic)} · tope {int(cfg.max_opens_per_hour)} aperturas/h",
            f"Kill switch {'armado' if cfg.kill_switch_armed else 'DESARMADO'} · "
            f"flatten {'on' if cfg.kill_switch_flatten else 'off'} · daily loss {float(cfg.daily_loss_limit):.2f}",
            f"Abiertas {len(rows)} (gestionadas {int(recon.get('positions_managed') or 0)}) · "
            f"pendientes {int(recon.get('pending_orders') or 0)}",
        ]
    )


def _telegram_notify_new_closes(cfg: Config) -> None:
    """Avisa por Telegram cierres nuevos (historial DEAL_ENTRY_OUT, magics del bot)."""
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_NOTIFY_TRADE_CLOSE", False):
        return
    _telegram_seed_seen_close_deals(cfg)

    now = datetime.now()
    # Usar la posición abierta más antigua conocida para cubrir su deal IN,
    # con un mínimo de 24h para no perder grades en trades que duraron muchas horas.
    try:
        open_positions = mt5.positions_get(group="*") or []
        oldest_open_sec = min(
            (int(getattr(p, "time", 0) or 0) for p in open_positions if int(getattr(p, "time", 0) or 0) > 0),
            default=0,
        )
        if oldest_open_sec > 0:
            lookback = max(86400.0, time.time() - float(oldest_open_sec) + 3600.0)
        else:
            lookback = 86400.0
    except Exception:
        lookback = 86400.0
    start = now - timedelta(seconds=lookback)
    deals = mt5.history_deals_get(start, now + timedelta(seconds=2))
    if not deals:
        return

    acc = mt5.account_info()
    cur = str(getattr(acc, "currency", "") or "USD") if acc is not None else "USD"

    in_magic = _in_deal_magic_by_position(deals)
    for d in deals:
        if _deal_entry(d) != int(mt5.DEAL_ENTRY_OUT):
            continue
        t = int(getattr(d, "ticket", 0) or 0)
        if t <= 0 or t in _TELEGRAM_SEEN_OUT_DEAL_TICKETS:
            continue
        if _out_deal_owner_magic(d, cfg, in_magic) is None:
            continue
        _TELEGRAM_SEEN_OUT_DEAL_TICKETS.add(t)

        sym = str(getattr(d, "symbol", "") or "?")
        profit = float(getattr(d, "profit", 0.0) or 0.0)
        swap = float(getattr(d, "swap", 0.0) or 0.0)
        comm = float(getattr(d, "commission", 0.0) or 0.0)
        net = profit + swap + comm
        vol = float(getattr(d, "volume", 0.0) or 0.0)
        price = float(getattr(d, "price", 0.0) or 0.0)
        reason = int(getattr(d, "reason", -1) or -1)
        pos_id = int(getattr(d, "position_id", 0) or 0)
        dtype = int(getattr(d, "type", -1) or -1)
        if dtype == int(mt5.DEAL_TYPE_BUY):
            deal_side_es = "compra"
        elif dtype == int(mt5.DEAL_TYPE_SELL):
            deal_side_es = "venta"
        else:
            deal_side_es = f"tipo_{dtype}"

        outcome = "ganancia" if net > 1e-9 else "pérdida" if net < -1e-9 else "breakeven"
        reason_txt = _deal_reason_label_es(reason)
        sinfo = mt5.symbol_info(sym)
        dig = int(sinfo.digits) if sinfo is not None else 5
        px_txt = f"{price:.{dig}f}" if price else "—"

        msg = (
            "meta-trader: posición cerrada (loop principal)\n"
            f"{sym} · deal de cierre: {deal_side_es} · vol {vol:.2f}\n"
            f"Motivo: {reason_txt}\n"
            f"Precio: {px_txt}\n"
            f"PnL (profit+swap+comisión): {net:+.2f} {cur} → {outcome}"
        )
        if pos_id:
            msg += f"\nPosition ID: {pos_id}"
        try:
            telegram_notify.notify(msg, dedup_key=None)
        except Exception:
            logging.exception("Telegram notify (close) failed (non-fatal).")


def _scan_logs_for_logged_close_deal_tickets(days: int) -> set[int]:
    """
    Scan trades.log + rotated logs in ./logs for TRADE_CLOSE events emitted in the
    last `days`, returning the set of deal_ticket values already logged.

    This is intentionally tolerant: we parse each line as JSON (after the
    "| INFO |" header), skip anything that doesn't match, and stop scanning a
    file as soon as the filesystem mtime is older than the cutoff. We do NOT
    read huge archives beyond that window.
    """
    seen: set[int] = set()
    cutoff = datetime.now() - timedelta(days=max(1, int(days)))
    candidates: list[Path] = []
    main_log = Path(_TRADES_LOG_FILENAME)
    if main_log.exists():
        candidates.append(main_log)
    logs_dir = Path("logs")
    if logs_dir.is_dir():
        try:
            rotated = sorted(
                logs_dir.glob("trades_*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            rotated = []
        for p in rotated:
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                    break
            except Exception:
                continue
            candidates.append(p)

    for path in candidates:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"TRADE_CLOSE"' not in line:
                        continue
                    idx = line.find("{")
                    if idx < 0:
                        continue
                    payload = line[idx:].strip()
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    if obj.get("event") != "TRADE_CLOSE":
                        continue
                    dt = obj.get("deal_ticket")
                    try:
                        dt_int = int(dt) if dt is not None else 0
                    except (TypeError, ValueError):
                        dt_int = 0
                    if dt_int > 0:
                        seen.add(dt_int)
        except Exception:
            logging.debug("Error scanning %s for TRADE_CLOSE history (non-fatal).", path, exc_info=True)
    return seen


def _seed_close_log_tracker(cfg: Config) -> None:
    """
    Seed the "already logged" set from what is actually present in trades.log
    (+ rotated logs), not from the broker's deal history. This way:
      - We never re-emit a TRADE_CLOSE that is already in our log.
      - Any DEAL_ENTRY_OUT in the broker history that does NOT appear in the log
        will be backfilled as a TRADE_CLOSE on the next poll (with
        "backfilled": true) — covers restarts, crashes, and mid-session
        upgrades where a close happened while the bot ran older code.
    """
    global _CLOSE_LOG_SEEN_DEAL_TICKETS, _CLOSE_LOG_TRACKER_SEEDED
    if _CLOSE_LOG_TRACKER_SEEDED:
        return
    _ = cfg  # kept for signature compatibility; seeding is log-based now.
    seen = _scan_logs_for_logged_close_deal_tickets(_CLOSE_LOG_BACKFILL_DAYS)
    _CLOSE_LOG_SEEN_DEAL_TICKETS.update(seen)
    _CLOSE_LOG_TRACKER_SEEDED = True
    logging.info(
        "TRADE_CLOSE tracker seeded from logs: %d prior deal tickets found (lookback=%dd).",
        len(seen), _CLOSE_LOG_BACKFILL_DAYS,
    )


def _first_in_deal_for_position_from_broker(pos_id: int, *, end: datetime) -> Any | None:
    """
    Obtiene el deal DEAL_ENTRY_IN más antiguo de una posición.
    Intenta la API por ``position=``; si el build de MT5 no lo soporta, escanea historial reciente.
    """
    if pos_id <= 0:
        return None
    end_adj = end + timedelta(seconds=2)
    dl: Any | None = None
    try:
        dl = mt5.history_deals_get(datetime(2000, 1, 1), end_adj, position=int(pos_id))
    except TypeError:
        dl = None
    if dl:
        ins = [x for x in dl if _deal_entry(x) == int(mt5.DEAL_ENTRY_IN)]
        if ins:
            return min(ins, key=lambda x: int(getattr(x, "time", 0) or 0))
    start_scan = end - timedelta(days=730)
    dl2 = mt5.history_deals_get(start_scan, end_adj)
    if not dl2:
        return None
    ins2 = [
        x
        for x in dl2
        if int(getattr(x, "position_id", 0) or 0) == int(pos_id)
        and _deal_entry(x) == int(mt5.DEAL_ENTRY_IN)
    ]
    if not ins2:
        return None
    return min(ins2, key=lambda x: int(getattr(x, "time", 0) or 0))


def _position_id_from_deal_ticket(deal_ticket: object) -> Optional[int]:
    """Resuelve position_id desde el ticket del deal de apertura (post-fill)."""
    if deal_ticket is None:
        return None
    try:
        tid = int(deal_ticket)
    except (TypeError, ValueError):
        return None
    if tid <= 0:
        return None
    end = datetime.now() + timedelta(seconds=2)
    try:
        dh = mt5.history_deals_get(datetime(2000, 1, 1), end, ticket=tid)
    except TypeError:
        dh = None
    if not dh:
        return None
    pid = int(getattr(dh[0], "position_id", 0) or 0)
    return pid if pid > 0 else None


def _trade_open_fields_from_trades_logs(
    position_id: int, *, lookback_days: int
) -> tuple[Optional[float], Optional[str], Optional[float]]:
    """
    Si TRADE_OPEN guardó position_id, recupera precio/fecha/lote sin depender del IN deal en el broker.
    """
    if position_id <= 0:
        return None, None, None
    days = max(1, int(lookback_days))
    cutoff = datetime.now() - timedelta(days=days)
    candidates: list[Path] = []
    main_log = Path(_TRADES_LOG_FILENAME)
    if main_log.exists():
        candidates.append(main_log)
    logs_dir = Path("logs")
    if logs_dir.is_dir():
        try:
            rotated = sorted(logs_dir.glob("trades_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            rotated = []
        for p in rotated:
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                    break
            except Exception:
                continue
            candidates.append(p)

    rows: list[tuple[datetime, float, str, float]] = []
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"TRADE_OPEN"' not in line:
                        continue
                    idx = line.find("{")
                    if idx < 0:
                        continue
                    payload = line[idx:].strip()
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    if obj.get("event") != "TRADE_OPEN":
                        continue
                    opid = obj.get("position_id")
                    try:
                        opi = int(opid) if opid is not None else 0
                    except (TypeError, ValueError):
                        opi = 0
                    if opi != int(position_id):
                        continue
                    ts_s = obj.get("ts") or obj.get("opened_at")
                    if not ts_s:
                        continue
                    try:
                        tdt = datetime.fromisoformat(str(ts_s))
                    except Exception:
                        continue
                    px = obj.get("price_open")
                    if px is None:
                        px = obj.get("price")
                    if px is None:
                        continue
                    try:
                        pxf = float(px)
                    except (TypeError, ValueError):
                        continue
                    lotv = float(obj.get("lot", 0.0) or 0.0)
                    rows.append((tdt, pxf, str(ts_s), lotv))
        except Exception:
            logging.debug("Error scanning %s for TRADE_OPEN by position_id (non-fatal).", path, exc_info=True)

    if not rows:
        return None, None, None
    rows.sort(key=lambda x: x[0])
    _, price_open, opened_at, lot = rows[0]
    return price_open, opened_at, (lot if lot > 0 else None)


def _maybe_log_rotation_book(cfg: Config) -> None:
    """Periodic floating PnL of magic 260414. Does not open, close, or resize."""
    global _ROTATION_BOOK_LAST_MONO, _ROTATION_BOOK_WATERMARKS
    interval = float(getattr(cfg, "rotation_book_snapshot_seconds", 60.0) or 0.0)
    if interval <= 0.0:
        return
    now_m = time.monotonic()
    if _ROTATION_BOOK_LAST_MONO > 0.0 and (now_m - _ROTATION_BOOK_LAST_MONO) < interval:
        return
    try:
        positions = mt5.positions_get() or ()
    except Exception:
        logging.exception("ROTATION_BOOK: positions_get failed (non-fatal).")
        return
    from trading.domain.rotation_book import summarize_rotation_book

    magic = int(cfg.magic)
    now_unix = time.time()
    legs: list[dict[str, Any]] = []
    for p in positions:
        if _position_magic_int(p) != magic:
            continue
        t_open = int(getattr(p, "time", 0) or 0)
        profit = float(getattr(p, "profit", 0.0) or 0.0)
        swap = float(getattr(p, "swap", 0.0) or 0.0)
        comm = float(getattr(p, "commission", 0.0) or 0.0)
        legs.append(
            {
                "net": profit + swap + comm,
                "age_seconds": max(0.0, now_unix - float(t_open)) if t_open > 0 else 0.0,
            }
        )
    ts = datetime.now().isoformat(timespec="seconds")
    payload, next_wm = summarize_rotation_book(
        legs,
        watermarks=_ROTATION_BOOK_WATERMARKS,
        ts=ts,
        aged_seconds=float(getattr(cfg, "rotation_stale_seconds", 3600.0) or 3600.0),
        magic=magic,
    )
    _ROTATION_BOOK_LAST_MONO = now_m
    _ROTATION_BOOK_WATERMARKS = next_wm
    _log_event(payload)


def _log_new_closes(cfg: Config) -> None:
    """
    Emite eventos estructurados TRADE_CLOSE para cada deal DEAL_ENTRY_OUT nuevo con nuestro magic.
    Cubre cierres hechos por el bot y también SL/TP ejecutados por el servidor, que antes quedaban
    fuera del log.
    """
    global _CLOSE_LOG_BACKFILL_DONE
    _seed_close_log_tracker(cfg)

    now = datetime.now()
    end = now + timedelta(hours=12)
    # On the first call after seeding, scan far back so that any server-side
    # close that happened while the bot was offline / running older code gets
    # emitted as a backfilled TRADE_CLOSE. Normal calls use a short window.
    if not _CLOSE_LOG_BACKFILL_DONE:
        lookback = float(_CLOSE_LOG_BACKFILL_DAYS) * 86400.0
    else:
        # Usar la posición abierta más antigua conocida para cubrir su deal IN,
        # con un mínimo de 24h para no perder grades en trades que duraron muchas horas.
        try:
            open_positions = mt5.positions_get(group="*") or []
            oldest_open_sec = min(
                (int(getattr(p, "time", 0) or 0) for p in open_positions if int(getattr(p, "time", 0) or 0) > 0),
                default=0,
            )
            if oldest_open_sec > 0:
                lookback = max(86400.0, time.time() - float(oldest_open_sec) + 3600.0)
            else:
                lookback = 86400.0
        except Exception:
            lookback = 86400.0
    start_close = now - timedelta(seconds=lookback)
    deals = mt5.history_deals_get(start_close, end)
    if not deals:
        _CLOSE_LOG_BACKFILL_DONE = True
        return

    # Index IN deals over a long window so OPEN price/time exist even if entry was months ago.
    index_days = max(int(_CLOSE_LOG_BACKFILL_DAYS), 365)
    start_index = now - timedelta(days=index_days)
    deals_index = mt5.history_deals_get(start_index, end)
    by_pos: dict[int, list] = {}
    if deals_index:
        for d in deals_index:
            if not _deal_magic_matches_bot(d, cfg):
                continue
            pid = int(getattr(d, "position_id", 0) or 0)
            if pid <= 0:
                continue
            by_pos.setdefault(pid, []).append(d)

    in_magic = _in_deal_magic_by_position(deals_index or deals)
    emitted = 0
    pending = False
    for d in sorted(deals, key=lambda x: int(getattr(x, "time_msc", 0) or getattr(x, "time", 0) or 0), reverse=True):
        if _deal_entry(d) != int(mt5.DEAL_ENTRY_OUT):
            continue
        t = int(getattr(d, "ticket", 0) or 0)
        if t <= 0 or t in _CLOSE_LOG_SEEN_DEAL_TICKETS:
            continue
        owner_magic = _out_deal_owner_magic(d, cfg, in_magic)
        if owner_magic is None:
            continue
        if emitted >= _CLOSE_LOG_MAX_PER_CALL:
            pending = True
            break
        emitted += 1
        _CLOSE_LOG_SEEN_DEAL_TICKETS.add(t)

        sym = str(getattr(d, "symbol", "") or "?")
        profit = float(getattr(d, "profit", 0.0) or 0.0)
        swap = float(getattr(d, "swap", 0.0) or 0.0)
        comm = float(getattr(d, "commission", 0.0) or 0.0)
        net = profit + swap + comm
        vol_out = float(getattr(d, "volume", 0.0) or 0.0)
        price_close = float(getattr(d, "price", 0.0) or 0.0)
        reason = int(getattr(d, "reason", -1) or -1)
        pos_id = int(getattr(d, "position_id", 0) or 0)
        dtype = int(getattr(d, "type", -1) or -1)
        deal_comment = str(getattr(d, "comment", "") or "")
        strategy = _strategy_label_for_magic(cfg, owner_magic)
        # The closing deal is the opposite direction of the original position.
        if dtype == int(mt5.DEAL_TYPE_BUY):
            position_side: Signal = "SELL"
        elif dtype == int(mt5.DEAL_TYPE_SELL):
            position_side = "BUY"
        else:
            position_side = "SELL"  # fallback, should not happen

        time_close = int(getattr(d, "time", 0) or 0)
        closed_at = datetime.fromtimestamp(time_close).isoformat(timespec="seconds") if time_close > 0 else None
        # A close is "backfilled" when we're logging it significantly after it
        # happened (e.g. the bot was offline / running older code at close time).
        is_backfill = bool(time_close > 0 and (now.timestamp() - time_close) > 300.0)

        price_open: float | None = None
        opened_at: str | None = None
        volume_in: float | None = None
        if pos_id and pos_id in by_pos:
            in_deals = [x for x in by_pos[pos_id] if _deal_entry(x) == int(mt5.DEAL_ENTRY_IN)]
            if in_deals:
                first_in = min(in_deals, key=lambda x: int(getattr(x, "time", 0) or 0))
                price_open = float(getattr(first_in, "price", 0.0) or 0.0) or None
                t_in = int(getattr(first_in, "time", 0) or 0)
                if t_in > 0:
                    opened_at = datetime.fromtimestamp(t_in).isoformat(timespec="seconds")
                volume_in = float(getattr(first_in, "volume", 0.0) or 0.0) or None

        if (price_open is None or opened_at is None) and pos_id > 0:
            fin = _first_in_deal_for_position_from_broker(pos_id, end=now)
            if fin is not None:
                price_open = float(getattr(fin, "price", 0.0) or 0.0) or None
                t_in2 = int(getattr(fin, "time", 0) or 0)
                if t_in2 > 0:
                    opened_at = datetime.fromtimestamp(t_in2).isoformat(timespec="seconds")
                volume_in = float(getattr(fin, "volume", 0.0) or 0.0) or None

        if (price_open is None or opened_at is None) and pos_id > 0:
            lp, lo, lv = _trade_open_fields_from_trades_logs(pos_id, lookback_days=max(int(_CLOSE_LOG_BACKFILL_DAYS), 365))
            if price_open is None and lp is not None:
                price_open = lp
            if opened_at is None and lo is not None:
                opened_at = lo
            if volume_in is None and lv is not None:
                volume_in = lv

        duration_seconds: int | None = None
        if opened_at and closed_at:
            try:
                duration_seconds = int(
                    (datetime.fromisoformat(closed_at) - datetime.fromisoformat(opened_at)).total_seconds()
                )
            except Exception:
                duration_seconds = None

        grades = _compute_trade_grades(
            symbol=sym,
            side=position_side,
            price_open=price_open,
            price_close=(price_close or None),
            opened_at_iso=opened_at,
            closed_at_iso=closed_at,
        )

        from trading.application.rotation_audit import classify_rotation_close

        close_how = classify_rotation_close(comment=deal_comment, reason_code=reason)
        open_audit = {}
        if pos_id and int(pos_id) in _ROTATION_OPEN_AUDIT:
            open_audit = dict(_ROTATION_OPEN_AUDIT.pop(int(pos_id), {}) or {})
        elif not is_backfill and str(sym).strip().upper() in _ROTATION_OPEN_AUDIT_SYM:
            open_audit = dict(_ROTATION_OPEN_AUDIT_SYM.pop(str(sym).strip().upper(), {}) or {})

        close_payload: dict[str, Any] = {
                "event": "TRADE_CLOSE",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "engine": open_audit.get("engine") or "rotation",
                "symbol": sym,
                "side": position_side,
                "position_id": pos_id or None,
                "deal_ticket": t,
                "volume": vol_out,
                "volume_in": volume_in,
                "price_open": price_open,
                "price_close": price_close or None,
                "profit": profit,
                "swap": swap,
                "commission": comm,
                "net": net,
                "reason_code": reason,
                "reason_label": _deal_reason_label_es(reason),
                "close_how": close_how,
                "why_closed": close_how,
                "comment": deal_comment,
                "opened_at": opened_at,
                "closed_at": closed_at,
                "duration_seconds": duration_seconds,
                "spread_usd": _spread_usd_now(sym, vol_out or volume_in or 0.0),
                "open_spread_usd": open_audit.get("spread_usd"),
                "backfilled": is_backfill,
                "entry_grade": grades.get("entry_grade"),
                "exit_grade": grades.get("exit_grade"),
                "trade_grade": grades.get("trade_grade"),
                "magic": owner_magic,
                "strategy": strategy,
                "why_open": open_audit.get("why_open"),
                "open_direction": open_audit.get("direction") or open_audit.get("side"),
                "open_buy_votes": open_audit.get("buy"),
                "open_sell_votes": open_audit.get("sell"),
                "open_votes": open_audit.get("votes"),
                "open_skip_reason": open_audit.get("skip_reason"),
                "open_action": open_audit.get("action"),
                "open_risk_usd": open_audit.get("risk_usd"),
                "open_sl": open_audit.get("sl"),
                "open_tp": open_audit.get("tp"),
                "murphy": open_audit.get("murphy"),
        }
        _log_event(close_payload)

    # Revert to the short lookback window only once the backlog is drained.
    if not pending:
        _CLOSE_LOG_BACKFILL_DONE = True


def _order_send_deal_try_fillings(symbol: str, request: dict, filling_candidates: list) -> Any:
    """
    Try order_send once per filling mode (10030 → next mode).
    Returns last result object or None if exception / null result.
    """
    result: Any = None
    for fill in filling_candidates:
        request["type_filling"] = fill
        try:
            result = mt5.order_send(request)
        except Exception:
            logging.exception("[%s] order_send raised exception (type_filling=%s).", symbol, fill)
            return None
        if result is None:
            logging.error("[%s] order_send returned None (type_filling=%s). last_error=%s", symbol, fill, mt5.last_error())
            return None
        if int(getattr(result, "retcode", -1)) == 10030:
            logging.warning("[%s] Unsupported filling mode with type_filling=%s (retcode=10030). Retrying...", symbol, fill)
            continue
        break
    return result


def open_trade(cfg: Config, symbol: str, side: Signal, audit: Optional[dict] = None) -> bool:
    if side not in ("BUY", "SELL"):
        return False
    rotation_cost: dict[str, Any] = {}

    if _kill_switch_halt_active(cfg):
        logging.warning("[%s] Skipping new entry: kill switch halt active.", symbol)
        _log_rotation_reject(symbol, side, "kill_switch_halt")
        return False

    if not _ensure_symbol(symbol):
        return False

    p = _effective_params(cfg, symbol)

    allowed, reason = _can_open_additional_position(cfg, symbol, side, p)
    if not allowed:
        logging.info("[%s] Skipping new entry: %s.", symbol, reason)
        _log_rotation_reject(symbol, side, str(reason or "slot_ocupado"))
        return False

    open_total = _count_managed_open_positions(cfg)
    if open_total >= cfg.max_open_trades:
        logging.info("[%s] Max open trades reached (%d/%d). Skipping.", symbol, open_total, cfg.max_open_trades)
        _log_rotation_reject(symbol, side, "max_open_trades", open_total=open_total, cap=cfg.max_open_trades)
        return False

    sl_money = float(p["sl_money"])
    tp_money = float(p["tp_money"])
    max_spread_points = _effective_max_spread_points(cfg, symbol, p)
    _, spread, _ = _is_spread_ok(cfg, symbol, max_spread_points=max_spread_points)

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        logging.error("[%s] Missing tick/info.", symbol)
        _log_rotation_reject(symbol, side, "sin_tick")
        return False

    term = mt5.terminal_info()
    if term is not None and hasattr(term, "trade_allowed") and not bool(getattr(term, "trade_allowed", True)):
        logging.error("[%s] Terminal trade not allowed (enable Algo Trading in MT5). Skipping.", symbol)
        return False
    if hasattr(info, "trade_mode") and int(getattr(info, "trade_mode") or 0) == int(getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", -1)):
        logging.error("[%s] Symbol trade mode disabled by broker. Skipping.", symbol)
        return False

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price = float(tick.ask if side == "BUY" else tick.bid)

    from trading.domain.rotation_stops import (
        clamp_symmetric_stops,
        rotation_spread_skip_reason,
        rotation_stop_distance,
        size_lot_for_percent_stop,
        spread_usd_for_lot,
    )

    spread_px = abs(float(getattr(tick, "ask", 0.0) or 0.0) - float(getattr(tick, "bid", 0.0) or 0.0))
    min_dist = _min_stop_distance_price(info, tick)
    risk = abs(float(sl_money)) if sl_money else float(getattr(cfg, "max_risk_usd", 0.50) or 0.50)
    sized = size_lot_for_percent_stop(
        risk_usd=risk,
        price=price,
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
        volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
        volume_max=float(getattr(info, "volume_max", 100.0) or 100.0),
        max_risk_usd=float(getattr(cfg, "max_risk_usd", 0.50) or 0.50),
    )
    if not sized.ok:
        logging.info("[%s] Rotation: cannot size 5pct stop (%s). Skipping.", symbol, sized.reason)
        _log_rotation_reject(symbol, side, f"size_5pct:{sized.reason}", risk_usd=risk)
        return False
    lot = _clip_lot_to_margin(
        symbol,
        _clamp_volume(symbol, sized.lot),
        max_margin_frac=float(getattr(cfg, "lot_max_margin_frac", 0.5) or 0.0),
    )
    if lot <= 0.0:
        logging.info("[%s] Rotation: lot zero after margin clip. Skipping.", symbol)
        _log_rotation_reject(symbol, side, "lote_cero_margen")
        return False
    if _currency_exposure_blocks_open(cfg, symbol, side, float(lot)):
        return False
    tv = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    ts = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    intended = rotation_stop_distance(
        price=price,
        lot=lot,
        tick_value=tv,
        tick_size=ts,
        max_risk_usd=float(getattr(cfg, "max_risk_usd", 0.50) or 0.50),
        min_dist=0.0,
    )
    if intended is None:
        intended = float(sized.dist) if float(sized.dist) > 0.0 else None
    if intended is None or intended <= 0.0:
        logging.info("[%s] Rotation: no intended stop distance. Skipping.", symbol)
        _log_rotation_reject(symbol, side, "spread_sin_stop", risk_usd=risk)
        return False
    if min_dist > intended + 1e-12:
        logging.info(
            "[%s] Rotation: broker min_dist %.6f > stop %.6f. Skipping.",
            symbol,
            min_dist,
            intended,
        )
        _log_rotation_reject(
            symbol, side, "sl_tp_invalido", min_dist=min_dist, stop_dist=intended, price=price
        )
        return False
    spread_skip = rotation_spread_skip_reason(
        spread_px=spread_px,
        stop_dist=intended,
        lot=lot,
        tick_value=tv,
        tick_size=ts,
        risk_usd=risk,
        max_spread_frac_of_risk=float(
            getattr(cfg, "rotation_max_spread_usd_frac", 0.10) or 0.10
        ),
    )
    hole = spread_usd_for_lot(spread_px=spread_px, lot=lot, tick_value=tv, tick_size=ts)
    hole_frac = (float(hole) / risk) if hole is not None and risk > 0.0 else None
    rotation_cost["spread_usd"] = None if hole is None else round(float(hole), 6)
    rotation_cost["spread_vs_risk"] = None if hole_frac is None else round(float(hole_frac), 6)
    rotation_cost["stop_dist"] = round(float(intended), 8)
    if spread_skip:
        logging.info(
            "[%s] Rotation: spread gate %s (spread_px=%.6f dist=%.6f hole$=%s frac=%s). Skipping.",
            symbol,
            spread_skip,
            spread_px,
            intended,
            rotation_cost.get("spread_usd"),
            rotation_cost.get("spread_vs_risk"),
        )
        _log_rotation_reject(
            symbol,
            side,
            spread_skip,
            spread_usd=rotation_cost.get("spread_usd"),
            spread_vs_risk=rotation_cost.get("spread_vs_risk"),
            stop_dist=intended,
            risk_usd=risk,
            lot=lot,
        )
        return False
    dist = intended
    if side == "BUY":
        sl, tp = price - dist, price + dist
    else:
        sl, tp = price + dist, price - dist
    pct = 100.0 * dist / price if price > 0 else 0.0
    logging.info(
        "[%s] Rotation stops: lot=%.4f dist=%s (%.2f%%) ~$%.4f reason=%s",
        symbol,
        lot,
        dist,
        pct,
        sized.usd,
        sized.reason,
    )
    clamped = clamp_symmetric_stops(
        side=side,
        entry=price,
        sl=sl,
        tp=tp,
        min_dist=min_dist,
        point=float(getattr(info, "point", 0.0) or 0.0),
        digits=int(info.digits),
    )
    if clamped is None:
        logging.info(
            "[%s] Rotation: no legal SL/TP at this price (min_dist=%.6f). Skipping.",
            symbol,
            min_dist,
        )
        _log_rotation_reject(symbol, side, "sl_tp_invalido", min_dist=min_dist, price=price)
        return False
    sl, tp = clamped
    if sl is None or not _sl_is_valid_now(side=side, sl=float(sl), info=info, tick=tick):
        logging.info("[%s] Rotation: clamped SL still invalid vs bid/ask. Skipping.", symbol)
        _log_rotation_reject(symbol, side, "sl_vs_mercado", sl=sl, tp=tp, price=price)
        return False
    logging.info("[%s] Rotation stops clamped to SL=%s TP=%s", symbol, sl, tp)

    digits = int(info.digits)
    filling_candidates = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
    ]
    request: dict = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "deviation": cfg.deviation_points,
        "magic": cfg.magic,
        "comment": "rot_4_5",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    if sl is not None:
        request["sl"] = sl
    if tp is not None:
        request["tp"] = tp

    logging.info(
        "[%s] Opening %s | lot=%.2f | price=%.*f | spread=%.1f pts | SL=%s | TP=%s",
        symbol,
        side,
        lot,
        digits,
        price,
        spread,
        f"{sl:.{digits}f}" if sl is not None else "None",
        f"{tp:.{digits}f}" if tp is not None else "None",
    )

    result = _order_send_deal_try_fillings(symbol, request, filling_candidates)
    if result is None:
        logging.error("[%s] order_send returned None. last_error=%s", symbol, mt5.last_error())
        _record_failed_order_attempt(symbol, cfg, None)
        _log_rotation_reject(symbol, side, "order_send_none", last_error=str(mt5.last_error()))
        return False

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(
            "[%s] Order failed. retcode=%s | comment=%s | request_id=%s",
            symbol,
            result.retcode,
            getattr(result, "comment", ""),
            getattr(result, "request_id", ""),
        )
        _record_failed_order_attempt(symbol, cfg, result)
        _log_rotation_reject(
            symbol,
            side,
            f"retcode_{getattr(result, 'retcode', '')}",
            broker_comment=getattr(result, "comment", ""),
            sl=sl,
            tp=tp,
            lot=lot,
            price=price,
        )
        return False

    logging.info("[%s] Order placed OK. deal=%s order=%s", symbol, getattr(result, "deal", None), getattr(result, "order", None))
    _LAST_ROTATION_OPEN_SKIP.pop(str(symbol).strip().upper(), None)
    _last_failed_order_attempt.pop(symbol, None)
    _last_trade_time[symbol] = datetime.now()
    pos_id_for_log = _position_id_from_deal_ticket(getattr(result, "deal", None))
    open_ts_iso = datetime.now().isoformat(timespec="seconds")
    open_payload: dict[str, Any] = {
            "event": "TRADE_OPEN",
            "ts": open_ts_iso,
            "opened_at": open_ts_iso,
            "engine": "rotation",
            "why_open": "consenso_4_5",
            "symbol": symbol,
            "side": side,
            "lot": lot,
            "price": price,
            "price_open": price,
            "spread_points": float(spread) if spread is not None else None,
            "spread_usd": rotation_cost.get("spread_usd"),
            "spread_vs_risk": rotation_cost.get("spread_vs_risk"),
            "stop_dist": rotation_cost.get("stop_dist"),
            "sl": sl,
            "tp": tp,
            "sl_money": sl_money,
            "tp_money": tp_money,
            "magic": cfg.magic,
            "retcode": int(getattr(result, "retcode", -1)),
            "order": getattr(result, "order", None),
            "deal": getattr(result, "deal", None),
            "position_id": pos_id_for_log,
        }
    if isinstance(audit, dict):
        for key, val in audit.items():
            if key in ("event", "ts") or val is None:
                continue
            if key not in open_payload:
                open_payload[key] = val
    _log_event(open_payload)
    stored = {k: v for k, v in open_payload.items() if k != "event"}
    if pos_id_for_log:
        _ROTATION_OPEN_AUDIT[int(pos_id_for_log)] = stored
    _ROTATION_OPEN_AUDIT_SYM[str(symbol).strip().upper()] = stored

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_TRADE_OPEN", False):
        try:
            sl_txt = f"{sl:.{digits}f}" if sl is not None else "—"
            tp_txt = f"{tp:.{digits}f}" if tp is not None else "—"
            acc = mt5.account_info()
            cur = str(getattr(acc, "currency", "") or "USD") if acc is not None else "USD"
            pos_extra = f"\nPosition ID: {pos_id_for_log}" if pos_id_for_log else ""
            telegram_notify.notify(
                f"meta-trader: orden ejecutada\n"
                f"{symbol} {side} · lot {lot:.2f} · precio {price:.{digits}f} {cur}\n"
                f"SL {sl_txt} · TP {tp_txt}\n"
                f"Order {getattr(result, 'order', None)} · Deal {getattr(result, 'deal', None)}{pos_extra}",
                dedup_key=None,
            )
        except Exception:
            logging.exception("Telegram notify (open) failed (non-fatal).")

    return True


def _min_stop_distance_price(symbol_info, symbol_tick) -> float:
    """
    Minimal distance (in PRICE units) required to place/modify stops.
    Uses both stops_level and freeze_level when available.
    """
    if symbol_info is None:
        return 0.0
    pt = float(getattr(symbol_info, "point", 0.0) or 0.0)
    if pt <= 0:
        return 0.0
    stops_lvl = float(getattr(symbol_info, "trade_stops_level", 0.0) or 0.0)
    freeze_lvl = float(getattr(symbol_info, "trade_freeze_level", 0.0) or 0.0)
    lvl = max(stops_lvl, freeze_lvl, 0.0)
    return float(lvl) * pt


def _sl_is_valid_now(*, side: str, sl: float, info, tick) -> bool:
    """
    Check if SL is on the correct side and respects stops/freeze distance vs current market.
    MT5 servers often reject invalid stops with retcode 10016 ("Invalid stops").
    """
    if sl is None or tick is None or info is None:
        return True
    min_dist = _min_stop_distance_price(info, tick)
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    if bid <= 0 or ask <= 0:
        return True
    if side == "BUY":
        return sl < (bid - min_dist)
    return sl > (ask + min_dist)


def _existing_position_tp(symbol: str, ticket: int) -> Optional[float]:
    """Current broker TP for `ticket`, or None if missing/zero."""
    ps = mt5.positions_get(ticket=int(ticket))
    if not ps:
        ps = mt5.positions_get(symbol=symbol)
    for p0 in ps or []:
        if int(getattr(p0, "ticket", 0) or 0) != int(ticket):
            continue
        v = float(getattr(p0, "tp", 0.0) or 0.0)
        return v if v > 0.0 else None
    return None


def _build_sltp_request(
    symbol: str,
    ticket: int,
    sl: Optional[float],
    tp: Optional[float],
    *,
    existing_tp: Optional[float] = None,
    action: Optional[int] = None,
) -> dict:
    """
    TRADE_ACTION_SLTP payload.

    tp=None means “leave TP unchanged”. IC Markets treats an omitted TP as 0, so we
    re-send the current TP when it is still > 0.
    """
    request: dict = {
        "action": mt5.TRADE_ACTION_SLTP if action is None else action,
        "symbol": symbol,
        "position": int(ticket),
    }
    if sl is not None:
        request["sl"] = float(sl)
    if tp is not None:
        request["tp"] = float(tp)
    elif existing_tp is not None and float(existing_tp) > 0.0:
        request["tp"] = float(existing_tp)
    return request


def _modify_position_sl_tp(
    symbol: str,
    ticket: int,
    sl: Optional[float],
    tp: Optional[float],
    *,
    side: Optional[str] = None,
    info=None,
    tick=None,
) -> bool:
    if sl is not None and side in ("BUY", "SELL") and info is not None and tick is not None:
        if not _sl_is_valid_now(side=side, sl=float(sl), info=info, tick=tick):
            logging.warning(
                "[%s] SLTP modify skipped: invalid SL for current price (side=%s sl=%s bid=%s ask=%s stops/freezes).",
                symbol,
                side,
                sl,
                getattr(tick, "bid", None),
                getattr(tick, "ask", None),
            )
            return False

    request = _build_sltp_request(
        symbol,
        int(ticket),
        sl,
        tp,
        existing_tp=_existing_position_tp(symbol, int(ticket)) if tp is None else None,
    )

    result = mt5.order_send(request)
    if result is None:
        logging.error("[%s] SLTP modify returned None. request=%s last_error=%s", symbol, request, mt5.last_error())
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(
            "[%s] SLTP modify failed. request=%s retcode=%s comment=%s last_error=%s",
            symbol,
            request,
            getattr(result, "retcode", None),
            getattr(result, "comment", ""),
            mt5.last_error(),
        )
        return False
    return True


def _position_magic(ticket: int) -> int:
    try:
        rows = mt5.positions_get(ticket=int(ticket))
    except Exception:
        return 0
    if not rows:
        return 0
    return int(getattr(rows[0], "magic", 0) or 0)


def _close_position_market(symbol: str, ticket: int, volume: float, side: str, comment: str) -> bool:
    """
    Close a position at market. `side` is the position side ("BUY"/"SELL"); the
    closing order uses the opposite direction.
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logging.error("[%s] Close market: no tick available.", symbol)
        return False
    if side == "BUY":
        order_type = mt5.ORDER_TYPE_SELL
        price = float(tick.bid)
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = float(tick.ask)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "position": int(ticket),
        "price": price,
        "deviation": 30,
        "magic": _position_magic(ticket),
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
    }
    filling_candidates = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
    ]
    result = _order_send_deal_try_fillings(symbol, request, filling_candidates)
    if result is None:
        logging.error("[%s] Close market returned None. last_error=%s", symbol, mt5.last_error())
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        rc = getattr(result, "retcode", "n/a")
        cm = getattr(result, "comment", "")
        logging.error("[%s] Close market failed. retcode=%s comment=%s", symbol, rc, cm)
        return False
    return True


def _rotation_position_age_seconds(p, ticket: int) -> float:
    """Wall-clock age. Ignore a broker clock that looks hours-ahead of first sight."""
    now = time.time()
    if ticket > 0 and ticket not in _ROTATION_FIRST_SEEN:
        _ROTATION_FIRST_SEEN[ticket] = now
    local_age = now - float(_ROTATION_FIRST_SEEN.get(ticket, now))
    broker_age = float(_position_age_seconds(p))
    if local_age < 120.0 and broker_age > local_age + 300.0:
        return local_age
    return max(broker_age, local_age)


def _close_stale_rotation_if_needed(
    cfg: Config,
    p,
    *,
    symbol: str,
    ticket: int,
    lot: float,
    side: str,
    profit: float,
) -> bool:
    """True if the position was closed (caller must skip further management)."""
    from trading.domain.rotation_stale import stale_rotation_close_reason

    reason = stale_rotation_close_reason(
        age_seconds=_rotation_position_age_seconds(p, ticket),
        profit=float(profit),
        min_age_seconds=float(getattr(cfg, "rotation_stale_seconds", 0.0) or 0.0),
        tiny_loss_usd=float(getattr(cfg, "rotation_stale_tiny_loss_usd", -0.0001)),
        take_usd=float(getattr(cfg, "rotation_stale_take_usd", 0.01) or 0.0),
        close_all_after_age=bool(getattr(cfg, "rotation_stale_close_all", False)),
    )
    if not reason or ticket <= 0 or lot <= 0:
        return False
    comment = {
        "stale_take": "rot_stale_take",
        "stale_time": "rot_stale_time",
    }.get(reason, "rot_stale_loss")
    logging.info(
        "[%s] Rotation stale close (%s) ticket=%s profit=%.6f -> market",
        symbol,
        reason,
        ticket,
        profit,
    )
    ok = _close_position_market(symbol, ticket, lot, side, comment=comment)
    _log_event(
        {
            "event": "ROTATION_STALE_CLOSE",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "engine": "rotation",
            "symbol": symbol,
            "ticket": ticket,
            "side": side,
            "profit": round(float(profit), 6),
            "reason": reason,
            "why_closed": reason,
            "closed": bool(ok),
        }
    )
    return bool(ok)


def _rotation_harvest_legs(cfg: Config, positions, *, now_unix: float) -> tuple[list[dict[str, Any]], list]:
    magic = int(cfg.magic)
    legs: list[dict[str, Any]] = []
    targets: list = []
    for p in positions:
        if _position_magic_int(p) != magic:
            continue
        if not _position_is_managed_by_bot(p, cfg):
            continue
        ticket = int(getattr(p, "ticket", 0) or 0)
        if ticket <= 0 or ticket_is_manually_excepted(ticket):
            continue
        t_open = int(getattr(p, "time", 0) or 0)
        profit = float(getattr(p, "profit", 0.0) or 0.0)
        swap = float(getattr(p, "swap", 0.0) or 0.0)
        comm = float(getattr(p, "commission", 0.0) or 0.0)
        legs.append(
            {
                "net": profit + swap + comm,
                "age_seconds": max(0.0, now_unix - float(t_open)) if t_open > 0 else 0.0,
            }
        )
        targets.append(p)
    return legs, targets


def _flatten_rotation_book_if_needed(cfg: Config, positions) -> set[int]:
    """Close every rotation ticket if the floating book cleared the harvest. Returns closed tickets."""
    from trading.domain.rotation_book import book_harvest_should_flatten

    harvest_usd = float(getattr(cfg, "rotation_book_harvest_usd", 0.0) or 0.0)
    if harvest_usd <= 0.0:
        return set()
    legs, targets = _rotation_harvest_legs(cfg, positions, now_unix=time.time())
    ages = [float(leg.get("age_seconds") or 0.0) for leg in legs]
    nets = [float(leg.get("net") or 0.0) for leg in legs]
    floating_net = round(sum(nets), 6)
    min_age = min(ages) if ages else None
    if not book_harvest_should_flatten(
        floating_net=floating_net,
        n_open=len(targets),
        min_age_seconds=min_age,
        harvest_usd=harvest_usd,
        min_age_seconds_required=float(getattr(cfg, "rotation_book_harvest_min_age_seconds", 600.0) or 0.0),
        min_open=int(getattr(cfg, "rotation_book_harvest_min_open", 2) or 2),
    ):
        return set()
    closed: set[int] = set()
    failed = 0
    for p in targets:
        ticket = int(getattr(p, "ticket", 0) or 0)
        symbol = str(getattr(p, "symbol", "") or "")
        lot = float(getattr(p, "volume", 0.0) or 0.0)
        side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
        if ticket <= 0 or lot <= 0.0 or not symbol:
            failed += 1
            continue
        ok = _close_position_market(symbol, ticket, lot, side, comment="rot_book_harvest")
        if ok:
            closed.add(ticket)
        else:
            failed += 1
    _log_event(
        {
            "event": "ROTATION_BOOK_HARVEST",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "engine": "rotation",
            "why_closed": "book_harvest",
            "n_open": len(targets),
            "n_closed": len(closed),
            "n_failed": failed,
            "floating_net": round(floating_net, 4),
            "min_age_seconds": None if min_age is None else int(min_age),
            "max_age_seconds": int(max(ages)) if ages else None,
            "harvest_usd": harvest_usd,
        }
    )
    logging.info(
        "Rotation book harvest: net=%.4f n=%s closed=%s failed=%s min_age=%s",
        floating_net,
        len(targets),
        len(closed),
        failed,
        None if min_age is None else int(min_age),
    )
    return closed


KILL_SWITCH_HALT_PATH = Path("kill_switch.halt")
KILL_SWITCH_CLEAR_PATH = Path("kill_switch.clear")
KILL_SWITCH_BLOCK_LOG_SECONDS = 600.0
_kill_switch_last_block_log: float = 0.0
_kill_switch_disarmed_logged = False


def _kill_switch_halt_active(cfg: Config) -> bool:
    """Entry guard. Any error reading the halt blocks the entry."""
    from trading.domain.kill_switch import halt_enforced
    from trading.infrastructure.kill_switch_file import load_halt

    try:
        return halt_enforced(
            armed=bool(getattr(cfg, "kill_switch_armed", True)),
            record=load_halt(KILL_SWITCH_HALT_PATH),
        )
    except Exception:
        logging.exception("kill_switch: no se pudo leer el halt; bloqueo la entrada.")
        return True


def _kill_switch_close_book(cfg: Config) -> tuple[int, int, int, int]:
    """Close every managed rotation ticket. Returns (n_targets, n_closed, n_failed, n_manual_skipped)."""
    positions = mt5.positions_get() or ()
    magic = int(cfg.magic)
    mine = [p for p in positions if _position_magic_int(p) == magic]
    _legs, targets = _rotation_harvest_legs(cfg, positions, now_unix=time.time())
    closed = 0
    failed = 0
    for p in targets:
        ticket = int(getattr(p, "ticket", 0) or 0)
        symbol = str(getattr(p, "symbol", "") or "")
        lot = float(getattr(p, "volume", 0.0) or 0.0)
        side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
        if ticket <= 0 or lot <= 0.0 or not symbol:
            failed += 1
            continue
        if _close_position_market(symbol, ticket, lot, side, comment="kill_switch"):
            closed += 1
        else:
            failed += 1
    return len(targets), closed, failed, max(0, len(mine) - len(targets))


def _kill_switch_flatten(cfg: Config, record: dict, *, pnl_today: float, retry: bool) -> dict:
    from trading.domain.kill_switch import after_flatten
    from trading.infrastructure.kill_switch_file import save_halt

    ts = datetime.now().isoformat(timespec="seconds")
    n_targets, n_closed, n_failed, n_manual = _kill_switch_close_book(cfg)
    record = after_flatten(record, n_closed=n_closed, n_failed=n_failed, ts=ts)
    try:
        save_halt(record, KILL_SWITCH_HALT_PATH)
    except OSError:
        logging.exception("kill_switch: no se pudo actualizar %s.", KILL_SWITCH_HALT_PATH)
    _log_event(
        {
            "event": "KILL_SWITCH_FLATTEN",
            "ts": ts,
            "engine": "rotation",
            "why_closed": "kill_switch",
            "retry": bool(retry),
            "pnl_today": round(float(pnl_today), 4),
            "daily_loss_limit": float(cfg.daily_loss_limit),
            "n_targets": n_targets,
            "n_closed": n_closed,
            "n_failed": n_failed,
            "n_manual_skipped": n_manual,
            "flatten_pending": bool(record.get("flatten_pending")),
        }
    )
    logging.warning(
        "KILL SWITCH flatten%s: pnl_today=%.2f limit=%.2f closed=%s/%s failed=%s manual_skipped=%s",
        " (retry)" if retry else "",
        float(pnl_today),
        float(cfg.daily_loss_limit),
        n_closed,
        n_targets,
        n_failed,
        n_manual,
    )
    if not retry or n_closed > 0:
        telegram_notify.notify_kill_switch(
            f"KILL SWITCH ({'reintento' if retry else 'daily loss'}): PnL hoy {float(pnl_today):.2f} <= "
            f"{float(cfg.daily_loss_limit):.2f}. Cerradas {n_closed}/{n_targets}, fallidas {n_failed}. "
            f"Entradas bloqueadas hasta clear manual (borrar {KILL_SWITCH_HALT_PATH} o crear {KILL_SWITCH_CLEAR_PATH})."
        )
    return record


def _kill_switch_step(cfg: Config, *, reached: bool, pnl_today: float) -> dict[str, Any]:
    """Once per loop: honor a manual clear, flatten on the first daily-loss hit if armed, report halt.

    BOT_KILL_SWITCH_FLATTEN=0 (default) never closes or writes a halt; an existing
    halt file still blocks entries. Any unexpected error blocks entries this loop.
    """
    global _kill_switch_last_block_log, _kill_switch_disarmed_logged
    from trading.domain.kill_switch import flatten_now, flatten_retry, halt_is_active, halt_record
    from trading.infrastructure.kill_switch_file import consume_clear_request, load_halt, save_halt

    flatten_enabled = bool(getattr(cfg, "kill_switch_flatten", False))
    armed = bool(getattr(cfg, "kill_switch_armed", True))
    try:
        if consume_clear_request(KILL_SWITCH_HALT_PATH, KILL_SWITCH_CLEAR_PATH):
            _log_event(
                {
                    "event": "KILL_SWITCH_CLEAR",
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "source": "clear_file",
                }
            )
            logging.warning("KILL SWITCH: halt limpiado a mano (%s).", KILL_SWITCH_CLEAR_PATH)
            telegram_notify.notify_kill_switch("KILL SWITCH: halt limpiado a mano. Entradas habilitadas.")
        record = load_halt(KILL_SWITCH_HALT_PATH)
        if not armed:
            if halt_is_active(record) and not _kill_switch_disarmed_logged:
                _log_event(
                    {
                        "event": "KILL_SWITCH_DISARMED",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "halt": record,
                    }
                )
                _kill_switch_disarmed_logged = True
            return {"armed": False, "flatten_enabled": flatten_enabled, "halt_active": False}
        halt_active = halt_is_active(record)
        if flatten_now(flatten_enabled=flatten_enabled, daily_reached=reached, halt_active=halt_active):
            ts = datetime.now().isoformat(timespec="seconds")
            record = halt_record(ts=ts, pnl_today=float(pnl_today), daily_loss_limit=float(cfg.daily_loss_limit))
            try:
                save_halt(record, KILL_SWITCH_HALT_PATH)
            except OSError:
                logging.exception("kill_switch: no se pudo persistir %s; igual aplano.", KILL_SWITCH_HALT_PATH)
            _log_event({"event": "KILL_SWITCH_HALT", "ts": ts, **record})
            record = _kill_switch_flatten(cfg, record, pnl_today=float(pnl_today), retry=False)
            halt_active = True
        elif flatten_retry(flatten_enabled=flatten_enabled, record=record):
            record = _kill_switch_flatten(cfg, dict(record or {}), pnl_today=float(pnl_today), retry=True)
        if halt_active:
            now_m = time.monotonic()
            if now_m - _kill_switch_last_block_log >= KILL_SWITCH_BLOCK_LOG_SECONDS:
                _log_event(
                    {
                        "event": "KILL_SWITCH_BLOCK",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "reason": (record or {}).get("reason"),
                        "since": (record or {}).get("ts"),
                        "flatten_pending": bool((record or {}).get("flatten_pending")),
                    }
                )
                _kill_switch_last_block_log = now_m
        return {
            "armed": True,
            "flatten_enabled": flatten_enabled,
            "halt_active": bool(halt_active),
            "reason": (record or {}).get("reason") if halt_active else None,
            "since": (record or {}).get("ts") if halt_active else None,
        }
    except Exception as exc:
        logging.exception("kill_switch: error en el paso; bloqueo entradas este ciclo.")
        _log_event(
            {
                "event": "KILL_SWITCH_ERROR",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "error": str(exc)[:300],
            }
        )
        return {"armed": armed, "flatten_enabled": flatten_enabled, "halt_active": True, "reason": "error"}


def _repair_rotation_floor_stops(cfg: Config, p, *, info, tick, symbol: str) -> bool:
    """Restore floor-parked SL, or tighten a 5% band back to the $0.50 money stop.

    Does not widen a legal money-stop out to ±5% (that made BTC TP unreachable).
    """
    from trading.domain.rotation_stops import clamp_symmetric_stops, is_floor_parked_sl, rotation_stop_distance

    ticket = int(getattr(p, "ticket", 0) or 0)
    entry = float(getattr(p, "price_open", 0.0) or 0.0)
    sl_now = float(getattr(p, "sl", 0.0) or 0.0)
    lot = float(getattr(p, "volume", 0.0) or 0.0)
    side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
    if ticket <= 0 or entry <= 0.0 or lot <= 0.0:
        return False
    min_dist = _min_stop_distance_price(info, tick)
    intended = rotation_stop_distance(
        price=entry,
        lot=lot,
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        max_risk_usd=float(getattr(cfg, "max_risk_usd", 0.50) or 0.50),
        min_dist=0.0,
    )
    if intended is None:
        logging.info("[%s] Rotation SL repair skipped: no money/%% distance (ticket=%s).", symbol, ticket)
        return False
    parked = is_floor_parked_sl(side=side, entry=entry, sl=sl_now if sl_now > 0.0 else None)
    current = abs(entry - sl_now) if sl_now > 0.0 else 0.0
    already = sl_now > 0.0 and intended > 0.0 and abs(current - intended) / intended <= 0.12
    if already and not parked:
        return False
    if side == "BUY":
        intended_sl, intended_tp = entry - intended, entry + intended
    else:
        intended_sl, intended_tp = entry + intended, entry - intended
    clamped = clamp_symmetric_stops(
        side=side,
        entry=entry,
        sl=intended_sl,
        tp=intended_tp,
        min_dist=min_dist,
        point=float(getattr(info, "point", 0.0) or 0.0),
        digits=int(info.digits),
    )
    if clamped is None:
        logging.info("[%s] Rotation SL repair skipped: no legal stop (ticket=%s).", symbol, ticket)
        return False
    new_sl, new_tp = clamped
    if not _sl_is_valid_now(side=side, sl=float(new_sl), info=info, tick=tick):
        logging.info(
            "[%s] Rotation SL repair skipped: SL invalid vs market (ticket=%s sl=%s).",
            symbol,
            ticket,
            new_sl,
        )
        return False
    ok = _modify_position_sl_tp(symbol, ticket, float(new_sl), float(new_tp), side=side, info=info, tick=tick)
    pct = 100.0 * intended / entry if entry > 0 else 0.0
    logging.info(
        "[%s] Rotation SL repair ticket=%s %s -> SL=%s TP=%s (%.2f%% / $0.50 cap) ok=%s",
        symbol,
        ticket,
        "applied" if ok else "failed",
        new_sl,
        new_tp,
        pct,
        ok,
    )
    return bool(ok)


def manage_positions(cfg: Config) -> None:
    """Book harvest, then per position: 5-min stale close or stop repair."""
    positions = mt5.positions_get()
    if positions is None:
        return

    open_managed: set[int] = set()
    for _p0 in positions:
        _tk = int(getattr(_p0, "ticket", 0) or 0)
        if _tk <= 0:
            continue
        if _position_is_managed_by_bot(_p0, cfg):
            open_managed.add(_tk)
    for _tk in list(_ROTATION_FIRST_SEEN.keys()):
        if _tk not in open_managed:
            del _ROTATION_FIRST_SEEN[_tk]

    harvested = _flatten_rotation_book_if_needed(cfg, positions)

    for p in positions:
        ticket = int(getattr(p, "ticket", 0) or 0)
        if ticket in harvested:
            continue
        if ticket_is_manually_excepted(ticket):
            continue
        if not _position_is_managed_by_bot(p, cfg):
            continue

        symbol = getattr(p, "symbol", None)
        if not symbol:
            continue

        profit = float(getattr(p, "profit", 0.0) or 0.0)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            continue

        side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
        lot = float(getattr(p, "volume", cfg.lot) or cfg.lot)

        if _close_stale_rotation_if_needed(
            cfg, p, symbol=str(symbol), ticket=ticket, lot=lot, side=side, profit=profit
        ):
            continue
        _repair_rotation_floor_stops(cfg, p, info=info, tick=tick, symbol=str(symbol))

def _count_broker_entry_ins(cfg: Config, now: datetime, window_seconds: float = 3600.0) -> int:
    """ENTRY_IN deals with our magic in the rolling window. Survives bot restart."""
    start = now - timedelta(seconds=float(window_seconds))
    end = now + timedelta(hours=2)
    deals = mt5.history_deals_get(start, end) or []
    entry_in = int(getattr(mt5, "DEAL_ENTRY_IN", 0) or 0)
    mag = int(cfg.magic)
    n = 0
    for d in deals:
        try:
            if int(getattr(d, "magic", 0) or 0) != mag:
                continue
            if _deal_entry(d) != entry_in:
                continue
        except (TypeError, ValueError, AttributeError):
            continue
        n += 1
    return n


def _open_symbols_for_magic(cfg: Config) -> list[str]:
    positions = mt5.positions_get()
    if positions is None:
        return []
    mag = int(cfg.magic)
    out: list[str] = []
    for pos in positions:
        try:
            if int(getattr(pos, "magic", 0) or 0) != mag:
                continue
            out.append(str(pos.symbol))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _usd_at_min_lot_min_stop(symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0
    vmin = float(getattr(info, "volume_min", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    point = float(getattr(info, "point", 0.0) or 0.0)
    if vmin <= 0.0 or tick_size <= 0.0 or tick_value <= 0.0:
        return 0.0
    stops = int(getattr(info, "trade_stops_level", 0) or 0)
    min_dist = max(point, tick_size, float(stops) * point if point > 0 else 0.0)
    if min_dist <= 0.0:
        min_dist = tick_size
    return abs(min_dist / tick_size * tick_value * vmin)


def _rotation_ohlcv(symbol: str, timeframe, count: int):
    from trading.domain.ensemble import Ohlcv

    n = max(8, int(count))
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, n)
    ordered = mt5_rates_oldest_to_newest(rates)
    if not ordered:
        return None
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    last_t = None
    for r in ordered:
        try:
            opens.append(float(r["open"]))
            highs.append(float(r["high"]))
            lows.append(float(r["low"]))
            closes.append(float(r["close"]))
            vols.append(float(r["tick_volume"]) if "tick_volume" in (getattr(getattr(r, "dtype", None), "names", None) or ()) else 0.0)
            last_t = int(r["time"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if len(closes) < 8:
        return None
    return Ohlcv.from_lists(opens, highs, lows, closes, vols), last_t


def _rotation_fetch_bars(symbol: str):
    m1 = _rotation_ohlcv(symbol, mt5.TIMEFRAME_M1, 80)
    m5 = _rotation_ohlcv(symbol, mt5.TIMEFRAME_M5, 40)
    m15 = _rotation_ohlcv(symbol, mt5.TIMEFRAME_M15, 192)
    if m1 is None or m5 is None or m15 is None:
        return None
    ohlcv_m1, t1 = m1
    ohlcv_m5, _t5 = m5
    ohlcv_m15, _t15 = m15
    if len(ohlcv_m15.closes) < 50:
        return None
    h4 = _rotation_ohlcv(symbol, mt5.TIMEFRAME_H4, 72)
    ohlcv_h4 = h4[0] if h4 is not None else None
    return ohlcv_m1, ohlcv_m5, ohlcv_m15, t1, ohlcv_h4


def _rotation_fetch_contract(symbol: str):
    from trading.domain.ensemble import ContractSpec

    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
    vstep = float(getattr(info, "volume_step", vmin) or vmin)
    vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
    tick = mt5.symbol_info_tick(symbol)
    px = 0.0
    tick_msc = 0
    if tick is not None:
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        px = (bid + ask) / 2.0 if bid > 0.0 and ask > 0.0 else (ask or bid)
        try:
            tick_msc = int(getattr(tick, "time_msc", 0) or 0)
        except (TypeError, ValueError):
            tick_msc = 0
    return ContractSpec(
        volume_min=vmin,
        volume_step=vstep,
        volume_max=vmax,
        usd_at_min_lot_min_stop=_usd_at_min_lot_min_stop(symbol),
        price=px,
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        bid=bid if tick is not None else 0.0,
        ask=ask if tick is not None else 0.0,
        tick_time_msc=tick_msc,
    )


def _rotation_overlay_tape(cycle):
    """Redis tape on 4/5 states only. Fail-open: any error returns the cycle unchanged."""
    if not _env_bool("BOT_ROTATION_TAPE_PEEK", True):
        return cycle
    try:
        from trading.application.rotation_engine import overlay_cycle_tape, tape_symbols
        from trading.infrastructure.tick_peek import peek_ticks_many

        syms = tape_symbols(cycle)
        if not syms:
            return cycle
        return overlay_cycle_tape(cycle, peek_ticks_many(syms))
    except Exception:
        logging.debug("rotation tape peek failed (non-fatal)", exc_info=True)
        return cycle


def _mt5_demo_flags() -> tuple[bool, bool, str, int]:
    """XBTFX + MT5 demo trade_mode of the attached terminal. No orders."""
    from trading.domain.demo_gate import account_is_mt5_demo
    from trading.domain.xbtfx import account_is_xbtfx
    from trading.infrastructure.xbtfx_session import server_markers

    acc = mt5.account_info()
    if acc is None:
        return False, False, "", -1
    server = str(getattr(acc, "server", "") or "")
    company = str(getattr(acc, "company", "") or "")
    try:
        trade_mode = int(getattr(acc, "trade_mode", -1))
    except (TypeError, ValueError):
        trade_mode = -1
    return (
        account_is_xbtfx(server, company, server_markers()),
        account_is_mt5_demo(trade_mode, server),
        server,
        trade_mode,
    )


def _rotation_execution_decision(cfg: Config) -> tuple[Any, dict]:
    """Demo XBTFX orders: account gates only. Votes/size live in the rotation cycle."""
    from trading.domain.demo_gate import resolve_demo_execution

    is_xbtfx, is_demo, server, trade_mode = _mt5_demo_flags()
    dry_env = bool(getattr(cfg, "dry_run", True))
    paper = bool(getattr(cfg, "paper_demo_live", False))
    exec_ = resolve_demo_execution(
        dry_run_env=dry_env,
        paper_demo_live=paper,
        is_xbtfx=is_xbtfx,
        is_demo=is_demo,
    )
    meta = {
        "server": server,
        "trade_mode": trade_mode,
        "is_xbtfx": is_xbtfx,
        "is_demo": is_demo,
        "validation": {},
        "validation_reason": "",
    }
    return exec_, meta


def _run_rotation_cycle(cfg: Config, state: dict, now: datetime) -> None:
    from trading.application.rotation_engine import RotationSettings, run_rotation_cycle
    from trading.infrastructure.persistence.ensemble_store import insert_ensemble_votes

    exec_, meta = _rotation_execution_decision(cfg)
    effective_dry = bool(exec_.dry_run)
    for s in cfg.symbols:
        _ensure_symbol(s)
    settings = RotationSettings(
        symbols=tuple(cfg.symbols),
        min_agree=int(getattr(cfg, "ensemble_min_agree", 4) or 4),
        max_risk_usd=float(getattr(cfg, "max_risk_usd", 0.50) or 0.50),
        hour_cap=int(getattr(cfg, "max_opens_per_hour", 60) or 60),
        dry_run=effective_dry,
    )
    cycle = run_rotation_cycle(
        settings=settings,
        fetch_bars=_rotation_fetch_bars,
        fetch_contract=_rotation_fetch_contract,
        open_symbols=_open_symbols_for_magic(cfg),
        opens_this_hour=_count_broker_entry_ins(cfg, now, 3600.0),
        now=now,
    )
    cycle = _rotation_overlay_tape(cycle)
    summary = dict(cycle.summary)
    summary["dry_run"] = effective_dry
    summary["send_orders"] = bool(exec_.send_orders)
    summary["execution_mode"] = exec_.mode
    summary["execution_reason"] = exec_.reason
    summary["account_demo"] = bool(meta.get("is_demo"))
    summary["account_xbtfx"] = bool(meta.get("is_xbtfx"))
    summary["validation"] = meta.get("validation") or {}
    state["ensemble"] = summary
    state["mode"] = exec_.mode
    rows = []
    from trading.application.rotation_audit import ensemble_vote_payload, open_audit_from_snap

    scanned_syms = set()
    for snap in cycle.snapshots:
        payload = snap.as_dict()
        payload["ts"] = cycle.ts
        rows.append(payload)
        scanned_syms.add(str(snap.symbol).strip().upper())
        _log_event(ensemble_vote_payload(payload, ts=cycle.ts, execution_mode=exec_.mode))
    for raw_sym in cfg.symbols:
        key = str(raw_sym).strip().upper()
        if key in scanned_syms:
            continue
        _log_event(
            {
                "event": "ENSEMBLE_VOTE",
                "ts": cycle.ts,
                "engine": "rotation",
                "symbol": raw_sym,
                "direction": "NONE",
                "action": "SKIP",
                "skip_reason": "sin_barras",
                "why_open": "sin_barras",
                "execution_mode": exec_.mode,
                "votes": [],
            }
        )
    _log_event(
        {
            "event": "ENSEMBLE_CYCLE",
            "ts": cycle.ts,
            "engine": "rotation",
            "scanned": summary.get("scanned"),
            "buy_consensus": summary.get("buy_consensus"),
            "sell_consensus": summary.get("sell_consensus"),
            "no_majority": summary.get("no_majority"),
            "fired_count": summary.get("fired_count"),
            "near_miss_count": summary.get("near_miss_count"),
            "dry_run": effective_dry,
            "send_orders": bool(exec_.send_orders),
            "execution_mode": exec_.mode,
            "execution_reason": exec_.reason,
            "opens_this_hour": summary.get("opens_this_hour"),
            "hour_cap": summary.get("hour_cap"),
        }
    )
    try:
        insert_ensemble_votes(rows, dry_run=effective_dry)
    except Exception:
        logging.exception("ensemble_votes persist failed (non-fatal)")

    for snap in cycle.intended:
        side_decision = snap.consensus.direction
        if side_decision not in ("BUY", "SELL"):
            continue
        side = side_decision
        m1_t = snap.m1_time
        if m1_t is not None and _ROTATION_LAST_M1.get(snap.symbol) == m1_t:
            continue
        _log_event(
            {
                "event": "ENSEMBLE_SIGNAL",
                "ts": cycle.ts,
                "symbol": snap.symbol,
                "side": side_decision,
                "side_decision": side_decision,
                "side_exec": side,
                "risk_usd": snap.size.risk_usd,
                "lot": snap.size.lot,
                "agree": snap.consensus.agree,
                "execution_mode": exec_.mode,
                "action": snap.action,
                "murphy": snap.murphy,
                "alpha": snap.alpha,
                "state": snap.state,
            }
        )
        if snap.action == "DRY_RUN" or not exec_.send_orders:
            if m1_t is not None:
                _ROTATION_LAST_M1[snap.symbol] = int(m1_t)
            _log_event(
                {
                    "event": "ENSEMBLE_ORDER_SKIPPED",
                    "ts": cycle.ts,
                    "symbol": snap.symbol,
                    "side": side_decision,
                    "side_decision": side_decision,
                    "side_exec": side,
                    "risk_usd": snap.size.risk_usd,
                    "lot": snap.size.lot,
                    "agree": snap.consensus.agree,
                    "reason": exec_.reason if not exec_.send_orders else "DRY_RUN",
                    "execution_mode": exec_.mode,
                }
            )
            continue
        _ROTATION_OVERRIDE[str(snap.symbol).strip().upper()] = {
            "lot": float(snap.size.lot),
            "sl_money": -abs(float(snap.size.risk_usd)),
            "tp_money": abs(float(snap.size.risk_usd)),
            "max_trades_per_hour_per_symbol": 0,
        }
        try:
            audit = open_audit_from_snap(snap.as_dict(), execution_mode=exec_.mode)
            audit["side_decision"] = side_decision
            audit["side_exec"] = side
            audit["side_inverted"] = False
            ok = open_trade(cfg, snap.symbol, side, audit=audit)
            skip_why = _LAST_ROTATION_OPEN_SKIP.get(str(snap.symbol).strip().upper(), "open_fail")
            if ok:
                if m1_t is not None:
                    _ROTATION_LAST_M1[snap.symbol] = int(m1_t)
            else:
                _patch_ensemble_skip(state, snap.symbol, skip_why)
            _log_event(
                {
                    "event": "ENSEMBLE_DEMO_ORDER",
                    "ts": cycle.ts,
                    "engine": "rotation",
                    "symbol": snap.symbol,
                    "side": side,
                    "side_decision": side_decision,
                    "side_exec": side,
                    "side_inverted": False,
                    "ok": bool(ok),
                    "why_open": "consenso_4_5",
                    "skip_reason": "" if ok else skip_why,
                    "risk_usd": snap.size.risk_usd,
                    "lot": snap.size.lot,
                    "agree": snap.consensus.agree,
                    "buy": snap.consensus.buy,
                    "sell": snap.consensus.sell,
                    "votes": [f"{v.name}:{v.side}" for v in snap.votes],
                    "venue": "XBTFX_DEMO",
                    "execution_mode": exec_.mode,
                }
            )
        finally:
            _ROTATION_OVERRIDE.pop(str(snap.symbol).strip().upper(), None)


def _load_dotenv() -> None:
    """Load variables from `.env` in the current working directory (does not override existing env)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _sleep_until_next_poll(cfg: Config, loop_started_monotonic: float) -> None:
    """
    Espera hasta completar `poll_seconds` desde loop_started_monotonic. Si
    `manage_positions_interval_seconds` es menor que el tiempo restante, llama
    a `manage_positions` entre trozos de sueño para reaccionar antes al SL/TP.
    """
    poll = max(1.0, float(cfg.poll_seconds))
    manage_iv = max(1.0, float(cfg.manage_positions_interval_seconds))
    elapsed = time.monotonic() - loop_started_monotonic
    remaining = max(0.0, poll - elapsed)
    if remaining <= 0:
        return
    if manage_iv >= remaining:
        time.sleep(remaining)
        return
    while remaining > 1e-6:
        sleep_chunk = min(manage_iv, remaining)
        time.sleep(sleep_chunk)
        remaining -= sleep_chunk
        if remaining > 1e-6:
            manage_positions(cfg)


def main() -> int:
    _load_dotenv()
    rotate_err = _rotate_trades_log_on_start()
    _setup_logging()
    if rotate_err:
        logging.warning("trades.log not rotated on start (appending to it): %s", rotate_err)
    # Universe is CRYPTO_UNIVERSE unless BOT_ROTATION_KEEP_ENV_SYMBOLS=1 (then BOT_SYMBOLS / Config.symbols).
    _raw_symbols = _env("BOT_SYMBOLS")
    dry_run = _env_bool("BOT_DRY_RUN", True)
    if _raw_symbols is None:
        symbols_effective: Tuple[str, ...] = Config.symbols
    else:
        symbols_effective = tuple(p.strip() for p in _raw_symbols.split(",") if p.strip())
    if not _env_bool("BOT_ROTATION_KEEP_ENV_SYMBOLS", False):
        from trading.domain.crypto_universe import CRYPTO_UNIVERSE

        symbols_effective = CRYPTO_UNIVERSE
    cfg = Config(
        symbols=symbols_effective,
        lot=float(_env("BOT_LOT", "0.01")),
        symbol_config=_parse_symbol_config(_env("BOT_SYMBOL_CONFIG")),
        max_spread_points=float(_env("BOT_MAX_SPREAD_POINTS", "25")),
        spread_dynamic_enabled=_env_bool("BOT_SPREAD_DYNAMIC_ENABLED", False),
        spread_dynamic_alpha=float(_env("BOT_SPREAD_DYNAMIC_ALPHA", "0.15")),
        spread_dynamic_mult=float(_env("BOT_SPREAD_DYNAMIC_MULT", "1.5")),
        poll_seconds=int(float(_env("BOT_POLL_SECONDS", "15"))),
        manage_positions_interval_seconds=float(_env("BOT_MANAGE_POSITIONS_INTERVAL_SECONDS", "5")),
        max_open_trades=int(float(_env("BOT_MAX_OPEN_TRADES", "60"))),
        max_positions_per_symbol=int(float(_env("BOT_MAX_POSITIONS_PER_SYMBOL", "1"))),
        pyramiding_require_be=_env_bool("BOT_PYRAMIDING_REQUIRE_BE", True),
        pyramiding_min_separation_points=float(_env("BOT_PYRAMIDING_MIN_SEPARATION_POINTS", "0")),
        daily_loss_limit=float(_env("BOT_DAILY_LOSS_LIMIT", "-60.0")),
        monthly_loss_limit_enabled=_env_bool("BOT_MONTHLY_LOSS_LIMIT_ENABLED", False),
        monthly_loss_limit_pct=float(_env("BOT_MONTHLY_LOSS_LIMIT_PCT", "6.0")),
        monthly_loss_limit_include_open_risk=_env_bool("BOT_MONTHLY_LOSS_LIMIT_INCLUDE_OPEN_RISK", True),
        sl_money=float(_env("BOT_SL_MONEY", "-0.50")),
        tp_money=float(_env("BOT_TP_MONEY", "0.50")),
        failed_attempt_cooldown_seconds=float(_env("BOT_FAILED_ATTEMPT_COOLDOWN_SECONDS", "120")),
        max_trades_per_hour_per_symbol=int(float(_env("BOT_MAX_TRADES_PER_HOUR_PER_SYMBOL", "0"))),
        max_trades_per_hour_crypto_default=int(float(_env("BOT_MAX_TRADES_PER_HOUR_CRYPTO_DEFAULT", "8"))),
        dry_run=dry_run,
        paper_demo_live=_env_bool("PAPER_DEMO_LIVE", False),
        ensemble_min_agree=int(float(_env("BOT_ENSEMBLE_MIN_AGREE", "4"))),
        max_risk_usd=float(_env("BOT_MAX_RISK_USD", "0.50")),
        max_opens_per_hour=int(float(_env("BOT_MAX_OPENS_PER_HOUR", "60"))),
        rotation_stale_seconds=float(_env("BOT_ROTATION_STALE_SECONDS", "300")),
        rotation_stale_tiny_loss_usd=float(_env("BOT_ROTATION_STALE_TINY_LOSS_USD", "-0.0001")),
        rotation_stale_take_usd=float(_env("BOT_ROTATION_STALE_TAKE_USD", "0.01")),
        rotation_stale_close_all=_env_bool("BOT_ROTATION_STALE_CLOSE_ALL", True),
        rotation_max_spread_usd_frac=float(_env("BOT_ROTATION_MAX_SPREAD_USD_FRAC", "0.10")),
        rotation_book_snapshot_seconds=float(_env("BOT_ROTATION_BOOK_SNAPSHOT_SECONDS", "60")),
        rotation_book_harvest_usd=float(_env("BOT_ROTATION_BOOK_HARVEST_USD", "0.50")),
        rotation_book_harvest_min_age_seconds=float(
            _env("BOT_ROTATION_BOOK_HARVEST_MIN_AGE_SECONDS", "600")
        ),
        rotation_book_harvest_min_open=int(float(_env("BOT_ROTATION_BOOK_HARVEST_MIN_OPEN", "2"))),
        kill_switch_flatten=_env_bool("BOT_KILL_SWITCH_FLATTEN", False),
        kill_switch_armed=_env_bool("BOT_KILL_SWITCH_ARMED", True),
        start_hour=int(float(_env("BOT_START_HOUR", str(Config.start_hour)))),
        end_hour=int(float(_env("BOT_END_HOUR", str(Config.end_hour)))),
        no_new_trades_after_hour=int(float(_env("BOT_NO_NEW_TRADES_AFTER_HOUR", str(Config.no_new_trades_after_hour)))),
        no_new_trades_after_minute=int(float(_env("BOT_NO_NEW_TRADES_AFTER_MINUTE", str(Config.no_new_trades_after_minute)))),
        trading_window_enabled=False,
        trading_window_sessions=_load_trading_window_sessions_from_env(),
        trading_window_timezone=str(_env("BOT_TRADING_WINDOW_TIMEZONE", "LOCAL") or "LOCAL"),
        weekend_block_enabled=_env_bool("BOT_WEEKEND_BLOCK_ENABLED", True),
        friday_cutoff_enabled=False,
        new_trade_cutoff_enabled=False,
        lot_max_margin_frac=float(_env("BOT_LOT_MAX_MARGIN_FRAC", "0.5")),
        news_block_hours=_env_int_list("BOT_NEWS_BLOCK_HOURS"),
        quiet_session_block_enabled=False,
        quiet_session_hours=_env_int_list("BOT_QUIET_SESSION_HOURS") or (23, 0, 1, 2, 3),
        quiet_session_exempt_symbols=tuple(_env_str_list("BOT_QUIET_SESSION_EXEMPT_SYMBOLS") or ("BTCUSD", "ETHUSD")),
        currency_exposure_enabled=_env_bool("BOT_CURRENCY_EXPOSURE_ENABLED", False),
        max_net_exposure_per_currency=float(_env("BOT_MAX_NET_EXPOSURE_PER_CURRENCY", "0")),
        max_gross_exposure_per_currency=float(_env("BOT_MAX_GROSS_EXPOSURE_PER_CURRENCY", "0")),
        currency_symbol_overrides=parse_currency_overrides(_env("BOT_CURRENCY_SYMBOL_OVERRIDES")),
        portfolio_enabled=_env_bool("BOT_PORTFOLIO_ENABLED", False),
        portfolio_weights=_parse_portfolio_weights(
            _env("BOT_PORTFOLIO_WEIGHTS"),
            symbols_effective or Config.symbols,
            renormalize=_env_bool("BOT_PORTFOLIO_RENORMALIZE", True),
        ),
        portfolio_dynamic_enabled=_env_bool("BOT_PORTFOLIO_DYNAMIC_ENABLED", False),
        portfolio_dynamic_lookback_days=int(float(_env("BOT_PORTFOLIO_DYNAMIC_LOOKBACK_DAYS", "14"))),
        portfolio_dynamic_rebalance_minutes=int(float(_env("BOT_PORTFOLIO_DYNAMIC_REBALANCE_MINUTES", "60"))),
        portfolio_dynamic_smoothing_alpha=float(_env("BOT_PORTFOLIO_DYNAMIC_SMOOTH_ALPHA", "0.35")),
        portfolio_dynamic_min_weight=float(_env("BOT_PORTFOLIO_DYNAMIC_MIN_WEIGHT", "0.05")),
        portfolio_dynamic_max_weight=float(_env("BOT_PORTFOLIO_DYNAMIC_MAX_WEIGHT", "0.60")),
        portfolio_dynamic_use_positive_only=_env_bool("BOT_PORTFOLIO_DYNAMIC_POSITIVE_ONLY", True),
        weekend_trade_symbols=tuple(symbols_effective),
        track_zero_magic_positions=_env_bool("BOT_TRACK_ZERO_MAGIC_POSITIONS", True),
        cancel_orphan_pending_on_start=_env_bool("BOT_CANCEL_ORPHAN_PENDING_ON_START", False),
        btcusd_max_spread_points=float(_env("BOT_BTCUSD_MAX_SPREAD_POINTS", "1000")),
    )

    pid = os.getpid()
    if cfg.trading_window_enabled and cfg.trading_window_sessions:
        rng = "; ".join(f"{a.strftime('%H:%M')}-{b.strftime('%H:%M')}" for a, b in cfg.trading_window_sessions)
        logging.info(
            "Trading window: TZ=%s | sessions=[%s] (outside ⇒ no new entries)",
            cfg.trading_window_timezone,
            rng,
        )
    logging.info("BTCUSD spread cap=%.0f pts (0=off).", float(cfg.btcusd_max_spread_points))
    logging.info(
        "Starting bot | PID=%s | Symbols=%s | poll=%ss | manage_interval=%ss | lot=%.2f | max_spread=%.1f pts",
        pid,
        cfg.symbols,
        cfg.poll_seconds,
        cfg.manage_positions_interval_seconds,
        cfg.lot,
        cfg.max_spread_points,
    )
    logging.info(
        "Validación: este proceso es PID=%s. Tras el primer ciclo, bot_state.json tendrá el mismo pid. "
        "PowerShell: Get-Process -Id %s",
        pid,
        pid,
    )
    _log_event(
        {
            "event": "BOT_START",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "pid": pid,
            "symbols": list(cfg.symbols),
            "poll_seconds": cfg.poll_seconds,
            "manage_positions_interval_seconds": float(cfg.manage_positions_interval_seconds),
            "weekend_block_enabled": bool(cfg.weekend_block_enabled),
            "weekend_trade_symbols": list(cfg.weekend_trade_symbols),
        }
    )

    if not connect_mt5(cfg):
        _save_stopped_state("mt5_connect_failed")
        return 2

    # Ensure symbols are ready
    for s in cfg.symbols:
        _ensure_symbol(s)

    recon = _startup_reconciliation(cfg)
    _seed_close_log_tracker(cfg)

    # Primer snapshot para el dashboard (evita mostrar un PID viejo hasta el primer sleep del loop)
    reached0, pnl0 = daily_loss_limit_reached(cfg.daily_loss_limit)
    monthly_reached0, monthly_info0 = monthly_loss_limit_reached(cfg)
    _save_state(
        {
            "running": True,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "pid": pid,
            "poll_seconds": cfg.poll_seconds,
            "manage_positions_interval_seconds": float(cfg.manage_positions_interval_seconds),
            "symbols": list(cfg.symbols),
            "weekend_block_enabled": bool(cfg.weekend_block_enabled),
            "weekend_trade_symbols": list(cfg.weekend_trade_symbols),
            "mode": "CONNECTED",
            "pnl_today": pnl0,
            "daily_loss_limit": cfg.daily_loss_limit,
            "daily_loss_limit_reached": reached0,
            "monthly_loss_limit": monthly_info0,
            "monthly_loss_limit_reached": bool(monthly_reached0),
            "open_positions_total": total_open_positions(),
            "open_positions_managed": _count_managed_open_positions(cfg),
            "reconcile_at_start": recon,
            "last_trade_time": {k: v.isoformat(timespec="seconds") for k, v in _last_trade_time.items()},
            "last_failed_order_attempt": {
                k: v.isoformat(timespec="seconds") for k, v in _last_failed_order_attempt.items()
            },
        }
    )
    logging.info("bot_state.json actualizado con PID=%s (modo CONNECTED).", pid)

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_START", True):
        telegram_notify.notify(
            _telegram_format_startup_message(cfg, pid=pid, recon=recon),
            dedup_key=None,
        )

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_MANUAL_EXCEPTIONS", True):
        from trading.infrastructure.telegram_manual_exceptions import start_listener

        start_listener()

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_FORWARD_CHANNEL", True):
        from trading.infrastructure.channel_signal_forward import start_channel_forwarder

        start_channel_forwarder()

    try:
        while True:
            loop_t0 = time.monotonic()
            try:
                if not mt5.terminal_info():
                    logging.warning("MT5 terminal_info unavailable. Trying to reconnect...")
                    mt5.shutdown()
                    ok_re = connect_mt5(cfg)
                    if not ok_re or not mt5.terminal_info():
                        logging.warning("MT5 still disconnected after reconnect attempt.")
                        telegram_notify.notify_error(
                            f"MT5 desconectado tras reintento ({datetime.now().strftime('%H:%M:%S')}). "
                            "Solo gestiona cuando vuelva el link."
                        )

                # Always manage positions (trailing) even when not opening new ones
                manage_positions(cfg)
                _log_new_closes(cfg)
                _telegram_notify_new_closes(cfg)
                _maybe_log_rotation_book(cfg)
                _telegram_notify_session_windows(cfg)

                reached, pnl_today = daily_loss_limit_reached(cfg.daily_loss_limit)
                kill_switch = _kill_switch_step(cfg, reached=reached, pnl_today=pnl_today)
                monthly_reached, monthly_info = monthly_loss_limit_reached(cfg)
                _maybe_rebalance_portfolio_weights(cfg)
                state = {
                    "running": True,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "pid": pid,
                    "poll_seconds": cfg.poll_seconds,
                    "manage_positions_interval_seconds": float(cfg.manage_positions_interval_seconds),
                    "symbols": list(cfg.symbols),
                    "weekend_block_enabled": bool(cfg.weekend_block_enabled),
                    "weekend_trade_symbols": list(cfg.weekend_trade_symbols),
                    "pnl_today": pnl_today,
                    "daily_loss_limit": cfg.daily_loss_limit,
                    "daily_loss_limit_reached": reached,
                    "kill_switch": kill_switch,
                    "monthly_loss_limit": monthly_info,
                    "monthly_loss_limit_reached": bool(monthly_reached),
                    "open_positions_total": total_open_positions(),
                    "open_positions_managed": _count_managed_open_positions(cfg),
                    "rotation_enabled": True,
                    "rotation_max_spread_usd_frac": float(
                        getattr(cfg, "rotation_max_spread_usd_frac", 0.10) or 0.10
                    ),
                    "magic": int(cfg.magic),
                    "max_opens_per_hour": int(getattr(cfg, "max_opens_per_hour", 60) or 60),
                    "last_trade_time": {k: v.isoformat(timespec="seconds") for k, v in _last_trade_time.items()},
                    "last_failed_order_attempt": {
                        k: v.isoformat(timespec="seconds") for k, v in _last_failed_order_attempt.items()
                    },
                }
                if bool(cfg.portfolio_enabled):
                    state["portfolio"] = {
                        "enabled": True,
                        "dynamic_enabled": bool(cfg.portfolio_dynamic_enabled),
                        "weights": dict(_portfolio_dynamic_weights) if bool(cfg.portfolio_dynamic_enabled) else dict(cfg.portfolio_weights),
                        "last_rebalance_ts": _portfolio_last_rebalance.isoformat(timespec="seconds") if _portfolio_last_rebalance else None,
                    }

                now_loop = datetime.now()
                allowed_syms = [s for s in cfg.symbols if _session_allows_new_trade(cfg, s, now_loop)]
                if not allowed_syms:
                    logging.info(
                        "No symbols allowed for new entries (weekend/window/Friday cutoff). Managing positions only."
                    )
                    state["mode"] = "MANAGE_ONLY_OUT_OF_HOURS"
                else:
                    if _is_new_trade_cutoff(cfg):
                        logging.info(
                            "New-trade cutoff reached (%02d:%02d). Managing only, no new entries.",
                            cfg.no_new_trades_after_hour,
                            cfg.no_new_trades_after_minute,
                        )
                        state["mode"] = "MANAGE_ONLY_CUTOFF"
                    else:
                        if _is_news_time(cfg):
                            logging.info("News-time block active. No new entries.")
                            state["mode"] = "MANAGE_ONLY_NEWS"
                        else:
                            if kill_switch.get("halt_active"):
                                logging.warning(
                                    "Kill switch halt active (reason=%s since=%s). No new entries.",
                                    kill_switch.get("reason"),
                                    kill_switch.get("since"),
                                )
                                state["mode"] = "MANAGE_ONLY_KILL_SWITCH"
                            elif reached:
                                logging.warning(
                                    "Daily loss limit reached (PnL today=%.2f <= %.2f). No new entries.",
                                    pnl_today,
                                    cfg.daily_loss_limit,
                                )
                                state["mode"] = "MANAGE_ONLY_DAILY_LOSS_LIMIT"
                            elif monthly_reached:
                                logging.warning(
                                    "Monthly loss limit reached (at_risk=%.2f >= threshold=%.2f, %.1f%% of %.2f). No new entries.",
                                    float(monthly_info.get("total_at_risk") or 0.0),
                                    float(monthly_info.get("threshold_money") or 0.0),
                                    float(monthly_info.get("pct") or 0.0),
                                    float(monthly_info.get("month_start_balance") or 0.0),
                                )
                                global _MONTHLY_LOSS_LIMIT_LOGGED_MONTH
                                current_month_tag = datetime.now().strftime("%Y-%m")
                                if _MONTHLY_LOSS_LIMIT_LOGGED_MONTH != current_month_tag:
                                    _log_event(
                                        {
                                            "event": "MONTHLY_LOSS_LIMIT_BLOCK",
                                            "ts": datetime.now().isoformat(timespec="seconds"),
                                            "month": current_month_tag,
                                            **monthly_info,
                                        }
                                    )
                                    _MONTHLY_LOSS_LIMIT_LOGGED_MONTH = current_month_tag
                                state["mode"] = "MANAGE_ONLY_MONTHLY_LOSS_LIMIT"
                            else:
                                _run_rotation_cycle(cfg, state, now_loop)

                _save_state(state)

            except Exception:
                logging.exception("Unhandled loop exception.")

            _sleep_until_next_poll(cfg, loop_t0)
    except KeyboardInterrupt:
        logging.info("Stopping bot (Ctrl+C).")
        if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_STOP", True):
            try:
                telegram_notify.notify(
                    "meta-trader: detenido (Ctrl+C).",
                    dedup_key=None,
                    timeout=3.0,
                )
            except KeyboardInterrupt:
                logging.debug("Shutdown Telegram notify interrupted; continuing cleanup.")
        _save_stopped_state("keyboard_interrupt")
    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

