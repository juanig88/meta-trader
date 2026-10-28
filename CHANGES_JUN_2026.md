# Cambios estrategia bot — Junio 2026 (experimento R:R + Chandelier)

> Histórico. El live actual (desde 2026-10-07) es el swing diario Donchian 20/10 + SMA200: `trading/docs/SWING_ENGINE.md`. La rotación crypto quedó apagada (`trading/docs/ROTATION_ENGINE.md`).

**Diagnóstico previo:** 94 trades reales en mayo (12-may → 1-jun) dieron:
- Win rate 51% (bueno), pero **profit factor 0.25** (desastroso)
- Avg win **$0.17** vs avg loss **-$0.71** → R:R **1:4 en contra**
- Net -$24.43, max DD -$25.43
- Las entradas NO son el problema (82% alineadas con EMA, igual que los ganadores). El problema es la **gestión de salidas**: la escalera USD obligaba `tp=None` y movía el SL a BE/lock con muy poco margen → cortaba ganadores, dejaba correr perdedores.

## Resumen de cambios

### 1) R:R asimétrico (ATR SL/TP con TP real)
Antes: SL inicial $0.80, TP=None (ladder lo borraba). SL distancia ≈ 0.10-0.17% del precio, dentro del ruido normal M15.
Ahora: SL = 2.0×ATR(M15), TP = 3.5×ATR(M15). Ambos REALES en el bróker.
**Esperado:** SL típico en BTC ~$400 (vs $80 anterior), TP ~$700 → R:R ≈ 1.75:1 a favor.

### 2) Profit-securing nueva (ATR profit trail, Chandelier-style)
Antes: escalera USD (`BOT_USD_SL_LADDER_ENABLED=1`) + watcher paralelo. Movía SL a BE en cuanto profit > $0.50, después a +$1, +$2 etc. → cortaba ganadores con cualquier retroceso.
Ahora (`BOT_ATR_PROFIT_TRAIL_ENABLED=1`):
- **Fase 1**: el SL inicial NO se toca hasta que `profit >= 1.5 × riesgo_inicial_en_money`. La posición respira.
- **Fase 2**: una vez activada, trail SL a `precio ± 2.5 × ATR(M15)`. Monotónico (solo se acerca).
- TP del entry queda intacto.

### 3) Cap de lote + daily kill switch
- `BOT_MAX_LOT_PER_TRADE=0.03` (antes: sin cap → ETH llegó a 0.24)
- `BOT_DAILY_LOSS_LIMIT=-5.0` (antes: -60.0 efectivamente off)
- `BOT_RISK_PER_TRADE_PCT=2` (antes: 3 global, 4 en BTC/ETH overrides)
- `BOT_MAX_OPEN_TRADES=4` (antes: 5)

### 4) Bloqueo horario (REVERTIDO 12-jun a pedido del usuario: BTC/ETH 24/7)
- `BOT_WEEKEND_TRADE_SYMBOLS=BTCUSD,ETHUSD` (vuelto al original) → crypto opera weekends
- `BOT_QUIET_SESSION_HOURS=0,1,2,3,4,5` (madrugada local ART; antes era 22-04 que en local interpretaba mal)
- `BOT_QUIET_SESSION_EXEMPT_SYMBOLS=BTCUSD,ETHUSD` (vuelto al original) → crypto exempto del quiet
- **Heads-up**: en mayo perdimos $14.55 en weekends. Con BTC/ETH 24/7, hay que monitorear si la nueva lógica ATR maneja mejor el chop de weekend. Si vuelve a perder en weekends, considerar bloquearlos otra vez.

