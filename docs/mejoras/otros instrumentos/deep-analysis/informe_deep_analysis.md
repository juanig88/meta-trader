# Informe deep-analysis — IC Markets / bot_mt5 style

- **Generado:** 2026-08-08 19:03 UTC
- **Broker:** Raw Trading Ltd / ICMarketsSC-Demo (vía MT5)
- **Premisa:** muchas operaciones, poca ganancia por trade (lot `0.01`, ATR SL×1.5 / TP RR2, comisión RT ≈ `$7.0`/lot)
- **Universo analizado:** 154 símbolos (Forex/Crypto/Commodities/Indices/Bonds + stocks curados; **sin** el dump de 7k stocks)
- **OK con datos:** 151 | **fallidos/sin hist:** 3 | **prometedores (gate):** 12
- **Holdout OOS:** último 20% de barras M15

## 0. Veredicto ejecutivo (risk-quant)

Hay **12** instrumentos que pasan el gate IS/OOS con la premisa de scalping/breakout de muchas ops chicas. **No es aprobación live.** Es shortlist para paper/demo en paralelo con riesgo acotado (0.01, 1 posición, cooldown).

**Lunes en paralelo:** solo candidatos `promising=true` con ventana horaria sugerida, máx 2–4 símbolos, demo, y sin aumentar lot. Stocks masivos quedan fuera de esta corrida.

### 0.1 Shortlist accionable (prioridad risk-quant)

Los 12 “promising” **no** son 12 independientes. Hay correlación fuerte y traps de contrato:

| Prioridad | Símbolo | Por qué | Cuidado |
|---|---|---|---|
| **A (demo lun)** | `XAUUSD` | Ya es el path del bot; breakout IS/OOS PF≈1.12/1.26; ~16 ops/sem; SL$ ATR≈ típicamente chico | DD IS ~−$340 @0.01 — no subir lot |
| **A (demo lun)** | `XNGUSD` | PF OOS alto (≈1.51), muchas ops, energies spot | Gas = gaps/spreads feos; paper estricto |
| **B (opcional)** | `BTCUSD` | Mejor score del scan; muchas ops (~30/sem) | Edge histórico puede ser **régimen bull / BUY-heavy**; el forward previo ya había bloqueado enable sin filtro de régimen |
| **B (research)** | 1 solo cross XAU (`XAUCHF` o `XAUJPY`) | Métricas fuertes | **No** correr varios XAU* juntos: son el mismo factor oro |
| **C (evitar lun)** | `Cocoa_*`, `GCZ26_CFD` | Pasan gate numérico | Futuros con **roll/vencimiento**, N menor o contrato puntual |
| **C (evitar lun)** | `EURHKD` | Scalp PF ok | N chico (48 IS / 19 OOS) → ruido |

**Basket lunes sugerido (máx 3, demo, 0.01):** `XAUUSD` + `XNGUSD` + (opcional) `BTCUSD` solo en ventanas ART del informe.  
**No** sumar XAUEUR/XAUGBP/… en paralelo.  
Kill-switch: DD semanal < −$15 por símbolo o PF rolling 3d < 0.8 → apagar ese símbolo.

Ver también: [`icmarkets_sesiones_referencia.md`](./icmarkets_sesiones_referencia.md) (horarios contractuales IC Markets).

## 1. Metodología

### 1.1 Datos

- Fuente primaria: **MT5 `copy_rates_range`** (cuenta demo ICMarketsSC).
- Timeframes usados en el scan: **M15 + H4** (estrategias), **H1** (bias del scalp).
- Profundidad real observada en este broker/terminal (límites prácticos ~100k barras):
  - H4 ≈ 5 años
  - M15 ≈ 3–4 años (a veces menos en crypto)
  - M5/M1 **no** se usaron para backtest masivo (hist. insuficiente a 5y)
- Costos modelados: spread actual del símbolo + 25% spread como slip + comisión RT $7/lot.

### 1.2 Estrategias (espejo del bot)

| Path | Lógica | Salidas |
|---|---|---|
| `breakout` | `strategy_core.decide` hybrid: breakout M15 + EMA50/200 + ADX + H4 | ATR×1.5 SL / RR2 TP |
| `scalp` | Edge EMA9/21 + ADX≥25 + precio del lado de EMA9 + bias H1 EMA20 | ATR×1.5 SL / RR2 TP (como XAU parallel) |

### 1.3 Gate 'promising'

- IS trades ≥ 25, PF IS ≥ 1.1, avg trade > 0
- OOS PF ≥ 1.0 con suficientes trades OOS
- Score combina PF OOS/IS, trades/semana (frecuencia) y penaliza drawdown

### 1.4 Sesiones (contexto IC Markets + mercado)

