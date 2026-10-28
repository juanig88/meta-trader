# Hechos establecidos del motor de rotación

Contexto fijo para la revisión semanal de Gemini (`nightly_review`): lo que ya se midió y lo que ya se decidió. Gemini no debe proponer algo que lo contradiga salvo que los datos de la semana muestren evidencia nueva. **Actualizar este archivo cuando cambie una regla del motor o se mida algo nuevo.**

Demo XBTFX, magic 260414. Runbook: `ROTATION_ENGINE.md`. Bitácora: `SESSION_2026-10-01_a_10-05.md`.

**Última revisión:** 2026-10-27.

## Datos y medición

- **La fuente de verdad del PnL es el neto del bróker.** El log casi no registra los cierres por SL/TP. El 2026-10-02 el log tenía 407 cierres y el bróker 552, con win rate 32% contra 44%. Una diferencia entre `sum_log_on_both` y `sum_broker_on_both` es esperable y no indica un problema de ejecución.
- **Clave de join:** `TRADE_OPEN.order` = position id de MT5 = `ticket` de STALE_CLOSE = `position_id` de TRADE_CLOSE. La fidelidad del backfill es 99.94% para Murphy y 100% para alpha.
- **Cinta (`tape_imbalance`):** la muestra todavía es muy chica (n de un dígito). No se pueden sacar conclusiones.
- Desde 2026-10-06 hay `TRADE_CLOSE` y el tope 60/h cuenta (ver runbook).

## Ejecución (descartado como causa)

- **No hay lag de ejecución.** El fill entra en la misma vela M1 que la señal: mediana 1 s, máximo 3 s, 0 fills con 1 o 2 velas de atraso (autopsia MAE, 895 fills, 2026-10-01). Latencia, slippage de API o "precio stale" no explican el PnL.
- **La excursión de 1 a 5 minutos es de centavos.** La MAE mediana a +5 min equivale a ~2.6% del stop de 0.50 USD. La plata no se pierde en el primer minuto.
- **Los rechazos `spread_caro` / `spread_cubre_stop` son el filtro funcionando.** No abre si el bid/ask cubre el stop o si el agujero supera el 10% de los 0.50 USD de riesgo. No son "fills perdidos por mala ejecución". BTC y varias alts no llenan por eso, a propósito.

## Salida

- **El time-stop de 5 minutos salió del mark-to-market de los fills** (369 fills pre-invert):

  | Horizonte | EV |
  |---|---|
  | +5 min | −0.0047 |
  | +15 min | −0.0056 |
  | +30 min | −0.0127 |
  | +60 min | −0.0181 |

  Corre desde 2026-10-03. Recorta el sangrado, no crea un edge.
- **Con el stale de 1 hora** (anterior al 2026-10-03), take ~+0.023 contra loss ~−0.031 exigía ~57% de win rate y había 25–29%.

## Dirección

- **El invert** (vender cuando el 4/5 decía comprar) estuvo vigente del 2026-10-01 14:43 al 2026-10-03 09:10 (937 fills): −1.45 centavos por fill y 51% perdedoras, contra −2.82 y 73% del consenso pre-invert.
  - Lo revirtió el usuario.
  - El corte también es mañana contra tarde, no un A/B limpio.
  - No volver a proponerlo sin un A/B diseñado.
- **El universo es crypto correlacionada.** Muchos tickets del mismo lado son una sola apuesta a la dirección de BTC, no apuestas independientes.

## Calibración (neto del bróker, 2026-09-29 → 2026-10-03; se recalcula cada semana)

- **Consenso enviado hoy** (730 fills): win rate 25%, −2.66 centavos por fill.
- **El consenso tiene edge bruto cero y la pérdida es el spread** (motor actual, post 2026-10-03 09:10, sin invert; 183 posiciones cerradas, todas con spread loggeado):
  - neto del bróker −1.38 centavos por trade, win rate 13.7%, vida mediana 305 s (el stale de 5 min funciona)
  - spread pagado al abrir: 1.29 centavos medios (3.8% del riesgo)
  - **antes del spread: −0.09 centavos (cero).** Mismo resultado que los replays de BTC, ETH y USDJPY: la señal no anticipa dirección en ningún símbolo, y lo que se pierde es el costo de entrar.
  - Salidas: `rot_stale_loss` 139 (−1.85 c cada una), `rot_stale_take` 12 (+2.43 c), `rot_stale_time` 27 (~0), 1 SL. El win rate bajo es en parte la clasificación (`loss` es cualquier cosa bajo −0.0001; `take` exige +0.01), no una asimetría de magnitudes.
