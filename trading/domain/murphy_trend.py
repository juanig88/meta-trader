"""Chapter-4 trend structure. Pure: no I/O, no orders.

Detection knobs that the book does not quantify live in ``params``:
``wing`` (bars on each side of a pivot) and ``touch_tol_frac`` (how near a
pivot must sit to count as a touch). The 3% penetration figure is not applied
as a law; a break is two consecutive closes beyond the line. A close that
only pierces with a wick does not break it.

Degrees use daily bars: minor ~3 weeks, intermediate ~3 months, major ~6 months.
A short series does not pretend to be the major trend.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from trading.domain.murphy_tools import bar_tools, ray_at
from trading.domain.swing_structure import confirmed_pivots

# Daily bars. Crypto D1 includes weekends, so the counts are calendar days.
DEGREE_BARS: tuple[tuple[str, int], ...] = (
    ("menor", 21),
    ("intermedia", 90),
    ("principal", 180),
)
DEFAULT_WING = 2
DEFAULT_TOUCH_TOL = 0.005
TWO_THIRDS = 2.0 / 3.0
# A line for a degree has to cover a real piece of that window. These floors
# are ours: without them the six-month line collapses onto the last two weeks.
DEGREE_MIN_SPAN: dict[str, int] = {
    "menor": 0,
    "intermedia": 15,
    "principal": 60,
}


def nearest_round(price: float) -> Optional[float]:
    """Nearest 1-2-5 grid step at about one tenth of the price.

    The chapter says stops do not sit on the round number. It does not define
    the grid. This step is ours, so the snapshot can name the level.
    """
    px = float(price)
    if not math.isfinite(px) or px <= 0.0:
        return None
    target = px / 10.0
    best = 1.0
    base = 1.0
    while base <= target + 1e-12:
        for mult in (1.0, 2.0, 5.0):
            cand = mult * base
            if cand <= target + 1e-12:
                best = cand
        base *= 10.0
    steps = px / best
    return float(round(steps) * best)


def read_degrees(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    wing: int = DEFAULT_WING,
    touch_tol_frac: float = DEFAULT_TOUCH_TOL,
    scale: str = "linear",
    volumes: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    """One reading per degree, plus the stance of the intermediate trend."""
    series = _as_series(highs, lows, closes)
    grados: dict[str, Any] = {}
    if series is None:
        for name, need in DEGREE_BARS:
            grados[name] = _empty_degree(need)
        return _wrap(grados, wing, touch_tol_frac, bars=0, scale=scale, herramientas={})

    h, l, c = series
    for name, need in DEGREE_BARS:
        if len(c) < need:
            grados[name] = _empty_degree(need)
            continue
        reading = read_structure(
            h[-need:], l[-need:], c[-need:],
            wing=wing, touch_tol_frac=touch_tol_frac, scale=scale,
            min_span=DEGREE_MIN_SPAN[name],
        )
        reading["suficiente"] = True
        reading["barras"] = need
        reading["desde_indice"] = len(c) - need
        grados[name] = reading
    return _wrap(
        grados, wing, touch_tol_frac, bars=len(c), scale=scale,
        herramientas=bar_tools(h, l, c, volumes),
    )


def read_structure(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    wing: int = DEFAULT_WING,
    touch_tol_frac: float = DEFAULT_TOUCH_TOL,
    scale: str = "linear",
    min_span: int = 0,
) -> dict[str, Any]:
    """Structure of one closed series. Never an order."""
    series = _as_series(highs, lows, closes)
    if series is None or len(series[2]) < 2 * max(1, int(wing)) + 1:
        out = _empty_degree(len(closes) if series is None else len(series[2]))
        out["suficiente"] = False
        return out

    h, l, c = series
    tol = max(0.0, float(touch_tol_frac))
    w = max(1, int(wing))
    raw = confirmed_pivots(h, l, wing=w)
    pivots = [{"index": int(i), "price": float(p), "kind": k} for i, p, k in raw]
    highs_p = [p for p in pivots if p["kind"] == "high"]
    lows_p = [p for p in pivots if p["kind"] == "low"]
    regime = _regime(highs_p, lows_p)
    escala = "log" if str(scale).lower() == "log" else "linear"
    linea = _line_for_regime(regime, highs_p, lows_p, c, tol, scale=escala, min_span=int(min_span))
    canal = _channel(linea, highs_p, lows_p, c, tol)
    retro = _retracement(regime, highs_p, lows_p, h, l, c)
    apoyo = _prior_swing(regime, highs_p, lows_p, c, tol)
    interna = _line_for_regime(
        regime, _priced_at_close(highs_p, c), _priced_at_close(lows_p, c), c, tol,
        scale=escala, min_span=int(min_span),
    )
    abanico = _fan(regime, highs_p, lows_p, c, tol)
    papeles = _role_flips(highs_p, lows_p, c)
    nivel = nearest_round(c[-1])
    motivos = _invalid_reasons(regime, linea, retro, apoyo)
    return {
        "suficiente": True,
        "barras": len(c),
        "desde_indice": 0,
        "regime": regime,
        "postura": _stance(regime),
        "envia_ordenes": False,
        "pivots": pivots,
        "linea": linea,
        "linea_interna": interna,
        "velocidad": _speed_lines(regime, highs_p, lows_p, len(c) - 1),
        "canal": canal,
        "retroceso": retro,
        "apoyo_previo": apoyo,
        "abanico": abanico,
        "cambio_de_papel": papeles,
        "redondo": {
            "nivel": nivel,
            "stop_largo": "debajo" if nivel is not None else None,
            "stop_corto": "arriba" if nivel is not None else None,
        },
        "premisa_invalidada": bool(motivos),
        "motivos": motivos,
    }


def _wrap(
    grados: dict[str, Any],
    wing: int,
    tol: float,
    *,
    bars: int,
    scale: str = "linear",
    herramientas: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    inter = grados["intermedia"]
    principal = grados["principal"]
    conflicto = False
    if inter.get("suficiente") and principal.get("suficiente"):
        pair = {inter.get("regime"), principal.get("regime")}
        conflicto = pair == {"alcista", "bajista"}
    if not inter.get("suficiente"):
        postura = "sin_datos"
    else:
        postura = _stance(str(inter.get("regime") or "insuficiente"))
    return {
        "fase": 1,
        "envia_ordenes": False,
        "barras": bars,
        "postura": postura,
        "conflicto_principal": conflicto,
        "grados": grados,
        "herramientas": herramientas or {},
        "parametros": {
            "wing": int(wing),
            "touch_tol_frac": float(tol),
            "escala": scale,
            "min_span": dict(DEGREE_MIN_SPAN),
            "grados_barras": {n: b for n, b in DEGREE_BARS},
        },
    }


def _empty_degree(bars: int) -> dict[str, Any]:
    return {
        "suficiente": False,
        "barras": int(bars),
        "desde_indice": None,
        "regime": "insuficiente",
        "postura": "sin_datos",
        "envia_ordenes": False,
        "pivots": [],
        "linea": None,
        "canal": None,
        "retroceso": None,
        "apoyo_previo": None,
        "abanico": None,
        "cambio_de_papel": [],
        "redondo": {"nivel": None, "stop_largo": None, "stop_corto": None},
        "premisa_invalidada": False,
        "motivos": [],
    }


def _as_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
) -> Optional[tuple[list[float], list[float], list[float]]]:
    if len(highs) != len(lows) or len(lows) != len(closes) or not closes:
        return None
    h: list[float] = []
    l: list[float] = []
    c: list[float] = []
    for hv, lv, cv in zip(highs, lows, closes):
        try:
            hf, lf, cf = float(hv), float(lv), float(cv)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(hf) and math.isfinite(lf) and math.isfinite(cf)):
            return None
        if hf < lf:
            return None
        h.append(hf)
        l.append(lf)
        c.append(cf)
    return h, l, c


def _regime(highs: list[dict[str, Any]], lows: list[dict[str, Any]]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return "insuficiente"
    h1, h2 = highs[-2]["price"], highs[-1]["price"]
    l1, l2 = lows[-2]["price"], lows[-1]["price"]
    if h2 > h1 and l2 > l1:
        return "alcista"
    if h2 < h1 and l2 < l1:
        return "bajista"
    return "lateral"


def _stance(regime: str) -> str:
    if regime == "lateral":
        return "afuera"
    if regime in ("alcista", "bajista"):
        return "solo_lectura"
    return "sin_datos"


def _eps(price: float, tol: float) -> float:
    return abs(float(price)) * max(float(tol), 1e-9)


def _project(i1: int, p1: float, slope: float, index: int, scale: str = "linear") -> float:
    if scale == "log":
        return math.exp(math.log(p1) + slope * (index - i1))
    return p1 + slope * (index - i1)


def _priced_at_close(points: list[dict[str, Any]], closes: Sequence[float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pt in points:
        idx = int(pt["index"])
        out.append({"index": idx, "price": float(closes[idx]), "kind": pt["kind"]})
    return out


def _line_for_regime(
    regime: str,
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
    *,
    scale: str = "linear",
    min_span: int = 0,
) -> Optional[dict[str, Any]]:
    if regime == "alcista":
        return _best_line(lows, closes, tol, rising=True, scale=scale, min_span=min_span)
    if regime == "bajista":
        return _best_line(highs, closes, tol, rising=False, scale=scale, min_span=min_span)
    return None


def _best_line(
    points: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
    *,
    rising: bool,
    scale: str = "linear",
    min_span: int = 0,
) -> Optional[dict[str, Any]]:
    intact: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    broken: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for a in range(len(points)):
        for b in range(a + 1, len(points)):
            built = _build_line(
                points[a], points[b], points[b + 1 :], closes, tol, rising=rising, scale=scale,
            )
            if built is None or int(built["extension"]) < int(min_span):
                continue
            # More touches, then a longer line, then the more recent one.
            score = (int(built["toques_extra"]), int(built["extension"]), int(built["anclas"][1]["index"]))
            bucket = broken if built["rompe_dos_cierres"] else intact
            bucket.append((score, built))
    pool = intact or broken
    if not pool:
        return None
    chosen = max(pool, key=lambda item: item[0])[1]
    if chosen["rompe_dos_cierres"]:
        chosen["estado"] = "rota"
    elif chosen["toques_extra"] >= 1:
        chosen["estado"] = "valida"
    else:
        chosen["estado"] = "orientativa"
    return chosen


def _build_line(
    first: dict[str, Any],
    second: dict[str, Any],
    later: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
    *,
    rising: bool,
    scale: str = "linear",
) -> Optional[dict[str, Any]]:
    i1, p1 = int(first["index"]), float(first["price"])
    i2, p2 = int(second["index"]), float(second["price"])
    if i2 <= i1:
        return None
    if rising and not p2 > p1:
        return None
    if not rising and not p2 < p1:
        return None
    if scale == "log":
        if p1 <= 0.0 or p2 <= 0.0:
            return None
        slope = (math.log(p2) - math.log(p1)) / (i2 - i1)
    else:
        slope = (p2 - p1) / (i2 - i1)
    extras = 0
    for pt in later:
        projected = _project(i1, p1, slope, int(pt["index"]), scale)
        if abs(float(pt["price"]) - projected) <= _eps(projected, tol):
            extras += 1
    n = len(closes)
    at_prev = _project(i1, p1, slope, n - 2, scale)
    at_last = _project(i1, p1, slope, n - 1, scale)
    c_prev, c_last = float(closes[-2]), float(closes[-1])
    if rising:
        broke = c_prev < at_prev - _eps(at_prev, tol) and c_last < at_last - _eps(at_last, tol)
        pen = (at_last - c_last) / c_last if broke and c_last else 0.0
    else:
        broke = c_prev > at_prev + _eps(at_prev, tol) and c_last > at_last + _eps(at_last, tol)
        pen = (c_last - at_last) / c_last if broke and c_last else 0.0
    return {
        "lado": "alcista" if rising else "bajista",
        "estado": "orientativa",
        "toques": 2 + extras,
        "toques_extra": extras,
        "extension": i2 - i1,
        "escala": scale,
        "slope": slope,
        "pct_por_barra": slope if scale == "log" else (slope / p2 if p2 else 0.0),
        "anclas": [{"index": i1, "price": p1}, {"index": i2, "price": p2}],
        "precio_en_ultima": at_last,
        "rompe_dos_cierres": broke,
        "penetracion_frac": pen if broke else 0.0,
    }


def _channel(
    linea: Optional[dict[str, Any]],
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
) -> Optional[dict[str, Any]]:
    if not linea or linea["estado"] == "rota":
        return None
    i1 = int(linea["anclas"][0]["index"])
    p1 = float(linea["anclas"][0]["price"])
    slope = float(linea["slope"])
    scale = str(linea.get("escala") or "linear")
    up = linea["lado"] == "alcista"
    peaks = highs if up else lows
    first = None
    for pt in peaks:
        idx = int(pt["index"])
        if idx <= i1:
            continue
        line_px = _project(i1, p1, slope, idx, scale)
        if up and float(pt["price"]) > line_px:
            first = pt
            break
        if not up and float(pt["price"]) < line_px:
            first = pt
            break
    if first is None:
        return None
    width = abs(float(first["price"]) - _project(i1, p1, slope, int(first["index"]), scale))
    if width <= 0.0:
        return None
    sign = 1.0 if up else -1.0
    n = len(closes)

    def channel_at(index: int) -> float:
        return _project(i1, p1, slope, index, scale) + sign * width

    later_peaks = [pt for pt in peaks if int(pt["index"]) > int(first["index"])]
    falla = False
    if later_peaks:
        last_peak = later_peaks[-1]
        band = _eps(channel_at(int(last_peak["index"])), tol)
        if up:
            falla = float(last_peak["price"]) < channel_at(int(last_peak["index"])) - band
        else:
            falla = float(last_peak["price"]) > channel_at(int(last_peak["index"])) + band
    last_ch = channel_at(n - 1)
    close = float(closes[-1])
    accel = close > last_ch + _eps(last_ch, tol) if up else close < last_ch - _eps(last_ch, tol)
    return {
        "ancho": width,
        "falla_en_alcanzar": falla,
        "aceleracion": accel,
        "objetivo": (last_ch + sign * width) if accel else None,
    }


def _speed_lines(
    regime: str,
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    index: int,
) -> Optional[dict[str, float]]:
    """Rays from the impulse origin through one third and two thirds of the move."""
    if regime == "bajista" and lows:
        end = lows[-1]
        prior = [pt for pt in highs if int(pt["index"]) < int(end["index"])]
    elif regime == "alcista" and highs:
        end = highs[-1]
        prior = [pt for pt in lows if int(pt["index"]) < int(end["index"])]
    else:
        return None
    if not prior:
        return None
    origin = prior[-1]
    o_i, o_p = int(origin["index"]), float(origin["price"])
    e_i, e_p = int(end["index"]), float(end["price"])
    return {
        "tercio": ray_at(o_p, e_p, 1.0 / 3.0, o_i, e_i, index),
        "dos_tercios": ray_at(o_p, e_p, TWO_THIRDS, o_i, e_i, index),
    }


def _retracement(
    regime: str,
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    bar_highs: Sequence[float],
    bar_lows: Sequence[float],
    closes: Sequence[float],
) -> Optional[dict[str, Any]]:
    """Giveback of the last impulse in the trend's direction.

    An uptrend corrects down, so the fraction is how much of the rise has been
    given back. A downtrend corrects up, so the fraction is how much of the
    drop has been recovered. Falling further in a downtrend is not a retracement.
    """
    if regime == "bajista":
        return _retrace_drop(highs, lows, bar_highs, closes)
    return _retrace_rise(highs, lows, bar_lows, closes)


def _retrace_rise(
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    bar_lows: Sequence[float],
    closes: Sequence[float],
) -> Optional[dict[str, Any]]:
    if not highs or not lows:
        return None
    last_high = highs[-1]
    prior_lows = [pt for pt in lows if int(pt["index"]) < int(last_high["index"])]
    if not prior_lows:
        return None
    hi = float(last_high["price"])
    lo = float(prior_lows[-1]["price"])
    span = hi - lo
    if span <= 0.0:
        return None
    close = float(closes[-1])
    after = int(last_high["index"]) + 1
    extreme = min(bar_lows[after:], default=close)
    return _retrace_payload(lo, hi, (hi - close) / span, (hi - float(extreme)) / span, up=True)


def _retrace_drop(
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    bar_highs: Sequence[float],
    closes: Sequence[float],
) -> Optional[dict[str, Any]]:
    if not highs or not lows:
        return None
    last_low = lows[-1]
    prior_highs = [pt for pt in highs if int(pt["index"]) < int(last_low["index"])]
    if not prior_highs:
        return None
    hi = float(prior_highs[-1]["price"])
    lo = float(last_low["price"])
    span = hi - lo
    if span <= 0.0:
        return None
    close = float(closes[-1])
    after = int(last_low["index"]) + 1
    extreme = max(bar_highs[after:], default=close)
    return _retrace_payload(lo, hi, (close - lo) / span, (float(extreme) - lo) / span, up=False)


def _retrace_payload(lo: float, hi: float, frac_close: float, frac_extreme: float, *, up: bool) -> dict[str, Any]:
    span = hi - lo
    if up:
        tercio, mitad, dos = hi - span / 3.0, hi - span / 2.0, hi - span * TWO_THIRDS
    else:
        tercio, mitad, dos = lo + span / 3.0, lo + span / 2.0, lo + span * TWO_THIRDS
    return {
        "impulso_bajo": lo,
        "impulso_alto": hi,
        "frac_cierre": frac_close,
        "frac_extremo": frac_extreme,
        "nivel_tercio": tercio,
        "nivel_mitad": mitad,
        "nivel_dos_tercios": dos,
        "pasa_dos_tercios": frac_close > TWO_THIRDS + 1e-9,
    }


def _prior_swing(
    regime: str,
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
) -> Optional[dict[str, Any]]:
    close = float(closes[-1])
    if regime == "lateral" and len(lows) >= 2 and len(highs) >= 2:
        prev_lo, last_lo = float(lows[-2]["price"]), float(lows[-1]["price"])
        prev_hi, last_hi = float(highs[-2]["price"]), float(highs[-1]["price"])
        return {
            "ultimo": last_lo,
            "anterior": prev_lo,
            "minimos_iguales": abs(last_lo - prev_lo) <= _eps(prev_lo, tol),
            "maximos_iguales": abs(last_hi - prev_hi) <= _eps(prev_hi, tol),
            "cierre_bajo_ultimo_apoyo": False,
        }
    if regime == "alcista" and len(lows) >= 2:
        prev, last = float(lows[-2]["price"]), float(lows[-1]["price"])
        band = _eps(prev, tol)
        return {
            "ultimo": last,
            "anterior": prev,
            "minimos_iguales": abs(last - prev) <= band,
            "cierre_bajo_ultimo_apoyo": close < last - _eps(last, tol),
        }
    if regime == "bajista" and len(highs) >= 2:
        prev, last = float(highs[-2]["price"]), float(highs[-1]["price"])
        band = _eps(prev, tol)
        return {
            "ultimo": last,
            "anterior": prev,
            "maximos_iguales": abs(last - prev) <= band,
            "cierre_sobre_ultima_resistencia": close > last + _eps(last, tol),
        }
    return None


def _fan(
    regime: str,
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
) -> Optional[dict[str, Any]]:
    if regime == "alcista":
        return _fan_side(lows, closes, tol, up=True)
    if regime == "bajista":
        return _fan_side(highs, closes, tol, up=False)
    return None


def _fan_side(
    points: list[dict[str, Any]],
    closes: Sequence[float],
    tol: float,
    *,
    up: bool,
) -> Optional[dict[str, Any]]:
    best: Optional[tuple[int, dict[str, Any]]] = None
    for origin in range(len(points) - 3):
        group = points[origin : origin + 4]
        i0, p0 = int(group[0]["index"]), float(group[0]["price"])
        slopes: list[float] = []
        ok = True
        for pt in group[1:]:
            idx = int(pt["index"])
            if idx <= i0:
                ok = False
                break
            slopes.append((float(pt["price"]) - p0) / (idx - i0))
        if not ok or len(slopes) != 3:
            continue
        if up and not (slopes[0] > slopes[1] > slopes[2] > 0.0):
            continue
        if not up and not (slopes[0] < slopes[1] < slopes[2] < 0.0):
            continue
        i3 = int(group[3]["index"])
        slope3 = slopes[2]
        n = len(closes)
        at_prev = _project(i0, p0, slope3, n - 2)
        at_last = _project(i0, p0, slope3, n - 1)
        c_prev, c_last = float(closes[-2]), float(closes[-1])
        if up:
            broke = c_prev < at_prev - _eps(at_prev, tol) and c_last < at_last - _eps(at_last, tol)
        else:
            broke = c_prev > at_prev + _eps(at_prev, tol) and c_last > at_last + _eps(at_last, tol)
        payload = {"lineas": 3, "rompe_tercera": broke, "ancla_origen": i0, "ancla_tercera": i3}
        if best is None or i3 >= best[0]:
            best = (i3, payload)
    return None if best is None else best[1]


def _role_flips(
    highs: list[dict[str, Any]],
    lows: list[dict[str, Any]],
    closes: Sequence[float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pt in highs:
        flip = _flip_level(float(pt["price"]), int(pt["index"]), closes, was="resistencia", up=True)
        if flip is not None:
            out.append(flip)
    for pt in lows:
        flip = _flip_level(float(pt["price"]), int(pt["index"]), closes, was="apoyo", up=False)
        if flip is not None:
            out.append(flip)
    return out[-6:]


def _flip_level(
    level: float,
    index: int,
    closes: Sequence[float],
    *,
    was: str,
    up: bool,
) -> Optional[dict[str, Any]]:
    after = [float(c) for c in closes[index + 1 :]]
    if len(after) < 2 or level == 0.0:
        return None
    two = False
    for a, b in zip(after, after[1:]):
        if up and a > level and b > level:
            two = True
            break
        if not up and a < level and b < level:
            two = True
            break
    if not two:
        return None
    extreme = max(after) if up else min(after)
    frac = abs(extreme - level) / abs(level)
    return {
        "nivel": level,
        "era": was,
        "ahora": "apoyo" if was == "resistencia" else "resistencia",
        "penetracion_frac": frac,
        "dos_cierres": True,
    }


def _invalid_reasons(
    regime: str,
    linea: Optional[dict[str, Any]],
    retro: Optional[dict[str, Any]],
    apoyo: Optional[dict[str, Any]],
) -> list[str]:
    if regime not in ("alcista", "bajista"):
        return []
    motivos: list[str] = []
    if linea and linea.get("rompe_dos_cierres"):
        motivos.append("rompe_dos_cierres")
    if retro and retro.get("pasa_dos_tercios"):
        motivos.append("pasa_dos_tercios")
    if apoyo and apoyo.get("cierre_bajo_ultimo_apoyo"):
        motivos.append("cierre_bajo_ultimo_apoyo")
    if apoyo and apoyo.get("cierre_sobre_ultima_resistencia"):
        motivos.append("cierre_sobre_ultima_resistencia")
    return motivos
