# Motor de rotación crypto (evidencia operativa)

> **APAGADO desde 2026-10-07 17:24 UTC.** Decisión del usuario: la rotación no gana (bruto ≈ 0 antes del spread, ver `ESTABLISHED_FACTS.md`). `bot_mt5.py` detenido con la cuenta plana y `kill_switch.halt` (reason `rotacion_reemplazada_por_swing`) para que no abra si se reinicia. El motor vivo en la demo es el swing diario: `trading/docs/SWING_ENGINE.md`. Este documento queda como registro.

Documento para **iterar**. Cursor debe leerlo al inicio de cada conversación de este repo (`AGENTS.md` + regla `.cursor/rules/rotation-engine.mdc`).

Fecha de corte: **2026-10-01**. Cuenta: **demo XBTFX** (`PAPER_DEMO_LIVE` + `BOT_DRY_RUN=0` + `ACCOUNT_TRADE_MODE_DEMO`). **Nunca dinero real.**

Si cambiás el motor, actualizá **este archivo en el mismo PR/cambio**. Es la memoria del bot.

---

## Qué está vivo ahora (no el bot H4/XAU)

La rotación es el **único motor**: `main()` la fija en ON y `BOT_ROTATION_ENABLED` ya no la apaga (solo la lee `data_layer_config` para el universo de los daemons). El 2026-10-06 se quitaron del código el loop H4/M15 (`strategy_core.py`), sus filtros (rango, vela, EMA, ADX), el EV gate / quarter-Kelly del loop principal, el detector de régimen (`market_regime.py`, overlay de `BOT_SYMBOL_CONFIG`, lote por régimen), los `EVAL_*` y el XAU paralelo (magic 260415), junto con las páginas de backtest/forward del dashboard. Más tarde ese día se quitaron también la escalera USD (y su recovery al arrancar), el ATR profit trail, el profit lock, el tight trail, el camino viejo de `open_trade` (lote por riesgo %, SL/TP ATR, anti-chase, dump-lock, grupos de correlación) y los módulos de dominio que solo usaban ellos. `open_trade` tiene un solo camino (rotación). El Kelly de `ensemble.py` (tope 0.50 USD) no se tocó.

Cada ~15s el bot:

