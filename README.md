# MetaTrader 5 Python Bot

Bot de trading para **MetaTrader 5** usando la librería oficial `MetaTrader5` (Python).  
Se ejecuta en tu PC con MT5 abierto y logueado.

**Motor vivo (desde 2026-10-07):** swing diario **Donchian 20/10 + SMA200**, solo compras, sobre 79 crypto CFD en **demo XBTFX** (`swing_daily.py`, magic 260501). Decide una vez por día después del cierre diario (00:10 UTC). Runbook, evidencia y backtests Vibe + QuantConnect: [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md). Contexto para agentes: [`AGENTS.md`](AGENTS.md).

**Rotación (`bot_mt5.py`): apagada desde 2026-10-07** porque no ganaba (antes del spread su resultado era cero). Queda detenida con `kill_switch.halt` (reason `rotacion_reemplazada_por_swing`). Su documentación sigue en [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md) como registro.

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

3) (Opcional) Ajustá el resto en `.env` (lista completa en `.env.example`, sección N). Lo esencial para el **motor vivo (swing)**:

- `BOT_SWING_DRY_RUN=0` y `BOT_SWING_DEMO_LIVE=1` para mandar órdenes (sin eso solo registra). Solo cuenta demo XBTFX.
- `BOT_SWING_RISK_FRAC=0.005` (0.5% del equity por trade), `BOT_SWING_MAX_POSITIONS=10`, `BOT_SWING_MAX_DD=0.35`
- `BOT_SWING_HONOR_ROTATION_HALT=0` (la rotación está apagada con su halt; el swing no lo respeta). Freno del swing: crear `swing_kill_switch.halt`.

Variables de la rotación (apagada), por si se vuelve a usar:

- `PAPER_DEMO_LIVE=1`, `BOT_DRY_RUN=0` (demo XBTFX; nunca cuenta real)
- `BOT_ENSEMBLE_MIN_AGREE=4` (4 de 5 módulos), `BOT_MAX_RISK_USD=0.50`, `BOT_MAX_OPENS_PER_HOUR=60`
- `BOT_ROTATION_MAX_SPREAD_USD_FRAC=0.10`, `BOT_ROTATION_STALE_SECONDS=300`, `BOT_ROTATION_STALE_CLOSE_ALL=1`
- `BOT_ROTATION_BOOK_HARVEST_USD=0.50` (aplanar el libro; 0 = off)

## Archivos principales (qué hace cada uno)

| Archivo | Rol |
|--------|-----|
| `swing_daily.py` | **Motor vivo:** swing diario Donchian 20/10 + SMA200; logs JSON en `swing_trades.log`, estado en `swing_state.json`. |
| `trading/domain/swing_donchian.py` | Reglas del swing (entrada, salida, tamaño, swap, halt por drawdown), sin I/O. |
| `trading/application/swing_engine.py` | Ciclo diario: primero salidas, después entradas, gates y log. |
| `trading/infrastructure/mt5_swing_broker.py` | Adaptador MT5 del swing (velas D1 cerradas, órdenes, swap en USD). |
| `trading/backtest/qc/swing_donchian_qc.py` | El mismo swing para el backtest independiente en QuantConnect. |
| `bot_mt5.py` | Rotación 5 módulos / 4/5, **apagada desde 2026-10-07** (ver `trading/docs/ROTATION_ENGINE.md`). |
| `trading/domain/ensemble.py` | Módulos HTF / momentum / intermarket / vol_bands / structure, vetos, Kelly cap $0.50. |
| `telegram_notify.py` | Avisos opcionales por Telegram (MT5 caído, errores de loop, arranque/parada, apertura/cierre de trades). |
| `flask_dashboard.py` | Dashboard local (`bot_state.json`, posiciones, lab, journal). |
| `indicators.py` | Indicadores compartidos (EMA, ADX Wilder, etc.). |
| `trading/domain/expected_value.py` | EV, posterior Beta-Binomial y tope quarter-Kelly (sin I/O). |
| `trading/domain/ev_validation.py` | Buckets `símbolo|sesión|ADX` y walk-forward. |
| `logs/trades_*.log` | Rotación de logs viejos si activaste rotación al arranque del bot. |

## Cómo funciona el bot

Hay **un solo motor vivo**: el swing diario. La rotación está apagada. El dashboard (`flask_dashboard.py`) lee `bot_state.json`, que escribía la rotación; el swing todavía no tiene vista propia (su estado está en `swing_trades.log` y `swing_state.json`).

```
00:10 UTC → velas diarias cerradas (79 crypto) → salidas → entradas → order_send demo XBTFX
                                                              ↓
                                       swing_trades.log (SWING_PLAN / SWING_OPEN / SWING_CLOSE)
```

### 1) Motor vivo — swing diario (magic `260501`)

1. Una vez por día, 10 minutos después del cierre diario (00:10 UTC), mira las 79 crypto.
2. **Vende** lo que tiene si cerró debajo del mínimo de los 10 días anteriores o debajo de su promedio de 200 días.
3. **Compra** si cerró por encima del máximo de los 20 días anteriores y por encima del promedio de 200 días. Solo compras, nunca ventas en corto.
4. Arriesga 0.5% del equity por trade (distancia hasta el mínimo de 10 días), máximo 10 posiciones. Si hay más señales que lugares, elige las que más subieron en 90 días.
5. Stop de emergencia en el bróker a 2 veces esa distancia. No entra si el swap del símbolo supera 30% anual.
6. Si el equity cae 35% desde su máximo, deja de abrir hasta que una persona borre `swing_kill_switch.halt`.

