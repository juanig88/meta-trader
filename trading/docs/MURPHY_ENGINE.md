# Lector Murphy — fase 1 (solo lectura)

Capítulo 4 de Murphy, diario, dos símbolos. Este proceso no manda órdenes. La rotación sigue detenida. El swing Donchian sigue siendo el motor viejo y, en esta terminal Exness, el candado de bróker no lo deja operar.

## Universo

Solo BTC y ETH, en dólares. En la trial de Exness (Standard) los nombres son `BTCUSDm` y `ETHUSDm`. El lector acepta el nombre exacto `BTCUSD` / `ETHUSD` o ese mismo nombre con el sufijo `m`. No usa `BTCUSDTm`, cruces, ni las 79 crypto de XBTFX.

Cuenta aceptada para leer: demo (`trade_mode` 0). Una cuenta real corta la lectura.

## Fases

Cada fase se confirma antes de abrir la siguiente. Confirmar es mirar el resultado y decir que sigue. No se salta.

### Fase 0 — Terminal y símbolos

Qué tiene que verse:

1. El script se conecta a la terminal ya abierta, sin imprimir la clave.
2. El servidor es Exness y la cuenta es demo.
3. Resuelve BTC y ETH, y baja velas diarias cerradas (la vela de hoy no entra).
4. El proceso no contiene `order_send`.

Hecho cuando esos cuatro puntos salen bien en una corrida.

### Fase 1 — Lectura de estructura (este capítulo)

El lector, sobre velas ya cerradas, anota por grado:

- Menor: últimas 21 diarias (unas tres semanas).
- Intermedia: últimas 90 (unos tres meses).
- Principal: últimas 180 (unos seis meses). Si no hay tanta historia, ese grado queda en `sin_datos`. No se inventa una tendencia principal.

Reglas que sí salen del capítulo:

- Alcista: últimos dos picos más altos y últimos dos valles más altos. Bajista: los dos pares en baja. Si no, lateral, y la postura de la intermedia es `afuera`.
- La línea alcista une valles ascendentes; la bajista, picos descendentes. Dos puntos la dibujan (`orientativa`). Un toque más la confirma (`valida`).
- Se usan mechas. Un pinchazo que cierra del lado original no rompe la línea. La ruptura que se anota es dos cierres seguidos del otro lado (`rota`).
- El retroceso del último impulso se mide en un tercio, la mitad y dos tercios. Pasar los dos tercios invalida la premisa de “sigo en la tendencia anterior”.
- Llegar al mínimo anterior es un aviso. Cerrar del otro lado de ese nivel invalida.
- El canal es la paralela desde el primer pico (o valle) prominente. No llegar al otro lado se anota. Cerrar fuera, a favor, se anota como aceleración y el objetivo es el ancho del canal.
- El abanico, si aparece, son tres líneas desde el mismo origen que se van aplanando. La ruptura de la tercera se anota. No es una orden.
- Un nivel atravesado por dos cierres cambia de papel.
- El número redondo se nombra. El stop de una compra iría debajo; el de una venta, arriba. El lector no calcula ni manda ese stop.

Lo que el capítulo no cifra, y por eso va en `parametros` como nuestro, no como del libro:

- `wing` 2: cuántas barras a cada lado confirman un pivote.
- `touch_tol_frac` 0.005: qué tan cerca tiene que pasar un pivote para contar como toque.
- La grilla 1-2-5 del número redondo.

La postura de la fase 1 es `solo_lectura`, `afuera` o `sin_datos`. `envia_ordenes` es siempre falso. Si la principal y la intermedia van en sentidos opuestos, se marca `conflicto_principal` y tampoco se opera.

Hecho cuando, en BTC y en ETH, la postura y la línea que imprime el script coinciden con lo que se ve en el gráfico diario. Si el pivote o la línea no coinciden, se ajusta `wing` o la tolerancia y se vuelve a mirar. No se pasa de fase con una lectura que no reconocemos.

La regla que se mide, y la única, es esta: a favor de la intermedia, en el retroceso de la menor, sin pasar los dos tercios; salida si el cierre cruza el último valle o pico, si pasa esos dos tercios, si el régimen de la intermedia cambia, o si choca con la principal. Lateral: no hay entrada. El retroceso de la menor se lee como retroceso entre 0 y 2/3 del último impulso de la intermedia. No se exige un régimen menor opuesto: nunca aparece, porque las dos ventanas comparten sus últimos pivotes (enmienda 2026-10-27, `PREREG_MURPHY_INTRADIA.md`). El diario llena en la apertura siguiente y descuenta el spread. El swap se anota y no se resta hasta tener la conversión del contrato. No se cambia la regla porque otra etiqueta tenga mejor winrate en el mismo historial.

