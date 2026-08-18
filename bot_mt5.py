import os
import sys
import time
import math
import logging
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Optional, Tuple, Literal, Any

import MetaTrader5 as mt5
from indicators import adx_wilder
from strategy_core import (
    decide,
    Features,
    StrategyParams,
    price_slope_m15,
    rsi_from_closes,
    symbol_uses_crypto_relaxed_m15,
    trend_h4_from_closes,
)

import telegram_notify


Signal = Literal["BUY", "SELL", "NONE"]
Trend = Literal["UP", "DOWN", "FLAT"]

_last_trade_time: dict[str, datetime] = {}
# Tras rechazo de order_send (broker): no reintentar abrir hasta pasado failed_attempt_cooldown_seconds.
_last_failed_order_attempt: dict[str, datetime] = {}
# Ventana deslizante de aperturas por símbolo (rate-limit / hora reales).
_symbol_open_timestamps: dict[str, list[datetime]] = {}
# PnL máximo visto (flotante) por ticket, para el escalón USD de profit lock (persiste mientras dura la posición).
_PROFIT_LOCK_PEAK_PROFIT: dict[int, float] = {}
# Escalera USD (BE + locks +1..+N) con tiempos entre pasos; se limpia al cerrar la posición.
_USD_SL_LADDER_STATE: dict[int, dict[str, Any]] = {}

# Última etiqueta de régimen conocida por símbolo (market_regime.check_all); usada para multiplicador de lote.
_LAST_REGIME_BY_SYMBOL: dict[str, str] = {}
# Parches acumulativos sobre BOT_SYMBOL_CONFIG (.env): se fusionan al arrancar y se actualizan con régimen automático.
SYMBOL_CONFIG_OVERLAY_FILE = Path("symbol_config_overlay.json")
REGIME_APPLY_AUDIT_JSONL = Path("regime_apply_audit.jsonl")

# Deals de cierre (DEAL_ENTRY_OUT) ya notificados por Telegram (evita duplicados).
_TELEGRAM_SEEN_OUT_DEAL_TICKETS: set[int] = set()
_TELEGRAM_CLOSE_TRACKER_SEEDED: bool = False

_CLOSE_LOG_SEEN_DEAL_TICKETS: set[int] = set()
_CLOSE_LOG_TRACKER_SEEDED: bool = False
_CLOSE_LOG_BACKFILL_DONE: bool = False
# How far back to look at startup when backfilling missing TRADE_CLOSE events.
# Covers bot restarts / code upgrades mid-session without flooding the log.
_CLOSE_LOG_BACKFILL_DAYS: int = 7
_spread_ema_points: dict[str, float] = {}
_portfolio_dynamic_weights: dict[str, float] = {}
_portfolio_last_rebalance: Optional[datetime] = None
_TRADES_LOG_FILENAME = "trades.log"

# Breakdown horario XAUUSD paralelo (sesión local del bot; se resetea cada día).
# Clave = hora 0..23; valores: signals / opens / closes / pnl / spread_sum / spread_n.
_XAUUSD_HOURLY_DAY: str = ""
_XAUUSD_HOURLY: dict[int, dict[str, Any]] = {}
# Rate-limit de eventos SKIP (por reason) para no saturar trades.log cada poll.
_XAUUSD_LAST_SKIP_LOG: dict[str, datetime] = {}
_XAUUSD_SKIP_LOG_COOLDOWN_SEC: float = 300.0
_XAUUSD_ADX_MIN: float = 25.0
_XAUUSD_EMA_FAST: int = 9
_XAUUSD_EMA_SLOW: int = 21
_XAUUSD_ADX_PERIOD: int = 14
_XAUUSD_H1_EMA_PERIOD: int = 20
_XAUUSD_LAST_OPEN_AT: Optional[datetime] = None
_XAUUSD_LAST_ENTRY_BAR_TIME: Optional[int] = None
# Barra M15 cerrada cuyo cruce EMA ya se operó (evita re-disparar el mismo edge cada poll).
_XAUUSD_LAST_CROSS_BAR_TIME: Optional[int] = None
_XAUUSD_LAST_SIGNAL: Optional[dict[str, Any]] = None
_XAUUSD_LAST_SKIP: Optional[dict[str, Any]] = None
_XAUUSD_SKIP_REASONS: dict[str, int] = {}
_XAUUSD_CLOSE_TICKETS_SEEN: set[int] = set()
_XAUUSD_BROKER_CLOSE_DEALS_SEEN: set[int] = set()

# Rate-limit EVAL_SKIP / EVAL_CYCLE por símbolo+reason (evaluación multi-track).
_EVAL_LAST_LOG: dict[str, datetime] = {}
_EVAL_SKIP_COOLDOWN_SEC: float = 120.0
_EVAL_CYCLE_COOLDOWN_SEC: float = 60.0
# Último snapshot en memoria para bot_state / dashboard (aunque el log esté rate-limited).
_EVAL_LAST_CYCLE_BY_SYMBOL: dict[str, dict[str, Any]] = {}
_EVAL_LAST_SKIP_BY_SYMBOL: dict[str, dict[str, Any]] = {}
_EVAL_SKIP_COUNTS: dict[str, dict[str, int]] = {}


def eval_log_should_emit(
    last_by_key: dict[str, datetime],
    key: str,
    now: datetime,
    cooldown_sec: float,
    *,
    force: bool = False,
) -> bool:
    """Pure helper: True si corresponde emitir un log rate-limited."""
    if force:
        return True
    last = last_by_key.get(key)
    if last is None:
        return True
    try:
        return (now - last).total_seconds() >= float(cooldown_sec)
    except Exception:
        return True


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
    # Optional deterministic targets in points (if set, overrides money-based SL/TP for that symbol)
    sl_points: float = 80.0
    tp_points: float = 160.0

    trail_trigger_money: float = 1.00
    trail_new_sl_money: float = 0.50
    trail_new_tp_money: float = 1.20
    break_even_trigger_money: float = 0.50
    break_even_offset_points: float = 0.0  # optional extra points beyond entry (0 = pure BE)

    max_spread_points: float = 25.0
    spread_dynamic_enabled: bool = False
    spread_dynamic_alpha: float = 0.15  # EMA smoothing for avg spread
    spread_dynamic_mult: float = 1.5  # current spread must be <= mult * avg spread
    magic: int = 260414
    deviation_points: int = 20

    # Global risk controls
    max_open_trades: int = 5
    max_open_trades_total: int = 5
    daily_loss_limit: float = -60.0  # stop opening new trades if daily PnL <= this (account currency)
    use_risk_sizing: bool = True
    risk_per_trade_pct: float = 10.0  # % of balance (portfolio mode scales by symbol weight)
    cooldown_minutes: int = 30
    # Piso de minutos entre aperturas en símbolos crypto (relajado M15). Evita martillar entradas si el JSON puso cooldown bajo.
    cooldown_minutes_crypto_floor: int = 6
    # Máximo de órdenes de apertura por símbolo por hora (0 = sin límite global). Crypto usa `max_trades_per_hour_crypto_default` si esto es 0.
    max_trades_per_hour_per_symbol: int = 0
    max_trades_per_hour_crypto_default: int = 8
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

    # Portfolio mode: distribute total risk across symbols by weights
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

    # Filters (robustness)
    ema_period_m15: int = 50  # kept for backward compatibility; used as "fast EMA" when EMA pair enabled
    ema_fast_m15: int = 50
    ema_slow_m15: int = 200
    min_avg_range_points_m15: float = 0.0  # 0 disables volatility filter
    adx_period_m15: int = 14
    adx_min: float = 20.0  # below => lateral, no trade

    # Crypto (relaxed M15): ventas solo si H4 cerrado está en DOWN (reduce shorts en chop alcista).
    crypto_sell_requires_h4_down: bool = True

    # Pendiente de regresión sobre N cierres M15: confirma dirección antes de BUY/SELL (modo allow_flat_h4).
    price_slope_filter_enabled: bool = False
    price_slope_n: int = 6

    # Tras risk sizing: multiplicar lote por etiqueta de régimen (TRENDING/RANGING/UNCERTAIN). Requiere detector activo.
    regime_lot_sizing_enabled: bool = False
    regime_lot_multiplier_by_label: dict[str, float] = field(default_factory=dict)

    # Tope duro de volumen por operación (0 = off). Por símbolo: clave max_lot_per_trade en BOT_SYMBOL_CONFIG / overlay.
    max_lot_per_trade: float = 0.0

    # --- Sideways / range filters (entry quality) ---
    # Range filter: detect recent range on a higher timeframe and avoid trading in the "middle" of that range.
    # This reduces chop losses in lateral markets (common in crypto CFDs).
    range_filter_enabled: bool = False
    range_filter_timeframe: str = "H1"  # H1 recommended for intraday chop; H4 for broader ranges
    range_filter_lookback_bars: int = 24  # e.g. 24 H1 bars = last 24 hours
    # If current price is within this fraction of the range around the mid, skip (0.30 means middle 60% of range).
    range_filter_mid_frac: float = 0.30

    # Candle strength filter: require the entry candle to have a strong body and not be a doji / long-wick rejection.
    candle_filter_enabled: bool = False
    candle_body_min_frac: float = 0.60  # body >= 60% of candle range
    candle_wick_max_frac: float = 0.35  # each wick <= 35% of candle range
    candle_require_confirmation: bool = False  # require previous candle to confirm momentum

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

    # --- Anti-chase filter (exhaustion candle) ---
    # If True: skip new entries when the last closed M15 candle has a range (high-low)
    # greater than anti_chase_atr_mult * ATR(anti_chase_atr_period). This avoids
    # entering on blow-off / exhaustion bars where mean reversion is likely.
    anti_chase_enabled: bool = True
    anti_chase_atr_period: int = 20
    anti_chase_atr_mult: float = 2.0
    # En crypto, no usar mult por debajo de este valor (velas más largas vs ATR; evita bloqueo casi permanente cerca del umbral).
    anti_chase_atr_mult_crypto_floor: float = 2.65

    # --- Filter exit (momentum loss) ---
    # If True: when an open position is NOT in loss, close it at market if the
    # trend/momentum breaks (ADX falls below filter_exit_adx_min or EMA fast
    # flips against the position side). Helps release capital from stalled trades
    # and cut tails before they give back profits.
    filter_exit_enabled: bool = True
    # If None (0.0 by convention), use cfg.adx_min. Otherwise use this threshold.
    filter_exit_adx_min: float = 0.0
    # Require EMA fast direction to flip against the position side (safer: only exit
    # when both momentum AND structure agree the move is done).
    filter_exit_require_ema_reversal: bool = True
    # Only filter-exit when profit >= this (avoids bailing near SL without reason).
    filter_exit_min_profit_money: float = 0.0

    # --- Open with SL at entry (experimental "zero initial risk" stop) ---
    # If True, after computing SL/TP, SL is set to the entry (fill) price. TP stays.
    # Broker may still reject or adjust if stops must be a minimum distance from price.
    open_sl_at_entry: bool = False
    # When open_sl_at_entry: try SL at price±0*point, then ±1*point, … up to this max (per stop rules).
    open_sl_at_entry_max_extra_points: int = 400
    # Primer índice extra a probar (0 = legacy). Suele dejarse 0; el broker mínimo se suma automáticamente.
    open_sl_at_entry_min_extra_points: int = 0

    # --- Profit lock ladder (USD): each time peak floating profit passes N*step, lock N*secure ---
    # Example: step=1, secure=1 → at +$1.00 MFE move SL to guarantee ~+$1.00 on stop-out; at +$2, ~+$2, etc.
    profit_lock_enabled: bool = False
    profit_lock_step_money: float = 1.0
    profit_lock_secure_money: float = 1.0
    # Tight ATR trail often fights a USD ladder; when True, skip tight_trail if profit lock is on for that symbol.
    skip_tight_trail_when_profit_lock: bool = True

    # --- USD SL ladder (live): ~-$0.80 al abrir → esperar → BE (0) → cada +$1 hasta +$N con esperas ---
    # Reemplaza BE / trailing clásico / profit_lock mientras esté activo. ON/OFF es global (todos los símbolos);
    # en BOT_SYMBOL_CONFIG solo se pueden tunear initial_sl / tiempos / max_lock, no desactivar por activo.
    usd_sl_ladder_enabled: bool = True
    usd_sl_ladder_initial_sl_money: float = 0.80  # riesgo inicial aprox. en moneda de cuenta (positivo)
    usd_ladder_wait_be_seconds: int = 180  # "unos minutos" antes de mover a BE
    usd_ladder_step_seconds: int = 120  # espera entre pasos (post-BE y entre locks +$1, +$2, …)
    usd_ladder_max_lock_usd: int = 0  # 0 = sin tope (+$1,+,$2,… mientras suba profit); >0 = tope en ese USD

    # --- Signal inversion (experimental: open opposite side of decide()) ---
    # If True, BUY → SELL and SELL → BUY at decision time. NONE stays NONE.
    # Justificación (jun 2026): muestras reales muestran entradas sistemáticamente anti-correlacionadas
    # con la dirección real (mayo: avg MAE 0.29% vs MFE 0.07% en perdedores; jun 13-14: 5/5 perdedores
    # cada uno en sentido opuesto). Invertir es el A/B test más simple.
    invert_signals: bool = False

    # --- ATR-based profit trailing (Chandelier-style, replaces USD ladder) ---
    # Wait until profit >= activation_mult * initial_risk_money (measured from current SL distance),
    # then trail SL at K * ATR(M15) behind current price. Monotonic: SL only moves closer to price.
    # Designed to avoid cutting winners short on M15 chop while still locking gains on real runs.
    # Mutually exclusive with usd_sl_ladder_enabled (ladder takes precedence if both on).
    atr_profit_trail_enabled: bool = False
    atr_profit_trail_activation_mult: float = 1.5  # activate when profit >= mult * initial risk in money
    atr_profit_trail_atr_period: int = 14
    atr_profit_trail_atr_mult: float = 2.5  # SL distance = atr_mult * ATR(M15)

    # --- USD ladder extensions (micro-BE, high-water trailing, stagnant close) ---
    # 0 = off. Per-symbol overrides: micro_be_trigger_money, hwm_trail_pct, stagnant_* en BOT_SYMBOL_CONFIG.
    micro_be_trigger_money: float = 0.0
    hwm_trail_pct: float = 0.0
    stagnant_timeout_minutes: float = 0.0
    stagnant_max_profit_threshold: float = 0.0

    # --- Tight trailing stop post-profit ---
    # Once the position has earned at least `tight_trail_trigger_money`, apply a
    # tighter trailing stop at ATR(M15) * tight_trail_atr_mult distance from the
    # current price. This only moves the SL closer (monotonic); TP is left alone
    # so runners can keep running.
    tight_trail_enabled: bool = True
    tight_trail_trigger_money: float = 2.0
    tight_trail_atr_period: int = 14
    tight_trail_atr_mult: float = 1.2

    # --- Correlation / exposure caps (Turtle, Way of the Turtle Ch.8 pp.118-119) ---
    # Groups of symbols sharing directional risk (e.g. all USD-denominated, all crypto).
    # When the count of OPEN positions in a group reaches `max_positions_per_group`,
    # new entries in ANY symbol of that group are blocked. Pyramiding layers count.
    # A symbol can belong to multiple groups (all caps are enforced independently).
    # Prevents stacking N correlated trades that move together on a single macro tick.
    correlation_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    max_positions_per_group: int = 0  # 0 = disabled

    # --- ATR-based SL/TP (Turtle-style, volatility-adaptive) ---
    # When enabled, SL and TP distances are computed from ATR at entry time:
    #   sl_distance = ATR * sl_atr_mult   (Turtle: 2N = 2*ATR)
    #   tp_distance = ATR * tp_atr_mult   (default 2R target = 4*ATR)
    # Overrideable per-symbol via sl_tp_atr_enabled, sl_atr_mult, tp_atr_mult,
    # sl_tp_atr_timeframe, sl_tp_atr_period, sl_tp_atr_min_points.
    # If ATR is not available at runtime, falls back to fixed sl_points/tp_points.
    sl_tp_atr_enabled: bool = False
    sl_tp_atr_timeframe: str = "M15"  # "M1"|"M5"|"M15"|"M30"|"H1"|"H4"|"D1"
    sl_tp_atr_period: int = 14
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 4.0
    # Floor for the ATR-derived SL distance (in points). If ATR is so small that
    # sl_points < floor, we bump SL to the floor and scale TP by the same ratio.
    # 0 = no floor.
    sl_tp_atr_min_points: float = 0.0

    # Reconciliación tras reinicio: muchas posiciones manuales / recovery vienen con magic=0.
    track_zero_magic_positions: bool = True
    # Si 1: al arrancar cancela órdenes pendientes (limit/stop) con nuestro magic en símbolos del bot.
    cancel_orphan_pending_on_start: bool = False

    # --- XAUUSD parallel strategy (independent magic + exit rules; does not alter other symbols) ---
    xauusd_parallel_enabled: bool = False
    xauusd_parallel_symbol: str = "XAUUSD"
    xauusd_parallel_magic: int = 260415
    xauusd_parallel_lot: float = 0.01
    # Fallback / modo money (legado 1:1). Preferir sl_mode=atr.
    xauusd_parallel_tp_money: float = 2.00
    xauusd_parallel_sl_money: float = 2.00
    # atr | money — evidencia 18m: ATR×1.5 + RR2 + ADX25 + H1 + 10–13 ART
    xauusd_parallel_sl_mode: str = "atr"
    xauusd_parallel_sl_atr_mult: float = 1.5
    xauusd_parallel_tp_rr: float = 2.0
    xauusd_parallel_sl_atr_min: float = 1.5  # floor en precio
    xauusd_parallel_atr_period: int = 14
    xauusd_parallel_adx_min: float = 25.0
    xauusd_parallel_h1_filter: bool = True
    xauusd_parallel_h1_ema_period: int = 20
    # Bloquea SELL si H4=UP y BUY si H4=DOWN (misma lógica trend_h4_from_closes).
    xauusd_parallel_h4_filter: bool = True
    xauusd_parallel_start_hour: int = 10
    xauusd_parallel_end_hour: int = 13
    # Si no vacío: franjas LOCAL (mismo formato que BOT_TRADING_WINDOW_SESSIONS).
    # Tiene prioridad sobre start_hour/end_hour.
    xauusd_parallel_sessions: str = ""
    xauusd_parallel_max_positions: int = 1
    xauusd_parallel_max_spread_points: float = 55.0
    # Segundos mínimos entre aperturas XAUUSD (evita reentrar cada poll con SL estrecho).
    xauusd_parallel_cooldown_seconds: float = 300.0
    # Si True: como máximo 1 entrada por vela M15 (además del cooldown).
    xauusd_parallel_one_trade_per_bar: bool = True
    # Tras el fill, aleja el SL por el spread actual (ask-bid) para no quedar dentro del ruido.
    xauusd_parallel_sl_spread_buffer: bool = True
    # breakout (scan IS/OOS PF>1) | scalp (EMA9/21 — PF OOS ~0.70, no usar en live).
    xauusd_parallel_entry_mode: str = "breakout"
    # Cierra posiciones XAU al salir de la ventana / fin de semana (evita SL en el hueco).
    xauusd_parallel_flatten_outside_window: bool = True
    xauusd_parallel_anti_chase: bool = True
    xauusd_parallel_anti_chase_atr_mult: float = 2.0
    # Vela M15 > 1.5×ATR y cierre en el 20% extremo = chase/agotamiento (T1/T5/T6/T7).
    xauusd_parallel_exhaustion_atr_mult: float = 1.5
    xauusd_parallel_exhaustion_extreme_frac: float = 0.20
    # No comprar/vender si el precio ya corre > 1×ATR vs EMA de la señal (EMA50 breakout / EMA9 scalp).
    xauusd_parallel_extension_atr_max: float = 1.0
    # Última M1 cerrada > 0.8×ATR(M15) = spike de 1 minuto (T7 10:29).
    xauusd_parallel_m1_spike_atr_mult: float = 0.80


def _log_event(payload: dict) -> None:
    """
    Structured log line (JSON) so it can be parsed later for stats.
    """
    try:
        logging.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logging.info("%s", payload)


def _eval_log_skip(
    symbol: str,
    reason: str,
    *,
    when: Optional[datetime] = None,
    force: bool = False,
    cooldown_sec: Optional[float] = None,
    **extra: Any,
) -> None:
    """JSON EVAL_SKIP rate-limited por symbol+reason (para análisis post-sesión)."""
    wall = when or datetime.now()
    sym = str(symbol or "").strip().upper()
    key_reason = str(reason or "unknown")
    counts = _EVAL_SKIP_COUNTS.setdefault(sym, {})
    counts[key_reason] = int(counts.get(key_reason, 0) or 0) + 1
    snap: dict[str, Any] = {
        "ts": wall.isoformat(timespec="seconds"),
        "symbol": sym,
        "reason": key_reason,
        "hour": wall.hour,
    }
    for k, v in extra.items():
        if v is not None:
            snap[k] = v
    _EVAL_LAST_SKIP_BY_SYMBOL[sym] = snap

    key = f"{sym}|{key_reason}"
    cd = float(_EVAL_SKIP_COOLDOWN_SEC if cooldown_sec is None else cooldown_sec)
    if not eval_log_should_emit(_EVAL_LAST_LOG, key, wall, cd, force=force):
        return
    _EVAL_LAST_LOG[key] = wall
    _log_event({"event": "EVAL_SKIP", **snap})


def _eval_log_cycle(
    symbol: str,
    *,
    decision: str,
    when: Optional[datetime] = None,
    force: bool = False,
    **extra: Any,
) -> None:
    """JSON EVAL_CYCLE: snapshot de features/decisión por poll (rate-limit si decision=NONE)."""
    wall = when or datetime.now()
    sym = str(symbol or "").strip().upper()
    snap: dict[str, Any] = {
        "ts": wall.isoformat(timespec="seconds"),
        "symbol": sym,
        "decision": decision,
        "hour": wall.hour,
    }
    for k, v in extra.items():
        if v is not None:
            snap[k] = v
    # Siempre actualizar memoria (dashboard); el archivo puede ir rate-limited.
    _EVAL_LAST_CYCLE_BY_SYMBOL[sym] = snap

    key = f"{sym}|CYCLE|{decision}"
    cd = 0.0 if (force or decision in ("BUY", "SELL")) else float(_EVAL_CYCLE_COOLDOWN_SEC)
    if cd > 0 and not eval_log_should_emit(_EVAL_LAST_LOG, key, wall, cd, force=False):
        return
    _EVAL_LAST_LOG[key] = wall
    _log_event({"event": "EVAL_CYCLE", **snap})


def _eval_tracks_state(cfg: Config, now: Optional[datetime] = None) -> dict[str, Any]:
    """Snapshot multi-track para bot_state.json / flask_dashboard."""
    wall = now or datetime.now()
    tracks: list[dict[str, Any]] = []
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        in_win = _xauusd_parallel_in_window(cfg, wall) and not _weekend_blocks_new_trade(
            cfg, _xauusd_parallel_symbol(cfg), wall
        )
        sym = _xauusd_parallel_symbol(cfg)
        tracks.append(
            {
                "track": "xau_parallel_breakout",
                "symbol": sym,
                "path": "parallel",
                "magic": int(cfg.xauusd_parallel_magic),
                "window": _xauusd_parallel_window_label(cfg),
                "window_tz": "LOCAL",
                "in_window": in_win,
                "lot": float(cfg.xauusd_parallel_lot),
                "sl_mode": _xauusd_parallel_sl_mode(cfg),
                "sl_atr_mult": float(cfg.xauusd_parallel_sl_atr_mult),
                "tp_rr": float(cfg.xauusd_parallel_tp_rr),
                "adx_min": float(cfg.xauusd_parallel_adx_min),
                "last_signal": _XAUUSD_LAST_SIGNAL,
                "last_skip": _XAUUSD_LAST_SKIP,
                "open_positions": int((_xauusd_open_snapshot(cfg) or {}).get("count") or 0),
            }
        )
    for sym in cfg.symbols:
        p = _effective_params(cfg, sym)
        win = str(_sym_get(cfg, sym, "trading_window_sessions", "") or "").strip()
        in_win = bool(_session_allows_new_trade(cfg, sym, wall))
        cycle = _EVAL_LAST_CYCLE_BY_SYMBOL.get(str(sym).upper())
        skip = _EVAL_LAST_SKIP_BY_SYMBOL.get(str(sym).upper())
        tracks.append(
            {
                "track": str(p.get("eval_track") or "main_loop"),
                "symbol": sym,
                "path": "main_loop",
                "magic": int(cfg.magic),
                "window": win or "(global/24x5)",
                "window_tz": str(cfg.trading_window_timezone or "LOCAL"),
                "in_window": in_win,
                "lot": float(p.get("lot") or 0.0) or float(cfg.lot),
                "sl_atr_mult": float(p.get("sl_atr_mult") or 0.0),
                "tp_atr_mult": float(p.get("tp_atr_mult") or 0.0),
                "adx_min": float(p.get("adx_min") or 0.0),
                "last_cycle": cycle,
                "last_skip": skip,
                "skip_reasons": dict(_EVAL_SKIP_COUNTS.get(str(sym).upper(), {})),
                "decision": (cycle or {}).get("decision"),
                "trend_h4": (cycle or {}).get("trend_h4"),
                "signal_m15": (cycle or {}).get("signal_m15"),
                "adx": (cycle or {}).get("adx"),
                "spread": (cycle or {}).get("spread"),
            }
        )
    return {
        "ts": wall.isoformat(timespec="seconds"),
        "tracks": tracks,
        "crypto_sell_requires_h4_down": bool(cfg.crypto_sell_requires_h4_down),
        "daily_loss_limit": float(cfg.daily_loss_limit),
        "max_open_trades_total": int(cfg.max_open_trades_total),
    }


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
      {
        "EURUSD": {"sl_money": -0.8, "tp_money": 1.0, "max_spread_points": 25},
        "XAUUSD": {"sl_points": 80, "tp_points": 160, "max_spread_points": 55},
        "BTCUSD": {"sl_points": 40000, "tp_points": 80000, "max_spread_points": 3500,
                   "break_even_trigger_money": 25, "trail_trigger_money": 50,
                   "trail_new_sl_money": 25, "trail_new_tp_money": 60}
      }
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


def _deep_merge_symbol_config(base: dict[str, dict]) -> dict[str, dict]:
    return {k: dict(v) for k, v in base.items()}


def _load_symbol_config_overlay() -> dict[str, dict]:
    try:
        raw = SYMBOL_CONFIG_OVERLAY_FILE.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, dict] = {}
        for k, v in obj.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k.strip()] = dict(v)
        return out
    except FileNotFoundError:
        return {}
    except Exception:
        logging.exception("Reading %s failed; starting without overlay merges.", SYMBOL_CONFIG_OVERLAY_FILE)
        return {}


def _merge_symbol_configs(base: dict[str, dict], overlay: dict[str, dict]) -> dict[str, dict]:
    merged = _deep_merge_symbol_config(base)
    for sym, ov in overlay.items():
        if not isinstance(ov, dict):
            continue
        merged.setdefault(sym, {})
        merged[sym].update(ov)
    return merged


def resolve_symbol_config_with_overlay() -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Igual que al arrancar el bot live: objeto por símbolo = BOT_SYMBOL_CONFIG (.env)
    fusionado con `symbol_config_overlay.json` (unless BOT_SYMBOL_CONFIG_OVERLAY_DISABLED).

    Retorna (config_merged, overlay_dict): overlay_dict es {} si el overlay está off o vacío.

    Pensado para backtests, watcher y otros CLIs que deben ver los mismos overrides que produce
    el régimen automático.
    """
    base = _parse_symbol_config(_env("BOT_SYMBOL_CONFIG"))
    overlay: dict[str, dict] = {}
    if not _env_bool("BOT_SYMBOL_CONFIG_OVERLAY_DISABLED", False):
        overlay = _load_symbol_config_overlay()
    return _merge_symbol_configs(base, overlay), overlay


def _save_symbol_config_overlay(overlay: dict[str, dict]) -> None:
    try:
        SYMBOL_CONFIG_OVERLAY_FILE.write_text(json.dumps(overlay, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.exception("Could not write %s", SYMBOL_CONFIG_OVERLAY_FILE)


def _append_regime_apply_audit(row: dict) -> None:
    try:
        with REGIME_APPLY_AUDIT_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logging.exception("Could not append to %s", REGIME_APPLY_AUDIT_JSONL)


def _parse_regime_lot_mult_json(raw: Optional[str]) -> dict[str, float]:
    if raw is None or not str(raw).strip():
        return {}
    try:
        obj = json.loads(str(raw).strip())
        if not isinstance(obj, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                continue
            try:
                mul = float(v)
            except (TypeError, ValueError):
                continue
            key = k.strip().upper()
            if mul > 0:
                out[key] = mul
        return out
    except Exception:
        logging.exception("BOT_REGIME_LOT_MULT_JSON invalid; ignoring.")
        return {}


def _apply_pending_regime_auto_patches(cfg: Config, detector: Any) -> None:
    """Merge suggested keys into live cfg + overlay file; audit en JSONL."""
    if detector is None:
        return
    if not _env_bool("BOT_MARKET_REGIME_AUTO_APPLY", False):
        try:
            detector.take_pending_auto_patches()
        except Exception:
            logging.exception("take_pending_auto_patches failed while auto-apply is off.")
        return
    try:
        patches = detector.take_pending_auto_patches()
    except Exception:
        logging.exception("take_pending_auto_patches failed.")
        return
    if not patches:
        return

    overlay = _load_symbol_config_overlay()
    for symbol, prev_label, new_label, patch in patches:
        if not isinstance(patch, dict) or not patch:
            continue
        sym = str(symbol).strip()
        cfg.symbol_config.setdefault(sym, {})
        cfg.symbol_config[sym].update(patch)
        overlay.setdefault(sym, {})
        overlay[sym].update(patch)
        _append_regime_apply_audit(
            {
                "event": "REGIME_AUTO_PATCH",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": sym,
                "prev_regime": prev_label,
                "new_regime": new_label,
                "patch": dict(patch),
                "overlay_file": str(SYMBOL_CONFIG_OVERLAY_FILE),
            }
        )
        logging.info(
            "[RegimeAuto] %s %s→%s merged into cfg + overlay (%d keys): %s",
            sym,
            prev_label,
            new_label,
            len(patch),
            patch,
        )

    _save_symbol_config_overlay(overlay)


def _parse_portfolio_weights(raw: Optional[str], symbols: Tuple[str, ...]) -> dict[str, float]:
    """
    Accepts:
      - "GBPUSD=0.4,USDJPY=0.4,NZDJPY=0.2"
      - JSON dict: {"GBPUSD":0.4,"USDJPY":0.4,"NZDJPY":0.2}
    Returns normalized weights for the provided `symbols`.
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


