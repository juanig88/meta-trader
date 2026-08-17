from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal

from indicators import adx_wilder, ema


Signal = Literal["BUY", "SELL", "NONE"]
Trend = Literal["UP", "DOWN", "FLAT"]

# Symbols whose names match these substrings use the relaxed M15 stack (hybrid / live tiers).
_CRYPTO_RELAXED_MARKERS: tuple[str, ...] = ("BTC", "ETH", "XBT", "SOL", "LTC", "XRP", "DOGE", "BNB")


def symbol_uses_crypto_relaxed_m15(symbol: str) -> bool:
    u = (symbol or "").upper()
    return any(m in u for m in _CRYPTO_RELAXED_MARKERS)


@dataclass(frozen=True)
class StrategyParams:
    ema_fast: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_min: float = 17.0
    # Entry/trend modes for A/B validation
    entry_mode: str = "hybrid"  # "strict" | "relaxed" | "hybrid" (strict for FX/metals, relaxed for crypto)
    trend_mode: str = "closes"  # "highs" | "closes"
    allow_flat_h4: bool = True  # if True: allow FLAT H4, but never trade against H4 direction
    # En símbolos crypto (relaxed M15), las ventas solo si H4 cerrado es DOWN (evita shorts contra tendencia).
    crypto_sell_requires_h4_down: bool = True
    # Filtro de pendiente sobre N cierres M15 (regresión lineal). Off por compatibilidad.
    price_slope_filter_enabled: bool = False
    price_slope_n: int = 6  # solo informativo en decide(); el caller calcula Trend y Features
    # Filtro de agotamiento RSI sobre H4. Validado con 17 meses de datos BTC/ETH.
    rsi_h4_filter_enabled: bool = False
    rsi_h4_oversold_threshold: float = 35.0    # bloquea SELL si RSI_H4 < este valor
    rsi_h4_overbought_threshold: float = 65.0  # bloquea BUY  si RSI_H4 > este valor


@dataclass(frozen=True)
class Features:
    trend_h4: Trend
    signal_m15: Signal
    ema_dir: Trend
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    adx: Optional[float]
    price_slope_m15: Trend = "FLAT"
    rsi_h4: Optional[float] = None


def trend_h4_from_highs(highs_h4: list[float]) -> Trend:
    """
    Bullish if last 3 closed highs are increasing, bearish if decreasing.
    Expects highs ordered oldest->newest.
    """
    if len(highs_h4) < 3:
        return "FLAT"
    h1, h2, h3 = highs_h4[-3], highs_h4[-2], highs_h4[-1]
    if h1 < h2 < h3:
        return "UP"
    if h1 > h2 > h3:
        return "DOWN"
    return "FLAT"


def trend_h4_from_closes(closes_h4: list[float]) -> Trend:
    """
    Pendiente de regresión lineal sobre los últimos 6 cierres H4.
    Tolera un pullback de una vela sin cambiar la tendencia detectada.
    """
    n = 6
    if len(closes_h4) < n:
        return "FLAT"
    vals = closes_h4[-n:]
    mean_x = (n - 1) / 2.0
    mean_y = sum(vals) / n
    num = sum((i - mean_x) * (vals[i] - mean_y) for i in range(n))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return "FLAT"
    slope = num / den
    threshold = mean_y * 0.0003  # 0.03% del precio como umbral mínimo
    if slope > threshold:
        return "UP"
    if slope < -threshold:
        return "DOWN"
    return "FLAT"


def price_slope_m15(closes_m15: list[float], n: int = 6) -> Trend:
    """
    Pendiente de regresión lineal sobre los últimos N cierres M15.
    Mismo algoritmo que trend_h4_from_closes; umbral 0.03% del precio medio.
    ``closes_m15`` orden oldest → newest (un cierre por vela cerrada).
    """
    if len(closes_m15) < n:
        return "FLAT"
    vals = closes_m15[-n:]
    mean_x = (n - 1) / 2.0
    mean_y = sum(vals) / n
    num = sum((i - mean_x) * (vals[i] - mean_y) for i in range(n))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return "FLAT"
    slope = num / den
    threshold = mean_y * 0.0003
    if slope > threshold:
        return "UP"
    if slope < -threshold:
        return "DOWN"
    return "FLAT"


