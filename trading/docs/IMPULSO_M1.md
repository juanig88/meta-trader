# Impulso M1 (demo Exness, desde 2026-10-30)

> **Desde 2026-10-31 la única entrada prendida es la ruptura falsa (magic 261033, `imp_falsa`).** El impulso M1 (261030/261031) y el rechazo (261032) quedan apagados (`BOT_IMPULSO_IMPULSE_ENABLED=0`, `BOT_IMPULSO_REJECTION_ENABLED=0`) porque perdieron en la demo y en todas las simulaciones. Ver la sección "Ruptura falsa".

Regla sacada de las operaciones manuales del usuario del 29 y 30/10 en BTCUSDm (5 de 5 ganadoras, la más clara es la 390018827 de las 14:40 UTC). Corre **solo en la demo** `Exness-MT5Trial11` y nunca en la cuenta real.

- Código: `trading/domain/impulso_m1.py` (regla pura) e `impulso_m1.py` (runner).
- Tests: `tests/test_impulso_m1.py`.
- Log: `impulso_trades.log`, un JSON por línea.
- Lecciones y diario de operaciones reales y del bot: [BASE_CONOCIMIENTO.md](BASE_CONOCIMIENTO.md). Se actualiza solo cuando se abre una orden y se ve su resultado. Es solo local (está en `.gitignore` porque tiene datos de la cuenta real).

## Entrada

Se evalúa cada 2 segundos y todo se mira en el momento de decidir, nunca en retrospectiva:

1. **M1 lateral y después un disparo.** Las últimas 3 velas M1 cerradas cierran todas para el mismo lado, y el empuje (apertura de la primera → cierre de la tercera) es al menos el rango alto-bajo de las 12 velas anteriores.
2. **El minuto 4 sigue.** El precio actual pasó el cierre de la tercera vela.
3. **Las velas en formación acompañan.** En al menos 4 de 5 temporalidades (M5, M15, M30, H1, H4), el precio está del lado del trade respecto de la apertura de la vela que se está formando.
4. Pausa de 15 minutos entre señales. Se saltea si el spread supera 30 USD por BTC o si existe `impulso_kill_switch.halt`.

## Stop y salidas

Cada señal abre dos posiciones de 0.01 lote, una por variante, para comparar las salidas sobre las mismas entradas.

**El stop es igual para las dos:** del otro lado del rango lateral más el spread. En un long va debajo del mínimo de esas 12 velas; en un short, arriba del máximo.

| Magic | Variante | Take profit | Salida anticipada |
|---|---|---|---|
| 261030 | `imp_fijo` | 2 USD | no |
| 261031 | `imp_fade` | 5 USD | si gana más de 0.30 USD y 2 velas M1 seguidas no hacen máximo nuevo (mínimo en shorts) o cierran en contra |

## Filtros de zona y de agotamiento (desde 2026-10-30 19:10 UTC)

Se agregaron después del primer trade del bot. A las 18:53 vendió en 76.180, adentro del piso de 75.5k–76.5k y justo después de una vela M1 de 299 USD (3.8x lo normal). Enseguida rebotó a 76.689. Código: `trading/domain/rebound_zones.py`.

**Zonas de rebote:**

- **Automáticas** (se recalculan cada 10 minutos sobre velas H1 de 60 días):
  - swings de 12 velas H1 para cada lado;
  - con un rebote de al menos 2% en las 24 horas siguientes;
  - agrupados dentro de 0.8%;
  - con 2 o más toques separados por al menos un día.
  - Quedan unas 14 zonas. El 30/10 la cercana era un piso entre 75.933 y 76.453, con 5 toques.
- **Recientes** (`BOT_IMPULSO_RECENT_ZONES=1`; en el log, `origin: m30_48h_reciente`): para zonas que se están formando y todavía no tienen días de toques. Se agregaron porque el usuario las ve en M30/H1.
  - Usan velas M30 de las últimas 48 h: swings de 1.5 h, rebote de al menos 0.7% en las 6 h siguientes, toques separados por 2 h o más, agrupados dentro de 1.2%.
  - El 30/10 encontraron el techo **77.738–78.660** y el piso **76.321–77.103**, 3 toques cada uno. Aparecieron después de que se rompiera el canal alcista con la caída del 29/10 a las 20:00.
  - Bloquean igual que las de 60 días. Comparar en unos días cuáles se confirmaron.