Fuente broker: [IC Markets Trading Hours](https://www.icmarkets.sc/global/en/trading-pricing/trading-hours) (server GMT+2 / GMT+3 DST).

- **Forex:** 00:01–23:59 server (vie 23:57)
- **XAU/metales:** ~01:02–23:59 server
- **Energies spot:** XTI/XNG ~01:00–23:59; XBR con break diario
- **Índices US (US500/USTEC/US30):** ~01:00–23:59 server con micro-breaks
- **Crypto CFD:** casi 24/7 (con huecos cortos vie/sáb)
- **Stocks NAS/NYSE cash:** ~16:35–22:55 server; variantes 24HR distintas

Liquidez FX típica (no es edge garantizado): overlap **London–NY** (aprox 12:00–16:00 UTC / 09:00–13:00 ART). El scan **no asume** eso: propone ventanas según PnL histórico simulado por hora de entrada.

## 2. Shortlist prometedora

| # | Símbolo | Tipo | Strat | Score | IS n | IS PF | IS PnL$ | IS DD$ | OOS n | OOS PF | OOS PnL$ | ops/sem | Ventanas UTC | Ventanas ART |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | `BTCUSD` | Crypto | breakout | 24.16 | 3653 | 1.26 | 2558.06 | -208.27 | 909 | 1.32 | 702.60 | 30.6 | 01:00-13:00 UTC, 15:00-21:00 UTC, 22:00-23:00 UTC | 22:00-10:00 ART, 12:00-18:00 ART, 19:00-20:00 ART |
| 2 | `XNGUSD` | Commodities | breakout | 17.79 | 2810 | 1.27 | 1623.60 | -112.59 | 703 | 1.51 | 578.22 | 16.0 | 02:00-10:00 UTC, 11:00-23:00 UTC | 23:00-07:00 ART, 08:00-20:00 ART |
| 3 | `XAUCHF` | Commodities | breakout | 11.19 | 2744 | 1.17 | 1528.72 | -212.60 | 761 | 1.27 | 2156.01 | 15.5 | 01:00-02:00 UTC, 03:00-08:00 UTC, 09:00-11:00 UTC, 13:00-24:00 UTC | 22:00-23:00 ART, 00:00-05:00 ART, 06:00-08:00 ART, 10:00-21:00 ART |
| 4 | `XAUJPY` | Commodities | breakout | 11.14 | 2742 | 1.17 | 1412.83 | -193.91 | 792 | 1.35 | 2944.18 | 15.5 | 01:00-02:00 UTC, 03:00-10:00 UTC, 11:00-12:00 UTC, 13:00-24:00 UTC | 22:00-23:00 ART, 00:00-07:00 ART, 08:00-09:00 ART, 10:00-21:00 ART |
| 5 | `XAUGBP` | Commodities | breakout | 8.64 | 2690 | 1.18 | 1633.10 | -288.42 | 765 | 1.38 | 2976.67 | 15.3 | 01:00-02:00 UTC, 03:00-24:00 UTC | 22:00-23:00 ART, 00:00-21:00 ART |
| 6 | `XAUAUD` | Commodities | breakout | 5.73 | 2790 | 1.14 | 1331.35 | -286.14 | 735 | 1.36 | 2700.94 | 15.8 | 01:00-06:00 UTC, 07:00-11:00 UTC, 13:00-18:00 UTC, 19:00-24:00 UTC | 22:00-03:00 ART, 04:00-08:00 ART, 10:00-15:00 ART, 16:00-21:00 ART |
| 7 | `CocoaZ6_CFD` | Commodities | breakout | 5.34 | 174 | 1.17 | 9.64 | -7.12 | 58 | 1.48 | 12.33 | 6.5 | 11:00-16:00 UTC, 17:00-19:00 UTC, 20:00-21:00 UTC | 08:00-13:00 ART, 14:00-16:00 ART, 17:00-18:00 ART |
| 8 | `EURHKD` | Forex | scalp | 4.76 | 48 | 1.56 | 16.06 | -4.86 | 19 | 1.28 | 2.58 | 1.4 | 02:00-03:00 UTC, 16:00-17:00 UTC | 23:00-00:00 ART, 13:00-14:00 ART |
| 9 | `Cocoa_U6` | Commodities | breakout | 4.74 | 159 | 1.16 | 8.30 | -7.12 | 55 | 1.20 | 5.33 | 6.4 | 11:00-12:00 UTC, 13:00-14:00 UTC, 15:00-19:00 UTC, 20:00-21:00 UTC | 08:00-09:00 ART, 10:00-11:00 ART, 12:00-16:00 ART, 17:00-18:00 ART |
| 10 | `XAUEUR` | Commodities | breakout | 4.04 | 2790 | 1.16 | 1441.53 | -338.59 | 797 | 1.26 | 2198.68 | 15.8 | 01:00-06:00 UTC, 07:00-11:00 UTC, 13:00-18:00 UTC, 19:00-24:00 UTC | 22:00-03:00 ART, 04:00-08:00 ART, 10:00-15:00 ART, 16:00-21:00 ART |
| 11 | `XAUUSD` | Commodities | breakout | 0.94 | 2922 | 1.12 | 1141.17 | -342.20 | 759 | 1.26 | 2210.63 | 16.6 | 01:00-05:00 UTC, 06:00-07:00 UTC, 08:00-10:00 UTC, 13:00-18:00 UTC, 20:00-21:00 UTC, 22:00-24:00 UTC | 22:00-02:00 ART, 03:00-04:00 ART, 05:00-07:00 ART, 10:00-15:00 ART, 17:00-18:00 ART, 19:00-21:00 ART |
| 12 | `GCZ26_CFD` | Commodities | breakout | -4.25 | 720 | 1.33 | 2483.24 | -718.80 | 203 | 1.27 | 487.65 | 16.6 | 01:00-07:00 UTC, 08:00-11:00 UTC, 14:00-16:00 UTC, 17:00-18:00 UTC, 19:00-21:00 UTC, 22:00-24:00 UTC | 22:00-04:00 ART, 05:00-08:00 ART, 11:00-13:00 ART, 14:00-15:00 ART, 16:00-18:00 ART, 19:00-21:00 ART |

## 3. Fichas detalladas (prometedores)

### `BTCUSD` — Bitcoin (USD)

- **Tipo:** Crypto / —
- **Estrategia ganadora en scan:** `breakout` (score 24.16)
- **Histórico M15 usado:** 99989 barras ≈ 2.87y (2023-09-24T21:30:00+00:00 → 2026-08-08T18:15:00+00:00)
- **Spread actual (points):** 1200.0 | **$/point @0.01:** 0.0001 | **SL$ est. ATR×1.5:** 2.03
- **IS:** n=3653 PF=1.26 WR=41.2% PnL=$2558.06 avg=$0.700 DD=$-208.27 ops/sem=30.6
- **OOS:** n=909 PF=1.32 PnL=$702.60 DD=$-105.33
- **Horas top rango (UTC):** [17, 16, 18, 19, 20]
- **Ventanas sugeridas UTC:** 01:00-13:00 UTC, 15:00-21:00 UTC, 22:00-23:00 UTC
- **Ventanas sugeridas ART:** 22:00-10:00 ART, 12:00-18:00 ART, 19:00-20:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 11 | 162 | 1.61 | 185.05 | 1.142 |
| 10 | 134 | 1.61 | 172.53 | 1.288 |
| 19 | 216 | 1.55 | 340.80 | 1.578 |
| 05 | 159 | 1.53 | 212.90 | 1.339 |
| 17 | 370 | 1.53 | 524.92 | 1.419 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Mon | 953 | 1.47 | 1115.18 |
| Wed | 662 | 1.34 | 658.19 |
| Sun | 654 | 1.30 | 351.56 |
| Fri | 661 | 1.28 | 544.52 |
| Thu | 631 | 1.27 | 495.92 |

- **Breakout IS/OOS PF:** 1.26 / 1.32 (n 3653/909)
- **Scalp IS/OOS PF:** 0.86 / 0.61 (n 337/78)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XNGUSD` — Natural Gas vs US Dollar

- **Tipo:** Commodities / Energies / Energies Spot
- **Estrategia ganadora en scan:** `breakout` (score 17.79)
- **Histórico M15 usado:** 100197 barras ≈ 4.24y (2022-05-13T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0100 | **SL$ est. ATR×1.5:** 1.20
- **IS:** n=2810 PF=1.27 WR=40.3% PnL=$1623.60 avg=$0.578 DD=$-112.59 ops/sem=16.0
- **OOS:** n=703 PF=1.51 PnL=$578.22 DD=$-49.02
- **Horas top rango (UTC):** [16, 15, 17, 18, 14]
- **Ventanas sugeridas UTC:** 02:00-10:00 UTC, 11:00-23:00 UTC
- **Ventanas sugeridas ART:** 23:00-07:00 ART, 08:00-20:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 08 | 88 | 1.84 | 79.65 | 0.905 |
| 06 | 51 | 1.81 | 47.84 | 0.938 |
| 17 | 298 | 1.73 | 474.48 | 1.592 |
| 02 | 59 | 1.58 | 60.68 | 1.029 |
| 09 | 139 | 1.52 | 90.27 | 0.649 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Mon | 818 | 1.48 | 716.90 |
| Fri | 670 | 1.34 | 455.25 |
| Tue | 711 | 1.27 | 399.54 |
| Thu | 667 | 1.26 | 371.12 |
| Wed | 647 | 1.20 | 259.01 |

- **Breakout IS/OOS PF:** 1.27 / 1.51 (n 2810/703)
- **Scalp IS/OOS PF:** 0.97 / 1.09 (n 403/86)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUCHF` — Gold vs Swiss Franc

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 11.19)
- **Histórico M15 usado:** 100778 barras ≈ 4.26y (2022-05-05T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0124 | **SL$ est. ATR×1.5:** 11.43
- **IS:** n=2744 PF=1.17 WR=37.6% PnL=$1528.72 avg=$0.557 DD=$-212.60 ops/sem=15.5
- **OOS:** n=761 PF=1.27 PnL=$2156.01 DD=$-582.63
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-02:00 UTC, 03:00-08:00 UTC, 09:00-11:00 UTC, 13:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-23:00 ART, 00:00-05:00 ART, 06:00-08:00 ART, 10:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 09 | 168 | 2.15 | 626.73 | 3.731 |
| 04 | 153 | 1.47 | 304.77 | 1.992 |
| 22 | 74 | 1.46 | 185.68 | 2.509 |
| 03 | 117 | 1.35 | 270.62 | 2.313 |
| 07 | 114 | 1.34 | 185.01 | 1.623 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 707 | 1.38 | 1261.28 |
| Mon | 673 | 1.29 | 1087.61 |
| Thu | 667 | 1.18 | 589.03 |
| Tue | 751 | 1.14 | 459.85 |
| Wed | 707 | 1.08 | 286.95 |

- **Breakout IS/OOS PF:** 1.17 / 1.27 (n 2744/761)
- **Scalp IS/OOS PF:** 0.88 / 0.79 (n 277/103)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUJPY` — Gold vs Japanese Yen

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 11.14)
- **Histórico M15 usado:** 100699 barras ≈ 4.26y (2022-05-06T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0063 | **SL$ est. ATR×1.5:** 12.86
- **IS:** n=2742 PF=1.17 WR=37.7% PnL=$1412.83 avg=$0.515 DD=$-193.91 ops/sem=15.5
- **OOS:** n=792 PF=1.35 PnL=$2944.18 DD=$-922.99
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-02:00 UTC, 03:00-10:00 UTC, 11:00-12:00 UTC, 13:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-23:00 ART, 00:00-07:00 ART, 08:00-09:00 ART, 10:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 14 | 136 | 1.75 | 374.99 | 2.757 |
| 01 | 109 | 1.66 | 396.77 | 3.640 |
| 19 | 139 | 1.57 | 358.08 | 2.576 |
| 09 | 180 | 1.55 | 339.80 | 1.888 |
| 03 | 141 | 1.52 | 388.14 | 2.753 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 756 | 1.50 | 1677.58 |
| Mon | 659 | 1.39 | 1276.35 |
| Wed | 711 | 1.37 | 1140.43 |
| Thu | 684 | 1.16 | 505.50 |
| Tue | 724 | 0.93 | -242.86 |

- **Breakout IS/OOS PF:** 1.17 / 1.35 (n 2742/792)
- **Scalp IS/OOS PF:** 0.95 / 0.83 (n 255/97)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUGBP` — Gold vs Great Britain Pound

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 8.64)
- **Histórico M15 usado:** 100224 barras ≈ 4.24y (2022-05-13T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0135 | **SL$ est. ATR×1.5:** 11.55
- **IS:** n=2690 PF=1.18 WR=37.7% PnL=$1633.10 avg=$0.607 DD=$-288.42 ops/sem=15.3
- **OOS:** n=765 PF=1.38 PnL=$2976.67 DD=$-562.18
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-02:00 UTC, 03:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-23:00 ART, 00:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 22 | 84 | 2.32 | 443.88 | 5.284 |
| 09 | 168 | 2.11 | 591.89 | 3.523 |
| 21 | 94 | 1.54 | 240.93 | 2.563 |
| 03 | 118 | 1.50 | 350.17 | 2.968 |
| 10 | 181 | 1.50 | 333.76 | 1.844 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 684 | 1.60 | 1825.22 |
| Wed | 725 | 1.32 | 1096.89 |
| Mon | 665 | 1.29 | 1089.66 |
| Tue | 698 | 1.15 | 459.55 |
| Thu | 683 | 1.04 | 138.45 |

- **Breakout IS/OOS PF:** 1.18 / 1.38 (n 2690/765)
- **Scalp IS/OOS PF:** 1.17 / 0.97 (n 296/95)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUAUD` — Gold vs Australian Dollar

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 5.73)
- **Histórico M15 usado:** 100505 barras ≈ 4.25y (2022-05-10T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0071 | **SL$ est. ATR×1.5:** 11.10
- **IS:** n=2790 PF=1.14 WR=37.4% PnL=$1331.35 avg=$0.477 DD=$-286.14 ops/sem=15.8
- **OOS:** n=735 PF=1.36 PnL=$2700.94 DD=$-584.38
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-06:00 UTC, 07:00-11:00 UTC, 13:00-18:00 UTC, 19:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-03:00 ART, 04:00-08:00 ART, 10:00-15:00 ART, 16:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 09 | 164 | 1.84 | 492.52 | 3.003 |
| 21 | 88 | 1.62 | 215.77 | 2.452 |
| 17 | 254 | 1.50 | 631.02 | 2.484 |
| 19 | 143 | 1.47 | 382.05 | 2.672 |
| 15 | 232 | 1.46 | 443.71 | 1.913 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 716 | 1.55 | 1795.92 |
| Mon | 662 | 1.37 | 1316.72 |
| Tue | 731 | 1.16 | 536.63 |
| Wed | 722 | 1.13 | 459.12 |
| Thu | 694 | 0.98 | -76.09 |

- **Breakout IS/OOS PF:** 1.14 / 1.36 (n 2790/735)
- **Scalp IS/OOS PF:** 0.81 / 0.85 (n 241/91)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `CocoaZ6_CFD` — Cocoa -December 26 CFD

- **Tipo:** Commodities / Softs
- **Estrategia ganadora en scan:** `breakout` (score 5.34)
- **Histórico M15 usado:** 5717 barras ≈ 0.66y (2025-12-09T11:45:00+00:00 → 2026-08-07T20:15:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0100 | **SL$ est. ATR×1.5:** 0.75
- **IS:** n=174 PF=1.17 WR=42.5% PnL=$9.64 avg=$0.055 DD=$-7.12 ops/sem=6.5
- **OOS:** n=58 PF=1.48 PnL=$12.33 DD=$-8.65
- **Horas top rango (UTC):** [11, 12, 15, 18, 17]
- **Ventanas sugeridas UTC:** 11:00-16:00 UTC, 17:00-19:00 UTC, 20:00-21:00 UTC
- **Ventanas sugeridas ART:** 08:00-13:00 ART, 14:00-16:00 ART, 17:00-18:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 11 | 8 | 3.23 | 2.71 | 0.339 |
| 14 | 22 | 1.98 | 5.57 | 0.253 |
| 17 | 23 | 1.91 | 6.17 | 0.268 |
| 15 | 30 | 1.65 | 6.47 | 0.216 |
| 20 | 8 | 1.20 | 0.51 | 0.063 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Mon | 50 | 2.29 | 17.86 |
| Tue | 60 | 1.46 | 8.78 |
| Thu | 40 | 1.45 | 5.96 |
| Fri | 38 | 1.10 | 1.44 |
| Wed | 44 | 0.49 | -12.07 |

- **Breakout IS/OOS PF:** 1.17 / 1.48 (n 174/58)
- **Scalp IS/OOS PF:** 0.50 / 0.77 (n 32/13)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `EURHKD` — Euro vs Hong Kong Dollar

- **Tipo:** Forex / Exotics
- **Estrategia ganadora en scan:** `scalp` (score 4.76)
- **Histórico M15 usado:** 21867 barras ≈ 0.88y (2025-09-22T00:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0013 | **SL$ est. ATR×1.5:** 0.67
- **IS:** n=48 PF=1.56 WR=43.8% PnL=$16.06 avg=$0.335 DD=$-4.86 ops/sem=1.4
- **OOS:** n=19 PF=1.28 PnL=$2.58 DD=$-4.75
- **Horas top rango (UTC):** [17, 15, 16, 18, 10]
- **Ventanas sugeridas UTC:** 02:00-03:00 UTC, 16:00-17:00 UTC
- **Ventanas sugeridas ART:** 23:00-00:00 ART, 13:00-14:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 16 | 5 | 5.79 | 6.65 | 1.330 |
| 02 | 5 | 2.68 | 3.00 | 0.600 |
| 11 | 6 | 0.85 | -0.64 | -0.107 |
| 10 | 11 | 0.53 | -3.17 | -0.288 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 8 | 3.31 | 6.11 |
| Wed | 15 | 2.88 | 10.08 |
| Mon | 21 | 1.37 | 5.03 |
| Tue | 13 | 1.25 | 1.83 |
| Thu | 10 | 0.49 | -4.40 |

- **Breakout IS/OOS PF:** 0.99 / 0.85 (n 635/146)
- **Scalp IS/OOS PF:** 1.56 / 1.28 (n 48/19)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `Cocoa_U6` — Cocoa -September 26 CFD

- **Tipo:** Commodities / Softs
- **Estrategia ganadora en scan:** `breakout` (score 4.74)
- **Histórico M15 usado:** 5472 barras ≈ 0.66y (2025-12-09T11:45:00+00:00 → 2026-08-07T20:15:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0100 | **SL$ est. ATR×1.5:** 0.76
- **IS:** n=159 PF=1.16 WR=42.1% PnL=$8.30 avg=$0.052 DD=$-7.12 ops/sem=6.4
- **OOS:** n=55 PF=1.20 PnL=$5.33 DD=$-4.44
- **Horas top rango (UTC):** [11, 12, 15, 18, 17]
- **Ventanas sugeridas UTC:** 11:00-12:00 UTC, 13:00-14:00 UTC, 15:00-19:00 UTC, 20:00-21:00 UTC
- **Ventanas sugeridas ART:** 08:00-09:00 ART, 10:00-11:00 ART, 12:00-16:00 ART, 17:00-18:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 11 | 8 | 1.86 | 1.47 | 0.184 |
| 15 | 27 | 1.73 | 6.20 | 0.230 |
| 17 | 22 | 1.51 | 3.87 | 0.176 |
| 13 | 27 | 1.44 | 3.99 | 0.148 |
| 20 | 8 | 1.20 | 0.51 | 0.063 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Tue | 56 | 1.44 | 8.16 |
| Thu | 43 | 1.39 | 5.77 |
| Mon | 30 | 1.37 | 3.61 |
| Fri | 36 | 1.22 | 2.72 |
| Wed | 49 | 0.72 | -6.63 |

- **Breakout IS/OOS PF:** 1.16 / 1.20 (n 159/55)
- **Scalp IS/OOS PF:** 0.38 / 0.81 (n 30/7)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUEUR` — Gold vs Euro

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 4.04)
- **Histórico M15 usado:** 100227 barras ≈ 4.24y (2022-05-13T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0116 | **SL$ est. ATR×1.5:** 11.52
- **IS:** n=2790 PF=1.16 WR=37.3% PnL=$1441.53 avg=$0.517 DD=$-338.59 ops/sem=15.8
- **OOS:** n=797 PF=1.26 PnL=$2198.68 DD=$-774.43
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-06:00 UTC, 07:00-11:00 UTC, 13:00-18:00 UTC, 19:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-03:00 ART, 04:00-08:00 ART, 10:00-15:00 ART, 16:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 21 | 85 | 1.60 | 234.87 | 2.763 |
| 09 | 183 | 1.57 | 377.82 | 2.065 |
| 03 | 121 | 1.54 | 368.57 | 3.046 |
| 19 | 143 | 1.51 | 417.10 | 2.917 |
| 14 | 138 | 1.47 | 267.94 | 1.942 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 710 | 1.62 | 1902.27 |
| Mon | 745 | 1.42 | 1565.37 |
| Wed | 706 | 1.12 | 443.04 |
| Thu | 668 | 0.96 | -132.83 |
| Tue | 758 | 0.96 | -137.65 |

- **Breakout IS/OOS PF:** 1.16 / 1.26 (n 2790/797)
- **Scalp IS/OOS PF:** 1.07 / 0.70 (n 288/98)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `XAUUSD` — Gold vs US Dollar

- **Tipo:** Commodities / Metals
- **Estrategia ganadora en scan:** `breakout` (score 0.94)
- **Histórico M15 usado:** 100018 barras ≈ 4.23y (2022-05-17T08:15:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 40.0 | **$/point @0.01:** 0.0100 | **SL$ est. ATR×1.5:** 12.39
- **IS:** n=2922 PF=1.12 WR=36.2% PnL=$1141.17 avg=$0.391 DD=$-342.20 ops/sem=16.6
- **OOS:** n=759 PF=1.26 PnL=$2210.63 DD=$-781.85
- **Horas top rango (UTC):** [16, 17, 15, 18, 4]
- **Ventanas sugeridas UTC:** 01:00-05:00 UTC, 06:00-07:00 UTC, 08:00-10:00 UTC, 13:00-18:00 UTC, 20:00-21:00 UTC, 22:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-02:00 ART, 03:00-04:00 ART, 05:00-07:00 ART, 10:00-15:00 ART, 17:00-18:00 ART, 19:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 17 | 299 | 1.78 | 1083.22 | 3.623 |
| 09 | 183 | 1.68 | 399.22 | 2.182 |
| 06 | 120 | 1.63 | 281.85 | 2.349 |
| 02 | 82 | 1.49 | 246.18 | 3.002 |
| 03 | 127 | 1.45 | 306.19 | 2.411 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 740 | 1.53 | 1687.34 |
| Mon | 710 | 1.23 | 855.02 |
| Thu | 708 | 1.08 | 280.67 |
| Wed | 724 | 1.08 | 289.12 |
| Tue | 799 | 1.07 | 239.64 |

- **Breakout IS/OOS PF:** 1.12 / 1.26 (n 2922/759)
- **Scalp IS/OOS PF:** 0.78 / 0.70 (n 318/91)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

### `GCZ26_CFD` — Gold Futures -December26

- **Tipo:** Commodities / Metal Futures
- **Estrategia ganadora en scan:** `breakout` (score -4.25)
- **Histórico M15 usado:** 24608 barras ≈ 1.04y (2025-07-23T01:00:00+00:00 → 2026-08-07T23:45:00+00:00)
- **Spread actual (points):** 0.0 | **$/point @0.01:** 0.0100 | **SL$ est. ATR×1.5:** 12.28
- **IS:** n=720 PF=1.33 WR=39.9% PnL=$2483.24 avg=$3.449 DD=$-718.80 ops/sem=16.6
- **OOS:** n=203 PF=1.27 PnL=$487.65 DD=$-267.03
- **Horas top rango (UTC):** [16, 17, 15, 4, 18]
- **Ventanas sugeridas UTC:** 01:00-07:00 UTC, 08:00-11:00 UTC, 14:00-16:00 UTC, 17:00-18:00 UTC, 19:00-21:00 UTC, 22:00-24:00 UTC
- **Ventanas sugeridas ART:** 22:00-04:00 ART, 05:00-08:00 ART, 11:00-13:00 ART, 14:00-15:00 ART, 16:00-18:00 ART, 19:00-21:00 ART

| Hora UTC | n | PF | PnL$ | avg$ |
|---:|---:|---:|---:|---:|
| 09 | 40 | 2.90 | 436.92 | 10.923 |
| 17 | 76 | 2.29 | 829.77 | 10.918 |
| 22 | 26 | 2.11 | 266.54 | 10.252 |
| 04 | 48 | 1.77 | 275.44 | 5.738 |
| 23 | 24 | 1.71 | 149.87 | 6.245 |

| Día | n | PF | PnL$ |
|---|---:|---:|---:|
| Fri | 170 | 1.88 | 1349.86 |
| Mon | 197 | 1.51 | 1044.74 |
| Wed | 195 | 1.21 | 392.28 |
| Tue | 188 | 1.13 | 228.24 |
| Thu | 173 | 0.98 | -44.24 |

- **Breakout IS/OOS PF:** 1.33 / 1.27 (n 720/203)
- **Scalp IS/OOS PF:** 0.72 / 1.09 (n 89/16)

**Failure mode:** si el edge depende de un régimen único (ej. solo BUY en bull crypto), o si el spread vivo >> spread del scan, el PF OOS colapsa. Paper primero.

## 4. Casi-prometedores (no pasan gate, útiles para research)

| Símbolo | Tipo | Strat | Score | IS PF | OOS PF | IS n | ops/sem |
|---|---|---|---:|---:|---:|---:|---:|
| `US30` | Indices | breakout | 5.75 | 1.09 | 1.05 | 2737 | 15.5 |
| `XAGEUR` | Commodities | breakout | 5.60 | 1.09 | 1.30 | 2713 | 15.4 |
| `MidDE60` | Indices | breakout | 3.89 | 1.07 | 1.27 | 472 | 8.0 |
| `IT40` | Indices | breakout | 3.62 | 1.09 | 1.10 | 2324 | 11.2 |
| `GBPJPY` | Forex | breakout | 3.53 | 1.14 | 0.96 | 2899 | 17.3 |
| `MidDE50` | Indices | breakout | 2.74 | 1.07 | 1.13 | 1703 | 8.2 |
| `XAGAUD` | Commodities | breakout | 2.02 | 1.05 | 1.22 | 2716 | 15.4 |

## 5. Ranking completo por tipo (mejor score)

### Bonds CFDs

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `EURBBL_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `EURBND_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `EURSCA_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `ITB10Y_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `JGB10Y_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `UKGB_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `UST05Y_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `UST10Y_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `UST30Y_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

### Commodities

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `XNGUSD` | breakout | 17.79 | 1.27 | 1.51 | 2810 | 16.0 | YES |
| `XAUCHF` | breakout | 11.19 | 1.17 | 1.27 | 2744 | 15.5 | YES |
| `XAUJPY` | breakout | 11.14 | 1.17 | 1.35 | 2742 | 15.5 | YES |
| `XAUGBP` | breakout | 8.64 | 1.18 | 1.38 | 2690 | 15.3 | YES |
| `XAUAUD` | breakout | 5.73 | 1.14 | 1.36 | 2790 | 15.8 | YES |
| `XAGEUR` | breakout | 5.60 | 1.09 | 1.30 | 2713 | 15.4 | no |
| `CocoaZ6_CFD` | breakout | 5.34 | 1.17 | 1.48 | 174 | 6.5 | YES |
| `Cocoa_U6` | breakout | 4.74 | 1.16 | 1.20 | 159 | 6.4 | YES |
| `XAUEUR` | breakout | 4.04 | 1.16 | 1.26 | 2790 | 15.8 | YES |
| `XAGAUD` | breakout | 2.02 | 1.05 | 1.22 | 2716 | 15.4 | no |
| `XAUUSD` | breakout | 0.94 | 1.12 | 1.26 | 2922 | 16.6 | YES |
| `GCZ26_CFD` | breakout | -4.25 | 1.33 | 1.27 | 720 | 16.6 | YES |
| `BR_V6_CFD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `Coffee_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `Corn_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

### Crypto

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `BTCUSD` | breakout | 24.16 | 1.26 | 1.32 | 3653 | 30.6 | YES |
| `ADAUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AVXUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `BCHUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `BNBUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `DATAUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `DOGUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `DOTUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `ETHUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `KSMUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `LNKUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `LTCUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `POLUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `SOLUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `UNIUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

### Forex

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `EURHKD` | scalp | 4.76 | 1.56 | 1.28 | 48 | 1.4 | YES |
| `GBPJPY` | breakout | 3.53 | 1.14 | 0.96 | 2899 | 17.3 | no |
| `AUDCAD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AUDCHF` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AUDJPY` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AUDNZD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AUDSGD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AUDUSD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CADCHF` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CADJPY` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CHFJPY` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CHFSGD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `EURAUD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `EURCAD` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `EURCHF` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

### Indices

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `US30` | breakout | 5.75 | 1.09 | 1.05 | 2737 | 15.5 | no |
| `MidDE60` | breakout | 3.89 | 1.07 | 1.27 | 472 | 8.0 | no |
| `IT40` | breakout | 3.62 | 1.09 | 1.10 | 2324 | 11.2 | no |
| `MidDE50` | breakout | 2.74 | 1.07 | 1.13 | 1703 | 8.2 | no |
| `AUS200` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CA60` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CHINA50` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `CHINAH` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `DE40` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `DXY_U6` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `ES35` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `F40` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `HK50` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `JP225` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `NETH25` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

### Stock CFD's (curated)

| Símbolo | Strat | Score | IS PF | OOS PF | IS n | ops/sem | Promising |
|---|---|---:|---:|---:|---:|---:|:---:|
| `AAPL.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AMD.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `AMZN.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `MELI.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `MSFT.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `NFLX.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `NVDA.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |
| `TSLA.NAS` | none | -999.00 | 0.00 | 0.00 | 0 | 0.0 | no |

## 6. Plan lunes (operativo, demo)

1. Elegir **máximo 3** de la shortlist (diversificar tipos: ej. 1 metal/índice + 1 FX + 1 crypto).
2. Correr solo en **ventanas ART sugeridas**; fuera de ventana = flat.
3. Lot **0.01**, 1 posición/símbolo, cooldown ≥ 5 min, magic distinto por símbolo.
4. Objetivo: validar si el PF paper ≈ OOS; no optimizar mid-week.
5. Kill-switch: si un símbolo hace DD < −$15 en la semana o PF rolling < 0.8 → apagar.

## 7. Limitaciones (leer antes de emocionarse)

- No es walk-forward multiperiodo ni Monte Carlo; es un holdout temporal simple.
- Spread del scan = snapshot; en news el costo real es mayor.
- M1/M5 5y **no disponibles** de forma usable en este terminal → el edge microescala no se midió.
- Escanear ~100+ símbolos implica **data snooping**: algunos 'promising' serán suerte.
- Stocks (7k) excluidos a propósito; solo un puñado curado.
- Futuros con vencimiento (`_U6`, etc.) pueden tener hist. corta / roll → tratar con cuidado.
- Aprobación paper/live formal del repo sigue pidiendo dual-backtest (Vibe + QC); este informe es research MT5.

## 8. Errores / sin datos

| Error | n |
|---|---:|
| `symbol_info_none` | 2 |
| `insufficient_history` | 1 |

<details><summary>Símbolos fallidos</summary>

- `SIN26`: insufficient_history
- `GOOGL.NAS`: symbol_info_none
- `META.NAS`: symbol_info_none

</details>

## 9. Cómo regenerar

```bash
python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py"
python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py" --resume
python "docs/mejoras/otros instrumentos/deep-analysis/deep_instrument_scan.py" --report-only
```

Progreso parcial: `results/scan_progress.jsonl`