1. Escanea el universo de **79 CFD crypto** XBTFX (`trading/domain/crypto_universe.py`, sufijo `!`).
2. En cada símbolo vota **5 módulos** sobre velas **M1, M5 y M15**. Extrae **M15 ~192** (~48 h) y **H4 ~72** (~12 d) para el snapshot Murphy. Clímax unbroken (`would_veto`) **sí veta** el envío (`murphy_climax`).
3. Si **4/5** coinciden en BUY o SELL **y** pasan los vetos HTF/pullback/RSI-rango **y** no es `rank_tail` / `murphy_climax`, intenta **una** orden (un slot por símbolo). El lado enviado es el consenso (sin invert). Votos, vetos y size no cambian. Logs: `side_decision` / `side_exec` iguales; `side_inverted=false`. El invert BUY↔SELL del 2026-10-01 14:36 se revirtió el 2026-10-03. Universo sigue en **79**; la cola `rank_tail` (IMX/VEC/TRX/ZRX/RSR/AXS) se escanea y se skipea.
4. Tope **60 aperturas / hora rodante** contadas con `DEAL_ENTRY_IN` del bróker (sobrevive un restart). Hasta el 2026-10-06 el contador devolvía siempre 0 (`DEAL_ENTRY_IN` vale 0 y el código hacía `entry or -1`), así que el tope no frenaba nada; con ~15 aperturas/h nunca se habría alcanzado. Corregido con `_deal_entry`.
5. SL y TP: si el contrato puede expresar **0.50 USD** dentro del **5%** del precio (BTC, ETH, AAV…), usamos esa distancia de dinero. Si $0.50 exigiría más del 5% (alts de precio chico / poco valor por tick), el stop queda en **±5%** y el lote sube hasta que ese 5% ≈ Kelly. CFD caro al `volume_min`: el 5% en USD supera 0.50, así que el stop se achica al money-stop. El repair **no** vuelve a ensanchar a 5% (eso dejó el BTC inalcanzable).
6. Gate de spread (después del size, antes de `order_send`): skip si el bid/ask **cubre el stop** (`spread_cubre_stop`) o si el agujero en USD es **> 10%** del riesgo (`spread_caro`, env `BOT_ROTATION_MAX_SPREAD_USD_FRAC`). No se usa el cap de 25 puntos (en crypto XBTFX es ruido). No se ensancha el SL a `1.2×spread`.
7. Tras **5 minutos**: cierra take si `profit > 0.01`, loss si `profit < -0.0001`, y la banda del medio (`stale_time`). Time-stop de scalp; MTM pre-invert 2026-10-03: EV −0.47¢ a +5m vs −1.8¢ a +60m.
8. Harvest del libro: si hay **≥ 2** tickets del magic, el más joven lleva **≥ 10 min** (tiempo de open del ticket) y el neto flotante (profit+swap+comm) es **≥ 0.50 USD**, cierra **todas**. Libera slots para un 4/5 nuevo. `BOT_ROTATION_BOOK_HARVEST_USD=0` lo apaga.
9. Kill switch (2026-10-03): si existe `kill_switch.halt` (junto a `bot_state.json`), modo `MANAGE_ONLY_KILL_SWITCH`: no abre, sigue gestionando stale/SL. `open_trade` también lo chequea (reject `kill_switch_halt`), así ningún camino lo saltea. Un halt **ilegible** bloquea igual (fail-closed). Con `BOT_KILL_SWITCH_FLATTEN=1` (default del código **0**; **live en `.env` = 1 desde 2026-10-05**) y PnL del día ≤ `BOT_DAILY_LOSS_LIMIT`, cierra **una vez** todos los tickets gestionados del magic (comment `kill_switch`, respeta excepciones manuales), escribe el halt y reintenta solo los cierres que fallaron. Clear manual: borrar `kill_switch.halt` o crear `kill_switch.clear`. `BOT_KILL_SWITCH_ARMED=0` ignora el halt mientras siga en 0 y nunca aplana. Ningún modelo limpia el halt. Si se limpia con el día todavía bajo el límite y flatten=1, vuelve a disparar.

Magic: **260414**. Filling: IOC/FOK. Comentario de apertura: `rot_4_5`. Cierres stale: `rot_stale_loss` / `rot_stale_take` / `rot_stale_time`. Harvest del libro: `rot_book_harvest`.

---

## Por qué abrimos

Solo si **todas** estas cosas son ciertas:

| Condición | Dónde |
|-----------|--------|
| 4/5 módulos del mismo lado | `trading/domain/ensemble.py` `MIN_AGREE=4` |
| Al enviar, el lado es el consenso | `open_trade(side_decision)` (invert off 2026-10-03) |
| HTF M15 no va contra el lado | skip `veto_htf` |
| Último M1 tocó EMA9/20 (no clímax) | skip `sin_pullback` |
| RSI≥70 BUY / RSI≤30 SELL solo si HTF es rango | skip `rsi_rango` |
| El símbolo no está en la cola ranking (IMX/VEC/TRX/ZRX/RSR/AXS) | skip `rank_tail` |
| Murphy no es clímax unbroken (`would_veto`) | skip `murphy_climax` |
| No hay ya una posición de este magic en ese símbolo | `apply_account_gates` → `ya_abierto` |
| Aperturas ENTRY_IN última hora &lt; 60 | `cuota_hora` |
| Demo XBTFX + `PAPER_DEMO_LIVE` + no dry | `trading/domain/demo_gate.py` |
| Lote 5% se puede calcular y el SL queda legal vs bid/ask | `open_trade` + `rotation_stops` |
| Spread no cubre el stop y hole USD ≤ 10% del riesgo | `rotation_spread_skip_reason` |
| Hay velas M1/M5/M15 (M15 ≥ 50 cerradas) | si no: `sin_barras`. H4 extra es opcional (Murphy `h4.ok=false` si faltan) |