def _portfolio_weight_for(cfg: Config, symbol: str) -> float:
    if not bool(cfg.portfolio_enabled):
        return 0.0
    if bool(cfg.portfolio_dynamic_enabled) and _portfolio_dynamic_weights:
        return float(_portfolio_dynamic_weights.get(symbol, 0.0) or 0.0)
    return float(cfg.portfolio_weights.get(symbol, 0.0) or 0.0)


def _sym_get(cfg: Config, symbol: str, key: str, default):
    o = cfg.symbol_config.get(symbol, {})
    if isinstance(o, dict) and key in o:
        return o[key]
    return default


def _effective_cooldown_minutes(cfg: Config, symbol: str) -> int:
    cd = int(float(_sym_get(cfg, symbol, "cooldown_minutes", cfg.cooldown_minutes)))
    if symbol_uses_crypto_relaxed_m15(symbol):
        fl = int(getattr(cfg, "cooldown_minutes_crypto_floor", 0) or 0)
        if fl > 0:
            cd = max(cd, fl)
    return max(0, cd)


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


def _prune_symbol_open_timestamps(symbol: str, now: datetime, window_seconds: float) -> None:
    ls = _symbol_open_timestamps.setdefault(symbol, [])
    cutoff = now - timedelta(seconds=window_seconds)
    _symbol_open_timestamps[symbol] = [t for t in ls if t > cutoff]


def _count_opens_in_window(symbol: str, now: datetime, window_seconds: float) -> int:
    _prune_symbol_open_timestamps(symbol, now, window_seconds)
    return len(_symbol_open_timestamps.get(symbol, []))


def _record_symbol_open_timestamp(symbol: str, now: Optional[datetime] = None) -> None:
    _symbol_open_timestamps.setdefault(symbol, []).append(now or datetime.now())


def _broker_min_stop_extra_points(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0
    stops = int(getattr(info, "trade_stops_level", 0) or 0)
    freeze = int(getattr(info, "trade_freeze_level", 0) or 0)
    return max(0, stops, freeze)


def _effective_params(cfg: Config, symbol: str) -> dict:
    """
    Returns effective parameters for a given symbol (cfg defaults overridden by cfg.symbol_config).
    Supported keys:
      sl_money, tp_money, max_spread_points, adx_min, ema_fast_m15, ema_slow_m15,
      cooldown_minutes, use_risk_sizing, risk_per_trade_pct,
      break_even_trigger_money, trail_trigger_money, trail_new_sl_money, trail_new_tp_money
      max_trades_per_hour_per_symbol (JSON override), open_sl_at_entry_min_extra_points (JSON)
      usd_sl_ladder_enabled (solo global cfg; no override por símbolo),
      usd_sl_ladder_initial_sl_money,       usd_ladder_wait_be_seconds, usd_ladder_step_seconds,
      usd_ladder_max_lock_usd (0 = sin tope en locks +$N)
      micro_be_trigger_money, hwm_trail_pct, stagnant_timeout_minutes, stagnant_max_profit_threshold
      range_filter_enabled, range_filter_timeframe, range_filter_lookback_bars, range_filter_mid_frac
      candle_filter_enabled, candle_body_min_frac, candle_wick_max_frac, candle_require_confirmation
      max_lot_per_trade (override simbólica; fallback cfg.max_lot_per_trade)
      price_slope_filter_enabled, price_slope_n (N cierres M15 para regressión)
      rsi_h4_filter_enabled, rsi_h4_oversold_threshold, rsi_h4_overbought_threshold
      extension_filter_max_pct, extension_filter_h1_bars
    """
    ac_mult = float(_sym_get(cfg, symbol, "anti_chase_atr_mult", cfg.anti_chase_atr_mult))
    if symbol_uses_crypto_relaxed_m15(symbol):
        ac_mult = max(ac_mult, float(getattr(cfg, "anti_chase_atr_mult_crypto_floor", 0.0) or 0.0))
    return {
        "sl_money": float(_sym_get(cfg, symbol, "sl_money", cfg.sl_money)),
        "tp_money": float(_sym_get(cfg, symbol, "tp_money", cfg.tp_money)),
        "sl_points": float(_sym_get(cfg, symbol, "sl_points", cfg.sl_points)),
        "tp_points": float(_sym_get(cfg, symbol, "tp_points", cfg.tp_points)),
        "max_spread_points": float(_sym_get(cfg, symbol, "max_spread_points", cfg.max_spread_points)),
        "adx_min": float(_sym_get(cfg, symbol, "adx_min", cfg.adx_min)),
        "range_filter_enabled": bool(_sym_get(cfg, symbol, "range_filter_enabled", cfg.range_filter_enabled)),
        "range_filter_timeframe": str(_sym_get(cfg, symbol, "range_filter_timeframe", cfg.range_filter_timeframe)),
        "range_filter_lookback_bars": int(
            float(_sym_get(cfg, symbol, "range_filter_lookback_bars", cfg.range_filter_lookback_bars))
        ),
        "range_filter_mid_frac": float(_sym_get(cfg, symbol, "range_filter_mid_frac", cfg.range_filter_mid_frac)),
        "candle_filter_enabled": bool(_sym_get(cfg, symbol, "candle_filter_enabled", cfg.candle_filter_enabled)),
        "candle_body_min_frac": float(_sym_get(cfg, symbol, "candle_body_min_frac", cfg.candle_body_min_frac)),
        "candle_wick_max_frac": float(_sym_get(cfg, symbol, "candle_wick_max_frac", cfg.candle_wick_max_frac)),
        "candle_require_confirmation": bool(
            _sym_get(cfg, symbol, "candle_require_confirmation", cfg.candle_require_confirmation)
        ),
        "ema_fast_m15": int(float(_sym_get(cfg, symbol, "ema_fast_m15", cfg.ema_fast_m15))),
        "ema_slow_m15": int(float(_sym_get(cfg, symbol, "ema_slow_m15", cfg.ema_slow_m15))),
        "cooldown_minutes": _effective_cooldown_minutes(cfg, symbol),
        "use_risk_sizing": bool(_sym_get(cfg, symbol, "use_risk_sizing", cfg.use_risk_sizing)),
        "risk_per_trade_pct": float(_sym_get(cfg, symbol, "risk_per_trade_pct", cfg.risk_per_trade_pct)),
        "break_even_trigger_money": float(_sym_get(cfg, symbol, "break_even_trigger_money", cfg.break_even_trigger_money)),
        "trail_trigger_money": float(_sym_get(cfg, symbol, "trail_trigger_money", cfg.trail_trigger_money)),
        "trail_new_sl_money": float(_sym_get(cfg, symbol, "trail_new_sl_money", cfg.trail_new_sl_money)),
        "trail_new_tp_money": float(_sym_get(cfg, symbol, "trail_new_tp_money", cfg.trail_new_tp_money)),
        "max_positions_per_symbol": int(float(_sym_get(cfg, symbol, "max_positions_per_symbol", cfg.max_positions_per_symbol))),
        "pyramiding_require_be": bool(_sym_get(cfg, symbol, "pyramiding_require_be", cfg.pyramiding_require_be)),
        "pyramiding_min_separation_points": float(_sym_get(cfg, symbol, "pyramiding_min_separation_points", cfg.pyramiding_min_separation_points)),
        "anti_chase_enabled": bool(_sym_get(cfg, symbol, "anti_chase_enabled", cfg.anti_chase_enabled)),
        "anti_chase_atr_mult": ac_mult,
        "anti_chase_atr_period": int(float(_sym_get(cfg, symbol, "anti_chase_atr_period", cfg.anti_chase_atr_period))),
        "filter_exit_enabled": bool(_sym_get(cfg, symbol, "filter_exit_enabled", cfg.filter_exit_enabled)),
        "filter_exit_adx_min": float(_sym_get(cfg, symbol, "filter_exit_adx_min", cfg.filter_exit_adx_min)),
        "filter_exit_require_ema_reversal": bool(_sym_get(cfg, symbol, "filter_exit_require_ema_reversal", cfg.filter_exit_require_ema_reversal)),
        "filter_exit_min_profit_money": float(_sym_get(cfg, symbol, "filter_exit_min_profit_money", cfg.filter_exit_min_profit_money)),
        "tight_trail_enabled": bool(_sym_get(cfg, symbol, "tight_trail_enabled", cfg.tight_trail_enabled)),
        "tight_trail_trigger_money": float(_sym_get(cfg, symbol, "tight_trail_trigger_money", cfg.tight_trail_trigger_money)),
        "tight_trail_atr_period": int(float(_sym_get(cfg, symbol, "tight_trail_atr_period", cfg.tight_trail_atr_period))),
        "tight_trail_atr_mult": float(_sym_get(cfg, symbol, "tight_trail_atr_mult", cfg.tight_trail_atr_mult)),
        "sl_tp_atr_enabled": bool(_sym_get(cfg, symbol, "sl_tp_atr_enabled", cfg.sl_tp_atr_enabled)),
        "sl_tp_atr_timeframe": str(_sym_get(cfg, symbol, "sl_tp_atr_timeframe", cfg.sl_tp_atr_timeframe)),
        "sl_tp_atr_period": int(float(_sym_get(cfg, symbol, "sl_tp_atr_period", cfg.sl_tp_atr_period))),
        "sl_atr_mult": float(_sym_get(cfg, symbol, "sl_atr_mult", cfg.sl_atr_mult)),
        "tp_atr_mult": float(_sym_get(cfg, symbol, "tp_atr_mult", cfg.tp_atr_mult)),
        "sl_tp_atr_min_points": float(_sym_get(cfg, symbol, "sl_tp_atr_min_points", cfg.sl_tp_atr_min_points)),
        "open_sl_at_entry": bool(_sym_get(cfg, symbol, "open_sl_at_entry", cfg.open_sl_at_entry)),
        "open_sl_at_entry_max_extra_points": int(
            float(_sym_get(cfg, symbol, "open_sl_at_entry_max_extra_points", cfg.open_sl_at_entry_max_extra_points))
        ),
        "open_sl_at_entry_min_extra_points": int(
            float(_sym_get(cfg, symbol, "open_sl_at_entry_min_extra_points", cfg.open_sl_at_entry_min_extra_points))
        ),
        "profit_lock_enabled": bool(_sym_get(cfg, symbol, "profit_lock_enabled", cfg.profit_lock_enabled)),
        "profit_lock_step_money": float(_sym_get(cfg, symbol, "profit_lock_step_money", cfg.profit_lock_step_money)),
        "profit_lock_secure_money": float(_sym_get(cfg, symbol, "profit_lock_secure_money", cfg.profit_lock_secure_money)),
        "usd_sl_ladder_enabled": bool(cfg.usd_sl_ladder_enabled),
        "usd_sl_ladder_initial_sl_money": float(
            _sym_get(cfg, symbol, "usd_sl_ladder_initial_sl_money", cfg.usd_sl_ladder_initial_sl_money)
        ),
        "usd_ladder_wait_be_seconds": int(
            float(_sym_get(cfg, symbol, "usd_ladder_wait_be_seconds", cfg.usd_ladder_wait_be_seconds))
        ),
        "usd_ladder_step_seconds": int(float(_sym_get(cfg, symbol, "usd_ladder_step_seconds", cfg.usd_ladder_step_seconds))),
        "usd_ladder_max_lock_usd": int(float(_sym_get(cfg, symbol, "usd_ladder_max_lock_usd", cfg.usd_ladder_max_lock_usd))),
        "micro_be_trigger_money": float(_sym_get(cfg, symbol, "micro_be_trigger_money", cfg.micro_be_trigger_money)),
        "hwm_trail_pct": float(_sym_get(cfg, symbol, "hwm_trail_pct", cfg.hwm_trail_pct)),
        "stagnant_timeout_minutes": float(
            _sym_get(cfg, symbol, "stagnant_timeout_minutes", getattr(cfg, "stagnant_timeout_minutes", 0.0))
        ),
        "stagnant_max_profit_threshold": float(
            _sym_get(cfg, symbol, "stagnant_max_profit_threshold", getattr(cfg, "stagnant_max_profit_threshold", 0.0))
        ),
        "max_lot_per_trade": float(_sym_get(cfg, symbol, "max_lot_per_trade", getattr(cfg, "max_lot_per_trade", 0.0))),
        # lot>0 en BOT_SYMBOL_CONFIG fuerza volumen fijo (ej. XNGUSD volume_min=0.1).
        "lot": float(_sym_get(cfg, symbol, "lot", 0.0) or 0.0),
        "eval_track": str(_sym_get(cfg, symbol, "eval_track", "") or ""),
        "price_slope_filter_enabled": bool(
            _sym_get(cfg, symbol, "price_slope_filter_enabled", cfg.price_slope_filter_enabled)
        ),
        "price_slope_n": max(
            2,
            int(float(_sym_get(cfg, symbol, "price_slope_n", getattr(cfg, "price_slope_n", 6)))),
        ),
        "rsi_h4_filter_enabled": bool(_sym_get(cfg, symbol, "rsi_h4_filter_enabled", False)),
        "rsi_h4_oversold_threshold": float(_sym_get(cfg, symbol, "rsi_h4_oversold_threshold", 35.0)),
        "rsi_h4_overbought_threshold": float(_sym_get(cfg, symbol, "rsi_h4_overbought_threshold", 65.0)),
        "extension_filter_max_pct": float(_sym_get(cfg, symbol, "extension_filter_max_pct", 4.0)),
        "extension_filter_h1_bars": int(float(_sym_get(cfg, symbol, "extension_filter_h1_bars", 16))),
        "max_trades_per_hour_per_symbol": _effective_max_trades_per_hour(cfg, symbol),
    }


def _preflight_new_entry(
    cfg: Config,
    symbol: str,
    *,
    side: Signal,
    now: datetime,
    adx: Optional[float],
    p: dict,
    active: Optional[bool],
    ok_spread: bool,
    spread: Optional[float],
    avg_spread: Optional[float],
) -> bool:
    """Filtros de entorno antes de open_trade (misma política que `_maybe_trade_symbol`)."""
    if side not in ("BUY", "SELL"):
        return False

    if not _session_allows_new_trade(cfg, symbol, now):
        reason = "session/calendar gate (weekend/window/cutoff)"
        skip_reason = "session_gate"
        if (
            cfg.quiet_session_block_enabled
            and cfg.quiet_session_hours
            and now.hour in cfg.quiet_session_hours
            and symbol not in cfg.quiet_session_exempt_symbols
        ):
            reason = f"quiet session (hour={now.hour:02d}h, low liquidity for FX)"
            skip_reason = "quiet_session"
            _log_event(
                {
                    "event": "QUIET_SESSION_SKIP",
                    "ts": now.isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "hour": now.hour,
                    "quiet_hours": list(cfg.quiet_session_hours),
                    "exempt_symbols": list(cfg.quiet_session_exempt_symbols),
                }
            )
        _eval_log_skip(symbol, skip_reason, when=now, side=side, detail=reason)
        logging.info("[%s] No trade: %s.", symbol, reason)
        return False

    if not _can_trade(cfg, symbol, now):
        _eval_log_skip(
            symbol,
            "cooldown",
            when=now,
            side=side,
            cooldown_minutes=_effective_cooldown_minutes(cfg, symbol),
        )
        logging.info(
            "[%s] No trade: cooldown active (effective %d min since last open).",
            symbol,
            _effective_cooldown_minutes(cfg, symbol),
        )
        return False

    if _failed_order_attempt_cooldown_blocks(cfg, symbol, now):
        sec = float(cfg.failed_attempt_cooldown_seconds or 0.0)
        last_f = _last_failed_order_attempt.get(symbol)
        ago = (now - last_f).total_seconds() if last_f is not None else 0.0
        _eval_log_skip(symbol, "failed_order_cooldown", when=now, side=side, ago_sec=ago, block_sec=sec)
        logging.info(
            "[%s] No trade: failed-order cooldown active (%.0f s since broker rejection; block %.0f s).",
            symbol,
            ago,
            sec,
        )
        return False

    if active is False:
        _eval_log_skip(symbol, "market_inactive", when=now, side=side)
        logging.info("[%s] No trade: market inactive (avg range filter).", symbol)
        return False

    if not ok_spread:
        _eval_log_skip(
            symbol,
            "spread",
            when=now,
            side=side,
            spread=spread,
            avg_spread=avg_spread,
            max_spread_points=float(p["max_spread_points"]),
        )
        if spread is None:
            logging.warning("[%s] Could not read spread. Skipping.", symbol)
        elif cfg.spread_dynamic_enabled and avg_spread is not None:
            logging.info(
                "[%s] No trade: spread gate failed (now=%.1f avg=%.1f mult=%.2f hard_max=%.1f).",
                symbol,
                spread,
                avg_spread,
                cfg.spread_dynamic_mult,
                float(p["max_spread_points"]),
            )
        else:
            logging.info("[%s] No trade: spread gate failed.", symbol)
        return False

    if _count_managed_open_positions(cfg) >= cfg.max_open_trades_total:
        _eval_log_skip(symbol, "max_open_total", when=now, side=side, max_open=cfg.max_open_trades_total)
        logging.info("[%s] No trade: max total open positions (gestionadas por bot) reached.", symbol)
        return False

    adx_min = float(p["adx_min"])
    if adx is None or adx < adx_min:
        _eval_log_skip(
            symbol,
            "adx_low",
            when=now,
            side=side,
            adx=adx if adx is not None else 0.0,
            adx_min=adx_min,
        )
        logging.info("[%s] No trade: ADX low (%.2f < %.2f) → lateral.", symbol, adx if adx is not None else 0.0, adx_min)
        return False

    if bool(p.get("range_filter_enabled", False)):
        ok_range, m = _range_filter_allows_entry(
            symbol,
            timeframe=str(p.get("range_filter_timeframe", "H1") or "H1"),
            lookback_bars=int(p.get("range_filter_lookback_bars", 24) or 24),
            mid_frac=float(p.get("range_filter_mid_frac", 0.30) or 0.30),
        )
        if not ok_range:
            _log_event(
                {
                    "event": "RANGE_FILTER_SKIP",
                    "ts": now.isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "timeframe": str(p.get("range_filter_timeframe", "H1") or "H1"),
                    "lookback_bars": int(p.get("range_filter_lookback_bars", 24) or 24),
                    "mid_frac": float(p.get("range_filter_mid_frac", 0.30) or 0.30),
                    **m,
                }
            )
            _eval_log_skip(symbol, "range_filter", when=now, side=side, **m)
            logging.info("[%s] No trade: range filter (near mid of recent range).", symbol)
            return False

    if bool(p.get("candle_filter_enabled", False)):
        ok_candle, cm = _passes_candle_strength_filter(
            symbol,
            side=side,
            body_min_frac=float(p.get("candle_body_min_frac", 0.60) or 0.60),
            wick_max_frac=float(p.get("candle_wick_max_frac", 0.35) or 0.35),
            require_confirmation=bool(p.get("candle_require_confirmation", False)),
        )
        if not ok_candle:
            _log_event(
                {
                    "event": "CANDLE_FILTER_SKIP",
                    "ts": now.isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "side": side,
                    **cm,
                }
            )
            _eval_log_skip(symbol, "candle_filter", when=now, side=side, **cm)
            logging.info("[%s] No trade: candle filter rejected entry candle.", symbol)
            return False

    return True


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


def _rotate_trades_log_on_start(*, logs_dir: str = "logs") -> None:
    """
    Rotate `trades.log` on each bot start:
    - If `trades.log` exists and has content, move it to `logs/trades_YYYY-MM-DD_HHMMSS.log`.
    - Create a fresh empty `trades.log` for the new session.
    """
    src = _TRADES_LOG_FILENAME
    try:
        if not os.path.isfile(src):
            return
        if os.path.getsize(src) <= 0:
            return

        os.makedirs(logs_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dst = os.path.join(logs_dir, f"trades_{stamp}.log")

        # os.replace is atomic on same filesystem (works on Windows too).
        os.replace(src, dst)

        # Ensure the new session has a fresh file ready before FileHandler attaches.
        with open(src, "w", encoding="utf-8"):
            pass
    except Exception:
        # Best-effort: never block bot start because of log rotation.
        try:
            logging.exception("Failed to rotate trades.log on start.")
        except Exception:
            pass


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


def _maybe_market_regime_detector(cfg: Config):
    """
    Régimen por símbolo (market_regime.py): Telegram + snapshot en bot_state.json.
    BOT_MARKET_REGIME_ENABLED=0 desactiva. BOT_MARKET_REGIME_CHECK_INTERVAL_SEC (default 900).
    BOT_MARKET_REGIME_ADX_TRENDING / BOT_MARKET_REGIME_ADX_RANGING — umbrales de clasificación (default 28 / 22).
    BOT_MARKET_REGIME_AUTO_APPLY (default 0): telemetría por defecto. Si =1, al cambiar régimen mergea
    claves sugeridas en cfg en vivo + symbol_config_overlay.json (audit: regime_apply_audit.jsonl).
    BOT_SYMBOL_CONFIG_OVERLAY_DISABLED=1 ignora el overlay al arrancar.
    """
    if not _env_bool("BOT_MARKET_REGIME_ENABLED", True):
        return None
    try:
        from market_regime import MarketRegimeDetector

        interval = float(os.getenv("BOT_MARKET_REGIME_CHECK_INTERVAL_SEC") or "900")
        adx_trending = float(os.getenv("BOT_MARKET_REGIME_ADX_TRENDING") or "28")
        adx_ranging = float(os.getenv("BOT_MARKET_REGIME_ADX_RANGING") or "22")
        return MarketRegimeDetector(
            cfg,
            check_interval_s=interval,
            adx_trending=adx_trending,
            adx_ranging=adx_ranging,
            notify_module=telegram_notify,
        )
    except Exception:
        logging.exception("MarketRegimeDetector no disponible; continuando sin régimen.")
        return None


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


def _xauusd_parallel_parsed_sessions(cfg: Config) -> Tuple[Tuple[dt_time, dt_time], ...]:
    raw = str(getattr(cfg, "xauusd_parallel_sessions", "") or "").strip()
    if not raw:
        return ()
    try:
        return parse_trading_window_sessions(raw)
    except Exception:
        logging.warning("BOT_XAUUSD_PARALLEL_SESSIONS inválido: %r — fallback start/end hour.", raw)
        return ()


def _xauusd_parallel_window_label(cfg: Config) -> str:
    sessions = _xauusd_parallel_parsed_sessions(cfg)
    if sessions:
        return _format_session_ranges(sessions)
    start_h = int(getattr(cfg, "xauusd_parallel_start_hour", 10) or 10)
    end_h = int(getattr(cfg, "xauusd_parallel_end_hour", 14) or 14)
    return f"{start_h:02d}:00–{end_h:02d}:00"


def _xauusd_parallel_in_window(cfg: Config, wall_now: Optional[datetime] = None) -> bool:
    """Ventana XAU parallel: sessions (LOCAL) si hay; si no, start_hour ≤ h < end_hour."""
    now = wall_now or datetime.now()
    sessions = _xauusd_parallel_parsed_sessions(cfg)
    if sessions:
        return _within_parsed_sessions(sessions, cfg, now, tz_override="LOCAL")
    start_h = int(getattr(cfg, "xauusd_parallel_start_hour", 10) or 10)
    end_h = int(getattr(cfg, "xauusd_parallel_end_hour", 14) or 14)
    return start_h <= now.hour < end_h


# Estado previo "dentro de ventana" por instrumento (Telegram session start/end).
_SESSION_WINDOW_PREV_INSIDE: dict[str, bool] = {}


def _instrument_session_windows(cfg: Config) -> list[dict[str, Any]]:
    """
    Ventanas horarias notificables por instrumento/estrategia.
    - XAUUSD paralelo: sessions multi-franja o start_hour/end_hour (fin exclusivo).
    - Símbolos del loop: trading_window_sessions por símbolo o global.
    """
    out: list[dict[str, Any]] = []
    tz = str(getattr(cfg, "trading_window_timezone", "LOCAL") or "LOCAL")

    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        sym = _xauusd_parallel_symbol(cfg)
        sessions = _xauusd_parallel_parsed_sessions(cfg)
        if sessions:
            out.append(
                {
                    "key": f"parallel:{sym}",
                    "symbol": sym,
                    "label": f"{sym} (scalper)",
                    "kind": "parallel",
                    "sessions": sessions,
                    "end_exclusive": False,
                    "tz": "LOCAL",
                    "range_label": _format_session_ranges(sessions),
                }
            )
        else:
            start_h = int(getattr(cfg, "xauusd_parallel_start_hour", 10) or 10)
            end_h = int(getattr(cfg, "xauusd_parallel_end_hour", 14) or 14)
            out.append(
                {
                    "key": f"parallel:{sym}",
                    "symbol": sym,
                    "label": f"{sym} (scalper)",
                    "kind": "parallel",
                    "sessions": ((dt_time(start_h % 24, 0), dt_time(end_h % 24, 0)),),
                    "end_exclusive": True,
                    "tz": "LOCAL",
                    "range_label": f"{start_h:02d}:00–{end_h:02d}:00",
                }
            )

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
        # Misma semántica que XAUUSD parallel: start_hour <= hour < end_hour
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
    Ej.: XAUUSD scalper 10–14 → mensaje a las 10:00 y a las 14:00 (hora local).
    FX legacy start/end hour → siempre reloj local (aunque TIMEZONE=UTC para stocks).
    """
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_NOTIFY_SESSION_WINDOW", True):
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


def _can_trade(cfg: Config, symbol: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    last = _last_trade_time.get(symbol)
    if last is None:
        return True
    minutes = float(_effective_cooldown_minutes(cfg, symbol))
    return (now - last).total_seconds() >= minutes * 60.0


def _record_failed_order_attempt(symbol: str, cfg: Optional[Config] = None, result: Any = None) -> None:
    _last_failed_order_attempt[symbol] = datetime.now()
    if cfg is not None:
        logging.info(
            "[%s] Failed order cooldown activado (retcode=%s). Próximo intento en %.0fs.",
            symbol,
            getattr(result, "retcode", "?") if result is not None else "?",
            float(cfg.failed_attempt_cooldown_seconds or 120),
        )


def _failed_order_attempt_cooldown_blocks(cfg: Config, symbol: str, now: datetime) -> bool:
    sec = float(getattr(cfg, "failed_attempt_cooldown_seconds", 0.0) or 0.0)
    if sec <= 0:
        return False
    last = _last_failed_order_attempt.get(symbol)
    if last is None:
        return False
    return (now - last).total_seconds() < sec


def _clamp_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return max(0.01, round(volume, 2))

    vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
    vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
    vstep = float(getattr(info, "volume_step", 0.01) or 0.01)

    vol = max(vmin, min(volume, vmax))
    if vstep > 0:
        steps = round(vol / vstep)
        vol = steps * vstep
    # Common lot precision is 2, but keep it safe
    return float(f"{vol:.2f}")


def calculate_lot_by_risk(symbol: str, risk_pct: float, sl_money_at_base_lot: float, base_lot: float) -> float:
    """
    Risk sizing that matches your existing money-SL model.
    Assumption: `sl_money_at_base_lot` is the expected loss in account currency for `base_lot`.
    Then loss scales ~linearly with volume, so:
      target_loss = balance * risk_pct
      lot = base_lot * (target_loss / abs(sl_money_at_base_lot))
    """
    acc = mt5.account_info()
    if acc is None:
        return _clamp_volume(symbol, base_lot)

    balance = float(getattr(acc, "balance", 0.0) or 0.0)
    risk_amount = balance * (max(0.0, risk_pct) / 100.0)
    if risk_amount <= 0 or abs(sl_money_at_base_lot) <= 0:
        return _clamp_volume(symbol, base_lot)

    lot = base_lot * (risk_amount / abs(sl_money_at_base_lot))
    return _clamp_volume(symbol, lot)

def connect_mt5(cfg: Config) -> bool:
    """
    Connects to the running MT5 terminal.
    Credentials are read from environment variables:
      MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
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

    last_err = None
    for attempt in range(1, cfg.connect_retries + 1):
        try:
            ok = mt5.initialize(login=login, password=password, server=server)
            if ok:
                acc = mt5.account_info()
                term = mt5.terminal_info()
                logging.info(
                    "Connected to MT5. Account=%s | Name=%s | Server=%s | Currency=%s | Terminal=%s",
                    getattr(acc, "login", "?"),
                    getattr(acc, "name", "?"),
                    getattr(acc, "server", "?"),
                    getattr(acc, "currency", "?"),
                    getattr(term, "name", "?"),
                )
                return True

            last_err = mt5.last_error()
            logging.warning("MT5 initialize failed (attempt %d/%d). last_error=%s", attempt, cfg.connect_retries, last_err)
        except Exception as e:
            last_err = e
            logging.exception("Exception during MT5 initialize (attempt %d/%d).", attempt, cfg.connect_retries)

        time.sleep(cfg.connect_retry_sleep)

    logging.error("Could not connect to MT5 after retries. Last error: %r", last_err)
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
        logging.error("Symbol not found in MT5: %s", symbol)
        return False
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logging.error("Could not select/enable symbol: %s", symbol)
            return False
    return True


def _get_spread_points(symbol: str) -> Optional[float]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None or info.point == 0:
        return None
    return (tick.ask - tick.bid) / info.point


def get_trend_h4(symbol: str) -> Trend:
    """
    H4 trend via `strategy_core.trend_h4_from_closes` on the last 6 closed bars.
    """
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 1, 7)
    if rates is None or len(rates) < 6:
        logging.warning("[%s] Not enough H4 data for trend.", symbol)
        return "FLAT"

    closes = [float(r["close"]) for r in rates[-6:]]
    return trend_h4_from_closes(closes)


def get_rsi_h4(symbol: str, period: int = 14) -> Optional[float]:
    """RSI(14) sobre los últimos period*2+2 cierres H4 cerrados."""
    bars_needed = period * 2 + 2
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 1, bars_needed)
    if rates is None or len(rates) < period + 1:
        logging.debug("[%s] Not enough H4 data for RSI.", symbol)
        return None
    closes = [float(r["close"]) for r in rates]
    return rsi_from_closes(closes, period=period)


def _movement_is_extended(
    symbol: str,
    side: Signal,
    *,
    lookback_h1_bars: int = 16,
    max_extension_pct: float = 4.0,
) -> bool:
    """
    Devuelve True si el precio ya se movió más de max_extension_pct% desde el
    extremo reciente en las últimas lookback_h1_bars velas H1 cerradas.
    SELL: precio ya cayó > X% desde el HIGH reciente → movimiento extendido.
    BUY:  precio ya subió > X% desde el LOW reciente  → movimiento extendido.
    Fail-open: si no hay datos, devuelve False (no bloquea).
    """
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, lookback_h1_bars)
    if rates is None or len(rates) < 2:
        return False

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
    current_price = float(tick.bid)

    if side == "SELL":
        recent_high = max(float(r["high"]) for r in rates)
        if recent_high <= 0:
            return False
        drop_pct = (recent_high - current_price) / recent_high * 100.0
        return drop_pct > max_extension_pct

    if side == "BUY":
        recent_low = min(float(r["low"]) for r in rates)
        if recent_low <= 0:
            return False
        rise_pct = (current_price - recent_low) / recent_low * 100.0
        return rise_pct > max_extension_pct

    return False


