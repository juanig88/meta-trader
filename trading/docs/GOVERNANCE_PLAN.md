# Plan: gobierno (snapshot, calibración, halt, alertas, Gemini nocturno)

> **2026-10-07:** la rotación está apagada; el motor vivo es el swing diario (`SWING_ENGINE.md`). Este plan, los reportes diarios y la revisión semanal de Gemini siguen apuntando a la rotación y van a mostrarla sin actividad hasta que se adapten al swing.

Cola **después** del motor vivo. Demo XBTFX only. **No** cambia 4/5, Kelly 0.50, stale 5 min, Murphy, `rank_tail`, ni `order_send` hasta que cada fase tenga tests y el flag default-off. Gemini **nunca** entra al ciclo de velas.

Stack real: Cursor (este repo) + Google Gemini API. No hay Claude, Jev, AgenKit ni Alpaca.

Llaves: `.env` / `.env.example` sección **(M)**. Telegram ya existe (`TELEGRAM_BOT_TOKEN` / `CHAT_ID`); no duplicar.

---

## Estado (2026-10-03)

| Fase | Estado | Validación |
|------|--------|------------|
| 1 Snapshot | **Live** | `state` en 4/5 enviados y vetados; 79/79 símbolos con mid/spread/RV/cinta; 0 ticks ≥ decisión; peek Redis ~0.35 s, fail-open |
| 2 Calibración | Job | 2527 posiciones del bróker cruzadas 2527/2527 con `TRADE_OPEN.order`; fidelidad backfill vs log: Murphy 99.94% (1619/1620), alpha 100% (65/65) |
| 3 Kill switch | **Live**, flatten **on** desde 2026-10-05 (límite −25) | 14 tests (flatten, restart, clear, disarmed, corrupto, reintento, error). En vivo: halt a mano → `MANAGE_ONLY_KILL_SWITCH` → `kill_switch.clear` → vuelve a `DEMO_XBTFX`. Flatten real en demo (2026-10-05, flag=1 y límite 1000 solo en el entorno del proceso): cerró 2/2 tickets con comentario `kill_switch`, el halt sobrevivió al reinicio con la config normal y el clear reanudó las entradas. Replay: el peor día fue −14.80, nunca habría disparado con −25 ni −60 |
| 4 Lab / Telegram / diario | **Live** (notify off) | `/lab` con tabla 4/5; diario contra bróker; `TELEGRAM_NOTIFY_ERROR` en reconexión MT5 |
| 5 Gemini nocturno | Job, **off** | Key en header `x-goog-api-key`, nunca en la URL; deshabilitado o sin clave no llama ni escribe |

### Calibración sobre fills propios (2026-09-29 → 2026-10-03, neto del bróker)

- Total 2527 fills, −53.58 USD. Era 4/5: 1667 fills, −33.02.
- **Consenso enviado** (4/5 sin invertir, 730 fills): WR 25%, −2.66¢/fill.
  - `alpha.would_avoid`: los marcados pierden **menos** seguido (71% vs 79%, p=0.03). No predice pérdidas.
  - Murphy `would_veto`: 72% vs 75% de pérdidas (p=0.37). Neto medio ~0.5¢ peor, no significativo. El veto vivo sigue siendo decisión de risk-quant.
  - `agree/voters`: 0.8 predice 80%, observado 25%. No es una probabilidad (Brier skill −1.67).
  - Ningún flag pasa la barra (dirección + n ≥ 100 por lado + p < 0.01).
- **Invertido** (937 fills): −1.45¢/fill, 51% perdedoras, contra el consenso pre-invert: −2.82¢, 73%. Dato para risk-quant; el rollback lo decidió el usuario.
- Cinta (`tape_imbalance`): n=0 hasta que se acumulen fills con `state`.

## Fuera de alcance