`skip_reason` en logs:

- `sin_consenso` — no llegó a 4/5
- `veto_htf` — 4/5 pero M15 EMA50 va contra el lado
- `sin_pullback` — 4/5 pero el último M1 no retrocedió a EMA9/20 (clímax)
- `rsi_rango` — HTF plano y RSI extremo contra el lado (≥70 BUY / ≤30 SELL)
- `rank_tail` — 4/5 en IMXUSD! / VECUSD! (WR stale &lt; 10%, n≥20 desde 2026-10-01) o TRXUSD! / ZRXUSD! / RSRUSD! / AXSUSD! (cero ganadores netos del bróker 2026-09-30→10-06)
- `murphy_climax` — 4/5 con `murphy.would_veto` (climax_unbroken en M15 o H4)
- `ya_abierto` — un slot ocupado
- `cuota_hora` — 60/h
- `sin_barras` — no hubo OHLCV M1/M5/M15
- `spread_caro` — el bid/ask en USD es > 10% del riesgo (rojo inmediato demasiado grande)
- `spread_cubre_stop` — el spread es ≥ la distancia del SL (caso BTC)
- `spread_sin_stop` — no hay distancia de stop para comparar
- size reasons desde `size_lot_for_percent_stop` (contrato inválido, etc.)

**BUY/SELL del ciclo en el dashboard no es acumulado**: es el consenso de **ese** poll.

Los 5 módulos (`VOTER_ORDER`): **htf** (M15 vs EMA50), **momentum_short** (mayoría ≥3 de cuerpo M5 / racha M1 / 3 M1→4ª / momentum M1), **intermarket** (correlación BTC), **vol_bands** (squeeze+ruptura o extremo de bandas en expansión; no fade de z-score), **structure** (cruce EMA M1 + alineación M1/M5). NONE cuenta **en contra** del 4/5. `vote_m1_flow` (volumen XBTFX) **no** entra al tally. RSI no vota: solo bloquea en rango.

`m1_follow3` sigue existiendo como señal interna de `momentum_short`: solo vota al empezar la 4ª vela (3 cuerpos iguales y la anterior distinta); no es la misma pregunta que `m1_streak` (cualquier racha de 3+). No abre sola.

Kelly **solo achica** el riesgo USD, nunca lo sube por encima de `BOT_MAX_RISK_USD=0.50`.

### Snapshot Murphy (gate `murphy_climax`)

Cuando el 4/5 **dispara** (`consensus.fired`), el bot etiqueta el cierre M15 y H4 contra el **último swing confirmado** (pivote con 2 velas a cada lado; las últimas 2 velas **no** son pivote — sin lookahead). Penetración = **mín(1% del precio, 1×ATR)**, no 10% del precio.

| `tag` | Qué es | `would_keep` / `would_veto` |
|-------|--------|------------------------------|
| `pullback_38_50` | Retroceso 38–50% del tramo del último impulso | keep |
| `retest_broken` | Retest del techo/suelo **ya penetrado** | keep |
| `climax_unbroken` | Ataque al máximo/mínimo **aún no penetrado** sin pullback | **veto live** (`murphy_climax`) |
| `deep_retrace` | Retroceso > 62% | ninguno |

Si `murphy.would_veto` (climax en M15 **o** H4), el 4/5 **no se envía**. Sigue logueando `murphy` en el SKIP. Muestra 2026-10-01→03: fills con veto ~0.5¢ peores y ~2 pp menos de WR (pre-invert: −3.2¢ vs −2.2¢, WR 24% vs 29%). Corta ~40% de los 4/5; no es un edge enorme, es no perseguir clímax. No suma votantes.

Campos: `murphy.tag_m15` / `tag_h4` / `would_veto` / `would_keep`, más `m15`/`h4` con `last_high`, `retrace`, `pen`, `broken_level`. Postgres: `ensemble_votes.murphy`.