- **Manuales:** se cargan en `impulso_zonas.txt`, una por línea (`75500-76500`; `#` para comentar). El bot relee el archivo cada minuto, así que no hace falta reiniciarlo. `BOT_IMPULSO_ZONES` en `.env` también funciona, pero ahí cualquier cambio necesita reinicio.

**Libro de zonas (`trading/domain/zone_book.py`, archivo `impulso_zonas_estado.json`).** El bot administra solo todas las zonas: manuales, de 60 días y recientes. Cada 10 minutos, o apenas cambia `impulso_zonas.txt`:

1. **Suma las zonas nuevas que detecta.** Si una se superpone en más de la mitad con otra que ya está, gana la existente; las manuales tienen prioridad.
2. **Reconstruye la historia de cada zona** con 60 días de velas H1 cerradas:
   - **toque:** una vela entra en la zona;
   - **rebote:** el primer cierre afuera después de un toque queda del mismo lado del que venía (uno por toque);
   - **ruptura:** cierra del otro lado. Cambia el rol (un piso roto pasa a ser techo) y mide cuánto se movió el precio desde la ruptura.
3. **Le asigna un estado:**
   - `activa`: bloquea.
   - `invertida`: rota en los últimos 7 días; bloquea con el rol nuevo.
   - `debil`: con 3 o más rebotes más rupturas, rebota menos del 40%; no bloquea.
   - `vencida`: sin toques en 7 días y a más de 3% del precio; no bloquea.
4. **Limpia:** olvida las zonas automáticas vencidas hace más de 30 días. Las manuales quedan hasta que el usuario borre la línea.
5. **Avisa por Telegram** (`BOT_IMPULSO_ZONE_TELEGRAM=1`) cuando una zona se rompe, se invierte, se debilita, vence o aparece. En el primer arranque no avisa nada.

**Estado del 30/10 a las 19:40 UTC:** 14 zonas. Por ejemplo:
- piso manual 75.500–76.500: 11 toques, 10 rebotes, 2 rupturas;
- techo 77.738–78.660: invertido por la caída de las 00:00, 2% de caída desde la ruptura;
- techos 79.1k–80.9k: invertidos por la caída del 29/10 a las 20:00;
- las zonas de 82k para arriba: vencidas.

**Qué se actualiza solo:**

- Las zonas automáticas se recalculan cada 10 minutos.
- Las recientes (M30, 48 h) entran y salen a medida que el precio arma o abandona una zona.
- Una ruptura confirmada por el cierre H1 levanta el bloqueo.

Zonas manuales (formato anterior): (rangos separados por coma). Valen como piso y como techo.

**Qué bloquean:**

- **Short:** dentro de un piso, hasta 0.3% por encima, o por debajo mientras la última vela H1 cerrada no haya cerrado bajo el piso. Una vez que cierra abajo (ruptura confirmada), vuelve a vender.
- **Long:** lo mismo con los techos.
- Aplica a las tres variantes. En el log aparece como `block_reason` `zona_piso` / `zona_techo`, con la zona.

**Antecedente:** de 17 rupturas de la zona 76.5k–77k desde 2024, 15 volvieron arriba de 77k (cayeron entre 0.3% y 3.6%). Las otras 2 fueron caídas de más del 20%: febrero y mayo de 2026.

**Vela de agotamiento (solo en el Impulso):** no entra si la última vela del empuje mide al menos `BOT_IMPULSO_SPIKE_MULT=3` veces la mediana de las 12 velas del rango previo.

## Variante rechazo (magic 261032, `imp_rechazo`)

Sale de la orden manual 390117817 del 30/10: un short a las 18:09 en tendencia bajista, después de 3 velas M1 verdes.

**Entrada:**

1. Al menos 4 de 5 velas en formación (M5 a H4) van para un lado.
2. Las 3 velas M1 cerradas anteriores fueron en contra: el rebote.
3. La última vela M1 cerrada vuelve a favor.
4. La M1 en formación ya se mueve a favor.

**Stop:** más allá del extremo del rebote, más el spread.

**Salida:** TP de 2 USD, más la misma salida anticipada por pérdida de fuerza que `imp_fade`.