def log_h4_snapshot_for_symbol(cfg: Config, symbol: str, *, trend_h4: Trend) -> None:
    """
    Each poll: H4 label (6-close regression), EMA(H4,50) / EMA(H4,200), and crypto short gate.
    sell_allowed mirrors BOT_CRYPTO_SELL_REQUIRES_H4_DOWN for relaxed crypto (else True).
    """
    ema50_h4 = get_ema(symbol, mt5.TIMEFRAME_H4, 50)
    ema200_h4 = get_ema(symbol, mt5.TIMEFRAME_H4, 200)
    e50_s = f"{ema50_h4:.5f}" if ema50_h4 is not None else "n/a"
    e200_s = f"{ema200_h4:.5f}" if ema200_h4 is not None else "n/a"
    relaxed = symbol_uses_crypto_relaxed_m15(symbol)
    req = bool(cfg.crypto_sell_requires_h4_down)
    sell_allowed = (trend_h4 == "DOWN") if relaxed and req else True
    logging.info(
        "[%s] H4 trend: %s | ema50_h4=%s ema200_h4=%s | sell_allowed=%s",
        symbol,
        trend_h4,
        e50_s,
        e200_s,
        str(sell_allowed),
    )


def get_signal_m15(symbol: str) -> Signal:
    """
    Entry confirmation (M15): strong candle + breakout.
    BUY: last closed candle bullish and closes above prev high
    SELL: last closed candle bearish and closes below prev low
    """
    sym_u = symbol.upper()
    is_crypto_relaxed = symbol_uses_crypto_relaxed_m15(sym_u)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 3)  # last 2 closed
    if rates is None or len(rates) < 2:
        logging.warning("[%s] Not enough M15 data for signal.", symbol)
        return "NONE"

    last = rates[0]
    prev = rates[1]

    last_open = float(last["open"])
    last_close = float(last["close"])
    last_high = float(last["high"])
    last_low = float(last["low"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    prev_close = float(prev["close"])

    # Tier 1: strict close breakout
    if last_close > last_open and last_close > prev_high:
        return "BUY"
    if last_close < last_open and last_close < prev_low:
        return "SELL"

    # For FX/metals keep it strict (avoid over-trading).
    if not is_crypto_relaxed:
        return "NONE"

    # Tier 2: momentum breakout (wick breaks, close confirms momentum)
    if last_close > last_open and last_close > prev_close and last_high > prev_high:
        return "BUY"
    if last_close < last_open and last_close < prev_close and last_low < prev_low:
        return "SELL"

    # Tier 3 (crypto-friendly): BUY only — permite momentum alcista sin wick breakout;
    # no Tier 3 SELL (los cortos débiles en chop suelen penalizar).
    if last_close > last_open and last_close > prev_close:
        return "BUY"
    return "NONE"


def get_m15_closed_closes(symbol: str, count: int) -> Optional[list[float]]:
    """
    Devuelve los últimos ``count`` cierres de velas M15 **cerradas**, orden oldest→newest.
    Usa posición 1 en MT5 para omitir la vela en formación.
    """
    if count < 1:
        return None
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, count)
    if rates is None or len(rates) < count:
        return None
    return [float(r["close"]) for r in reversed(rates)]


def _m15_slope_trend(symbol: str, *, n_closes: int) -> Trend:
    need = max(int(n_closes), 2)
    seq = get_m15_closed_closes(symbol, need)
    if seq is None:
        return "FLAT"
    return price_slope_m15(seq, n=int(n_closes))


def _passes_candle_strength_filter(
    symbol: str,
    *,
    side: Signal,
    body_min_frac: float,
    wick_max_frac: float,
    require_confirmation: bool,
) -> tuple[bool, dict[str, float]]:
    """
    Evaluate last closed M15 candle "quality".
    Returns (ok, metrics) where metrics can be logged.
    """
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 3)  # last 2 closed (+ extra)
    if rates is None or len(rates) < 2:
        return True, {}

    last = rates[0]
    prev = rates[1]

    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])

    rng = max(0.0, h - l)
    if rng <= 0:
        return True, {}

    body = abs(c - o)
    upper_wick = max(0.0, h - max(o, c))
    lower_wick = max(0.0, min(o, c) - l)

    body_frac = body / rng
    upper_wick_frac = upper_wick / rng
    lower_wick_frac = lower_wick / rng

    # Directional sanity: make sure candle is aligned with the intended side.
    if side == "BUY" and c <= o:
        return False, {"body_frac": body_frac, "uw_frac": upper_wick_frac, "lw_frac": lower_wick_frac}
    if side == "SELL" and c >= o:
        return False, {"body_frac": body_frac, "uw_frac": upper_wick_frac, "lw_frac": lower_wick_frac}

    ok = (body_frac >= float(body_min_frac)) and (upper_wick_frac <= float(wick_max_frac)) and (lower_wick_frac <= float(wick_max_frac))

    if ok and require_confirmation:
        po = float(prev["open"])
        pc = float(prev["close"])
        if side == "BUY":
            ok = (pc > po) and (c > pc)
        else:
            ok = (pc < po) and (c < pc)

    return bool(ok), {"body_frac": body_frac, "uw_frac": upper_wick_frac, "lw_frac": lower_wick_frac}


def _range_filter_allows_entry(
    symbol: str,
    *,
    timeframe: str,
    lookback_bars: int,
    mid_frac: float,
) -> tuple[bool, dict[str, float]]:
    """
    Compute recent range on higher timeframe and block entries near the middle of the range.
    Returns (ok, metrics).
    """
    tf = _mt5_timeframe(timeframe)
    n = max(5, int(lookback_bars))
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, n)
    tick = mt5.symbol_info_tick(symbol)
    if rates is None or len(rates) < 5 or tick is None:
        return True, {}

    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    hi = max(highs)
    lo = min(lows)
    rng = max(0.0, hi - lo)
    if rng <= 0:
        return True, {}

    mid = (hi + lo) / 2.0
    price = float(getattr(tick, "bid", 0.0) or 0.0)
    if price <= 0:
        return True, {}

    dist_mid = abs(price - mid)
    dist_mid_frac = dist_mid / rng  # 0 at mid, 0.5 at extremes

    # Middle band width: mid_frac=0.30 => block when dist_mid_frac < 0.30 (middle 60% of range)
    ok = dist_mid_frac >= float(mid_frac)
    return bool(ok), {"range_hi": hi, "range_lo": lo, "range_mid": mid, "dist_mid_frac": dist_mid_frac}


def _range_filter_signal_snapshot(symbol: str, p: dict) -> dict[str, Any]:
    """Campos para evento SIGNAL: resultado del range filter y umbral efectivo."""
    mid_thr = float(p.get("range_filter_mid_frac", 0.30) or 0.30)
    if not bool(p.get("range_filter_enabled", False)):
        return {
            "range_filter_result": "disabled",
            "range_filter_dist_mid_frac": None,
            "range_filter_mid_frac": mid_thr,
        }
    ok, m = _range_filter_allows_entry(
        symbol,
        timeframe=str(p.get("range_filter_timeframe", "H1") or "H1"),
        lookback_bars=int(p.get("range_filter_lookback_bars", 24) or 24),
        mid_frac=mid_thr,
    )
    if not m:
        return {
            "range_filter_result": "passed",
            "range_filter_dist_mid_frac": None,
            "range_filter_mid_frac": mid_thr,
        }
    dist = m.get("dist_mid_frac")
    return {
        "range_filter_result": "blocked" if not ok else "passed",
        "range_filter_dist_mid_frac": float(dist) if dist is not None else None,
        "range_filter_mid_frac": mid_thr,
    }


def _ema(values: list[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / float(period)
    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))
    return float(ema)


def _ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """EMA value at each index (None until the series is warm)."""
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / float(period)
    out[period - 1] = float(ema)
    for i in range(period, n):
        ema = (float(values[i]) * k) + (ema * (1.0 - k))
        out[i] = float(ema)
    return out


def _xauusd_ema_cross_decision(
    ema_fast_prev: float,
    ema_slow_prev: float,
    ema_fast_curr: float,
    ema_slow_curr: float,
) -> Signal:
    """
    Edge puro: BUY solo si fast cruza arriba de slow; SELL si cruza abajo.
    Estado persistente (fast>slow sin cruce) → NONE.
    """
    prev_up = float(ema_fast_prev) > float(ema_slow_prev)
    curr_up = float(ema_fast_curr) > float(ema_slow_curr)
    prev_dn = float(ema_fast_prev) < float(ema_slow_prev)
    curr_dn = float(ema_fast_curr) < float(ema_slow_curr)
    if (not prev_up) and curr_up:
        return "BUY"
    if (not prev_dn) and curr_dn:
        return "SELL"
    return "NONE"


def _xauusd_compute_sl_tp_prices(
    *,
    side: str,
    entry: float,
    sl_dist: float,
    tp_dist: float,
    digits: int,
    spread_buffer: float = 0.0,
) -> Tuple[float, float]:
    """SL/TP en precio desde el fill (entry). Buffer opcional solo aleja el SL."""
    buf = max(0.0, float(spread_buffer))
    if side == "BUY":
        sl = float(entry) - float(sl_dist) - buf
        tp = float(entry) + float(tp_dist)
    else:
        sl = float(entry) + float(sl_dist) + buf
        tp = float(entry) - float(tp_dist)
    return round(sl, int(digits)), round(tp, int(digits))


def _xauusd_exit_distances_from_atr(
    atr: float,
    *,
    atr_mult: float = 1.5,
    tp_rr: float = 2.0,
    atr_min: float = 1.5,
) -> Tuple[float, float]:
    """Distancia SL/TP en precio a partir de ATR (testable, sin I/O)."""
    sl = max(float(atr_min), float(atr) * float(atr_mult))
    # Floor 1.5: RR 0.5 en live (Aug 16–17) exigía WR>67% y cerró negativo.
    tp = sl * max(1.5, float(tp_rr))
    return float(sl), float(tp)


def _xauusd_h1_bias_allows(side: str, price: float, h1_ema: float) -> bool:
    """True si el lado alinea con bias H1 (precio vs EMA)."""
    if side == "BUY":
        return float(price) > float(h1_ema)
    if side == "SELL":
        return float(price) < float(h1_ema)
    return False


def _xauusd_h4_bias_allows(side: str, trend_h4: str) -> bool:
    """
    True si el lado no pelea la marea H4.
    SELL bloqueado en UP; BUY bloqueado en DOWN; FLAT permite ambos.
    """
    t = str(trend_h4 or "").strip().upper()
    if side == "SELL":
        return t != "UP"
    if side == "BUY":
        return t != "DOWN"
    return False


def _xauusd_effective_tp_rr(cfg: Optional[Config] = None) -> float:
    raw = float(getattr(cfg, "xauusd_parallel_tp_rr", 2.0) or 2.0) if cfg is not None else 2.0
    return max(1.5, raw)


def _xauusd_effective_adx_min(cfg: Optional[Config] = None) -> float:
    raw = float(getattr(cfg, "xauusd_parallel_adx_min", _XAUUSD_ADX_MIN) or _XAUUSD_ADX_MIN) if cfg is not None else float(_XAUUSD_ADX_MIN)
    return max(22.0, raw)


def _xauusd_entry_mode(cfg: Optional[Config] = None) -> str:
    raw = str(getattr(cfg, "xauusd_parallel_entry_mode", "breakout") or "breakout").strip().lower() if cfg is not None else "breakout"
    return raw if raw in ("breakout", "scalp") else "breakout"


def _xauusd_should_flatten_now(cfg: Config, now: Optional[datetime] = None) -> bool:
    """True si hay que cerrar XAU paralelo (fuera de ventana o fin de semana)."""
    if not bool(getattr(cfg, "xauusd_parallel_flatten_outside_window", True)):
        return False
    wall = now or datetime.now()
    if _weekend_blocks_new_trade(cfg, _xauusd_parallel_symbol(cfg), wall):
        return True
    return not _xauusd_parallel_in_window(cfg, wall)


def _xauusd_exhaustion_skip_reason(
    *,
    side: str,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    atr: float,
    range_atr_mult: float = 1.5,
    extreme_frac: float = 0.20,
) -> Optional[str]:
    """
    Skip si la vela señal ya es un impulso > range_atr_mult×ATR y cierra en el
    extremo del lado de la operación (comprar el high / vender el low).
    """
    rng = float(bar_high) - float(bar_low)
    if atr is None or float(atr) <= 0 or rng <= 0:
        return None
    if rng <= float(atr) * float(range_atr_mult):
        return None
    s = str(side or "").strip().upper()
    frac = max(0.0, min(0.5, float(extreme_frac)))
    if s == "BUY" and (float(bar_high) - float(bar_close)) / rng <= frac:
        return "anti_chase_exhaustion"
    if s == "SELL" and (float(bar_close) - float(bar_low)) / rng <= frac:
        return "anti_chase_exhaustion"
    return None


def _xauusd_extension_skip_reason(
    *,
    side: str,
    price: Optional[float],
    ema: Optional[float],
    atr: float,
    max_atr: float = 1.0,
) -> Optional[str]:
    """Skip si el fill/precio ya está > max_atr×ATR lejos de la EMA de la señal."""
    if price is None or ema is None or atr is None or float(atr) <= 0:
        return None
    s = str(side or "").strip().upper()
    dist = float(price) - float(ema)
    cap = float(max_atr) * float(atr)
    if s == "BUY" and dist > cap:
        return "chase_extension"
    if s == "SELL" and dist < -cap:
        return "chase_extension"
    return None


def _xauusd_m1_spike_skip_reason(
    *,
    last_m1_range: Optional[float],
    atr: float,
    mult: float = 0.80,
) -> Optional[str]:
    """Skip si la última M1 cerrada es un spike vs ATR(M15)."""
    if last_m1_range is None or atr is None or float(atr) <= 0:
        return None
    if float(last_m1_range) > float(mult) * float(atr):
        return "m1_spike"
    return None


def _xauusd_live_bar_skip_reason(
    *,
    side: str,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    atr: float,
    price: Optional[float] = None,
    ema: Optional[float] = None,
    last_m1_range: Optional[float] = None,
    range_hard_mult: float = 2.0,
    exhaustion_atr_mult: float = 1.5,
    exhaustion_extreme_frac: float = 0.20,
    extension_atr_max: float = 1.0,
    m1_spike_atr_mult: float = 0.80,
) -> Optional[str]:
    """Orden: rango duro → agotamiento → extensión vs EMA → spike M1."""
    rng = float(bar_high) - float(bar_low)
    if atr is None or float(atr) <= 0 or rng <= 0:
        return None
    if rng > float(atr) * float(range_hard_mult):
        return "anti_chase"
    reason = _xauusd_exhaustion_skip_reason(
        side=side,
        bar_high=bar_high,
        bar_low=bar_low,
        bar_close=bar_close,
        atr=float(atr),
        range_atr_mult=exhaustion_atr_mult,
        extreme_frac=exhaustion_extreme_frac,
    )
    if reason:
        return reason
    reason = _xauusd_extension_skip_reason(
        side=side,
        price=price,
        ema=ema,
        atr=float(atr),
        max_atr=extension_atr_max,
    )
    if reason:
        return reason
    return _xauusd_m1_spike_skip_reason(
        last_m1_range=last_m1_range,
        atr=float(atr),
        mult=m1_spike_atr_mult,
    )


def _xauusd_breakout_skip_reason(
    *,
    adx: Optional[float],
    adx_min: float,
    signal_m15: str,
    ema_dir: str,
    trend_h4: str,
    slope: str,
    decision: str,
) -> Optional[str]:
    """Clasifica por qué generate_signal devolvió NONE (testable, sin I/O)."""
    if decision in ("BUY", "SELL"):
        return None
    if adx is None:
        return "data_missing"
    if float(adx) < float(adx_min):
        return "adx_flat"
    sig = str(signal_m15 or "NONE").upper()
    if sig == "NONE":
        return "no_breakout"
    ema = str(ema_dir or "FLAT").upper()
    if sig == "BUY" and ema != "UP":
        return "ema_mismatch"
    if sig == "SELL" and ema != "DOWN":
        return "ema_mismatch"
    h4 = str(trend_h4 or "FLAT").upper()
    if sig == "BUY" and h4 == "DOWN":
        return "h4_bias"
    if sig == "SELL" and h4 == "UP":
        return "h4_bias"
    sl = str(slope or "FLAT").upper()
    if sig == "BUY" and sl != "UP":
        return "slope"
    if sig == "SELL" and sl != "DOWN":
        return "slope"
    return "no_setup"


def _xauusd_parallel_sl_mode(cfg: Config) -> str:
    mode = str(getattr(cfg, "xauusd_parallel_sl_mode", "atr") or "atr").strip().lower()
    return mode if mode in ("atr", "money") else "atr"


def _xauusd_parallel_exit_distances(
    cfg: Config,
    symbol: str,
    lot: float,
    side: Signal,
) -> Optional[Tuple[float, float, dict[str, Any]]]:
    """
    Distancias SL/TP en unidades de precio para el parallel XAU.
    mode=atr: ATR(M15)*mult, TP = SL * rr
    mode=money: convierte money targets a distancia de precio
    """
    mode = _xauusd_parallel_sl_mode(cfg)
    meta: dict[str, Any] = {"mode": mode}
    if mode == "money":
        sl_money = float(getattr(cfg, "xauusd_parallel_sl_money", 2.00) or 2.00)
        tp_money = float(getattr(cfg, "xauusd_parallel_tp_money", 2.00) or 2.00)
        sl_dist = _price_distance_for_money(symbol, lot, sl_money, side)
        tp_dist = _price_distance_for_money(symbol, lot, tp_money, side)
        if sl_dist is None or tp_dist is None:
            return None
        meta.update({"sl_money": sl_money, "tp_money": tp_money, "sl_dist": float(sl_dist), "tp_dist": float(tp_dist)})
        return float(sl_dist), float(tp_dist), meta

    atr_period = max(2, int(getattr(cfg, "xauusd_parallel_atr_period", 14) or 14))
    atr_val = get_atr(symbol, mt5.TIMEFRAME_M15, atr_period)
    if atr_val is None or float(atr_val) <= 0:
        return None
    atr_mult = float(getattr(cfg, "xauusd_parallel_sl_atr_mult", 1.5) or 1.5)
    tp_rr = _xauusd_effective_tp_rr(cfg)
    atr_min = float(getattr(cfg, "xauusd_parallel_sl_atr_min", 1.5) or 1.5)
    sl_dist, tp_dist = _xauusd_exit_distances_from_atr(
        float(atr_val), atr_mult=atr_mult, tp_rr=tp_rr, atr_min=atr_min
    )
    meta.update(
        {
            "atr": round(float(atr_val), 5),
            "atr_period": atr_period,
            "atr_mult": atr_mult,
            "tp_rr": tp_rr,
            "sl_dist": round(sl_dist, 5),
            "tp_dist": round(tp_dist, 5),
        }
    )
    return sl_dist, tp_dist, meta


def get_ema(symbol: str, timeframe, period: int = 50) -> Optional[float]:
    """
    EMA on closed candles for the given timeframe.
    Uses start_pos=1 to avoid the currently forming candle.
    """
    count = max(period + 10, period)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) < period:
        logging.warning("[%s] Not enough data for EMA.", symbol)
        return None

    closes = [float(r["close"]) for r in rates]
    return _ema(closes, period)


def ema_filter(symbol: str, period: int = 50) -> Signal:
    ema = get_ema(symbol, mt5.TIMEFRAME_M15, period)
    tick = mt5.symbol_info_tick(symbol)
    if ema is None or tick is None:
        return "NONE"

    price = float(tick.bid)
    if price > ema:
        return "BUY"
    if price < ema:
        return "SELL"
    return "NONE"


def _is_market_active_m15(symbol: str, min_avg_range_points: float) -> Optional[bool]:
    """
    Volatility filter: average (high-low) range in last N closed M15 candles.
    Returns None if cannot compute.
    """
    if min_avg_range_points <= 0:
        return True

    info = mt5.symbol_info(symbol)
    if info is None or not info.point:
        return None

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 10)
    if rates is None or len(rates) < 5:
        return None

    ranges_points = [(float(r["high"]) - float(r["low"])) / float(info.point) for r in rates]
    avg_range_points = sum(ranges_points) / float(len(ranges_points))
    return avg_range_points >= min_avg_range_points


def get_adx(symbol: str, timeframe, period: int = 14) -> Optional[float]:
    """
    ADX Wilder (smoothed) on closed candles (anti-lateral filter).
    """
    count = max((2 * period) + 60, (2 * period) + 5)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) < (2 * period) + 5:
        return None

    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]

    r = adx_wilder(highs, lows, closes, period=period)
    v = r.adx[-1] if r.adx else None
    return float(v) if v is not None else None


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


def _last_closed_bar_ohlc(symbol: str, timeframe) -> Optional[dict[str, float]]:
    """OHLC of the last closed bar for `timeframe` (start_pos=1)."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)
    if rates is None or len(rates) < 1:
        return None
    last = rates[0]
    high = float(last["high"])
    low = float(last["low"])
    if high < low:
        return None
    return {
        "open": float(last["open"]),
        "high": high,
        "low": low,
        "close": float(last["close"]),
        "time": float(last["time"]),
    }


def get_last_m15_bar_range(symbol: str) -> Optional[float]:
    """
    Returns (high - low) of the most recent CLOSED M15 candle, in price units.
    """
    bar = _last_closed_bar_ohlc(symbol, mt5.TIMEFRAME_M15)
    if bar is None:
        return None
    rng = float(bar["high"]) - float(bar["low"])
    return rng if rng > 0 else None


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


def ema_trend(symbol: str, fast: int = 50, slow: int = 200) -> Trend:
    ema_fast = get_ema(symbol, mt5.TIMEFRAME_M15, fast)
    ema_slow = get_ema(symbol, mt5.TIMEFRAME_M15, slow)
    tick = mt5.symbol_info_tick(symbol)
    if ema_fast is None or ema_slow is None or tick is None:
        return "FLAT"

    price = float(tick.bid)
    if ema_fast > ema_slow and price > ema_fast:
        return "UP"
    if ema_fast < ema_slow and price < ema_fast:
        return "DOWN"
    return "FLAT"


def _price_distance_for_money(symbol: str, lot: float, money: float, direction: Signal) -> Optional[float]:
    """
    Approximate a price distance that would yield `money` profit (or loss) for `lot`.
    Uses symbol tick_value and tick_size as linear approximation:
      money ≈ (distance / tick_size) * tick_value * lot
      => distance ≈ money * tick_size / (tick_value * lot)
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)

    if tick_value <= 0 or tick_size <= 0 or lot <= 0:
        return None

    distance = abs(money) * tick_size / (tick_value * lot)

    # Round up to price step (point)
    point = float(info.point) if info.point else 0.0
    if point > 0:
        distance = math.ceil(distance / point) * point

    return distance


def _sl_price_for_max_loss_usd(
    symbol: str,
    side: Signal,
    entry_price: float,
    lot: float,
    loss_usd: float,
    *,
    digits: int,
) -> Optional[float]:
    """
    SL price such that, if hit, loss ≈ `loss_usd` (positive number) in account currency (linear model).
    BUY: SL below entry; SELL: SL above entry.
    """
    if entry_price <= 0 or lot <= 0 or loss_usd <= 0:
        return None
    d = _price_distance_for_money(symbol, lot, float(loss_usd), side)
    if d is None or d <= 0:
        return None
    if side == "BUY":
        return round(entry_price - d, int(digits))
    return round(entry_price + d, int(digits))


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


def _sl_price_to_lock_profit_usd(
    symbol: str,
    side: Signal,
    entry_price: float,
    lot: float,
    lock_profit_usd: float,
    *,
    digits: int,
) -> Optional[float]:
    """
    Price level for SL that would yield approximately `lock_profit_usd` if stopped out
    (linear tick-value model; same as `_price_distance_for_money`).
    BUY: SL above entry; SELL: SL below entry.
    """
    if entry_price <= 0 or lot <= 0 or lock_profit_usd <= 0:
        return None
    d = _price_distance_for_money(symbol, lot, float(lock_profit_usd), side)
    if d is None or d <= 0:
        return None
    if side == "BUY":
        return round(entry_price + d, int(digits))
    return round(entry_price - d, int(digits))


def _profit_usd_at_sl(
    symbol: str,
    side: Signal,
    entry_price: float,
    lot: float,
    sl_price: float,
    *,
    digits: int,
) -> Optional[float]:
    """
    Inversa lineal de `_sl_price_to_lock_profit_usd`: USD aproximados que representaría salir por ese SL
    (modelo tick_value/tick_size). Solo lado «ganancia» respecto de la entrada (SL ya mejor que BE puramente pérdida).
    """
    _ = digits
    info = mt5.symbol_info(symbol)
    if info is None or entry_price <= 0 or lot <= 0 or sl_price <= 0:
        return None
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    if tick_value <= 0 or tick_size <= 0:
        return None

    if side == "BUY":
        d = float(sl_price) - float(entry_price)
    else:
        d = float(entry_price) - float(sl_price)

    if d <= 0:
        return None

    money = (d / tick_size) * tick_value * lot
    return float(money)


def _build_sl_tp(symbol: str, side: Signal, lot: float, sl_money: float, tp_money: float) -> Tuple[Optional[float], Optional[float]]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        return None, None

    price = float(tick.ask if side == "BUY" else tick.bid)

    sl_dist = _price_distance_for_money(symbol, lot, abs(sl_money), side)
    tp_dist = _price_distance_for_money(symbol, lot, abs(tp_money), side)

    if sl_dist is None or tp_dist is None:
        # Fallback: no SL/TP if cannot compute safely
        return None, None

    if side == "BUY":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    digits = int(info.digits)
    return round(sl, digits), round(tp, digits)


def _build_sl_tp_points(symbol: str, side: Signal, sl_points: float, tp_points: float) -> Tuple[Optional[float], Optional[float]]:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None or not info.point:
        return None, None

    price = float(tick.ask if side == "BUY" else tick.bid)
    point = float(info.point)

    if sl_points <= 0 or tp_points <= 0:
        return None, None

    if side == "BUY":
        sl = price - (sl_points * point)
        tp = price + (tp_points * point)
    else:
        sl = price + (sl_points * point)
        tp = price - (tp_points * point)

    digits = int(info.digits)
    return round(sl, digits), round(tp, digits)


def _value_per_point_1lot(symbol: str) -> Optional[float]:
    """
    Estimated account-currency value of 1 point (info.point) for 1.0 lot.
    Uses trade_tick_value and trade_tick_size.
    """
    info = mt5.symbol_info(symbol)
    if info is None or not info.point:
        return None
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    point = float(info.point)
    if tick_value <= 0 or tick_size <= 0 or point <= 0:
        return None
    # value per point = tick_value * (point / tick_size)
    return tick_value * (point / tick_size)


def calculate_lot_by_risk_points(symbol: str, risk_pct: float, sl_points: float, base_lot: float) -> float:
    """
    Risk sizing using deterministic SL in points.
      risk_amount = balance * risk_pct
      loss_per_1lot ≈ sl_points * value_per_point_1lot
      lot = risk_amount / loss_per_1lot
    """
    acc = mt5.account_info()
    if acc is None:
        return _clamp_volume(symbol, base_lot)
    vpp = _value_per_point_1lot(symbol)
    if vpp is None or sl_points <= 0:
        return _clamp_volume(symbol, base_lot)

    balance = float(getattr(acc, "balance", 0.0) or 0.0)
    risk_amount = balance * (max(0.0, float(risk_pct)) / 100.0)
    loss_1lot = float(sl_points) * float(vpp)
    if loss_1lot <= 0 or risk_amount <= 0:
        return _clamp_volume(symbol, base_lot)

    lot = risk_amount / loss_1lot
    return _clamp_volume(symbol, lot)


def _position_magic_int(p) -> int:
    try:
        return int(getattr(p, "magic", -1) or -1)
    except (TypeError, ValueError):
        return -1


def _bot_trade_magics(cfg: Config) -> tuple[int, ...]:
    """Magic numbers whose deals se registran en trades.log (estrategia principal + paralelas)."""
    magics: list[int] = [int(cfg.magic)]
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        magics.append(int(getattr(cfg, "xauusd_parallel_magic", 0) or 0))
    return tuple(m for m in magics if m > 0)


def _deal_magic_matches_bot(d, cfg: Config) -> bool:
    return int(getattr(d, "magic", -1) or -1) in _bot_trade_magics(cfg)


def _strategy_label_for_magic(cfg: Config, magic: int) -> str:
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        if int(magic) == int(getattr(cfg, "xauusd_parallel_magic", 0) or 0):
            return "xauusd_parallel"
    if int(magic) == int(cfg.magic):
        return "main"
    return f"magic_{magic}"


def _xauusd_parallel_symbol(cfg: Config) -> str:
    return str(getattr(cfg, "xauusd_parallel_symbol", "XAUUSD") or "XAUUSD").strip().upper()


def _is_symbol_handled_by_xauusd_parallel(cfg: Config, symbol: str) -> bool:
    if not bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        return False
    return str(symbol or "").strip().upper() == _xauusd_parallel_symbol(cfg)


def _xauusd_parallel_positions(cfg: Config) -> list:
    """
    Todas las posiciones del magic XAU, uniendo filtro por símbolo + listado global.
    El filtro por símbolo solo puede devolver un subset (16 ago: 2 BUY solapadas
    con max_positions=1). El magic 260415 es exclusivo de esta pista.
    """
    magic = int(getattr(cfg, "xauusd_parallel_magic", 0) or 0)
    if magic <= 0:
        return []
    sym = _xauusd_parallel_symbol(cfg)
    by_ticket: dict[int, Any] = {}
    for src in (mt5.positions_get(symbol=sym), mt5.positions_get()):
        for p in src or ():
            if _position_magic_int(p) != magic:
                continue
            ticket = int(getattr(p, "ticket", 0) or 0)
            key = ticket if ticket > 0 else id(p)
            by_ticket[key] = p
    return list(by_ticket.values())


