"""Five-module ensemble, HTF/pullback gates, and $0.50-capped Kelly sizing.

Pure domain: no MT5, no DB, no Flask. Each module returns BUY, SELL, or NONE.
NONE counts against the 4/5 fire rule. HTF (M15 EMA50) and pullback are extra
gates. Kelly scales dollar risk up to the cap; it never raises risk above
max_risk_usd.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from indicators import atr as atr_series
from indicators import ema as ema_series
from trading.domain.alpha_score import flow_factor, momentum_factor, volatility_factor
from trading.domain.alpha_shadow import alpha_shadow_from_bars
from trading.domain.crypto_universe import BTC_SYMBOL, rotation_rank_skip
from trading.domain.decision_state import pack_decision_state
from trading.domain.expected_value import kelly_fraction
from trading.domain.position_sizing import clamp_lot_volume
from trading.domain.quant_factors import bollinger_bandwidth, price_zscore, rsi_state
from trading.domain.rotation_stops import size_lot_for_percent_stop
from trading.domain.swing_structure import murphy_snapshot
from trading.domain.xbtfx import root_symbol

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDE_NONE = "NONE"

VOTER_COUNT = 5
MIN_AGREE = 4
MAX_RISK_USD = 0.50
HTF_EMA_PERIOD = 50
PULLBACK_CLIMAX_ATR = 1.5
MODULE_GATE_SKIPS = frozenset({"veto_htf", "sin_pullback", "rsi_rango"})

VOTER_ORDER: tuple[str, ...] = (
    "htf",
    "momentum_short",
    "intermarket",
    "vol_bands",
    "structure",
)

VOTER_LABELS: dict[str, str] = {
    "htf": "HTF M15",
    "momentum_short": "Momentum corto",
    "intermarket": "Intermercado BTC",
    "vol_bands": "Bandas / amplitud",
    "structure": "Estructura M1/M5",
    "m5_trend": "Tendencia M5",
    "m5_body": "Cuerpo M5",
    "m1_streak": "Racha M1",
    "m1_follow3": "3 M1 → 4ª",
    "m1_momentum": "Momentum M1",
    "m1_ema_cross": "Cruce medias M1",
    "m1_flow": "Volumen / FLOW",
    "m1_expansion": "Expansión",
    "zscore": "Z-score",
    "bollinger": "Bollinger",
    "btc_corr": "Correlación BTC",
    "rsi_m1": "RSI M1",
    "m1_m5_align": "M1 alineada M5",
}


@dataclass(frozen=True)
class Ohlcv:
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...] = ()

    @classmethod
    def from_lists(
        cls,
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[float] = (),
    ) -> "Ohlcv":
        return cls(
            opens=tuple(float(x) for x in opens),
            highs=tuple(float(x) for x in highs),
            lows=tuple(float(x) for x in lows),
            closes=tuple(float(x) for x in closes),
            volumes=tuple(float(x) for x in volumes),
        )

    def upto(self, end: int) -> "Ohlcv":
        """Prefix of the series, exclusive end. Empty if end <= 0."""
        n = max(0, int(end))
        vols = self.volumes[:n] if self.volumes else ()
        return Ohlcv(
            opens=self.opens[:n],
            highs=self.highs[:n],
            lows=self.lows[:n],
            closes=self.closes[:n],
            volumes=vols,
        )


@dataclass(frozen=True)
class Vote:
    name: str
    side: str
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": VOTER_LABELS.get(self.name, self.name),
            "side": self.side,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Consensus:
    direction: str
    buy: int
    sell: int
    none: int
    agree: int
    fired: bool
    near_miss: bool

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "buy": self.buy,
            "sell": self.sell,
            "none": self.none,
            "agree": self.agree,
            "fired": self.fired,
            "near_miss": self.near_miss,
        }


@dataclass(frozen=True)
class SizeDecision:
    ok: bool
    reason: str
    risk_usd: float
    lot: float
    kelly_f: float

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "risk_usd": self.risk_usd,
            "lot": self.lot,
            "kelly_f": self.kelly_f,
        }


@dataclass(frozen=True)
class ContractSpec:
    volume_min: float
    volume_step: float
    volume_max: float = 100.0
    usd_at_min_lot_min_stop: float = 0.0
    price: float = 0.0
    tick_value: float = 0.0
    tick_size: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    tick_time_msc: int = 0


@dataclass(frozen=True)
class EnsembleSnapshot:
    symbol: str
    votes: tuple[Vote, ...]
    consensus: Consensus
    size: SizeDecision
    skip_reason: str = ""
    action: str = "SKIP"
    m1_time: Optional[int] = None
    murphy: Optional[dict] = None
    alpha: Optional[dict] = None
    state: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "votes": [v.as_dict() for v in self.votes],
            "consensus": self.consensus.as_dict(),
            "size": self.size.as_dict(),
            "skip_reason": self.skip_reason,
            "action": self.action,
            "m1_time": self.m1_time,
            "direction": self.consensus.direction,
            "murphy": self.murphy,
            "alpha": self.alpha,
            "state": self.state,
        }


def _murphy_if_fired(
    consensus: Consensus,
    m15: Optional[Ohlcv],
    h4: Optional[Ohlcv],
) -> Optional[dict]:
    if not consensus.fired or consensus.direction not in (SIDE_BUY, SIDE_SELL):
        return None
    return murphy_snapshot(
        side=consensus.direction,
        m15_highs=m15.highs if m15 else (),
        m15_lows=m15.lows if m15 else (),
        m15_closes=m15.closes if m15 else (),
        h4_highs=h4.highs if h4 else (),
        h4_lows=h4.lows if h4 else (),
        h4_closes=h4.closes if h4 else (),
    )


def _alpha_if_fired(
    consensus: Consensus,
    symbol: str,
    m15: Optional[Ohlcv],
) -> Optional[dict]:
    if not consensus.fired or consensus.direction not in (SIDE_BUY, SIDE_SELL):
        return None
    return alpha_shadow_from_bars(symbol, consensus.direction, m15)


def _state_if_fired(
    consensus: Consensus,
    *,
    m1: Ohlcv,
    votes: tuple[Vote, ...],
    murphy: Optional[dict],
    alpha: Optional[dict],
    spec: ContractSpec,
    m1_time: Optional[int],
) -> Optional[dict]:
    if not consensus.fired:
        return None
    return pack_decision_state(
        m1_closes=m1.closes,
        votes=votes,
        murphy=murphy,
        alpha=alpha,
        mid=spec.price or None,
        bid=spec.bid or None,
        ask=spec.ask or None,
        tick_value=spec.tick_value,
        tick_size=spec.tick_size,
        lot=spec.volume_min,
        decision_ts_msc=decision_cutoff_msc(spec.tick_time_msc, m1_time),
        ticks=(),
    )


def decision_cutoff_msc(tick_time_msc: int, m1_time: Optional[int]) -> Optional[int]:
    """Server-time msc that tape ticks must be strictly below.

    The quote the bot sized on is known, so the cutoff sits 1 ms past it. Without
    a quote, the close of the last closed M1 (open + 60 s) is the latest safe edge.
    """
    if tick_time_msc and int(tick_time_msc) > 0:
        return int(tick_time_msc) + 1
    if m1_time:
        return (int(m1_time) + 60) * 1000
    return None


def _vote(name: str, side: str, reason: str = "") -> Vote:
    s = side if side in (SIDE_BUY, SIDE_SELL, SIDE_NONE) else SIDE_NONE
    return Vote(name=name, side=s, reason=reason)


def _signed_vote(name: str, value: Optional[float], deadband: float) -> Vote:
    if value is None:
        return _vote(name, SIDE_NONE, "sin_dato")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _vote(name, SIDE_NONE, "sin_dato")
    if v != v or abs(v) < float(deadband):
        return _vote(name, SIDE_NONE, "dentro_banda")
    if v > 0:
        return _vote(name, SIDE_BUY, "positivo")
    return _vote(name, SIDE_SELL, "negativo")


def _last_body_side(
    opens: Sequence[float],
    closes: Sequence[float],
    *,
    min_body_frac: float = 0.0,
    highs: Sequence[float] = (),
    lows: Sequence[float] = (),
) -> str:
    if not opens or not closes:
        return SIDE_NONE
    o, c = float(opens[-1]), float(closes[-1])
    body = c - o
    if min_body_frac > 0.0 and highs and lows:
        rng = float(highs[-1]) - float(lows[-1])
        if rng <= 0.0 or abs(body) / rng < min_body_frac:
            return SIDE_NONE
    if body > 0:
        return SIDE_BUY
    if body < 0:
        return SIDE_SELL
    return SIDE_NONE


def _sma(values: Sequence[float], period: int) -> Optional[float]:
    vals = [float(v) for v in values]
    p = int(period)
    if p < 1 or len(vals) < p:
        return None
    window = vals[-p:]
    return sum(window) / float(p)


def _atr(ohlcv: Ohlcv, period: int = 14) -> Optional[float]:
    series = atr_series(list(ohlcv.highs), list(ohlcv.lows), list(ohlcv.closes), period)
    for v in reversed(series):
        if v is not None and float(v) > 0.0:
            return float(v)
    return None


def _last_ema(closes: Sequence[float], period: int) -> Optional[float]:
    series = ema_series(list(closes), int(period))
    return next((float(v) for v in reversed(series) if v is not None), None)


def vote_m5_trend(m5: Ohlcv, *, period: int = 20) -> Vote:
    sma = _sma(m5.closes, period)
    if sma is None:
        return _vote("m5_trend", SIDE_NONE, "pocas_velas")
    close = float(m5.closes[-1])
    if close > sma:
        return _vote("m5_trend", SIDE_BUY, "cierre_sobre_media")
    if close < sma:
        return _vote("m5_trend", SIDE_SELL, "cierre_bajo_media")
    return _vote("m5_trend", SIDE_NONE, "sobre_media")


def vote_m5_body(m5: Ohlcv, *, min_body_frac: float = 0.25) -> Vote:
    side = _last_body_side(
        m5.opens, m5.closes, min_body_frac=min_body_frac, highs=m5.highs, lows=m5.lows
    )
    return _vote("m5_body", side, "cuerpo" if side != SIDE_NONE else "doji")


def vote_m1_streak(m1: Ohlcv, *, bars: int = 3) -> Vote:
    n = int(bars)
    if len(m1.opens) < n or len(m1.closes) < n:
        return _vote("m1_streak", SIDE_NONE, "pocas_velas")
    sides = [
        _last_body_side(m1.opens[: i + 1], m1.closes[: i + 1])
        for i in range(len(m1.closes) - n, len(m1.closes))
    ]
    if any(s == SIDE_NONE for s in sides):
        return _vote("m1_streak", SIDE_NONE, "doji_en_racha")
    if all(s == SIDE_BUY for s in sides):
        return _vote("m1_streak", SIDE_BUY, f"{n}_alcistas")
    if all(s == SIDE_SELL for s in sides):
        return _vote("m1_streak", SIDE_SELL, f"{n}_bajistas")
    return _vote("m1_streak", SIDE_NONE, "mixta")


def vote_m1_follow3(m1: Ohlcv, *, run: int = 3) -> Vote:
    """Vote that the next M1 continues after exactly ``run`` closed bodies the same way.

    Distinct from ``m1_streak``: a 8-bar run does not count. Only the start of
    the 4th candle (three same, the bar before them not the same side).
    """
    n = int(run)
    if n < 1 or len(m1.opens) < n or len(m1.closes) < n:
        return _vote("m1_follow3", SIDE_NONE, "pocas_velas")
    start = len(m1.closes) - n
    sides = [
        _last_body_side(m1.opens[: i + 1], m1.closes[: i + 1])
        for i in range(start, len(m1.closes))
    ]
    if any(s == SIDE_NONE for s in sides):
        return _vote("m1_follow3", SIDE_NONE, "doji_en_racha")
    if not (all(s == SIDE_BUY for s in sides) or all(s == SIDE_SELL for s in sides)):
        return _vote("m1_follow3", SIDE_NONE, "mixta")
    run_side = sides[0]
    if start > 0:
        prior = _last_body_side(m1.opens[:start], m1.closes[:start])
        if prior == run_side:
            return _vote("m1_follow3", SIDE_NONE, "racha_mas_larga")
    return _vote("m1_follow3", run_side, f"{n}_luego_4ta")


def three_m1_then_fourth_stats(
    opens: Sequence[float],
    closes: Sequence[float],
    *,
    run: int = 3,
    exact: bool = True,
) -> dict[str, float | int]:
    """Hit rate of the next body after ``run`` same-direction M1 bodies.

    ``exact`` True = the bar before the run is not the same side (4th candle).
    """
    n = int(run)
    o = [float(x) for x in opens]
    c = [float(x) for x in closes]
    hits = 0
    cases = 0
    up_hits = 0
    up_n = 0
    down_hits = 0
    down_n = 0
    base_hits = 0
    base_n = 0
    for i in range(1, len(c)):
        a = _last_body_side(o[:i], c[:i])
        b = _last_body_side(o[: i + 1], c[: i + 1])
        if a in (SIDE_BUY, SIDE_SELL) and b in (SIDE_BUY, SIDE_SELL):
            base_n += 1
            if a == b:
                base_hits += 1
    for i in range(n, len(c)):
        run_sides = [_last_body_side(o[: j + 1], c[: j + 1]) for j in range(i - n, i)]
        if any(s == SIDE_NONE for s in run_sides):
            continue
        if not (all(s == SIDE_BUY for s in run_sides) or all(s == SIDE_SELL for s in run_sides)):
            continue
        side = run_sides[0]
        if exact and i - n > 0:
            prior = _last_body_side(o[: i - n], c[: i - n])
            if prior == side:
                continue
        nxt = _last_body_side(o[: i + 1], c[: i + 1])
        if nxt == SIDE_NONE:
            continue
        cases += 1
        ok = nxt == side
        if ok:
            hits += 1
        if side == SIDE_BUY:
            up_n += 1
            if ok:
                up_hits += 1
        else:
            down_n += 1
            if ok:
                down_hits += 1
    return {
        "n": cases,
        "hits": hits,
        "hit_rate": round(hits / cases, 4) if cases else 0.0,
        "up_n": up_n,
        "up_hit_rate": round(up_hits / up_n, 4) if up_n else 0.0,
        "down_n": down_n,
        "down_hit_rate": round(down_hits / down_n, 4) if down_n else 0.0,
        "baseline_n": base_n,
        "baseline_rate": round(base_hits / base_n, 4) if base_n else 0.0,
    }


def vote_m1_momentum(m1: Ohlcv, *, deadband: float = 0.05) -> Vote:
    atr_v = _atr(m1)
    value = momentum_factor(m1.closes, atr_v)
    return _signed_vote("m1_momentum", value, deadband)


def vote_m1_ema_cross(m1: Ohlcv, *, fast: int = 8, slow: int = 21) -> Vote:
    series_f = ema_series(list(m1.closes), int(fast))
    series_s = ema_series(list(m1.closes), int(slow))
    f = next((v for v in reversed(series_f) if v is not None), None)
    s = next((v for v in reversed(series_s) if v is not None), None)
    if f is None or s is None:
        return _vote("m1_ema_cross", SIDE_NONE, "pocas_velas")
    if float(f) > float(s):
        return _vote("m1_ema_cross", SIDE_BUY, "rapida_arriba")
    if float(f) < float(s):
        return _vote("m1_ema_cross", SIDE_SELL, "rapida_abajo")
    return _vote("m1_ema_cross", SIDE_NONE, "iguales")


def vote_m1_flow(m1: Ohlcv, *, deadband: float = 0.05) -> Vote:
    value = flow_factor(m1.opens, m1.closes, m1.volumes)
    return _signed_vote("m1_flow", value, deadband)


def vote_m1_expansion(m1: Ohlcv, *, deadband: float = 0.05) -> Vote:
    value = volatility_factor(m1.highs, m1.lows, m1.closes)
    return _signed_vote("m1_expansion", value, deadband)


def vote_zscore(m1: Ohlcv, *, stretch: float = 1.5) -> Vote:
    z = price_zscore(m1.closes, lookback=min(50, max(5, len(m1.closes))))
    if z is None:
        return _vote("zscore", SIDE_NONE, "sin_dato")
    if abs(float(z)) < float(stretch):
        return _vote("zscore", SIDE_NONE, "no_estirado")
    if float(z) > 0:
        return _vote("zscore", SIDE_SELL, "estirado_arriba")
    return _vote("zscore", SIDE_BUY, "estirado_abajo")


def vote_bollinger(m1: Ohlcv) -> Vote:
    state = bollinger_bandwidth(m1.closes)
    if state is None or not m1.closes:
        return _vote("bollinger", SIDE_NONE, "sin_dato")
    close = float(m1.closes[-1])
    width = float(state.upper) - float(state.lower)
    if width <= 0.0:
        return _vote("bollinger", SIDE_NONE, "banda_plana")
    if state.squeeze is True:
        if close > float(state.upper):
            return _vote("bollinger", SIDE_BUY, "squeeze_ruptura_arriba")
        if close < float(state.lower):
            return _vote("bollinger", SIDE_SELL, "squeeze_ruptura_abajo")
        return _vote("bollinger", SIDE_NONE, "squeeze_sin_ruptura")
    near = 0.15 * width
    if close >= float(state.upper) - near:
        return _vote("bollinger", SIDE_SELL, "cerca_banda_alta")
    if close <= float(state.lower) + near:
        return _vote("bollinger", SIDE_BUY, "cerca_banda_baja")
    return _vote("bollinger", SIDE_NONE, "en_el_medio")


def _is_btc(symbol: str) -> bool:
    return root_symbol(symbol) == root_symbol(BTC_SYMBOL)


def vote_btc_corr(
    symbol: str,
    m5: Ohlcv,
    *,
    btc_m5: Optional[Ohlcv] = None,
    eth_m5: Optional[Ohlcv] = None,
    sol_m5: Optional[Ohlcv] = None,
) -> Vote:
    own = _last_body_side(m5.opens, m5.closes)
    if own == SIDE_NONE:
        return _vote("btc_corr", SIDE_NONE, "propio_doji")
    if _is_btc(symbol):
        if eth_m5 is None or sol_m5 is None:
            return _vote("btc_corr", SIDE_NONE, "faltan_eth_sol")
        eth_s = _last_body_side(eth_m5.opens, eth_m5.closes)
        sol_s = _last_body_side(sol_m5.opens, sol_m5.closes)
        if eth_s == SIDE_NONE or sol_s == SIDE_NONE or eth_s != sol_s:
            return _vote("btc_corr", SIDE_NONE, "eth_sol_no_acuerdan")
        return _vote("btc_corr", eth_s, "eth_sol")
    if btc_m5 is None:
        return _vote("btc_corr", SIDE_NONE, "falta_btc")
    btc_s = _last_body_side(btc_m5.opens, btc_m5.closes)
    if btc_s == SIDE_NONE:
        return _vote("btc_corr", SIDE_NONE, "btc_doji")
    if btc_s != own:
        return _vote("btc_corr", SIDE_NONE, "desacoplado")
    return _vote("btc_corr", own, "con_btc")


def vote_rsi_m1(m1: Ohlcv, *, buy_above: float = 55.0, sell_below: float = 45.0) -> Vote:
    state = rsi_state(m1.closes)
    if state.rsi is None:
        return _vote("rsi_m1", SIDE_NONE, "sin_dato")
    rsi = float(state.rsi)
    if rsi >= 70.0 or rsi <= 30.0:
        return _vote("rsi_m1", SIDE_NONE, "extremo")
    if rsi >= float(buy_above):
        return _vote("rsi_m1", SIDE_BUY, "fuerza")
    if rsi <= float(sell_below):
        return _vote("rsi_m1", SIDE_SELL, "debilidad")
    return _vote("rsi_m1", SIDE_NONE, "zona_media")


def vote_m1_m5_align(m1: Ohlcv, m5: Ohlcv) -> Vote:
    a = _last_body_side(m1.opens, m1.closes)
    b = _last_body_side(m5.opens, m5.closes)
    if a == SIDE_NONE or b == SIDE_NONE:
        return _vote("m1_m5_align", SIDE_NONE, "doji")
    if a != b:
        return _vote("m1_m5_align", SIDE_NONE, "pelean")
    return _vote("m1_m5_align", a, "alineadas")


def vote_htf(m15: Optional[Ohlcv], *, period: int = HTF_EMA_PERIOD, atr_frac: float = 0.15) -> Vote:
    """M15 close vs EMA50. NONE when the close sits inside an ATR band (range)."""
    if m15 is None or len(m15.closes) < int(period):
        return _vote("htf", SIDE_NONE, "pocas_velas")
    ema = _last_ema(m15.closes, int(period))
    if ema is None:
        return _vote("htf", SIDE_NONE, "pocas_velas")
    close = float(m15.closes[-1])
    atr_v = _atr(m15) or abs(ema) * 0.002
    band = max(float(atr_v) * float(atr_frac), abs(close) * 1e-8)
    delta = close - ema
    if abs(delta) < band:
        return _vote("htf", SIDE_NONE, "rango")
    if delta > 0:
        return _vote("htf", SIDE_BUY, "cierre_sobre_ema50")
    return _vote("htf", SIDE_SELL, "cierre_bajo_ema50")


def vote_momentum_short(m1: Ohlcv, m5: Ohlcv) -> Vote:
    """One vote from the four correlated short-term internals (need ≥3)."""
    internals = (
        vote_m5_body(m5),
        vote_m1_streak(m1),
        vote_m1_follow3(m1),
        vote_m1_momentum(m1),
    )
    buy = sum(1 for v in internals if v.side == SIDE_BUY)
    sell = sum(1 for v in internals if v.side == SIDE_SELL)
    if buy >= 3 and buy > sell:
        return _vote("momentum_short", SIDE_BUY, f"mayoria_{buy}")
    if sell >= 3 and sell > buy:
        return _vote("momentum_short", SIDE_SELL, f"mayoria_{sell}")
    return _vote("momentum_short", SIDE_NONE, "sin_mayoria")


def vote_intermarket(
    symbol: str,
    m5: Ohlcv,
    *,
    btc_m5: Optional[Ohlcv] = None,
    eth_m5: Optional[Ohlcv] = None,
    sol_m5: Optional[Ohlcv] = None,
) -> Vote:
    raw = vote_btc_corr(symbol, m5, btc_m5=btc_m5, eth_m5=eth_m5, sol_m5=sol_m5)
    return _vote("intermarket", raw.side, raw.reason)


def vote_vol_bands(m1: Ohlcv) -> Vote:
    """Bollinger amplitude as strength (not mean-reversion fade)."""
    state = bollinger_bandwidth(m1.closes)
    if state is None or not m1.closes:
        return _vote("vol_bands", SIDE_NONE, "sin_dato")
    close = float(m1.closes[-1])
    upper = float(state.upper)
    lower = float(state.lower)
    width = upper - lower
    if width <= 0.0:
        return _vote("vol_bands", SIDE_NONE, "banda_plana")
    if state.squeeze is True:
        if close > upper:
            return _vote("vol_bands", SIDE_BUY, "squeeze_ruptura_arriba")
        if close < lower:
            return _vote("vol_bands", SIDE_SELL, "squeeze_ruptura_abajo")
        return _vote("vol_bands", SIDE_NONE, "squeeze_sin_ruptura")
    if close >= upper - 0.20 * width:
        return _vote("vol_bands", SIDE_BUY, "expansion_techo")
    if close <= lower + 0.20 * width:
        return _vote("vol_bands", SIDE_SELL, "expansion_piso")
    return _vote("vol_bands", SIDE_NONE, "expansion_medio")


def vote_structure(m1: Ohlcv, m5: Ohlcv) -> Vote:
    ema = vote_m1_ema_cross(m1)
    align = vote_m1_m5_align(m1, m5)
    if ema.side in (SIDE_BUY, SIDE_SELL) and ema.side == align.side:
        return _vote("structure", ema.side, "ema_y_align")
    if ema.side in (SIDE_BUY, SIDE_SELL) and align.side == SIDE_NONE:
        return _vote("structure", ema.side, "ema")
    if align.side in (SIDE_BUY, SIDE_SELL) and ema.side == SIDE_NONE:
        return _vote("structure", align.side, "align")
    if ema.side in (SIDE_BUY, SIDE_SELL) and align.side in (SIDE_BUY, SIDE_SELL):
        return _vote("structure", SIDE_NONE, "pelean")
    return _vote("structure", SIDE_NONE, "sin_estructura")


def pullback_allows(
    m1: Ohlcv,
    side: str,
    *,
    climax_atr: float = PULLBACK_CLIMAX_ATR,
) -> bool:
    """True when the last M1 tagged EMA9/20 and closed with the side, not a climax."""
    if side not in (SIDE_BUY, SIDE_SELL) or len(m1.closes) < 21:
        return False
    ema9 = _last_ema(m1.closes, 9)
    ema20 = _last_ema(m1.closes, 20)
    if ema9 is None or ema20 is None:
        return False
    close = float(m1.closes[-1])
    high = float(m1.highs[-1]) if m1.highs else close
    low = float(m1.lows[-1]) if m1.lows else close
    atr_v = _atr(m1) or abs(close) * 0.002
    if atr_v <= 0.0:
        return False
    touch = max(atr_v * 0.25, abs(close) * 1e-8)
    ema_lo = min(ema9, ema20)
    ema_hi = max(ema9, ema20)
    if side == SIDE_BUY:
        if (close - ema20) / atr_v >= float(climax_atr):
            return False
        return low <= ema_hi + touch and close > ema9
    if (ema20 - close) / atr_v >= float(climax_atr):
        return False
    return high >= ema_lo - touch and close < ema9


def rsi_range_blocks(direction: str, m1: Ohlcv, htf_side: str) -> bool:
    """In a range (HTF NONE), block BUY at RSI≥70 and SELL at RSI≤30. Trend: off."""
    if htf_side in (SIDE_BUY, SIDE_SELL):
        return False
    if direction not in (SIDE_BUY, SIDE_SELL):
        return False
    state = rsi_state(m1.closes)
    if state.rsi is None:
        return False
    rsi = float(state.rsi)
    if direction == SIDE_BUY and rsi >= 70.0:
        return True
    if direction == SIDE_SELL and rsi <= 30.0:
        return True
    return False


def _module_side(votes: Sequence[Vote], name: str) -> str:
    for v in votes:
        if v.name == name:
            return v.side
    return SIDE_NONE


def rotation_module_skip(
    votes: Sequence[Vote],
    consensus: Consensus,
    m1: Ohlcv,
) -> str:
    """Empty if 4/5 may proceed; else veto_htf / sin_pullback / rsi_rango / sin_consenso."""
    if not consensus.fired:
        return "sin_consenso"
    htf_s = _module_side(votes, "htf")
    if htf_s in (SIDE_BUY, SIDE_SELL) and consensus.direction != htf_s:
        return "veto_htf"
    if not pullback_allows(m1, consensus.direction):
        return "sin_pullback"
    if rsi_range_blocks(consensus.direction, m1, htf_s):
        return "rsi_rango"
    return ""


def rotation_structure_skip(symbol: str, murphy: Optional[dict]) -> str:
    """Empty if a passed 4/5 may send; else rank_tail / murphy_climax."""
    ranked = rotation_rank_skip(symbol)
    if ranked:
        return ranked
    if isinstance(murphy, dict) and murphy.get("would_veto") is True:
        return "murphy_climax"
    return ""


def cast_votes(
    symbol: str,
    m1: Ohlcv,
    m5: Ohlcv,
    *,
    m15: Optional[Ohlcv] = None,
    btc_m5: Optional[Ohlcv] = None,
    eth_m5: Optional[Ohlcv] = None,
    sol_m5: Optional[Ohlcv] = None,
) -> tuple[Vote, ...]:
    return (
        vote_htf(m15),
        vote_momentum_short(m1, m5),
        vote_intermarket(symbol, m5, btc_m5=btc_m5, eth_m5=eth_m5, sol_m5=sol_m5),
        vote_vol_bands(m1),
        vote_structure(m1, m5),
    )


def tally_votes(
    votes: Sequence[Vote],
    *,
    min_agree: int = MIN_AGREE,
    voter_count: int = VOTER_COUNT,
) -> Consensus:
    buy = sum(1 for v in votes if v.side == SIDE_BUY)
    sell = sum(1 for v in votes if v.side == SIDE_SELL)
    none = max(0, int(voter_count) - buy - sell)
    need = int(min_agree)
    fired = False
    direction = SIDE_NONE
    agree = 0
    if buy >= need and buy >= sell:
        fired = True
        direction = SIDE_BUY
        agree = buy
    elif sell >= need and sell > buy:
        fired = True
        direction = SIDE_SELL
        agree = sell
    else:
        agree = max(buy, sell)
    near = (not fired) and agree == need - 1
    return Consensus(
        direction=direction,
        buy=buy,
        sell=sell,
        none=none,
        agree=agree,
        fired=fired,
        near_miss=near,
    )


def risk_usd_from_votes(
    agree: int,
    *,
    voter_count: int = VOTER_COUNT,
    max_risk_usd: float = MAX_RISK_USD,
) -> tuple[float, float]:
    n = max(1, int(voter_count))
    p = max(0.0, min(1.0, float(agree) / float(n)))
    f_star = kelly_fraction(p, 1.0)
    cap = max(0.0, float(max_risk_usd))
    risk = min(cap, cap * f_star)
    return risk, f_star


def size_rotation_trade(
    *,
    agree: int,
    contract: ContractSpec,
    voter_count: int = VOTER_COUNT,
    max_risk_usd: float = MAX_RISK_USD,
) -> SizeDecision:
    risk, f_star = risk_usd_from_votes(
        agree, voter_count=voter_count, max_risk_usd=max_risk_usd
    )
    vmin = float(contract.volume_min or 0.0)
    if vmin <= 0.0:
        return SizeDecision(False, "volume_min_invalido", 0.0, 0.0, f_star)
    min_stop = float(contract.usd_at_min_lot_min_stop or 0.0)
    if min_stop > float(max_risk_usd) + 1e-9:
        return SizeDecision(False, "min_lote_supera_050", min_stop, 0.0, f_star)
    if min_stop > risk + 1e-9:
        if min_stop > float(max_risk_usd) + 1e-9:
            return SizeDecision(False, "min_lote_supera_050", min_stop, 0.0, f_star)
        risk = min_stop
    if (
        float(contract.price or 0.0) > 0.0
        and float(contract.tick_value or 0.0) > 0.0
        and float(contract.tick_size or 0.0) > 0.0
    ):
        sized = size_lot_for_percent_stop(
            risk_usd=risk,
            price=float(contract.price),
            tick_value=float(contract.tick_value),
            tick_size=float(contract.tick_size),
            volume_min=vmin,
            volume_step=float(contract.volume_step or vmin),
            volume_max=float(contract.volume_max or 100.0),
            max_risk_usd=float(max_risk_usd),
        )
        if not sized.ok:
            return SizeDecision(False, sized.reason, sized.usd, 0.0, f_star)
        return SizeDecision(True, sized.reason, round(sized.usd, 4), sized.lot, f_star)
    lot = clamp_lot_volume(
        vmin,
        volume_min=vmin,
        volume_step=float(contract.volume_step or vmin),
        volume_max=float(contract.volume_max or 100.0),
    )
    if lot <= 0.0:
        return SizeDecision(False, "lote_cero", risk, 0.0, f_star)
    if risk <= 0.0:
        return SizeDecision(False, "riesgo_cero", 0.0, 0.0, f_star)
    if risk - float(max_risk_usd) > 1e-9:
        return SizeDecision(False, "supera_tope", risk, 0.0, f_star)
    return SizeDecision(True, "ok", round(risk, 4), lot, f_star)


def hourly_quota_allows(opens_this_hour: int, *, cap: int = 60) -> bool:
    limit = int(cap)
    if limit <= 0:
        return True
    return int(opens_this_hour) < limit


def symbol_slot_free(open_symbols: Sequence[str], symbol: str) -> bool:
    want = str(symbol or "").strip().upper()
    held = {str(s or "").strip().upper() for s in open_symbols}
    return want not in held


def evaluate_symbol(
    symbol: str,
    m1: Ohlcv,
    m5: Ohlcv,
    *,
    m15: Optional[Ohlcv] = None,
    h4: Optional[Ohlcv] = None,
    btc_m5: Optional[Ohlcv] = None,
    eth_m5: Optional[Ohlcv] = None,
    sol_m5: Optional[Ohlcv] = None,
    contract: Optional[ContractSpec] = None,
    min_agree: int = MIN_AGREE,
    max_risk_usd: float = MAX_RISK_USD,
    m1_time: Optional[int] = None,
) -> EnsembleSnapshot:
    votes = cast_votes(
        symbol, m1, m5, m15=m15, btc_m5=btc_m5, eth_m5=eth_m5, sol_m5=sol_m5
    )
    consensus = tally_votes(votes, min_agree=min_agree)
    spec = contract or ContractSpec(volume_min=0.01, volume_step=0.01, usd_at_min_lot_min_stop=0.01)
    murphy = _murphy_if_fired(consensus, m15, h4)
    alpha = _alpha_if_fired(consensus, symbol, m15)
    state = _state_if_fired(
        consensus, m1=m1, votes=votes, murphy=murphy, alpha=alpha, spec=spec, m1_time=m1_time
    )
    gate = rotation_module_skip(votes, consensus, m1) or rotation_structure_skip(
        symbol, murphy
    )
    if gate:
        size = SizeDecision(False, gate, 0.0, 0.0, 0.0)
        return EnsembleSnapshot(
            symbol=symbol,
            votes=votes,
            consensus=consensus,
            size=size,
            skip_reason=gate,
            action="SKIP",
            m1_time=m1_time,
            murphy=murphy,
            alpha=alpha,
            state=state,
        )
    size = size_rotation_trade(
        agree=consensus.agree, contract=spec, max_risk_usd=max_risk_usd
    )
    if not size.ok:
        return EnsembleSnapshot(
            symbol=symbol,
            votes=votes,
            consensus=consensus,
            size=size,
            skip_reason=size.reason,
            action="SKIP",
            m1_time=m1_time,
            murphy=murphy,
            alpha=alpha,
            state=state,
        )
    return EnsembleSnapshot(
        symbol=symbol,
        votes=votes,
        consensus=consensus,
        size=size,
        skip_reason="",
        action="FIRE",
        m1_time=m1_time,
        murphy=murphy,
        alpha=alpha,
        state=state,
    )


def apply_account_gates(
    snap: EnsembleSnapshot,
    *,
    open_symbols: Sequence[str],
    opens_this_hour: int,
    hour_cap: int = 60,
    dry_run: bool = True,
) -> EnsembleSnapshot:
    if snap.action != "FIRE":
        return snap
    if not hourly_quota_allows(opens_this_hour, cap=hour_cap):
        return replace(snap, skip_reason="cuota_hora", action="SKIP")
    if not symbol_slot_free(open_symbols, snap.symbol):
        return replace(snap, skip_reason="ya_abierto", action="SKIP")
    action = "DRY_RUN" if dry_run else "LIVE"
    return replace(snap, skip_reason="", action=action)


def cycle_summary(snapshots: Sequence[EnsembleSnapshot], *, dry_run: bool, opens_this_hour: int, hour_cap: int) -> dict:
    fired = [s for s in snapshots if s.action in ("FIRE", "DRY_RUN", "LIVE")]
    near = [s for s in snapshots if s.consensus.near_miss]
    gated = [s for s in snapshots if s.skip_reason in MODULE_GATE_SKIPS]
    chips = []
    for s in snapshots:
        chips.append(
            {
                "symbol": s.symbol,
                "direction": s.consensus.direction,
                "buy": s.consensus.buy,
                "sell": s.consensus.sell,
                "action": s.action,
                "skip_reason": s.skip_reason,
                "consensus": {
                    "direction": s.consensus.direction,
                    "buy": s.consensus.buy,
                    "sell": s.consensus.sell,
                    "agree": s.consensus.agree,
                    "fired": s.consensus.fired,
                },
            }
        )
    return {
        "dry_run": bool(dry_run),
        "opens_this_hour": int(opens_this_hour),
        "hour_cap": int(hour_cap),
        "scanned": len(snapshots),
        "buy_consensus": sum(1 for s in snapshots if s.consensus.direction == SIDE_BUY and s.consensus.fired),
        "sell_consensus": sum(1 for s in snapshots if s.consensus.direction == SIDE_SELL and s.consensus.fired),
        "no_majority": sum(1 for s in snapshots if not s.consensus.fired),
        "voter_count": VOTER_COUNT,
        "min_agree": MIN_AGREE,
        "fired_count": len(fired),
        "near_miss_count": len(near),
        "fired": [s.as_dict() for s in fired[:20]],
        "near_miss": [s.as_dict() for s in near[:20]],
        "module_skips": [s.as_dict() for s in gated[:20]],
        "symbols": chips,
        "skip_counts": {
            r: sum(1 for s in snapshots if s.skip_reason == r)
            for r in {s.skip_reason for s in snapshots if s.skip_reason}
        },
    }
