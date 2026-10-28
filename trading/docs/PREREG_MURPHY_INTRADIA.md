# Pre-registro: regla Murphy de retroceso en BTC M5 / M15 con costos de Exness

Escrito 2026-10-27, **antes de correr nada**. El script (`analyze_murphy_intraday_prereg.py`) guarda el SHA-256 de este archivo en su salida: si el archivo cambia después de mirar, el hash no coincide. No se cambia ningún número de acá por lo que muestre el train.

## Hipótesis

La única regla del lector Murphy (`trading/domain/murphy_rule.py`, `retroceso_menor`), aplicada sin cambios a velas de 5 y 15 minutos de BTC, tiene retorno neto positivo después del spread y el swap de Exness.

La regla: entrar a favor de la tendencia intermedia mientras la menor corrige en contra y el retroceso no pasó los dos tercios. Salir si el cierre cruza el último pivote (stop estructural), si pasa los dos tercios, si cambia el régimen de la intermedia o si choca con la principal. Lateral no opera.

Por qué vale la pena: en Exness el spread de BTCUSDm es 0.17x el movimiento mediano a 5 min y 0.10x a 15 min (`analyze_exness_cost_vs_move.py`, 2026-10-27). El costo ya no hace imposible el horizonte. Lo que falta saber es si la regla acierta la dirección.

## Datos

- Binance spot `BTCUSDT` 1m, 2024-10-01 → 2026-09-30 (`logs/binance/BTCUSDT_1m.npz`), como proxy del CFD `BTCUSDm`. Agregado a M5 y M15 alineado a UTC. Una vela con minutos faltantes se arma con los que hay; una vela sin ningún minuto no existe.
- **Train:** hasta 2026-01-31 23:59 UTC (por fecha de entrada).
- **Holdout:** 2026-02-01 → 2026-09-30. Se abre **solo** si el train pasa, y solo para la temporalidad que pasó. Este holdout ya estaba reservado "para una hipótesis distinta" en `ESTABLISHED_FACTS.md`: esta es distinta (otra regla, otro bróker) y lo consume. No se reusa para variantes de esta regla.

## Regla, sin parámetros nuevos

- `read_degrees` con los valores por defecto del código: grados de 21 / 90 / 180 **barras**, `wing` 2, `touch_tol_frac` 0.005, escala lineal. Se le pasan las últimas 180 barras cerradas (los grados solo miran esas).
  - Las barras quedan iguales aunque la vela sea de 5 o 15 minutos: menor ≈ 1.75 h / 5.25 h, intermedia ≈ 7.5 h / 22.5 h, principal ≈ 15 h / 45 h. No se reescala a "3 semanas / 3 meses / 6 meses" en minutos: eso sería elegir otro parámetro.
  - La tolerancia de 0.5% se pensó para el diario. En M5 es ancha. Se deja igual a propósito y se anota como limitación.
- Entrada y salida: `signal_from_reading` y `exit_reason` de `murphy_rule.py`, sin tocarlos.
- Señal al cierre de la vela i; llena en la apertura de i+1. Una posición a la vez por temporalidad.
- Stop: el pivote previo de la intermedia (`apoyo_previo.ultimo`), revisado dentro de cada vela con `stop_fill` (un hueco a través del stop llena en la apertura). Sin take profit: la regla del capítulo no tiene.
- Si la vela toca el stop y además hay razón de salida, gana el stop.

## Costos

- Precio Binance = precio medio.
- **Spread de ida y vuelta: 0.0118% del precio**, la mediana del campo `spread` de las velas M1 de Exness en 30 días. Es más alto que la mediana de ticks (0.0080%): se elige el conservador. Mitad en la entrada y mitad en la salida, en **todas** las salidas, incluido el stop.
- **Swap:** largos pagan 0.0222% del precio por cada 00:00 UTC cruzado (−1786.5 puntos × 0.01 en BTCUSDm). Cortos 0.
- Comisión: 0 (cuenta Standard; no verificado por API).
- Estrés informativo (no decide): spread 2× (0.0236%).

## Métrica y test

- Unidad: retorno neto por trade en puntos básicos del precio de entrada. Se informa también el múltiplo R.
- Test: bootstrap por día calendario de entrada (se remuestrean días con reemplazo), 2000 iteraciones, semilla 20261027. p de una cola: fracción de medias remuestreadas ≤ 0.

## Vara (fijada ahora)

**Pasa el train** una temporalidad si cumple todo:

1. n ≥ 100 trades
2. media neta > 0
3. p < 0.005 (0.01 con Bonferroni por 2 temporalidades)
4. profit factor neto > 1

**Confirma el holdout** si la media neta es > 0 con p < 0.05.

Si una temporalidad pasa los dos: **no va a vivo**. Sigue forward en demo Exness, gate dual (Vibe + QuantConnect) y nota de risk-quant.

Si no pasa el train: la regla queda descartada en intradía para BTC, el holdout no se abre y se anota en `ESTABLISHED_FACTS.md`.

## Diagnósticos (se informan, no deciden)

- Bruto (sin spread ni swap)
- Por lado (BUY / SELL)
- Por motivo de salida
- Duración mediana
- El lado opuesto de cada señal (con los mismos costos)

Ninguno de estos sirve para elegir una variante. Si el lado opuesto gana, eso no habilita invertir la regla: sería una hipótesis nueva sobre los mismos datos.

## Enmienda 1 (2026-10-27, antes de la segunda corrida)

Hash del texto original, sin esta enmienda: `7a718e9be16eb8241cc9ef74bf1022cdbb1e47e0f9a830ccb18cfc9c1f12a6d6`.

**Qué pasó.** La primera corrida dio 0 trades en M5 y M15. No se vio ningún retorno porque no hubo trades. La regla pedía que el régimen de la menor (21 barras) fuera opuesto al de la intermedia (90). Las dos ventanas terminan en la misma vela y usan el mismo detector de pivotes, así que sus últimos dos picos y valles coinciden: la menor sale igual que la intermedia o `insuficiente`, nunca opuesta. Pasa en cualquier temporalidad: 0 de 3153 lecturas diarias de BTC (Binance, 2017–2026) la tienen opuesta.

**Cambio (decidido por el usuario).** La corrección menor **es** el retroceso del último impulso de la intermedia: `0 < frac_cierre ≤ 2/3`. Se saca el requisito de régimen menor opuesto (`menor_no_corrige`). Es la lectura de Murphy: la corrección secundaria devuelve entre un tercio y dos tercios del movimiento previo. No agrega parámetros: el retroceso ya se medía y ya se exigía.

Se cambia en `trading/domain/murphy_rule.py`, así que el lector diario Murphy también usa la regla enmendada. Sigue exigiendo que la menor tenga barras suficientes.

Todo lo demás queda igual: datos, corte, costos, vara, diagnósticos y el holdout cerrado hasta que el train pase.

Es la única enmienda permitida para esta hipótesis. Si esta corrida no pasa, la regla queda descartada en intradía para BTC: no hay enmienda 2 sobre estos datos.