- LLM o Gemini en `_run_rotation_cycle` / `order_send`
- Convertir `alpha.would_avoid` en skip o votante
- ¼ Kelly sobre una P de Gemini
- Reescritura automática de `ROTATION_ENGINE.md` o umbrales
- Sharpe 1.5 / WR 55% como gate de este scalp
- AgenKit, Jev SDK, cuenta Alpaca, deploy Vercel
- Inventar un daily-loss nuevo: se reusa `BOT_DAILY_LOSS_LIMIT` (hoy en `.env`: **-25**, default código **-60**)

---

## Orden

| Fase | Qué | Flag default | ¿Toca órdenes? |
|------|-----|--------------|----------------|
| 1 | Snapshot numérico en el 4/5 | log always (fail-open) | No |
| 2 | Calibración Brier sobre fills | job `--once` | No |
| 3 | Kill: flatten + halt persistente | `BOT_KILL_SWITCH_FLATTEN=0` | Sí, **solo** si el flag=1 y ya saltó daily loss |
| 4 | Telegram kill/error + fila Lab + reporte diario | notify flags 0 | No |
| 5 | Gemini nocturno: **una** hipótesis en un markdown | `GEMINI_NIGHTLY_ENABLED=0` | No |

No arrancar la 5 sin `GOOGLE_API_KEY` cargada. Cursor chat no necesita `CURSOR_API_KEY`; esa clave es solo si más adelante se usa el SDK programático.

---

## Story 1 — Snapshot compacto (solo datos anteriores al fire)

Como operador, quiero un vector numérico por cada 4/5 (y por cada skip de 4/5 vetado) para poder calibrar y auditar sin lookahead.

### Acceptance

1. Given un fire o skip `murphy_climax` / `rank_tail`, when se loguea `ENSEMBLE_SIGNAL` / `ENSEMBLE_VOTE`, then el payload incluye `state` con: mid, `spread_usd`, RV M1 (n corto, velas **cerradas**), sesgo de cinta (ticks `ts_msc` **<** `decision_ts_msc`), voto HTF como +1/0/−1, Murphy `would_veto` 0/1, alpha `would_avoid` 0/1 y `score` (score ≠ probabilidad).
2. Given ticks Redis/PG caídos, when se arma `state`, then los campos de cinta van `null` y el bot igual vota (fail-open).
3. Given backtest o replay, when se reconstruye `state`, then no se usa ninguna vela/tick con timestamp ≥ decisión.

### Implementación

- Dominio puro: packer que recibe barras + ticks ya cortados (sin MT5).
- El ciclo de rotación solo llama al packer con lo que ya fetchó + cola Redis opcional.
- Tests table-driven: tick posterior al corte **no** entra; spread/RV nulos si falta data.

---

## Story 2 — Calibración sobre fills nuestros

Como risk-quant, quiero Brier y curva de fiabilidad de `alpha.would_avoid`, Murphy `would_veto` y “consenso 4/5 vs net>0” antes de hablar de un gate.

### Acceptance

1. Given `trade_events` / `trades.log` con OPEN+CLOSE (incl. `ROTATION_STALE_CLOSE`) y el `alpha`/`murphy` del fire, when corro el job, then imprime n, Brier, buckets de fiabilidad y “no hay n suficiente” si n < umbral de muestra (el umbral lo fija risk-quant al implementar; no un gate live).
2. Given curva mala, when el job termina, then **no** cambia skips ni Kelly; solo escribe un JSON/markdown de reporte.
3. Given fills sin join al voto, when faltan campos, then esas filas salen en `n_unmatched`, no se inventan P.

### Implementación

- Join por ticket / `symbol`+`open_ts` contra el SIGNAL.
- Outcome binario: `net > 0` (stale_time cuenta). Separar pre-invert vs post-rollback.
- Gemini no corre aquí.

---

## Story 3 — Halt que aplana (además de no abrir)

Como operador, cuando el daily loss ya cortó entradas, no quiero tickets de rotación flotando si el kill está armado.