### Alpha sombra (telemetría, no gate)

En cada 4/5 el bot corre `score_bars` sobre el **mismo M15** del voto (`trading/domain/alpha_shadow.py`). Escribe `alpha.direction` / `score` / `code` (`alpha_igual` / `alpha_habria_evitado` / `alpha_contrario`) / `would_avoid` en `ENSEMBLE_VOTE` / SIGNAL / OPEN. **No skipea.**

Aparte, con rotación ON, `mt5_tick_daemon` + `alpha_engine` usan las **mismas 79** (no `BOT_SYMBOLS` BTC/ETH/JPY). Redis lleva ticks; Postgres guarda `md_bars` / `md_ticks` / `alpha_scores` cada ~30 s. `copy_ticks_from` toma `date_from` en **hora local del PC** (no UTC naive): en Windows ART eso devolvía `fetched: 0` con Success. El daemon no hace `mt5.shutdown()` al parar (el bot comparte el terminal). Sigue siendo sombra: **no entra al order_send**. Sirve para cinta continua y join asof, no para un sexto votante. `MT5_TICK_DAEMON_SYMBOLS` no vacío pisa el universo.

Postgres (fail-open): `ensemble_votes` (incluye `alpha`) + `trade_events` para SIGNAL / OPEN / CLOSE / REJECT / STALE / HARVEST. `trades.log` sigue siendo el audit.

### Ranking de símbolos (`rank_tail`)

El universo sigue en **79**. Tras el 4/5 y los vetos de módulo, los nombres de la cola salen con `rank_tail`. No se tocó el cap 60/h ni Kelly. No ampliar la lista sin otra muestra.

| Símbolo | Evidencia | Desde |
|---------|-----------|-------|
| IMXUSD! / VECUSD! | stale WR ~7% / ~6%, n≥20, `trades.log` desde 2026-10-01 | 2026-10-03 |
| TRXUSD! / ZRXUSD! / RSRUSD! / AXSUSD! | cero tickets con neto > 0 en el reporte MT5 2026-09-30→10-06 (0/32, 0/16, 0/12, 0/2). TRX/ZRX: precio bajo, el spread se come el +0.01 | 2026-10-07 |

Ojo: en ese mismo reporte IMX (9/21) y VEC (9/25) ganaron al nivel del promedio (~30%); revisar si siguen mereciendo la cola con la próxima muestra.

---

## Por qué cerramos

| Cierre | Cómo se ve | Tag `close_how` / `why_closed` |
|--------|------------|--------------------------------|
| Precio tocó SL ($0.50 o ±5%) | bróker `DEAL_REASON_SL` | `stop_loss` |
| Precio tocó TP ($0.50 o ±5%) | bróker `DEAL_REASON_TP` | `take_profit` |
| 5 min + profit &lt; −0.0001 | bot `order_send` close, comment `rot_stale_loss` | `stale_loss` |
| 5 min + profit &gt; 0.01 | comment `rot_stale_take` | `stale_take` |
| 5 min, banda [−0.0001, 0.01] | comment `rot_stale_time` | `stale_time` |
| Libro flotante ≥ 0.50, ≥2 tickets, más joven ≥ 10 min | bot cierra todas, comment `rot_book_harvest` | `book_harvest` |
| Daily loss con `BOT_KILL_SWITCH_FLATTEN=1` | bot cierra todas, comment `kill_switch` | `kill_switch` |
| Manual / otra app | `DEAL_REASON_CLIENT` | `client_manual` |
| Expert genérico | `DEAL_REASON_EXPERT` | `expert_or_bot` |
| Stop out | `DEAL_REASON_SO` | `stop_out` |

