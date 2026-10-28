"""El chequeo previo convertido en bot: ¿gana? Exploratorio, solo lectura.

Reglas fijadas antes de correr (2026-10-27). No se ajustan por el resultado:

- Cada minuto, al cierre, arma una candidata larga y una corta sobre BTC.
  - Stop: el último pivote confirmado de M15 (wing 2) del lado del stop.
  - Take profit: 5 veces la distancia al stop (Tudor Jones).
- Aprueba si pasa todo el chequeo de ``trading.domain.pretrade``:
  1. D1, H4 y H1 a favor (cierre vs EMA20 y pendiente de 4 velas cerradas).
  2. Lado correcto de la SMA200 diaria.
  3. No persigue: el movimiento de 15 min a favor ≤ 1.5× lo normal (mediana de 3 días).
  4. Stop fuera del ruido: distancia ≥ movimiento normal de 15 min.
  5. Spread ≤ 10% del riesgo.
  6. El lote mínimo (0.01) arriesga ≤ 10 USD: stop ≤ 1000 USD de precio.
- Si las dos lados aprueban a la vez, no entra.
- Ejecución: llena en la apertura del minuto siguiente. Sale por SL o TP intravela; si los
  dos caen en la misma vela, cuenta el SL. Si no toca ninguno en 24 h, cierra a mercado.
  Una posición a la vez.
- Costos Exness: spread 0.0118% ida y vuelta; swap 0.0222% por noche en largos.

Datos: Binance spot BTCUSDT 1m, 2024-10 → 2026-01-31. Ese tramo ya se usó para otras
reglas, así que un resultado positivo acá no prueba nada: solo decide si vale la pena
medirlo en forward. Referencia sin ventaja: con TP 5× el stop, el win rate de azar es 1/6.
Contraste: las mismas candidatas sin el chequeo y con dirección al azar (solo stop, TP y tiempo).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from analyze_murphy_intraday_prereg import _stats
from trading.domain.swing_structure import confirmed_pivots

DATA = Path("logs/binance/BTCUSDT_1m.npz")
OUT = Path("analyze_checklist_bot_out.json")
END = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
RR = 5.0
WING = 2
CHASE_MULT = 1.5
SPREAD_FRAC = 0.000118
SWAP_LONG_FRAC_PER_DAY = 0.000222
MAX_STOP_USD_PRICE = 1000.0  # 10 USD de riesgo con 0.01 lote
MAX_HOLD_MIN = 1440
SEED = 20261027


def main() -> int:
    d = np.load(DATA)
    keep = d["time"] < END + 2 * 86400
    t = d["time"][keep].astype(np.int64)
    o, h, l, c = (d[k][keep].astype(float) for k in ("open", "high", "low", "close"))
    ctx = _context(t, h, l, c)
    out = {"envia_ordenes": False, "exploratorio": True, "generado": datetime.now(timezone.utc).isoformat()}
    for name, use_checklist in (("con_chequeo", "full"), ("azar_filtros_no_direccionales", "nodir"),
                                ("sin_chequeo", "none")):
        trades, rejects = _walk(t, o, h, l, c, ctx, use_checklist)
        trades = [r for r in trades if r["entry_t"] < END]
        rng = lambda: np.random.default_rng(SEED)  # noqa: E731
        out[name] = {
            "neto_bps": _stats(trades, "net_bps", rng()),
            "bruto_bps": _stats(trades, "gross_bps", rng()),
            "r_neto": _stats(trades, "r", rng()),
            "por_lado": {s: _stats([x for x in trades if x["side"] == s], "r", rng()) for s in ("BUY", "SELL")},
            "salidas": {k: sum(1 for x in trades if x["exit"] == k) for k in ("tp", "sl", "tiempo")},
            "minutos_mediana": float(np.median([x["minutes"] for x in trades])) if trades else None,
            "stop_mediano_usd": float(np.median([x["stop_usd"] for x in trades])) if trades else None,
            "rechazos": rejects,
        }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("con_chequeo", "azar_filtros_no_direccionales", "sin_chequeo"):
        b = out[name]
        r, nb, gb = b["r_neto"], b["neto_bps"], b["bruto_bps"]
        if not r.get("n"):
            print(name, "sin trades", b["rechazos"])
            continue
        print(f"\n[{name}] n={r['n']} dias={r['dias']} WR={r['winrate']:.1%} (azar 16.7%)  "
              f"R neto medio={r['media_bps']:+.3f} IC95=[{r['ic95_bps'][0]:+.3f}, {r['ic95_bps'][1]:+.3f}] p={r['p_una_cola']:.3f} PF={r['pf']:.2f}")
        print(f"  bps: bruto {gb['media_bps']:+.2f}  neto {nb['media_bps']:+.2f}  | salidas {b['salidas']} | "
              f"minutos mediana {b['minutos_mediana']} | stop mediano {b['stop_mediano_usd']:.0f} USD")
        for s, x in b["por_lado"].items():
            if x.get("n"):
                print(f"  {s}: n={x['n']} WR={x['winrate']:.1%} R medio={x['media_bps']:+.3f}")
        print(f"  rechazos: {b['rechazos']}")
    return 0


def _bars(t, h, l, c, tf_min):
    b = t // (tf_min * 60)
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], len(t)]
    close_t = b[starts] * tf_min * 60 + tf_min * 60
    return (close_t, np.maximum.reduceat(h, starts), np.minimum.reduceat(l, starts), c[ends - 1])


def _trend_labels(closes, period=20, slope=4):
    alpha = 2.0 / (period + 1.0)
    ema = np.empty(len(closes))
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = alpha * closes[i] + (1 - alpha) * ema[i - 1]
    lab = np.zeros(len(closes), dtype=int)
    prev = np.r_[np.full(slope, np.nan), ema[:-slope]]
    lab[(closes > ema) & (ema > prev)] = 1
    lab[(closes < ema) & (ema < prev)] = -1
    lab[: period + slope] = 0
    return lab


def _context(t, h, l, c) -> dict[str, Any]:
    now = t + 60  # la señal se evalúa al cierre del minuto
    ctx: dict[str, Any] = {}
    for name, tf in (("H1", 60), ("H4", 240), ("D1", 1440)):
        ct, _, _, cc = _bars(t, h, l, c, tf)
        idx = np.searchsorted(ct, now, side="right") - 1
        lab = _trend_labels(cc)
        ctx[name] = np.where(idx >= 0, lab[np.clip(idx, 0, None)], 0)
        if name == "D1":
            sma = np.convolve(cc, np.ones(200) / 200, mode="full")[: len(cc)]
            sma[:199] = np.nan
            ctx["sma200"] = np.where(idx >= 0, sma[np.clip(idx, 0, None)], np.nan)
    ct, mh, ml, _ = _bars(t, h, l, c, 15)
    piv = confirmed_pivots(mh.tolist(), ml.tolist(), wing=WING)
    # Un pivote de la vela j se confirma al cierre de la vela j+WING.
    for kind in ("low", "high"):
        rows = sorted((ct[min(j + WING, len(ct) - 1)], p) for j, p, k in piv if k == kind)
        ctx[f"piv_{kind}"] = (np.array([r[0] for r in rows]), np.array([r[1] for r in rows]))
    move = np.full(len(t), np.nan)
    move[15:] = c[15:] - c[:-15]
    typical = np.full(len(t), np.nan)
    hours = np.flatnonzero(t % 3600 == 0)
    for i in hours:
        if i >= 3 * 1440:
            w = move[i - 3 * 1440 : i]
            typical[i : i + 60] = np.nanmedian(np.abs(w))
    ctx["move15"], ctx["typical15"] = move, typical
    return ctx


def _last_pivot(pivots, when, below=None, above=None):
    """Último pivote confirmado a ``when`` por debajo/encima del precio (busca hasta 30 atrás)."""
    times, prices = pivots
    k = int(np.searchsorted(times, when, side="right")) - 1
    for j in range(k, max(-1, k - 30), -1):
        p = prices[j]
        if (below is not None and p < below) or (above is not None and p > above):
            return p
    return None


def _check(side, entry, stop, i, ctx, use_checklist) -> str | None:
    dist = abs(entry - stop)
    if dist > MAX_STOP_USD_PRICE:
        return "lote_minimo"
    if use_checklist == "none":
        return None
    want = 1 if side == "BUY" else -1
    typ = ctx["typical15"][i]
    if np.isnan(typ) or np.isnan(ctx["move15"][i]):
        return "sin_datos"
    if use_checklist == "full":
        if not (ctx["D1"][i] == want and ctx["H4"][i] == want and ctx["H1"][i] == want):
            return "tendencias"
        sma = ctx["sma200"][i]
        if np.isnan(sma) or (side == "BUY" and entry <= sma) or (side == "SELL" and entry >= sma):
            return "sma200"
        if want * ctx["move15"][i] > CHASE_MULT * typ:
            return "persigue"
    if dist < typ:
        return "ruido"
    if SPREAD_FRAC * entry > 0.10 * dist:
        return "spread"
    return None


def _walk(t, o, h, l, c, ctx, use_checklist):
    trades, rejects = [], {}
    rng = np.random.default_rng(SEED)
    n = len(t)
    i = 3 * 1440
    while i < n - 2:
        if t[i] >= END:
            break
        when = t[i] + 60
        px = c[i]
        cands = []
        lo = _last_pivot(ctx["piv_low"], when, below=px)
        hi = _last_pivot(ctx["piv_high"], when, above=px)
        for side, stop in (("BUY", lo), ("SELL", hi)):
            if stop is None:
                rejects["sin_pivote"] = rejects.get("sin_pivote", 0) + 1
                continue
            why = _check(side, px, stop, i, ctx, use_checklist)
            if why:
                rejects[why] = rejects.get(why, 0) + 1
            else:
                cands.append((side, stop))
        if len(cands) == 2 and use_checklist != "full":
            # Contraste sin ventaja: mismas mecánicas, dirección al azar.
            cands = [cands[int(rng.integers(2))]]
        if len(cands) != 1:
            if len(cands) == 2:
                rejects["dos_lados"] = rejects.get("dos_lados", 0) + 1
            i += 1
            continue
        side, stop = cands[0]
        tr, exit_i = _simulate(t, o, h, l, c, i + 1, side, stop)
        if tr is None:
            i += 1
            continue
        trades.append(tr)
        i = exit_i + 1
    return trades, rejects


def _simulate(t, o, h, l, c, k, side, stop):
    entry = o[k]
    dist = abs(entry - stop)
    if (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry) or dist <= 0:
        return None, k
    tp = entry + RR * dist if side == "BUY" else entry - RR * dist
    end = min(len(t) - 1, k + MAX_HOLD_MIN - 1)
    hh, ll = h[k : end + 1], l[k : end + 1]
    if side == "BUY":
        hit_sl, hit_tp = ll <= stop, hh >= tp
    else:
        hit_sl, hit_tp = hh >= stop, ll <= tp
    first_sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
    first_tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
    if first_sl is not None and (first_tp is None or first_sl <= first_tp):
        j, px, why = k + first_sl, stop, "sl"
        if side == "BUY" and o[j] < stop or side == "SELL" and o[j] > stop:
            px = o[j]  # hueco a través del stop
    elif first_tp is not None:
        j, px, why = k + first_tp, tp, "tp"
    else:
        j, px, why = end, c[end], "tiempo"
    sign = 1.0 if side == "BUY" else -1.0
    gross = sign * (px - entry) / entry
    nights = int(t[j] // 86400 - t[k] // 86400)
    swap = SWAP_LONG_FRAC_PER_DAY * nights if side == "BUY" else 0.0
    net = gross - SPREAD_FRAC - swap
    risk = dist / entry
    return {"entry_t": int(t[k]), "side": side, "exit": why, "minutes": int(j - k + 1), "stop_usd": dist,
            "gross_bps": gross * 1e4, "net_bps": net * 1e4, "r": net / risk}, j


if __name__ == "__main__":
    sys.exit(main())
