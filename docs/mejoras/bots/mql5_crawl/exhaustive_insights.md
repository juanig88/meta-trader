# MQL5 Exhaustive Insights — qué es realmente útil

- Archivos CSV: **2375** | trades parseados: **2,563,469**
- Signals con PnL: **2375** | rentables (net>0): **88.76%**
- Share grid/martingale: **87.8%**
- Net PnL agregado (todas las curves, unidades nativas mezcladas): **312,526,887.1**
- Media net grid vs no-grid: **149266.69** vs **4001.97**
- Concentración: top 5% signals positivos aportan **98%** del PnL positivo

## ¿Hay ganancias reales?

Sí en el sentido contable del export: ~88.76% de signals cierran net>0 en el histórico exportado. Pero **no es edge clonable**: 88% son grid/martingale; el PnL positivo está muy concentrado (top 5% ≈ 98%). Mezclar monedas/cuentas hace el total agregado solo orientativo.

Entre **aplicables** (sin grid, 1–2 pos): n=56, RR medio=0.892, con RR≥1.3: 9, con RR<0.6 (high-WR frágil): 26.

## Instrumentos más operados (todos los trades)

| Symbol | Trades | PnL sum | WR% |
|---|---:|---:|---:|
| XAUUSD | 790226 | 143719312.29 | 64.36 |
| EURUSD | 157800 | 11624322.65 | 71.09 |
| BTCUSD | 142860 | 55003955.99 | 55.19 |
| AUDCAD | 118036 | 1430898.4 | 75.99 |
| USDJPY | 84699 | 9796074.02 | 59.02 |
| GBPUSD | 79906 | 35468079.7 | 73.0 |
| NZDCAD | 63671 | 269972.03 | 76.53 |
| GOLD | 60024 | 310150.57 | 68.96 |
| US30 | 46985 | 18104914.52 | 53.6 |
| AUDNZD | 42452 | 140331.03 | 72.52 |
| US500 | 40277 | -3400810.67 | 54.4 |
| AUDUSD | 38828 | -10606030.57 | 72.82 |
| USDCAD | 27798 | 10826754.58 | 71.14 |
| USTE | 27018 | 6006703.15 | 53.56 |
| EURJPY | 26790 | 4681980.13 | 71.53 |

### PnL no-grid por símbolo (más limpio para nosotros)

| Symbol | Trades | PnL |
|---|---:|---:|
| XAUUSD | 23347 | 1107224.03 |
| FDAXU | 43 | 26018.84 |
| NDX | 193 | 12762.21 |
| GOLD | 2710 | 4637.46 |
| WINQ26 | 343 | 4482.0 |
| BTCUSD | 1302 | 3286.19 |
| WINM26 | 36 | 1560.0 |
| EURUSD | 990 | 1522.49 |
| GOLDL | 330 | 894.33 |
| US30 | 170 | 869.77 |
| US100 | 58 | 744.41 |
| USTE | 377 | 375.45 |

## Horarios (hora del broker del signal = open time del CSV)

### Mejores horas no-grid (ppt = pnl/trade, ≥200 trades)

| Hour | nongrid trades | nongrid pnl | ppt |
|---:|---:|---:|---:|
| 15 | 2259 | 320942.1 | 142.0726 |
| 22 | 673 | 89242.36 | 132.6038 |
| 21 | 822 | 90401.54 | 109.9775 |
| 19 | 899 | 78550.18 | 87.3751 |
| 09 | 1238 | 104534.29 | 84.438 |
| 07 | 962 | 78114.19 | 81.1998 |
| 08 | 1397 | 113248.42 | 81.0654 |
| 14 | 1567 | 85179.83 | 54.3585 |
| 06 | 911 | 40446.42 | 44.3978 |
| 11 | 1451 | 43498.1 | 29.978 |
| 10 | 1287 | 37133.92 | 28.8531 |
| 05 | 1239 | 34250.36 | 27.6436 |

### Perfil 24h (todos)