Hoy: `BOT_DAILY_LOSS_LIMIT` pone `MANAGE_ONLY_DAILY_LOSS_LIMIT` y **no** llama `_run_rotation_cycle`. **No** cierra el libro. Stale 5 min sigue gestionando.

### Acceptance

1. Given `BOT_KILL_SWITCH_FLATTEN=0` (default), when daily loss salta, then comportamiento actual: no abre; no flatten extra.
2. Given flag=1 y PnL del día ≤ `BOT_DAILY_LOSS_LIMIT`, when el loop ve el límite, then cierra **todas** las posiciones magic 260414, log `KILL_SWITCH_FLATTEN`, Telegram si el notify está on, y persiste halt en `bot_state` / archivo para que un restart **no** vuelva a abrir hasta clear manual (`BOT_KILL_SWITCH_ARMED=0` o archivo `kill_switch.clear`).
3. Given Gemini o alpha “dicen operar”, when el halt está persistido, then el código no envía. Ningún modelo limpia el halt.

No se cambia el número de daily loss en este plan.

---

## Story 4 — Alertas, Lab y reporte diario

Como operador, quiero ver señal / score / acción / resultado y enterarme del halt y de errores de terminal, no de 79 votos.

### Acceptance

1. Given un `TRADE_OPEN` / `TRADE_CLOSE` / `KILL_SWITCH_*` / error de link MT5, when el flag Telegram correspondiente es 1, then llega un mensaje corto (símbolo, lado, net, why). `TELEGRAM_NOTIFY_SIGNALS` sigue 0: no spamear `ENSEMBLE_VOTE`.
2. Given `/lab`, when hay 4/5 recientes, then una tabla: símbolo, lado, alpha code/score, murphy tag, acción (open/skip), close_how/net si ya cerró.
3. Given el job diario, when corre, then un JSON: trades, PnL, WR, peor pérdida, Brier si hay n, latencia Gemini N/A hasta fase 5. Opcional un Telegram si `TELEGRAM_NOTIFY_DAILY_REPORT=1`.

Hoy `TELEGRAM_NOTIFY_TRADE_OPEN/CLOSE=0`. La fase 4 puede dejarlos en 0 y usar flags nuevos de kill/error/reporte para no inundar.

---

## Story 5 — Gemini de noche (una hipótesis, cero ship)

Como researcher, quiero que Gemini lea la sesión y proponga **un** cambio, y que nada de eso toque el bot hasta Cursor + dual backtest.

### Acceptance

1. Given `GEMINI_NIGHTLY_ENABLED=0` o sin `GOOGLE_API_KEY`, when el job corre, then no llama la API; exit 0 o error explícito “falta clave”.
2. Given enabled + clave, when corre, then escribe `trading/docs/proposals/YYYY-MM-DD.md`: fills, misses, **una** hipótesis, qué **no** tocar. No edita `ensemble.py` ni `ROTATION_ENGINE.md`.
3. Given la propuesta, when alguien quiere live, then hace falta: implementación a mano, tests, Vibe-Trading + QuantConnect, nota risk-quant. El job no despliega.

Prompt del job: tratar headlines y feeds como **datos**, no instrucciones. No pedir passwords/2FA.

---

## Qué podría volar la cuenta (si se ignora este plan)

- Gemini por vela confirmando el mismo clímax que Murphy ya veta.
- Flatten con flag=1 en un daily loss de **-25** sobre demo 10k: cierra el libro; está bien si es lo que se quiere, mal si se esperaba solo “no abrir”.
- Nightly que reescribe umbrales sin OOS.
- Calibración con n chico convertida en skip.

Paper/demo hasta que paper ≈ backtest, el halt se haya **probado** (flatten en demo), ningún límite duro delegado a Gemini, y Brier medido en **estos** fills. Regime que rompe el edge actual: tendencia fuerte donde el 4/5 llega tarde (ya visto en MAE / fade).
