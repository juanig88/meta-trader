# Estrategias alternativas + plan de pruebas en paralelo

Fecha: 2026-08-09  
Premisa operativa: **no serializar** (oro → ajustar → otro → ajustar). Probar **varias hipótesis a la vez** en demo, con riesgo acotado y kill-switch.

---

## 1. Qué es el “Mercado” de MT5

Es el **MQL5 Market**: tienda de Expert Advisors (bots), indicadores y utilidades.  
Lo útil para nosotros **no** es comprar a ciegas, sino leer:

| Señal pública | Qué vale | Qué no vale |
|---|---|---|
| Descripción del producto | Pistas de **familia** de estrategia (grid, sesión, no-martingale, TF) | Marketing (“leyenda”, “élite”) |
| Presets de broker (IC Markets RAW) | Confirma que el edge depende de **spread/ejecución** | No prueba que funcione en tu cuenta |
| Live Signal MQL5 / Myfxbook | Equity, DD, #trades, holding time (si el link es real) | Cuentas seleccionadas, depósitos, riesgo alto, curva de grid |
| Reviews “funciona bien” | Ruido + sesgo de supervivencia | Evidencia científica |
| Código cerrado | Casi nunca revelan reglas | No se puede auditar lookahead / martingale oculto |

**No hace falta comprar Quantum Queen** para aprender de ella. Las pistas ya están en el copy y en reviews técnicas.

---

## 2. Quantum Queen X — qué se puede comprobar (y qué no)

### Pistas reales del copy / reviews

1. **Instrumento único:** XAUUSD (especialización, no “opera todo”).
2. **Multi-estrategia:** 9–12 módulos ON/OFF → portfolio de *edges* sobre el mismo símbolo.
3. **Sesión ampliada:** más horas que un scalp 10–13 ART (ellos buscan cobertura 24/5 selectiva).
4. **Filtros de calidad:** “no opera por estar ocupada”; spread/news en inputs típicos.
5. **Presets IC Markets RAW** → asumen cuenta hedging, oro a **2 decimales**, leverage alto.
6. **Motor declarado:** *“trend-following grid”* / recovery (step, lot multiplier, max orders en análisis de settings de la línea Quantum).

### Evidencia “live” — leerla con escepticismo quant

Reviews citan señales del orden de **+3000% growth**, **DD ~29–30%**, winrate ~77%, ~12 trades/semana, holding ~1h.  
Eso es **compatible con grid/recovery en oro**, no con “1 trade = 1R limpio”:

- Alta WR + DD 30% + equity explosiva = típico de **promediar en contra / grid**.
- Un DD del 30% en demo/live del vendor **no** es “seguro”; es el precio del modelo.
- Comentarios positivos ≠ OOS reproducible en tu infra.

### Qué **sí** copiamos como hipótesis (sin comprar el EA)

| Hipótesis extraída | Implementable / testeable | Riesgo |
|---|---|---|
| Especializar 1 símbolo profundo (XAU) + 1–2 satélites | Sí | Bajo |
| Varias estrategias en paralelo sobre el mismo chart (magics distintos) | Sí | Medio (correlación) |
| Filtro de sesión London/NY + spread + news | Sí | Bajo |
| Selectividad (pocas ops/día de calidad) vs muchas ops chicas | Sí — A/B | Bajo |
| Grid / martingale / lot multiplier | **Solo lab con hard cap** o **evitar** | **Alto** — no para lunes “serio” |

**Veredicto risk-quant:** Quantum Queen es una **pista de producto** (XAU + multi-strat + sesión + grid). No es blueprint aprobable. El grid lo tratamos como experimento aislado o lo descartamos; el resto (sesión, selectividad, multi-strat sin averaging) sí.

