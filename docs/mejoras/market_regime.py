"""
Detector de régimen de mercado por símbolo para bot_mt5.py.

TRENDING / RANGING / UNCERTAIN (ADX M15, ADX H4, EMA M15, posición en rango H1).
Si cambia el régimen, avisa por Telegram con JSON sugerido para BOT_SYMBOL_CONFIG.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import MetaTrader5 as mt5

from indicators import adx_wilder, ema as ema_series

RegimeLabel = str

_CRYPTO = {"BTCUSD", "ETHUSD"}
_METALS = {"XAUUSD"}

# Texto corto para Telegram / dashboard (qué implica el régimen).
_REGIME_HINT_ES: dict[str, str] = {
    "TRENDING": (
        "Mercado con ADX alto y confirmación: conviene permitir más trades a favor "
        "(adx_min algo más bajo, rango H1 menos restrictivo, menos cooldown, dejar correr profit en filter-exit)."
    ),
    "RANGING": (
        "Mercado más plano / precio cerca del medio del rango H1: endurecer entradas "
        "(adx_min más alto, range_filter más estricto, cooldown más largo, opcional tope en escalera USD)."
    ),
    "UNCERTAIN": (
        "Señales mixtas: perfil intermedio/conservador hasta que se defina tendencia o lateral claro."
    ),
}

# Leyenda de cada clave del JSON sugerido (merge dentro del objeto del símbolo en BOT_SYMBOL_CONFIG).
_PATCH_KEY_HELP_ES: dict[str, str] = {
    "adx_min": "Umbral mínimo ADX M15 para abrir (más alto ⇒ menos entradas en chop).",
    "range_filter_mid_frac": "Si el precio está muy cerca del centro del rango H1, bloquea entrada; más bajo ⇒ menos bloqueos en tendencia.",
    "cooldown_minutes": "Minutos mínimos entre aperturas del mismo símbolo.",
    "filter_exit_min_profit_money": "Ganancia mínima (USD) para que el filtro de salida pueda cerrar por pérdida de momentum.",
    "usd_ladder_max_lock_usd": "Tope USD de la escalera (+$1,+,$2…); 0 = sin tope. >0 ayuda a no dejar profit atrapado indefinidamente en lateral.",
}


@dataclass
class RegimeState:
    label: RegimeLabel = "UNCERTAIN"
    adx: Optional[float] = None
    adx_h4: Optional[float] = None
    ema_aligned: Optional[bool] = None
    dist_mid_frac: Optional[float] = None
    updated_at: Optional[float] = None
    notified_label: Optional[str] = None


def _suggested_params(symbol: str, regime: RegimeLabel) -> dict:
    is_crypto = symbol in _CRYPTO
    is_metal = symbol in _METALS

    if regime == "TRENDING":
        if is_crypto:
            return {
                "adx_min": 22,
                "range_filter_mid_frac": 0.20,
                "cooldown_minutes": 20,
                "filter_exit_min_profit_money": 1.20,
                "usd_ladder_max_lock_usd": 0,
            }
        if is_metal:
            return {
                "adx_min": 21,
                "range_filter_mid_frac": 0.20,
                "cooldown_minutes": 25,
                "filter_exit_min_profit_money": 0.60,
                "usd_ladder_max_lock_usd": 0,
            }
        return {
            "adx_min": 20,
            "range_filter_mid_frac": 0.20,
            "cooldown_minutes": 20,
            "filter_exit_min_profit_money": 0.40,
            "usd_ladder_max_lock_usd": 0,
        }

    if regime == "RANGING":
        if symbol == "ETHUSD":
            return {
                "adx_min": 28,
                "range_filter_mid_frac": 0.20,
                "cooldown_minutes": 45,
                "filter_exit_min_profit_money": 0.40,
                "usd_ladder_max_lock_usd": 3,
            }
        if is_crypto:
            return {
                "adx_min": 28,
                "range_filter_mid_frac": 0.32,
                "cooldown_minutes": 45,
                "filter_exit_min_profit_money": 0.40,
                "usd_ladder_max_lock_usd": 3,
            }
        if is_metal:
            return {
                "adx_min": 26,
                "range_filter_mid_frac": 0.32,
                "cooldown_minutes": 40,
                "filter_exit_min_profit_money": 0.30,
                "usd_ladder_max_lock_usd": 3,
            }
        return {
            "adx_min": 24,
            "range_filter_mid_frac": 0.30,
            "cooldown_minutes": 35,
            "filter_exit_min_profit_money": 0.20,
            "usd_ladder_max_lock_usd": 2,
        }

    if symbol == "ETHUSD":
        return {
            "adx_min": 27,
            "range_filter_mid_frac": 0.20,
            "cooldown_minutes": 45,
            "filter_exit_min_profit_money": 0.50,
            "usd_ladder_max_lock_usd": 0,
        }
    if is_crypto:
        return {
            "adx_min": 27,
            "range_filter_mid_frac": 0.30,
            "cooldown_minutes": 45,
            "filter_exit_min_profit_money": 0.50,
            "usd_ladder_max_lock_usd": 0,
        }
    if is_metal:
        return {
            "adx_min": 24,
            "range_filter_mid_frac": 0.30,
            "cooldown_minutes": 35,
            "filter_exit_min_profit_money": 0.30,
            "usd_ladder_max_lock_usd": 0,
        }
    return {
        "adx_min": 22,
        "range_filter_mid_frac": 0.28,
        "cooldown_minutes": 30,
        "filter_exit_min_profit_money": 0.20,
        "usd_ladder_max_lock_usd": 0,
    }


def _last_adx(highs: list[float], lows: list[float], closes: list[float], period: int) -> Optional[float]:
    r = adx_wilder(highs, lows, closes, period)
    for v in reversed(r.adx):
        if v is not None:
            return float(v)
    return None


def _last_ema(values: list[float], period: int) -> Optional[float]:
    s = ema_series(values, period)
    for v in reversed(s):
        if v is not None:
            return float(v)
    return None


def _get_rates(symbol: str, tf, count: int) -> Optional[list]:
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
    if rates is None or len(rates) < count // 2:
        return None
    return list(rates)


class MarketRegimeDetector:
    def __init__(
        self,
        cfg=None,
        *,
        adx_period_m15: int = 14,
        adx_period_h4: int = 14,
        adx_trending: float = 28.0,
        adx_ranging: float = 22.0,
        ema_fast: int = 50,
        ema_slow: int = 200,
        range_lookback_h1: int = 24,
        range_mid_frac: float = 0.30,
        check_interval_s: float = 900.0,
        notify_module=None,
    ):
        _ = cfg  # reservado (API alineada con docs; Config / env a futuro)
        self.adx_period_m15 = adx_period_m15
        self.adx_period_h4 = adx_period_h4
        self.adx_trending = adx_trending
        self.adx_ranging = adx_ranging
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.range_lookback_h1 = range_lookback_h1
        self.range_mid_frac = range_mid_frac
        self.check_interval_s = check_interval_s

        self._states: dict[str, RegimeState] = {}
        self._last_check: dict[str, float] = {}
        # Parches para aplicación automática en bot_mt5 (consumir con take_pending_auto_patches).
        self._pending_auto_patches: list[tuple[str, str, str, dict]] = []

        if notify_module is not None:
            self._notify = notify_module
        else:
            try:
                import telegram_notify as tn

                self._notify = tn
            except ImportError:
                self._notify = None

        logging.info(
            "[MarketRegime] iniciado | adx_trending=%.1f adx_ranging=%.1f ema=%d/%d H1_lb=%d check=%ds",
            adx_trending,
            adx_ranging,
            ema_fast,
            ema_slow,
            range_lookback_h1,
            int(check_interval_s),
        )

    def get_regime(self, symbol: str) -> RegimeLabel:
        return self._states.get(symbol, RegimeState()).label

    def get_state(self, symbol: str) -> RegimeState:
        return self._states.get(symbol, RegimeState())

    def check_symbol(self, symbol: str) -> RegimeLabel:
        now = time.time()
        last = self._last_check.get(symbol, 0.0)
        if now - last < self.check_interval_s:
            return self.get_regime(symbol)

        self._last_check[symbol] = now
        new_state = self._detect(symbol)
        prev_st = self._states.get(symbol)
        prev_label = prev_st.label if prev_st else None
        self._states[symbol] = new_state

        if prev_label is not None and new_state.label != prev_label:
            logging.info(
                "[MarketRegime] %s: %s → %s (ADX_M15=%.1f ADX_H4=%.1f ema_aligned=%s dist_mid=%.2f)",
                symbol,
                prev_label,
                new_state.label,
                new_state.adx or 0.0,
                new_state.adx_h4 or 0.0,
                new_state.ema_aligned,
                new_state.dist_mid_frac or 0.0,
            )
            patch = _suggested_params(symbol, new_state.label)
            self._pending_auto_patches.append((symbol, prev_label, new_state.label, patch))
            self._notify_change(symbol, prev_label, new_state)

        return new_state.label

    def take_pending_auto_patches(self) -> list[tuple[str, str, str, dict]]:
        """Transiciones desde el último tick: (symbol, prev_label, new_label, suggested_patch_dict)."""
        out = self._pending_auto_patches
        self._pending_auto_patches = []
        return out

    def check_all(self, cfg) -> dict[str, RegimeLabel]:
        results: dict[str, RegimeLabel] = {}
        for symbol in cfg.symbols:
            try:
                results[symbol] = self.check_symbol(symbol)
            except Exception:
                logging.exception("[MarketRegime] error chequeando %s.", symbol)
                results[symbol] = "UNCERTAIN"
        return results

    def summary(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym, st in self._states.items():
            patch = _suggested_params(sym, st.label)
            out[sym] = {
                "regime": st.label,
                "adx_m15": round(st.adx, 2) if st.adx is not None else None,
                "adx_h4": round(st.adx_h4, 2) if st.adx_h4 is not None else None,
                "ema_aligned": st.ema_aligned,
                "dist_mid_frac": round(st.dist_mid_frac, 3) if st.dist_mid_frac is not None else None,
                "updated_at": datetime.fromtimestamp(st.updated_at).isoformat(timespec="seconds")
                if st.updated_at
                else None,
                "hint_es": _REGIME_HINT_ES.get(st.label, ""),
                "suggested_config_patch": patch,
                "suggested_config_patch_json": json.dumps(patch, separators=(",", ":"), ensure_ascii=False),
            }
        return out

    def _detect(self, symbol: str) -> RegimeState:
        state = RegimeState(updated_at=time.time())

        count_m15 = max(self.adx_period_m15 * 3 + 10, 60)
        rates_m15 = _get_rates(symbol, mt5.TIMEFRAME_M15, count_m15)
        if rates_m15:
            h = [float(r["high"]) for r in rates_m15]
            l = [float(r["low"]) for r in rates_m15]
            c = [float(r["close"]) for r in rates_m15]
            state.adx = _last_adx(h, l, c, self.adx_period_m15)

            ema_f = _last_ema(c, self.ema_fast)
            ema_s = _last_ema(c, self.ema_slow)
            price = c[-1]
            if ema_f is not None and ema_s is not None:
                state.ema_aligned = (ema_f > ema_s and price > ema_f) or (ema_f < ema_s and price < ema_f)

        count_h4 = max(self.adx_period_h4 * 3 + 10, 50)
        rates_h4 = _get_rates(symbol, mt5.TIMEFRAME_H4, count_h4)
        if rates_h4:
            h4 = [float(r["high"]) for r in rates_h4]
            l4 = [float(r["low"]) for r in rates_h4]
            c4 = [float(r["close"]) for r in rates_h4]
            state.adx_h4 = _last_adx(h4, l4, c4, self.adx_period_h4)

        rates_h1 = _get_rates(symbol, mt5.TIMEFRAME_H1, self.range_lookback_h1 + 2)
        tick = mt5.symbol_info_tick(symbol)
        if rates_h1 and tick is not None:
            highs_h1 = [float(r["high"]) for r in rates_h1]
            lows_h1 = [float(r["low"]) for r in rates_h1]
            hi, lo = max(highs_h1), min(lows_h1)
            rng = hi - lo
            if rng > 0:
                mid = (hi + lo) / 2.0
                price_now = float(tick.bid)
                state.dist_mid_frac = abs(price_now - mid) / rng

        state.label = self._classify(state)
        return state

    def _classify(self, st: RegimeState) -> RegimeLabel:
        adx = st.adx
        adx_h4 = st.adx_h4
        aligned = st.ema_aligned
        dist = st.dist_mid_frac

        if adx is None:
            return "UNCERTAIN"

        h4_confirms = adx_h4 is not None and adx_h4 >= self.adx_trending * 0.85
        if adx >= self.adx_trending and (h4_confirms or aligned is True):
            return "TRENDING"

        if adx <= self.adx_ranging and (dist is None or dist < self.range_mid_frac):
            return "RANGING"

        return "UNCERTAIN"

    def _notify_change(self, symbol: str, prev: str, st: RegimeState) -> None:
        if self._notify is None:
            return
        try:
            if not self._notify.telegram_configured():
                return
        except Exception:
            return

        emoji = {"TRENDING": "📈", "RANGING": "↔️", "UNCERTAIN": "❓"}
        label_es = {"TRENDING": "TENDENCIA", "RANGING": "LATERAL", "UNCERTAIN": "INCIERTO"}

        params = _suggested_params(symbol, st.label)
        params_lines = "\n".join(f"  {k}: {v}" for k, v in params.items())
        params_json = json.dumps(params, separators=(",", ":"), ensure_ascii=False)

        legend_lines = []
        for k in params:
            expl = _PATCH_KEY_HELP_ES.get(k)
            if expl:
                legend_lines.append(f"  • {k}: {expl}")

        lines = [
            f"{emoji.get(st.label, '?')} Régimen cambiado: {symbol}",
            f"{label_es.get(prev, prev)} → {label_es.get(st.label, st.label)}",
            "",
            _REGIME_HINT_ES.get(st.label, ""),
            "",
            "Qué ajustar:",
            "Mergeá SOLO estas claves dentro del objeto de ESE símbolo en BOT_SYMBOL_CONFIG",
            "(una línea JSON en .env). El resto (EMA, SL/TP points, spreads, ladder initial, etc.) dejalo como está.",
            "Después reiniciás el bot.",
            "",
            "Métricas:",
        ]
        if st.adx is not None:
            lines.append(f"  ADX M15: {st.adx:.1f}")
        if st.adx_h4 is not None:
            lines.append(f"  ADX H4:  {st.adx_h4:.1f}")
        if st.ema_aligned is not None:
            lines.append(f"  EMA alineadas: {'sí' if st.ema_aligned else 'no'}")
        if st.dist_mid_frac is not None:
            lines.append(f"  Dist. centro rango: {st.dist_mid_frac:.2f}")
        lines += [
            "",
            "Valores sugeridos (solo estas claves):",
            params_lines,
            "",
            "Para qué sirve cada una:",
            "\n".join(legend_lines) if legend_lines else "  (sin leyenda adicional)",
            "",
            "JSON de una línea para pegar dentro de BOT_SYMBOL_CONFIG → " + symbol + ":",
            params_json,
        ]
        final_msg = "\n".join(lines)

        try:
            self._notify.notify(
                final_msg,
                dedup_key=f"regime:{symbol}:{st.label}",
                min_interval_sec=600.0,
            )
        except Exception:
            logging.exception("[MarketRegime] Telegram notify falló (no fatal).")

        st.notified_label = st.label
        logging.info("[MarketRegime] Telegram notificado: %s → %s.", symbol, st.label)