- **El costo contra el movimiento define el horizonte jugable** (79 símbolos, 2026-10-05): spread mediano 0.197% del precio; movimiento mediano 0.147% a 5 min, 0.172% a 15 min, 1.075% a 4 h. A 5 min el costo es **1.3x** el movimiento (solo 18 de 79 tienen spread menor al movimiento); a 4 h es **0.2x** (75 de 79). **El scalp de 5 min es aritméticamente casi imposible, independientemente de la señal.**
- **Horizontes largos con la misma señal: descartado por test pre-registrado** (`analyze_horizon_edge_2y.py`, 2026-10-05). El indicio de 1 día que había aparecido en 29 días de Yahoo (bruto +0.0228, t 1.17, neto +0.0108, n 137) **no sobrevivió a 24 meses**: era ruido de muestra chica.
  - Diseño fijado antes de mirar: Binance spot 1m oct-2024 → sep-2026, 12 símbolos (BTC solo como peer, su swap de 0.46/día lo hace inviable a días), entrada idéntica a la viva, costos = spread XBTFX por hora UTC + swap por rollover cruzado, un trade por símbolo-día, bootstrap sobre días calendario (2000 iter, semilla 20261005), train hasta 2026-01-31 y holdout 2026-02 → 09. Regla: avanzaba solo con train neto > 0 y p < 0.01 por día.

  | Tenencia | n (train) | Bruto/trade | p | Neto/trade | p | WR | PF |
  |---|---|---|---|---|---|---|---|
  | 4 h | 2863 | +0.0035 | 0.34 | −0.0055 | 0.14 | 46.1% | 0.89 |
  | 1 día | 1798 | −0.0026 | 0.68 | −0.0144 | 0.035 | 46.3% | 0.86 |

  - **El bruto es cero en los dos horizontes** (intervalos al 95% cruzando el cero), igual que a 5 minutos. Alargar la tenencia baja el peso del spread pero no crea dirección.
  - **El holdout (799 y 1261 trades) quedó sin mirar**, porque la regla ya falló en el primer tramo. Sigue reservado para una hipótesis distinta; no reusarlo para variantes de esta. No se corrió `--stress`: solo empeora un resultado ya negativo.
  - Sesgos que quedan y que **no** rescatan el resultado: supervivencia, Binance spot como proxy del CFD, spread de hoy proyectado a 2 años.
- **Ningún módulo predice dirección, por separado ni junto** (`analyze_module_information.py`, 2026-10-05). Auditoría pre-registrada sobre 560k barras (12 símbolos, oct-2024 → ene-2026, holdout sin leer): retorno medio firmado por el voto, a precio medio, **sin costos y sin stop** (cota superior de lo que el módulo sabe). 6 filas × 5 horizontes = 30 tests, vara Bonferroni p < 0.00033.

  - **Ninguna de las 30 celdas pasa** (tabla completa en `analyze_module_information_out.json`). Lo mejor: `momentum_short` −1.76 bps a 15 min (p 0.003) y −2.79 a 60 min; `intermarket` −0.50 a 5 min (p 0.002); `consenso 4/5` −0.51 / −1.21 / −1.79 a 5/15/60 min. `htf` y `structure` son ~0 en todo.
  - **La magnitud pesa más que la significancia:** el spread mediano es **19.7 bps**. El valor más grande de la tabla (htf a 1 día, +6.33, p 0.50) es **3 veces menor** que el costo; el mejor con algo de señal es **11 veces menor**. Aunque alguno fuera real, no paga el spread.
  - Casi todo lo que tiene tamaño es **negativo**: los módulos quedan levemente del lado equivocado. Invertir ya se probó en vivo (fade del 2026-10-01, revertido el 10-03) y perdió; acá el tamaño tampoco alcanza.
  - `htf` y `structure` votan el 94% y 88% de las veces: casi siempre opinan y su media es cero. Aportan poco como votantes.
- **Cartera relativa a 7 días: descartada** (`analyze_cross_section.py`, 2026-10-05). 73 CFD, 67 semanas. Bruto +175 bps/semana (p 0.23; 52% de semanas positivas). Neto +43 bps (p 0.76) tras 133 bps de spread y swap. El intervalo cruza el cero. Holdout de 34 semanas sin abrir.
- **Trampa de método al medir el neto:** los deals de cierre llevan **magic 0**, no 260414. Filtrar por magic pierde ~95% de los cierres y da un neto sesgado (daba −6.09 c por trade con 158 cierres en lugar de −1.38 c con 2638). Hay que agrupar por `position_id`: el IN identifica si es nuestra, y se suman **todos** los OUT sin filtrar por magic.
- **Acceso al universo:** el gate de spread bloquea **14 de 79** en un momento dado (snapshot 2026-10-05 con el riesgo Kelly vivo de 0.30, lote escalado como en producción: ATM, AXS, BTC, CRV, ETH, FIL, GRM, INC, IOT, NEO, SHB, SUS, XTZ, YFI; con 0.50 son 12). No es permanente: el spread varía y **76 de 79 abrieron al menos una vez** en la semana. El 4/5 dispara en los 79. Cuello de botella: `sin_pullback` 78%, `veto_htf` 14%; solo el 2% llega a enviar.
  - Calcular el gate con `volume_min` en vez del lote escalado subestima los bloqueos (daba 2 en lugar de 14).
