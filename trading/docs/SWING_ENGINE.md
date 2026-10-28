# Swing diario Donchian 20/10 + SMA200 (no vivo)

**Motor vivo en la demo XBTFX desde 2026-10-07** (reemplaza a la rotación, que quedó apagada). Decide una vez por día UTC sobre la vela diaria cerrada. Dry-run por defecto en el código; en `.env` está `BOT_SWING_DRY_RUN=0` + `BOT_SWING_DEMO_LIVE=1`. Gate dual cumplido (Vibe + QuantConnect): aprobado por risk-quant solo para demo (ver "Nota risk-quant").

Estado al encender (2026-10-07 17:27 UTC): cuenta plana, equity 9,928 USD, rotación detenida con `kill_switch.halt` (`rotacion_reemplazada_por_swing`, el swing no lo honra: `BOT_SWING_HONOR_ROTATION_HALT=0`). Último deal de rotación 17:18 UTC; el guard de 2 h libera a las 19:18 UTC. Primera decisión con órdenes: 2026-10-08 00:10 UTC. Freno manual del swing: crear `swing_kill_switch.halt`.

## Exness (dry-run desde 2026-10-27)

Desde 2026-10-10 la terminal es Exness (`Exness-MT5Trial11`, demo). El swing la detecta por el nombre del servidor y cambia tres cosas:

- **Universo:** `BTCUSDm` y `ETHUSDm` (`murphy_universe.resolve_universe`), salvo que `BOT_SWING_SYMBOLS` diga otra cosa.
- **Archivos propios:** `swing_exness_state.json`, `swing_exness_kill_switch.halt` y `swing_exness_trades.log`. El estado de XBTFX tiene un pico de equity de 9,928 USD; con los 200 USD de Exness habría disparado el halt por drawdown.
- **Siempre dry-run**, aunque `.env` tenga `BOT_SWING_DRY_RUN=0` + `BOT_SWING_DEMO_LIVE=1`. La nota risk-quant aprobó XBTFX con 79 CFD, no Exness con dos símbolos. Para mandar órdenes hace falta una nota nueva y sacar el forzado en `swing_daily.run_once`.

Costos de Exness, leídos en MT5 el 2026-10-27:

| | Spread | Swap largo |
|---|---|---|
| BTCUSDm | 0.008–0.012% | −8.1%/año (−1786.5 puntos) |
| ETHUSDm | 0.04% | −8.2%/año |

- El swap es triple el viernes (`swap_rollover3days` 5), lo que da ~10.4%/año. Pasa el filtro de 30%.
- No hay comisión en la cuenta Standard (no verificado por API).

Backtest con esos costos (`analyze_swing_regime.py --cost-side 0.0002 --swap-year 0.104`, salida `analyze_swing_regime_exness_out.json`, exposición 1x por manga, BTC+ETH equiponderado):

| Tramo | CAGR | Sharpe | DD |
|---|---|---|---|
| 2018-03 → 2026-09 | 32.4% | 0.98 | −34.0% |
| A (2018–2023) | 41.6% | 1.09 | −34.0% |
| B (2023–2026) | 17.4% | 0.74 | −30.7% |

- Con costos XBTFX daba 27.5% / 0.87 / −36.5%.
- Por año: 2018 −9.1%, 2022 0%, 2025 −4.6%, 2026 +3.1%.
- Son ~60 trades en 8.5 años: muestra chica.

**Tamaño:** con 200 USD de equity y 0.5% de riesgo, el riesgo es 1 USD por trade. El lote mínimo de BTC (0.01) con el stop en el mínimo de 10 días arriesga ~76 USD (2026-10-27: entrada sobre 86,687, mínimo 79,117). Todas las entradas saldrían `bajo_volume_min`. Hace falta un saldo demo de ~15,000 USD para BTC y ~9,000 para ETH. Con 20,000 entran los dos.

Primer dry-run (2026-10-27): 2 símbolos, 0 entradas, 0 salidas. Los dos están sobre la SMA200 y bajo el máximo de 20 días: BTC necesita cerrar sobre 86,687 (+8.3%) y ETH sobre 2,724 (+17%).

## Evidencia (2026-10-06/07)