| Hour | trades | pnl | ppt | nongrid_pnl |
|---:|---:|---:|---:|---:|
| 00 | 40850 | 16172005.82 | 395.8875 | 2031.34 |
| 01 | 90399 | 2110416.29 | 23.3456 | 26028.37 |
| 02 | 66935 | 3786141.83 | 56.5645 | 6705.77 |
| 03 | 100521 | 23383412.78 | 232.6222 | 9154.27 |
| 04 | 101701 | 10625315.95 | 104.476 | 12301.17 |
| 05 | 73878 | 18658087.89 | 252.5527 | 34250.36 |
| 06 | 64553 | 69791439.67 | 1081.1494 | 40446.42 |
| 07 | 69089 | 7626251.36 | 110.383 | 78114.19 |
| 08 | 88151 | 38889859.05 | 441.1732 | 113248.42 |
| 09 | 134776 | 19221072.85 | 142.615 | 104534.29 |
| 10 | 144275 | 9554583.14 | 66.2248 | 37133.92 |
| 11 | 135471 | -17021895.21 | -125.6497 | 43498.1 |
| 12 | 119594 | 5457595.82 | 45.6344 | 29166.09 |
| 13 | 115581 | -4367988.18 | -37.7916 | 4108.61 |
| 14 | 112125 | 14579491.15 | 130.0289 | 85179.83 |
| 15 | 159088 | 7831658.15 | 49.2285 | 320942.1 |
| 16 | 222998 | -6537200.23 | -29.3151 | -50583.16 |
| 17 | 185290 | 956930.05 | 5.1645 | -22593.23 |
| 18 | 130048 | 10004711.79 | 76.9309 | 6270.01 |
| 19 | 88266 | 40456851.46 | 458.3515 | 78550.18 |
| 20 | 81595 | 7398635.72 | 90.6751 | 9358.84 |
| 21 | 69366 | 32609690.92 | 470.1106 | 90401.54 |
| 22 | 76363 | 1463455.43 | 19.1645 | 89242.36 |
| 23 | 92556 | -123636.38 | -1.3358 | 9078.28 |

## Días de semana

| Dow | trades | pnl | ppt |
|---|---:|---:|---:|
| Mon | 496589 | 85956720.06 | 173.0943 |
| Tue | 517018 | 24628597.68 | 47.6359 |
| Wed | 542974 | 113339217.83 | 208.7378 |
| Thu | 518076 | -833481.18 | -1.6088 |
| Fri | 467867 | 88002438.65 | 188.0929 |
| Sat | 8853 | 3327211.16 | 375.8287 |
| Sun | 12092 | -1893817.1 | -156.6174 |

## Últimos meses (actividad / pnl agregado)

| Month | trades | pnl |
|---|---:|---:|
| 2025-03 | 31200 | 2042421.24 |
| 2025-04 | 40454 | 425249.76 |
| 2025-05 | 39607 | -5721584.95 |
| 2025-06 | 46225 | 2503960.97 |
| 2025-07 | 51890 | 4383219.54 |
| 2025-08 | 49760 | 1294342.58 |
| 2025-09 | 68674 | 5141988.54 |
| 2025-10 | 87722 | -7061211.3 |
| 2025-11 | 78714 | 2952780.92 |
| 2025-12 | 90529 | 8023997.64 |
| 2026-01 | 118944 | 13795787.77 |
| 2026-02 | 126100 | 15297814.19 |
| 2026-03 | 195954 | 5377663.22 |
| 2026-04 | 188360 | 30974570.71 |
| 2026-05 | 228166 | 37401083.06 |
| 2026-06 | 277382 | 87778678.68 |
| 2026-07 | 322415 | 105393936.28 |
| 2026-08 | 75560 | -66600761.57 |

## A qué se debe (interpretación risk-quant)

1. **XAU domina** el universo rentable público: liquidez + volatilidad intradía + marketing de EAs gold.
2. **Grids fabrican WR y curvas suaves** hasta el evento de cola; por eso el ranking MQL5 engaña.
3. **Overlap Londres–NY (~14–18 en muchos brokers)** concentra actividad y ppt no-grid; Asia (~1–5) aparece en scalps gold de micro-hold.
4. **Muchos 'scalps aplicables' tienen RR<0.6**: ganan seguido, pierden fuerte — frágil bajo costos reales IC Markets.
5. El subconjunto útil es **single-entry XAU/EUR/JPY con RR≥1.3**, no clonar el top growth.

## Acciones concretas para `bot_mt5`

