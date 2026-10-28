"""Chequeo previo y diario de operaciones manuales en la demo de Exness.

Solo lee la terminal MT5: nunca manda órdenes. La operación la abrís vos en MT5.

    python chequeo_previo.py abrir --lado BUY --stop 80300 --tp 81000 --idea "rebote en el piso de H4"
    python chequeo_previo.py cerrar            # busca en MT5 el resultado de la última abierta
    python chequeo_previo.py resumen

Opciones de abrir: --simbolo (BTCUSDm), --entrada (por defecto el ask o el bid actual),
--riesgo-usd (10), --invalida "qué tendría que pasar para que la idea deje de valer".
Escribe en DIARIO.md (para leer) y diario_operaciones.jsonl (para medir).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from trading.domain.demo_gate import TRADE_MODE_REAL
from trading.domain.pretrade import (
    Context, Plan, breakeven_winrate, reasons_against, reward_risk, size_lots, trend, validate,
)

DIARIO_MD = Path("DIARIO.md")
DIARIO_JSONL = Path("diario_operaciones.jsonl")
MOTIVOS = {"p": "plan", "f": "fomo", "v": "venganza", "a": "aburrimiento"}
REVENGE_WINDOW_MIN = 60


def main() -> int:
    # La consola de Windows usa cp1252 y no tiene "−"; sin esto el chequeo se corta a la mitad.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Chequeo previo y diario. No manda órdenes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("abrir")
    a.add_argument("--simbolo", default="BTCUSDm")
    a.add_argument("--lado", required=True, type=str.upper, choices=("BUY", "SELL"))
    a.add_argument("--stop", required=True, type=float)
    a.add_argument("--tp", required=True, type=float)
    a.add_argument("--entrada", type=float)
    a.add_argument("--riesgo-usd", type=float, default=10.0)
    a.add_argument("--idea", required=True)
    a.add_argument("--invalida", default="")
    c = sub.add_parser("cerrar")
    c.add_argument("--id", type=int)
    sub.add_parser("resumen")
    args = ap.parse_args()
    if args.cmd == "resumen":
        return resumen()
    mt5 = _connect()
    if mt5 is None:
        return 2
    try:
        return abrir(mt5, args) if args.cmd == "abrir" else cerrar(mt5, args.id)
    finally:
        mt5.shutdown()


def _connect():
    import MetaTrader5 as mt5

    if not mt5.initialize():
        print(f"No se pudo conectar a MT5: {mt5.last_error()}")
        return None
    acc = mt5.account_info()
    if acc is None:
        print("MT5 sin cuenta logueada.")
        mt5.shutdown()
        return None
    if int(acc.trade_mode) == TRADE_MODE_REAL:
        print("AVISO: la cuenta conectada es REAL. Este diario está pensado para la demo.")
    return mt5


def abrir(mt5, args) -> int:
    sym = args.simbolo
    mt5.symbol_select(sym, True)
    info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        print(f"No encuentro {sym} en la terminal.")
        return 2
    entry = args.entrada or (float(tick.ask) if args.lado == "BUY" else float(tick.bid))
    plan = Plan(args.lado, entry, args.stop, args.tp)
    err = validate(plan)
    if err:
        print(f"Plan inválido: {err}.")
        return 2

    ctx = _context(mt5, sym, info, tick)
    lots, real_risk = size_lots(risk_usd=args.riesgo_usd, plan=plan, ctx=ctx)
    rr = reward_risk(plan)
    reward_usd = abs(plan.take_profit - entry) * ctx.usd_per_price_unit * lots
    spread_usd = ctx.spread_price * ctx.usd_per_price_unit * lots
    against = reasons_against(plan=plan, ctx=ctx, lots=lots, risk_usd=args.riesgo_usd, real_risk_usd=real_risk)
    revenge = _recent_loss()
    if revenge:
        against.insert(0, f"Cerraste una pérdida hace {revenge} min. ¿Es una operación de venganza?")

    print(f"\n== Chequeo previo {sym} {plan.side} ==")
    print(f"Entrada {entry:,.2f} | stop {plan.stop:,.2f} | take profit {plan.take_profit:,.2f}")
    print(f"Tamaño: {lots} lotes = {real_risk:.2f} USD de riesgo (límite {args.riesgo_usd:.2f})")
    print(f"  fórmula: riesgo / (|entrada − stop| × USD por unidad de precio por lote) = "
          f"{args.riesgo_usd:.2f} / ({abs(entry - plan.stop):,.2f} × {ctx.usd_per_price_unit:g})")
    print(f"R:R {rr:.2f} a 1 → si sale bien +{reward_usd:.2f} USD, si sale mal −{real_risk:.2f} USD, "
          f"spread {spread_usd:.2f} USD")
    print(f"Sin ventaja en la entrada, este R:R acierta ~{breakeven_winrate(rr):.0%} de las veces y queda en cero.")
    sma = f"{ctx.sma200_d1:,.2f}" if ctx.sma200_d1 else "sin datos"
    print(f"Media de 200 días: {sma} | tendencias: " + ", ".join(f"{k} {v}" for k, v in ctx.trends.items()))
    print("\nEscenarios:")
    print(f"  {'alcista' if plan.side == 'BUY' else 'bajista'}: toca el take profit, +{reward_usd:.2f} USD")
    print(f"  {'bajista' if plan.side == 'BUY' else 'alcista'}: toca el stop, −{real_risk:.2f} USD. No agregues a la posición.")
    print("  lateral: no toca ninguno. Decidí ahora cuánto tiempo le das.")
    print("\nRazones para NO entrar:")
    for r in against or ["Ninguna de las medibles. Igual puede salir mal: el chequeo no predice la dirección."]:
        print(f"  - {r}")

    motivo = MOTIVOS.get(_ask("\n¿Por qué entrás ahora? [p]lan / [f]omo / [v]enganza / [a]burrimiento: ", "p")[:1].lower(), "plan")
    if motivo != "plan":
        print(f"  Dijiste {motivo}. Tudor Jones: primero proteger lo que tenés. Mejor no entrar.")
    duda = _ask("¿Qué te hace dudar? (enter para nada): ", "")
    invalida = args.invalida or _ask("¿Qué tendría que pasar para que la idea deje de valer? (enter = tocar el stop): ", "toca el stop")
    decision = _ask("¿Entrás? [s/n]: ", "n").lower().startswith("s")

    row = {
        "id": _next_id(), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "simbolo": sym,
        "lado": plan.side, "entrada": entry, "stop": plan.stop, "tp": plan.take_profit, "lotes": lots,
        "riesgo_usd": round(real_risk, 2), "objetivo_usd": round(reward_usd, 2), "rr": round(rr, 2),
        "spread_usd": round(spread_usd, 2), "sma200": ctx.sma200_d1, "tendencias": ctx.trends,
        "razones_en_contra": against, "motivo": motivo, "idea": args.idea, "duda": duda,
        "invalida": invalida, "entra": decision, "estado": "abierta" if decision else "descartada",
    }
    _append(row)
    print(f"\nAnotado como #{row['id']} en {DIARIO_MD} ({row['estado']}).")
    if decision:
        print(f"Abrilo vos en MT5: {plan.side} {lots} {sym}, SL {plan.stop}, TP {plan.take_profit}. "
              "Después: python chequeo_previo.py cerrar")
    return 0


def _context(mt5, sym, info, tick) -> Context:
    usd = float(info.trade_tick_value) / float(info.trade_tick_size)
    d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 200)
    sma = float(np.mean(d1["close"])) if d1 is not None and len(d1) == 200 else None
    trends = {}
    for name, tf in (("D1", mt5.TIMEFRAME_D1), ("H4", mt5.TIMEFRAME_H4), ("H1", mt5.TIMEFRAME_H1)):
        r = mt5.copy_rates_from_pos(sym, tf, 1, 60)
        trends[name] = trend(list(r["close"])) if r is not None else "sin_datos"
    m1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 3 * 1440)
    typical = last = None
    if m1 is not None and len(m1) > 30:
        c = m1["close"].astype(float)
        typical = float(np.median(np.abs(c[15:] - c[:-15])))
        last = float(c[-1] - c[-16])
    return Context(usd_per_price_unit=usd, volume_min=float(info.volume_min), volume_step=float(info.volume_step),
                   spread_price=float(tick.ask) - float(tick.bid), sma200_d1=sma, trends=trends,
                   typical_move_15m=typical, last_move_15m=last)


def cerrar(mt5, trade_id: Optional[int]) -> int:
    rows = _rows()
    open_rows = [r for r in rows if r.get("estado") == "abierta" and (trade_id is None or r["id"] == trade_id)]
    if not open_rows:
        print("No hay operaciones abiertas en el diario.")
        return 1
    row = open_rows[-1]
    found = _find_position(mt5, row)
    if found is None:
        print(f"No encontré en MT5 una posición {row['lado']} {row['simbolo']} abierta después del chequeo #{row['id']}.")
        return 1
    if not found["cerrada"]:
        print(f"La posición {found['position_id']} sigue abierta. Volvé a correr cerrar cuando termine.")
        return 0
    net = found["neto_usd"]
    r_mult = net / row["riesgo_usd"] if row["riesgo_usd"] else None
    print(f"\n#{row['id']} cerrada: neto {net:+.2f} USD ({r_mult:+.2f} R), salida {found['salida']:,.2f}, "
          f"{found['minutos']} min.")
    print(f"Lo que esperabas: +{row['objetivo_usd']:.2f} si salía bien, −{row['riesgo_usd']:.2f} si salía mal.")
    siguio = _ask("¿Seguiste el plan (mismo stop, mismo take profit, mismo tamaño)? [s/n]: ", "n").lower().startswith("s")
    buena = _ask("Más allá del resultado, ¿fue una buena operación? [s/n]: ", "n").lower().startswith("s")
    leccion = _ask("Una lección en una línea: ", "")
    row.update(estado="cerrada", neto_usd=round(net, 2), r=round(r_mult, 2) if r_mult is not None else None,
               salida=found["salida"], minutos=found["minutos"], position_id=found["position_id"],
               siguio_plan=siguio, buena_operacion=buena, leccion=leccion,
               cerrada_ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _rewrite(rows)
    print(f"Anotado en {DIARIO_MD}.")
    return 0


def _find_position(mt5, row) -> Optional[dict[str, Any]]:
    since = datetime.fromisoformat(row["ts"]) - timedelta(minutes=2)
    deals = mt5.history_deals_get(since, datetime.now(timezone.utc) + timedelta(days=1)) or ()
    want_type = 0 if row["lado"] == "BUY" else 1
    ins = [d for d in deals if d.symbol == row["simbolo"] and int(d.entry) == 0 and int(d.type) == want_type]
    if not ins:
        positions = [p for p in (mt5.positions_get(symbol=row["simbolo"]) or ())
                     if int(p.type) == want_type and p.time >= since.timestamp()]
        return {"position_id": int(positions[0].ticket), "cerrada": False} if positions else None
    pid = int(sorted(ins, key=lambda d: d.time)[0].position_id)
    mine = [d for d in (mt5.history_deals_get(position=pid) or ())]
    outs = [d for d in mine if int(d.entry) in (1, 3)]
    if not outs:
        return {"position_id": pid, "cerrada": False}
    net = sum(float(d.profit) + float(d.swap) + float(d.commission) + float(getattr(d, "fee", 0.0)) for d in mine)
    t_in = min(d.time for d in mine if int(d.entry) == 0)
    last = max(outs, key=lambda d: d.time)
    return {"position_id": pid, "cerrada": True, "neto_usd": net, "salida": float(last.price),
            "minutos": int((last.time - t_in) // 60)}


def resumen() -> int:
    rows = _rows()
    closed = [r for r in rows if r.get("estado") == "cerrada"]
    print(f"Chequeos: {len(rows)} | entradas: {sum(1 for r in rows if r.get('entra'))} | "
          f"descartadas: {sum(1 for r in rows if r.get('estado') == 'descartada')} | cerradas: {len(closed)}")
    if not closed:
        return 0
    net = [r["neto_usd"] for r in closed]
    wins = [x for x in net if x > 0]
    losses = [-x for x in net if x < 0]
    print(f"Neto {sum(net):+.2f} USD | win rate {len(wins) / len(net):.0%} | "
          f"ganancia media {np.mean(wins) if wins else 0:.2f} | pérdida media {np.mean(losses) if losses else 0:.2f} | "
          f"R medio {np.mean([r['r'] for r in closed if r.get('r') is not None]):+.2f}")
    followed = [r for r in closed if r.get("siguio_plan")]
    print(f"Seguiste el plan en {len(followed)} de {len(closed)}.")
    if len(closed) < 30:
        print("Con menos de 30 operaciones cerradas esto es ruido: no saques conclusiones todavía.")
    return 0


def _recent_loss() -> Optional[int]:
    now = datetime.now(timezone.utc)
    for r in reversed(_rows()):
        if r.get("estado") == "cerrada" and r.get("neto_usd", 0) < 0 and r.get("cerrada_ts"):
            mins = int((now - datetime.fromisoformat(r["cerrada_ts"])).total_seconds() // 60)
            return mins if mins <= REVENGE_WINDOW_MIN else None
    return None


def _ask(prompt: str, default: str) -> str:
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def _rows() -> list[dict[str, Any]]:
    if not DIARIO_JSONL.exists():
        return []
    return [json.loads(line) for line in DIARIO_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]


def _next_id() -> int:
    return max((r["id"] for r in _rows()), default=0) + 1


def _append(row: dict[str, Any]) -> None:
    rows = _rows() + [row]
    _rewrite(rows)


def _rewrite(rows: list[dict[str, Any]]) -> None:
    DIARIO_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    DIARIO_MD.write_text(_render(rows), encoding="utf-8")


def _render(rows: list[dict[str, Any]]) -> str:
    out = ["# Diario de operaciones (demo Exness)", "",
           "Generado por `chequeo_previo.py`. No editar a mano: la fuente es `diario_operaciones.jsonl`.", ""]
    for r in reversed(rows):
        out.append(f"## #{r['id']} · {r['ts'][:16].replace('T', ' ')} UTC · {r['simbolo']} {r['lado']} · {r['estado']}")
        out.append("")
        out.append(f"- **Idea:** {r['idea']}")
        out.append(f"- **Plan:** entrada {r['entrada']:,.2f}, stop {r['stop']:,.2f}, take profit {r['tp']:,.2f}, "
                   f"{r['lotes']} lotes")
        out.append(f"- **Riesgo / objetivo:** −{r['riesgo_usd']:.2f} / +{r['objetivo_usd']:.2f} USD "
                   f"(R:R {r['rr']:.2f}), spread {r['spread_usd']:.2f}")
        out.append(f"- **Contexto:** SMA200 {r['sma200']:,.2f} · " if r.get("sma200") else "- **Contexto:** SMA200 sin datos · ")
        out[-1] += ", ".join(f"{k} {v}" for k, v in (r.get("tendencias") or {}).items())
        out.append(f"- **Motivo:** {r['motivo']}" + (f" · **Duda:** {r['duda']}" if r.get("duda") else ""))
        out.append(f"- **Invalida:** {r['invalida']}")
        if r.get("razones_en_contra"):
            out.append("- **Razones en contra:**")
            out.extend(f"  - {x}" for x in r["razones_en_contra"])
        if r.get("estado") == "cerrada":
            out.append(f"- **Resultado:** {r['neto_usd']:+.2f} USD ({r['r']:+.2f} R), salida {r['salida']:,.2f}, "
                       f"{r['minutos']} min · plan {'seguido' if r['siguio_plan'] else 'NO seguido'} · "
                       f"{'buena' if r['buena_operacion'] else 'mala'} operación")
            if r.get("leccion"):
                out.append(f"- **Lección:** {r['leccion']}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