Tiene su propia pausa de 15 minutos entre señales y se apaga con `BOT_IMPULSO_REJECTION_ENABLED=0`.

**Repetición con velas M1 del 27 al 30/10** (demo, 0.01 lote, sin spread):

- 91 señales, unas 24 por día; reproduce la entrada de las 18:09.
- **Resultado: −7.39 USD**, con 48% ganadoras y riesgo mediano de 0.72 USD.
- Con el spread, unos 0.09 USD por operación, pierde más.

Corre en demo a pedido del usuario. Si después de una semana el neto del bróker de este magic es negativo, se apaga.

## Régimen para el rechazo (desde 2026-10-31, `trading/domain/regime.py`)

Regla del usuario: operar contra el piso o el techo, y frenar todo cuando se rompen hasta que el mercado arme uno nuevo. Estado en `impulso_regimen.json`; en el log, `IMPULSO_REGIME`.

1. **Tendencia:** estructura de máximos y mínimos (swings de 3 velas) de las velas cerradas H4, D1, W1 y MN1. Si 3 de las 4 coinciden, es alcista o bajista; si no, es rango.
2. **Lado (desde 2026-10-31 01:00 UTC):** solo a favor de la **tendencia de fondo**, que se sostiene: sigue bajista aunque haya rebotes hasta que 3 de las 4 temporalidades marquen alcista, y al revés. Si 3 de 4 están en rango, mantiene la última tendencia. Sin tendencia previa, no opera. Al arrancar sin memoria, la reconstruye día por día con los últimos 120 días; el 31/10 salió bajista. Estado en `held_trend` / `held_since` de `impulso_regimen.json`; Telegram avisa el cambio con "🔄 CAMBIO DE TENDENCIA".
   - Repetición del 1 al 31/10: con la tendencia sostenida (solo ventas) dio 14 trades y −11.59 USD. Comprando en el piso y vendiendo en el techo en rango dio −8.14. El lado al azar con la tendencia sostenida tuvo una mediana de −1.66: tampoco hay ventaja medible.
3. **Piso y techo operativos:** el piso más cercano debajo del precio y el techo más cercano arriba, sacados del libro de zonas. Una zona manual es piso si el precio está arriba de su mitad y techo si está abajo.
4. **Stop:** 0.1% más allá del piso (compra) o del techo (venta). Si eso arriesga más de `BOT_IMPULSO_REJ_MAX_RISK_USD=8`, no abre (`riesgo_mayor_al_tope`). TP de 2 USD y salida anticipada, sin cambios.
5. **Pausa:** el bot frena (`regimen_pausa`) cuando una vela H1 cierra más allá del piso o del techo operativos, o cuando un rechazo se cierra por SL. Avisa por Telegram.
6. **Reanudación:** solo cuando se arma un piso o techo nuevo con velas H1 posteriores a la pausa: 2 toques separados por 4 h o más, con rebote de 1% en 12 h. Avisa por Telegram.
7. Si el precio se aleja y no vuelve a tocar el piso ni el techo, el stop queda a más de 8 USD y no abre.

Otros motivos de bloqueo en `RECHAZO_SIGNAL`: `contra_tendencia` y `sin_piso_techo`.

**Impulso (261030/261031), desde 2026-10-31:** también respeta la pausa y la tendencia (`regimen_pausa`, `contra_tendencia` en `IMPULSO_SIGNAL`). Su stop sigue siendo el del rango lateral y un SL del Impulso no pausa: la pausa por SL es solo para pérdidas contra el piso o el techo.

**Telegram:** al arrancar y cada vez que cambia la tendencia, el piso, el techo o la pausa, manda el mapa: precio, tendencia con el voto de H4/D1/W1/MN1, techo y piso con la distancia, y si está operando o en pausa.

**Zonas manuales:** el bot solo lee `impulso_zonas.txt` y nunca lo escribe. Las zonas nuevas que se forman después de una pausa se guardan en `impulso_regimen.json`, no en el txt.

**Repetición del 1 al 31/10** (M1, 0.01 lote, spread real; en 30 días):