**Bug encontrado y entendido** (que motivó el revert):
- El gate `_session_allows_new_trade` (bot_mt5.py:1410) con `BOT_TRADING_WINDOW_ENABLED=1` y sesiones vacías, cae al fallback `start_hour=11, end_hour=16` que usa **hora local del bot (Argentina GMT-3), NO UTC**.
- Cuando vacié `BOT_WEEKEND_TRADE_SYMBOLS`, BTC/ETH dejaron de bypassar la ventana y fueron bloqueados todos los días fuera de 11-16 ART. Por eso aparecían logs "No symbols allowed for new entries" hasta a media tarde.
- Restaurar `weekend_trade_symbols=BTCUSD,ETHUSD` los hace 24/7 (incluyendo weekend, dado el bypass en la línea 1444 del código).

### 5) Símbolos: crypto-only durante el experimento
- `BOT_SYMBOLS=BTCUSD,ETHUSD` (sin NVDA, MELI, TSM)
- `BOT_PORTFOLIO_WEIGHTS={"BTCUSD":0.5,"ETHUSD":0.5}` (50/50)
- Las configs de stocks en `BOT_SYMBOL_CONFIG` quedan intactas pero inertes (no están en BOT_SYMBOLS).

### 6) Watcher paralelo
**No correr `watcher_ladder.py` durante el experimento.** Solo invocaba `_apply_usd_sl_ladder` que ahora está desactivado. Si se corre, no hace nada (la ladder global está OFF), pero mejor no levantarlo para evitar logs ruidosos.

### 7) Filtros legacy de la escalera (desactivados)
- `BOT_TIGHT_TRAIL_ENABLED=0` (reemplazado por ATR profit trail)
- `BOT_MICRO_BE_TRIGGER_USD=0`, `BOT_HWM_TRAIL_PCT=0`, `BOT_STAGNANT_TIMEOUT_MINUTES=0` (todos off)

## Archivos modificados

- **`bot_mt5.py`**:
  - Líneas ~261-273: nuevos campos en `Config` (`atr_profit_trail_*`)
  - Línea ~4242: nueva función `_money_at_distance`
  - Línea ~4257: nueva función `_apply_atr_profit_trail`
  - En `manage_positions` (~línea 4470): nuevo gate que llama a la función nueva cuando `atr_profit_trail_enabled=1` y ladder=0
  - En `main()` (~línea 4866): parseo de las 4 env vars nuevas
- **`.env`**: cambios listados arriba (BOT_SYMBOLS, weights, risk, lot cap, daily limit, quiet/weekend, ATR mults, USD ladder OFF, ATR profit trail ON, BOT_SYMBOL_CONFIG para BTC/ETH)

## Cómo correr el experimento

```powershell
cd C:\Users\juani\Projects\meta-trader
py .\bot_mt5.py
```

**No correr** `watcher_ladder.py` esta semana.

## Qué medir (1 semana)

Datos a recolectar al final:
1. `ReportHistory-52848914.html` actualizado (nuevo del período)
2. `trades.log` del período
3. CSVs BTC/ETH M1+M5+M15+H4 actualizados

Métricas objetivo vs baseline:
| Métrica | Baseline (mayo) | Objetivo |
|---|---|---|
| Profit factor | 0.25 | > 1.0 |
| Avg win | $0.17 | $0.80+ |
| Avg loss | -$0.71 | -$0.60 a -$0.80 |
| Winrate | 51% | 40-55% (puede bajar) |
| Net 1 sem | proporcional -$8/sem | > 0 |

**Nota importante**: el winrate puede bajar (con TP más lejos hay más trades que no llegan). Lo que importa es que los wins sean grandes. Profit factor > 1 es la métrica clave.

## Rollback

Si la cosa va peor en 3-4 días:
1. En `.env`: `BOT_USD_SL_LADDER_ENABLED=1` y `BOT_ATR_PROFIT_TRAIL_ENABLED=0`
2. Volver `BOT_SL_ATR_MULT=1.5`, `BOT_TP_ATR_MULT=4.0`
3. Volver `BOT_SYMBOL_CONFIG` BTC/ETH a `risk_per_trade_pct:4`, `sl_atr_mult:1.5`, `tp_atr_mult:4.0`
4. Reiniciar el bot
