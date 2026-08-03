# Meta-Trader (MT5) Bot — Manual de uso

Proyecto en Python para **trading automatizado en MetaTrader 5** (librería `MetaTrader5`), con:

- **Bot live** multi-símbolo (demo/real) con filtros (EMA/ADX), control de spread, cooldown y límites de riesgo.
- **Backtesting determinista** (bar-by-bar) en M15 + confirmación H4.
- **Portfolio backtest** (un solo balance) con distribución de riesgo por pesos.
- **Métricas forward** (`forward_metrics.py`): historial MT5 + log vs baseline de portfolio.
- **Scan de símbolos** y ranking.
- **Dashboard** web local para ver estado del bot y resultados de backtests/scans.

> Aviso: todo lo que sigue es para **educación / testing**. Un backtest **no garantiza** resultados en real.

---

## 1) Estructura del repo (qué hace cada archivo)

### Archivos principales

- **`bot_mt5.py`**
  - Bot live.
  - Lee `.env`, conecta MT5, decide señales por símbolo y abre trades con SL/TP.
  - Escribe `bot_state.json` (para el dashboard) y logs estructurados (`trades.log`: texto + líneas JSON con eventos `SIGNAL`, `TRADE_OPEN`, etc.).
  - Soporta **risk sizing** y **modo portfolio (pesos)** para distribuir riesgo total.
  - Opcional: **`market_regime.py`** detecta régimen por símbolo (tendencia / lateral / incierto), actualiza **`market_regimes`** en `bot_state.json` y puede avisar por **Telegram** con JSON sugerido para pegar en `BOT_SYMBOL_CONFIG` (sin aplicar cambios solo: reiniciás el bot cuando copies el config).

- **`market_regime.py`**
  - Módulo aparte usado solo por el bot live (no cambia SL/TP ni escalera USD por sí mismo).
  - Umbrales y lecturas: ADX M15 + ADX H4 + alineación EMA50/200 en M15 + distancia al centro del rango en velas **H1**.

- **`indicators.py`**
  - Indicadores “puros” (sin MT5): `ema`, `atr` (Wilder), `adx_wilder` (DI+/DI- + ADX).

- **`strategy_core.py`**
  - Lógica de estrategia “pura” reutilizable: trend H4 (por highs), señal M15 (breakout fuerte), EMA trend y filtro ADX.
  - `decide()` devuelve `BUY/SELL/NONE`.

### Backtests / research

- **`backtest.py`**
  - Backtest determinista (M15 + H4).
  - Ejecuta trades bar-by-bar con SL/TP en **points** y spread fijo (en points).
  - Reporta métricas en **points** y en **money** (moneda de depósito), con balance inicial configurable.
  - Soporta:
    - **`--risk-sizing`**: lote variable por % de equity.
    - **`--mode xau_atr`**: SL/TP dinámicos por ATR (útil para XAUUSD) + filtro de expansión.
  - Output: append a `backtest_results.jsonl`.

- **`scan_symbols.py`**
  - Selecciona ~N símbolos FX tier‑1 por spread actual y corre backtest rápido para rankear.
  - Output: JSONL (por defecto `scan_results.jsonl` o el que pases con `--out`).

- **`portfolio_backtest.py`**
  - Backtest **multi-símbolo con un solo balance** (portfolio).
  - Distribuye el riesgo por símbolo según **pesos** y `risk_pct`.
  - Puede **rebalancear** pesos cada N días según PnL reciente (simple).
  - Output: append a `portfolio_results.jsonl`.

- **`optimize_portfolio.py`**
  - Grid search de portfolio: prueba combinaciones de **pesos + risk_pct + rebalance** y filtra por constraints:
    - Ej: “final_balance ≥ 1000” y “max_dd_money ≥ -200”.
  - Carga `.env` al inicio (mismas credenciales MT5 que el resto del repo).
  - Output: `portfolio_grid.jsonl`.

