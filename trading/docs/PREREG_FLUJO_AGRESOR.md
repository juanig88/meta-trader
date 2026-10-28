# Pre-registro: flujo de órdenes agresivas en BTC, 5 a 30 minutos, costos Exness

Escrito 2026-10-27, **antes de bajar los datos**. El script (`analyze_flow_imbalance_prereg.py`) guarda el SHA-256 de este archivo en su salida. No se cambia ningún número de acá por lo que muestre el train.

## Hipótesis

El desbalance entre compras y ventas agresivas (taker) de los últimos minutos en Binance anticipa el retorno de BTC de los minutos siguientes en un tamaño que paga el spread de Exness.

Es información distinta de las velas: no sale de apertura, máximo, mínimo ni cierre. Todo lo que ya se probó sobre velas dio bruto cero (`ESTABLISHED_FACTS.md`, `INTRADIA_EXNESS.md`).

## Datos

- Binance spot `BTCUSDT` 1m de data.binance.vision, 2024-10 → 2026-09. Columnas: cierre, volumen y volumen comprador taker (`taker_buy_base`). Ejecución supuesta en `BTCUSDm` de Exness al mismo precio.
- **Train:** entradas hasta 2026-01-31 23:59 UTC.
- **Holdout:** 2026-02-01 → 2026-09-30. Se abre **solo** para las celdas que pasen el train. Nadie miró todavía ese tramo en datos de Binance. El zigzag de 2026-10-27 usó precios de Exness de may–oct 2026 (otro feed, solo velas): se declara como contacto menor.

## Señal

- Desbalance de N minutos al cierre del minuto t: `I = (2 · Σ taker_buy − Σ volumen) / Σ volumen`, sumado sobre los minutos t−N+1 … t. Va de −1 (todo venta agresiva) a +1 (todo compra agresiva).
- Umbral: el percentil 90 de |I| en los 30 días previos a t (sin mirar adelante). Se recalcula una vez por día UTC con el día anterior incluido. Hay señal si |I| supera ese umbral.
- Dirección, las dos declaradas ahora como celdas separadas:
  - **continuación**: compra si I > 0, vende si I < 0
  - **reversión**: lo contrario
- Entrada: apertura del minuto t+1. Salida: cierre del minuto t+H. Sin stop ni take profit: la salida es por tiempo. Una posición a la vez; mientras hay posición, no se miran señales.

Celdas: N ∈ {5, 15} × H ∈ {5, 15, 30} × dirección ∈ {continuación, reversión} = **12**.

## Costos

- Spread de ida y vuelta: 0.0118% del precio (mediana del campo `spread` de las velas M1 de Exness, el valor conservador)
- Swap: 0.0222% por cada 00:00 UTC cruzado, solo en largos
- Comisión: 0
- Estrés informativo: spread 2×

## Métrica, test y vara

- Unidad: retorno neto por trade en bps del precio de entrada.
- Test: bootstrap por día calendario de entrada, 2000 iteraciones, semilla 20261027, p de una cola (fracción de medias ≤ 0).
- **Pasa el train** una celda si cumple todo:
  1. n ≥ 200
  2. media neta > 0
  3. p < 0.01 / 12 = 0.00083
  4. PF neto > 1
- **Confirma el holdout** si la media neta es > 0 con p < 0.05.

Si una celda pasa los dos: no va a vivo. Sigue forward en demo Exness, gate dual y nota de risk-quant. El tamaño de 10 USD de riesgo máximo por trade se define después, no acá.

## Diagnósticos (no deciden)

- Bruto de cada celda.
- Cota superior de información: retorno medio firmado por sign(I) sobre **todos** los minutos con señal, solapados, sin costos.
- Por lado y por hora UTC.

No se elige otro umbral, otra N ni otra H por estos diagnósticos. Eso sería una hipótesis nueva con datos nuevos.
