**Bot MT5 — Plan de Mejoras**

Análisis basado en 162 operaciones reales · 21 abr – 4 may 2026

Documento técnico para implementación en Cursor

| \-$23.50 P\&L bot solo | 29.9% Win rate global | 4.15× Ratio W/L | 162 Ops analizadas |
| :---: | :---: | :---: | :---: |

# **1\. Diagnóstico: qué está fallando**

El bot genera pérdidas en 3 de cada 4 operaciones. El P\&L positivo del período se debe exclusivamente a 2 operaciones manuales. Sin ellas, el resultado es \-$23.50. Se identificaron 5 problemas concretos:

### **Problema 1 · ETHUSD SELL: 89.6% de pérdida (CRÍTICO)**

| DATOS | 42 de 48 sells en ETH cerraron en pérdida. El 27/04 el bot ejecutó 33 sells en 11 minutos con gaps de 30–70 segundos, perdiendo $0.09 en cada una mientras ETH subía de $2,310 a $2,326. |
| :---: | :---- |

Causa raíz:

* El bot abre sells sin verificar la tendencia H4 del subyacente

* El cooldown de 45min no está activo para ETHUSD → permite re-entradas cada 30 segundos

* El filtro de tendencia (EMA50/EMA200) no bloquea sells en tendencia alcista

### **Problema 2 · SELL global: WR 19.8% vs BUY WR 39.8%**

| DATOS | Las ventas pierden en 4 de cada 5 entradas. El período analizado fue alcista para BTC y ETH. El bot abre sells sin confirmar que la tendencia macro acompaña. |
| :---: | :---- |

Causa raíz:

* El filtro H4 existe en el código pero usa EMA cruce básico sin confirmar con ADX

* No hay distinción entre "precio bajando en tendencia bajista" vs "precio corrigiendo en tendencia alcista"

### **Problema 3 · 100/107 pérdidas terminan exactamente en el SL**

| DATOS | El 93.4% de las pérdidas cierran cuando el SL es tocado. Esto indica que el SL está calibrado dentro del ruido del mercado: el precio toca el SL y luego retoma la dirección original. |
| :---: | :---- |

Causa raíz:

* SL calculado con distancia fija, no basado en ATR del símbolo

* Para ETHUSD con ATR\_M15 de \~744 puntos, un SL de 290 puntos (= el spread) está dentro del ruido normal

### **Problema 4 · Horarios muertos generan $22.14 en pérdidas evitables**

| DATOS | Los horarios 00h–05h y 19h–21h tienen WR \< 15% consistente. Los mejores horarios son 06h–07h (apertura Londres) y 16h–17h (overlap NY–Londres). |
| :---: | :---- |

Distribución horaria de P\&L:

| Franja horaria | Ops | Win Rate | P\&L |
| :---- | :---- | :---- | :---- |
| 06h-07h (Londres open) | 16 | 62.5% | **\+$10.13** |
| 16h-17h (NY overlap) | 44 | 46.6% | **\+$56.94** |
| 22h-23h (NY close) | 8 | 75.0% | **\+$51.82** |
| 00h-05h (Asiatica) | 28 | 14.3% | **\-$14.84** |
| 08h-11h (Media manana) | 10 | 10.0% | **\-$11.34** |
| 19h-21h (Cierre NY) | 26 | 7.7% | **\-$11.01** |

### **Problema 5 · GBPUSD y XAUUSD sin operaciones en 13 días**

| DATOS | 2 de 6 símbolos configurados no generaron ninguna entrada. Si los filtros los bloquean siempre, se está desaprovechando capacidad operativa del bot. |
| :---: | :---- |

# **2\. Mejoras a implementar**

Las siguientes mejoras están ordenadas por impacto estimado en P\&L, de mayor a menor. Cada una incluye el archivo a modificar, la lógica exacta y el código de referencia.

## **Mejora 1 · Filtro de tendencia H4 obligatorio para SELLS**

**Impacto estimado:** \+$13.02 de pérdidas evitadas (todas las sell-loss del período)

**Archivo:** strategy\_core.py  →  función que evalúa señales de entrada

**Lógica a implementar:**

* Antes de aprobar cualquier señal SELL, verificar que la EMA50 H4 esté por debajo de la EMA200 H4

* Condición adicional: ADX H4 \>= 22 (confirma que es tendencia bajista real, no solo cruce)

* Si no se cumplen ambas condiciones → bloquear el SELL y loggear el motivo

* Para BUY: aplicar la lógica inversa (EMA50 H4 \> EMA200 H4)

**Pseudocódigo para Cursor:**

\# En strategy\_core.py, función check\_trade\_direction(symbol, direction):