def rsi_from_closes(closes: list[float], period: int = 14) -> Optional[float]:
    """
    RSI de Wilder sobre los últimos ``period*2`` cierres.
    Devuelve None si no hay suficientes datos.
    ``closes`` orden oldest → newest.
    """
    n = len(closes)
    if n < period + 1:
        return None
    # Usar los últimos period*2 valores para warm-up adecuado
    vals = closes[-(period * 2):]
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(vals)):
        diff = vals[i] - vals[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    if len(gains) < period:
        return None
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0.0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def signal_m15_strong_breakout(
    open_m15: list[float],
    high_m15: list[float],
    low_m15: list[float],
    close_m15: list[float],
) -> Signal:
    """Strict breakout (original)."""
    if len(close_m15) < 2:
        return "NONE"
    o = open_m15[-1]
    c = close_m15[-1]
    prev_h = high_m15[-2]
    prev_l = low_m15[-2]
    if c > o and c > prev_h:
        return "BUY"
    if c < o and c < prev_l:
        return "SELL"
    return "NONE"


def signal_m15_relaxed(
    open_m15: list[float],
    high_m15: list[float],
    low_m15: list[float],
    close_m15: list[float],
    *,
    is_crypto: bool = False,
) -> Signal:
    """
    Relaxed entry:
    - Tier 1: strict close breakout (same as original)
    - Tier 2: wick breakout + momentum close
    - Tier 3 (crypto BUY only): momentum close without wick breakout (sin Tier 3 SELL)
    """
    if len(close_m15) < 2:
        return "NONE"
    o = open_m15[-1]
    c = close_m15[-1]
    h = high_m15[-1]
    l = low_m15[-1]
    prev_h = high_m15[-2]
    prev_l = low_m15[-2]
    prev_c = close_m15[-2]

    if c > o and c > prev_h:
        return "BUY"
    if c < o and c < prev_l:
        return "SELL"

    if c > o and c > prev_c and h > prev_h:
        return "BUY"
    if c < o and c < prev_c and l < prev_l:
        return "SELL"

    if is_crypto:
        if c > o and c > prev_c:
            return "BUY"
    return "NONE"


def ema_trend_from_series(price: list[float], fast: int, slow: int) -> tuple[Trend, Optional[float], Optional[float]]:
    if not price:
        return "FLAT", None, None
    ef = ema(price, fast)
    es = ema(price, slow)
    f = ef[-1]
    s = es[-1]
    if f is None or s is None:
        return "FLAT", f, s
    p = price[-1]
    if f > s and p > f:
        return "UP", f, s
    if f < s and p < f:
        return "DOWN", f, s
    return "FLAT", f, s


def adx_from_series(high: list[float], low: list[float], close: list[float], period: int) -> Optional[float]:
    r = adx_wilder(high, low, close, period=period)
    v = r.adx[-1] if r.adx else None
    return float(v) if v is not None else None


def decide(params: StrategyParams, features: Features, *, symbol: Optional[str] = None) -> Signal:
    if features.adx is None or features.adx < params.adx_min:
        return "NONE"
    # Filtro RSI H4: bloquea entradas en zonas de agotamiento.
    # BUY: no entrar si el precio ya está overbought en H4 (subió demasiado).
    # SELL: no entrar si el precio ya está oversold en H4 (ya cayó demasiado).
    if params.rsi_h4_filter_enabled and features.rsi_h4 is not None:
        if features.signal_m15 == "BUY" and features.rsi_h4 > params.rsi_h4_overbought_threshold:
            return "NONE"
        if features.signal_m15 == "SELL" and features.rsi_h4 < params.rsi_h4_oversold_threshold:
            return "NONE"
    if params.allow_flat_h4:
        slope = features.price_slope_m15
        if features.signal_m15 == "BUY" and features.ema_dir == "UP" and features.trend_h4 != "DOWN":
            # Strict: exigir pendiente a favor (FLAT/DOWN = entrada tardía / contra-rebote).
            if params.price_slope_filter_enabled and slope != "UP":
                return "NONE"
            return "BUY"
        if features.signal_m15 == "SELL" and features.ema_dir == "DOWN":
            if features.trend_h4 == "UP":
                return "NONE"
            if params.crypto_sell_requires_h4_down and symbol and symbol_uses_crypto_relaxed_m15(symbol):
                if features.trend_h4 != "DOWN":
                    return "NONE"
            # Strict: SELL solo con slope DOWN. FLAT tras un bounce UP era el caso
            # BTC 2026-08-10 16:15 (H4 DOWN + signal SELL pero microtendencia ya no bajista).
            if params.price_slope_filter_enabled and slope != "DOWN":
                return "NONE"
            return "SELL"
        return "NONE"

    # strict original alignment
    if features.trend_h4 == "UP" and features.signal_m15 == "BUY" and features.ema_dir == "UP":
        return "BUY"
    if features.trend_h4 == "DOWN" and features.signal_m15 == "SELL" and features.ema_dir == "DOWN":
        return "SELL"
    return "NONE"

