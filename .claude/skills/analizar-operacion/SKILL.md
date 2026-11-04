---
name: analizar-operacion
description: Analiza una operación manual de BTC que el usuario quiere abrir (o ya abrió) con todas las variables que miran los bots de la demo, y dice qué factores del mercado están a favor y cuáles en contra, sin recomendar comprar ni vender. Usala cuando el usuario comparte una captura de la ventana de orden o del gráfico de MetaTrader, describe un ticket ("quiero vender a mercado", "abrí el ticket que termina en..."), o pregunta "qué opinás de esta operación", "está a favor el mercado", "la abro?", aunque no nombre la skill.
---

# Analizar una operación

El usuario opera BTC a mano en su cuenta real de Exness y trae cada operación antes de abrirla.
Tu trabajo es leer el mercado como lo leen los bots y poner los números de la cuenta sobre la
mesa. **No das consejos de compra o venta**: decís qué está a favor, qué en contra y cuánto
arriesga; la decisión es suya. Nunca mandes, cambies ni cierres órdenes en ninguna cuenta.

## 1. Sacar la orden de la captura o del mensaje

De la ventana de orden o del gráfico, anotá:

- lado: el botón que va a tocar (Sell/Buy by Market) o, si no lo dice, el que encaja con SL y TP
  (SL arriba y TP abajo = venta);
- lote, SL y TP;
- precio de entrada si es pendiente (limit/stop);
- posiciones que ya se ven abiertas (línea "SELL 0.01 at ..." en el gráfico, pestaña Trade).

Si falta algo que cambia el resultado (el lado, el SL), preguntalo en una línea; si no, seguí.

## 2. Leer el mercado y la cuenta

Desde la raíz del repo:

```bash
python analizar_operacion.py --lado sell --lote 0.01 --sl 80275.95 --tp 78175.84 --cuenta
```

`--precio X` para una orden pendiente; sin `--lado` da solo el mercado. Los precios vienen del
terminal demo (el mismo BTC) y `--cuenta` solo **lee** el terminal real (balance, posiciones,
órdenes pendientes). El script no tiene ninguna llamada que mande órdenes.

Qué trae y cómo leerlo:

| Bloque | Qué es | A favor / en contra |
|---|---|---|
| `zonas` | Techos y pisos del bot: H1 de 60 días y M30 de 48 h. Adentro, la más cerca arriba y abajo, con distancia en % | Vender debajo de un techo o comprar arriba de un piso va a favor. Vender encima de un piso intacto o comprar debajo de un techo intacto va en contra (lección L1) |
| `rupturas_48h` | Para cada zona cercana: `intacta`, `en_evaluacion`, `ruptura_falsa_volvio` o `ruptura_confirmada` (2% y 24 cierres H1 afuera) | Una ruptura falsa que volvió empuja de vuelta hacia adentro. Un piso confirmado roto pasa a ser techo |
| `tendencia_ema20` | Precio contra la EMA 20 en M1 a MN1, más el color de las últimas 4 velas | M1/M5/M15 dan la dirección; M30/H1/H4 confirman; D1/W1/MN1 son el fondo. Contá cuántos van con la operación |
| `velas_m15` | Rango de las últimas 4 M15 contra 2,5× la mediana de las 96 previas y el movimiento de 4 h | `vela_grande` o `disparo_4h` = mercado que sale disparado, en contra de un TP chico |
| `dia_hoy` | Apertura, máximo, mínimo y rango del día | Un día de rango grande (3%+) se mueve más que el SL pensado |
| `bots` | Lado de la EMA 20 D1 de ayer y qué harían ahora TP chico C1 y TP chico lateral | Si el bot lateral entraría del mismo lado, la operación cumple su regla; si no, decí qué la frena |
| `ath` | Distancia al máximo histórico | Solo dato; no lo uses a favor ni en contra |
| `cuenta` | Balance, equity, margen libre, posiciones, pendientes, precio donde el equity llega a 0 | Riesgo total y liquidación |
| `orden` | USD si toca TP y SL, % del precio del SL, acierto necesario, riesgo junto con las del mismo lado | Ver punto 4 |

## 3. Leer la base de conocimiento

Leé `trading/docs/BASE_CONOCIMIENTO.md` (local, fuera de git): el checklist y la tabla de
lecciones. Citá la lección que se parezca a esta operación (por ejemplo L13 para un TP chico con
SL amplio, L10 para mucho volumen sin SL). Si el archivo no está, seguí sin él y decilo.

## 4. Los números que siempre van

- **USD si toca el SL y si toca el TP**, en dinero de la cuenta (0.01 lote = 1 USD cada 100 de
  precio). Hablá en USD de la cuenta, no en puntos ni en "pips".
- **Acierto necesario** = SL / (SL + TP). Explicá que es lo que da tirar una moneda con esos
  niveles: para ganar hay que superarlo de forma sostenida.
- **Riesgo junto**: si ya hay posiciones del mismo lado con SL parecido, funcionan como una sola
  operación más grande. Dalo en USD y en % del balance.
- Sin SL: decilo como alerta y usá el precio de equity 0 como el peor caso.
- Órdenes pendientes con su propio riesgo, si las hay.

## 5. Alarmas del checklist

Marcá las que apliquen: abrir para recuperar una pérdida, subir el volumen para recuperar
rápido, abrir sin SL cerca de una zona, abrir contra la tendencia, sumar una segunda posición del
mismo lado mientras la primera pierde.

## Formato de la respuesta (en español, corto)

```
**La orden:** SELL 0.01 a mercado (~78.306), TP 78.176, SL 80.276.
+1,30 si toca el TP, −19,70 si toca el SL; hay que acertar el 94%.

| Punto | Qué veo |
|---|---|
| Mapa | ... |
| Ruptura | ... |
| Tendencia | ... (cuántos marcos a favor y cuáles en contra) |
| Velas M15 | ... |
| Bots | ... |
| Cuenta | ... |
| Alarmas | ... |

**A favor:** ... **En contra:** ... La decisión es tuya.
```

Terminá con el balance de factores a favor y en contra, sin "abrila" ni "no la abras".

## Después

No actualices `BASE_CONOCIMIENTO.md` por analizar. Solo cuando el usuario cuente que abrió la
operación y cómo terminó: agregá la fila al diario y, si deja algo nuevo, una lección.