`manage_positions` solo hace harvest del libro, cierre stale a 5 min y repair del stop. El 2026-10-06 se quitaron la escalera USD y, con OK de risk-quant, filter-exit (ADX M15 + EMA en contra, comment `bot_filter_exit`), break-even (+0.50) y trailing (+1.00). Filter-exit cerró 201 tickets hasta el 2026-10-03 09:34 y ninguno desde el time-stop de 5 min; break-even y trailing no podían dispararse porque el TP (riesgo Kelly ≤ 0.50) llega antes, y el trailing hubiera movido el TP a +1.20. Los cierres viejos con `bot_filter_exit` siguen apareciendo en el historial.

---

## Dónde está el código

| Pieza | Archivo |
|-------|---------|
| 5 módulos + Kelly cap | `trading/domain/ensemble.py` |
| SL/TP 5% + lote | `trading/domain/rotation_stops.py` |
| Cierre stale 5 min | `trading/domain/rotation_stale.py` |
| Snapshot libro flotante | `trading/domain/rotation_book.py` |
| Harvest canasta ≥ 0.50 | `book_harvest_should_flatten` + `_flatten_rotation_book_if_needed` |
| Scan + gates | `trading/application/rotation_engine.py` |
| Tags de log | `trading/application/rotation_audit.py` |
| Loop MT5, order_send, manage | `bot_mt5.py` (`_run_rotation_cycle`, `open_trade`, `manage_positions`) |
| Snapshot Murphy + alpha sombra | `trading/domain/swing_structure.py`, `trading/domain/alpha_shadow.py` |
| Persistencia fail-open | `trading/infrastructure/persistence/ensemble_store.py` |
| Demo gate | `trading/domain/demo_gate.py` |
| Dashboard cuota 60 min | `flask_dashboard.py` + `bot/templates/index.html` |
| Lab 5 módulos + tabla 4/5 | `bot/templates/lab.html`, `trading/application/four_five_lab.py` |
| Snapshot numérico `state` en el 4/5 | `trading/domain/decision_state.py`, `overlay_cycle_tape` en `rotation_engine.py`, `trading/infrastructure/tick_peek.py` |
| Kill switch | `trading/domain/kill_switch.py`, `trading/infrastructure/kill_switch_file.py`, `_kill_switch_step` en `bot_mt5.py` |
| Calibración / diario / nocturno | `trading/application/calibration_report.py`, `calibration_backfill.py`, `daily_report.py`, `nightly_review.py` |

No editar el plan original del ensemble en `docs/improvement/` salvo que el usuario lo pida.

---

## Logs (fuente para quedarnos con las cryptos rentables)

Archivo: **`trades.log`** (líneas JSON dentro del log de logging). Eventos:

| `event` | Cuándo | Campos clave |
|---------|--------|----------------|
| `ENSEMBLE_VOTE` | **Cada símbolo, cada ciclo** (~15s × 79) | `symbol`, `direction`, `buy`/`sell`, `action`, `skip_reason`, `why_open`, `votes` (`nombre:LADO`); **`murphy`** y **`alpha`** si 4/5 disparó |
| `ENSEMBLE_CYCLE` | 1 por poll | `scanned`, `fired_count`, `opens_this_hour`, `hour_cap`, `execution_mode` |
| `ENSEMBLE_SIGNAL` | Consenso que intenta orden | `side` / `side_decision` / `side_exec` (mismo lado; invert off), `agree`, `lot`, `risk_usd`, `murphy`, `alpha` |
| `ENSEMBLE_DEMO_ORDER` | Tras `order_send` | `ok`, votos, `why_open` |
| `ROTATION_OPEN_REJECT` | 4/5 pero no fill | `reason` (`spread_caro`, `spread_cubre_stop`, `sl_vs_mercado`, `retcode_10016`, `lote_cero_margen`, …), `spread_usd`, `spread_vs_risk`, `stop_dist` |
| `TRADE_OPEN` | Fill | `engine=rotation`, `why_open=consenso_4_5`, `sl`, `tp`, `lot`, `spread_usd`, `spread_vs_risk`, `stop_dist`, `position_id`, votos si venía `audit`, `murphy` |
| `ROTATION_STALE_CLOSE` | Intentamos close a los 5 min | `why_closed`, `profit` |
| `ROTATION_BOOK` | Cada N s (default **60**, env `BOT_ROTATION_BOOK_SNAPSHOT_SECONDS`; `0` = off) | `floating_net` (profit+swap+comm, magic 260414), `hwm` / `trough` / `drawdown_from_hwm` (desde el arranque del proceso), `n_open`, `n_young` / `young_net` (edad &lt; 1h), `n_aged` / `aged_net`, `best_net` / `worst_net`, `min_age_seconds` / `max_age_seconds`. |
| `ROTATION_BOOK_HARVEST` | Aplanamos el libro | `floating_net`, `n_open`, `n_closed`, `min_age_seconds`, `harvest_usd` |
| `TRADE_CLOSE` | Deal OUT del bróker (SL/TP/stale/manual) | `net`, `close_how`, `why_closed`, `comment`, `duration_seconds`, `spread_usd` (agujero bid/ask **al cerrar**), `open_spread_usd` (si el proceso vio el open), `why_open`, `open_votes`, `murphy` (si el open lo tenía) |
| `KILL_SWITCH_HALT` | Primer daily loss con flatten=1 | `reason`, `pnl_today`, `daily_loss_limit` |
| `KILL_SWITCH_FLATTEN` | Cierre del libro (o reintento) | `retry`, `n_targets`, `n_closed`, `n_failed`, `n_manual_skipped`, `flatten_pending` |
| `KILL_SWITCH_BLOCK` | Halt activo (cada 10 min) | `reason`, `since`, `flatten_pending` |
| `KILL_SWITCH_CLEAR` / `KILL_SWITCH_DISARMED` / `KILL_SWITCH_ERROR` | Clear manual / ARMED=0 con halt / error (bloquea ese ciclo) | `source` / `halt` / `error` |

`ENSEMBLE_VOTE` (4/5) y `ENSEMBLE_SIGNAL` traen `state`: `mid`, `spread_usd`, `rv_m1` (M1 cerradas), `htf` (+1/0/−1), `murphy_veto`, `alpha_avoid`, `alpha_score`, `tape_imbalance` / `tape_n` (ticks Redis con `ts < decision_ts_msc`; `null` si Redis cae) y `decision_ts_msc`. Postgres guarda `state` en `ensemble_votes`.

**Ojo:** en el log casi no hay `TRADE_CLOSE`; el neto suele venir del `profit` MTM de `ROTATION_STALE_CLOSE` y faltan los SL/TP del bróker (2026-10-02: 407 en log vs 552 en bróker; WR 32% log vs 44% bróker). Para PnL y calibración usar el bróker.

**Cierres con magic 0 (corregido 2026-10-06):** los cierres a mercado del bot (`stale_*`, `rot_book_harvest`, `kill_switch`) salían con `magic: 0` y el bróker graba el deal OUT con ese magic. El filtro por magic los descartaba: desde 2026-10-05 ~11:24 no hubo ningún `TRADE_CLOSE` ni aviso de cierre por Telegram. Ahora el request de cierre lleva el magic de la posición, y los deals OUT se atribuyen al bot por el `position_id` de su `DEAL_ENTRY_IN` (`_out_deal_owner_magic`). Al reiniciar, el backfill de 7 días emite los `TRADE_CLOSE` faltantes con `backfilled: true`; esos no heredan el audit de open por símbolo. El kill switch no estaba afectado: `daily_loss_limit_reached` suma todos los deals de la cuenta.

```powershell
# Calibración (lee MT5 sin shutdown; join TRADE_OPEN.order == position id)
py -m trading.application.calibration_backfill --since 2026-09-29
py -m trading.application.calibration_report      # -> trading/docs/reports/calibration_latest.json

# Reporte diario (bróker como verdad; --telegram fuerza el envío)
py -m trading.application.daily_report --day 2026-10-03

# Propuesta nocturna Gemini (no llama nada si GEMINI_NIGHTLY_ENABLED=0 o falta la clave)
py -m trading.application.nightly_review
```

