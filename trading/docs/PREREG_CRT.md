# Pre-registro: CRT (Candle Range Theory) en BTC

Escrito 2026-11-05, **antes de correr nada sobre CRT**. El script (`analyze_crt.py`) imprime el SHA-256 de este archivo junto a los resultados y se niega a abrir el holdout dos veces.

## Hipótesis

Idea traída por el usuario (indicador "CRT - Candle Range Theory" de TradingView). Una vela madre marca un rango. La vela siguiente barre un solo lado del rango y cierra de vuelta adentro. Eso anticipa un movimiento hacia el otro lado del rango.

- **CRT bajista:** el máximo de la vela CRT supera el máximo de la madre. Su mínimo no baja del mínimo de la madre. Cierra adentro del rango de la madre.
- **CRT alcista:** lo mismo al revés.
- Las velas que barren los dos lados (outside bars) no cuentan.

## Contacto previo con los datos

- No se corrió ninguna prueba CRT sobre estos datos.
- Lecciones anteriores en este repo:
  - entrar en la ruptura de una zona perdió;
  - operar la vuelta de una ruptura falsa sobre zonas no mostró ventaja (L6, `IMPULSO_M1.md`).
- El año del holdout (2025-11-04 → 2026-11-04) se usó para pruebas de TP chico, pero nunca para una regla de barrida de una vela.
- El 2026-11-04 se comentó con el usuario una barrida diaria (mínimo 75.611) que terminó subiendo: es un caso, no una prueba.

## Datos

- Binance spot `BTCUSDT`, 1h (`data/binance/BTCUSDT_1h.csv`). Las velas **H4** (00, 04, … UTC) y **D1** (00 UTC) se arman agrupando las de 1h. Las salidas se miden con 15m (`BTCUSDT_15m.csv`).
- **Train:** patrones cuya vela CRT cierra entre 2018-01-01 y 2025-11-03.
- **Holdout:** 2025-11-04 hasta el final de los datos. Se abre una sola vez, solo para las celdas que pasen el train.

## Regla (todo fijo)

- **Entrada:** al cierre de la vela CRT, a su precio de cierre. CRT bajista → venta; alcista → compra.
- **SL:** el extremo que barrió la vela CRT. En una venta, su máximo; en una compra, su mínimo.
- **TP**, dos variantes:
  - **T50:** el 50% del rango de la madre.
  - **TOP:** el otro extremo de la madre (en una venta, su mínimo; en una compra, su máximo).
- Si el TP no queda del lado de la ganancia respecto de la entrada, esa operación no se hace. Pasa con T50 cuando el cierre ya cruzó el 50%.
- **Salida:** la primera que toque, TP o SL, con velas de 15m. Si una vela de 15m toca los dos, cuenta como SL.
  - Límite de tiempo: 10 velas del marco del patrón (40 h en H4, 10 días en D1).
  - Si se cumple el límite, cierra al último cierre de 15m.
- Cada patrón es una operación. Las operaciones pueden superponerse.
- **Filtros del indicador**, tres variantes:
  - **B (básico):** cualquier CRT válido.
  - **C50:** el cierre no llega al 50%. En el bajista, cierre > 50%; en el alcista, cierre < 50%.
  - **P50:** el precio no llega al 50%. En el bajista, el mínimo de la CRT > 50%; en el alcista, su máximo < 50%.

Celdas: 2 marcos (H4, D1) × 3 filtros (B, C50, P50) × 2 TP (T50, TOP) = **N = 12**.

## Costos

- Spread 0,0083% del precio (6,4 USD a 77.000).
  - La venta entra al bid y sale al ask.
  - La compra entra al ask y sale al bid.
  - El spread se suma en contra en el TP, en el SL y en la salida por tiempo.
- Swap de las compras: 0,0231% del precio por día. Las ventas no pagan.
- Estrés (solo diagnóstico): spread ×2.

## Métrica, prueba y aprobación

- **Unidad:** resultado neto por operación **en R**, es decir, dividido por la distancia de entrada a SL. Así cada operación arriesga lo mismo, como cuando uno ajusta el lote al SL.
- **Prueba:** bootstrap por día de entrada, 2000 iteraciones, semilla 7, p de una cola (media ≤ 0).
- **Aprueba el train:**
  - n ≥ 100;
  - media neta en R > 0;
  - p < 0,00083 (0,01 / 12);
  - profit factor > 1.
- **Confirma el holdout:** media neta en R > 0 y p < 0,05.
- Si una celda confirma: demo con parámetros congelados. Si ninguna pasa: se descarta, sin variantes sobre estos datos.

## Diagnósticos (se informan, no deciden)

- Acierto contra la moneda: el promedio por operación de SL / (SL + TP), con las distancias medidas desde la entrada.
- **Dirección sin costos:** las mismas operaciones a precio medio, sin spread ni swap. También con el lado invertido, para ver si la señal sabe la dirección.
- Compras y ventas por separado.
- Resultado en % del precio.
- Estrés a spread ×2.
- Salidas por tiempo.
