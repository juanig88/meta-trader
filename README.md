# MetaTrader 5 Python Bot

Bot de trading para **MetaTrader 5** usando la librería oficial `MetaTrader5` (Python).  
Se ejecuta en tu PC con MT5 abierto y logueado.

## Requisitos

- Windows + MetaTrader 5 instalado
- Python 3.10+ recomendado
- MT5 abierto y con tu cuenta iniciada

## Instalación

```powershell
py -m pip install -r requirements.txt
```

## Configuración (credenciales y bot)

1) Instalá dependencias (incluye `python-dotenv`):

```powershell
py -m pip install -r requirements.txt
```

2) Copiá `.env.example` a `.env` y completá valores (el bot carga `.env` al arrancar; no pisa variables que ya hayas definido en PowerShell):

- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`

3) (Opcional) Ajustá el resto en `.env` (lista completa en `.env.example`). Lo esencial para alinear **live** con **backtest** de este repo:

- `BOT_SYMBOLS`, `BOT_PORTFOLIO_ENABLED`, `BOT_PORTFOLIO_WEIGHTS`
- `BOT_USE_RISK_SIZING`, `BOT_RISK_PER_TRADE_PCT` (p. ej. **5** = 5 % base; en portfolio el riesgo efectivo por símbolo se multiplica por el peso)
- `BOT_SL_POINTS`, `BOT_TP_POINTS`
- `BOT_ADX_MIN`, `BOT_EMA_FAST_M15`, `BOT_EMA_SLOW_M15`
- `BOT_MAX_SPREAD_POINTS`, `BOT_MAX_OPEN_TRADES`, `BOT_MAX_OPEN_TRADES_TOTAL`

## Archivos principales (qué hace cada uno)

| Archivo | Rol |
|--------|-----|
| `bot_mt5.py` | Bot live: MT5, señales H4+M15, riesgo/pesos, logs JSON en `trades.log`. |
| `strategy_core.py` | Reglas de decisión puras (misma idea que live y backtest). |
| `backtest.py` | Backtest bar-by-bar **por símbolo** (M15, SL/TP points, riesgo opcional). |
| `portfolio_backtest.py` | Backtest **un solo balance** multi-símbolo con pesos y `risk_pct` (default desde `.env`). |
| `forward_metrics.py` | Métricas forward desde **historial real** MT5 (`history_deals`) filtrado por magic/símbolos. |
| `mt5_deals_audit.py` | Lista deals del historial: operaciones del bot vs otras (manual, balance, etc.). |
| `telegram_notify.py` | Avisos opcionales por Telegram (MT5 caído, errores de loop, arranque/parada, apertura/cierre de trades). |
| `flask_dashboard.py` | Dashboard local (`bot_state.json`, enlaces a reportes). |
| `dashboard.py` | Vista mínima de estado sin Flask. |
| `optimize.py` / `optimize_portfolio.py` | Búsquedas de parámetros (símbolo / portfolio). |
| `scan_symbols.py` | Ranking exploratorio de símbolos. |
| `indicators.py` | Indicadores compartidos (EMA, ADX Wilder, etc.). |
| `*.jsonl` | Resultados acumulados (backtest, portfolio, forward, scans). |
| `logs/trades_*.log` | Rotación de logs viejos si activaste rotación al arranque del bot. |

## Ejecutar

Con `.env` en la carpeta del proyecto:

```powershell
cd C:\Users\juani\Projects\meta-trader
py .\bot_mt5.py
```

Alternativa: definir credenciales solo en PowerShell (sin `.env`):

```powershell
$env:MT5_LOGIN="123456"
$env:MT5_PASSWORD="your_password"
$env:MT5_SERVER="MetaQuotes-Demo"
py .\bot_mt5.py
```

## Dashboard (local)

El bot escribe `bot_state.json`. Podés ver ese estado en el navegador:

### Opción A: Flask (recomendado)

```powershell
pip install -r requirements.txt
python .\flask_dashboard.py
```

Luego abrí `http://127.0.0.1:8000`.

### Opción B: servidor simple (sin Flask)

```powershell
python .\dashboard.py
```

Luego abrí `http://127.0.0.1:8000`.

## Logs estructurados

Además de consola, el bot escribe `trades.log` (incluye eventos JSON como `SIGNAL`, `TRADE_OPEN`, `BREAK_EVEN`, `TRAILING_UPDATE`).