- **`forward_metrics.py`**
  - Lee el **historial real de MT5** (`history_deals`) filtrado por `magic` y `BOT_SYMBOLS`.
  - Resume PnL, frecuencia (cierres ≈ deals `OUT`), winrate y distribución de PnL por símbolo y **por semana ISO**.
  - Opcional: `--baseline` (última fila de `portfolio_results.jsonl`) y `--log-trades trades.log` (spread al abrir desde eventos `TRADE_OPEN`).
  - Output: consola y/o append a `forward_results.jsonl` con `--out`.

### Dashboard

- **`flask_dashboard.py`**
  - Web local `http://127.0.0.1:8000`
  - En el **home** (`/`): además del estado habitual, tabla **Régimen de mercado** (lee `market_regimes` desde `bot_state.json` si el bot lo está poblando).
  - Rutas:
    - `/` estado del bot (lee `bot_state.json`)
    - `/export/market-regime` texto plano con régimen por símbolo + JSON de **parches sugeridos** (para copiar y pegar / merge de `BOT_SYMBOL_CONFIG`)
    - `/reports` índice de todos los JSONL con enlaces y conteo de líneas
    - `/backtests` tabla de `backtest_results.jsonl` (points + money)
    - `/scans` tabla de scan (`scan_results_run.jsonl` o, si está vacío, `scan_results.jsonl`)
    - `/portfolio` tabla de `portfolio_results.jsonl`
    - `/portfolio-grid` tabla de `portfolio_grid.jsonl` (grid `optimize_portfolio`)
    - `/forward` tabla + JSON de la última corrida de `forward_results.jsonl`
    - En el **home** (`/`): resumen del último forward + formulario **POST `/forward/run`** que ejecuta `forward_metrics.py` (subproceso; puede chocar con el bot si ambos usan la API MT5 desde Python a la vez)

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
  - Puede incluir **`market_regimes`**: objeto por símbolo con `regime` (`TRENDING` / `RANGING` / `UNCERTAIN`), `adx_m15`, `adx_h4`, `updated_at`, etc. (solo si `BOT_MARKET_REGIME_ENABLED=1` y `market_regime.py` cargó bien).

- **`backtest_results.jsonl`**
  - Resultados de backtests (cada línea es un JSON).

- **`scan_results_run.jsonl` / `scan_results.jsonl`**
  - Resultados de scans (cada línea es un JSON).

- **`portfolio_results.jsonl` / `portfolio_grid.jsonl`**
  - Portfolio runs y grid search (JSONL).

- **`forward_results.jsonl`**
  - Snapshots periódicos del script `forward_metrics.py` (una línea JSON por corrida).

- **`trades.log`**
  - Log del bot (archivo). Incluye JSON con `TRADE_OPEN` (precio, lot, SL/TP, spread en points, pesos portfolio y riesgo efectivo cuando aplica).

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

### Paso 1: correr el bot

```bash
py .\bot_mt5.py
```

### Paso 2: correr el dashboard (opcional)

```bash
py .\flask_dashboard.py
```

Abrí:
- `http://127.0.0.1:8000/` (home)
- `http://127.0.0.1:8000/reports` (hub de JSONL)
- `http://127.0.0.1:8000/backtests` · `http://127.0.0.1:8000/scans` · `http://127.0.0.1:8000/portfolio` · `http://127.0.0.1:8000/forward` · `http://127.0.0.1:8000/portfolio-grid`

---

## 4) Configuración clave en `.env`

### Símbolos

- `BOT_SYMBOLS=GBPUSD,USDJPY,NZDJPY,XAUUSD,BTCUSD` (ejemplo con crypto CFD en MT5)

### Velas `BTCUSD` vs FX (misma API MT5)

Las velas **M15/H4** se leen igual (`copy_rates_*`): mismos campos OHLC/tiempo. Lo que cambia por símbolo es **`point`**, **`trade_tick_value` / `trade_tick_size`**, **spread típico** y el **volumen mínimo**. Por eso en `BOT_SYMBOL_CONFIG` conviene **SL/TP en points** y **`max_spread_points`** distintos para BTC (órdenes de miles de points si `point=0.01`) y a veces un **`max_spread_points`** mayor para XAU.