def is\_trend\_aligned(symbol: str, direction: str) \-\> bool:

    rates\_h4 \= mt5.copy\_rates\_from\_pos(symbol, TIMEFRAME\_H4, 0, 210\)

    closes   \= \[r\["close"\] for r in rates\_h4\]

    ema50    \= calculate\_ema(closes, 50\)

    ema200   \= calculate\_ema(closes, 200\)

    adx\_h4   \= calculate\_adx(rates\_h4, period=14)

    if adx\_h4 \< 22:

        return False  \# mercado lateral, no operar en ninguna dirección

    if direction \== "sell":

        return ema50 \< ema200  \# tendencia bajista confirmada

    elif direction \== "buy":

        return ema50 \> ema200  \# tendencia alcista confirmada

    return False

**Variable de config a agregar en .env:**

TREND\_FILTER\_H4\_ENABLED=true

TREND\_FILTER\_H4\_ADX\_MIN=22

## **Mejora 2 · Cooldown mínimo 15 minutos para ETHUSD**

**Impacto estimado:** \+$2.97 directos (33 re-entradas en 11 minutos el 27/04)

**Archivo:** .env  \+  BOT\_SYMBOL\_CONFIG  →  sin cambio de código

**Cambio en BOT\_SYMBOL\_CONFIG para ETHUSD:**

"ETHUSD": {

  "cooldown\_minutes": 15,   // era 45 pero no estaba activo

  "adx\_min": 28,

  "range\_filter\_enabled": true,

  ...

}

**Verificar adicionalmente que el cooldown se aplique por símbolo, no globalmente. Si la lógica es global, separar el diccionario de last\_trade\_time por símbolo:**

\# En bot\_mt5.py — cambiar:

last\_trade\_time \= 0  \# global

\# Por:

last\_trade\_time: dict\[str, float\] \= {}  \# por símbolo

\# Y en el check:

elapsed \= time.time() \- last\_trade\_time.get(symbol, 0\)

if elapsed \< cooldown\_seconds:

    continue  \# skip este símbolo en este ciclo

## **Mejora 3 · Filtro de sesión horaria**

**Impacto estimado:** \+$22.14 de pérdidas evitadas eliminando horarios muertos

**Archivo:** bot\_mt5.py  →  inicio del loop principal, antes de evaluar señales

**Sesiones recomendadas basadas en los datos reales:**

* Sesión 1 (Londres): 06:00 – 12:00 UTC

* Sesión 2 (NY overlap): 13:00 – 18:00 UTC

* Bloquear completamente: 00:00 – 05:59 y 19:00 – 23:59 UTC

*Nota sobre 22h–23h: aparecen rentables por las 2 operaciones manuales grandes. Sin ellas ese bloque también es negativo. Conservar el bloqueo.*

**Variables de config a agregar en .env:**

TRADING\_SESSIONS=06:00-12:00,13:00-18:00

TRADING\_SESSIONS\_TIMEZONE=UTC

**Pseudocódigo para Cursor:**

\# En bot\_mt5.py, inicio del ciclo del loop:

def is\_in\_trading\_session() \-\> bool:

    now\_utc \= datetime.utcnow()

    sessions \= parse\_sessions(os.getenv("TRADING\_SESSIONS"))

    for start, end in sessions:

        if start \<= now\_utc.time() \<= end:

            return True

    return False

\# En el loop:

if not is\_in\_trading\_session():

    time.sleep(60)

    continue

## **Mejora 4 · SL basado en ATR por símbolo**

**Impacto estimado:** Reducir los 100 SL hits — dar más espacio al precio para respirar

**Archivo:** strategy\_core.py  o  bot\_mt5.py  →  función que calcula el SL antes de enviar la orden

**El problema concreto con ATR vs SL actual:**

| Símbolo | ATR M15 (pts) | SL actual estimado | SL sugerido (1.5× ATR) |
| :---- | :---- | :---- | :---- |
| BTCUSD | 16,053 | \~8,000 | **\~24,080** |
| ETHUSD | 744 | 290 | **\~1,116** |
| GBPUSD | 56 | \~30 | **\~84** |
| USDJPY | 65 | \~30 | **\~98** |
| NZDJPY | 53 | \~20 | **\~80** |
| XAUUSD | 763 | \~300 | **\~1,145** |

**Pseudocódigo para Cursor:**

\# Nueva función en strategy\_core.py:

def calculate\_atr\_sl(symbol: str, tf=TIMEFRAME\_M15, period=14,

                     multiplier=1.5) \-\> float:

    rates \= mt5.copy\_rates\_from\_pos(symbol, tf, 1, period \+ 10\)

    trs \= \[\]

    for i in range(1, len(rates)):

        h, l, pc \= rates\[i\]\["high"\], rates\[i\]\["low"\], rates\[i-1\]\["close"\]

        trs.append(max(h-l, abs(h-pc), abs(l-pc)))

    atr \= sum(trs\[-period:\]) / period

    return atr \* multiplier

\# Al construir la orden, reemplazar SL fijo por:

sl\_distance \= calculate\_atr\_sl(symbol)

sl\_price \= entry\_price \- sl\_distance  \# BUY

sl\_price \= entry\_price \+ sl\_distance  \# SELL

**Variable de config a agregar:**

ATR\_SL\_ENABLED=true

ATR\_SL\_MULTIPLIER=1.5   \# ajustable por símbolo via BOT\_SYMBOL\_CONFIG

