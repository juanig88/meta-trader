# MetaTrader 5 Python Bot

Bot e investigación de trading en crypto para **MetaTrader 5**, con la librería oficial `MetaTrader5` de Python. Corre en tu PC con MT5 abierto y logueado. **Solo cuentas demo.**

Contexto para agentes: [`AGENTS.md`](AGENTS.md). Lo que ya se midió y no hay que volver a proponer: [`trading/docs/ESTABLISHED_FACTS.md`](trading/docs/ESTABLISHED_FACTS.md).

## Estado al 2026-10-28

- **Bróker:** Exness, cuenta demo (`Exness-MT5Trial11`), símbolos `BTCUSDm` y `ETHUSDm`. Antes era XBTFX demo; desde el 2026-10-10 la terminal es Exness.
- **Ningún motor manda órdenes hoy.**

| Pieza | Qué hace | Estado |
|---|---|---|
| `swing_daily.py` | Swing diario Donchian 20/10 + SMA200, solo compras | **Dry-run en Exness** (forzado en el código). En XBTFX está aprobado para demo. |
| `chequeo_previo.py` | Chequeo antes de cada operación manual y diario (`DIARIO.md`) | En uso. Solo lee MT5. |
| `record_binance_book.py` | Graba el libro de órdenes público de Binance BTCUSDT | Grabando desde 2026-10-27 para una medición futura |
| `murphy_dry.py` | Lector del capítulo 4 de Murphy sobre el diario de BTC y ETH | Solo lectura |
| `bot_mt5.py` | Rotación intradía de 79 crypto, 4 de 5 módulos | **Apagada** desde 2026-10-07 |

## Qué aprendimos

Todo está medido con datos, costos reales y, cuando corresponde, con pre-registro y holdout. El detalle está en [`trading/docs/INTRADIA_EXNESS.md`](trading/docs/INTRADIA_EXNESS.md) y en `ESTABLISHED_FACTS.md`.

- **Intradía (M1 a M30) en BTC:** ninguna regla acierta la dirección lo suficiente para pagar el spread. Se probaron:
  - la rotación de 5 módulos
  - la regla de retroceso de Murphy
  - un zigzag de "tendencia que ya se ve"
  - rupturas con EMA20 y volumen
  - el flujo agresor de Binance
  - el chequeo previo convertido en bot
- **La única señal que se replica fuera de muestra** es la reversión corta: después de un movimiento fuerte de 5–15 min, el precio devuelve algo en los 15–30 min siguientes. Da +0.5 a +1.0 bps antes de costos, menos que el spread de Exness (1.18 bps).
- **El costo depende del bróker:** en Exness, el spread de BTC es 0.17× el movimiento típico de 5 min; en XBTFX era 1.3×. Pero un costo bajo no crea una ventaja.
- **Stop y take profit no crean ventaja.** Con entradas al azar, el win rate sale del cociente SL/(SL+TP) y el resultado esperado es cero. Lo único que importa es que la entrada acierte la dirección.
- **Diario, solo compras, siguiendo la tendencia:** es lo único que pasó todas las pruebas. Es el swing.

## Requisitos

- Windows y MetaTrader 5 instalado, abierto y logueado en una cuenta demo
- Python 3.10 o superior

```powershell
py -m pip install -r requirements.txt
```

## Configuración

Copiá `.env.example` a `.env` y completá `MT5_LOGIN`, `MT5_PASSWORD` y `MT5_SERVER`. El bot carga `.env` al arrancar y no pisa las variables que ya definiste en PowerShell. La lista completa de opciones está en `.env.example`; las del swing están en la sección N.

Variables del swing:

| Variable | Default | Qué hace |
|---|---|---|
| `BOT_SWING_DRY_RUN` / `BOT_SWING_DEMO_LIVE` | `1` / `0` | Para mandar órdenes hacen falta `0` y `1`. **En Exness se ignoran:** siempre es dry-run. |
| `BOT_SWING_RISK_FRAC` | `0.005` | Riesgo por operación: 0.5% del equity |
| `BOT_SWING_MAX_POSITIONS` | `10` | Posiciones simultáneas |
| `BOT_SWING_MAX_DD` | `0.35` | Drawdown que frena las entradas |
| `BOT_SWING_SYMBOLS` | vacío | Por defecto: las 79 crypto de XBTFX, o `BTCUSDm,ETHUSDm` en Exness |

En Exness, el swing usa sus propios archivos: `swing_exness_state.json`, `swing_exness_trades.log` y `swing_exness_kill_switch.halt`.

## Uso

### Swing diario

```powershell
py .\swing_daily.py --once --dry-run   # decide hoy, sin órdenes
py .\swing_daily.py                    # queda corriendo; decide una vez por día después de las 00:10 UTC
```

Una vez por día, sobre la vela diaria cerrada:

1. **Vende** si el cierre quedó debajo del mínimo de los 10 días anteriores o de la media de 200 días.
2. **Compra** si el cierre supera el máximo de los 20 días anteriores y está sobre la media de 200 días.
3. **Tamaño:** arriesga 0.5% del equity hasta el mínimo de 10 días. El stop de emergencia en el bróker va al doble de esa distancia.
4. **Freno:** no entra si el swap pasa el 30% anual, y deja de abrir si el equity cae 35% desde su máximo.

Backtest BTC+ETH 2018–2026 con costos de Exness: 32% anual, Sharpe 0.98, caída máxima −34%, win rate ~30%. Opera poco: un par de veces por año y por símbolo.

En Exness, con 200 USD de saldo no puede entrar: el lote mínimo de BTC arriesga unos 76 USD. Hace falta un saldo demo de ~20.000 USD.

Runbook completo: [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md).

### Chequeo previo y diario de operaciones manuales