- **Swap XBTFX ~20%/año del nocional**, largo y corto, 77 de 79 crypto (HYP 9%, SHB 89%). Buy & hold BTC+ETH en CFD: 0% CAGR 2018–2026 (spot +23%).
- **SFE Crypto Master (SignalStart, 947 trades): su estructura intradía no tiene edge** (`analyze_sfe_replica.py`, `analyze_sfe_fingerprint.py`, `analyze_sfe_impulse.py`). Impulso 4 h a favor + bracket ~0.5 ATR + time-stop 7 h: PF 0.94–1.06 en BTC/ETH 5m 2018–2026, dos motores. Sus entradas reales con nuestras salidas sí (PF 2.24): la selección no sale de OHLCV. A 7 h ni comprar siempre paga el 0.1% de costo; el ciclo se captura a 5–20 días.
- **Donchian 20/10 + SMA200 diario, solo largos: sobrevive a costos (0.10%/lado) y swap** (`analyze_swing_regime.py`, Binance spot diario):
  - BTC+ETH 2018-03 → 2026-09: CAGR 27.5%, Sharpe 0.87, DD −36.5% (buy & hold CFD 0%, DD −85%). Vibe coincide (+476% / +44% por tramo, DD −37% / −32%).
  - 74 altcoins: CAGR 21.4%, DD −43%. Sesgo de supervivencia (universo XBTFX de hoy).
  - 9 vecinos de parámetros (entrada 10–55, salida 5–20) positivos en los dos tramos. Sin SMA200, 2023–2026 cae a ~0.
  - BTC Bitstamp 2011–2018 (sin tocar parámetros): pasa (`analyze_swing_pre2018.py`). LTC 2014–2018 falla solo por DD.
  - Captura parte de la suba (2020 +145% vs +393%; 2024 +25% vs +78%) y esquiva bajistas (2018 −10% vs −76%; 2022 0% vs −66%). ~30 trades por símbolo en 8.5 años: muestra chica.

## Reglas

| Qué | Regla | Código |
|---|---|---|
| Universo | Las 79 de `CRYPTO_UNIVERSE` (o `BOT_SWING_SYMBOLS`) | `trading/domain/crypto_universe.py` |
| Entrada | Cierre > máximo de los 20 días previos **y** cierre > SMA200. Solo largos | `swing_donchian.entry_signal` |
| Salida | Cierre < mínimo de los 10 días previos **o** cierre < SMA200 | `swing_donchian.exit_reason` |
| Orden del día | Primero salidas, después entradas en los cupos libres | `swing_donchian.plan_day` |
| Ranking | Si hay más señales que cupos, momentum de 90 días | `plan_day` |
| Tamaño | `riesgo × equity / (cierre − mínimo 10d)`, redondeado **hacia abajo** al step. Si no llega al lote mínimo, no entra (`bajo_volume_min`) | `swing_donchian.size_lots` |
| Riesgo | 0.5% del equity por trade, 10 posiciones máximo (`BOT_SWING_RISK_FRAC`, `BOT_SWING_MAX_POSITIONS`). Cartera 2018–2026: CAGR 19%, Sharpe 0.91, PF 1.92, DD −28.6%. Con 1%/10 el DD llega a −47.8% | V3, `analyze_swing_portfolio.py` |
| SL en bróker | Stop de desastre a 2× la distancia al mínimo de 10 días. La salida normal es por cierre diario, no intradía | `plan_day` |
| Swap | No entra si el swap largo supera 30%/año del nocional (`swap_caro`). Modos de swap desconocidos cuentan como prohibitivos | `mt5_swing_broker._swap_pct` |
| Halt | Equity ≤ 65% del pico → escribe `swing_kill_switch.halt`, no abre más; las salidas siguen. Clear humano: borrar el archivo | `drawdown_halt`, `swing_daily.py` |

Velas: `copy_rates_from_pos(D1, desde 1)` solo cerradas. El servidor XBTFX está en UTC y la D1 abre 00:00 UTC (igual que Binance del backtest). Si más de la mitad del universo tiene vela vieja, no marca el día como corrido y reintenta al minuto siguiente.

Swap verificado en MT5 el 2026-10-07: BTC `point` 0.001, `swap_long` −46029.79 pts, contrato 1 → −46.0 USD/lote/día = −20.2%/año. ETH −20.6%, SOL −20.2%. Coincide con el 21% usado en la investigación.

Paridad: `levels`/`entry_signal`/`exit_reason` día a día contra la máquina de estados del backtest (`don_pos`): 0 diferencias en 17,580 días (BTC, ETH, SOL, LTC, DOGE, LINK).

## Gates para mandar órdenes (todos)