ATR\_SL\_TIMEFRAME=M15

## **Mejora 5 · Activar market\_regime.py \+ position\_sizer.py**

**Impacto estimado:** Amplificar wins en tendencia y reducir pérdidas en lateral

**Archivos:** market\_regime.py  \+  position\_sizer.py  →  ya provistos

**Integración en bot\_mt5.py (3 líneas de cambio):**

\# ── Al iniciar, después de connect\_mt5() ──────────────────

from market\_regime import MarketRegimeDetector

from position\_sizer import PositionSizer

regime\_detector \= MarketRegimeDetector(cfg)

sizer           \= PositionSizer(cfg, verbose=True)

\# ── En el loop principal, bloque TRADING ──────────────────

regimes \= regime\_detector.check\_all(cfg)  \# cada 15min auto-throttled

\# ── Al construir cada orden ───────────────────────────────

regime   \= regime\_detector.get\_regime(symbol)

sl\_pts   \= calculate\_atr\_sl(symbol)  \# Mejora 4

lot      \= sizer.calculate(symbol, sl\_pts, regime)

request \= {

    "symbol": symbol,

    "volume": lot,      \# antes era hardcoded 0.01

    ...

}

**Tabla de multiplicadores aplicados automáticamente:**

| Símbolo | Risk base | TRENDING | UNCERTAIN | RANGING |
| :---- | :---- | :---- | :---- | :---- |
| BTCUSD | 0.8% | ×1.40 | ×1.00 | ×0.40 |
| ETHUSD | 0.8% | ×1.40 | ×1.00 | ×0.40 |
| GBPUSD | 1.2% | ×1.60 | ×1.00 | ×0.50 |
| USDJPY | 1.2% | ×1.60 | ×1.00 | ×0.50 |
| NZDJPY | 1.0% | ×1.50 | ×1.00 | ×0.45 |
| XAUUSD | 1.0% | ×1.50 | ×1.00 | ×0.45 |

## **Mejora 6 · Verificar y activar GBPUSD y XAUUSD**

**Archivo:** .env  →  lista de símbolos activos

**Checklist para Cursor:**

1. Confirmar que GBPUSD y XAUUSD están en la variable BOT\_SYMBOLS del .env

2. Bajar temporalmente ADX\_MIN a 20 solo para estos dos símbolos y observar si generan señales

3. Verificar que el spread filter no los bloquea: GBPUSD max spread sugerido \= 18 pts, XAUUSD \= 12 pts

4. Dejar el bot varios días en marcha con logs y confirmar que el engine procesa señales y gestión como esperás

# **3\. Resumen ejecutivo de cambios**

| \# | Mejora | Archivo(s) | Esfuerzo | Impacto estimado |
| :---- | :---- | :---- | :---- | :---- |
| 1 | Filtro tendencia H4 para SELLs | strategy\_core.py | Media | \+$13.02 evitados |
| 2 | Cooldown 15min ETHUSD | .env | Baja | \+$2.97 directos |
| 3 | Filtro sesión horaria | bot\_mt5.py | Media | \+$22.14 evitados |
| 4 | SL basado en ATR | strategy\_core.py | Media | Reducir SL hits |
| 5 | market\_regime \+ position\_sizer | bot\_mt5.py | Alta | Amplificar wins |
| 6 | Activar GBPUSD y XAUUSD | .env | Baja | Más oportunidades |

## **Orden de implementación sugerido para Cursor**

Implementar en este orden para ver resultados en cada paso:

5. Mejora 2 (cooldown .env) — 5 minutos, sin tocar código, impacto inmediato

6. Mejora 3 (sesión horaria) — 30 minutos, agregar función \+ variable de entorno

7. Mejora 1 (filtro H4 sells) — 1–2 horas, la de mayor impacto estructural

8. Mejora 4 (SL ATR-based) — 1–2 horas; validar con varios días de ejecución continua y revisión de logs

9. Mejora 6 (activar GBPUSD/XAUUSD) — 15 minutos \+ observación durante varios días

10. Mejora 5 (regime \+ sizer) — media jornada, integrar los archivos provistos

| IMPORTANTE | Las Mejoras 1, 3 y 4 cambian la lógica de entrada/salida. Conviene dejar el sistema corriendo varios días seguidos y revisar `trades.log` / `bot_state.json` antes de ajustes finos. Las Mejoras 2 y 6 son sobre todo variables de config. |
| :---: | :---- |

# **4\. Archivos ya provistos**

Los siguientes módulos ya están escritos y listos para integrar:

* market\_regime.py — detector de régimen por símbolo (TRENDING / RANGING / UNCERTAIN) con notificación Telegram al cambiar

* position\_sizer.py — sizing dinámico basado en % del balance × multiplicador de régimen × topes por símbolo

Ambos módulos están documentados internamente con instrucciones de integración al final del archivo (sección "Integración en bot\_mt5.py").

*Generado: 5/5/2026 · Análisis de 162 ops reales*