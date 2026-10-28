# Meta-Trader (MT5) Bot — Manual de uso

Proyecto en Python para **trading automatizado en MetaTrader 5** (librería `MetaTrader5`).

**Motor vivo (desde 2026-10-07):** swing diario **Donchian 20/10 + SMA200** en **demo XBTFX** (`swing_daily.py`, magic **260501**). Una decisión por día a las 00:10 UTC sobre las 79 crypto: compra rupturas del máximo de 20 días con precio sobre su promedio de 200 días, vende al perder el mínimo de 10 días o el promedio. 0.5% de riesgo por trade, 10 posiciones máximo, freno a −35% de drawdown. Runbook y evidencia (Vibe + QuantConnect): [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md).

**Rotación (`bot_mt5.py`): apagada desde 2026-10-07** porque no ganaba. Detenida con `kill_switch.halt` (reason `rotacion_reemplazada_por_swing`); si alguien la arranca, no abre. Lo que sigue sobre `bot_mt5.py` describe la rotación tal como quedó ([`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md)). El loop H4/M15, el EV gate, el detector de régimen y la escalera USD se quitaron del código el 2026-10-06.

También hay:

- **Dashboard** web local (`http://127.0.0.1:8000`) — muestra el estado que dejó la rotación (`bot_state.json`); el swing todavía no tiene vista propia.

> Aviso: todo lo que sigue es para **educación / testing**. Un backtest **no garantiza** resultados en real.

---

## 1) Estructura del repo (qué hace cada archivo)

### Archivos principales

- **`swing_daily.py`** (motor vivo)
  - Lee `.env`, conecta MT5, decide una vez por día UTC con velas D1 cerradas. Órdenes solo con `BOT_SWING_DRY_RUN=0` + `BOT_SWING_DEMO_LIVE=1` en cuenta demo XBTFX.
  - Reglas en `trading/domain/swing_donchian.py`, ciclo en `trading/application/swing_engine.py`, MT5 en `trading/infrastructure/mt5_swing_broker.py`.
  - Escribe `swing_trades.log` (JSON: `SWING_GATE`, `SWING_PLAN`, `SWING_OPEN`, `SWING_CLOSE`, `SWING_SKIP`) y `swing_state.json` (día decidido, pico de equity). Freno manual: `swing_kill_switch.halt`.
  - Detalle: `trading/docs/SWING_ENGINE.md`.

- **`bot_mt5.py`** (apagado desde 2026-10-07)
  - Rotación crypto (5 módulos / 4/5 + vetos). Detenida con `kill_switch.halt`.
  - Lee `.env`, conecta MT5 (demo XBTFX), vota M1/M5/M15, aplica Kelly $0.50 y gate de spread, `order_send` si `PAPER_DEMO_LIVE` + no dry-run.
  - Escribe `bot_state.json` (dashboard: cuota 60 min, skip `veto_htf` / `sin_pullback` / `rsi_rango` / spread) y logs JSON (`ENSEMBLE_VOTE`, `TRADE_OPEN` con `why_open=consenso_4_5`, `TRADE_CLOSE`).
  - Magic **260414**, comentario `rot_4_5`. Detalle: `trading/docs/ROTATION_ENGINE.md`.

- **`indicators.py`**
  - Indicadores “puros” (sin MT5): `ema`, `atr` (Wilder), `adx_wilder` (DI+/DI- + ADX).

### Dashboard

- **`flask_dashboard.py`**
  - Web local `http://127.0.0.1:8000`
  - Con rotación ON: Home muestra **5 módulos · 4/5 + veto M15**, cuota ENTRY_IN 60 min, skip `veto_htf` / `sin_pullback` / `rsi_rango` / spread. Lab: 5 badges por símbolo (no 13 votantes).
  - También: el minero de trades manuales.
  - Rutas:
    - `/` estado del bot (lee `bot_state.json`)
    - `/positions` posiciones abiertas (lectura MT5)
    - `/lab` votos del consenso por símbolo
    - `/journal` cierres desde `trades.log`
    - `/miner` minero de trades manuales (sin rotación)
    - `/telegram/chats` lista de `chat_id` vía getUpdates

- **`bot/templates/*.html`** y **`bot/static/style.css`**
  - Templates + estilos del dashboard Flask.

### Archivos de datos y estado

- **`.env`**
  - Configuración local (credenciales MT5, símbolos, riesgo, SL/TP, etc.).
  - Debe estar ignorado en git.

- **`.env.example`**
  - Plantilla de `.env`.

- **`bot_state.json`**
  - Estado “vivo” del bot para el dashboard (pid, heartbeat, símbolos, modo, PnL diario, etc.).

- **`trades.log`**
  - Log del bot (archivo). Incluye JSON con `ENSEMBLE_VOTE`, `TRADE_OPEN` (precio, lot, SL/TP, spread, riesgo USD) y `TRADE_CLOSE`.

---

## 2) Setup rápido

### Requisitos
- Windows
- MetaTrader 5 instalado y funcionando
- Python (recomendado usar `py`)

### Instalar dependencias

```bash
py -m pip install -r requirements.txt
```

### Configurar `.env`

1) Copiá `.env.example` como `.env`  
2) Completá estas claves:

- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`

Y configurá símbolos / riesgo (ver sección 4).

---

## 3) Cómo correr el bot live (demo/real)

### Paso 0: MT5

- Abrí MT5 con tu cuenta.
- Habilitá **AutoTrading** y permisos de Expert Advisors.
- Asegurate de que los símbolos estén disponibles en **Market Watch** (el bot también intenta seleccionarlos).

### Paso 1: correr el motor vivo (swing)

```bash
py .\swing_daily.py
```

Queda corriendo y decide una vez por día a las 00:10 UTC (21:10 en Argentina). Si se reinicia no repite el día ya decidido. Prueba sin órdenes: `py .\swing_daily.py --once --dry-run`. También lo levanta `start_all.ps1`.

La rotación (`py .\bot_mt5.py`) está apagada; con `kill_switch.halt` presente no abre.

### Paso 2: correr el dashboard (opcional)

```bash
py .\flask_dashboard.py
```

Abrí:
- `http://127.0.0.1:8000/` (home)
- `http://127.0.0.1:8000/lab` · `http://127.0.0.1:8000/positions` · `http://127.0.0.1:8000/journal`

---

## 4) Configuración clave en `.env`

### Símbolos

El universo es `CRYPTO_UNIVERSE` (79 CFD crypto, `trading/domain/crypto_universe.py`). `BOT_SYMBOLS` solo se usa si `BOT_ROTATION_KEEP_ENV_SYMBOLS=1`.

### `BOT_SYMBOL_CONFIG` (overrides por símbolo)

JSON en **una sola línea**. Claves que el bot todavía lee:

- `max_spread_points`
- `max_positions_per_symbol`, `pyramiding_require_be`, `pyramiding_min_separation_points`

`lot`, `sl_money`, `tp_money` y `max_trades_per_hour_per_symbol` los pisa la rotación en cada apertura (lote Kelly y stop de $0.50 / ±5%). El resto de claves viejas (rango, vela, anti-chase, escalera USD, SL/TP en points, ATR, break-even, trailing, filter-exit) ya no existen en el código y se ignoran.

Tras editar esta variable hay que **reiniciar** el proceso `bot_mt5.py` para que tome valores nuevos.

### Límite de pérdida diaria

- `BOT_DAILY_LOSS_LIMIT=-25`: si el PnL del día (deals del día en MT5) cae por debajo, el bot pasa a modo “solo gestión”, sin nuevas entradas. Con `BOT_KILL_SWITCH_FLATTEN=1` además cierra el libro una vez y deja `kill_switch.halt` (ver `trading/docs/ROTATION_ENGINE.md`).

### Horario