### Fase 2 — Línea del grado y escala

La línea de un grado tiene que cubrir ese grado. Piso nuestro: 15 diarias para la intermedia y 60 para la principal. Si no hay una recta así de larga, ese grado no hereda la de dos semanas.

La misma recta se calcula en escala lineal y en logaritmo. La regla medida sigue en lineal. El logaritmo se muestra al lado para ver si las anclas se mueven. No se adopta el logaritmo porque en una corrida gane más.

Hecho cuando, en BTC, la ancla de la principal no es la misma recta de dos semanas, y el logaritmo queda escrito al lado.

### Fase 3 — Resto de herramientas, como etiquetas

Sobre las velas reales se anotan, sin convertirlas en otra entrada:

- Rayos a un tercio y dos tercios del impulso, y el abanico de fracciones del mismo impulso.
- Línea interna, usando el cierre del pivote en lugar de la mecha.
- Día de cambio: máximo nuevo y cierre en el cuarto inferior del rango, o el espejo. El cuarto es nuestro.
- Hueco: la barra no solapa a la anterior.
- Media de 50 cierres, para cuando la recta se empina. El 50 es convencional, no está optimizado.
- Razón del volumen de ticks de la última barra contra las 20 anteriores.

Los patrones de cambio y de continuación (capítulos 5 y 6) no se operan en esta regla. Sumarlos y quedarse con el de mejor winrate es buscar en el mismo historial.

Hecho cuando esas etiquetas salen en la lectura de BTC y de ETH y ninguna de ellas abre una orden.

### Fase 4 — Diario en seco con datos del bróker

`murphy_dry.py` baja el diario cerrado de `BTCUSDm` y `ETHUSDm`, mide spread y swap, y recorre la regla. El resultado se parte: todo el historial, y las últimas 60 barras aparte. Sigue sin `order_send`.

Hecho cuando hay un número de operaciones, un winrate y un R medio, con el spread descontado, y ese número no se usa para reescribir la regla.

### Medición H4 / M15 (2026-10-13)

Regla fija antes de ver el número: dirección del H4 (120 velas), entrada en el retroceso del M15 (96 velas) sin pasar los dos tercios. Datos Binance spot `BTCUSDT` y `ETHUSDT`, dos años de M15 (70.080 velas) y H4 (4.380), con volumen y cantidad de trades guardados. La regla no usa todavía ese volumen. Costo Binance: 0,10 % por lado. El spread de Exness es la cotización de la demo en esta corrida, aplicada igual a todo el historial, no el spread de cada vela. Sin órdenes.

| Par | Operaciones | Esperanza R con fee Binance | Tramo final (20 %) | Esperanza R solo con spread Exness |
|---|---|---|---|---|
| BTCUSDT | 3.007 | −0,51 | −0,46 | −0,05 |
| ETHUSDT | 3.150 | −0,36 | −0,32 | −0,16 |

La esperanza es negativa en los dos pares y en el tramo que no se usó para escribir la regla. Esta frase no abre órdenes.

### Fase 5 — Órdenes, recién si el diario se sostiene

El lote sale de la distancia hasta el stop y de un riesgo en dólares que todavía no está fijado. Antes de mandar una orden en la demo hacen falta el diario de la fase 4, el swap ya convertido, y las dos validaciones de siempre. Esta fase no empieza con el lector.

### Fuera de esta versión

- Las 79 crypto, el voto de indicadores y el Donchian como señal de este bot.
- Un modelo de lenguaje decidiendo la orden.
- Operar corto, mediano y largo a la vez en el mismo símbolo.
- Operar en contra desde la línea del canal.
- Cuenta real.

## Dónde está la fase 0

Corrida del 2026-10-13 contra la terminal abierta: servidor `Exness-MT5Trial11`, cuenta demo, 200 diarias cerradas hasta el 2026-10-12. BTC resolvió a `BTCUSDm`, ETH a `ETHUSDm`. `envia_ordenes` salió falso. La fase 1 (si esa lectura coincide con el gráfico) todavía no está confirmada.

## Cómo mirar la fase 1

Con la terminal de Exness abierta y logueada en la demo:

```text
python murphy_read.py
```

Sale un JSON. `envia_ordenes` tiene que ser falso. `simbolos.BTCUSD.broker` es el nombre de la cuenta (`BTCUSDm` en esta trial). La postura que importa es la de `postura` (la intermedia).