## Estrategia A (live): SL en USD con escalera (+$1, +$2, +$3…)

Este repo incluye un modo de gestión de SL pensado para forward/live:

- **SL inicial**: al abrir la operación, se calcula un SL para arriesgar aprox. `-0.80 USD` (según contrato/tick value del símbolo y el lot).
- **Break-even (BE)**: tras esperar `BOT_USD_LADDER_WAIT_BE_SECONDS`, cuando la operación está **en ganancia en ese instante**, el bot mueve el SL a **precio de entrada** (offset opcional en puntos).
- **Escalera de ganancia asegurada**: si el profit flotante llega a \(+\$1\), mueve el SL para asegurar \(+\$1\); luego \(+\$2\), \(+\$3\), etc., con una pausa mínima entre escalones (`BOT_USD_LADDER_STEP_SECONDS`).

Variables relevantes en `.env`:

- `BOT_USD_SL_LADDER_ENABLED=1`
- `BOT_USD_SL_LADDER_INITIAL_SL_USD=0.8`
- `BOT_USD_LADDER_WAIT_BE_SECONDS=180`
- `BOT_USD_LADDER_STEP_SECONDS=120`
- `BOT_USD_LADDER_MAX_LOCK_USD=0` (0 = sin tope)

Notas prácticas:

- Si MT5 rechaza un cambio de SL (por `stops_level` / `freeze_level` o porque el SL quedaría del lado inválido del precio actual), el bot lo registra y lo reintenta en próximos ciclos cuando sea válido.
- Con `BOT_USD_SL_LADDER_ENABLED=1`, el bot **no usa** el trailing clásico / profit lock viejo para evitar conflictos.

## Watcher paralelo (solo escalera, cada 15s)

Si querés una “segunda capa” que fuerce la escalera aunque el bot principal esté ocupado o se pierda un tick, podés correr este watcher en paralelo. **No abre ni cierra trades**: solo ajusta el SL de la escalera USD.

En otra terminal:

```powershell
cd C:\Users\juani\Projects\meta-trader
py .\watcher_ladder.py --interval 15
```

## Filtros anti-lateral (reducen pérdidas en rango)

Si el mercado lateraliza (rango), el bot puede acumular varias pérdidas pequeñas seguidas. Para reducir entradas “falsas” en rango hay dos filtros opcionales:

- **Range filter (H1/H4)**: evita operar cerca del **medio** del rango reciente. Emite `RANGE_FILTER_SKIP`.
- **Candle strength filter (M15)**: exige vela con **cuerpo grande** y **mechas chicas** (y opcionalmente confirmación de 2 velas). Emite `CANDLE_FILTER_SKIP`.

Estos flags se pueden activar globalmente por `.env` o por símbolo vía `BOT_SYMBOL_CONFIG` (recomendado para activar solo en `ETHUSD`).

## Backtest (skeleton)

`backtest.py` corre un backtest determinista bar-by-bar (M15) y guarda resultados en `backtest_results.jsonl`.

Requisitos: MT5 abierto y logueado.

Ejemplos:

```powershell
# 60 días, símbolos desde BOT_SYMBOLS (o EURUSD por defecto)
py .\backtest.py

# Un solo símbolo y parámetros manuales
py .\backtest.py --symbols EURUSD --days 180 --sl 200 --tp 250 --spread 20 --ema-fast 50 --ema-slow 200 --adx-min 20

# Rango exacto
py .\backtest.py --symbols EURUSD,GBPUSD --start 2026-01-01 --end 2026-04-01
```

## Notas operativas

- Timeframes: **H4** (tendencia) + **M15** (confirmación).
- Ventana horaria: configurable con `BOT_TRADING_WINDOW_ENABLED` y `BOT_START_HOUR` / `BOT_END_HOUR` (en `.env.example` el perfil típico es **24/5** con `BOT_TRADING_WINDOW_ENABLED=0` y fin de semana bloqueado).
- Con `BOT_PORTFOLIO_ENABLED=1`, el riesgo **base** (`BOT_RISK_PER_TRADE_PCT`) se reparte por **peso** por símbolo (ver `MANUAL.md`).
- Máximo de posiciones: `BOT_MAX_OPEN_TRADES` / `BOT_MAX_OPEN_TRADES_TOTAL`; órdenes del bot identificables por `magic` (p. ej. `BOT_MAGIC`).