Overrides opcionales por símbolo (además de `sl_points`, `tp_money`, etc.): **`break_even_trigger_money`**, **`trail_trigger_money`**, **`trail_new_sl_money`**, **`trail_new_tp_money`** (en moneda de cuenta), para que trailing/breakeven no use umbrales de $0.50 pensados en FX cuando la posición es BTC.

`BOT_SYMBOL_CONFIG` es JSON en **una sola línea**. Además de lo anterior suele llevar por instrumento cosas como:

- **`adx_min`**, **`max_spread_points`**, **`cooldown_minutes`**
- **Filtro de rango**: `range_filter_enabled`, `range_filter_timeframe`, `range_filter_lookback_bars`, `range_filter_mid_frac`
- **Filtro de vela**: `candle_filter_enabled`, `candle_body_min_frac`, `candle_wick_max_frac`, `candle_require_confirmation`
- **Anti-chase**: `anti_chase_atr_mult` (el piso crypto global sigue aplicando si corresponde)
- **Escalera USD** (solo parámetros, la lógica es global): `usd_sl_ladder_initial_sl_money`, `usd_ladder_wait_be_seconds`, `usd_ladder_step_seconds`, `usd_ladder_max_lock_usd`
- **Filter exit**: `filter_exit_min_profit_money` (y otros `filter_exit_*` si los definís por símbolo)

Tras editar esta variable hay que **reiniciar** el proceso `bot_mt5.py` para que tome valores nuevos.

### Régimen de mercado (`market_regime.py`)

El bot puede clasificar cada símbolo en **tendencia**, **lateral** o **incierto** y:

1. Guardar el snapshot en `bot_state.json` → campo **`market_regimes`** (visible en dashboard).
2. Enviar por **Telegram** un mensaje cuando **cambia** el régimen (no en la primera muestra tras arrancar, para evitar spam), con un JSON sugerido **solo algunas claves** de `BOT_SYMBOL_CONFIG` (`adx_min`, `range_filter_mid_frac`, etc.). Vos decidís si lo pegás en `.env`; el módulo **no** sobrescribe el `.env` ni modifica órdenes.

Variables:

- **`BOT_MARKET_REGIME_ENABLED=1`** — activado por defecto en setups recientes; poné **`0`** para desactivar.
- **`BOT_MARKET_REGIME_CHECK_INTERVAL_SEC=900`** (opcional; default **900** si no está definida): tiempo mínimo entre recálculos **por símbolo** dentro del detector.
- **`BOT_MARKET_REGIME_ADX_TRENDING`** / **`BOT_MARKET_REGIME_ADX_RANGING`** (opcionales): umbrales de clasificación ADX M15 vs etiqueta TRENDING / RANGING (defaults **28** y **22**). Los `adx_min` del `BOT_SYMBOL_CONFIG` son filtros de **entrada** del bot y pueden ir por otro valor; si ves demasiado `UNCERTAIN` o saltos raros, tocá estos dos.

El **dashboard** muestra régimen + texto de ayuda + JSON sugerido solo como **lectura del último estado** (no envía alertas): las alertas de “conviene revisar” llegan por **Telegram cuando cambia** el régimen (`TRENDING`/`RANGING`/`UNCERTAIN`).

### SL/TP en points (determinista)

Para operar “como backtest” con sizing por riesgo, lo más consistente es SL/TP en points:

- `BOT_SL_POINTS=80`
- `BOT_TP_POINTS=160`

> Si `BOT_SL_POINTS/BOT_TP_POINTS` están en 0, el bot usa SL/TP en **money** (`sl_money/tp_money`) en vez de points.

### Risk sizing (lote variable)

- `BOT_USE_RISK_SIZING=1`
- `BOT_RISK_PER_TRADE_PCT=10` (ejemplo agresivo; ajustá a tu perfil)