```powershell
py .\chequeo_previo.py abrir --lado BUY --stop 80300 --tp 81500 --idea "M5 rompió el mínimo de la última hora"
py .\chequeo_previo.py cerrar     # busca el resultado en MT5 y pregunta si seguiste el plan
py .\chequeo_previo.py resumen    # neto, win rate, R medio, plan seguido
```

`abrir` calcula el tamaño para no pasar tu riesgo (10 USD por defecto, `--riesgo-usd`) y muestra el R:R y los escenarios. También lista las razones para **no** entrar:

- el lote mínimo pasa tu riesgo
- el precio está del lado equivocado de la media de 200 días
- D1, H4 o H1 no acompañan
- estás persiguiendo un movimiento
- el stop está dentro del ruido normal
- el R:R es bajo
- el spread es caro
- cerraste una pérdida hace menos de una hora

Además te pregunta si entrás por plan, FOMO, venganza o aburrimiento. Lo anota en `DIARIO.md` (para leer) y en `diario_operaciones.jsonl` (para medir). **Nunca abre la operación**: eso lo hacés vos en MT5.

### Grabador del libro de órdenes

```powershell
py .\record_binance_book.py     # BTCUSDT, 20 niveles, 1 por segundo → logs/binance/book/
```

Hace lectura pública de Binance, sin clave. Se corta si cerrás la ventana o la PC se suspende.

### Stack local

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1   # swing + dashboard
python .\flask_dashboard.py                                          # dashboard en http://127.0.0.1:8000
```

El dashboard lee `bot_state.json`, que escribía la rotación. El swing todavía no tiene vista propia.

## Cómo se investiga una idea

1. **Pre-registro:** antes de mirar los datos se escribe un `trading/docs/PREREG_*.md` con la regla, los costos y la vara (n mínimo, p con Bonferroni, PF > 1). El script guarda el SHA-256 de ese archivo en su salida, y `.gitattributes` mantiene esos archivos byte por byte.
2. **Datos:** Binance spot de 1 minuto (`fetch_binance_1m.py`, `fetch_binance_1m_flow.py`) como precio, con el spread y el swap de Exness como costo.
3. **Train y holdout:** el train va de octubre de 2024 a enero de 2026. El holdout (febrero a septiembre de 2026) **ya se usó** para la reversión corta. Las ideas nuevas se prueban solo con datos nuevos: forward en demo o el libro de órdenes grabado.
4. **Antes de cualquier dinero real:** forward en demo, gate dual (Vibe-Trading + QuantConnect) y nota de risk-quant. Ver `AGENTS.md`.

Los scripts de investigación son los `analyze_*.py` de la raíz. Cada uno deja su resultado en `*_out.json`.

## Archivos principales

| Archivo | Rol |
|---|---|
| `swing_daily.py` | Motor del swing: detección de Exness o XBTFX, gates, loop diario |
| `trading/domain/swing_donchian.py` | Reglas del swing (entrada, salida, tamaño, swap, halt), sin I/O |
| `trading/application/swing_engine.py` | Ciclo diario: primero salidas, después entradas |
| `trading/infrastructure/mt5_swing_broker.py` | Adaptador MT5 del swing |
| `chequeo_previo.py`, `trading/domain/pretrade.py` | Chequeo previo y diario manual |
| `trading/domain/murphy_trend.py`, `murphy_rule.py` | Lectura de tendencia de Murphy (capítulo 4) y su regla |
| `record_binance_book.py` | Grabador del libro de Binance |
| `analyze_*.py` | Investigación (solo lectura) |
| `trading/backtest/qc/swing_donchian_qc.py` | El swing en QuantConnect |
| `bot_mt5.py`, `trading/domain/ensemble.py` | Rotación (apagada) |
| `telegram_notify.py`, `flask_dashboard.py` | Avisos por Telegram y dashboard local |

## Documentación

| Documento | Contenido |
|---|---|
| [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md) | Runbook del swing, evidencia, sección Exness y nota risk-quant |
| [`trading/docs/INTRADIA_EXNESS.md`](trading/docs/INTRADIA_EXNESS.md) | Todo lo medido en intradía en Exness |
| [`trading/docs/ESTABLISHED_FACTS.md`](trading/docs/ESTABLISHED_FACTS.md) | Hechos establecidos y lo ya descartado |
| `trading/docs/PREREG_*.md` | Pre-registros (Murphy intradía, flujo agresor, reversión corta) |
| [`trading/docs/MURPHY_ENGINE.md`](trading/docs/MURPHY_ENGINE.md) | Lector Murphy por fases |
| [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md) | Rotación apagada, como registro histórico |

## Logs

| Archivo | Contenido |
|---|---|
| `swing_trades.log` / `swing_exness_trades.log` | JSON por línea: `SWING_GATE`, `SWING_PLAN`, `SWING_OPEN`, `SWING_CLOSE`, `SWING_DRY_*` |
| `trades.log` | Rotación: `ENSEMBLE_VOTE`, `TRADE_OPEN`, `TRADE_CLOSE`, `ROTATION_BOOK` (histórico) |
| `logs/binance/` | Datos de Binance para la investigación (no se versionan) |

## Historial

- **2026-10-27/28:** se adapta el swing a Exness (dry-run). Investigación intradía en Exness, sin ventaja neta. Se suman el chequeo previo y el grabador del libro.
- **2026-10-10:** la terminal pasa a Exness. Arranca el lector Murphy en solo lectura.
- **2026-10-07:** se apaga la rotación, porque antes del spread daba cero. El swing pasa a ser el motor en la demo XBTFX.
- **2026-10-06:** se retiran el loop H4/M15 (`strategy_core.py`), el EV gate, el detector de régimen y el XAUUSD paralelo.