- 27 trades, **−7.12 USD**, 78% ganadoras, ganancia promedio 1.01, pérdida promedio −4.72, peor racha −15.3.
- Estuvo en pausa 585 de las 720 horas.
- Con el lado al azar, en las mismas condiciones, la mediana fue −5.35 (de −16.4 a +12.2): no hay ventaja medible.
- Del 16 al 31/10: 8 trades, −1.10 USD.
- Sin el régimen, con el stop en la zona y el mismo tope de 8 USD, perdía −31.4 USD en los 30 días.
- El +52.9 USD de la primera repetición (16 al 31/10, stop en la zona sin tope) venía de trades que arriesgaban más de 8 USD.

## Ruptura falsa (magic 261033, `imp_falsa`, desde 2026-10-31)

Regla del usuario del 31/10, en `Regime.on_h1_bar` (`trading/domain/regime.py`). Se evalúa una vez por cada H1 cerrado.

1. **Sospecha.** Una vela H1 cierra del otro lado de una zona del libro (piso hacia abajo, techo hacia arriba). El bot entra en **modo evaluación** y no abre nada.
2. **Ruptura confirmada.** Lleva **24 cierres H1 afuera** y el precio está a más del **2%** del borde. Se pausa hasta que se forme un piso o techo nuevo (2 toques con rebote, igual que la pausa del régimen). Si pasaron 24 horas pero no llegó al 2%, sigue evaluando.
3. **Ruptura falsa.** Un H1 vuelve a cerrar adentro de la zona antes de confirmarse. Se opera el regreso **solo a favor de la tendencia de fondo**. En bajista, solo se vende cuando un techo se rompió hacia arriba y el precio volvió; en alcista, solo se compra cuando un piso se rompió hacia abajo y el precio volvió.
   - **SL:** 0.1% más allá del punto más lejano que tocó la ruptura falsa.
   - **TP:** la zona siguiente, el techo del piso de abajo en una venta o el piso del techo de arriba en una compra. Sin zona objetivo, no entra.
4. **Riesgo.** El volumen es siempre **0.01**; el runner topea `BOT_IMPULSO_LOT` en 0.01. La pérdida máxima por trade es `max(4 USD, 50% de lo ganado)`, con lo ganado medido como el balance menos 200 (`BOT_IMPULSO_START_BALANCE`). Si el SL cuesta más que eso, no entra.
5. **Pausa tras perder.** Si la orden cierra por SL, se pausa hasta que se forme un piso o techo nuevo.
6. Una posición a la vez. Telegram avisa cuando empieza una evaluación (🔎), cuando la ruptura resulta falsa (↩️) y cuando se confirma o se pausa (🛑). El mapa muestra "EVALUANDO posible ruptura…" mientras dura.

Log: `FALSA_SIGNAL` con `block_reason` (`contra_tendencia`, `sin_zona_objetivo`, `riesgo_mayor_al_tope`, `precio_pasado_el_extremo`, `spread_caro`, `impulso_halt`, `ya_hay_posicion`) y `IMPULSO_OPEN` con `variant=imp_falsa`.

**Simulación (nov-2024 → oct-2026, M15, a 0.01, con este mismo código):** 36 trades, **+10.02 USD**, 53% ganadoras (ganancia media 3.96, pérdida media 3.84), peor racha −12.71. Por semestre, en múltiplos del riesgo: 2025a −2.2, 2025b −0.8, 2026a +4.8, 2026b +2.0. Entrando en horas al azar con el mismo riesgo y objetivo, la mediana fue +0.65 y esta regla salió mejor que 21 de 30 pruebas: **no hay ventaja demostrada**. Es la variante que menos pierde, no una que gane seguro. Antes, en la misma ventana, entrar en la ruptura misma perdió en todas las variantes, peor que el azar en 30 de 30.


### Volumen real (desde 2026-11-01, `trading/domain/volume.py`)

El volumen de Exness es solo cantidad de cambios de precio (CFD), así que el bot usa los **USDT operados en Binance spot BTCUSDT**, la misma vela H1 en horario UTC. Cada hora agrega a `data/binance/BTCUSDT_1h.csv` (ignorado por git, historia desde 2017-08) las velas que faltan. Si la red falla, registra `VOLUME_ERROR` y sigue sin el dato. La variable es el **volumen relativo de la vela de la ruptura**: lo que se operó en ella dividido por la mediana de las 24 H1 anteriores. Se guarda en la evaluación (`watch.vol_rel`), va en `FALSA_SIGNAL` / `IMPULSO_OPEN` y en el Telegram de la evaluación.