**Programado (2026-10-05).** Dos tareas de Windows corren `run_reports.ps1`, que solo lee el bróker y los logs: nunca opera ni toca el bot. Necesitan la PC prendida y la sesión iniciada; si la PC estaba apagada, corren al volver (`StartWhenAvailable`). Cada corrida deja su salida en `logs\reports_<modo>_<fecha>.log`.

| Tarea | Cuándo | Qué hace |
|---|---|---|
| `MetaTrader Daily Report` | Todos los días 00:10 | `daily_report --day <ayer>` contra el bróker, después Telegram (`TELEGRAM_NOTIFY_DAILY_REPORT=1`). Desde 2026-10-09 el texto también trae la decisión del swing de ese día local y el halt de rotación si el archivo sigue |
| `MetaTrader Weekly Gemini` | Domingo 23:30 | `calibration_backfill`, `calibration_report` y `nightly_review`, después Telegram con la hipótesis (`TELEGRAM_NOTIFY_NIGHTLY=1`) |

Gemini recibe además [`ESTABLISHED_FACTS.md`](ESTABLISHED_FACTS.md) (lo ya medido y decidido) y las hipótesis de las últimas 4 propuestas, y tiene que completar la sección "Contraste con hechos". **Cuando cambie una regla del motor o se mida algo nuevo, actualizá `ESTABLISHED_FACTS.md`**; si no, Gemini razona con datos viejos. Red de seguridad: si este archivo se modificó después de `ESTABLISHED_FACTS.md` (más de 60 s), el Telegram semanal avisa y `nightly_latest.json` marca `facts_outdated: true`. Para resolverlo, revisá los hechos y actualizá su línea "Última revisión", aunque no haya otro cambio.

**Telegram (2026-10-05).** Llegan solo estos mensajes:

- arranque (corto, describe la rotación) y parada con Ctrl+C
- kill switch (`TELEGRAM_NOTIFY_KILL_SWITCH=1`)
- reporte diario
- propuesta semanal
- reenvío de "NUEVA SEÑAL" del canal (`TELEGRAM_FORWARD_CHANNEL=1`)

Apagados: señales, aperturas, cierres, ventanas de sesión y error de MT5. El error de MT5 avisa cada 3 minutos mientras dura la desconexión, por eso está apagado.

Para rankear símbolos cuando haya muestra:

```powershell
# Cierres con net, duración, motivo y spread al salir
Select-String -Path .\trades.log -Pattern '"TRADE_CLOSE"' |
  ForEach-Object { $_.Line.Substring($_.Line.IndexOf('{')) } |
  ConvertFrom-Json |
  Select-Object ts, symbol, duration_seconds, net, close_how, spread_usd, open_spread_usd

# Libro flotante: peak-to-trough vs tickets jóvenes (stale no dispara)
Select-String -Path .\trades.log -Pattern '"ROTATION_BOOK"' |
  ForEach-Object { $_.Line.Substring($_.Line.IndexOf('{')) } |
  ConvertFrom-Json |
  Select-Object ts, n_open, floating_net, hwm, trough, drawdown_from_hwm, n_young, young_net, n_aged, aged_net
```

Un close **después de restart** puede no traer `open_votes` / `open_spread_usd` (el mapa en memoria se perdió). El `comment` del bróker, `close_how`, `duration_seconds`, `net` y `spread_usd` al cerrar sí quedan.

### Autopsia MAE (señal original vs M1)

No cambia la ejecución. Cruza `ENSEMBLE_SIGNAL` / `TRADE_OPEN` con velas M1 **cerradas** a +1/+2/+3/+5 min y mide MAE (en contra del **voto**, no del fill invertido) vs MFE.

```powershell
# Solo log (sin MT5): cuenta fires/fills y marca missing_bars
py -m trading.application.mae_profiler --since 2026-10-01 --no-mt5 --out mae_report.json

# Con M1 del terminal XBTFX (lectura; no manda órdenes ni apaga el terminal).
py -m trading.application.mae_profiler --since 2026-10-01 --out mae_report.json
```