def _xauusd_hourly_clear_for_tests() -> None:
    """Reset in-memory hourly buckets (unit tests only)."""
    global _XAUUSD_HOURLY_DAY, _XAUUSD_HOURLY, _XAUUSD_LAST_SKIP_LOG
    global _XAUUSD_LAST_OPEN_AT, _XAUUSD_LAST_ENTRY_BAR_TIME, _XAUUSD_LAST_CROSS_BAR_TIME
    global _XAUUSD_LAST_SIGNAL, _XAUUSD_LAST_SKIP, _XAUUSD_SKIP_REASONS
    global _XAUUSD_CLOSE_TICKETS_SEEN, _XAUUSD_BROKER_CLOSE_DEALS_SEEN
    _XAUUSD_HOURLY_DAY = ""
    _XAUUSD_HOURLY = {}
    _XAUUSD_LAST_SKIP_LOG = {}
    _XAUUSD_LAST_OPEN_AT = None
    _XAUUSD_LAST_ENTRY_BAR_TIME = None
    _XAUUSD_LAST_CROSS_BAR_TIME = None
    _XAUUSD_LAST_SIGNAL = None
    _XAUUSD_LAST_SKIP = None
    _XAUUSD_SKIP_REASONS = {}
    _XAUUSD_CLOSE_TICKETS_SEEN = set()
    _XAUUSD_BROKER_CLOSE_DEALS_SEEN = set()


def _xauusd_hourly_reset_if_new_day(now: Optional[datetime] = None) -> None:
    """Start a fresh hourly map when the local calendar day changes."""
    global _XAUUSD_HOURLY_DAY, _XAUUSD_HOURLY
    wall = now or datetime.now()
    day = wall.strftime("%Y-%m-%d")
    if day != _XAUUSD_HOURLY_DAY:
        _XAUUSD_HOURLY_DAY = day
        _XAUUSD_HOURLY = {}


def _xauusd_hourly_bucket(hour: int) -> dict[str, Any]:
    h = max(0, min(23, int(hour)))
    bucket = _XAUUSD_HOURLY.get(h)
    if bucket is None:
        bucket = {
            "hour": h,
            "signals": 0,
            "opens": 0,
            "closes": 0,
            "skips": 0,
            "pnl": 0.0,
            "spread_sum": 0.0,
            "spread_n": 0,
        }
        _XAUUSD_HOURLY[h] = bucket
    return bucket


def _xauusd_record_hourly(
    kind: Literal["signal", "open", "close", "skip"],
    *,
    when: Optional[datetime] = None,
    profit: Optional[float] = None,
    spread_points: Optional[float] = None,
) -> None:
    """
    Acumula métricas por hora local para validar la ventana 10–13.
    - signal / open / skip: hora del evento
    - close: hora del cierre (puede caer fuera de ventana si el SL/TP se ejecuta después)
    """
    wall = when or datetime.now()
    _xauusd_hourly_reset_if_new_day(wall)
    bucket = _xauusd_hourly_bucket(wall.hour)
    if kind == "signal":
        bucket["signals"] = int(bucket["signals"]) + 1
    elif kind == "open":
        bucket["opens"] = int(bucket["opens"]) + 1
        if spread_points is not None:
            try:
                sp = float(spread_points)
            except (TypeError, ValueError):
                sp = None
            if sp is not None:
                bucket["spread_sum"] = float(bucket["spread_sum"]) + sp
                bucket["spread_n"] = int(bucket["spread_n"]) + 1
    elif kind == "close":
        bucket["closes"] = int(bucket["closes"]) + 1
        if profit is not None:
            try:
                bucket["pnl"] = round(float(bucket["pnl"]) + float(profit), 4)
            except (TypeError, ValueError):
                pass
    elif kind == "skip":
        bucket["skips"] = int(bucket.get("skips", 0) or 0) + 1