La rotación opera 24/7: el código fuerza a off la ventana horaria, el cutoff de viernes, el cutoff de nuevas entradas y el bloqueo de sesión quieta, y todos los símbolos del universo cuentan como “de fin de semana”. `BOT_TRADING_WINDOW_*`, `BOT_FRIDAY_CUTOFF_ENABLED`, `BOT_NEW_TRADE_CUTOFF_ENABLED` y `BOT_WEEKEND_TRADE_SYMBOLS` no tienen efecto.

### Portfolio weights

`BOT_PORTFOLIO_ENABLED` / `BOT_PORTFOLIO_WEIGHTS` / `BOT_PORTFOLIO_DYNAMIC_*` solo llenan el bloque `portfolio` de `bot_state.json`. No cambian el lote de la rotación.

### Spread / protección de costos

- Gate de la rotación: `BOT_ROTATION_MAX_SPREAD_USD_FRAC=0.10` (skip si el agujero del spread supera el 10% del riesgo en USD, o si cubre el stop).
- `BOT_MAX_SPREAD_POINTS` / `BOT_SPREAD_DYNAMIC_ENABLED` siguen en el código, pero en crypto XBTFX el gate que decide es el de USD.

### Límites

- `BOT_MAX_OPEN_TRADES=60`, una posición por símbolo, `BOT_MAX_OPENS_PER_HOUR=60` (`DEAL_ENTRY_IN` del bróker, hora rodante).
- `BOT_DAILY_LOSS_LIMIT` (ver arriba).

### Magic (identificar órdenes del bot en el historial)

El bot usa `magic=260414` (default de `Config` en `bot_mt5.py`; no hay override por env en el bot). El dashboard y los reportes leen `BOT_MAGIC` con ese mismo default para filtrar.

---

## 8b) Telegram (alertas del bot)

Opcional: avisos cuando el proceso no puede mantener MT5, excepciones en el loop, arranque y parada, **aperturas y cierres de operaciones** (mismo `magic` que el bot).

Variables en `.env`:

- `TELEGRAM_BOT_TOKEN` — token del bot (BotFather).
- `TELEGRAM_CHAT_ID` — tu chat numérico (o grupo; probá primero con un chat privado).
- `TELEGRAM_NOTIFY_START=1` — avisar al conectar (default 1 si token+chat están).
- `TELEGRAM_NOTIFY_STOP=1` — avisar al detener con Ctrl+C (default 1).
- `TELEGRAM_NOTIFY_TRADE_OPEN=1` — avisar cuando **se ejecuta una entrada** (default 1).
- `TELEGRAM_NOTIFY_TRADE_CLOSE=1` — avisar cuando **se cierra** una posición vía historial (default 1): motivo (SL, TP, manual, etc.), precio del deal de salida y PnL aproximado (profit + swap + comisión del deal).
- `TELEGRAM_TIMEOUT=12` — timeout HTTP en segundos.

**Cómo obtener `TELEGRAM_CHAT_ID`:** Telegram no hace que el bot te “devuelva” el id en el chat. Opciones:

1. **Solo con token:** poné `TELEGRAM_BOT_TOKEN` en `.env`, en Telegram abrí el bot y enviá `/start`, luego en el repo ejecutá `py .\get_telegram_chat_id.py` o en el dashboard abrí **Telegram → lista de chat_id** (`/telegram/chats`).
2. **Chat privado contigo:** escribile a **@userinfobot**; el número que te muestra suele ser el mismo `chat_id` que usás para recibir mensajes de tu bot en ese chat privado.
3. **Grupo:** agregá el bot al grupo, enviá un mensaje que mencione al bot o un comando, y mirá la lista de (1); los grupos suelen tener id negativo.

Si `getUpdates` falla por **webhook activo**, hay que quitar el webhook del bot (API `deleteWebhook`) para poder usar getUpdates desde el script/dashboard.

Comportamiento:

- **Fallo de login MT5 al inicio**: un mensaje.
- **Reconexión**: si tras `terminal_info` + `connect_mt5` sigue sin terminal, mensaje con **anti-spam** (mismo aviso como mucho cada ~5 min).
- **Excepción no capturada en el loop** (el bot sigue vivo): mensaje con traceback truncado, anti-spam ~10 min.
- **Orden ejecutada** (`TELEGRAM_NOTIFY_TRADE_OPEN`): símbolo, lado, lote, precio, SL/TP, ids de order/deal.
- **Posición cerrada** (`TELEGRAM_NOTIFY_TRADE_CLOSE`): tras cada ciclo se revisan deals de salida (`DEAL_ENTRY_OUT`) con el **magic del bot**; se indica motivo de cierre (p. ej. take profit / stop loss), precio y si el PnL del deal fue ganancia o pérdida. Al **arrancar**, los cierres de los últimos 2 días se marcan como ya vistos para no inundar el chat.

No subas el token a git: solo en `.env` local.

---

## 10) Glosario (términos importantes)

### SL (Stop Loss)
Nivel donde se cierra la operación con pérdida controlada.  
- En este proyecto puede ser:
  - **En money** (moneda de la cuenta) o
  - **En points** (más determinista para backtest).

### TP (Take Profit)
Nivel donde se cierra la operación con ganancia objetivo.

### Points / Puntos
Unidad mínima de precio del símbolo (en MT5: `symbol_info().point`).  
Ej:
- EURUSD: point suele ser `0.00001`
- XAUUSD: point suele ser `0.01`

En backtest, `pnl_points` se calcula como:
\( (exit - entry) / point \) (o invertido para SELL).

### Spread
Costo implícito por diferencia entre bid/ask.  
En backtest se modela como **spread fijo en points** (simplificación).

### Lot (lote)
Tamaño de posición en MT5 (`volume`). A mayor lot, mayor exposición y variación en PnL.

### Risk sizing
“Dimensionamiento por riesgo”: el lote se calcula para que el **riesgo por operación** sea un % del balance (equity).  
En points:
- Calcula cuánto “cuesta” el SL de `sl_points` para 1 lot (value-per-point).
- Ajusta el lot para que la pérdida al SL sea ~`risk_pct` del equity.

### Portfolio weights (pesos)
Distribución del riesgo total entre símbolos.  
Ej: 40/40/20 significa que el símbolo con 0.4 recibe el doble de riesgo que el de 0.2.

### Drawdown (DD)
Caída desde un pico del equity hasta un valle posterior.
- `max_drawdown_points`: peor caída en points (negativo)
- `max_drawdown_money`: peor caída en money (negativo)

### Winrate
Porcentaje de trades ganadores. No alcanza por sí solo: hay estrategias con winrate bajo y buen payoff, o viceversa.

### EMA (Exponential Moving Average)
Media móvil exponencial. En este bot se usa para:
- Determinar dirección (fast vs slow + precio vs fast).

### ADX (Average Directional Index)
Mide “fuerza” de tendencia. Se usa como filtro anti-lateral:
- Si ADX < `adx_min` → no se opera.

### Backtest determinista (bar-by-bar)
Simulación que avanza vela por vela (M15).  
Simplificaciones típicas:
- Spread fijo
- Sin slippage real
- Sin latencia / rechazos de ejecución

### Rebanceo (rebalance)
En portfolio: cambiar pesos cada N días según performance reciente (en este proyecto es un modelo simple basado en PnL trailing).

---

## 11) Límites y advertencias (muy importantes)

- Un backtest **no** incluye todo lo que pasa en real:
  - slippage, requotes, comisiones reales, spreads variables por sesión/noticias, gaps, etc.
- Cuentas chicas pueden quedar dominadas por:
  - `volume_min` del broker,
  - margen,
  - restricciones de símbolos (especialmente cruces exóticos y metales).
- Si un backtest da “100→1000”, interpretarlo como:
  - “en este modelo y este período histórico, con estos supuestos, podría”,
  - no como garantía.