Esto define el % base de riesgo por trade (ver “portfolio mode” abajo para distribuirlo).

### Portfolio mode (riesgo total distribuido por pesos)

Habilita que el risk sizing use:
\( risk\_pct\_efectivo = BOT\_RISK\_PER\_TRADE\_PCT \times peso\_del\_símbolo \)

- `BOT_PORTFOLIO_ENABLED=1`
- `BOT_PORTFOLIO_WEIGHTS={"GBPUSD":0.2,"USDJPY":0.2,...}` (JSON o formato `SYM=w,…`)

Ejemplo: con `BOT_RISK_PER_TRADE_PCT=10` y peso 0.2 por símbolo:
- riesgo efectivo por ese símbolo ≈ **2%** del equity por operación (10 × 0.2).

### Límite de pérdida diaria

- `BOT_DAILY_LOSS_LIMIT=-60` (ejemplo): si el PnL del día (deals del día en MT5) cae por debajo de **-60** en moneda de cuenta, el bot pasa a modo “solo gestión”, sin nuevas entradas. Más negativo = más margen antes del corte.

### Fin de semana: FX quieto, crypto operando

- `BOT_WEEKEND_BLOCK_ENABLED=1`: sábado y domingo **no** se abren entradas nuevas en los pares listados, **salvo** los que figuren en `BOT_WEEKEND_TRADE_SYMBOLS` (coma), típicamente `BTCUSD`.
- El **trailing / gestión** de posiciones abiertas sigue corriendo siempre (`manage_positions`).
- Si activás `BOT_TRADING_WINDOW_ENABLED` o `BOT_FRIDAY_CUTOFF_ENABLED`, esas reglas **sí aplican también** a los símbolos de fin de semana (incluido BTC), salvo que las desactives.

### Portfolio weights dinámicos (opcional)

Si querés que el bot **reasigne el riesgo** entre instrumentos según “cómo vienen” (PnL realizado por símbolo), activá el rebalance dinámico. Internamente usa `MT5 history_deals_get` filtrado por `magic` y arma pesos en base al PnL de los últimos N días, con:
- **smoothing** (EMA) para evitar saltos,
- **min/max weight** para no apagar símbolos ni concentrarse demasiado,
- fallback a `BOT_PORTFOLIO_WEIGHTS` si no hay historial suficiente.

Variables:
- `BOT_PORTFOLIO_DYNAMIC_ENABLED=1`
- `BOT_PORTFOLIO_DYNAMIC_LOOKBACK_DAYS=14`
- `BOT_PORTFOLIO_DYNAMIC_REBALANCE_MINUTES=60`
- `BOT_PORTFOLIO_DYNAMIC_SMOOTH_ALPHA=0.35`
- `BOT_PORTFOLIO_DYNAMIC_MIN_WEIGHT=0.05`
- `BOT_PORTFOLIO_DYNAMIC_MAX_WEIGHT=0.60`
- `BOT_PORTFOLIO_DYNAMIC_POSITIVE_ONLY=1` (1=solo ganadores suman; 0=shift y todos compiten)

Ejemplo de pesos base (JSON) como el que mencionaste:

```text
BOT_PORTFOLIO_WEIGHTS={"GBPUSD":0.3,"NZDJPY":0.2,"USDJPY":0.3,"XAUUSD":0.2}
```

### Spread / protección de costos

- `BOT_MAX_SPREAD_POINTS=25`
- `BOT_SPREAD_DYNAMIC_ENABLED=0|1` (si querés gate dinámico por EMA de spread)

### Límites

- `BOT_MAX_OPEN_TRADES` / `BOT_MAX_OPEN_TRADES_TOTAL`
- `BOT_DAILY_LOSS_LIMIT` (stop de nuevas entradas si el PnL diario ≤ este valor; más negativo = más margen antes de cortar; default en código -25)
- `BOT_COOLDOWN_MINUTES`

### 24/5 recomendado (FX/XAU) y 24/7 (solo si tu broker lo soporta)

