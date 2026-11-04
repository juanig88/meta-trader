# Pre-registro: TP chico solo en mercado lateral, hasta 2 posiciones, una sola corrida en el holdout

Escrito 2026-11-04, **antes de correr esta regla**. El script (`analyze_tp_chico_lateral.py`) imprime el SHA-256 de este archivo y se niega a correr el holdout dos veces.

## Hipótesis (idea del usuario)

El TP chico (+1 USD) con SL amplio (−10 USD) gana cuando el precio va y viene, y pierde cuando sale disparado. Entonces: operar solo con el mercado lateral, **cerca** de un techo o piso y **sin velas grandes** recientes; no operar lejos de las zonas ni cuando el precio se dispara. Hasta 2 posiciones a la vez.

## Por qué se prueba directo en el holdout

La idea sale del train de `PREREG_TP_CHICO.md` (2018-2025): allí, lejos de las zonas el resultado empeoró (C3-C5). Probarla en ese train sería circular; se informa solo como diagnóstico.

Contacto previo con el holdout (2025-11-04 → 2026-11-04): `analyze_tp_chico.py` corrió la regla sin filtros y con tendencia; `analyze_tp_chico_filtros.py` corrió C0-C5 sobre el **último mes** de Exness (05/10 → 04/11), que cae adentro del holdout. Ninguna corrida usó el filtro de velas grandes, la cercanía a zonas ni posiciones en paralelo. Se declara.

## Datos

- Binance spot `BTCUSDT` 15m (velas y entradas) y 1h (zonas), mismo precio supuesto en `BTCUSDm`.
- **Holdout (decide):** entradas 2025-11-04 → 2026-11-04, en USD reales: TP 100 / SL 1.000 de precio, 0.01 lote (+1 / −10 USD).
- **Train (solo diagnóstico):** 2018-01-01 → 2025-11-03, en % (TP 0,13%, SL 1,3%).

## Regla (todo fijo)

Se evalúa al cierre de **cada vela M15**. Entra si se cumple todo:

1. **Cerca de una zona:** la distancia del precio al borde de la zona más cercana es **≤ 1%** (0 si está adentro). Zonas: `find_zones` por defecto sobre las 1.440 H1 cerradas antes del día (se recalculan una vez por día).
2. **Sin velas grandes:** ninguna de las últimas 4 velas M15 cerradas tiene un rango (máximo − mínimo) mayor a **2,5 veces** la mediana de los rangos de las 96 velas M15 anteriores a esas 4.
3. **Sin disparo:** el precio no se movió más de **1%** entre el cierre de hace 16 velas M15 (4 horas) y el actual.
4. **Lugar libre:** hay menos de **2** posiciones abiertas, y si hay una, el precio actual está a **100 USD de precio o más** del precio de entrada de esa (si no, sería duplicar la misma operación).

Lado, una celda por cada forma (N = 2):

| Celda | Lado |
|---|---|
| L1 | Tendencia: EMA 20 diaria de ayer (cierre debajo → venta, arriba → compra) |
| L2 | Zona: si la zona más cercana está arriba del precio → venta; abajo → compra; adentro de una zona → su tipo (techo → venta, piso → compra) |

Salida: TP o SL desde el precio de llenado, lo primero que toque; si una vela M15 toca los dos, SL. Sin límite de tiempo.

## Costos

Spread 6,4 USD de precio (el TP necesita 100 + spread; el SL salta un spread antes). Swap de las compras 0,178 USD por día. Estrés (diagnóstico): spread 10.

## Métrica, prueba y aprobación

- Unidad: USD neto por operación (0.01 lote).
- Prueba: bootstrap por día de entrada, 2000 iteraciones, semilla 7, p de una cola.
- **Aprueba (holdout):** n ≥ 200, media neta > 0, p < 0,025 (0,05 / 2), profit factor > 1.
- Si aprueba: demo con parámetros congelados, magic propio, 100 operaciones o −30 USD. Si no: se descarta, sin variantes sobre estos datos.

## Diagnósticos (no deciden)

Acierto contra la moneda (90,9%), operaciones por día, peor pérdida en un mismo día, cuántas veces estuvieron las 2 abiertas, compras y ventas, el train en %, spread 10.

## Enmiendas

Ninguna.