1. Mantener **XAU** como núcleo; BTC/EUR solo como tracks satélite con ventanas propias.
2. Priorizar ventanas **14–18** (y validar offset broker vs ART) para gold scalp/intraday.
3. Exigir **RR≥1.5 ATR** en entradas; descartar perfiles tipo WR>80 & RR<0.5 aunque el signal 'aplique'.
4. Deep-dive CSV de aplicables con RR≥1.3 (lista abajo) → hours + minute alignment → mirror en config.
5. No implementar grid/martingale aunque el PnL histórico sea enorme.

## Top aplicables (para deep-dive)

| ID | Title | n | net | PF | top sym |
|---:|---|---:|---:|---:|---|
| 2375687 | Safe Scalp Gold TM | 84 | 215.48 | 6.248 | XAUUSD |
| 2373424 | EUROPEAN UNION | 178 | 154.55 | 3.665 | EURUSD |
| 2361018 | ZPowerLX | 218 | 353.44 | 3.21 | XAUUSD |
| 2362714 | Gold Snap | 92 | 740.56 | 2.771 | XAUUSD |
| 2366507 | AZ AurumBreakoutEA Signal 1 | 104 | 78.22 | 2.14 | XAUUSD |
| 2378365 | BIZKO GOLD DIGGER EA | 203 | 115.5 | 2.091 | XAUUSD |
| 2365400 | SMART GOLD HUNTER | 156 | 140.44 | 2.026 | XAUUSD |
| 2369567 | Awake Snake | 185 | 1587.38 | 2.018 | EURUSD |
| 2310627 | Prado Pro USDJPY | 83 | 269.9 | 1.931 | USDJPY |
| 2377205 | The Buster XAU Single Entry | 150 | 84004.26 | 1.917 | XAUUSD |
| 2382544 | Buster Diamond Med Risk | 122 | 65.1 | 1.842 | XAUUSD |
| 2328761 | Akali | 197 | 140.63 | 1.728 | XAUUSD |
| 2380965 | Tradefx | 158 | 865.92 | 1.726 | XAUUSD |
| 2378535 | ZPowerXM | 168 | 102.9 | 1.717 | GOLD |
| 2360740 | Core Pulse X Live Signal | 101 | 68.11 | 1.713 | XAUUSD |
| 2373900 | Dynamic Liquidity Intelligence HFM50 | 106 | 314.91 | 1.686 | XAUUSD |
| 2366376 | TradeSentinel Apex HighRisk | 170 | 4774.25 | 1.619 | XAUUSD |
| 2376022 | ThreeEMA Knight | 80 | 359.48 | 1.583 | XAUUSD |
| 2365584 | Breakout XAUUSD | 204 | 841.03 | 1.533 | GOLD |
| 2364127 | EA Legendary Scalper SL3 XAUUSD | 194 | 163.29 | 1.507 | XAUUSD |

## Top no-grid por expectancy (net/trade)

| ID | Style | n | net | net/trade | top sym |
|---:|---|---:|---:|---:|---|
| 2377205 | scalp_single | 150 | 84004.26 | 560.0284 | XAUUSD |
| 2385460 | intraday_single | 154 | 18073.35 | 117.3594 | XAUUSD |
| 2367938 | scalp_single | 221 | 14705.76 | 66.5419 | XAUUSD |
| 2325600 | intraday_single | 193 | 12762.21 | 66.1254 | NDX |
| 2365514 | intraday_single | 173 | 9603.43 | 55.5112 | XAUUSD |
| 2374368 | intraday_single | 96 | 5116.8 | 53.3 | XAUUSD |
| 2365513 | intraday_single | 174 | 6923.01 | 39.7874 | XAUUSD |
| 2365511 | intraday_single | 174 | 5448.04 | 31.3106 | XAUUSD |
| 2366376 | scalp_single | 170 | 4774.25 | 28.0838 | XAUUSD |
| 2379946 | intraday_single | 126 | 2846.86 | 22.5941 | XAUUSD |
| 2351091 | mixed | 102 | 1741.91 | 17.0775 | XAUUSD |
| 2377566 | intraday_single | 81 | 1200.7 | 14.8235 | XAUUSD |
| 2383910 | scalp_single | 100 | 1205.24 | 12.0524 | XAUUSD |
| 2369567 | intraday_single | 185 | 1587.38 | 8.5804 | EURUSD |
| 2362714 | scalp_single | 92 | 740.56 | 8.0496 | XAUUSD |