1. `BOT_SWING_DRY_RUN=0` (default 1). `--dry-run` en la línea de comando lo fuerza.
2. `BOT_SWING_DEMO_LIVE=1` (default 0). **No** usa `PAPER_DEMO_LIVE` de la rotación.
3. Terminal XBTFX y cuenta demo (`resolve_demo_execution`).
4. Entradas bloqueadas si:
   - el magic de rotación (260414) tiene posiciones o deals en las últimas `BOT_SWING_ROTATION_LOOKBACK_H` horas (2) en esa cuenta → `cuenta_compartida_con_rotacion`. La rotación chequea un slot por símbolo sin mirar el magic y su kill switch diario (−25 USD) saltaría con las oscilaciones del swing.
   - existe `swing_kill_switch.halt` → `swing_halt`
   - existe `kill_switch.halt` de rotación y `BOT_SWING_HONOR_ROTATION_HALT=1` → `kill_switch_halt`
   - drawdown ≥ 35% → `drawdown_halt`

Las salidas de posiciones del magic swing (260501) se mandan aunque las entradas estén bloqueadas, siempre que pasen 1–3.

## Correr

```powershell
python swing_daily.py --once --dry-run   # decide hoy, no manda nada
python swing_daily.py                    # loop: una decisión por día después de 00:10 UTC; imprime "swing vivo | ... | proxima ..." al arrancar y cada hora
```

Cuenta propia: `BOT_SWING_MT5_PATH` (otra instalación de MT5), `BOT_SWING_MT5_LOGIN` / `PASSWORD` / `SERVER`. Sin eso se conecta a la terminal por defecto (la de rotación) y las entradas quedan bloqueadas.

Estado: `swing_state.json` (`last_run_day`, `equity_peak`). Variables: `.env.example` sección (N).

El Telegram de las 00:10 (hora Argentina) sigue siendo la tarea `MetaTrader Daily Report`. Desde 2026-10-09 ese texto agrega la decisión del swing que cayó en el día local (la de las 21:10, cuyo `day` es el UTC del día siguiente) y, si sigue el archivo, `rotation halt`. La rotación apagada deja el bloque 4/5 en cero; eso no es un fallo del swing.

## Log (`swing_trades.log`, JSON por línea)

| Evento | Cuándo |
|---|---|
| `SWING_GATE` | Cada corrida: cuenta, gate demo, `block_reason`, riesgo |
| `SWING_PLAN` | Equity, abiertas, salidas, entradas, skips (`swap_caro`, `tope_posiciones`, `bajo_volume_min`, `stop_invalido`), `stale_bars` |
| `SWING_OPEN` / `SWING_OPEN_FAIL` | Orden de compra: lotes, `entry_ref`, `exit_low`, `disaster_sl`, `risk_usd`, `momentum_90d`, retcode |
| `SWING_CLOSE` / `SWING_CLOSE_FAIL` | `why_closed` = `bajo_minimo_10d` / `bajo_sma200` |
| `SWING_DRY_OPEN` / `SWING_DRY_CLOSE` | Lo mismo sin orden (dry-run) |
| `SWING_SKIP` | Entrada válida bloqueada por gate (`block_reason`) |
| `SWING_ABORT` | Sin cuenta MT5 |

## Primer dry-run (2026-10-07, cuenta de rotación)

79 símbolos, ninguna vela vieja, 0 entradas y 0 salidas. 69 de 79 sobre SMA200, ninguno cerró sobre el máximo de 20 días (el más cerca, TETUSD! = Theta, a −1.3%; BTC −2.1%). Gate: `BOT_DRY_RUN` + `cuenta_compartida_con_rotacion`.

## Nota risk-quant (2026-10-07)

**Veredicto: aprobado solo para demo XBTFX en cuenta separada, con 0.5% / 10 posiciones. No para dinero real.**

Evidencia citada:
- Vibe-Trading: BTC-USDT/ETH-USDT diario, tramos 2018-03 → 2023-08 y 2023-09 → 2026-09: +476% / +44%, DD −37% / −32% (runs `swing_don_A`, `swing_don_B`).
- QuantConnect (IDE web, Coinbase diario, 0.10%/lado + swap 20%/año): `sleeve`+`majors` CAGR 27.0%, DD −38.8%; `risk`+`alts` CAGR 8.6%, DD −29.5%. Los cuatro criterios fijados antes de correr se cumplen.
- Motor propio (Binance spot diario, specs XBTFX): cartera 0.5%/10 CAGR 19.0%, Sharpe 0.91, Sortino 1.12, DD −28.6%, WR 29.3%, PF 1.92, 587 trades. OOS extra: BTC Bitstamp 2011–2018 pasa sin tocar parámetros. 9 vecinos de parámetros positivos.