def _xauusd_hourly_snapshot(
    cfg: Optional[Config] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Snapshot serializable para bot_state.json / dashboard."""
    wall = now or datetime.now()
    _xauusd_hourly_reset_if_new_day(wall)
    start_h = int(getattr(cfg, "xauusd_parallel_start_hour", 10) or 10) if cfg is not None else 10
    end_h = int(getattr(cfg, "xauusd_parallel_end_hour", 13) or 13) if cfg is not None else 13

    by_hour: list[dict[str, Any]] = []
    totals = {
        "signals": 0,
        "opens": 0,
        "closes": 0,
        "skips": 0,
        "pnl": 0.0,
        "spread_sum": 0.0,
        "spread_n": 0,
    }
    in_window = {
        "signals": 0,
        "opens": 0,
        "closes": 0,
        "skips": 0,
        "pnl": 0.0,
        "spread_sum": 0.0,
        "spread_n": 0,
    }

    for h in sorted(_XAUUSD_HOURLY.keys()):
        b = _XAUUSD_HOURLY[h]
        spread_n = int(b["spread_n"])
        spread_avg = round(float(b["spread_sum"]) / spread_n, 1) if spread_n > 0 else None
        row = {
            "hour": int(h),
            "label": f"{int(h):02d}:00-{int(h):02d}:59",
            "signals": int(b["signals"]),
            "opens": int(b["opens"]),
            "closes": int(b["closes"]),
            "skips": int(b.get("skips", 0) or 0),
            "pnl": round(float(b["pnl"]), 4),
            "spread_avg": spread_avg,
            "in_window": (
                bool(_xauusd_parallel_in_window(cfg, wall.replace(hour=int(h), minute=0, second=0, microsecond=0)))
                if cfg is not None
                else bool(start_h <= int(h) < end_h)
            ),
        }
        by_hour.append(row)
        totals["signals"] += row["signals"]
        totals["opens"] += row["opens"]
        totals["closes"] += row["closes"]
        totals["skips"] += row["skips"]
        totals["pnl"] = round(float(totals["pnl"]) + float(row["pnl"]), 4)
        totals["spread_sum"] = float(totals["spread_sum"]) + float(b["spread_sum"])
        totals["spread_n"] += spread_n
        if row["in_window"]:
            in_window["signals"] += row["signals"]
            in_window["opens"] += row["opens"]
            in_window["closes"] += row["closes"]
            in_window["skips"] += row["skips"]
            in_window["pnl"] = round(float(in_window["pnl"]) + float(row["pnl"]), 4)
            in_window["spread_sum"] = float(in_window["spread_sum"]) + float(b["spread_sum"])
            in_window["spread_n"] += spread_n

    tot_spread_n = int(totals["spread_n"])
    totals_out = {
        "signals": int(totals["signals"]),
        "opens": int(totals["opens"]),
        "closes": int(totals["closes"]),
        "skips": int(totals["skips"]),
        "pnl": round(float(totals["pnl"]), 4),
        "spread_avg": round(float(totals["spread_sum"]) / tot_spread_n, 1) if tot_spread_n > 0 else None,
    }
    return {
        "date": _XAUUSD_HOURLY_DAY or now.strftime("%Y-%m-%d"),
        "by_hour": by_hour,
        "totals": totals_out,
        "window": {
            "start_hour": start_h,
            "end_hour": end_h,
            "label": _xauusd_parallel_window_label(cfg) if cfg is not None else f"{start_h:02d}:00–{end_h:02d}:00",
            "sessions": str(getattr(cfg, "xauusd_parallel_sessions", "") or "") if cfg is not None else "",
            "signals": int(in_window["signals"]),
            "opens": int(in_window["opens"]),
            "closes": int(in_window["closes"]),
            "skips": int(in_window["skips"]),
            "pnl": round(float(in_window["pnl"]), 4),
            "spread_avg": (
                round(float(in_window["spread_sum"]) / int(in_window["spread_n"]), 1)
                if int(in_window["spread_n"]) > 0
                else None
            ),
        },
    }


def _xauusd_log_skip(
    symbol: str,
    reason: str,
    *,
    when: Optional[datetime] = None,
    force: bool = False,
    count_hourly: bool = True,
    **extra: Any,
) -> None:
    """
    Emite XAUUSD_PARALLEL_SKIP (JSON) con cooldown por reason.
    Por defecto incrementa el contador horario de skips (aunque el log esté rate-limited).
    """
    global _XAUUSD_LAST_SKIP, _XAUUSD_SKIP_REASONS
    wall = when or datetime.now()
    if count_hourly:
        _xauusd_record_hourly("skip", when=wall)
    key = str(reason or "unknown")
    _XAUUSD_SKIP_REASONS[key] = int(_XAUUSD_SKIP_REASONS.get(key, 0) or 0) + 1
    _XAUUSD_LAST_SKIP = {
        "ts": wall.isoformat(timespec="seconds"),
        "reason": key,
        "hour": wall.hour,
        **{k: v for k, v in extra.items() if v is not None},
    }
    last = _XAUUSD_LAST_SKIP_LOG.get(key)
    if (
        not force
        and last is not None
        and (wall - last).total_seconds() < float(_XAUUSD_SKIP_LOG_COOLDOWN_SEC)
    ):
        return
    _XAUUSD_LAST_SKIP_LOG[key] = wall
    payload: dict[str, Any] = {
        "event": "XAUUSD_PARALLEL_SKIP",
        "ts": wall.isoformat(timespec="seconds"),
        "symbol": symbol,
        "reason": key,
        "hour": wall.hour,
    }
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    _log_event(payload)


def _xauusd_m15_bar_time(symbol: str) -> Optional[int]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 1)
    if rates is None or len(rates) < 1:
        return None
    try:
        return int(rates[0]["time"])
    except Exception:
        return None


def _xauusd_cooldown_remaining_sec(cfg: Config, now: Optional[datetime] = None) -> float:
    wall = now or datetime.now()
    cd = float(getattr(cfg, "xauusd_parallel_cooldown_seconds", 300.0) or 0.0)
    if cd <= 0 or _XAUUSD_LAST_OPEN_AT is None:
        return 0.0
    elapsed = (wall - _XAUUSD_LAST_OPEN_AT).total_seconds()
    return max(0.0, cd - elapsed)


def _xauusd_record_soft_close(
    *,
    ticket: int,
    profit: float,
    when: Optional[datetime] = None,
    closed: bool,
) -> None:
    """Cuenta un close del manage() solo si el cierre a mercado confirmó OK."""
    if not closed or ticket <= 0:
        return
    if ticket in _XAUUSD_CLOSE_TICKETS_SEEN:
        return
    _XAUUSD_CLOSE_TICKETS_SEEN.add(int(ticket))
    _xauusd_record_hourly("close", when=when or datetime.now(), profit=float(profit))


def _xauusd_ingest_broker_closes(cfg: Config) -> None:
    """
    Atribuye PnL horario a cierres del broker (SL/TP) del magic paralelo.
    Cubre el caso en que TRADE_CLOSE no corrió o el deal salió con magic distinto.
    """
    if not bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        return
    magic = int(getattr(cfg, "xauusd_parallel_magic", 0) or 0)
    sym = _xauusd_parallel_symbol(cfg)
    if magic <= 0:
        return
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(start, now + timedelta(seconds=2))
    if not deals:
        return
    for d in deals:
        if int(getattr(d, "entry", -1) or -1) != int(mt5.DEAL_ENTRY_OUT):
            continue
        deal_magic = int(getattr(d, "magic", -1) or -1)
        deal_sym = str(getattr(d, "symbol", "") or "").strip().upper()
        if deal_magic != magic and not (deal_magic == 0 and deal_sym == sym):
            continue
        if deal_sym and deal_sym != sym:
            continue
        tid = int(getattr(d, "ticket", 0) or 0)
        if tid <= 0 or tid in _XAUUSD_BROKER_CLOSE_DEALS_SEEN:
            continue
        # Evitar doble conteo si ya lo registró manage() con el position ticket.
        pos_id = int(getattr(d, "position_id", 0) or 0)
        if pos_id > 0 and pos_id in _XAUUSD_CLOSE_TICKETS_SEEN:
            _XAUUSD_BROKER_CLOSE_DEALS_SEEN.add(tid)
            continue
        _XAUUSD_BROKER_CLOSE_DEALS_SEEN.add(tid)
        if pos_id > 0:
            _XAUUSD_CLOSE_TICKETS_SEEN.add(pos_id)
        profit = float(getattr(d, "profit", 0.0) or 0.0)
        swap = float(getattr(d, "swap", 0.0) or 0.0)
        comm = float(getattr(d, "commission", 0.0) or 0.0)
        net = profit + swap + comm
        t_close = int(getattr(d, "time", 0) or 0)
        when = datetime.fromtimestamp(t_close) if t_close > 0 else now
        _xauusd_record_hourly("close", when=when, profit=net)
        _log_event(
            {
                "event": "XAUUSD_PARALLEL_BROKER_CLOSE",
                "ts": now.isoformat(timespec="seconds"),
                "symbol": sym,
                "deal_ticket": tid,
                "position_id": pos_id or None,
                "net": round(net, 4),
                "profit": round(profit, 4),
                "magic": deal_magic,
                "hour": when.hour,
                "closed_at": when.isoformat(timespec="seconds"),
            }
        )


def _xauusd_open_snapshot(cfg: Config) -> dict[str, Any]:
    """Estado live de la(s) posición(es) paralelas para el dashboard."""
    positions = _xauusd_parallel_positions(cfg)
    if not positions:
        return {
            "count": 0,
            "float_pnl": 0.0,
            "side": None,
            "ticket": None,
            "price_open": None,
            "sl": None,
            "tp": None,
        }
    p = positions[0]
    side = _position_side(p)
    return {
        "count": len(positions),
        "float_pnl": round(sum(float(getattr(x, "profit", 0.0) or 0.0) for x in positions), 4),
        "side": side,
        "ticket": int(getattr(p, "ticket", 0) or 0) or None,
        "price_open": float(getattr(p, "price_open", 0.0) or 0.0) or None,
        "sl": float(getattr(p, "sl", 0.0) or 0.0) or None,
        "tp": float(getattr(p, "tp", 0.0) or 0.0) or None,
    }


def _position_is_managed_by_bot(p, cfg: Config) -> bool:
    """
    Posiciones que el bot debe trailing/BE (mismo magic, o magic 0 si está habilitado).
    Solo símbolos listados en cfg.symbols.
    Las posiciones de la estrategia XAUUSD paralela (magic dedicado) se excluyen aquí.
    """
    sym = getattr(p, "symbol", None)
    if not sym or sym not in cfg.symbols:
        return False
    m = _position_magic_int(p)
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        par_sym = _xauusd_parallel_symbol(cfg)
        par_magic = int(getattr(cfg, "xauusd_parallel_magic", 0) or 0)
        if str(sym).upper() == par_sym and m == par_magic:
            return False
    if m == int(cfg.magic):
        return True
    if m == 0 and bool(getattr(cfg, "track_zero_magic_positions", True)):
        return True
    return False


def _has_any_open_position_on_symbol(symbol: str) -> bool:
    """Evita abrir otra operación market en el mismo símbolo si ya hay cualquier posición abierta."""
    pos = mt5.positions_get(symbol=symbol)
    if pos is None:
        return False
    return len(pos) > 0


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


def _groups_for_symbol(cfg: Config, symbol: str) -> list[str]:
    """Return the list of correlation-group names the symbol belongs to."""
    if not cfg.correlation_groups:
        return []
    out: list[str] = []
    for gname, members in cfg.correlation_groups.items():
        if symbol in members:
            out.append(gname)
    return out


def _count_open_in_group(cfg: Config, group_name: str) -> int:
    """Count managed-by-bot OPEN positions whose symbol is in group `group_name`."""
    members = cfg.correlation_groups.get(group_name, ())
    if not members:
        return 0
    pos = mt5.positions_get()
    if pos is None:
        return 0
    memberset = set(members)
    n = 0
    for p in pos:
        if getattr(p, "symbol", None) in memberset and _position_is_managed_by_bot(p, cfg):
            n += 1
    return n


def _group_exposure(cfg: Config) -> dict[str, dict]:
    """
    Return { group_name: {members, cap, open_count, blocked} } for observability.
    Cheap enough to call on every open attempt / dashboard refresh.
    """
    if not cfg.correlation_groups:
        return {}
    cap = int(cfg.max_positions_per_group or 0)
    out: dict[str, dict] = {}
    for gname, members in cfg.correlation_groups.items():
        n = _count_open_in_group(cfg, gname)
        out[gname] = {
            "members": list(members),
            "cap": cap,
            "open_count": n,
            "blocked": (cap > 0 and n >= cap),
        }
    return out


def _parse_correlation_groups(raw: Optional[str]) -> dict[str, tuple[str, ...]]:
    """
    Parse BOT_CORRELATION_GROUPS env var. Accepts JSON like:
        {"USD_BASE":["GBPUSD","USDJPY","NZDJPY","XAUUSD"],"CRYPTO":["BTCUSD","ETHUSD"]}
    Returns {} on empty / invalid input (and logs a warning).
    """
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        logging.warning("BOT_CORRELATION_GROUPS: JSON inválido, ignorando. Valor=%r", raw)
        return {}
    if not isinstance(data, dict):
        logging.warning("BOT_CORRELATION_GROUPS: se esperaba objeto {group: [symbols]}.")
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for k, v in data.items():
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[str(k)] = tuple(str(x).strip() for x in v if str(x).strip())
    return out


def _startup_reconciliation(cfg: Config) -> dict[str, int]:
    """
    Tras un corte / reinicio: inventario de posiciones y órdenes pendientes en los símbolos del bot.
    Opcionalmente cancela pendientes con nuestro magic (BOT_CANCEL_ORPHAN_PENDING_ON_START=1).
    Incluye el símbolo de la estrategia XAUUSD paralela cuando está activa.
    """
    symset = set(cfg.symbols)
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        symset.add(_xauusd_parallel_symbol(cfg))
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
    # history_deals_get expects a range
    deals = mt5.history_deals_get(start, now + timedelta(seconds=1))
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
    deals = mt5.history_deals_get(start, now + timedelta(seconds=1))
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
    Incluye magic principal y paralelo (XAUUSD).
    """
    global _TELEGRAM_SEEN_OUT_DEAL_TICKETS, _TELEGRAM_CLOSE_TRACKER_SEEDED
    if _TELEGRAM_CLOSE_TRACKER_SEEDED:
        return
    now = datetime.now()
    start = now - timedelta(days=2)
    deals = mt5.history_deals_get(start, now + timedelta(seconds=2))
    if deals:
        for d in deals:
            if not _deal_magic_matches_bot(d, cfg):
                continue
            if int(getattr(d, "entry", -1) or -1) != int(mt5.DEAL_ENTRY_OUT):
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
    wall = now or datetime.now()
    lines = [
        "meta-trader: conectado",
        f"PID {pid}",
        f"Loop: {', '.join(cfg.symbols)} · magic {int(cfg.magic)}",
    ]
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        sym = _xauusd_parallel_symbol(cfg)
        in_win = _xauusd_parallel_in_window(cfg, wall)
        win_state = "ABIERTA" if in_win else "CERRADA"
        lines.append(
            f"XAUUSD parallel: ON · {sym} · {_xauusd_entry_mode(cfg)} · magic {int(cfg.xauusd_parallel_magic)} · "
            f"{_xauusd_parallel_window_label(cfg)} local ({win_state})"
        )
        mode = _xauusd_parallel_sl_mode(cfg)
        adx_min = float(getattr(cfg, "xauusd_parallel_adx_min", _XAUUSD_ADX_MIN) or _XAUUSD_ADX_MIN)
        h1_on = "sí" if bool(getattr(cfg, "xauusd_parallel_h1_filter", True)) else "no"
        h4_on = "sí" if bool(getattr(cfg, "xauusd_parallel_h4_filter", True)) else "no"
        if mode == "atr":
            exits = (
                f"ATR×{float(cfg.xauusd_parallel_sl_atr_mult):.2f} · "
                f"RR {float(cfg.xauusd_parallel_tp_rr):.2f}"
            )
        else:
            exits = (
                f"TP/SL ${float(cfg.xauusd_parallel_tp_money):.2f}/"
                f"${float(cfg.xauusd_parallel_sl_money):.2f}"
            )
        lines.append(
            f"  {exits} · ADX≥{adx_min:.0f} · H1={h1_on} · H4={h4_on} · "
            f"lot {float(cfg.xauusd_parallel_lot):.2f} · "
            f"cooldown {int(cfg.xauusd_parallel_cooldown_seconds)}s · "
            f"1/vela={'sí' if bool(cfg.xauusd_parallel_one_trade_per_bar) else 'no'}"
        )
    else:
        lines.append("XAUUSD parallel: OFF")

    rows = list(recon.get("positions") or [])
    par_magic = (
        int(getattr(cfg, "xauusd_parallel_magic", 0) or 0)
        if bool(getattr(cfg, "xauusd_parallel_enabled", False))
        else -999
    )
    n_par = sum(1 for r in rows if int(r.get("magic") or -1) == par_magic)
    n_loop = max(0, len(rows) - n_par)
    lines.append(
        f"Inventario: loop={n_loop} (gestionadas={int(recon.get('positions_managed') or 0)}) · "
        f"xauusd={n_par} · pendientes={int(recon.get('pending_orders') or 0)}"
    )
    return "\n".join(lines)


def _telegram_notify_new_closes(cfg: Config) -> None:
    """Avisa por Telegram cierres nuevos (historial DEAL_ENTRY_OUT, magics del bot)."""
    if not telegram_notify.telegram_configured():
        return
    if not telegram_notify.env_bool("TELEGRAM_NOTIFY_TRADE_CLOSE", True):
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

    for d in deals:
        if not _deal_magic_matches_bot(d, cfg):
            continue
        if int(getattr(d, "entry", -1) or -1) != int(mt5.DEAL_ENTRY_OUT):
            continue
        t = int(getattr(d, "ticket", 0) or 0)
        if t <= 0 or t in _TELEGRAM_SEEN_OUT_DEAL_TICKETS:
            continue
        _TELEGRAM_SEEN_OUT_DEAL_TICKETS.add(t)

        sym = str(getattr(d, "symbol", "") or "?")
        deal_magic = int(getattr(d, "magic", -1) or -1)
        strategy = _strategy_label_for_magic(cfg, deal_magic)
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
        strat_txt = "XAUUSD scalper" if strategy == "xauusd_parallel" else "loop principal"

        msg = (
            f"meta-trader: posición cerrada ({strat_txt})\n"
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
        ins = [x for x in dl if int(getattr(x, "entry", -1) or -1) == int(mt5.DEAL_ENTRY_IN)]
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
        and int(getattr(x, "entry", -1) or -1) == int(mt5.DEAL_ENTRY_IN)
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


def _log_new_closes(cfg: Config) -> None:
    """
    Emite eventos estructurados TRADE_CLOSE para cada deal DEAL_ENTRY_OUT nuevo con nuestro magic.
    Cubre cierres hechos por el bot y también SL/TP ejecutados por el servidor, que antes quedaban
    fuera del log.
    """
    global _CLOSE_LOG_BACKFILL_DONE
    _seed_close_log_tracker(cfg)

    now = datetime.now()
    end = now + timedelta(seconds=2)
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

    for d in deals:
        if not _deal_magic_matches_bot(d, cfg):
            continue
        if int(getattr(d, "entry", -1) or -1) != int(mt5.DEAL_ENTRY_OUT):
            continue
        t = int(getattr(d, "ticket", 0) or 0)
        if t <= 0 or t in _CLOSE_LOG_SEEN_DEAL_TICKETS:
            continue
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
        deal_magic = int(getattr(d, "magic", 0) or 0)
        strategy = _strategy_label_for_magic(cfg, deal_magic)
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
            in_deals = [x for x in by_pos[pos_id] if int(getattr(x, "entry", -1) or -1) == int(mt5.DEAL_ENTRY_IN)]
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

        _log_event(
            {
                "event": "TRADE_CLOSE",
                "ts": datetime.now().isoformat(timespec="seconds"),
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
                "opened_at": opened_at,
                "closed_at": closed_at,
                "duration_seconds": duration_seconds,
                "backfilled": is_backfill,
                "entry_grade": grades.get("entry_grade"),
                "exit_grade": grades.get("exit_grade"),
                "trade_grade": grades.get("trade_grade"),
                "magic": deal_magic,
                "strategy": strategy,
            }
        )

        # PnL horario XAUUSD: atribuir a la hora del cierre (incluye SL/TP del broker).
        if strategy == "xauusd_parallel" and not is_backfill:
            if t not in _XAUUSD_BROKER_CLOSE_DEALS_SEEN:
                _XAUUSD_BROKER_CLOSE_DEALS_SEEN.add(t)
                if pos_id > 0:
                    _XAUUSD_CLOSE_TICKETS_SEEN.add(pos_id)
                close_when = datetime.fromtimestamp(time_close) if time_close > 0 else now
                _xauusd_record_hourly("close", when=close_when, profit=net)

    # After the first successful pass we revert to the short lookback window.
    _CLOSE_LOG_BACKFILL_DONE = True


def _open_sl_at_entry_retry_retcodes() -> frozenset[int]:
    """Retcodes where nudging SL by extra_points may fix broker stop/freeze rules."""
    return frozenset(
        c
        for c in (
            int(getattr(mt5, "TRADE_RETCODE_INVALID_STOPS", 10016)),
            int(getattr(mt5, "TRADE_RETCODE_INVALID_PRICE", 10015)),
            int(getattr(mt5, "TRADE_RETCODE_OFF_QUOTES", 10014)),
            int(getattr(mt5, "TRADE_RETCODE_REQUOTE", 10004)),
        )
        if c > 0
    )


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


def open_trade(cfg: Config, symbol: str, side: Signal) -> bool:
    if side not in ("BUY", "SELL"):
        return False

    if not _ensure_symbol(symbol):
        return False

    p = _effective_params(cfg, symbol)

    allowed, reason = _can_open_additional_position(cfg, symbol, side, p)
    if not allowed:
        logging.info("[%s] Skipping new entry: %s.", symbol, reason)
        return False

    open_total = _count_managed_open_positions(cfg)
    if open_total >= cfg.max_open_trades:
        logging.info("[%s] Max open trades reached (%d/%d). Skipping.", symbol, open_total, cfg.max_open_trades)
        return False

    # --- Correlation / exposure cap per group (Way of the Turtle Ch.8) ---
    group_cap = int(cfg.max_positions_per_group or 0)
    if group_cap > 0 and cfg.correlation_groups:
        groups = _groups_for_symbol(cfg, symbol)
        for gname in groups:
            n_in_group = _count_open_in_group(cfg, gname)
            if n_in_group >= group_cap:
                logging.info(
                    "[%s] Correlation cap: group %r has %d/%d open positions. Skipping.",
                    symbol, gname, n_in_group, group_cap,
                )
                _log_event(
                    {
                        "event": "CORRELATION_SKIP",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "side": side,
                        "group": gname,
                        "members": list(cfg.correlation_groups.get(gname, ())),
                        "open_in_group": n_in_group,
                        "cap": group_cap,
                    }
                )
                return False

    # --- Hourly open rate limit (rolling 60m; cuenta solo aperturas exitosas registradas en este proceso) ---
    cap_h = int(p.get("max_trades_per_hour_per_symbol", 0) or 0)
    if cap_h > 0:
        now_h = datetime.now()
        n_h = _count_opens_in_window(symbol, now_h, 3600.0)
        if n_h >= cap_h:
            logging.info(
                "[%s] Hourly rate limit: %d opens in last 60m (cap=%d). Skipping.",
                symbol,
                n_h,
                cap_h,
            )
            _log_event(
                {
                    "event": "RATE_LIMIT_SKIP",
                    "ts": now_h.isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "side": side,
                    "opens_last_60m": n_h,
                    "cap": cap_h,
                }
            )
            return False

    # --- A) Anti-chase / exhaustion candle filter ---
    # Skip entries when the signal bar (last closed M15) is abnormally large vs ATR.
    if bool(p.get("anti_chase_enabled", False)):
        atr_period = int(p.get("anti_chase_atr_period", 20) or 20)
        atr_mult = float(p.get("anti_chase_atr_mult", 2.0) or 2.0)
        bar_range = get_last_m15_bar_range(symbol)
        atr_val = get_atr(symbol, mt5.TIMEFRAME_M15, atr_period)
        if bar_range is not None and atr_val is not None and atr_val > 0:
            threshold = atr_val * atr_mult
            if bar_range > threshold:
                ratio = bar_range / atr_val
                logging.info(
                    "[%s] Anti-chase: signal bar range %.5f > %.2fx ATR(%d) %.5f (ratio=%.2f). Skipping.",
                    symbol, bar_range, atr_mult, atr_period, atr_val, ratio,
                )
                _log_event(
                    {
                        "event": "BAR_EXHAUSTION_SKIP",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "side": side,
                        "bar_range": round(bar_range, 6),
                        "atr": round(atr_val, 6),
                        "atr_period": atr_period,
                        "atr_mult": atr_mult,
                        "ratio": round(ratio, 3),
                    }
                )
                return False
    sl_money = float(p["sl_money"])
    tp_money = float(p["tp_money"])
    sl_points = float(p["sl_points"])
    tp_points = float(p["tp_points"])
    max_spread_points = float(p["max_spread_points"])

    ok_spread, spread, avg_spread = _is_spread_ok(cfg, symbol, max_spread_points=max_spread_points)
    if not ok_spread:
        if spread is None:
            logging.warning("[%s] Could not read spread. Skipping.", symbol)
        else:
            if cfg.spread_dynamic_enabled and avg_spread is not None:
                logging.info(
                    "[%s] Spread not OK (now=%.1f pts, avg=%.1f pts, mult=%.2f, hard_max=%.1f). Skipping.",
                    symbol,
                    spread,
                    avg_spread,
                    cfg.spread_dynamic_mult,
                    max_spread_points,
                )
            else:
                logging.info("[%s] Spread too high (%.1f pts > %.1f pts). Skipping.", symbol, spread, max_spread_points)
        return False

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        logging.error("[%s] Missing tick/info.", symbol)
        return False

    term = mt5.terminal_info()
    if term is not None and hasattr(term, "trade_allowed") and not bool(getattr(term, "trade_allowed", True)):
        logging.error("[%s] Terminal trade not allowed (enable Algo Trading in MT5). Skipping.", symbol)
        return False
    if hasattr(info, "trade_mode") and int(getattr(info, "trade_mode") or 0) == int(getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", -1)):
        logging.error("[%s] Symbol trade mode disabled by broker. Skipping.", symbol)
        return False

    risk_pct_base = float(p["risk_per_trade_pct"])
    port_w = _portfolio_weight_for(cfg, symbol)
    risk_pct_eff = risk_pct_base * max(0.0, port_w) if bool(cfg.portfolio_enabled) else risk_pct_base

    # --- ATR-based SL/TP override (Turtle-style, volatility-adaptive) ---
    # When enabled, recompute sl_points / tp_points from ATR at entry. The rest of
    # the flow (lot sizing, _build_sl_tp_points) stays unchanged. On ATR failure
    # we keep the fixed points as a safe fallback.
    sl_tp_mode = "fixed"
    atr_at_entry: Optional[float] = None
    if bool(p.get("sl_tp_atr_enabled", False)):
        tf_str = str(p.get("sl_tp_atr_timeframe", "M15"))
        tf = _mt5_timeframe(tf_str)
        atr_period = int(p.get("sl_tp_atr_period", 14) or 14)
        sl_mult = float(p.get("sl_atr_mult", 2.0) or 2.0)
        tp_mult = float(p.get("tp_atr_mult", 4.0) or 4.0)
        min_pts = float(p.get("sl_tp_atr_min_points", 0.0) or 0.0)
        atr_val = get_atr(symbol, tf, atr_period)
        point = float(getattr(info, "point", 0.0) or 0.0)
        if atr_val is not None and atr_val > 0 and point > 0 and sl_mult > 0 and tp_mult > 0:
            new_sl_pts = (atr_val * sl_mult) / point
            new_tp_pts = (atr_val * tp_mult) / point
            if min_pts > 0 and new_sl_pts < min_pts:
                scale = min_pts / new_sl_pts
                new_sl_pts = min_pts
                new_tp_pts = new_tp_pts * scale
            sl_points = float(new_sl_pts)
            tp_points = float(new_tp_pts)
            atr_at_entry = float(atr_val)
            sl_tp_mode = f"atr({tf_str.upper()},p={atr_period},sl*{sl_mult:g},tp*{tp_mult:g})"
            logging.info(
                "[%s] SL/TP from ATR(%s,%d)=%.5f → sl_pts=%.0f, tp_pts=%.0f (mult sl=%.2f tp=%.2f).",
                symbol, tf_str.upper(), atr_period, atr_val, sl_points, tp_points, sl_mult, tp_mult,
            )
        else:
            logging.warning(
                "[%s] ATR SL/TP enabled but ATR invalid (atr=%s, point=%s). Fallback to fixed sl=%.0f tp=%.0f.",
                symbol, atr_val, point, sl_points, tp_points,
            )

    # Lote: override fijo por símbolo (ej. XNGUSD min 0.1) → risk sizing → clamp broker.
    fixed_lot = float(p.get("lot", 0.0) or 0.0)
    lot = float(fixed_lot) if fixed_lot > 0 else float(cfg.lot)
    if bool(p["use_risk_sizing"]) and fixed_lot <= 0:
        if sl_points > 0:
            lot = calculate_lot_by_risk_points(symbol, float(risk_pct_eff), sl_points, cfg.lot)
        else:
            lot = calculate_lot_by_risk(symbol, float(risk_pct_eff), sl_money, cfg.lot)
    lot = _clamp_volume(symbol, float(lot))

    if bool(cfg.regime_lot_sizing_enabled):
        lab = str(_LAST_REGIME_BY_SYMBOL.get(symbol, "") or "UNCERTAIN").strip().upper()
        mult = float(cfg.regime_lot_multiplier_by_label.get(lab, 1.0))
        if mult > 0 and abs(mult - 1.0) > 1e-9:
            lot = _clamp_volume(symbol, float(lot) * mult)
            logging.info("[%s] Regime lot mult label=%s x%.4f → lot=%.4f", symbol, lab, mult, lot)

    ml_cap_sym = float(p.get("max_lot_per_trade", 0.0) or 0.0)
    ml_cap_cfg = float(getattr(cfg, "max_lot_per_trade", 0.0) or 0.0)
    ml_cap = ml_cap_sym if ml_cap_sym > 0 else ml_cap_cfg
    if ml_cap > 0:
        clipped = min(float(lot), ml_cap)
        if clipped + 1e-9 < float(lot):
            logging.info("[%s] max_lot_per_trade cap %.4f → %.4f", symbol, lot, clipped)
            lot = _clamp_volume(symbol, clipped)

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price = float(tick.ask if side == "BUY" else tick.bid)

    if sl_points > 0 and tp_points > 0:
        sl, tp = _build_sl_tp_points(symbol, side, sl_points, tp_points)
    else:
        sl, tp = _build_sl_tp(symbol, side, lot, sl_money, tp_money)

    ladder_on = bool(cfg.usd_sl_ladder_enabled)
    ladder_init = float(p.get("usd_sl_ladder_initial_sl_money", cfg.usd_sl_ladder_initial_sl_money) or 0.8)
    if ladder_on:
        # Margen inicial ~-$X USD desde el precio de entrada (evita SL en el precio = cierre instantáneo).
        sl_usd = _sl_price_for_max_loss_usd(symbol, side, price, lot, ladder_init, digits=int(info.digits))
        if sl_usd is not None:
            sl = sl_usd
        # Runner: sin TP fijo en bróker; el techo lo da el mercado y la escalera sigue subiendo el SL.
        tp = None

    account = mt5.account_info()
    balance = float(account.balance) if account else 0.0
    risk_efectivo_pct = (ladder_init / balance * 100) if balance > 0 else 0.0
    logging.info(
        "[%s] Sizing: lot=%.4f | sl_atr_pts=%.0f | ladder_initial_sl=$%.2f | risk_efectivo_pct=%.2f%%",
        symbol,
        lot,
        sl_points,
        ladder_init,
        risk_efectivo_pct,
    )

    tp_target = tp
    digits = int(info.digits)
    point = float(getattr(info, "point", 0.0) or 0.0)
    open_sl_active = bool(p.get("open_sl_at_entry", False)) and sl is not None and (not ladder_on)

    filling_candidates = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
    ]

    result: Any = None
    open_sl_extra_used: Optional[int] = None
    execution_price = float(price)

    if open_sl_active:
        if point <= 0:
            logging.error("[%s] open_sl_at_entry: symbol point is zero.", symbol)
            return False
        max_extra = max(
            0,
            int(
                p.get(
                    "open_sl_at_entry_max_extra_points",
                    getattr(cfg, "open_sl_at_entry_max_extra_points", 400),
                )
            ),
        )
        min_cfg = max(
            0,
            int(
                float(
                    p.get(
                        "open_sl_at_entry_min_extra_points",
                        getattr(cfg, "open_sl_at_entry_min_extra_points", 0),
                    )
                )
            ),
        )
        extra_broker = _broker_min_stop_extra_points(symbol)
        extra_start = max(min_cfg, extra_broker, 0)
        if extra_start > max_extra:
            logging.error(
                "[%s] open_sl_at_entry: min extra points %d (cfg=%d broker>=%d) > max %d. Skipping.",
                symbol,
                extra_start,
                min_cfg,
                extra_broker,
                max_extra,
            )
            return False
        retry_codes = _open_sl_at_entry_retry_retcodes()
        last_res: Any = None
        for idx, extra in enumerate(range(extra_start, max_extra + 1)):
            tick2 = mt5.symbol_info_tick(symbol)
            if tick2 is None:
                logging.error("[%s] open_sl_at_entry: no tick.", symbol)
                return False
            execution_price = float(tick2.ask if side == "BUY" else tick2.bid)
            if side == "BUY":
                sl_try = round(execution_price - extra * point, digits)
            else:
                sl_try = round(execution_price + extra * point, digits)
            tp_try = tp_target
            if tp_try is not None:
                if side == "BUY" and float(tp_try) <= sl_try:
                    tp_try = None
                elif side == "SELL" and float(tp_try) >= sl_try:
                    tp_try = None
            request: dict = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": execution_price,
                "deviation": cfg.deviation_points,
                "magic": cfg.magic,
                "comment": "meta-trader-bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "sl": sl_try,
            }
            if tp_try is not None:
                request["tp"] = tp_try

            if idx == 0:
                logging.info(
                    "[%s] Opening %s | lot=%.2f | price=%.*f | spread=%.1f pts | open_sl_at_entry try extra=%d (start=%d) → SL=%.*f TP=%s",
                    symbol,
                    side,
                    lot,
                    digits,
                    execution_price,
                    spread if spread is not None else 0.0,
                    extra,
                    extra_start,
                    digits,
                    sl_try,
                    f"{tp_try:.{digits}f}" if tp_try is not None else "None",
                )
            last_res = _order_send_deal_try_fillings(symbol, request, filling_candidates)
            if last_res is None:
                _record_failed_order_attempt(symbol, cfg, None)
                return False
            rc = int(getattr(last_res, "retcode", -1))
            if rc == int(mt5.TRADE_RETCODE_DONE):
                sl, tp = sl_try, tp_try
                open_sl_extra_used = extra
                result = last_res
                if extra > 0:
                    logging.info(
                        "[%s] open_sl_at_entry: broker accepted SL with extra_points=%d SL=%.*f (price=%.*f)",
                        symbol,
                        extra,
                        digits,
                        sl_try,
                        digits,
                        execution_price,
                    )
                break
            if rc not in retry_codes:
                logging.error(
                    "[%s] open_sl_at_entry: order failed (not retryable). retcode=%s | comment=%s | extra=%d",
                    symbol,
                    rc,
                    getattr(last_res, "comment", ""),
                    extra,
                )
                _record_failed_order_attempt(symbol, cfg, last_res)
                return False
            logging.warning(
                "[%s] open_sl_at_entry: retcode=%s extra_points=%d comment=%r → trying larger SL distance…",
                symbol,
                rc,
                extra,
                getattr(last_res, "comment", ""),
            )
        else:
            logging.error(
                "[%s] open_sl_at_entry: exhausted SL offsets 0..%d (last retcode=%s comment=%r).",
                symbol,
                max_extra,
                getattr(last_res, "retcode", "n/a") if last_res else "n/a",
                getattr(last_res, "comment", "") if last_res else "",
            )
            _record_failed_order_attempt(symbol, cfg, last_res)
            return False
    else:
        request: dict = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": cfg.deviation_points,
            "magic": cfg.magic,
            "comment": "meta-trader-bot",
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
            return False

    logging.info("[%s] Order placed OK. deal=%s order=%s", symbol, getattr(result, "deal", None), getattr(result, "order", None))
    _last_failed_order_attempt.pop(symbol, None)
    _last_trade_time[symbol] = datetime.now()
    _record_symbol_open_timestamp(symbol)
    pos_id_for_log = _position_id_from_deal_ticket(getattr(result, "deal", None))
    open_ts_iso = datetime.now().isoformat(timespec="seconds")
    _log_event(
        {
            "event": "TRADE_OPEN",
            "ts": open_ts_iso,
            "opened_at": open_ts_iso,
            "symbol": symbol,
            "side": side,
            "lot": lot,
            "price": execution_price,
            "price_open": execution_price,
            "spread_points": float(spread) if spread is not None else None,
            "sl": sl,
            "tp": tp,
            "sl_money": sl_money,
            "tp_money": tp_money,
            "sl_points": sl_points if sl_points > 0 else None,
            "tp_points": tp_points if tp_points > 0 else None,
            "sl_tp_mode": sl_tp_mode,
            "open_sl_at_entry": bool(p.get("open_sl_at_entry", False)),
            "open_sl_at_entry_extra_points": open_sl_extra_used,
            "atr_at_entry": round(atr_at_entry, 6) if atr_at_entry is not None else None,
            "portfolio_enabled": bool(cfg.portfolio_enabled),
            "portfolio_weight": round(port_w, 6) if bool(cfg.portfolio_enabled) else None,
            "risk_per_trade_pct_base": risk_pct_base,
            "risk_per_trade_pct_effective": round(risk_pct_eff, 6),
            "magic": cfg.magic,
            "retcode": int(getattr(result, "retcode", -1)),
            "order": getattr(result, "order", None),
            "deal": getattr(result, "deal", None),
            "position_id": pos_id_for_log,
        }
    )

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_TRADE_OPEN", True):
        try:
            sl_txt = f"{sl:.{int(info.digits)}f}" if sl is not None else "—"
            tp_txt = f"{tp:.{int(info.digits)}f}" if tp is not None else "—"
            acc = mt5.account_info()
            cur = str(getattr(acc, "currency", "") or "USD") if acc is not None else "USD"
            pos_extra = f"\nPosition ID: {pos_id_for_log}" if pos_id_for_log else ""
            telegram_notify.notify(
                f"meta-trader: orden ejecutada\n"
                f"{symbol} {side} · lot {lot:.2f} · precio {execution_price:.{int(info.digits)}f} {cur}\n"
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

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": int(ticket),
    }
    if sl is not None:
        request["sl"] = float(sl)
    if tp is not None:
        request["tp"] = float(tp)

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


def _close_position_market(symbol: str, ticket: int, volume: float, side: str, comment: str = "bot_filter_exit") -> bool:
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
        "magic": 0,
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        logging.error("[%s] Close market returned None. last_error=%s", symbol, mt5.last_error())
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        # Retry with FOK filling (some brokers disallow IOC for close)
        request["type_filling"] = mt5.ORDER_FILLING_FOK
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            rc = getattr(result, "retcode", "n/a") if result else "n/a"
            cm = getattr(result, "comment", "") if result else ""
            logging.error("[%s] Close market failed. retcode=%s comment=%s", symbol, rc, cm)
            return False
    return True


def _apply_usd_sl_ladder(
    cfg: Config,
    symbol: str,
    p,
    pr: dict,
    *,
    side: Signal,
    lot: float,
    digits: int,
    entry_price: float,
    ticket: int,
    profit: float,
) -> None:
    """
    Escalera: SL inicial ~-initial_sl USD desde entrada (si el broker dejó SL demasiado ajustado),
    tras `wait_be` s si profit>0 → BE, luego cada `step` s asegura +$K con K=floor(profit).
    Si el broker rechaza +$K (stops/freeze), reintenta +$(K-1), … hasta un nivel válido (sin bajar
    de lo ya locked). Si `usd_ladder_max_lock_usd` > 0, tope en ese nivel; si es 0, sin tope.
    """
    if ticket <= 0 or entry_price <= 0:
        return

    init_usd = abs(float(pr.get("usd_sl_ladder_initial_sl_money", cfg.usd_sl_ladder_initial_sl_money) or 0.8))
    wait_be = max(0, int(pr.get("usd_ladder_wait_be_seconds", cfg.usd_ladder_wait_be_seconds) or 0))
    step_s = max(0, int(pr.get("usd_ladder_step_seconds", cfg.usd_ladder_step_seconds) or 0))
    max_lock = max(0, int(pr.get("usd_ladder_max_lock_usd", cfg.usd_ladder_max_lock_usd) or 0))

    st = _USD_SL_LADDER_STATE.setdefault(
        ticket,
        {
            "init_done": False,
            "be_done": False,
            "locked_usd": 0,
            "last_ts": None,
            "max_floating_profit": float(profit),
        },
    )
    if "max_floating_profit" not in st:
        st["max_floating_profit"] = float(profit)
    # Guardar timestamp real de primer proceso (independiente del clock del broker).
    # _position_age_seconds(p) falla en brokers UTC+3 porque p.time está 3h adelantado
    # respecto a time.time() (UTC), dando age negativo → clampeado a 0 las primeras 3h.
    if "open_ts" not in st:
        st["open_ts"] = time.time()
    age = time.time() - float(st["open_ts"])
    cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
    now_ts = time.time()
    info_sym = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info_sym is None or tick is None:
        return
    pt = float(getattr(info_sym, "point", 0.0) or 0.0) if info_sym is not None else 0.0

    # 0) Una vez: asegurar SL no más apretado que ~-init_usd (corrige open_sl_at_entry u órdenes viejas).
    if not bool(st.get("init_done")):
        tgt_init = _sl_price_for_max_loss_usd(symbol, side, entry_price, lot, init_usd, digits=digits)

        # Recovery tras reinicio: SL ya mejor que el ancla de max pérdida → no pisar con widen; inferir escalera.
        ladder_recovery = False
        if tgt_init is not None and cur_sl != 0.0:
            already_tighter = (side == "BUY" and cur_sl > tgt_init) or (side == "SELL" and cur_sl < tgt_init)
            if already_tighter:
                ladder_recovery = True
                st["micro_be_done"] = True
                logging.info(
                    "[%s] Recovery: SL %.*f más ajustado que init_sl %.*f → micro_be_done=True inferido",
                    symbol,
                    digits,
                    cur_sl,
                    digits,
                    tgt_init,
                )
                locked_recovered = _profit_usd_at_sl(symbol, side, entry_price, lot, cur_sl, digits=digits)
                if locked_recovered is not None and locked_recovered > 0:
                    st["hwm_trail_lock_target_usd"] = float(locked_recovered)
                    st["max_floating_profit"] = max(float(st["max_floating_profit"]), float(locked_recovered))
                    logging.info(
                        "[%s] Recovery: hwm_trail_lock_target_usd=%.3f inferido desde SL actual",
                        symbol,
                        locked_recovered,
                    )

        if tgt_init is not None:
            if cur_sl == 0.0:
                logging.info(
                    "[%s] USD ladder: set initial SL ~-%.2f USD risk → SL=%.*f",
                    symbol,
                    init_usd,
                    digits,
                    tgt_init,
                )
                ok = _modify_position_sl_tp(symbol, ticket, tgt_init, None, side=side, info=info_sym, tick=tick)
                _log_event(
                    {
                        "event": "USD_LADDER_INIT_SL",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "ticket": ticket,
                        "side": side,
                        "target_sl": tgt_init,
                        "applied": bool(ok),
                    }
                )
            else:
                widen = (side == "BUY" and cur_sl > tgt_init) or (side == "SELL" and cur_sl < tgt_init)
                # Si ya inferimos escalera post-reinicio, no «ensanchar» hacia tgt_init (destruiría BE/HWM).
                if widen and not ladder_recovery:
                    logging.info(
                        "[%s] USD ladder: widen initial SL toward ~-%.2f USD risk (cur=%.*f → %.*f).",
                        symbol,
                        init_usd,
                        digits,
                        cur_sl,
                        digits,
                        tgt_init,
                    )
                    ok = _modify_position_sl_tp(symbol, ticket, tgt_init, None, side=side, info=info_sym, tick=tick)
                    _log_event(
                        {
                            "event": "USD_LADDER_INIT_SL",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol,
                            "ticket": ticket,
                            "side": side,
                            "target_sl": tgt_init,
                            "applied": bool(ok),
                        }
                    )
        st["init_done"] = True

    st["max_floating_profit"] = max(float(st["max_floating_profit"]), float(profit))
    mfe = float(st["max_floating_profit"])

    be_offset = float(cfg.break_even_offset_points) * pt

    # 1) Break-even tras espera y habiendo tenido verde alguna vez (MFE>0).
    #    Importante: NO depende de `profit>0` porque el loop puede "perder" el momento verde entre polls.
    #    En cambio, intentamos BE sólo si el SL calculado es válido vs precio actual (si no lo es, lo
    #    salteamos y lo reintentamos en el futuro cuando vuelva a ser válido).
    if not bool(st.get("be_done")):
        if age >= float(wait_be) and mfe > 0.0:
            if side == "BUY":
                be_sl = round(entry_price + be_offset, digits)
                should = (cur_sl == 0.0) or (cur_sl < be_sl)
            else:
                be_sl = round(entry_price - be_offset, digits)
                should = (cur_sl == 0.0) or (cur_sl > be_sl)
            if should and _sl_is_valid_now(side=side, sl=float(be_sl), info=info_sym, tick=tick):
                logging.info(
                    "[%s] USD ladder: BE after %.0fs (profit=%.2f mfe=%.2f) → SL=%.*f",
                    symbol,
                    age,
                    profit,
                    mfe,
                    digits,
                    be_sl,
                )
                ok = _modify_position_sl_tp(symbol, ticket, be_sl, None, side=side, info=info_sym, tick=tick)
                _log_event(
                    {
                        "event": "USD_LADDER_BREAK_EVEN",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "ticket": ticket,
                        "side": side,
                        "age_sec": round(age, 1),
                        "profit": profit,
                        "max_floating_profit": round(mfe, 4),
                        "new_sl": be_sl,
                        "applied": bool(ok),
                    }
                )
                if ok:
                    st["be_done"] = True
                    st["last_ts"] = now_ts
        # No return: si el profit actual ya es >= +$1, permitimos pasar directo al lock +$K
        # (K = floor(profit)), aun si BE todavía no se pudo aplicar.

    # --- Micro breakeven: umbral inferior al BE clásico; SL en entrada ± offset (lock_profit_usd=0 invalida helpers). ---
    micro_be_trig = float(pr.get("micro_be_trigger_money", getattr(cfg, "micro_be_trigger_money", 0.0)) or 0.0)
    if micro_be_trig > 0 and not bool(st.get("micro_be_done")) and not bool(st.get("be_done")) and profit >= micro_be_trig:
        be_off = float(cfg.break_even_offset_points) * pt
        if side == "BUY":
            micro_sl = round(entry_price + be_off, digits)
            should_micro = (cur_sl == 0.0) or (cur_sl < micro_sl)
        else:
            micro_sl = round(entry_price - be_off, digits)
            should_micro = (cur_sl == 0.0) or (cur_sl > micro_sl)
        if should_micro and _sl_is_valid_now(side=side, sl=float(micro_sl), info=info_sym, tick=tick):
            ok_mb = _modify_position_sl_tp(symbol, ticket, micro_sl, None, side=side, info=info_sym, tick=tick)
            if ok_mb:
                st["micro_be_done"] = True
                logging.info(
                    "[%s] Micro-BE applied at profit=%.3f (trigger=%.2f) → SL %.*f",
                    symbol,
                    profit,
                    micro_be_trig,
                    digits,
                    micro_sl,
                )
                _log_event(
                    {
                        "event": "USD_LADDER_MICRO_BE",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "ticket": ticket,
                        "side": side,
                        "profit": profit,
                        "trigger_money": micro_be_trig,
                        "new_sl": micro_sl,
                        "applied": True,
                    }
                )

    # --- High water mark trailing: tras BE normal o micro-BE, mover SL cuando el HWM permite asegurar (1-trailpct)*MFE ---
    hwm_trail_pct = float(pr.get("hwm_trail_pct", getattr(cfg, "hwm_trail_pct", 0.0)) or 0.0)
    hwm = float(st.get("max_floating_profit", 0.0))

    if hwm_trail_pct > 0 and (bool(st.get("be_done")) or bool(st.get("micro_be_done"))) and hwm > 0 and profit > 0:
        lock_target = hwm * (1.0 - hwm_trail_pct)
        # El trail nunca baja del piso garantizado más reciente (locked_usd).
        floor_usd = float(int(st.get("locked_usd") or 0))
        if floor_usd > 0:
            lock_target = max(lock_target, floor_usd)
        hwm_prev_lock = float(st.get("hwm_trail_lock_target_usd", 0.0) or 0.0)

        if lock_target > hwm_prev_lock + 1e-6:
            hwm_sl = _sl_price_to_lock_profit_usd(
                symbol, side, entry_price, lot, float(lock_target), digits=digits
            )
            if hwm_sl is not None and _sl_is_valid_now(side=side, sl=float(hwm_sl), info=info_sym, tick=tick):
                ok_hwm = _modify_position_sl_tp(symbol, ticket, float(hwm_sl), None, side=side, info=info_sym, tick=tick)
                if ok_hwm:
                    st["hwm_trail_lock_target_usd"] = float(lock_target)
                    logging.info(
                        "[%s] HWM trail: hwm=%.3f trail=%.0f%% lock_target=%.3f → SL %.*f",
                        symbol,
                        hwm,
                        hwm_trail_pct * 100.0,
                        lock_target,
                        digits,
                        hwm_sl,
                    )
                    _log_event(
                        {
                            "event": "USD_LADDER_HWM_TRAIL",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol,
                            "ticket": ticket,
                            "side": side,
                            "hwm": round(hwm, 4),
                            "hwm_trail_pct": hwm_trail_pct,
                            "lock_target": round(lock_target, 6),
                            "new_sl": hwm_sl,
                            "applied": True,
                        }
                    )

    # --- Stagnant timeout: opcional — cierra mercado si edad>mín y el MFE nunca superó el umbral ---
    stagnant_timeout = float(pr.get("stagnant_timeout_minutes", getattr(cfg, "stagnant_timeout_minutes", 0.0)) or 0.0)
    stagnant_threshold = float(pr.get("stagnant_max_profit_threshold", getattr(cfg, "stagnant_max_profit_threshold", 0.0)) or 0.0)
    age_sec = age
    max_fp_here = float(st.get("max_floating_profit", 0.0) or 0.0)

    if (
        stagnant_timeout > 0
        and stagnant_threshold > 0
        and not bool(st.get("stagnant_closed"))
        and age_sec > stagnant_timeout * 60.0
        and max_fp_here < stagnant_threshold
    ):
        logging.warning(
            "[%s] Stagnant position timeout: age=%.0fs max_fp=%.3f threshold=%.3f → closing",
            symbol,
            age_sec,
            max_fp_here,
            stagnant_threshold,
        )
        close_ok = _close_position_market(symbol, ticket, lot, side, comment="meta_stagnant_timeout")
        if close_ok:
            st["stagnant_closed"] = True
            _log_event(
                {
                    "event": "STAGNANT_TIMEOUT_CLOSE",
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "symbol": symbol,
                    "ticket": ticket,
                    "age_seconds": round(age_sec, 1),
                    "max_floating_profit": max_fp_here,
                    "threshold": stagnant_threshold,
                }
            )
        return

    #    Si profit salta de +1.2 a +3.9, movemos directo a asegurar +$3 (monótono).
    locked = int(st.get("locked_usd") or 0)
    last_ts = st.get("last_ts")
    if max_lock > 0 and locked >= max_lock:
        return
    if last_ts is None:
        # Semilla: si partimos en 0s transcurridos, este tick nunca evaluaría locks (y un proceso
        # nuevo, p. ej. watcher_ladder, quedaría ~step_s segundos "mudo" vs el bot principal).
        st["last_ts"] = float(now_ts) - float(step_s)
        last_ts = st["last_ts"]
    if (now_ts - float(last_ts)) < float(step_s):
        return

    target_k = int(float(profit) // 1.0)
    if max_lock > 0:
        target_k = min(int(max_lock), int(target_k))
    if target_k <= locked:
        return

    # Fallback ladder: if +$K is rejected (invalid stops / freeze / etc), try K-1, K-2...
    # Rango: desde target_k hasta (locked+1) inclusive → range(target_k, locked, -1).
    cur_sl2 = float(getattr(p, "sl", 0.0) or 0.0)
    applied_k: int | None = None
    applied_sl: float | None = None
    attempted: list[int] = []
    for k in range(int(target_k), int(locked), -1):
        attempted.append(int(k))
        lock_sl = _sl_price_to_lock_profit_usd(symbol, side, entry_price, lot, float(k), digits=digits)
        if lock_sl is None:
            continue

        if side == "BUY":
            should_lock = (cur_sl2 == 0.0) or (lock_sl > cur_sl2)
        else:
            should_lock = (cur_sl2 == 0.0) or (lock_sl < cur_sl2)
        if not should_lock:
            # Already better than this rung; advance state and stop.
            st["locked_usd"] = int(k)
            st["be_done"] = True
            st["last_ts"] = now_ts
            return

        # Skip candidates that are invalid given current price + broker stop levels.
        if not _sl_is_valid_now(side=side, sl=float(lock_sl), info=info_sym, tick=tick):
            continue

        logging.info(
            "[%s] USD ladder: lock +$%d (profit=%.2f) → SL=%.*f",
            symbol,
            int(k),
            profit,
            digits,
            lock_sl,
        )
        ok = _modify_position_sl_tp(symbol, ticket, lock_sl, None, side=side, info=info_sym, tick=tick)
        if ok:
            applied_k = int(k)
            applied_sl = float(lock_sl)
            break

    _log_event(
        {
            "event": "USD_LADDER_LOCK",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "ticket": ticket,
            "side": side,
            "lock_usd": int(target_k),
            "attempted_rungs": attempted,
            "applied_rung": applied_k,
            "profit": profit,
            "new_sl": applied_sl,
            "applied": bool(applied_k is not None),
        }
    )
    if applied_k is not None:
        st["locked_usd"] = int(applied_k)
        st["be_done"] = True
        st["last_ts"] = now_ts


def _money_at_distance(symbol: str, lot: float, distance_price: float) -> Optional[float]:
    """
    Convert a price distance into account-currency money for `lot`.
    Linear model: money ≈ (distance / tick_size) * tick_value * lot.
    """
    info = mt5.symbol_info(symbol)
    if info is None or lot <= 0 or distance_price <= 0:
        return None
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    if tick_value <= 0 or tick_size <= 0:
        return None
    return (float(distance_price) / tick_size) * tick_value * lot


def _apply_atr_profit_trail(
    cfg: Config,
    symbol: str,
    p,
    pr: dict,
    *,
    side: Signal,
    lot: float,
    digits: int,
    entry_price: float,
    ticket: int,
    profit: float,
) -> None:
    """
    Chandelier-style profit trailing (replacement for the USD ladder).

    Two phases:
      1) Pre-activation: SL stays at its initial placement (where open_trade put it).
         Do not move it at all until profit >= activation_mult * initial_risk_money.
         This avoids cutting winners by reacting to M15 noise.
      2) Active: trail SL at (current_price ± atr_mult * ATR(M15)). Monotonic — SL only
         moves *closer* to price, never widens. TP (if set at entry) is left alone so
         runners can hit it.
    """
    if ticket <= 0 or entry_price <= 0 or lot <= 0:
        return
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return

    cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
    if cur_sl <= 0:
        return  # no SL set yet (broker rejection?); skip trailing this tick

    # Distance from entry to the CURRENT SL → money at risk if SL is hit.
    sl_distance = abs(float(entry_price) - float(cur_sl))
    initial_risk_money = _money_at_distance(symbol, lot, sl_distance)
    if initial_risk_money is None or initial_risk_money <= 0:
        return

    activation_mult = float(
        pr.get("atr_profit_trail_activation_mult", cfg.atr_profit_trail_activation_mult) or 1.5
    )
    activation_profit = activation_mult * initial_risk_money

    if float(profit) < activation_profit:
        return  # Phase 1: SL untouched. Let the trade breathe.

    # Phase 2: active trailing.
    atr_period = int(pr.get("atr_profit_trail_atr_period", cfg.atr_profit_trail_atr_period) or 14)
    atr_mult = float(pr.get("atr_profit_trail_atr_mult", cfg.atr_profit_trail_atr_mult) or 2.5)
    atr_val = get_atr(symbol, mt5.TIMEFRAME_M15, atr_period)
    if atr_val is None or atr_val <= 0:
        return

    trail_distance = float(atr_val) * atr_mult
    price_now = float(tick.bid if side == "BUY" else tick.ask)

    if side == "BUY":
        candidate_sl = round(price_now - trail_distance, digits)
        is_tighter = candidate_sl > cur_sl
    else:
        candidate_sl = round(price_now + trail_distance, digits)
        is_tighter = candidate_sl < cur_sl

    if not is_tighter:
        return

    if not _sl_is_valid_now(side=side, sl=float(candidate_sl), info=info, tick=tick):
        return

    point = float(getattr(info, "point", 0.0) or 0.0)
    min_step = point * 0.5
    if min_step > 0 and abs(candidate_sl - cur_sl) <= min_step:
        return

    logging.info(
        "[%s] ATR profit trail: profit=%.2f act_thr=%.2f init_risk=%.2f ATR=%.5f mult=%.2f → SL %.*f → %.*f",
        symbol,
        profit,
        activation_profit,
        initial_risk_money,
        atr_val,
        atr_mult,
        digits,
        cur_sl,
        digits,
        candidate_sl,
    )
    ok = _modify_position_sl_tp(symbol, ticket, float(candidate_sl), None, side=side, info=info, tick=tick)
    _log_event(
        {
            "event": "ATR_PROFIT_TRAIL",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "ticket": ticket,
            "side": side,
            "profit": round(float(profit), 4),
            "activation_profit": round(float(activation_profit), 4),
            "initial_risk_money": round(float(initial_risk_money), 4),
            "atr": round(float(atr_val), 6),
            "atr_period": atr_period,
            "atr_mult": atr_mult,
            "prev_sl": cur_sl,
            "new_sl": candidate_sl,
            "applied": bool(ok),
        }
    )


def manage_positions(cfg: Config) -> None:
    """
    Trailing logic:
      If profit >= +1.00:
        move SL to lock +0.50
        move TP to target +1.20
    """
    positions = mt5.positions_get()
    if positions is None:
        return

    open_managed: set[int] = set()
    for _p0 in positions:
        if not _position_is_managed_by_bot(_p0, cfg):
            continue
        _tk = int(getattr(_p0, "ticket", 0) or 0)
        if _tk > 0:
            open_managed.add(_tk)
    for _tk in list(_PROFIT_LOCK_PEAK_PROFIT.keys()):
        if _tk not in open_managed:
            del _PROFIT_LOCK_PEAK_PROFIT[_tk]
    for _tk in list(_USD_SL_LADDER_STATE.keys()):
        if _tk not in open_managed:
            del _USD_SL_LADDER_STATE[_tk]

    for p in positions:
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

        pr = _effective_params(cfg, symbol)
        be_trig = float(pr["break_even_trigger_money"])
        trail_trig = float(pr["trail_trigger_money"])
        trail_sl_m = float(pr["trail_new_sl_money"])
        trail_tp_m = float(pr["trail_new_tp_money"])

        side = "BUY" if int(getattr(p, "type", -1)) == mt5.POSITION_TYPE_BUY else "SELL"
        lot = float(getattr(p, "volume", cfg.lot) or cfg.lot)
        digits = int(info.digits)
        entry_price = float(getattr(p, "price_open", 0.0) or 0.0)
        ticket = int(getattr(p, "ticket", 0) or 0)

        # --- B) Filter exit: close on momentum loss when not in loss ---
        if bool(pr.get("filter_exit_enabled", False)) and ticket > 0:
            min_profit = float(pr.get("filter_exit_min_profit_money", 0.0) or 0.0)
            if profit >= min_profit:
                adx_now = get_adx(symbol, mt5.TIMEFRAME_M15, cfg.adx_period_m15)
                adx_thr_raw = float(pr.get("filter_exit_adx_min", 0.0) or 0.0)
                adx_threshold = adx_thr_raw if adx_thr_raw > 0 else float(pr["adx_min"])
                ema_dir = ema_trend(symbol, int(pr["ema_fast_m15"]), int(pr["ema_slow_m15"]))
                adx_broken = adx_now is not None and adx_now < adx_threshold
                ema_against = (side == "BUY" and ema_dir == "DOWN") or (side == "SELL" and ema_dir == "UP")
                require_ema = bool(pr.get("filter_exit_require_ema_reversal", True))
                should_exit = False
                reason_parts: list[str] = []
                if require_ema:
                    if adx_broken and ema_against:
                        should_exit = True
                        reason_parts = [f"ADX={adx_now:.1f}<{adx_threshold:.1f}", f"EMA={ema_dir} vs {side}"]
                else:
                    if adx_broken:
                        should_exit = True
                        reason_parts.append(f"ADX={adx_now:.1f}<{adx_threshold:.1f}")
                    elif ema_against:
                        should_exit = True
                        reason_parts.append(f"EMA={ema_dir} vs {side}")
                if should_exit:
                    reason_str = " & ".join(reason_parts)
                    logging.info(
                        "[%s] Filter exit (%s). Closing at market. profit=%.2f",
                        symbol, reason_str, profit,
                    )
                    ok = _close_position_market(symbol, ticket, lot, side, comment="bot_filter_exit")
                    _log_event(
                        {
                            "event": "FILTER_EXIT",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol,
                            "ticket": ticket,
                            "side": side,
                            "profit": profit,
                            "adx": round(adx_now, 2) if adx_now is not None else None,
                            "adx_threshold": adx_threshold,
                            "ema_dir": ema_dir,
                            "reason": reason_str,
                            "closed": bool(ok),
                        }
                    )
                    if ok:
                        continue

        # --- USD SL ladder (BE + locks +$1..+$N con tiempos): sustituye BE / profit_lock / trailing clásico ---
        ladder_on = bool(cfg.usd_sl_ladder_enabled)
        if ladder_on and ticket > 0 and entry_price > 0:
            _apply_usd_sl_ladder(
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
            continue

        # --- ATR-based profit trail (Chandelier; replaces ladder when enabled and ladder off) ---
        # Phase 1: SL stays at entry placement until profit >= activation_mult * initial risk.
        # Phase 2: trail SL at K * ATR(M15) behind market. TP from entry left untouched.
        if bool(cfg.atr_profit_trail_enabled) and ticket > 0 and entry_price > 0:
            _apply_atr_profit_trail(
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
            continue

        # Break-even before trailing
        if profit >= be_trig and entry_price > 0:
            cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
            be_offset = float(cfg.break_even_offset_points) * float(info.point or 0.0)
            if side == "BUY":
                be_sl = round(entry_price + be_offset, digits)
                should_set = (cur_sl == 0.0) or (cur_sl < be_sl)
            else:
                be_sl = round(entry_price - be_offset, digits)
                should_set = (cur_sl == 0.0) or (cur_sl > be_sl)

            if should_set and ticket > 0:
                logging.info("[%s] Break-even: profit=%.2f -> SL=%.*f", symbol, profit, digits, be_sl)
                _modify_position_sl_tp(symbol, ticket, be_sl, None)
                _log_event(
                    {
                        "event": "BREAK_EVEN",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "ticket": ticket,
                        "side": side,
                        "profit": profit,
                        "new_sl": be_sl,
                    }
                )

        # --- USD profit lock (peak MFE; same tick snapshot + historical max) ---
        if bool(pr.get("profit_lock_enabled", False)) and ticket > 0 and entry_price > 0:
            step_m = float(pr.get("profit_lock_step_money", 1.0) or 1.0)
            sec_m = float(pr.get("profit_lock_secure_money", 1.0) or 1.0)
            if step_m > 0 and sec_m > 0:
                prev_peak = _PROFIT_LOCK_PEAK_PROFIT.get(ticket, profit)
                peak = max(float(prev_peak), float(profit))
                _PROFIT_LOCK_PEAK_PROFIT[ticket] = peak
                n = int(peak // step_m)
                lock_usd = float(n) * sec_m
                if lock_usd > 0:
                    lock_sl = _sl_price_to_lock_profit_usd(
                        symbol, side, entry_price, lot, lock_usd, digits=digits
                    )
                    if lock_sl is not None:
                        cur_sl2 = float(getattr(p, "sl", 0.0) or 0.0)
                        if side == "BUY":
                            pl_should = (cur_sl2 == 0.0) or (lock_sl > cur_sl2)
                        else:
                            pl_should = (cur_sl2 == 0.0) or (lock_sl < cur_sl2)
                        if pl_should:
                            logging.info(
                                "[%s] Profit lock: peak=%.2f n=%d lock$≈%.2f -> SL=%.*f",
                                symbol, peak, n, lock_usd, digits, lock_sl,
                            )
                            pl_ok = _modify_position_sl_tp(symbol, ticket, lock_sl, None)
                            _log_event(
                                {
                                    "event": "PROFIT_LOCK_LADDER",
                                    "ts": datetime.now().isoformat(timespec="seconds"),
                                    "symbol": symbol,
                                    "ticket": ticket,
                                    "side": side,
                                    "profit_now": round(profit, 4),
                                    "peak_profit": round(peak, 4),
                                    "step_usd": step_m,
                                    "secure_per_step_usd": sec_m,
                                    "rungs": n,
                                    "lock_usd": lock_usd,
                                    "new_sl": lock_sl,
                                    "applied": bool(pl_ok),
                                }
                            )

        # --- C) Tight trailing stop post-profit (ATR-based) ---
        # When profit >= tight_trail_trigger_money, use ATR(M15)*mult as SL distance.
        # Only tightens the SL (monotonic); leaves TP alone so winners can keep running.
        # Takes precedence over the classic trailing when active, to avoid conflicts.
        skip_tt = bool(pr.get("profit_lock_enabled", False)) and bool(
            getattr(cfg, "skip_tight_trail_when_profit_lock", True)
        )
        tight_applied = False
        if (not skip_tt) and bool(pr.get("tight_trail_enabled", False)) and ticket > 0:
            tight_trig = float(pr.get("tight_trail_trigger_money", 0.0) or 0.0)
            if profit >= tight_trig:
                tt_period = int(pr.get("tight_trail_atr_period", 14) or 14)
                tt_mult = float(pr.get("tight_trail_atr_mult", 1.2) or 1.2)
                atr_val = get_atr(symbol, mt5.TIMEFRAME_M15, tt_period)
                if atr_val is not None and atr_val > 0:
                    distance = atr_val * tt_mult
                    price_now = float(tick.bid if side == "BUY" else tick.ask)
                    cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
                    if side == "BUY":
                        candidate_sl = round(price_now - distance, digits)
                        is_tighter = (cur_sl == 0.0) or (candidate_sl > cur_sl)
                    else:
                        candidate_sl = round(price_now + distance, digits)
                        is_tighter = (cur_sl == 0.0) or (candidate_sl < cur_sl)

                    min_step = float(info.point or 0.0) * 0.5
                    if is_tighter and (cur_sl == 0.0 or abs(candidate_sl - cur_sl) > min_step):
                        logging.info(
                            "[%s] Tight trail (profit=%.2f, ATR=%.5f, mult=%.2f). SL=%.*f -> %.*f",
                            symbol, profit, atr_val, tt_mult, digits, cur_sl, digits, candidate_sl,
                        )
                        ok = _modify_position_sl_tp(symbol, ticket, candidate_sl, None)
                        _log_event(
                            {
                                "event": "TIGHT_TRAIL",
                                "ts": datetime.now().isoformat(timespec="seconds"),
                                "symbol": symbol,
                                "ticket": ticket,
                                "side": side,
                                "profit": profit,
                                "atr": round(atr_val, 6),
                                "atr_period": tt_period,
                                "atr_mult": tt_mult,
                                "prev_sl": cur_sl if cur_sl else None,
                                "new_sl": candidate_sl,
                                "applied": bool(ok),
                            }
                        )
                        tight_applied = True

        if tight_applied:
            # Tight trail took precedence; don't re-apply the classic trailing this tick
            # (it would likely widen the SL back out).
            continue

        if profit < trail_trig:
            continue

        # Build new SL/TP around current market price to approximate locked profit targets.
        # This is an approximation; exact money targets depend on symbol contract specs & current price.
        sl_dist = _price_distance_for_money(symbol, lot, trail_sl_m, side)
        tp_dist = _price_distance_for_money(symbol, lot, trail_tp_m, side)
        if sl_dist is None or tp_dist is None:
            continue

        price_now = float(tick.bid if side == "BUY" else tick.ask)

        if side == "BUY":
            new_sl = round(price_now - sl_dist, digits)
            new_tp = round(price_now + tp_dist, digits)
        else:
            new_sl = round(price_now + sl_dist, digits)
            new_tp = round(price_now - tp_dist, digits)

        # Avoid unnecessary modifications
        cur_sl = getattr(p, "sl", 0.0) or 0.0
        cur_tp = getattr(p, "tp", 0.0) or 0.0
        if cur_sl and abs(float(cur_sl) - new_sl) < (info.point * 0.5):
            new_sl = None
        if cur_tp and abs(float(cur_tp) - new_tp) < (info.point * 0.5):
            new_tp = None

        if new_sl is None and new_tp is None:
            continue

        if ticket <= 0:
            continue

        logging.info(
            "[%s] Trailing (profit=%.2f). Updating SL=%s TP=%s",
            symbol,
            profit,
            f"{new_sl:.{digits}f}" if new_sl is not None else "keep",
            f"{new_tp:.{digits}f}" if new_tp is not None else "keep",
        )

        _modify_position_sl_tp(symbol, ticket, new_sl, new_tp)
        _log_event(
            {
                "event": "TRAILING_UPDATE",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "ticket": ticket,
                "profit": profit,
                "new_sl": new_sl,
                "new_tp": new_tp,
            }
        )


def _maybe_invert_decision(cfg: Config, symbol: str, decision: Signal) -> Signal:
    """
    When ``cfg.invert_signals`` is True, swap BUY ↔ SELL (NONE stays NONE) and emit a log
    event so the inversion is traceable in trades.log. Centralized so both decision call
    sites in this file invert consistently.
    """
    if not bool(getattr(cfg, "invert_signals", False)):
        return decision
    if decision == "BUY":
        flipped: Signal = "SELL"
    elif decision == "SELL":
        flipped = "BUY"
    else:
        return decision
    logging.info(
        "[%s] SIGNAL_INVERT: original=%s -> traded=%s (BOT_INVERT_SIGNALS=1)",
        symbol, decision, flipped,
    )
    _log_event(
        {
            "event": "SIGNAL_INVERT",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "original": decision,
            "inverted": flipped,
        }
    )
    return flipped


def _maybe_trade_symbol(cfg: Config, symbol: str) -> None:
    now = datetime.now()
    p = _effective_params(cfg, symbol)
    trend_h4 = get_trend_h4(symbol)
    rsi_h4 = get_rsi_h4(symbol)
    signal = get_signal_m15(symbol)
    ema_dir = ema_trend(symbol, int(p["ema_fast_m15"]), int(p["ema_slow_m15"]))
    adx = get_adx(symbol, mt5.TIMEFRAME_M15, cfg.adx_period_m15)

    ok_spread, spread, avg_spread = _is_spread_ok(cfg, symbol, max_spread_points=float(p["max_spread_points"]))
    if spread is None:
        spread_s = "n/a"
    elif cfg.spread_dynamic_enabled and avg_spread is not None:
        spread_s = f"{spread:.1f} pts (avg {avg_spread:.1f})"
    else:
        spread_s = f"{spread:.1f} pts"
    active = _is_market_active_m15(symbol, cfg.min_avg_range_points_m15)

    slope_trend = _m15_slope_trend(symbol, n_closes=int(p["price_slope_n"]))

    logging.info(
        "[%s] H4=%s | M15=%s | EMA(%d/%d)=%s | ADX=%.2f | price_slope%d=%s | Spread=%s | Active=%s",
        symbol,
        trend_h4,
        signal,
        int(p["ema_fast_m15"]),
        int(p["ema_slow_m15"]),
        ema_dir,
        adx if adx is not None else 0.0,
        int(p["price_slope_n"]),
        slope_trend,
        spread_s,
        "yes" if active is True else ("no" if active is False else "n/a"),
    )

    decision = generate_signal(
        trend_h4=trend_h4,
        signal_m15=signal,
        ema_dir=ema_dir,
        adx=adx,
        adx_min=float(p["adx_min"]),
        symbol=symbol,
        crypto_sell_requires_h4_down=cfg.crypto_sell_requires_h4_down,
        allow_flat_h4=True,
        m15_price_slope=slope_trend,
        price_slope_filter_enabled=bool(p["price_slope_filter_enabled"]),
        ema_fast=get_ema(symbol, mt5.TIMEFRAME_M15, int(p["ema_fast_m15"])),
        ema_slow=get_ema(symbol, mt5.TIMEFRAME_M15, int(p["ema_slow_m15"])),
        rsi_h4=rsi_h4,
        rsi_h4_filter_enabled=bool(p.get("rsi_h4_filter_enabled", False)),
        rsi_h4_oversold_threshold=float(p.get("rsi_h4_oversold_threshold", 35.0)),
        rsi_h4_overbought_threshold=float(p.get("rsi_h4_overbought_threshold", 65.0)),
    )

    # Inversión opcional: se aplica ANTES del extension filter para que ese se evalúe en la dirección real del trade.
    decision = _maybe_invert_decision(cfg, symbol, decision)

    # Filtro de extensión: bloquea entradas si el precio ya recorrió demasiado desde el extremo H1.
    if decision != "NONE":
        ext_pct = float(p.get("extension_filter_max_pct", 4.0))
        ext_bars = int(p.get("extension_filter_h1_bars", 16))
        if _movement_is_extended(symbol, decision,
                                 lookback_h1_bars=ext_bars,
                                 max_extension_pct=ext_pct):
            logging.info(
                "[%s] No trade: movimiento extendido >%.1f%% desde extremo H1 (%d bars).",
                symbol, ext_pct, ext_bars,
            )
            _log_event({
                "event": "EXTENSION_FILTER_SKIP",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": decision,
                "extension_filter_max_pct": ext_pct,
                "extension_filter_h1_bars": ext_bars,
            })
            _eval_log_skip(
                symbol,
                "extension_filter",
                when=now,
                side=decision,
                extension_filter_max_pct=ext_pct,
                extension_filter_h1_bars=ext_bars,
            )
            decision = "NONE"

    in_window = _session_allows_new_trade(cfg, symbol, now)
    _eval_log_cycle(
        symbol,
        decision=decision,
        when=now,
        track=str(p.get("eval_track") or ""),
        trend_h4=trend_h4,
        signal_m15=signal,
        ema_dir=ema_dir,
        adx=adx if adx is not None else None,
        adx_min=float(p["adx_min"]),
        price_slope=slope_trend,
        rsi_h4=rsi_h4,
        spread=spread,
        ok_spread=ok_spread,
        active=active,
        in_session_window=in_window,
        crypto_sell_requires_h4_down=bool(cfg.crypto_sell_requires_h4_down),
    )

    if decision == "NONE":
        _eval_log_skip(
            symbol,
            "no_signal_align",
            when=now,
            trend_h4=trend_h4,
            signal_m15=signal,
            ema_dir=ema_dir,
            adx=adx if adx is not None else None,
            track=str(p.get("eval_track") or ""),
        )
        logging.info("[%s] No trade: conditions not aligned.", symbol)
        return

    if not _preflight_new_entry(
        cfg,
        symbol,
        side=decision,
        now=now,
        adx=adx,
        p=p,
        active=active,
        ok_spread=ok_spread,
        spread=spread,
        avg_spread=avg_spread,
    ):
        return

    _log_event(
        {
            "event": "EVAL_ENTRY_ATTEMPT",
            "ts": now.isoformat(timespec="seconds"),
            "symbol": symbol,
            "side": decision,
            "track": str(p.get("eval_track") or ""),
            "trend_h4": trend_h4,
            "signal_m15": signal,
            "ema_dir": ema_dir,
            "adx": adx,
            "spread": spread,
        }
    )
    open_trade(cfg, symbol, decision)


# --- Phase 1: pure strategy for backtesting ---
def generate_signal(
    *,
    trend_h4: Trend,
    signal_m15: Signal,
    ema_dir: Trend,
    adx: Optional[float],
    adx_min: float,
    symbol: str = "",
    crypto_sell_requires_h4_down: bool = True,
    allow_flat_h4: bool = True,
    m15_price_slope: Trend = "FLAT",
    price_slope_filter_enabled: bool = False,
    ema_fast: Optional[float] = None,
    ema_slow: Optional[float] = None,
    rsi_h4: Optional[float] = None,
    rsi_h4_filter_enabled: bool = False,
    rsi_h4_oversold_threshold: float = 35.0,
    rsi_h4_overbought_threshold: float = 65.0,
) -> Signal:
    """
    Delega en ``strategy_core.decide`` para mantener una sola fuente de verdad con backtest.
    """
    sym_trim = symbol.strip()
    sym_k = sym_trim.upper() if sym_trim else ""

    sell_probe = signal_m15 == "SELL" and ema_dir == "DOWN"
    if sell_probe and adx is not None and adx >= adx_min:
        relaxed_crypto = symbol_uses_crypto_relaxed_m15(sym_k) if sym_k else False
        logging.info(
            "[%s] SELL gate: trend_h4=%s ema_dir=%s relaxed=%s requires_h4_down=%s",
            sym_k if sym_k else "?",
            trend_h4,
            ema_dir,
            relaxed_crypto,
            crypto_sell_requires_h4_down,
        )

    sp = StrategyParams(
        adx_min=adx_min,
        allow_flat_h4=allow_flat_h4,
        crypto_sell_requires_h4_down=crypto_sell_requires_h4_down,
        price_slope_filter_enabled=price_slope_filter_enabled,
        rsi_h4_filter_enabled=rsi_h4_filter_enabled,
        rsi_h4_oversold_threshold=rsi_h4_oversold_threshold,
        rsi_h4_overbought_threshold=rsi_h4_overbought_threshold,
    )
    feat = Features(
        trend_h4=trend_h4,
        signal_m15=signal_m15,
        ema_dir=ema_dir,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        adx=adx,
        price_slope_m15=m15_price_slope,
        rsi_h4=rsi_h4,
    )
    return decide(sp, feat, symbol=(sym_trim or None))


def _try_execute_trade_through_gates(cfg: Config, symbol: str, decision: Signal, *, now: datetime) -> bool:
    """Igual que `_maybe_trade_symbol`: preflight antes de ejecutar."""
    if decision == "NONE":
        return False
    p = _effective_params(cfg, symbol)
    adx = get_adx(symbol, mt5.TIMEFRAME_M15, cfg.adx_period_m15)
    ok_spread, spread, avg_spread = _is_spread_ok(cfg, symbol, max_spread_points=float(p["max_spread_points"]))
    active = _is_market_active_m15(symbol, cfg.min_avg_range_points_m15)
    if not _preflight_new_entry(
        cfg,
        symbol,
        side=decision,
        now=now,
        adx=adx,
        p=p,
        active=active,
        ok_spread=ok_spread,
        spread=spread,
        avg_spread=avg_spread,
    ):
        return False
    return execute_trade(cfg, symbol, decision)


def execute_trade(cfg: Config, symbol: str, signal: Signal) -> bool:
    if signal == "BUY":
        return open_trade(cfg, symbol, "BUY")
    if signal == "SELL":
        return open_trade(cfg, symbol, "SELL")
    return False


def _xauusd_apply_anti_chase(symbol: str, cfg: Optional[Config], out: dict[str, Any]) -> dict[str, Any]:
    """
    Bloquea chase/agotamiento: rango M15 duro, cierre en el extremo, extensión vs EMA, spike M1.
    """
    if out.get("decision") not in ("BUY", "SELL"):
        return out
    if cfg is None or not bool(getattr(cfg, "xauusd_parallel_anti_chase", True)):
        return out
    m15 = _last_closed_bar_ohlc(symbol, mt5.TIMEFRAME_M15)
    atr_val = get_atr(symbol, mt5.TIMEFRAME_M15, 20)
    if m15 is None or atr_val is None or atr_val <= 0:
        return out
    m1 = _last_closed_bar_ohlc(symbol, mt5.TIMEFRAME_M1)
    m1_range = None if m1 is None else float(m1["high"]) - float(m1["low"])
    ema_ref = out.get("ema_fast")
    try:
        ema_f = None if ema_ref is None else float(ema_ref)
    except (TypeError, ValueError):
        ema_f = None
    price = out.get("price")
    try:
        px = None if price is None else float(price)
    except (TypeError, ValueError):
        px = None
    reason = _xauusd_live_bar_skip_reason(
        side=str(out.get("decision") or ""),
        bar_high=float(m15["high"]),
        bar_low=float(m15["low"]),
        bar_close=float(m15["close"]),
        atr=float(atr_val),
        price=px,
        ema=ema_f,
        last_m1_range=m1_range,
        range_hard_mult=float(getattr(cfg, "xauusd_parallel_anti_chase_atr_mult", 2.0) or 2.0),
        exhaustion_atr_mult=float(getattr(cfg, "xauusd_parallel_exhaustion_atr_mult", 1.5) or 1.5),
        exhaustion_extreme_frac=float(getattr(cfg, "xauusd_parallel_exhaustion_extreme_frac", 0.20) or 0.20),
        extension_atr_max=float(getattr(cfg, "xauusd_parallel_extension_atr_max", 1.0) or 1.0),
        m1_spike_atr_mult=float(getattr(cfg, "xauusd_parallel_m1_spike_atr_mult", 0.80) or 0.80),
    )
    if reason:
        out["decision"] = "NONE"
        out["skip_reason"] = reason
        out["bar_range"] = round(float(m15["high"]) - float(m15["low"]), 5)
        out["atr"] = round(float(atr_val), 5)
        out["anti_chase_mult"] = float(getattr(cfg, "xauusd_parallel_anti_chase_atr_mult", 2.0) or 2.0)
        if m1_range is not None:
            out["m1_range"] = round(float(m1_range), 5)
    return out


def _xauusd_breakout_eval(symbol: str, cfg: Optional[Config], out: dict[str, Any]) -> dict[str, Any]:
    """Señal breakout M15 (misma que el scan IS/OOS PF 1.12/1.26), no EMA9/21."""
    adx_min = float(out.get("adx_min") or _xauusd_effective_adx_min(cfg))
    trend_h4 = get_trend_h4(symbol)
    signal_m15 = get_signal_m15(symbol)
    ema_dir = ema_trend(symbol, 50, 200)
    ema_fast = get_ema(symbol, mt5.TIMEFRAME_M15, 50)
    ema_slow = get_ema(symbol, mt5.TIMEFRAME_M15, 200)
    slope = _m15_slope_trend(symbol, n_closes=6)
    adx_val = out.get("adx")
    out["entry_mode"] = "breakout"
    out["trend_h4"] = str(trend_h4)
    out["signal_m15"] = str(signal_m15)
    out["ema_dir"] = str(ema_dir)
    out["ema_fast"] = None if ema_fast is None else round(float(ema_fast), 5)
    out["ema_slow"] = None if ema_slow is None else round(float(ema_slow), 5)
    out["ema_fast_period"] = 50
    out["ema_slow_period"] = 200
    out["price_slope_m15"] = str(slope)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 2)
    if rates is not None and len(rates) >= 1:
        out["cross_bar_time"] = int(rates[0]["time"])
    decision = generate_signal(
        trend_h4=trend_h4,
        signal_m15=signal_m15,
        ema_dir=ema_dir,
        adx=None if adx_val is None else float(adx_val),
        adx_min=adx_min,
        symbol=symbol,
        crypto_sell_requires_h4_down=False,
        allow_flat_h4=True,
        m15_price_slope=slope,
        price_slope_filter_enabled=True,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )
    out["decision"] = decision
    if decision == "NONE":
        out["skip_reason"] = _xauusd_breakout_skip_reason(
            adx=None if adx_val is None else float(adx_val),
            adx_min=adx_min,
            signal_m15=str(signal_m15),
            ema_dir=str(ema_dir),
            trend_h4=str(trend_h4),
            slope=str(slope),
            decision=str(decision),
        )
    return out


def _xauusd_scalping_eval(symbol: str, cfg: Optional[Config] = None) -> dict[str, Any]:
    """
    Evalúa entrada XAUUSD paralelo (breakout por defecto; scalp EMA9/21 solo si entry_mode=scalp).
    decision: BUY | SELL | NONE
    """
    adx_min = _xauusd_effective_adx_min(cfg)
    h1_filter = bool(getattr(cfg, "xauusd_parallel_h1_filter", True)) if cfg is not None else True
    h4_filter = bool(getattr(cfg, "xauusd_parallel_h4_filter", True)) if cfg is not None else True
    h1_period = int(
        getattr(cfg, "xauusd_parallel_h1_ema_period", _XAUUSD_H1_EMA_PERIOD)
        if cfg is not None
        else _XAUUSD_H1_EMA_PERIOD
    ) or _XAUUSD_H1_EMA_PERIOD

    adx_val = get_adx(symbol, mt5.TIMEFRAME_M15, _XAUUSD_ADX_PERIOD)
    tick = mt5.symbol_info_tick(symbol)

    out: dict[str, Any] = {
        "decision": "NONE",
        "ema_fast": None,
        "ema_slow": None,
        "ema_fast_prev": None,
        "ema_slow_prev": None,
        "adx": None if adx_val is None else round(float(adx_val), 2),
        "adx_min": float(adx_min),
        "ema_fast_period": int(_XAUUSD_EMA_FAST),
        "ema_slow_period": int(_XAUUSD_EMA_SLOW),
        "h1_filter": bool(h1_filter),
        "h4_filter": bool(h4_filter),
        "h1_ema": None,
        "trend_h4": None,
        "price": None,
        "cross_bar_time": None,
        "skip_reason": None,
        "entry_mode": _xauusd_entry_mode(cfg),
    }
    if adx_val is None or tick is None:
        out["skip_reason"] = "data_missing"
        return out
    out["price"] = round(float(tick.bid), 5)

    if _xauusd_entry_mode(cfg) == "breakout":
        out = _xauusd_breakout_eval(symbol, cfg, out)
        return _xauusd_apply_anti_chase(symbol, cfg, out)

    count = max(_XAUUSD_EMA_SLOW + 40, _XAUUSD_EMA_SLOW + 5)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, count)
    if rates is None or len(rates) < (_XAUUSD_EMA_SLOW + 2):
        out["skip_reason"] = "data_missing"
        return out

    closes = [float(r["close"]) for r in rates]
    fast_s = _ema_series(closes, _XAUUSD_EMA_FAST)
    slow_s = _ema_series(closes, _XAUUSD_EMA_SLOW)
    i = len(closes) - 1
    ema_fast = fast_s[i]
    ema_slow = slow_s[i]
    ema_fast_prev = fast_s[i - 1]
    ema_slow_prev = slow_s[i - 1]
    if None in (ema_fast, ema_slow, ema_fast_prev, ema_slow_prev):
        out["skip_reason"] = "data_missing"
        return out

    out["ema_fast"] = round(float(ema_fast), 5)
    out["ema_slow"] = round(float(ema_slow), 5)
    out["ema_fast_prev"] = round(float(ema_fast_prev), 5)
    out["ema_slow_prev"] = round(float(ema_slow_prev), 5)
    out["cross_bar_time"] = int(rates[i]["time"])
    price = float(tick.bid)

    if float(adx_val) < float(adx_min):
        out["skip_reason"] = "adx_flat"
        return out

    decision = _xauusd_ema_cross_decision(
        float(ema_fast_prev),
        float(ema_slow_prev),
        float(ema_fast),
        float(ema_slow),
    )
    if decision == "NONE":
        out["skip_reason"] = "no_cross"
        return out

    if decision == "BUY" and price <= float(ema_fast):
        out["skip_reason"] = "no_cross"
        return out
    if decision == "SELL" and price >= float(ema_fast):
        out["skip_reason"] = "no_cross"
        return out

    if h1_filter:
        h1_ema = get_ema(symbol, mt5.TIMEFRAME_H1, int(h1_period))
        if h1_ema is None:
            out["skip_reason"] = "data_missing"
            return out
        out["h1_ema"] = round(float(h1_ema), 5)
        if not _xauusd_h1_bias_allows(decision, price, float(h1_ema)):
            out["skip_reason"] = "h1_bias"
            return out

    if h4_filter:
        trend_h4 = get_trend_h4(symbol)
        out["trend_h4"] = str(trend_h4)
        if not _xauusd_h4_bias_allows(decision, str(trend_h4)):
            out["skip_reason"] = "h4_bias"
            return out

    out["decision"] = decision
    return _xauusd_apply_anti_chase(symbol, cfg, out)


def _xauusd_flatten_outside_window(cfg: Config, now: Optional[datetime] = None) -> None:
    """Cierra XAU paralelo a mercado al salir de la ventana (ATR mode no lo hacía)."""
    if not _xauusd_should_flatten_now(cfg, now):
        return
    wall = now or datetime.now()
    sym = _xauusd_parallel_symbol(cfg)
    for p in _xauusd_parallel_positions(cfg):
        ticket = int(getattr(p, "ticket", 0) or 0)
        side = _position_side(p)
        lot = float(getattr(p, "volume", cfg.xauusd_parallel_lot) or cfg.xauusd_parallel_lot)
        profit = float(getattr(p, "profit", 0.0) or 0.0)
        if side is None or ticket <= 0:
            continue
        ok = _close_position_market(sym, ticket, lot, side, comment="xauusd_session_end")
        _xauusd_record_soft_close(ticket=ticket, profit=profit, when=wall, closed=bool(ok))
        _log_event(
            {
                "event": "XAUUSD_PARALLEL_CLOSE",
                "ts": wall.isoformat(timespec="seconds"),
                "symbol": sym,
                "ticket": ticket,
                "position_id": ticket,
                "side": side,
                "profit": round(profit, 4),
                "reason": "session_end",
                "closed": bool(ok),
                "magic": int(cfg.xauusd_parallel_magic),
                "hour": wall.hour,
            }
        )
        logging.info(
            "[%s] XAUUSD flatten outside window: ticket=%d profit=%.2f closed=%s",
            sym,
            ticket,
            profit,
            ok,
        )


def _xauusd_scalping_signal(symbol: str, cfg: Optional[Config] = None) -> Signal:
    """Compat: solo la decisión BUY/SELL/NONE."""
    return _xauusd_scalping_eval(symbol, cfg)["decision"]  # type: ignore[return-value]


def manage_xauusd_parallel_positions(cfg: Config) -> None:
    """
    Gestión de salida XAUUSD paralelo:
      - flatten al salir de ventana / fin de semana (ATR y money)
      - modo money: profit >= tp_money → cierre; profit <= -sl_money → cierre
    En modo atr las salidas normales son SL/TP del bróker.
    """
    if not bool(cfg.xauusd_parallel_enabled):
        return
    _xauusd_flatten_outside_window(cfg)
    if _xauusd_parallel_sl_mode(cfg) != "money":
        return

    sl_money = float(getattr(cfg, "xauusd_parallel_sl_money", 2.00) or 2.00)
    max_loss_money = -sl_money

    sym = _xauusd_parallel_symbol(cfg)
    tp_money = float(getattr(cfg, "xauusd_parallel_tp_money", 2.00) or 2.00)
    for p in _xauusd_parallel_positions(cfg):
        profit = float(getattr(p, "profit", 0.0) or 0.0)
        ticket = int(getattr(p, "ticket", 0) or 0)
        side = _position_side(p)
        lot = float(getattr(p, "volume", cfg.xauusd_parallel_lot) or cfg.xauusd_parallel_lot)
        if side is None or ticket <= 0:
            continue
        if profit >= tp_money:
            logging.info(
                "[%s] XAUUSD parallel TP hit: profit=%.4f >= %.4f. Closing ticket=%d.",
                sym,
                profit,
                tp_money,
                ticket,
            )
            ok = _close_position_market(sym, ticket, lot, side, comment="xauusd_parallel_tp")
            close_now = datetime.now()
            _xauusd_record_soft_close(ticket=ticket, profit=profit, when=close_now, closed=bool(ok))
            _log_event(
                {
                    "event": "XAUUSD_PARALLEL_CLOSE",
                    "ts": close_now.isoformat(timespec="seconds"),
                    "symbol": sym,
                    "ticket": ticket,
                    "position_id": ticket,
                    "side": side,
                    "profit": round(profit, 4),
                    "tp_money": tp_money,
                    "reason": "take_profit",
                    "closed": bool(ok),
                    "magic": int(cfg.xauusd_parallel_magic),
                    "hour": close_now.hour,
                }
            )
        # Cierre automático si la pérdida alcanza el SL configurado
        elif profit <= max_loss_money:
            logging.info(
                "[%s] XAUUSD parallel SL limit hit: profit=%.4f <= %.4f. Closing ticket=%d to prevent deeper loss.",
                sym,
                profit,
                max_loss_money,
                ticket,
            )
            ok = _close_position_market(sym, ticket, lot, side, comment="xauusd_parallel_loss_limit")
            close_now = datetime.now()
            _xauusd_record_soft_close(ticket=ticket, profit=profit, when=close_now, closed=bool(ok))
            _log_event(
                {
                    "event": "XAUUSD_PARALLEL_CLOSE",
                    "ts": close_now.isoformat(timespec="seconds"),
                    "symbol": sym,
                    "ticket": ticket,
                    "position_id": ticket,
                    "side": side,
                    "profit": round(profit, 4),
                    "loss_limit_money": max_loss_money,
                    "reason": "max_loss_limit",
                    "closed": bool(ok),
                    "magic": int(cfg.xauusd_parallel_magic),
                    "hour": close_now.hour,
                }
            )


def open_xauusd_parallel_trade(
    cfg: Config,
    side: Signal,
    *,
    signal_features: Optional[dict[str, Any]] = None,
) -> bool:
    """Abre una operación XAUUSD con magic dedicado y SL/TP físicos en el bróker."""
    if side not in ("BUY", "SELL"):
        return False
    if not bool(cfg.xauusd_parallel_enabled):
        return False

    sym = _xauusd_parallel_symbol(cfg)
    max_pos = max(1, int(getattr(cfg, "xauusd_parallel_max_positions", 1) or 1))
    open_n = len(_xauusd_parallel_positions(cfg))
    if open_n >= max_pos:
        _xauusd_log_skip(
            sym,
            "max_positions",
            open_positions=open_n,
            max_positions=max_pos,
        )
        return False

    if not _ensure_symbol(sym):
        _xauusd_log_skip(sym, "symbol_unavailable", force=True)
        return False

    max_spread = float(getattr(cfg, "xauusd_parallel_max_spread_points", 55.0) or 55.0)
    ok_spread, spread, avg_spread = _is_spread_ok(cfg, sym, max_spread_points=max_spread)
    if not ok_spread:
        if spread is None:
            logging.warning("[%s] XAUUSD parallel: could not read spread. Skipping.", sym)
            _xauusd_log_skip(sym, "spread_unavailable", max_spread_points=max_spread)
        else:
            logging.info(
                "[%s] XAUUSD parallel: spread too high (%.1f > %.1f pts). Skipping.",
                sym,
                spread,
                max_spread,
            )
            _xauusd_log_skip(
                sym,
                "spread_high",
                spread_points=float(spread),
                max_spread_points=max_spread,
                avg_spread_points=float(avg_spread) if avg_spread is not None else None,
            )
        return False

    tick = mt5.symbol_info_tick(sym)
    info = mt5.symbol_info(sym)
    if tick is None or info is None:
        logging.error("[%s] XAUUSD parallel: missing tick/info.", sym)
        _xauusd_log_skip(sym, "tick_missing", force=True)
        return False

    term = mt5.terminal_info()
    if term is not None and hasattr(term, "trade_allowed") and not bool(getattr(term, "trade_allowed", True)):
        logging.error("[%s] XAUUSD parallel: terminal trade not allowed.", sym)
        _xauusd_log_skip(sym, "trade_not_allowed", force=True)
        return False

    lot = _clamp_volume(sym, float(getattr(cfg, "xauusd_parallel_lot", cfg.lot) or cfg.lot))
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price = float(tick.ask if side == "BUY" else tick.bid)
    digits = int(info.digits)
    magic = int(getattr(cfg, "xauusd_parallel_magic", 0) or 0)

    request: dict = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": order_type,
        "price": price,
        "deviation": cfg.deviation_points,
        "magic": magic,
        "comment": "xauusd-parallel",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    # Provisional SL/TP desde quote (protección); se recalcula post-fill.
    exits = _xauusd_parallel_exit_distances(cfg, sym, lot, side)
    if exits is None:
        logging.warning("[%s] XAUUSD parallel: could not compute SL/TP distances. Skipping.", sym)
        _xauusd_log_skip(sym, "atr_missing" if _xauusd_parallel_sl_mode(cfg) == "atr" else "sl_tp_unavailable", force=True)
        return False
    sl_dist, tp_dist, exit_meta = exits
    tp_money = float(exit_meta.get("tp_money", getattr(cfg, "xauusd_parallel_tp_money", 2.00) or 2.00))
    sl_money = float(exit_meta.get("sl_money", getattr(cfg, "xauusd_parallel_sl_money", 2.00) or 2.00))

    sl0, tp0 = _xauusd_compute_sl_tp_prices(
        side=side,
        entry=price,
        sl_dist=float(sl_dist),
        tp_dist=float(tp_dist),
        digits=digits,
        spread_buffer=0.0,
    )
    request["sl"] = sl0
    request["tp"] = tp0
    # ----------------------------------------

    filling_candidates = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
    ]

    logging.info(
        "[%s] XAUUSD parallel opening %s | lot=%.2f | price=%.*f | spread=%.1f pts | SL=%.*f | TP=%.*f",
        sym,
        side,
        lot,
        digits,
        price,
        spread if spread is not None else 0.0,
        digits,
        request.get("sl", 0.0),
        digits,
        request.get("tp", 0.0),
    )
    result = _order_send_deal_try_fillings(sym, request, filling_candidates)
    if result is None:
        logging.error("[%s] XAUUSD parallel: order_send returned None. last_error=%s", sym, mt5.last_error())
        _xauusd_log_skip(sym, "order_send_none", side=side, force=True)
        return False
    if int(getattr(result, "retcode", -1)) != int(mt5.TRADE_RETCODE_DONE):
        logging.error(
            "[%s] XAUUSD parallel order failed. retcode=%s | comment=%s",
            sym,
            getattr(result, "retcode", "n/a"),
            getattr(result, "comment", ""),
        )
        _xauusd_log_skip(
            sym,
            "order_rejected",
            side=side,
            retcode=int(getattr(result, "retcode", -1)),
            comment=str(getattr(result, "comment", "") or ""),
            force=True,
        )
        return False

    _last_trade_time[sym] = datetime.now()
    _record_symbol_open_timestamp(sym)
    open_now = datetime.now()
    global _XAUUSD_LAST_OPEN_AT, _XAUUSD_LAST_ENTRY_BAR_TIME, _XAUUSD_LAST_CROSS_BAR_TIME
    _XAUUSD_LAST_OPEN_AT = open_now
    bar_t = _xauusd_m15_bar_time(sym)
    if bar_t is not None:
        _XAUUSD_LAST_ENTRY_BAR_TIME = bar_t
    cross_bar = None
    if signal_features:
        try:
            cross_bar = int(signal_features.get("cross_bar_time") or 0) or None
        except (TypeError, ValueError):
            cross_bar = None
    if cross_bar is not None:
        _XAUUSD_LAST_CROSS_BAR_TIME = int(cross_bar)

    deal_ticket = getattr(result, "deal", None)
    order_ticket = getattr(result, "order", None)
    pos_id = _position_id_from_deal_ticket(deal_ticket)

    # --- SL/TP desde fill (evita TP detrás del entry por slippage) ---
    fill_price = float(getattr(result, "price", 0.0) or 0.0)
    pos_ticket = int(pos_id or 0)
    if fill_price <= 0 or pos_ticket <= 0:
        positions = _xauusd_parallel_positions(cfg)
        if positions:
            p0 = positions[0]
            if fill_price <= 0:
                fill_price = float(getattr(p0, "price_open", 0.0) or 0.0)
            if pos_ticket <= 0:
                pos_ticket = int(getattr(p0, "ticket", 0) or 0)

    final_sl = request.get("sl")
    final_tp = request.get("tp")
    spread_buf = 0.0
    if bool(getattr(cfg, "xauusd_parallel_sl_spread_buffer", True)):
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if ask > bid > 0:
            spread_buf = ask - bid

    if fill_price > 0 and pos_ticket > 0:
        final_sl, final_tp = _xauusd_compute_sl_tp_prices(
            side=side,
            entry=fill_price,
            sl_dist=float(sl_dist),
            tp_dist=float(tp_dist),
            digits=digits,
            spread_buffer=spread_buf,
        )
        tick_after = mt5.symbol_info_tick(sym) or tick
        ok_mod = _modify_position_sl_tp(
            sym,
            pos_ticket,
            final_sl,
            final_tp,
            side=side,
            info=info,
            tick=tick_after,
        )
        if not ok_mod:
            logging.warning(
                "[%s] XAUUSD parallel: post-fill SL/TP modify failed (fill=%.*f sl=%.*f tp=%.*f).",
                sym,
                digits,
                fill_price,
                digits,
                final_sl,
                digits,
                final_tp,
            )

    feats = signal_features or {}
    _xauusd_record_hourly(
        "open",
        when=open_now,
        spread_points=float(spread) if spread is not None else None,
    )
    _log_event(
        {
            "event": "XAUUSD_PARALLEL_OPEN",
            "ts": open_now.isoformat(timespec="seconds"),
            "symbol": sym,
            "side": side,
            "lot": lot,
            "price": price,
            "fill_price": fill_price if fill_price > 0 else None,
            "spread_points": float(spread) if spread is not None else None,
            "spread_buffer": round(spread_buf, digits) if spread_buf else 0.0,
            "sl": final_sl,
            "tp": final_tp,
            "tp_money": tp_money,
            "sl_money": sl_money,
            "sl_mode": exit_meta.get("mode"),
            "atr": exit_meta.get("atr"),
            "atr_mult": exit_meta.get("atr_mult"),
            "tp_rr": exit_meta.get("tp_rr"),
            "sl_dist": exit_meta.get("sl_dist", sl_dist),
            "tp_dist": exit_meta.get("tp_dist", tp_dist),
            "h1_ema": feats.get("h1_ema"),
            "trend_h4": feats.get("trend_h4"),
            "h4_filter": feats.get("h4_filter"),
            "magic": magic,
            "trend_rule": "m15_cross_adx_h1_h4_atr",
            "hour": open_now.hour,
            "deal": deal_ticket,
            "order": order_ticket,
            "position_id": pos_id or (pos_ticket or None),
            "ema_fast": feats.get("ema_fast"),
            "ema_slow": feats.get("ema_slow"),
            "ema_fast_prev": feats.get("ema_fast_prev"),
            "ema_slow_prev": feats.get("ema_slow_prev"),
            "adx": feats.get("adx"),
            "adx_min": feats.get("adx_min", _XAUUSD_ADX_MIN),
            "signal_price": feats.get("price"),
            "cross_bar_time": feats.get("cross_bar_time"),
        }
    )

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_TRADE_OPEN", True):
        try:
            fill_for_msg = fill_price if fill_price > 0 else price
            sl_txt = f"{final_sl:.{digits}f}" if final_sl is not None else "—"
            tp_txt = f"{final_tp:.{digits}f}" if final_tp is not None else "—"
            acc = mt5.account_info()
            cur = str(getattr(acc, "currency", "") or "USD") if acc is not None else "USD"
            pos_extra = f"\nPosition ID: {pos_id or pos_ticket}" if (pos_id or pos_ticket) else ""
            if str(exit_meta.get("mode") or "") == "atr":
                target_txt = (
                    f"ATR×{float(exit_meta.get('atr_mult') or 0):.2f} · "
                    f"RR {float(exit_meta.get('tp_rr') or 0):.2f}"
                )
            else:
                target_txt = f"target ${tp_money:.2f}/${sl_money:.2f}"
            telegram_notify.notify(
                f"meta-trader: orden ejecutada (XAUUSD scalper)\n"
                f"{sym} {side} · lot {lot:.2f} · fill {fill_for_msg:.{digits}f} {cur}\n"
                f"SL {sl_txt} · TP {tp_txt} · {target_txt}\n"
                f"Order {order_ticket} · Deal {deal_ticket}{pos_extra}",
                dedup_key=None,
            )
        except Exception:
            logging.exception("Telegram notify (XAUUSD open) failed (non-fatal).")
    return True


def _count_all_bot_open_positions(cfg: Config) -> int:
    """Posiciones del magic principal (gestionadas) + magic XAU parallel."""
    n = int(_count_managed_open_positions(cfg))
    if bool(getattr(cfg, "xauusd_parallel_enabled", False)):
        n += len(_xauusd_parallel_positions(cfg))
    return n


def run_xauusd_parallel_cycle(cfg: Config) -> None:
    global _XAUUSD_LAST_SIGNAL
    if not bool(cfg.xauusd_parallel_enabled):
        return

    sym = _xauusd_parallel_symbol(cfg)

    # 1. Filtro Horario (sessions multi-franja o start/end hour)
    now = datetime.now()
    if _weekend_blocks_new_trade(cfg, sym, now):
        _xauusd_log_skip(
            sym,
            "weekend_block",
            when=now,
            weekday=int(now.weekday()),
            count_hourly=False,
        )
        return
    if not _xauusd_parallel_in_window(cfg, now):
        _xauusd_log_skip(
            sym,
            "outside_hours",
            when=now,
            window=_xauusd_parallel_window_label(cfg),
            start_hour=int(cfg.xauusd_parallel_start_hour),
            end_hour=int(cfg.xauusd_parallel_end_hour),
            count_hourly=False,
        )
        return

    # 1b. Risk compartido con el loop principal (daily / monthly / max opens)
    reached_daily, pnl_today = daily_loss_limit_reached(cfg.daily_loss_limit)
    if reached_daily:
        _xauusd_log_skip(
            sym,
            "daily_loss_limit",
            when=now,
            pnl_today=round(float(pnl_today), 2),
            daily_loss_limit=float(cfg.daily_loss_limit),
        )
        return
    monthly_reached, monthly_info = monthly_loss_limit_reached(cfg)
    if monthly_reached:
        _xauusd_log_skip(
            sym,
            "monthly_loss_limit",
            when=now,
            total_at_risk=monthly_info.get("total_at_risk"),
            threshold_money=monthly_info.get("threshold_money"),
        )
        return
    open_all = _count_all_bot_open_positions(cfg)
    max_total = int(getattr(cfg, "max_open_trades_total", 0) or 0)
    if max_total > 0 and open_all >= max_total:
        _xauusd_log_skip(
            sym,
            "max_open_trades_total",
            when=now,
            open_positions_bot=open_all,
            max_open_trades_total=max_total,
        )
        return

    # 2. Exposición
    max_pos = max(1, int(getattr(cfg, "xauusd_parallel_max_positions", 1) or 1))
    open_n = len(_xauusd_parallel_positions(cfg))
    if open_n >= max_pos:
        _xauusd_log_skip(
            sym,
            "max_positions",
            when=now,
            open_positions=open_n,
            max_positions=max_pos,
        )
        return

    # 2b. Cooldown post-apertura (evita martillar el mismo scalp cada poll)
    cd_left = _xauusd_cooldown_remaining_sec(cfg, now)
    if cd_left > 0:
        _xauusd_log_skip(
            sym,
            "cooldown",
            when=now,
            cooldown_remaining_sec=round(cd_left, 1),
            cooldown_seconds=float(getattr(cfg, "xauusd_parallel_cooldown_seconds", 300.0) or 300.0),
        )
        return

    # 2c. Una entrada por vela M15
    bar_t = _xauusd_m15_bar_time(sym)
    if (
        bool(getattr(cfg, "xauusd_parallel_one_trade_per_bar", True))
        and bar_t is not None
        and _XAUUSD_LAST_ENTRY_BAR_TIME is not None
        and int(bar_t) == int(_XAUUSD_LAST_ENTRY_BAR_TIME)
    ):
        _xauusd_log_skip(
            sym,
            "same_m15_bar",
            when=now,
            bar_time=int(bar_t),
        )
        return

    # 3. Señal (con features para auditoría)
    eval_ = _xauusd_scalping_eval(sym, cfg)
    decision: Signal = eval_["decision"]  # type: ignore[assignment]
    if decision == "NONE":
        _xauusd_log_skip(
            sym,
            str(eval_.get("skip_reason") or "no_cross"),
            when=now,
            ema_fast=eval_.get("ema_fast"),
            ema_slow=eval_.get("ema_slow"),
            adx=eval_.get("adx"),
            adx_min=eval_.get("adx_min"),
            price=eval_.get("price"),
            trend_h4=eval_.get("trend_h4"),
            h1_ema=eval_.get("h1_ema"),
        )
        return

    # 3b. Mismo cruce (misma vela cerrada) ya operado → no reabrir
    cross_bar = eval_.get("cross_bar_time")
    if (
        cross_bar is not None
        and _XAUUSD_LAST_CROSS_BAR_TIME is not None
        and int(cross_bar) == int(_XAUUSD_LAST_CROSS_BAR_TIME)
    ):
        _xauusd_log_skip(
            sym,
            "cross_already_handled",
            when=now,
            cross_bar_time=int(cross_bar),
        )
        return

    logging.info("[%s] XAUUSD parallel signal: %s → %s", sym, eval_.get("entry_mode") or "breakout", decision)
    _xauusd_record_hourly("signal", when=now)
    _XAUUSD_LAST_SIGNAL = {
        "ts": now.isoformat(timespec="seconds"),
        "decision": decision,
        "ema_fast": eval_.get("ema_fast"),
        "ema_slow": eval_.get("ema_slow"),
        "adx": eval_.get("adx"),
        "price": eval_.get("price"),
        "cross_bar_time": cross_bar,
        "hour": now.hour,
    }
    _log_event(
        {
            "event": "XAUUSD_PARALLEL_SIGNAL",
            "ts": now.isoformat(timespec="seconds"),
            "symbol": sym,
            "trend_m15_scalp": decision,
            "decision": decision,
            "hour": now.hour,
            "ema_fast": eval_.get("ema_fast"),
            "ema_slow": eval_.get("ema_slow"),
            "ema_fast_prev": eval_.get("ema_fast_prev"),
            "ema_slow_prev": eval_.get("ema_slow_prev"),
            "adx": eval_.get("adx"),
            "adx_min": eval_.get("adx_min"),
            "ema_fast_period": eval_.get("ema_fast_period"),
            "ema_slow_period": eval_.get("ema_slow_period"),
            "price": eval_.get("price"),
            "bar_time": bar_t,
            "cross_bar_time": cross_bar,
            "entry_mode": eval_.get("entry_mode") or _xauusd_entry_mode(cfg),
            "trend_h4": eval_.get("trend_h4"),
            "h4_filter": eval_.get("h4_filter"),
            "h1_ema": eval_.get("h1_ema"),
        }
    )
    open_xauusd_parallel_trade(cfg, decision, signal_features=eval_)


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
            if cfg.xauusd_parallel_enabled:
                manage_xauusd_parallel_positions(cfg)
                _xauusd_ingest_broker_closes(cfg)


def main() -> int:
    _load_dotenv()
    _rotate_trades_log_on_start()
    _setup_logging()
    # BOT_SYMBOLS unset → default Config.symbols; set but empty → main loop off (solo parallel).
    _raw_symbols = _env("BOT_SYMBOLS")
    if _raw_symbols is None:
        symbols_effective: Tuple[str, ...] = Config.symbols
    else:
        symbols_effective = tuple(p.strip() for p in _raw_symbols.split(",") if p.strip())
    merged_symbol_cfg, _overlay_symbol_cfg = resolve_symbol_config_with_overlay()
    cfg = Config(
        symbols=symbols_effective,
        lot=float(_env("BOT_LOT", "0.01")),
        symbol_config=merged_symbol_cfg,
        max_spread_points=float(_env("BOT_MAX_SPREAD_POINTS", "25")),
        spread_dynamic_enabled=_env_bool("BOT_SPREAD_DYNAMIC_ENABLED", False),
        spread_dynamic_alpha=float(_env("BOT_SPREAD_DYNAMIC_ALPHA", "0.15")),
        spread_dynamic_mult=float(_env("BOT_SPREAD_DYNAMIC_MULT", "1.5")),
        poll_seconds=int(float(_env("BOT_POLL_SECONDS", "30"))),
        manage_positions_interval_seconds=float(_env("BOT_MANAGE_POSITIONS_INTERVAL_SECONDS", "5")),
        max_open_trades=int(float(_env("BOT_MAX_OPEN_TRADES", "5"))),
        max_open_trades_total=int(float(_env("BOT_MAX_OPEN_TRADES_TOTAL", "5"))),
        max_positions_per_symbol=int(float(_env("BOT_MAX_POSITIONS_PER_SYMBOL", "1"))),
        pyramiding_require_be=_env_bool("BOT_PYRAMIDING_REQUIRE_BE", True),
        pyramiding_min_separation_points=float(_env("BOT_PYRAMIDING_MIN_SEPARATION_POINTS", "0")),
        daily_loss_limit=float(_env("BOT_DAILY_LOSS_LIMIT", "-60.0")),
        monthly_loss_limit_enabled=_env_bool("BOT_MONTHLY_LOSS_LIMIT_ENABLED", False),
        monthly_loss_limit_pct=float(_env("BOT_MONTHLY_LOSS_LIMIT_PCT", "6.0")),
        monthly_loss_limit_include_open_risk=_env_bool("BOT_MONTHLY_LOSS_LIMIT_INCLUDE_OPEN_RISK", True),
        sl_points=float(_env("BOT_SL_POINTS", "80")),
        tp_points=float(_env("BOT_TP_POINTS", "160")),
        use_risk_sizing=_env_bool("BOT_USE_RISK_SIZING", True),
        risk_per_trade_pct=float(_env("BOT_RISK_PER_TRADE_PCT", "10")),
        cooldown_minutes=int(float(_env("BOT_COOLDOWN_MINUTES", "30"))),
        cooldown_minutes_crypto_floor=int(float(_env("BOT_COOLDOWN_MINUTES_CRYPTO_FLOOR", "6"))),
        failed_attempt_cooldown_seconds=float(_env("BOT_FAILED_ATTEMPT_COOLDOWN_SECONDS", "120")),
        max_trades_per_hour_per_symbol=int(float(_env("BOT_MAX_TRADES_PER_HOUR_PER_SYMBOL", "0"))),
        max_trades_per_hour_crypto_default=int(float(_env("BOT_MAX_TRADES_PER_HOUR_CRYPTO_DEFAULT", "8"))),
        start_hour=int(float(_env("BOT_START_HOUR", str(Config.start_hour)))),
        end_hour=int(float(_env("BOT_END_HOUR", str(Config.end_hour)))),
        no_new_trades_after_hour=int(float(_env("BOT_NO_NEW_TRADES_AFTER_HOUR", str(Config.no_new_trades_after_hour)))),
        no_new_trades_after_minute=int(float(_env("BOT_NO_NEW_TRADES_AFTER_MINUTE", str(Config.no_new_trades_after_minute)))),
        trading_window_enabled=_env_bool("BOT_TRADING_WINDOW_ENABLED", False),
        trading_window_sessions=_load_trading_window_sessions_from_env(),
        trading_window_timezone=str(_env("BOT_TRADING_WINDOW_TIMEZONE", "LOCAL") or "LOCAL"),
        weekend_block_enabled=_env_bool("BOT_WEEKEND_BLOCK_ENABLED", True),
        friday_cutoff_enabled=_env_bool("BOT_FRIDAY_CUTOFF_ENABLED", True),
        new_trade_cutoff_enabled=_env_bool("BOT_NEW_TRADE_CUTOFF_ENABLED", True),
        ema_period_m15=int(float(_env("BOT_EMA_PERIOD_M15", "50"))),
        ema_fast_m15=int(float(_env("BOT_EMA_FAST_M15", _env("BOT_EMA_PERIOD_M15", "50") or "50"))),
        ema_slow_m15=int(float(_env("BOT_EMA_SLOW_M15", "200"))),
        min_avg_range_points_m15=float(_env("BOT_MIN_AVG_RANGE_POINTS_M15", "0")),
        adx_period_m15=int(float(_env("BOT_ADX_PERIOD_M15", "14"))),
        adx_min=float(_env("BOT_ADX_MIN", "20")),
        crypto_sell_requires_h4_down=_env_bool("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN", True),
        price_slope_filter_enabled=_env_bool("BOT_PRICE_SLOPE_FILTER_ENABLED", False),
        price_slope_n=max(2, int(float(_env("BOT_PRICE_SLOPE_N", "6")))),
        regime_lot_sizing_enabled=_env_bool("BOT_REGIME_LOT_SIZING_ENABLED", False),
        regime_lot_multiplier_by_label=_parse_regime_lot_mult_json(_env("BOT_REGIME_LOT_MULT_JSON")),
        max_lot_per_trade=float(_env("BOT_MAX_LOT_PER_TRADE", "0")),
        range_filter_enabled=_env_bool("BOT_RANGE_FILTER_ENABLED", False),
        range_filter_timeframe=str(_env("BOT_RANGE_FILTER_TIMEFRAME", "H1") or "H1"),
        range_filter_lookback_bars=int(float(_env("BOT_RANGE_FILTER_LOOKBACK_BARS", "24"))),
        range_filter_mid_frac=float(_env("BOT_RANGE_FILTER_MID_FRAC", "0.30")),
        candle_filter_enabled=_env_bool("BOT_CANDLE_FILTER_ENABLED", False),
        candle_body_min_frac=float(_env("BOT_CANDLE_BODY_MIN_FRAC", "0.60")),
        candle_wick_max_frac=float(_env("BOT_CANDLE_WICK_MAX_FRAC", "0.35")),
        candle_require_confirmation=_env_bool("BOT_CANDLE_REQUIRE_CONFIRMATION", False),
        break_even_trigger_money=float(_env("BOT_BREAK_EVEN_TRIGGER_MONEY", "0.5")),
        break_even_offset_points=float(_env("BOT_BREAK_EVEN_OFFSET_POINTS", "0")),
        trail_trigger_money=float(_env("BOT_TRAIL_TRIGGER_MONEY", "1.0")),
        trail_new_sl_money=float(_env("BOT_TRAIL_NEW_SL_MONEY", "0.5")),
        trail_new_tp_money=float(_env("BOT_TRAIL_NEW_TP_MONEY", "1.2")),
        news_block_hours=_env_int_list("BOT_NEWS_BLOCK_HOURS"),
        quiet_session_block_enabled=_env_bool("BOT_QUIET_SESSION_BLOCK_ENABLED", False),
        quiet_session_hours=_env_int_list("BOT_QUIET_SESSION_HOURS") or (23, 0, 1, 2, 3),
        quiet_session_exempt_symbols=tuple(_env_str_list("BOT_QUIET_SESSION_EXEMPT_SYMBOLS") or ("BTCUSD", "ETHUSD")),
        anti_chase_enabled=_env_bool("BOT_ANTI_CHASE_ENABLED", True),
        anti_chase_atr_period=int(float(_env("BOT_ANTI_CHASE_ATR_PERIOD", "20"))),
        anti_chase_atr_mult=float(_env("BOT_ANTI_CHASE_ATR_MULT", "2.0")),
        anti_chase_atr_mult_crypto_floor=float(_env("BOT_ANTI_CHASE_ATR_MULT_CRYPTO_FLOOR", "2.65")),
        filter_exit_enabled=_env_bool("BOT_FILTER_EXIT_ENABLED", True),
        filter_exit_adx_min=float(_env("BOT_FILTER_EXIT_ADX_MIN", "0")),
        filter_exit_require_ema_reversal=_env_bool("BOT_FILTER_EXIT_REQUIRE_EMA_REVERSAL", True),
        filter_exit_min_profit_money=float(_env("BOT_FILTER_EXIT_MIN_PROFIT_MONEY", "0")),
        tight_trail_enabled=_env_bool("BOT_TIGHT_TRAIL_ENABLED", True),
        tight_trail_trigger_money=float(_env("BOT_TIGHT_TRAIL_TRIGGER_MONEY", "2.0")),
        tight_trail_atr_period=int(float(_env("BOT_TIGHT_TRAIL_ATR_PERIOD", "14"))),
        tight_trail_atr_mult=float(_env("BOT_TIGHT_TRAIL_ATR_MULT", "1.2")),
        sl_tp_atr_enabled=_env_bool("BOT_SL_TP_ATR_ENABLED", False),
        sl_tp_atr_timeframe=str(_env("BOT_SL_TP_ATR_TIMEFRAME", "M15") or "M15"),
        sl_tp_atr_period=int(float(_env("BOT_SL_TP_ATR_PERIOD", "14"))),
        sl_atr_mult=float(_env("BOT_SL_ATR_MULT", "2.0")),
        tp_atr_mult=float(_env("BOT_TP_ATR_MULT", "4.0")),
        sl_tp_atr_min_points=float(_env("BOT_SL_TP_ATR_MIN_POINTS", "0")),
        correlation_groups=_parse_correlation_groups(_env("BOT_CORRELATION_GROUPS")),
        max_positions_per_group=int(float(_env("BOT_MAX_POSITIONS_PER_GROUP", "0"))),
        portfolio_enabled=_env_bool("BOT_PORTFOLIO_ENABLED", False),
        portfolio_weights=_parse_portfolio_weights(_env("BOT_PORTFOLIO_WEIGHTS"), symbols_effective or Config.symbols),
        portfolio_dynamic_enabled=_env_bool("BOT_PORTFOLIO_DYNAMIC_ENABLED", False),
        portfolio_dynamic_lookback_days=int(float(_env("BOT_PORTFOLIO_DYNAMIC_LOOKBACK_DAYS", "14"))),
        portfolio_dynamic_rebalance_minutes=int(float(_env("BOT_PORTFOLIO_DYNAMIC_REBALANCE_MINUTES", "60"))),
        portfolio_dynamic_smoothing_alpha=float(_env("BOT_PORTFOLIO_DYNAMIC_SMOOTH_ALPHA", "0.35")),
        portfolio_dynamic_min_weight=float(_env("BOT_PORTFOLIO_DYNAMIC_MIN_WEIGHT", "0.05")),
        portfolio_dynamic_max_weight=float(_env("BOT_PORTFOLIO_DYNAMIC_MAX_WEIGHT", "0.60")),
        portfolio_dynamic_use_positive_only=_env_bool("BOT_PORTFOLIO_DYNAMIC_POSITIVE_ONLY", True),
        weekend_trade_symbols=tuple(_env_str_list("BOT_WEEKEND_TRADE_SYMBOLS") or ()),
        track_zero_magic_positions=_env_bool("BOT_TRACK_ZERO_MAGIC_POSITIONS", True),
        cancel_orphan_pending_on_start=_env_bool("BOT_CANCEL_ORPHAN_PENDING_ON_START", False),
        open_sl_at_entry=_env_bool("BOT_OPEN_SL_AT_ENTRY", False),
        open_sl_at_entry_max_extra_points=int(float(_env("BOT_OPEN_SL_AT_ENTRY_MAX_EXTRA_POINTS", "400"))),
        open_sl_at_entry_min_extra_points=int(float(_env("BOT_OPEN_SL_AT_ENTRY_MIN_EXTRA_POINTS", "0"))),
        profit_lock_enabled=_env_bool("BOT_PROFIT_LOCK_ENABLED", False),
        profit_lock_step_money=float(_env("BOT_PROFIT_LOCK_STEP_MONEY", "1.0")),
        profit_lock_secure_money=float(_env("BOT_PROFIT_LOCK_SECURE_MONEY", "1.0")),
        skip_tight_trail_when_profit_lock=_env_bool("BOT_SKIP_TIGHT_TRAIL_WHEN_PROFIT_LOCK", True),
        usd_sl_ladder_enabled=_env_bool("BOT_USD_SL_LADDER_ENABLED", True),
        usd_sl_ladder_initial_sl_money=float(_env("BOT_USD_SL_LADDER_INITIAL_SL_USD", "0.8")),
        usd_ladder_wait_be_seconds=int(float(_env("BOT_USD_LADDER_WAIT_BE_SECONDS", "180"))),
        usd_ladder_step_seconds=int(float(_env("BOT_USD_LADDER_STEP_SECONDS", "120"))),
        usd_ladder_max_lock_usd=int(float(_env("BOT_USD_LADDER_MAX_LOCK_USD", "0"))),
        atr_profit_trail_enabled=_env_bool("BOT_ATR_PROFIT_TRAIL_ENABLED", False),
        atr_profit_trail_activation_mult=float(_env("BOT_ATR_PROFIT_TRAIL_ACTIVATION_MULT", "1.5")),
        atr_profit_trail_atr_period=int(float(_env("BOT_ATR_PROFIT_TRAIL_ATR_PERIOD", "14"))),
        atr_profit_trail_atr_mult=float(_env("BOT_ATR_PROFIT_TRAIL_ATR_MULT", "2.5")),
        micro_be_trigger_money=float(_env("BOT_MICRO_BE_TRIGGER_USD", "0")),
        hwm_trail_pct=float(_env("BOT_HWM_TRAIL_PCT", "0")),
        stagnant_timeout_minutes=float(_env("BOT_STAGNANT_TIMEOUT_MINUTES", "0")),
        stagnant_max_profit_threshold=float(_env("BOT_STAGNANT_MAX_FP_THRESHOLD", "0")),
        xauusd_parallel_enabled=_env_bool("BOT_XAUUSD_PARALLEL_ENABLED", False),
        xauusd_parallel_symbol=str(_env("BOT_XAUUSD_PARALLEL_SYMBOL", "XAUUSD") or "XAUUSD").strip().upper(),
        xauusd_parallel_magic=int(float(_env("BOT_XAUUSD_PARALLEL_MAGIC", str(int(Config.magic) + 1)))),
        xauusd_parallel_lot=float(_env("BOT_XAUUSD_PARALLEL_LOT", _env("BOT_LOT", "0.01") or "0.01")),
        xauusd_parallel_tp_money=float(_env("BOT_XAUUSD_PARALLEL_TP_MONEY", "2.00")),
        xauusd_parallel_sl_money=float(_env("BOT_XAUUSD_PARALLEL_SL_MONEY", "2.00")),
        xauusd_parallel_sl_mode=str(_env("BOT_XAUUSD_PARALLEL_SL_MODE", "atr") or "atr").strip().lower(),
        xauusd_parallel_sl_atr_mult=float(_env("BOT_XAUUSD_PARALLEL_SL_ATR_MULT", "1.5")),
        xauusd_parallel_tp_rr=float(_env("BOT_XAUUSD_PARALLEL_TP_RR", "2.0")),
        xauusd_parallel_sl_atr_min=float(_env("BOT_XAUUSD_PARALLEL_SL_ATR_MIN", "1.5")),
        xauusd_parallel_atr_period=int(float(_env("BOT_XAUUSD_PARALLEL_ATR_PERIOD", "14"))),
        xauusd_parallel_adx_min=float(_env("BOT_XAUUSD_PARALLEL_ADX_MIN", "25")),
        xauusd_parallel_h1_filter=_env_bool("BOT_XAUUSD_PARALLEL_H1_FILTER", True),
        xauusd_parallel_h4_filter=_env_bool("BOT_XAUUSD_PARALLEL_H4_FILTER", True),
        xauusd_parallel_h1_ema_period=int(float(_env("BOT_XAUUSD_PARALLEL_H1_EMA_PERIOD", "20"))),
        xauusd_parallel_start_hour=int(float(_env("BOT_XAUUSD_PARALLEL_START_HOUR", "10"))),
        xauusd_parallel_end_hour=int(float(_env("BOT_XAUUSD_PARALLEL_END_HOUR", "16"))),
        xauusd_parallel_sessions=str(_env("BOT_XAUUSD_PARALLEL_SESSIONS", "10:00-16:00,19:00-21:00") or "").strip(),
        xauusd_parallel_max_positions=max(1, int(float(_env("BOT_XAUUSD_PARALLEL_MAX_POSITIONS", "1")))),
        xauusd_parallel_max_spread_points=float(_env("BOT_XAUUSD_PARALLEL_MAX_SPREAD_POINTS", "55")),
        xauusd_parallel_cooldown_seconds=float(_env("BOT_XAUUSD_PARALLEL_COOLDOWN_SECONDS", "300")),
        xauusd_parallel_one_trade_per_bar=_env_bool("BOT_XAUUSD_PARALLEL_ONE_TRADE_PER_BAR", True),
        xauusd_parallel_sl_spread_buffer=_env_bool("BOT_XAUUSD_PARALLEL_SL_SPREAD_BUFFER", True),
        xauusd_parallel_entry_mode=str(_env("BOT_XAUUSD_PARALLEL_ENTRY_MODE", "breakout") or "breakout").strip().lower(),
        xauusd_parallel_flatten_outside_window=_env_bool("BOT_XAUUSD_PARALLEL_FLATTEN_OUTSIDE_WINDOW", True),
        xauusd_parallel_anti_chase=_env_bool("BOT_XAUUSD_PARALLEL_ANTI_CHASE", True),
        xauusd_parallel_anti_chase_atr_mult=float(_env("BOT_XAUUSD_PARALLEL_ANTI_CHASE_ATR_MULT", "2.0")),
        xauusd_parallel_exhaustion_atr_mult=float(_env("BOT_XAUUSD_PARALLEL_EXHAUSTION_ATR_MULT", "1.5")),
        xauusd_parallel_exhaustion_extreme_frac=float(_env("BOT_XAUUSD_PARALLEL_EXHAUSTION_EXTREME_FRAC", "0.20")),
        xauusd_parallel_extension_atr_max=float(_env("BOT_XAUUSD_PARALLEL_EXTENSION_ATR_MAX", "1.0")),
        xauusd_parallel_m1_spike_atr_mult=float(_env("BOT_XAUUSD_PARALLEL_M1_SPIKE_ATR_MULT", "0.80")),
    )

    rr_raw = float(cfg.xauusd_parallel_tp_rr)
    adx_raw = float(cfg.xauusd_parallel_adx_min)
    rr_eff = _xauusd_effective_tp_rr(cfg)
    adx_eff = _xauusd_effective_adx_min(cfg)
    if rr_raw < rr_eff or adx_raw < adx_eff:
        logging.warning(
            "XAUUSD parallel clamps applied: TP_RR %.2f→%.2f, ADX_MIN %.1f→%.1f "
            "(RR<1.5 was negative expectancy in live 16–17 Aug).",
            rr_raw,
            rr_eff,
            adx_raw,
            adx_eff,
        )
        cfg = replace(cfg, xauusd_parallel_tp_rr=rr_eff, xauusd_parallel_adx_min=adx_eff)

    if _overlay_symbol_cfg:
        logging.info(
            "[Config] Merged symbol_config_overlay.json over .env BOT_SYMBOL_CONFIG (%d symbol(s) patched).",
            len(_overlay_symbol_cfg),
        )

    pid = os.getpid()
    if cfg.trading_window_enabled and cfg.trading_window_sessions:
        rng = "; ".join(f"{a.strftime('%H:%M')}-{b.strftime('%H:%M')}" for a, b in cfg.trading_window_sessions)
        logging.info(
            "Trading window: TZ=%s | sessions=[%s] (outside ⇒ no new entries)",
            cfg.trading_window_timezone,
            rng,
        )
    logging.info(
        "Starting bot | PID=%s | Symbols=%s | poll=%ss | manage_interval=%ss | lot=%.2f | max_spread=%.1f pts",
        pid,
        cfg.symbols,
        cfg.poll_seconds,
        cfg.manage_positions_interval_seconds,
        cfg.lot,
        cfg.max_spread_points,
    )
    if cfg.xauusd_parallel_enabled:
        logging.info(
            "XAUUSD parallel strategy ON | symbol=%s | magic=%d | lot=%.4f | tp=$%.2f | sl=$%.2f | "
            "window=%s | RR=%.2f | ADX>=%.0f | mode=%s | flatten_off_window=%s | max_pos=%d | cooldown=%ss | "
            "one_per_m15=%s | sl_spread_buf=%s (main loop skips %s; breakout M15 + ATR SL/TP)",
            cfg.xauusd_parallel_symbol,
            cfg.xauusd_parallel_magic,
            cfg.xauusd_parallel_lot,
            cfg.xauusd_parallel_tp_money,
            cfg.xauusd_parallel_sl_money,
            _xauusd_parallel_window_label(cfg),
            float(cfg.xauusd_parallel_tp_rr),
            float(cfg.xauusd_parallel_adx_min),
            _xauusd_entry_mode(cfg),
            bool(cfg.xauusd_parallel_flatten_outside_window),
            cfg.xauusd_parallel_max_positions,
            int(cfg.xauusd_parallel_cooldown_seconds),
            bool(cfg.xauusd_parallel_one_trade_per_bar),
            bool(cfg.xauusd_parallel_sl_spread_buffer),
            cfg.xauusd_parallel_symbol,
        )
    # Snapshot de tracks activos para evaluación multi-instrumento (parseable en trades.log).
    boot_tracks: list[dict[str, Any]] = []
    if cfg.xauusd_parallel_enabled:
        boot_tracks.append(
            {
                "track": "xau_parallel_breakout",
                "symbol": cfg.xauusd_parallel_symbol,
                "magic": cfg.xauusd_parallel_magic,
                "window": f"{_xauusd_parallel_window_label(cfg)} LOCAL",
                "lot": cfg.xauusd_parallel_lot,
                "sl_mode": cfg.xauusd_parallel_sl_mode,
                "tp_rr": cfg.xauusd_parallel_tp_rr,
            }
        )
    for sym in cfg.symbols:
        p_boot = _effective_params(cfg, sym)
        boot_tracks.append(
            {
                "track": str(p_boot.get("eval_track") or "main_loop"),
                "symbol": sym,
                "magic": cfg.magic,
                "window": str(_sym_get(cfg, sym, "trading_window_sessions", "") or "(global/24x5)"),
                "lot": float(p_boot.get("lot") or 0.0) or cfg.lot,
                "adx_min": float(p_boot.get("adx_min") or 0.0),
                "sl_atr_mult": float(p_boot.get("sl_atr_mult") or 0.0),
                "tp_atr_mult": float(p_boot.get("tp_atr_mult") or 0.0),
            }
        )
    _log_event(
        {
            "event": "EVAL_TRACKS_BOOT",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "pid": pid,
            "tracks": boot_tracks,
            "daily_loss_limit": cfg.daily_loss_limit,
            "max_open_trades_total": cfg.max_open_trades_total,
            "crypto_sell_requires_h4_down": bool(cfg.crypto_sell_requires_h4_down),
            "trading_window_timezone": cfg.trading_window_timezone,
        }
    )
    logging.info("EVAL tracks boot: %s", json.dumps(boot_tracks, ensure_ascii=False, default=str))
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
        if telegram_notify.telegram_configured():
            telegram_notify.notify(
                "meta-trader: no se pudo conectar a MT5 al inicio (credenciales / terminal cerrado).",
                dedup_key=None,
            )
        _save_stopped_state("mt5_connect_failed")
        return 2

    # Ensure symbols are ready
    for s in cfg.symbols:
        _ensure_symbol(s)
    if cfg.xauusd_parallel_enabled:
        _ensure_symbol(_xauusd_parallel_symbol(cfg))

    regime_detector = _maybe_market_regime_detector(cfg)

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
            "market_regimes": {},
        }
    )
    logging.info("bot_state.json actualizado con PID=%s (modo CONNECTED).", pid)

    if telegram_notify.telegram_configured() and telegram_notify.env_bool("TELEGRAM_NOTIFY_START", True):
        telegram_notify.notify(
            _telegram_format_startup_message(cfg, pid=pid, recon=recon),
            dedup_key=None,
        )
        recon_positions = list(recon.get("positions") or [])
        if recon_positions:
            lines = ["meta-trader: posiciones abiertas detectadas al arrancar"]
            for r in recon_positions:
                side = "BUY" if int(r.get("type", -1)) == 0 else "SELL"
                sym = str(r.get("symbol") or "?")
                sinfo = mt5.symbol_info(sym)
                digits = int(getattr(sinfo, "digits", 2)) if sinfo is not None else 2
                po = float(r.get("price_open") or 0.0)
                sl = float(r.get("sl") or 0.0)
                tp = float(r.get("tp") or 0.0)
                vol = float(r.get("volume") or 0.0)
                profit = float(r.get("profit") or 0.0)
                ticket = int(r.get("ticket") or 0)
                strategy = str(r.get("strategy") or _strategy_label_for_magic(cfg, int(r.get("magic") or -1)))
                strat_txt = "XAUUSD scalper" if strategy == "xauusd_parallel" else "loop"
                managed = "sí" if bool(r.get("managed_by_bot")) else ("scalper" if strategy == "xauusd_parallel" else "no")
                lines.append(
                    f"#{ticket} {sym} {side} · lot {vol:g} · entrada {po:.{digits}f}\n"
                    f"  SL {sl:.{digits}f} · TP {tp:.{digits}f} · PnL {profit:+.2f} · "
                    f"{strat_txt} · gestionada: {managed}"
                )
            telegram_notify.notify("\n".join(lines), dedup_key=None)

    try:
        while True:
            loop_t0 = time.monotonic()
            try:
                if not mt5.terminal_info():
                    logging.warning("MT5 terminal_info unavailable. Trying to reconnect...")
                    mt5.shutdown()
                    ok_re = connect_mt5(cfg)
                    if not ok_re or not mt5.terminal_info():
                        if telegram_notify.telegram_configured():
                            telegram_notify.notify(
                                "meta-trader: MT5 desconectado o sin terminal tras intento de reconexión.",
                                dedup_key="mt5_disconnect",
                                min_interval_sec=300.0,
                            )

                # Always manage positions (trailing) even when not opening new ones
                manage_positions(cfg)
                if cfg.xauusd_parallel_enabled:
                    manage_xauusd_parallel_positions(cfg)
                    _xauusd_ingest_broker_closes(cfg)
                _log_new_closes(cfg)
                _telegram_notify_new_closes(cfg)
                _telegram_notify_session_windows(cfg)

                reached, pnl_today = daily_loss_limit_reached(cfg.daily_loss_limit)
                monthly_reached, monthly_info = monthly_loss_limit_reached(cfg)
                _maybe_rebalance_portfolio_weights(cfg)
                market_regimes: dict = {}
                if regime_detector is not None:
                    try:
                        regimes_now = regime_detector.check_all(cfg)
                        for sym_r, lab_r in regimes_now.items():
                            if lab_r:
                                _LAST_REGIME_BY_SYMBOL[str(sym_r)] = str(lab_r)
                        _apply_pending_regime_auto_patches(cfg, regime_detector)
                        market_regimes = regime_detector.summary()
                    except Exception:
                        logging.exception("market_regime: check_all falló (no fatal).")
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
                    "monthly_loss_limit": monthly_info,
                    "monthly_loss_limit_reached": bool(monthly_reached),
                    "open_positions_total": total_open_positions(),
                    "open_positions_managed": _count_managed_open_positions(cfg),
                    "last_trade_time": {k: v.isoformat(timespec="seconds") for k, v in _last_trade_time.items()},
                    "last_failed_order_attempt": {
                        k: v.isoformat(timespec="seconds") for k, v in _last_failed_order_attempt.items()
                    },
                    "market_regimes": market_regimes,
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
                            if reached:
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
                                    if telegram_notify.telegram_configured():
                                        telegram_notify.notify(
                                            f"meta-trader: 6% Rule disparada para {current_month_tag}. "
                                            f"At-risk={float(monthly_info.get('total_at_risk') or 0.0):.2f} "
                                            f">= threshold={float(monthly_info.get('threshold_money') or 0.0):.2f} "
                                            f"({float(monthly_info.get('pct') or 0.0):.1f}% de "
                                            f"{float(monthly_info.get('month_start_balance') or 0.0):.2f}). "
                                            "Sin nuevas entradas hasta el próximo mes.",
                                            dedup_key=f"monthly_loss_limit_{current_month_tag}",
                                        )
                                state["mode"] = "MANAGE_ONLY_MONTHLY_LOSS_LIMIT"
                            else:
                                state["mode"] = "TRADING"
                                for s in allowed_syms:
                                    if _is_symbol_handled_by_xauusd_parallel(cfg, s):
                                        continue
                                    p = _effective_params(cfg, s)
                                    trend_h4 = get_trend_h4(s)
                                    rsi_h4 = get_rsi_h4(s)
                                    log_h4_snapshot_for_symbol(cfg, s, trend_h4=trend_h4)
                                    signal_m15 = get_signal_m15(s)
                                    ema_fast = int(p["ema_fast_m15"])
                                    ema_slow = int(p["ema_slow_m15"])
                                    ema_dir = ema_trend(s, ema_fast, ema_slow)
                                    adx = get_adx(s, mt5.TIMEFRAME_M15, cfg.adx_period_m15)
                                    slope_trend = _m15_slope_trend(s, n_closes=int(p["price_slope_n"]))
                                    decision = generate_signal(
                                        trend_h4=trend_h4,
                                        signal_m15=signal_m15,
                                        ema_dir=ema_dir,
                                        adx=adx,
                                        adx_min=float(p["adx_min"]),
                                        symbol=s,
                                        crypto_sell_requires_h4_down=cfg.crypto_sell_requires_h4_down,
                                        allow_flat_h4=True,
                                        m15_price_slope=slope_trend,
                                        price_slope_filter_enabled=bool(p["price_slope_filter_enabled"]),
                                        ema_fast=get_ema(s, mt5.TIMEFRAME_M15, ema_fast),
                                        ema_slow=get_ema(s, mt5.TIMEFRAME_M15, ema_slow),
                                        rsi_h4=rsi_h4,
                                        rsi_h4_filter_enabled=bool(p.get("rsi_h4_filter_enabled", False)),
                                        rsi_h4_oversold_threshold=float(p.get("rsi_h4_oversold_threshold", 35.0)),
                                        rsi_h4_overbought_threshold=float(p.get("rsi_h4_overbought_threshold", 65.0)),
                                    )
                                    # Filtro de extensión: bloquea entradas si el precio ya recorrió demasiado desde el extremo H1.
                                    if decision != "NONE":
                                        ext_pct = float(p.get("extension_filter_max_pct", 4.0))
                                        ext_bars = int(p.get("extension_filter_h1_bars", 16))
                                        if _movement_is_extended(s, decision,
                                                                 lookback_h1_bars=ext_bars,
                                                                 max_extension_pct=ext_pct):
                                            logging.info(
                                                "[%s] No trade: movimiento extendido >%.1f%% desde extremo H1 (%d bars).",
                                                s, ext_pct, ext_bars,
                                            )
                                            _log_event({
                                                "event": "EXTENSION_FILTER_SKIP",
                                                "ts": datetime.now().isoformat(timespec="seconds"),
                                                "symbol": s,
                                                "side": decision,
                                                "extension_filter_max_pct": ext_pct,
                                                "extension_filter_h1_bars": ext_bars,
                                            })
                                            decision = "NONE"
                                    sig_base = {
                                        "event": "SIGNAL",
                                        "ts": datetime.now().isoformat(timespec="seconds"),
                                        "symbol": s,
                                        "trend_h4": trend_h4,
                                        "rsi_h4": rsi_h4,
                                        "signal_m15": signal_m15,
                                        "ema_dir": ema_dir,
                                        "ema_fast_m15": ema_fast,
                                        "ema_slow_m15": ema_slow,
                                        "adx": adx,
                                        "adx_min": float(p["adx_min"]),
                                        "max_spread_points": float(p["max_spread_points"]),
                                        "sl_money": float(p["sl_money"]),
                                        "tp_money": float(p["tp_money"]),
                                        "sl_points": float(p["sl_points"]) if float(p["sl_points"]) > 0 else None,
                                        "tp_points": float(p["tp_points"]) if float(p["tp_points"]) > 0 else None,
                                        "use_risk_sizing": bool(p["use_risk_sizing"]),
                                        "risk_per_trade_pct": float(p["risk_per_trade_pct"]),
                                        "cooldown_minutes": int(p["cooldown_minutes"]),
                                        "max_trades_per_hour_per_symbol": int(p.get("max_trades_per_hour_per_symbol", 0)),
                                        "anti_chase_atr_mult": float(p.get("anti_chase_atr_mult", 0.0)),
                                        "crypto_sell_requires_h4_down": bool(cfg.crypto_sell_requires_h4_down),
                                        "price_slope_m15": slope_trend,
                                        "price_slope_n": int(p["price_slope_n"]),
                                        "price_slope_filter_enabled": bool(p["price_slope_filter_enabled"]),
                                        "decision": decision,
                                    }
                                    sig_base.update(_range_filter_signal_snapshot(s, p))
                                    _log_event(sig_base)
                                    if (
                                        decision in ("BUY", "SELL")
                                        and telegram_notify.telegram_configured()
                                        and telegram_notify.env_bool("TELEGRAM_NOTIFY_SIGNALS", False)
                                    ):
                                        try:
                                            ok_spread, spread, avg_spread = _is_spread_ok(cfg, s, max_spread_points=float(p["max_spread_points"]))
                                            if spread is None:
                                                spread_s = "n/a"
                                            elif cfg.spread_dynamic_enabled and avg_spread is not None:
                                                spread_s = f"{spread:.1f} pts (avg {avg_spread:.1f})"
                                            else:
                                                spread_s = f"{spread:.1f} pts"

                                            telegram_notify.notify(
                                                f"meta-trader: señal {decision}\n"
                                                f"{s} · H4={trend_h4} · EMA={ema_dir} · ADX={adx if adx is not None else 0.0:.1f}\n"
                                                f"M15={signal_m15} · spread={spread_s}",
                                                dedup_key=f"sig:{s}:{decision}",
                                                min_interval_sec=900.0,  # 15 min por símbolo y dirección
                                            )
                                        except Exception:
                                            logging.exception("Telegram notify (signal) failed (non-fatal).")
                                    _try_execute_trade_through_gates(cfg, s, decision, now=now_loop)

                if cfg.xauusd_parallel_enabled:
                    run_xauusd_parallel_cycle(cfg)
                    state["open_positions_total"] = total_open_positions()
                    state["open_positions_managed"] = _count_managed_open_positions(cfg)
                    open_snap = _xauusd_open_snapshot(cfg)
                    hourly = _xauusd_hourly_snapshot(cfg)
                    totals = hourly.get("totals") or {}
                    sl_mode = _xauusd_parallel_sl_mode(cfg)
                    snap_now = datetime.now()
                    state["xauusd_parallel"] = {
                        "enabled": True,
                        "symbol": _xauusd_parallel_symbol(cfg),
                        "magic": int(cfg.xauusd_parallel_magic),
                        "open_positions": int(open_snap.get("count") or 0),
                        "tp_money": float(cfg.xauusd_parallel_tp_money),
                        "sl_money": float(cfg.xauusd_parallel_sl_money),
                        "sl_mode": sl_mode,
                        "sl_atr_mult": float(cfg.xauusd_parallel_sl_atr_mult),
                        "tp_rr": float(cfg.xauusd_parallel_tp_rr),
                        "adx_min": float(cfg.xauusd_parallel_adx_min),
                        "h1_filter": bool(cfg.xauusd_parallel_h1_filter),
                        "h4_filter": bool(cfg.xauusd_parallel_h4_filter),
                        "start_hour": int(cfg.xauusd_parallel_start_hour),
                        "end_hour": int(cfg.xauusd_parallel_end_hour),
                        "sessions": str(cfg.xauusd_parallel_sessions or ""),
                        "window_label": _xauusd_parallel_window_label(cfg),
                        "in_window": (
                            _xauusd_parallel_in_window(cfg, snap_now)
                            and not _weekend_blocks_new_trade(cfg, _xauusd_parallel_symbol(cfg), snap_now)
                        ),
                        "max_positions": int(cfg.xauusd_parallel_max_positions),
                        "cooldown_seconds": float(cfg.xauusd_parallel_cooldown_seconds),
                        "cooldown_remaining_sec": round(_xauusd_cooldown_remaining_sec(cfg), 1),
                        "one_trade_per_bar": bool(cfg.xauusd_parallel_one_trade_per_bar),
                        "position": open_snap,
                        "last_signal": _XAUUSD_LAST_SIGNAL,
                        "last_skip": _XAUUSD_LAST_SKIP,
                        "skip_reasons": dict(_XAUUSD_SKIP_REASONS),
                        "hourly": hourly,
                        "session": {
                            "signals": int(totals.get("signals") or 0),
                            "opens": int(totals.get("opens") or 0),
                            "closes": int(totals.get("closes") or 0),
                            "skips": int(totals.get("skips") or 0),
                            "pnl": float(totals.get("pnl") or 0.0),
                            "spread_avg": totals.get("spread_avg"),
                            "open_close_gap": int(totals.get("opens") or 0) - int(totals.get("closes") or 0),
                        },
                    }

                try:
                    state["eval_tracks"] = _eval_tracks_state(cfg, datetime.now())
                except Exception:
                    logging.exception("eval_tracks_state failed")
                    state["eval_tracks"] = {"tracks": [], "error": "build_failed"}

                _save_state(state)

            except Exception:
                logging.exception("Unhandled loop exception.")
                if telegram_notify.telegram_configured():
                    try:
                        import traceback

                        telegram_notify.notify(
                            "meta-trader: excepción en el loop principal:\n"
                            + traceback.format_exc()[-2500:],
                            dedup_key="loop_exception",
                            min_interval_sec=600.0,
                        )
                    except Exception:
                        pass

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