Medido con esta misma regla sobre Binance 2017-08 → 2026-10 (3443 sospechas). El análisis quedó escrito y sellado con SHA-256 antes de ver los datos; el desarrollo es antes del 2024-11-10 y el examen después.

| Volumen de la ruptura | Rupturas falsas (desarrollo) | Rupturas falsas (examen) |
|---|---|---|
| bajo (≤ 0.98x) | 87% | 92% |
| medio (≤ 1.73x) | 84% | 88% |
| alto (> 1.73x) | 74% | 79% |

- **El volumen sí distingue** rupturas falsas de sostenidas, y se repite en los dos períodos.
- **Como filtro de la operación de regreso, se rechazó.** El elegido en el desarrollo (operar solo con volumen bajo) dio −0.06 veces el riesgo por trade en el examen, contra +0.05 sin filtro (permutación p 0.87). Por eso por ahora **solo se registra y se informa, no bloquea**.
- Sin tope de riesgo en USD, la regla completa sobre 9 años dio 957 trades: +0.014 veces el riesgo por trade en el desarrollo y +0.052 en el examen. Es casi cero.

## Seguridad

- El runner se conecta **solo** a la terminal de `BOT_IMPULSO_MT5_PATH`: una segunda instalación de MT5, logueada en la demo. Sin esa ruta no arranca, porque `mt5.initialize()` sin ruta se engancharía a la terminal donde el usuario opera la real.
- Si la cuenta no es demo (`trade_mode != 0`), corta.
- Manda órdenes solo con `BOT_IMPULSO_DEMO_LIVE=1` y sin `--dry-run`.

## Instalar la segunda MT5

1. Bajar el instalador de MT5 desde el sitio de Exness e instalarlo en **otra carpeta**, por ejemplo `C:\MT5-Demo`.
2. Abrirla, loguearse en la demo (`MT5_LOGIN` / `MT5_SERVER` del `.env`) y activar *Herramientas → Opciones → Asesores Expertos → Permitir trading algorítmico*.
3. Poner `BOT_IMPULSO_MT5_PATH=C:\MT5-Demo\terminal64.exe` en `.env`.
4. `python impulso_m1.py --dry-run` para ver señales sin operar; `python impulso_m1.py` para operar en la demo.

## Avisos de noticias (`news_watch.py`)

Solo avisa por Telegram y no opera: el Impulso M1 no lo lee.

**Cada ciclo (3 minutos por defecto):**

1. Lee el RSS de CoinDesk, Cointelegraph, Decrypt y The Block.
2. Se queda con los titulares nuevos, de menos de 90 minutos y relevantes para BTC: palabras como hack, ETF, SEC, Fed o Binance.
3. Le pregunta a Gemini (`GEMINI_API_KEY`) si cada uno probablemente hace subir o bajar BTC, con fuerza de 1 a 3.

**Qué avisa:**

- Un solo mensaje por ciclo, con hasta 5 titulares de fuerza ≥ 2 y el precio de BTC en Binance.
- Gemini descarta como neutral la misma noticia repetida por varias fuentes y lo que ya se avisó.
- Si Gemini falla, usa palabras clave y marca el aviso "(sin IA)".

**Archivos:** `news_seen.json` guarda lo ya visto y `news_alerts.log` el veredicto de cada titular.

**Comandos:**

- `python news_watch.py --once --dry-run` imprime en lugar de mandar.
- `python news_watch.py` queda corriendo.

## Lo que se sabe y lo que no

- **Frecuencia (27 al 30/10):** dispara unas 6 veces por día y reproduce la entrada manual de las 14:40 del 30/10.
- **Repetición con velas M1 de esa misma semana** (23 señales, 0.01 lote, sin spread; el spread es ~0.09 USD por trade):
  - TP fijo: +20.1 USD
  - salida dinámica: +17.0 USD
- **Eso no es evidencia.** La regla se escribió mirando esa semana y la muestra es chica. Además, reglas parecidas en BTC M5/M15 dieron edge bruto cero en 2 años (`INTRADIA_EXNESS.md`).
- **Cómo se evalúa:** con una semana de demo, sobre el neto del bróker por magic.
