# Pre-registro: TP chico y SL amplio en BTC, con los filtros del usuario

Escrito 2026-11-04, **antes de correr los filtros**. El script (`analyze_tp_chico_filtros.py`) imprime el SHA-256 de este archivo junto a los resultados y se niega a abrir el holdout dos veces.

## Hipótesis (idea del usuario)

Con 0.01 lote, TP a 100 USD de precio (+1 USD) y SL amplio, el precio casi siempre vuelve al TP. Sin filtros eso es geometría: una moneda da SL/(TP+SL) y la esperanza es cero antes de costos (`analyze_tp_chico.py`, 12 meses: 90% de acierto con SL 1.000, −4 a −12 USD cada 100 trades). El usuario propone tres ajustes para superar a la moneda:

1. **Ciclo de 10:** después de 9 ganadoras seguidas no abrir la siguiente y empezar un ciclo nuevo.
2. **Tendencia:** operar solo a favor.
3. **Lejos del techo y el piso:** no operar si hay una zona cerca.

## Contacto previo con los datos

`analyze_tp_chico.py` ya corrió la regla **sin filtros** (y con tendencia por EMA 20 diaria) sobre 2025-11-04 → 2026-11-04. Por eso ese año es holdout solo para los filtros de zona y el ciclo, no para la tendencia sola; se declara y la celda C2 se juzga con el train.

## Datos

- Binance spot `BTCUSDT` 15m (`data/binance/BTCUSDT_15m.csv`) y 1h para las zonas. Se supone el mismo precio en `BTCUSDm` de Exness.
- Las distancias van en **% del precio** para que 2018 (BTC a 4–20k) y 2026 (77k) sean comparables: TP 0,13%, SL 1,3% (100 y 1.000 USD a 77.000).
- **Train:** 2018-01-01 → 2025-11-03.
- **Holdout:** 2025-11-04 → 2026-11-04. Se abre una sola vez, solo para las celdas que pasen el train.

## Regla (todo fijo)

- Entradas: al cierre de cada hora UTC. El precio de entrada es el cierre de la vela 15m de :45.
- Salida: TP a +0,13% o SL a −1,3%, lo primero que toque. Si una vela 15m toca los dos, cuenta como SL. Sin límite de tiempo.
- Tendencia: lado de la EMA 20 diaria de **ayer** (cierre bajo la EMA → venta; sobre la EMA → compra).
- Zonas: `trading.domain.rebound_zones.find_zones` con sus valores por defecto, sobre las 1.440 velas H1 que cerraron antes del día de la entrada (se recalculan una vez por día). La distancia es al borde más cercano de cualquier zona; adentro de una zona la distancia es 0.
- Ciclo: se opera **en serie** (una posición a la vez; la siguiente entrada es la primera hora después del cierre). Después de 9 ganadoras seguidas se saltea la entrada siguiente y la racha vuelve a cero.

Celdas (N = 5):

| Celda | Regla |
|---|---|
| C1 | Tendencia + ciclo de 10 (en serie) |
| C2 | Tendencia sola (cada hora) |
| C3 | Tendencia + ninguna zona a menos de 1% |
| C4 | Tendencia + ninguna zona a menos de 2% |
| C5 | Tendencia + ninguna zona a menos de 5% (el número del usuario) |

## Costos

- Spread 0,0083% del precio (6,4 USD a 77.000): el TP necesita que el precio medio recorra 0,13% + spread y el SL salta un spread antes.
- Swap de las compras: 0,0231% del precio por día (0,178 USD por día con 0.01 a 77.000). Las ventas no pagan.
- Estrés (solo diagnóstico): spread ×2.

## Métrica, prueba y aprobación

- Unidad: resultado neto por trade en % del precio. Se informa también en USD con 0.01 a 77.000 (× 7,7).
- Prueba: bootstrap por día de entrada, 2000 iteraciones, semilla 7, p de una cola (media ≤ 0).
- **Aprueba el train:** n ≥ 200, media neta > 0, p < 0,002 (0,01 / 5), profit factor > 1.
- **Confirma el holdout:** media neta > 0 y p < 0,05.
- Si una celda confirma: demo con parámetros congelados, 100 operaciones. Si ninguna pasa: se descarta, sin variantes sobre estos datos.

## Diagnósticos (se informan, no deciden)

- Acierto contra la moneda, SL/(TP+SL), por celda.
- Ciclo: acierto de la operación que sigue a k ganadoras seguidas (k = 0…9).
- Compras y ventas por separado. Estrés a spread ×2.

## Enmiendas

Ninguna.