Fuentes: [MQL5 product page](https://www.mql5.com/en/market/product/185687), reviews que documentan grid/recovery y métricas de señal.

---

## 3. Otras premisas con evidencia *comprobable* (internet + Market)

“Comprobable” = se puede **codificar + backtest/forward** con reglas claras. No significa “aprobado para live”.

### Familia A — Session / kill-zone (muy repetida en XAU)

| Premisa | Evidencia / uso | Reglas testeables |
|---|---|---|
| **Asia range → London sweep → reversal** | Guías SMC/session gold (London 07–16 GMT; sweep Asia H/L) | Marcar high/low Asia; en London open, wick fuera + cierre adentro + BOS M15 |
| **London box → NY break & retest** | Material session-flow XAU 2024–25 | Break body del rango London en NY; retest; TP parcial |
| **Overlap Lon–NY only** | Consenso liquidez FX/oro | Solo entradas 12:00–16:00 UTC |

*Comprobable:* sí, con M15/H1. No requiere indicadores propietarios.

### Familia B — Trend / breakout con SL duro (Market “no grid”)

EAs populares (Ultra Power Gold, GoldIQ Pro, BB Return, Smart Gold Hunter, etc.) repiten:

- **No grid / no martingale**
- **Session filter** (US o London/NY)
- **AMA/EMA + ATR SL/TP** o **NY session breakout**
- 1 posición, BE + trail

Esto se parece más a nuestro `strategy_core` / breakout que a Quantum Queen.

### Familia C — Scalp momentum sesión (código abierto MQL5)

Ej. GoldScalpBot (Code Base): M5, London/NY, EMA momentum + breakout/retest, ATR exits, % risk, **sin martingale post-loss**.  
*Comprobable:* sí; cercano a nuestro parallel XAU pero con **filtro de sesión Lon/NY** en vez de solo 10–13 ART.

### Familia D — Mean reversion Asia / late NY

Premisa pública: Asia = rango; fade extremos (BB/RSI) con targets chicos; late NY fade a VWAP si exhaustion.  
*Comprobable:* sí en M15/H1. Falla en trend days → necesita filtro de rango (ATR día vs mediana).

### Familia E — Crypto / gas / índices (otras premisas, no solo oro)

| Instrumento | Premisa alternativa | Por qué probarla YA |
|---|---|---|
| `BTCUSD` | Breakout M15 + **solo con régimen H4** (ya en bot); o swing H1 EMA | Score alto en scan; no esperar a “terminar oro” |
| `XNGUSD` | Breakout sesión US energies | Scan OOS fuerte; no correlaciona 1:1 con XAU |
| `US30` / `US500` | NY open drive (15–30 min post-open) | Premisa session-index estándar; pocas ops/día |
| FX major | Lon–NY overlap breakout | Más “aburrido”, mejor para diversificar |

### Familia F — Grid suave (lab only)

Máx N adds, **multiplier = 1.0** (mismo lot), step = ATR×k, hard equity stop.  
Sirve para entender por qué Queen “se ve bien” — no para escalar capital.

---

## 4. Matriz de pruebas EN PARALELO (empezar ya)

Cada celda = **1 magic number**, lot demo pequeño, log aparte, kill-switch propio.

| Track | Símbolo | Premisa | TF | Objetivo ops | Horizonte demo |
|---|---|---|---|---|---|
| **T1** | XAUUSD | Actual parallel scalp EMA9/21 (baseline) | M15 | Muchas / chicas | Ya corriendo |
| **T2** | XAUUSD | Breakout `strategy_core` + ventana Lon/NY | M15+H4 | Medias | 2–3 semanas |
| **T3** | XAUUSD | Asia sweep → London reversal (1 trade/día máx) | M15 | 0–2/día | 2–3 semanas |
| **T4** | XNGUSD | Breakout ATR (scan promising) | M15+H4 | Medias | 2–3 semanas |
| **T5** | BTCUSD | Breakout + H4 filter (no short vs H4 up) | M15+H4 | Medias–altas | 2–3 semanas |
| **T6** *(opcional lab)* | XAUUSD | Grid step ATR, mult=1.0, max 3 adds, DD cap | M15 | Baja | 1 semana lab |

### Reglas de diversificación (para no quemar tiempo ni cuenta)

1. **Máx 1 track grid** y separado del resto.
2. **Máx 2 tracks sobre XAU** “reales” (T1+T2 o T1+T3); el tercero XAU solo si es lab.
3. Siempre **≥1 instrumento no-oro** (T4 y/o T5) para no quedar atrapados en un solo factor.
4. Mismo capital mental: si la cuenta demo es $X, riesgo agregado diario acotado (ej. −2% → pause all).
5. Comparar tracks por **PF / DD / ops / expectancy** a los 10–15 días — no “ajustar” cada día.

### Criterio de promoción (demo → más capital demo / paper serio)

Por track, a 10+ trading days:

- trades ≥ 20 (o ≥ 10 si es session-selective T3)
- PF ≥ 1.1
- max DD monetario acotado al diseño
- no dependencia obvia de 1 solo día outlier

Si falla → **apagar track**, no “tunear 3 semanas más” antes de mirar los otros.

---

## 5. Qué NO hacer

- Comprar EA de $1k–$2k solo porque “comentarios dicen que anda”.
- Copiar grid con lot multiplier > 1 sin entender el DD path a −40%.
- Esperar a que T1 XAU esté “perfecto” antes de encender T4/T5.
- Correr 6 cruces de oro a la vez (XAUUSD+XAUEUR+…).

---

## 6. Próximo paso de ingeniería (cuando digas)

1. Scaffold de **multi-track demo** (magics + ventanas + logs por estrategia).
2. Implementar **T3 Asia-sweep London** como módulo puro testeable.
3. Re-scan de instrumentos con **premisas B/D** (no solo ATR 1.5/RR2), ranking por familia.

Referencias internas: `informe_deep_analysis.md`, `icmarkets_sesiones_referencia.md`, `bot_mt5.py` (parallel XAU + strategy_core).
