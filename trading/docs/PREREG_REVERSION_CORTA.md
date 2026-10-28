# Pre-registro: reversión de corto plazo en BTC con guard, una sola corrida en el holdout

Escrito 2026-10-27, **antes de mirar el holdout**. El script (`analyze_reversal_holdout_prereg.py`) guarda el SHA-256 de este archivo. También se niega a correr si ya existe su salida: la prueba es **una sola vez**.

## Hipótesis (idea del usuario)

Si BTC se movió en los últimos minutos más de lo normal para esa hora, tiende a devolver parte en los 15–30 minutos siguientes. Por eso se opera en contra del movimiento, salvo que el movimiento sea brusco o venga de velas seguidas del mismo lado: ahí puede ser una ruptura de verdad.

## Por qué se prueba directo en el holdout

La idea viene de dos pistas del **train** (oct-2024 → ene-2026), así que probarla en el train sería circular:

- Flujo agresor de BTC: ir en contra dio +0.87 bps brutos a 30 min (`PREREG_FLUJO_AGRESOR.md`).
- Auditoría de módulos de 12 crypto: el momentum corto perdía −1.76 bps a 15 min (`ESTABLISHED_FACTS.md`).

El holdout (2026-02-01 → 2026-09-30) no se miró en datos de Binance. Hubo un contacto menor que se declara: el zigzag del 2026-10-27 usó velas de Exness de may–oct 2026, que es otro feed y otra regla.

## Datos

- Binance spot `BTCUSDT` 1m (`logs/binance/flow/BTCUSDT_1m_flow.npz`).
- Enero 2026 sirve solo de base para la medida de "lo normal": ahí no hay trades.
- Trades: entradas desde 2026-02-01 00:00 hasta 2026-09-30 UTC.
- Ejecución supuesta en `BTCUSDm` de Exness al mismo precio.

## Regla (todo fijo)

- **Movimiento:** `M = cierre[t] / cierre[t−N] − 1`, con N ∈ {5, 15} minutos y velas continuas.
- **Lo normal:** la mediana de |M| a la misma hora UTC en los 30 días previos (sin mirar adelante, recalculada cada día).
- **Disparo:** |M| > 1.5 × lo normal.
- **Guards** (no entra si se cumple alguno):
  1. **Brusco:** |M| > 3 × lo normal.
  2. **Seguidas:** al menos el 80% de las N velas de 1 minuto cerraron del lado del movimiento.
- **Lado:** en contra de M. Si subió vende; si bajó compra.
- **Entrada y salida:** entra en la apertura de t+1 y sale al cierre de t+H, con H ∈ {15, 30} minutos. Sin SL ni TP: la salida es por tiempo. Una posición a la vez.

Celdas: N {5, 15} × H {15, 30} = **4**.

## Costos de Exness

- Spread de ida y vuelta: 0.0118% del precio.
- Swap: 0.0222% por cada 00:00 UTC cruzado, solo en largos.
- Comisión: 0.
- Estrés informativo: spread 2×.

## Vara (una sola prueba, 4 celdas)

Una celda **pasa** si cumple todo:

1. n ≥ 200
2. media neta > 0
3. p < 0.01 / 4 = 0.0025 (bootstrap por día, 2000 iteraciones, semilla 20261027, una cola)
4. PF neto > 1

Si pasa alguna celda: va a forward en la demo de Exness, por al menos 4 semanas o 200 trades, con los mismos parámetros. Después necesita gate dual y una nota de risk-quant antes de cualquier dinero real.

Si no pasa ninguna: la reversión corta queda descartada en BTC, y no queda historia limpia de Binance para otra versión. Cualquier variante (otro umbral, otro guard, otra tenencia) se prueba solo con datos nuevos: forward o el libro de órdenes que se está grabando.

## Diagnósticos (no deciden)

- Bruto.
- Por lado.
- Por hora UTC.
- Cuántas señales sacó cada guard y qué habrían dado esos trades.
- El mismo cálculo sin guards.

No se elige la celda ni el guard por estos números.