Para FX y oro en MT5, lo más común es **24/5** (no hay mercado el finde). Si querés que el bot evalúe entradas todo el día pero respete el finde:

- `BOT_TRADING_WINDOW_ENABLED=0`
- `BOT_WEEKEND_BLOCK_ENABLED=1`
- `BOT_FRIDAY_CUTOFF_ENABLED=0`
- `BOT_NEW_TRADE_CUTOFF_ENABLED=0`

Si tu broker te ofrece **cripto 24/7 real** (símbolos tipo `BTCUSD`) y querés operar también el finde:

- `BOT_WEEKEND_BLOCK_ENABLED=0`

Si preferís **mantener ventana** (por ejemplo solo FX en tu horario), dejá `BOT_TRADING_WINDOW_ENABLED=1` y configurá:

- `BOT_START_HOUR`
- `BOT_END_HOUR`
- `BOT_NO_NEW_TRADES_AFTER_HOUR`
- `BOT_NO_NEW_TRADES_AFTER_MINUTE`

### Magic (identificar órdenes del bot en el historial)

Por defecto el bot usa `magic=260414` (código en `Config` de `bot_mt5.py`). Para `forward_metrics.py` podés fijar otro valor con:

- `BOT_MAGIC=260414` (opcional; si no está, el script usa 260414 o `--magic` en línea de comandos).

Las órdenes del bot deben coincidir con ese número para que el resumen forward filtre solo tus trades.

---

## 5) Backtests (single-symbol)

### Backtest clásico (points + money)

```bash
py .\backtest.py --symbols GBPUSD --days 180 --sl 80 --tp 160 --spread 6 --adx-min 20 --initial-balance 100 --risk-sizing 1 --risk-pct 5
```

Notas:
- `--spread` está en **points**.
- `--initial-balance` es en **moneda de depósito**.
- Con `--risk-sizing 1`, el lote se ajusta con el balance.

### XAUUSD “xau_atr” (ATR dinámico)

Ejemplo de idea “tipo Valkyrie” (SL/TP por ATR + expansión):

```bash
py .\backtest.py --symbols XAUUSD --days 365 --mode xau_atr --spread 17 --sl 0 --tp 0 \
  --atr-period 14 --atr-lookback 20 --atr-expand-mult 1.1 --atr-sl-mult 2 --atr-tp-mult 3 --atr-min-points 50 \
  --initial-balance 1000 --risk-sizing 1 --risk-pct 1
```

---

## 6) Scan de símbolos (ranking)

```bash
py .\scan_symbols.py --days 180 --sl 80 --tp 160 --adx-min 20 --limit 25 --out scan_results_run.jsonl
```

---

## 7) Portfolio backtest (un solo balance)

Si **no** pasás `--weights`, el script usa `BOT_PORTFOLIO_WEIGHTS` del `.env` (misma idea que el bot live).

Ejemplo (365 días) con pesos 40/40/20 y spreads fijos:

```bash
py .\portfolio_backtest.py --symbols GBPUSD,USDJPY,NZDJPY \
  --weights GBPUSD=0.4,USDJPY=0.4,NZDJPY=0.2 \
  --spreads GBPUSD=6,USDJPY=3,NZDJPY=9 \
  --days 365 --sl 80 --tp 160 --initial-balance 100 --adx-min 20 --risk-pct 5 --rebalance-days 0
```

---

## 8) Grid search de portfolio (buscar 100→1000 con DD límite)

Ejemplo:

```bash
py .\optimize_portfolio.py \
  --symbols GBPUSD,USDJPY,NZDJPY \
  --spreads GBPUSD=6,USDJPY=3,NZDJPY=9 \
  --days 365 --initial-balance 100 \
  --sl 80 --tp 160 --adx-min 20 \
  --risk-list 2,3,4,5,6 \
  --rebalance-list 0,7,14,30 \
  --weight-step 0.2 \
  --target-final 1000 \
  --dd-min -200 \
  --out portfolio_grid.jsonl --top 10
```

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
- **Cambio de régimen** (si `BOT_MARKET_REGIME_ENABLED=1`): mensaje con métricas y JSON sugerido para `BOT_SYMBOL_CONFIG` por símbolo; anti-spam con `dedup_key` / intervalo entre avisos similares.

