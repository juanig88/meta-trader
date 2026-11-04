"""Read-only market and account snapshot for a planned manual BTC trade: the variables the bots read.

Prints one JSON with what the demo bots look at right now (zones, break state, EMA 20 per timeframe,
calm market, time window, what TP chico C1 and TP chico lateral would do, distance to the ATH) and,
when given an order, its numbers in account USD next to the open positions of the real account.

It never sends, modifies or closes anything. Market data come from the demo terminal
(BOT_IMPULSO_MT5_PATH, same BTC price); the account is read from the real terminal only with --cuenta.

    python analizar_operacion.py
    python analizar_operacion.py --lado sell --lote 0.01 --sl 80275.95 --tp 78175.84
    python analizar_operacion.py --lado buy --lote 0.01 --precio 77426.7 --sl 73674.97 --tp 77541.27 --cuenta

Used by the analizar-operacion skill (.claude/skills/analizar-operacion/SKILL.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from impulso_m1 import _bars, _connect, _env, _load_dotenv
from trading.domain.ath import drawdown_from_ath
from trading.domain.rebound_zones import find_recent_zones, find_zones
from trading.domain.tp_chico import ema_last, trend_side
from trading.domain.tp_lateral import (
    BIG_LAST, BIG_MULT, BIG_REF, RUN_BARS, ZONE_MAX, LateralState, calm_block, entry_block, time_block,
    zone_distance,
)

REAL_PATH_DEFAULT = r"C:\Program Files\MetaTrader 5\terminal64.exe"
BREAK_PCT, BREAK_HOURS = 2.0, 24  # a break counts only with 2% and 24 H1 closes outside
TFS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _zone(z, price):
    inside = z.low <= price <= z.high
    edge = z.low if price < z.low else z.high if price > z.high else price
    return {"tipo": z.kind, "desde": _r(z.low), "hasta": _r(z.high), "toques": z.touches, "origen": z.origin,
            "dist_pct": 0.0 if inside else _r(abs(price - edge) / price * 100, 3),
            "posicion": "adentro" if inside else "arriba" if z.low > price else "abajo"}


def _break_state(z, h1):
    """H1 closes beyond the zone in the last 48h: excursion and hours outside (the 2%/24h rule)."""
    closes = [b.close for b in h1[-48:]]
    lows, highs = [b.low for b in h1[-48:]], [b.high for b in h1[-48:]]
    if z.kind == "piso":
        out = [c for c in closes if c < z.low]
        wick = (z.low - min(lows)) / z.low * 100 if min(lows) < z.low else 0.0
        exc = (z.low - min(out)) / z.low * 100 if out else 0.0
        streak = 0
        for c in reversed(closes):
            if c >= z.low:
                break
            streak += 1
    else:
        out = [c for c in closes if c > z.high]
        wick = (max(highs) - z.high) / z.high * 100 if max(highs) > z.high else 0.0
        exc = (max(out) - z.high) / z.high * 100 if out else 0.0
        streak = 0
        for c in reversed(closes):
            if c <= z.high:
                break
            streak += 1
    if not out and wick == 0:
        state = "intacta"
    elif exc >= BREAK_PCT and streak >= BREAK_HOURS:
        state = "ruptura_confirmada"
    elif streak:
        state = "en_evaluacion"
    else:
        state = "ruptura_falsa_volvio"
    return {"zona": f"{z.kind} {z.low:.0f}-{z.high:.0f}", "estado": state, "cierres_h1_afuera_48h": len(out),
            "horas_afuera_seguidas": streak, "max_cierre_afuera_pct": _r(exc, 2), "max_mecha_afuera_pct": _r(wick, 2)}


def market(mt5, symbol: str) -> dict:
    now = datetime.now(timezone.utc)
    tick = mt5.symbol_info_tick(symbol)
    bid, ask = float(tick.bid), float(tick.ask)
    out: dict = {"hora_utc": now.strftime("%Y-%m-%d %H:%M"), "dia": ["lunes", "martes", "miercoles", "jueves",
                 "viernes", "sabado", "domingo"][now.weekday()], "bid": bid, "ask": ask, "spread": _r(ask - bid)}

    h1 = _bars(mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 1440))
    m30 = _bars(mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 1, 96))
    zones = find_zones(h1) + find_recent_zones(m30)
    near = sorted((z for z in zones if abs((z.low + z.high) / 2 - bid) / bid < 0.06),
                  key=lambda z: abs((z.low + z.high) / 2 - bid))
    above = [z for z in zones if z.low > bid]
    below = [z for z in zones if z.high < bid]
    inside = [z for z in zones if z.low <= bid <= z.high]
    out["zonas"] = {
        "adentro_de": [_zone(z, bid) for z in inside],
        "mas_cerca_arriba": _zone(min(above, key=lambda z: z.low), bid) if above else None,
        "mas_cerca_abajo": _zone(max(below, key=lambda z: z.high), bid) if below else None,
        "cercanas_6pct": [_zone(z, bid) for z in near[:8]],
        "dist_zona_mas_cercana_pct": _r((zone_distance(bid, zones) or 0) * 100, 3) if zones else None,
    }
    out["rupturas_48h"] = [_break_state(z, h1) for z in near[:6]]

    trend = {}
    for name in TFS:
        r = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{name}"), 0, 120)
        if r is None or len(r) < 21:
            continue
        closes = [float(x["close"]) for x in r]
        ema = ema_last(closes)
        trend[name] = {"ema20": _r(ema), "precio_vs_ema_pct": _r((bid / ema - 1) * 100, 2),
                       "lado": "sube" if bid > ema else "baja",
                       "ultimas_4_velas": ["verde" if x["close"] >= x["open"] else "roja" for x in r[-5:-1]]}
    out["tendencia_ema20"] = trend

    m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, 120)
    hi, lo, cl = [float(x["high"]) for x in m15], [float(x["low"]) for x in m15], [float(x["close"]) for x in m15]
    rng = [h - l for h, l in zip(hi, lo)]
    ref = sorted(rng[-(BIG_LAST + BIG_REF):-BIG_LAST])[BIG_REF // 2]
    calm = calm_block(hi, lo, cl)
    out["velas_m15"] = {"rangos_ultimas_4": [_r(x, 0) for x in rng[-BIG_LAST:]], "mediana_96_previas": _r(ref, 0),
                        "limite_vela_grande": _r(BIG_MULT * ref, 0),
                        "movimiento_4h_pct": _r((cl[-1] / cl[-1 - RUN_BARS] - 1) * 100, 2),
                        "bloqueo": calm or "tranquilo"}

    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 5000)
    today = d1[-1]
    out["dia_hoy"] = {"apertura": _r(today["open"]), "maximo": _r(today["high"]), "minimo": _r(today["low"]),
                      "cambio_desde_apertura_pct": _r((bid / float(today["open"]) - 1) * 100, 2),
                      "rango_pct": _r((float(today["high"]) / float(today["low"]) - 1) * 100, 2)}
    out["ath"] = {"dd_from_ath_pct": _r((drawdown_from_ath([float(x["high"]) for x in d1], bid) or 0) * 100, 2),
                  "nota": "solo dato, ninguna regla lo usa"}

    ema_prev = ema_last([float(x["close"]) for x in d1[-121:-1]])
    side = trend_side(bid, ema_prev)
    lado = "buy" if side == 1 else "sell" if side == -1 else None
    lat = entry_block(LateralState(), now, zone_distance(bid, find_zones(h1)), calm)
    out["bots"] = {
        "ema20_d1_ayer": _r(ema_prev), "lado_tendencia_d1": lado,
        "tp_chico_c1": f"al cambio de hora abriria {lado} (+1/-10 USD)",
        "tp_chico_lateral": f"abriria {lado}" if not lat else f"no entra: {lat}",
        "regla_lateral": f"zona <= {ZONE_MAX * 100:.0f}%, sin vela > {BIG_MULT}x mediana, sin 1% en 4h, "
                         "ni lunes ni 00-05 UTC",
    }
    return out


def order_numbers(args, bid: float, ask: float, contract: float, positions: list) -> dict:
    sell = args.lado == "sell"
    price = args.precio or (bid if sell else ask)
    per_price = args.lote * contract  # USD per 1.0 of price
    o: dict = {"lado": args.lado, "lote": args.lote, "entrada": price,
               "tipo": "pendiente" if args.precio else "a mercado"}
    if args.tp:
        tp_d = (price - args.tp) if sell else (args.tp - price)
        o["tp_distancia_precio"] = _r(tp_d)
        o["usd_si_toca_tp"] = _r(tp_d * per_price)
    if args.sl:
        sl_d = (args.sl - price) if sell else (price - args.sl)
        o["sl_distancia_precio"] = _r(sl_d)
        o["usd_si_toca_sl"] = _r(-sl_d * per_price)
        o["sl_pct_del_precio"] = _r(sl_d / price * 100, 2)
    else:
        o["usd_si_toca_sl"] = None
        o["alerta"] = "sin SL"
    if args.tp and args.sl and tp_d > 0 and sl_d > 0:
        o["acierto_necesario_pct"] = _r(sl_d / (sl_d + tp_d) * 100, 1)
        o["nota_acierto"] = "es lo que da la moneda con ese TP y SL; hay que superarlo para ganar"
    same = [p for p in positions if (p["lado"] == "sell") == sell]
    if same:
        o["mismo_lado_ya_abiertas"] = len(same)
        o["riesgo_sl_juntas_usd"] = _r(sum(p["usd_si_toca_sl"] or 0 for p in same) + (o["usd_si_toca_sl"] or 0))
    return o


def account(mt5, symbol: str) -> dict:
    a = mt5.account_info()
    tick = mt5.symbol_info_tick(symbol)
    contract = float(mt5.symbol_info(symbol).trade_contract_size or 1.0)
    pos = []
    for p in mt5.positions_get() or ():
        sell = int(p.type) == 1
        usd_sl = None
        if p.sl:
            usd_sl = _r(-((p.sl - p.price_open) if sell else (p.price_open - p.sl)) * p.volume * contract)
        pos.append({"ticket": p.ticket, "simbolo": p.symbol, "lado": "sell" if sell else "buy", "lote": p.volume,
                    "entrada": p.price_open, "sl": p.sl or None, "tp": p.tp or None, "flotante": _r(p.profit),
                    "swap": _r(p.swap), "usd_si_toca_sl": usd_sl})
    kinds = {2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop"}
    orders = [{"ticket": o.ticket, "tipo": kinds.get(int(o.type), int(o.type)),"precio": o.price_open, "sl": o.sl or None, "tp": o.tp or None,
               "lote": o.volume_current} for o in mt5.orders_get() or ()]
    net_lots = sum((-p["lote"] if p["lado"] == "sell" else p["lote"]) for p in pos if p["simbolo"] == symbol)
    liq = None
    if net_lots:
        liq = _r(float(tick.bid) - float(a.equity) / (net_lots * contract))  # equity 0 (stop out 0%)
    return {"servidor": a.server, "real": int(a.trade_mode) == 2, "balance": _r(a.balance), "equity": _r(a.equity),
            "margen": _r(a.margin), "margen_libre": _r(a.margin_free), "apalancamiento": a.leverage,
            "posiciones": pos, "ordenes_pendientes": orders, "lote_neto_btc": _r(net_lots, 2),
            "precio_equity_cero": liq, "_contract": contract, "_bid": float(tick.bid), "_ask": float(tick.ask)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lado", choices=("buy", "sell"))
    ap.add_argument("--lote", type=float, default=0.01)
    ap.add_argument("--precio", type=float, help="precio de una orden pendiente; sin esto, a mercado")
    ap.add_argument("--sl", type=float)
    ap.add_argument("--tp", type=float)
    ap.add_argument("--cuenta", action="store_true", help="leer (solo leer) la cuenta del terminal real")
    args = ap.parse_args()

    _load_dotenv()
    import MetaTrader5 as mt5

    symbol = _env("BOT_IMPULSO_SYMBOL", "BTCUSDm")
    err = _connect(mt5)
    if err:
        print(json.dumps({"error": err}, ensure_ascii=False))
        return 1
    mt5.symbol_select(symbol, True)
    out = {"mercado": market(mt5, symbol)}
    contract = float(mt5.symbol_info(symbol).trade_contract_size or 1.0)
    mt5.shutdown()

    positions: list = []
    if args.cuenta:
        if mt5.initialize(path=_env("MT5_REAL_PATH", REAL_PATH_DEFAULT)):
            mt5.symbol_select(symbol, True)
            acc = account(mt5, symbol)
            mt5.shutdown()
            contract = acc.pop("_contract")
            acc.pop("_bid"), acc.pop("_ask")
            positions = [p for p in acc["posiciones"] if p["simbolo"] == symbol]
            out["cuenta"] = acc
        else:
            out["cuenta"] = {"error": f"no pude leer el terminal real: {mt5.last_error()}"}
    if args.lado:
        m = out["mercado"]
        out["orden"] = order_numbers(args, m["bid"], m["ask"], contract, positions)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
