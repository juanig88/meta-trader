# TP chico C1: prueba en la demo de Exness (desde 2026-11-04)

Prueba hacia adelante de la celda **C1** de [PREREG_TP_CHICO.md](PREREG_TP_CHICO.md), pedida por el usuario. **No aprobó el train**: en 2018-2025 perdió −0,14 USD por operación (38.620 operaciones). En octubre de 2026, con velas M1 reales de Exness, dio +0,21 (295 operaciones). La demo dice cuál de los dos se parece a las próximas 100 operaciones.

- Código: `trading/domain/tp_chico.py` (regla pura) y `tp_chico.py` (runner).
- Tests: `tests/test_tp_chico.py`.
- Investigación: `analyze_tp_chico.py`, `analyze_tp_chico_filtros.py`.

## Regla (fija, como se probó)

| | |
|---|---|
| Cuenta | Demo Exness, el terminal de `BOT_IMPULSO_MT5_PATH` (el mismo que Impulso). Se niega a correr si la cuenta no es demo |
| Símbolo / lote | `BTCUSDm`, **0.01** |
| Magic | **261104** (`BOT_TPCHICO_MAGIC`) |
| Cuándo | Al empezar cada hora UTC, solo si no hay posición abierta de este magic |
| Lado | El de la EMA 20 diaria de ayer (velas D1 de Exness): precio debajo → venta, arriba → compra |
| TP / SL | 100 / 1.000 de precio desde el precio de llenado: **+1 / −10 USD** |
| Ciclo | Después de 9 ganadoras seguidas saltea una entrada y la racha vuelve a 0 |
| Fin | A las **100 operaciones** o con **−30 USD** netos, lo primero que pase. Queda en `finished` y no abre más |

Protección extra que la simulación no tenía: no abre si el spread supera 30 de precio (`BOT_TPCHICO_MAX_SPREAD`). En octubre el spread estuvo entre 6,4 y 10.

## Cómo se juzga

Con el neto del broker por magic 261104, no con el log. La moneda da 90,9% de ganadoras con +1 / −10. Con el spread hace falta **más de ~91,5%** sostenido para ganar. Si al terminar no lo supera, se apaga y no se ajusta con estos mismos datos.

## Operación

```bash
python tp_chico.py --dry-run   # registra decisiones, no manda órdenes
python tp_chico.py             # opera en la demo (necesita BOT_TPCHICO_DEMO_LIVE=1 en .env)
```

- Detener: cerrar su ventana (`tp_chico`). Pausar entradas sin cerrarla: crear `tp_chico_kill_switch.halt` (`BOT_TPCHICO_HALT`).
- Una posición abierta sigue con su SL y TP en el broker aunque el bot esté apagado. Al arrancar, retoma el ticket del estado o la posición abierta de su magic.
- Estado: `tp_chico_estado.json` (racha, operaciones, ganadoras, salteos, neto, ticket). Log: `tp_chico_trades.log`. Los dos quedan fuera de git.
- Al arrancar espera al cambio de hora siguiente, como en la prueba.

## Eventos del log

| Evento | Qué es |
|---|---|
| `TPCHICO_START` | Arranque: cuenta, magic, reglas y estado |
| `TPCHICO_OPEN` / `TPCHICO_OPEN_FAIL` | Orden: lado, SL, TP, spread, EMA, racha, retcode |
| `TPCHICO_DRY_OPEN` | Lo mismo sin orden (dry-run) |
| `TPCHICO_SKIP` | `ciclo_9_ganadoras`, `spread_caro`, `sin_ema`, `sin_tick` o `tpchico_halt` |
| `TPCHICO_CLOSED` | Cierre visto en el broker: `trade_net_usd` (neto de esa operación), precio, comentario (`[tp ...]` / `[sl ...]`) y estado (`net_usd` es el acumulado) |
| `TPCHICO_END` | Llegó a 100 operaciones o a −30 USD |

# TP chico lateral: segunda prueba en la demo (desde 2026-11-04)

Bot aparte, en el mismo terminal demo y al lado de C1: `tp_lateral.py`, magic **261105**. Reglas fijas en [PREREG_TP_LATERAL_DEMO.md](PREREG_TP_LATERAL_DEMO.md).

- Código: `trading/domain/tp_lateral.py` (regla pura) y `tp_lateral.py` (runner; reusa el envío de órdenes de `tp_chico.py`). Tests: `tests/test_tp_lateral.py`.
- Origen: `analyze_tp_chico_lateral.py` (la regla lateral con 2 posiciones no aprobó el holdout: −0,13 USD por operación) y `analyze_dias_malos.py` (con 1 posición, lunes y 00–05 UTC fueron peores en 2018-2021 y en 2022-2025; sin ellos quedó en +0,036 y +0,007). Es exploración: solo la demo lo confirma.

## Regla

| | |
|---|---|
| Cuándo | Al cierre de cada vela M15 de Exness, sin posición abierta de este magic (1 a la vez) |
| Horario | Nunca los lunes ni de 00:00 a 05:59 UTC |
| Mercado | Precio a ≤ 1% de una zona; ninguna de las últimas 4 M15 con rango > 2,5× la mediana de las 96 anteriores; sin movimiento > 1% en 4 horas |
| Lado | EMA 20 diaria (debajo → venta, arriba → compra) |
| TP / SL | +1 / −10 USD con 0.01 (100 / 1.000 de precio) |
| Tope diario | −20 USD en el día UTC: no abre más hasta el día siguiente |
| Fin | 100 operaciones o −50 USD |

## Operación

```bash
python tp_lateral.py --dry-run
python tp_lateral.py             # necesita BOT_TPLATERAL_DEMO_LIVE=1 en .env
```

Pausar: `tp_lateral_kill_switch.halt`. Estado: `tp_lateral_estado.json`. Log: `tp_lateral_trades.log` (fuera de git).

| Evento | Qué es |
|---|---|
| `TPLAT_START` | Arranque |
| `TPLAT_SKIP` | Cada M15 sin entrada: `lunes`, `00_05_utc`, `lejos_de_zona`, `vela_grande`, `disparo_4h`, `tope_diario`, `spread_caro`, `sin_ema`, `tplateral_halt`, `terminado` |
| `TPLAT_OPEN` / `TPLAT_OPEN_FAIL` / `TPLAT_DRY_OPEN` | Entrada: lado, SL, TP, distancia a la zona, spread |
| `TPLAT_CLOSED` / `TPLAT_END` | Cierre: `trade_net_usd` es el neto del broker de esa operación y `net_usd` el acumulado / fin de la prueba |