- **`alpha.would_avoid`:** los fills marcados pierden **menos** seguido (71% contra 79%, p=0.03). Los quintiles de score alpha más alto rinden peor.
  - Es un dato a seguir, no un gate.
  - Regla fija: alpha es solo telemetría. Ni `would_avoid` ni el score (en quintiles o invertido) pueden ser filtro, skip, votante ni ajuste de tamaño.
  - Un filtro "alpha al revés" descubierto en estos mismos datos sería data snooping.
- **Murphy `would_veto`:** 72% contra 75% de pérdidas (p=0.37), no significativo. Mantener o quitar el veto vivo es decisión de risk-quant.
- **`agree/voters` = 0.8** predice 80% y se observa 25%: no es una probabilidad (Brier skill −1.67).
- **Vara para discutir un gate:**
  - que el lado marcado pierda más seguido **y** tenga peor neto medio
  - n ≥ 100 por lado
  - p < 0.01
  - que el skip hubiera sacado USD perdedores

  `live_gate` siempre es falso: nada pasa a vivo sin el gate dual.

## Investigación fuera de la rotación (no vivo)

- **Intradía Exness** (`INTRADIA_EXNESS.md`): Murphy, zigzag, flujo y reversión: neto < 0 en M5–M30.
- **Swap:** XBTFX ~20%/año, Exness ~10%. Swing: dry-run en Exness.

## Ya decidido (no re-proponer)

- **Cortes vivos:** `murphy_climax` (clímax unbroken en M15/H4) y `rank_tail` (IMX/VEC: WR stale < 10%, n ≥ 20; TRX/ZRX/RSR/AXS: 0 ganadores 30/9–6/10).
- **Fijo:** 4/5, Kelly acotado a 0.50 USD, stale 5 min, un slot por símbolo, 60 aperturas por hora.
- **Kill switch:** flatten en vivo desde 2026-10-05 con daily loss −25. El PnL del día incluye movimientos de balance.
- **Descartado:**
  - más votantes
  - aflojar umbrales
  - órdenes limit en la EMA
  - esperar una vela
  - un LLM por vela
  - ¼ Kelly sobre una probabilidad de un LLM
  - Sharpe 1.5 / win rate 55% como gate de este scalp
  - volumen de XBTFX como voto
  - **USDJPY en la rotación** (replay offline 2026-10-05, `analyze_usdjpy_rotation_replay.py`). Se usó el `evaluate_symbol` vivo, 0,01 lote, SL/TP de $0,50, cierre a 5 min y el spread XBTFX por hora (17 pts, 78 a las 18 UTC).
    - Yahoo 1m, 2026-09-07 → 10-05: 315 trades, −0,133 USD por trade, win rate 29%, PF 0,26. Mitades iguales (−0,134 y −0,132).
    - Antes del spread da −0,018 por trade (t −1,3): no hay ventaja de dirección. El lado opuesto también pierde (−0,114).
    - XBTFX solo (5,5 días, 22 trades) da lo mismo: −0,171 neto y −0,011 antes del spread.
    - El gate de spread lo bloquea siempre: 0,01 lote mínimo con $0,50 de riesgo pone el spread en ~21% del riesgo.
    - Re-proponer solo con un replay nuevo que muestre ventaja antes del spread mayor a ~0,12 USD por trade.
  - **Subir el riesgo (o escalarlo con el saldo) para que BTC pase el gate de spread** (replay 2026-10-05, `analyze_rotation_replay.py`, Yahoo 1m 29 días + caché XBTFX 3,5 días).
    - BTC necesita ~9 USD de riesgo a 0,01 lote: stop ~1,1% y spread 0,87 USD por trade.
    - Con salida a 5, 15 y 60 min, la ganancia antes del spread fue +0,07 / +0,05 / −0,12 USD por trade (t < 1, es cero). El neto fue −0,80 / −0,82 / −0,98 por trade (179 / 163 / 123 trades). El lado opuesto también pierde.
    - ETH (pasa el gate con 0,50) da lo mismo: antes del spread ~0 en los tres horizontes, neto −0,03 a −0,04 por trade.
    - El spread escala con el lote igual que el movimiento: más saldo no cambia la relación.
    - Re-proponer solo con un replay que muestre ventaja antes del spread mayor al spread del símbolo.
  - **Alargar la tenencia del 4/5 a 4 h o 1 día** (test pre-registrado de 24 meses, ver arriba). El bruto es cero en los dos horizontes; el neto queda en −0,0055 y −0,0144 por trade. El holdout quedó sin abrir.
  - **Cartera de las 5 más fuertes contra las 5 más débiles, a 7 días** (ver arriba). El bruto no se distingue del azar y el costo de la semana se come lo que queda.