Cohortes en el JSON: `pre_invert` (antes de 2026-10-01 14:36) vs `invert`, `filled` vs `unfilled`. `pct_mae_gt_mfe` en cada ventana. Kelly 0.50 USD/ticket. Si `n_missing` es alto, **no** concluir lag de API.

### P&L del fill a 5/15/30/60 min

Mark-to-market del **ticket ejecutado** (no del voto) sobre M1 cerradas. Sirve para elegir el time-stop de scalp. Pre-invert es la muestra del bot sin fade.

```powershell
py -m trading.application.fill_mtm_profiler --since 2026-10-01 --out fill_mtm.json
```

`ROTATION_BOOK.hwm` / `trough` se reinician con el proceso. El peak-to-trough durable es la serie de `floating_net`. Arrancar `bot_mt5.py` **rota** `trades.log` a `logs/trades_*.log`.

El log crece: ~80 JSON/ciclo. Si duele, bajar verbosidad de `ENSEMBLE_VOTE` a fired+skip “interesante”, pero el default es **todo**.

---

## Dashboard

- Home: cuota **ENTRY_IN 60 min**, consenso del ciclo, conteo `spread_caro` / `spread_cubre_stop` / `veto_htf` / `sin_pullback` / `rsi_rango` / `murphy_climax` / `rank_tail`, **todos** los cierres crypto desde **2026-09-29** (bot, SL/TP y cierre a mano). No historial H4 desde el 24/09.
- Lab: 5 módulos por símbolo + skip `veto_htf` / `sin_pullback` / `rsi_rango` / `murphy_climax` / `rank_tail` / spread. Alpha sombra / minero **ocultos** en modo rotación. Tabla **4/5 recientes**: enviados con alpha, Murphy, cinta, spread, cierre y net (join por ticket), más conteo de vetos. Aviso si el kill switch está activo.
- Posiciones: columnas **spread pts** y **spread $** (agujero bid/ask del lote ahora).
- Flask `debug=False` carga código al arrancar: tras cambiar `flask_dashboard.py` hay que **reiniciar Flask**.

---

## Constraints que no se negocian sin el usuario

- Demo XBTFX. No live real.
- Kelly cap **0.50 USD** (solo achica).
- Fire **4/5** + veto M15 / pullback / RSI-rango + `murphy_climax` + `rank_tail` (IMX/VEC/TRX/ZRX/RSR/AXS).
- Spread: no abrir si cubre el stop o hole > **10%** del riesgo USD.
- **60** opens/hora (bróker IN).
- **Un** slot por símbolo.
- No commitear a menos que lo pidan.
- No editar el archivo de plan original del ensemble.

---

## Cómo iterar mañana

Gobierno (snapshot, Brier, kill switch, Lab/Telegram, Gemini nocturno): [`GOVERNANCE_PLAN.md`](GOVERNANCE_PLAN.md). Las 5 fases están implementadas. Desde 2026-10-05 están en vivo: flatten del kill switch, reporte diario por Telegram y Gemini semanal programado. El estado y los números están ahí.

1. Leer este archivo + `AGENTS.md`.
2. Si el cambio es de **riesgo** (size, stops, 4/5, 60/h, stale): risk-quant manda; no inventar números.
3. Tests table-driven en `tests/test_ensemble.py`, `test_rotation_stops.py`, `test_rotation_stale.py`, `test_rotation_audit.py`, `test_rotation_book.py`, `test_swing_structure.py`, `test_mae_excursion.py`, `test_mae_profiler.py`, `test_rotation_state.py`, `test_decision_state.py`, `test_calibration_*.py`, `test_kill_switch*.py`, `test_phase4_reports.py`, `test_nightly_review.py`.
4. Actualizar **esta página** con la nueva regla y el nuevo `event` de log.
5. Reiniciar `bot_mt5.py` para que el loop tome código.
