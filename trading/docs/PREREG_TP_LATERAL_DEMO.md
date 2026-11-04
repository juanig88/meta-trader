# Pre-registro: TP chico lateral sin lunes ni 00–05 UTC, prueba hacia adelante en la demo

Escrito 2026-11-04, **antes de la primera operación**. Datos de prueba: solo operaciones de la demo desde el arranque del bot `tp_lateral.py`. El histórico ya no puede confirmar esta regla: el train la sugirió (`analyze_dias_malos.py`) y el holdout 2025-11 → 2026-11 lo consumió `PREREG_TP_CHICO_LATERAL.md`.

## Hipótesis (idea del usuario)

La regla lateral (L1 de `PREREG_TP_CHICO_LATERAL.md`) pierde sobre todo los **lunes** y entre las **00 y las 05 UTC**. En el train, sacando esas horas, dio +0,036 USD por operación en 2018-2021 y +0,007 en 2022-2025, con 91,3% y 91,0% de ganadoras. Se encontró mirando 10 variables: puede ser casualidad.

## Regla (todo fijo)

- Cuenta: demo Exness, terminal `BOT_IMPULSO_MT5_PATH`. `BTCUSDm`, **0.01**, magic **261105**.
- Se evalúa al cierre de cada vela M15 (velas de Exness). Entra si se cumple todo:
  1. No es lunes ni son las 00:00–05:59 UTC.
  2. No hay posición abierta de este magic (**1 a la vez**).
  3. Distancia a la zona más cercana ≤ 1% (`find_zones` sobre las 1.440 H1 cerradas antes del día UTC).
  4. Ninguna de las últimas 4 M15 tiene un rango > 2,5 veces la mediana de las 96 anteriores.
  5. El precio no se movió más de 1% en las últimas 16 M15.
  6. Spread ≤ 30 de precio.
- Lado: EMA 20 de las D1 cerradas (precio debajo → venta, arriba → compra).
- TP 100 / SL 1.000 de precio desde el llenado: **+1 / −10 USD**.
- Tope diario: con **−20 USD** netos en el día UTC no abre más hasta el día siguiente.
- Fin: **100 operaciones** o **−50 USD** netos.

## Cómo se juzga

Neto del broker por magic 261105. La moneda da 90,9% de ganadoras; con el spread hace falta **más de ~91,5%** y media neta > 0. Con 100 operaciones no alcanza para un p chico: el resultado dice si conviene seguir juntando operaciones (otras 100 con la regla congelada) o descartarla. No se cambia ningún número durante la prueba.

## Diagnósticos (no deciden)

Compras y ventas, peor día, cuántas veces frenó el tope diario, minutos hasta el TP.

## Enmiendas

Ninguna.