Qué es y qué no: es **trend-following (momentum de serie temporal), un factor conocido**, no alpha nuevo. Agrega sobre la regla más simple del mismo factor (tener mientras cierre > SMA200): BTC+ETH Sharpe 0.87 contra 0.63 y DD −36.5% contra −65%; altcoins CAGR 21.4% contra 5.3%. Excepción: BTC+ETH 2023–2026 la SMA200 sola rindió más (20.8% contra 12.9%). Se aprueba como motor de exposición a ciclos alcistas con control de caída, que es el objetivo. El zoo de 460 alphas de Vibe es de sección cruzada en acciones y no aplica a una regla de serie temporal en crypto; no se corrió.

Qué esperar: CAGR entre ~9% (QC, 40 pares) y ~19% (79 CFD); DD de hasta −30% con riesgo 0.5%/10; win rate ~30% (pierde seguido, gana grande); años planos o negativos (2018–2019 ~0%, 2022 −12%, 2025 entre −5% y +9%). Peor día del backtest −8.7%.

Escenarios que lo rompen:
- Mercado lateral sobre la SMA200 con rupturas falsas repetidas (2025, majors 2023–2026): pierde de a poco en costos y swap.
- Swap XBTFX sube sobre 30%/año: el filtro bloquea entradas; con 40%+ en todo el universo el edge neto desaparece.
- Caída con gap (el stop de desastre está a 2× la distancia al mínimo de 10 días; la salida normal es al cierre diario).
- Sesgo de supervivencia: el universo es el de XBTFX hoy. QC con 40 pares dio la mitad del CAGR: usar ~9% como expectativa conservadora.

Condiciones:
- No convivir con la rotación en la misma cuenta (el gate `cuenta_compartida_con_rotacion` lo exige). Cumplido el 2026-10-07 apagando la rotación: la cuenta demo es la misma.
- No cambiar 20/10/200/90, 0.5% ni 10 posiciones sin nueva validación en los dos motores.
- Halt automático a −35% de DD. Revisar en demo a los 6 meses o 40 trades cerrados: si el DD supera −25% o el PF de demo es < 1.0 con ≥ 40 trades, frenar y revisar.
- Gate de despliegue (`tech-lead.mdc`): la regla dice que paper/vivo va por QuantConnect MCP; este motor corre en MT5 demo como la rotación. Desvío consciente que decide el usuario.

## Seguimiento

- Revisión a los 6 meses o 40 trades cerrados (ver condiciones de la nota risk-quant).
- `swing_daily.py` corre como proceso de larga vida; si se cierra la terminal, se detiene. Al reiniciar no repite el día ya decidido (`swing_state.json`). Algoritmo listo: `trading/backtest/qc/swing_donchian_qc.py` (Coinbase diario, 0.10%/lado, swap 20%/año). Criterios de acuerdo fijados en su docstring antes de correr: `sleeve`+`majors` CAGR > 0 en los dos tramos, CAGR total 27.5% ± 10 pp, DD ≥ −45%; `risk`+`alts` CAGR > 0, DD ≥ −35%. Corrido en el IDE web gratis de QC (el MCP local pide plan pago). Logs: `docs/quantconnect/logs/`.
  - `risk`+`alts` (2026-10-07, Coinbase, 2018-03-05 → 2026-07-10, fin de datos QC): **pasa**. CAGR 8.6%, DD −29.5%, total +98.5%. Tramo A +6.6%/año (DD −28.7%), tramo B +12.5%/año (DD −15.5%). El DD coincide con V3 (−28.6%); el CAGR es la mitad (19% en V3 con 79 CFD XBTFX contra 40 pares Coinbase).
  - `sleeve`+`majors` (2026-10-07, corrida `f757f62d…`): **pasa**. CAGR 27.0% (nuestro 27.5%), DD −38.8% (−36.5%), tramo A +34.6%/año (+36%), tramo B +13.6%/año (+13%). Las dos corridas previas fueron inválidas por bugs de rebalanceo del algoritmo en la cuenta cash, no de la estrategia.
- Cuenta demo XBTFX separada.