Detalle, gates y catálogo de logs: [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md).

### 2) Apagado el 2026-10-07 — rotación crypto (magic `260414`)

Descripción tal como quedó (no corre):

Universo: **79 CFD** XBTFX (`!`). Demo only (`PAPER_DEMO_LIVE` + `trade_mode` demo). Nunca dinero real.

1. Cada ~15s vota **5 módulos** independientes: `htf` (M15 vs EMA50), `momentum_short`, `intermarket`, `vol_bands`, `structure`.
2. Dispara si **4/5** coinciden en BUY o SELL. `NONE` cuenta en contra. Volumen XBTFX no vota.
3. Vetos (no son votos extra): HTF contra el lado (`veto_htf`); último M1 sin pullback a EMA9/20 (`sin_pullback`); RSI≥70/≤30 solo si HTF es rango (`rsi_rango`).
4. Size: Kelly **solo achica** hasta `BOT_MAX_RISK_USD=0.50`. SL/TP $0.50 si entra en el 5% del precio; si no, ±5% y el lote escala.
5. Skip spread si el bid/ask cubre el stop o el agujero USD es **> 10%** del riesgo.
6. Tope **60** `DEAL_ENTRY_IN` / hora y **un** slot por símbolo.
7. Tras **5 min**: `profit > 0.01` → `stale_take`, `profit < -0.0001` → `stale_loss`, banda del medio → `stale_time`.
8. Harvest del libro: neto flotante ≥ **0.50**, ≥2 tickets, el más joven ≥ 10 min → cierra todas (`rot_book_harvest`).
9. Comentario de apertura `rot_4_5`. `why_open=consenso_4_5`.

Catálogo de logs y skip reasons: [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md). Dashboard: Home “5 módulos · 4/5 + veto M15”; Lab 5 badges.

### 3) Retirado el 2026-10-06

El loop H4/M15 (`strategy_core.py`), el EV gate / quarter-Kelly del loop principal, el detector de régimen (`market_regime.py`) y el XAUUSD paralelo (magic `260415`) se quitaron del código. Su historial sigue en el bróker y en `trades.log`; el motor vivo no los usa.

## Ejecutar

Stack local (swing + dashboard Flask; sin Docker, MetaTrader 5 abierto):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

Solo el motor vivo (swing), con `.env` en la carpeta del proyecto:

```powershell
cd C:\Users\juani\Projects\meta-trader
py .\swing_daily.py                    # queda corriendo; decide una vez por día
py .\swing_daily.py --once --dry-run   # prueba: decide hoy sin mandar órdenes
```

La rotación (`py .\bot_mt5.py`) está apagada: si se arranca, el `kill_switch.halt` le impide abrir.

Alternativa: definir credenciales solo en PowerShell (sin `.env`):

```powershell
$env:MT5_LOGIN="123456"
$env:MT5_PASSWORD="your_password"
$env:MT5_SERVER="MetaQuotes-Demo"
py .\bot_mt5.py
```

## Dashboard (local)

El bot escribe `bot_state.json`. Podés ver ese estado en el navegador:

```powershell
pip install -r requirements.txt
python .\flask_dashboard.py
```

Luego abrí `http://127.0.0.1:8000`.

## Logs estructurados

El swing escribe `swing_trades.log` (`SWING_GATE`, `SWING_PLAN`, `SWING_OPEN`, `SWING_CLOSE`, …; catálogo en [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md)). La rotación escribía `trades.log` con eventos JSON (`ENSEMBLE_VOTE`, `TRADE_OPEN`, `TRADE_CLOSE`, `ROTATION_BOOK`, …). Catálogo completo en [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md).

## Gestión de posiciones

Swing: salida por cierre diario (mínimo de 10 días o SMA200) y stop de emergencia en el bróker; ver [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md). Rotación (apagada): SL/TP de $0.50 (o ±5%), cierre a los 5 minutos, harvest del libro y kill switch: ver [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md). La escalera USD, el ATR trail, el profit lock, el filter-exit, el break-even y el trailing se quitaron del código el 2026-10-06.

Si MT5 rechaza un cambio de SL (por `stops_level` / `freeze_level` o porque el SL quedaría del lado inválido del precio actual), el bot lo registra y lo reintenta en próximos ciclos cuando sea válido.

## Notas operativas

- Swing: velas **D1** cerradas (el servidor XBTFX está en UTC); magic **260501**.
- Rotación (apagada): timeframes de voto **M1 / M5 / M15** (más M15/H4 para el snapshot Murphy).
- Ventana horaria: `BOT_TRADING_WINDOW_ENABLED` y el cutoff de viernes no aplican a la rotación (el código los fuerza a off).
- Máximo de posiciones: `BOT_MAX_OPEN_TRADES` (60) y una por símbolo; órdenes del bot identificables por `magic` (260414).