No subas el token a git: solo en `.env` local.

---

## 9) Validación forward (live vs portfolio backtest)

Objetivo: comparar en **las mismas ideas** (símbolos, SL/TP en points, risk % y pesos) lo que pasó en **cuenta real/demo** con lo que dice el **portfolio backtest**, sin quedarse solo con el balance puntual: mirá **semanas** de frecuencia, PnL por símbolo, distribución de resultados en cierres y, si podés, spread al abrir.

### Correr una vez (desde la carpeta del repo)

Asegurate de tener MT5 abierto, `.env` con `MT5_*` y `BOT_SYMBOLS` alineados al bot que corrés.

```bash
py .\forward_metrics.py --days 14 --log-trades trades.log --out forward_results.jsonl
```

Con `portfolio_results.jsonl` para contexto: `forward_metrics.py` intenta elegir la fila que **matchee** símbolos, pesos, `BOT_RISK_PER_TRADE_PCT` y SL/TP del `.env` (si no hay match, cae a la última línea válida).

```bash
py .\forward_metrics.py --days 14 --log-trades trades.log --baseline portfolio_results.jsonl --out forward_results.jsonl
```

Para listar deals del historial (bot vs manual / otros):

```bash
py .\mt5_deals_audit.py --days 14
```

Flags útiles:

| Flag | Rol |
|------|-----|
| `--days N` | Ventana hacia atrás desde “ahora” (default 7). |
| `--symbols A,B,C` | Filtra símbolos (si no, usa `BOT_SYMBOLS`). |
| `--magic N` | Filtra deals por magic (default: env `BOT_MAGIC` o 260414). |
| `--log-trades PATH` | Parsea `TRADE_OPEN` y resume `spread_points` por símbolo y semana. |
| `--baseline FILE` | JSONL de portfolio: elige fila alineada con tu `.env` cuando puede; contrasta trades/día backtest vs forward. |
| `--out FILE` | Append de un resumen JSON (historial semanal a mano o con tarea programada). |

### Qué mirar en el JSON de salida

- **`deals_out_matched` / `outs_per_day`**: frecuencia de cierres (en MT5 un cierre puede ser más de un deal OUT si hay parciales).
- **`by_iso_week`**: desglose semanal (PnL, outs, winrate por símbolo).
- **`by_symbol`**: PnL total y **distribución** de PnL solo en outs (min/max/media/mediana).
- **`from_log`**: si pasaste `--log-trades`, spread al abrir (mean/median por símbolo y semana); útil para contrastar con `spread_points_by_symbol` del backtest.
- **`baseline`**: comparación orientativa `trades_per_day_backtest` vs `outs_per_day_forward` (no es match perfecto: horizonte distinto, filtros de sesión, slippage, etc.).

### Evento `TRADE_OPEN` en el log

Cada apertura exitosa registra JSON con, entre otros: `spread_points`, `portfolio_enabled`, `portfolio_weight`, `risk_per_trade_pct_base`, `risk_per_trade_pct_effective`, además de lot, SL/TP y `deal`.

### Automatizar (opcional)

En el **Programador de tareas** de Windows podés ejecutar semanalmente el mismo comando con rutas absolutas a `py`, al repo y a `trades.log` / `portfolio_results.jsonl`, para ir llenando `forward_results.jsonl`.

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

### Risk % (risk_per_trade_pct)
Porcentaje del balance que se busca arriesgar en una operación.  
Ej: con **5%** base y **portfolio**, el riesgo **por símbolo** es \(5\% \times peso\); si el SL se ejecuta, la pérdida esperada ronda ese % del equity (con supuestos del modelo).

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

