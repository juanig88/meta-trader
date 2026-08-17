# MQL5 Positions — Pattern Mining (exhaustivo)

- CSVs analizados (filtrados n≥60): **2035** / raw 2375
- Estilos: {'grid_or_martingale': 1878, 'scalp_single': 64, 'mixed': 42, 'intraday_single': 40, 'swing_single': 11}
- Share grid/martingale: **92.3%** (1878)
- Candidatos aplicables (1–2 pos, PF≥1.25, sin grid/martingale): **56**

## Qué NO copiar (riesgo)

- La mayoría del ranking público es **grid / basket / martingale** (WR alto, muchos cierres simultáneos, lotes escalados).
- PF público inflado + DD equity bajo en la ficha a menudo **no** sobrevive un stop de cuenta real.
- Ultra-WR (>92%) con holds cortos suele ser hedged grid o scalp de micro-TP.

## Patrones transferibles (si aparecen en no-grid)

- Horas más frecuentes (entre aplicables): [(16, 772), (4, 461), (15, 458), (17, 424), (3, 377), (2, 340), (1, 304), (14, 270)]
- Símbolos dominantes (aplicables): [('XAUUSD', 41), ('GOLD', 6), ('EURUSD', 4), ('USDJPY', 1), ('BTCUSD', 1), ('GOLDL', 1), ('USTE', 1), ('US100', 1)]
- Align M15 medio: 0.21
- RR empírico medio (|AW|/|AL|): 0.89
- XAU aplicables: 41 | BTC aplicables: 1

## Shortlist aplicable (priorizar deep-dive / forward demo)

| ID | Título | Style | n | WR | PF | RR | med hold | max pos | Top sym | Align15 | Ventana 4h |
|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| [2375687](https://www.mql5.com/en/signals/2375687) | Safe Scalp Gold TM | scalp_single | 84 | 84.52 | 6.248 | 1.144 | 0.05 | 1 | XAUUSD | 0.155 | 14-18 (0.512) |
| [2373424](https://www.mql5.com/en/signals/2373424) | EUROPEAN UNION | scalp_single | 178 | 91.57 | 3.665 | 0.337 | 8.1 | 2 | EURUSD | 0.753 | 17-21 (0.382) |
| [2361018](https://www.mql5.com/en/signals/2361018) | ZPowerLX | scalp_single | 218 | 82.57 | 3.21 | 0.678 | 0.41 | 2 | XAUUSD | 0.147 | 14-18 (0.362) |
| [2362714](https://www.mql5.com/en/signals/2362714) | Gold Snap | scalp_single | 92 | 80.43 | 2.771 | 0.674 | 0.78 | 1 | XAUUSD | 0.087 | 13-17 (0.37) |
| [2366507](https://www.mql5.com/en/signals/2366507) | AZ AurumBreakoutEA Signal 1 | scalp_single | 104 | 75.96 | 2.14 | 0.677 | 0.13 | 1 | XAUUSD | 0.106 | 11-15 (0.49) |
| [2378365](https://www.mql5.com/en/signals/2378365) | BIZKO GOLD DIGGER EA | scalp_single | 203 | 46.8 | 2.091 | 2.356 | 0.05 | 2 | XAUUSD | 0.143 | 13-17 (0.34) |
| [2365400](https://www.mql5.com/en/signals/2365400) | SMART GOLD HUNTER | scalp_single | 156 | 76.92 | 2.026 | 0.608 | 0.26 | 1 | XAUUSD | 0.167 | 14-18 (0.333) |
| [2369567](https://www.mql5.com/en/signals/2369567) | Awake Snake | intraday_single | 185 | 53.51 | 2.018 | 1.671 | 21.9 | 2 | EURUSD | 0.124 | 12-16 (0.324) |
| [2310627](https://www.mql5.com/en/signals/2310627) | Prado Pro USDJPY | swing_single | 83 | 40.96 | 1.931 | 2.783 | 253.15 | 1 | USDJPY | 0.096 | 8-12 (0.337) |
| [2377205](https://www.mql5.com/en/signals/2377205) | The Buster XAU Single Entry | scalp_single | 150 | 88.0 | 1.917 | 0.261 | 0.07 | 2 | XAUUSD | 0.053 | 15-19 (0.287) |
| [2382544](https://www.mql5.com/en/signals/2382544) | Buster Diamond Med Risk | scalp_single | 122 | 84.43 | 1.842 | 0.34 | 0.33 | 2 | XAUUSD | 0.098 | 15-19 (0.361) |
| [2328761](https://www.mql5.com/en/signals/2328761) | Akali | scalp_single | 197 | 89.85 | 1.728 | 0.195 | 0.2 | 1 | XAUUSD | 0.086 | 1-5 (0.416) |
| [2380965](https://www.mql5.com/en/signals/2380965) | Tradefx | intraday_single | 158 | 60.76 | 1.726 | 1.115 | 127.58 | 2 | XAUUSD | 0.38 | 15-19 (0.437) |
| [2378535](https://www.mql5.com/en/signals/2378535) | ZPowerXM | scalp_single | 168 | 76.79 | 1.717 | 0.519 | 0.4 | 2 | GOLD | 0.101 | 14-18 (0.369) |
| [2360740](https://www.mql5.com/en/signals/2360740) | Core Pulse X Live Signal | scalp_single | 101 | 77.23 | 1.713 | 0.483 | 0.07 | 1 | XAUUSD | 0.109 | 1-5 (0.475) |
| [2373900](https://www.mql5.com/en/signals/2373900) | Dynamic Liquidity Intelligence HFM50 | intraday_single | 106 | 86.79 | 1.686 | 0.257 | 36.72 | 1 | XAUUSD | 0.16 | 14-18 (0.425) |
| [2366376](https://www.mql5.com/en/signals/2366376) | TradeSentinel Apex HighRisk | scalp_single | 170 | 50.0 | 1.619 | 1.619 | 1.05 | 1 | XAUUSD | 0.082 | 1-5 (0.424) |
| [2376022](https://www.mql5.com/en/signals/2376022) | ThreeEMA Knight | swing_single | 80 | 62.5 | 1.583 | 0.95 | 258.37 | 1 | XAUUSD | 1.0 | 5-9 (0.25) |
| [2365584](https://www.mql5.com/en/signals/2365584) | Breakout XAUUSD | intraday_single | 204 | 17.65 | 1.533 | 6.772 | 114.88 | 1 | GOLD | 0.191 | 14-18 (0.363) |
| [2364127](https://www.mql5.com/en/signals/2364127) | EA Legendary Scalper SL3 XAUUSD | scalp_single | 194 | 57.73 | 1.507 | 1.103 | 0.2 | 2 | XAUUSD | 0.124 | 15-19 (0.381) |
| [2370969](https://www.mql5.com/en/signals/2370969) | Dynamic Liquidity Intelligence XM100 | intraday_single | 134 | 85.82 | 1.505 | 0.249 | 43.1 | 1 | GOLD | 0.164 | 14-18 (0.381) |
| [2357092](https://www.mql5.com/en/signals/2357092) | Gold V2 EBC 260 Fixed lot MT5 M30 | scalp_single | 226 | 67.26 | 1.502 | 0.731 | 2.63 | 2 | XAUUSD | 0.106 | 15-19 (0.35) |
| [2365511](https://www.mql5.com/en/signals/2365511) | Dynamic Liquidity Intelligence R1000 | intraday_single | 174 | 85.63 | 1.486 | 0.249 | 37.12 | 1 | XAUUSD | 0.144 | 14-18 (0.397) |
| [2294444](https://www.mql5.com/en/signals/2294444) | Bitcoin Scalper BTC | scalp_single | 218 | 61.47 | 1.484 | 0.919 | 8.16 | 2 | BTCUSD | 0.096 | 0-4 (0.422) |
| [2385460](https://www.mql5.com/en/signals/2385460) | Definitely No Hype EA MT5 | intraday_single | 154 | 71.43 | 1.476 | 0.59 | 27.87 | 1 | XAUUSD | 0.058 | 14-18 (0.325) |
| [2354745](https://www.mql5.com/en/signals/2354745) | GoldVein EA | intraday_single | 122 | 56.56 | 1.475 | 1.133 | 78.77 | 1 | GOLDL | 0.459 | 1-5 (0.279) |
| [2269966](https://www.mql5.com/en/signals/2269966) | BreakZone Scalper HFM | scalp_single | 396 | 75.0 | 1.464 | 0.488 | 2.08 | 1 | XAUUSD | 0.091 | 2-6 (0.444) |
| [2383910](https://www.mql5.com/en/signals/2383910) | GOLDWF | scalp_single | 100 | 36.0 | 1.463 | 2.6 | 12.47 | 1 | XAUUSD | 0.27 | 12-16 (1.0) |
| [2271031](https://www.mql5.com/en/signals/2271031) | Break Zone Tickmill | scalp_single | 402 | 70.65 | 1.446 | 0.59 | 2.2 | 1 | XAUUSD | 0.102 | 2-6 (0.453) |
| [2377971](https://www.mql5.com/en/signals/2377971) | SMC FVG Retest | scalp_single | 81 | 79.01 | 1.44 | 0.382 | 10.52 | 1 | XAUUSD | 0.358 | 3-7 (0.272) |
| [2365513](https://www.mql5.com/en/signals/2365513) | Dynamic Liquidity Intelligence R500 | intraday_single | 174 | 85.63 | 1.437 | 0.241 | 37.12 | 1 | XAUUSD | 0.144 | 14-18 (0.397) |
| [2361988](https://www.mql5.com/en/signals/2361988) | Zone Breaker Eightcap | scalp_single | 125 | 62.4 | 1.431 | 0.863 | 0.43 | 1 | XAUUSD | 0.08 | 2-6 (0.44) |
| [2372484](https://www.mql5.com/en/signals/2372484) | NS X | intraday_single | 171 | 85.96 | 1.42 | 0.232 | 21.53 | 2 | XAUUSD | 0.123 | 3-7 (0.316) |
| [2384567](https://www.mql5.com/en/signals/2384567) | XAUUSDabc EA | intraday_single | 143 | 58.04 | 1.409 | 1.018 | 197.25 | 1 | GOLD | 0.112 | 14-18 (0.336) |
| [2368827](https://www.mql5.com/en/signals/2368827) | Zone Breakout XM | scalp_single | 110 | 64.55 | 1.403 | 0.771 | 0.5 | 2 | GOLD | 0.127 | 2-6 (0.364) |
| [2365514](https://www.mql5.com/en/signals/2365514) | Dynamic Liquidity Intelligence R300 | intraday_single | 173 | 85.55 | 1.392 | 0.235 | 37.23 | 1 | XAUUSD | 0.139 | 14-18 (0.399) |
| [2373901](https://www.mql5.com/en/signals/2373901) | Dynamic Liquidity Intelligence PUP50 | intraday_single | 102 | 85.29 | 1.378 | 0.238 | 45.98 | 1 | XAUUSD | 0.167 | 14-18 (0.412) |
| [2364384](https://www.mql5.com/en/signals/2364384) | Gold Dragonball | intraday_single | 120 | 80.0 | 1.375 | 0.344 | 47.63 | 1 | XAUUSD | 0.233 | 15-19 (0.275) |
| [2371866](https://www.mql5.com/en/signals/2371866) | EA Legendary Scalper SL5 XAUUSD | scalp_single | 117 | 54.7 | 1.36 | 1.126 | 0.48 | 2 | XAUUSD | 0.103 | 15-19 (0.342) |
| [2360163](https://www.mql5.com/en/signals/2360163) | DhaBass CT | intraday_single | 163 | 42.94 | 1.35 | 1.775 | 35.18 | 2 | EURUSD | 0.092 | 10-14 (0.423) |

## Soft shortlist (estudio, n=28)

| ID | Style | n | WR | PF | RR | med hold | max pos | Top sym | Ventana 4h |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|
| [2369448](https://www.mql5.com/en/signals/2369448) | mixed | 133 | 89.47 | 4.748 | 0.559 | 0.18 | 3 | XAUUSD | 3-7 (0.571) |
| [2382855](https://www.mql5.com/en/signals/2382855) | mixed | 83 | 84.34 | 4.241 | 0.788 | 0.68 | 3 | BTCUSD | 12-16 (0.361) |
| [2373930](https://www.mql5.com/en/signals/2373930) | mixed | 97 | 85.57 | 2.647 | 0.447 | 9.08 | 3 | XAUUSD | 11-15 (0.505) |
| [2351091](https://www.mql5.com/en/signals/2351091) | mixed | 102 | 70.59 | 2.333 | 0.972 | 488.18 | 3 | XAUUSD | 17-21 (0.304) |
| [2370863](https://www.mql5.com/en/signals/2370863) | mixed | 147 | 89.8 | 2.264 | 0.257 | 1.87 | 3 | GOLD | 2-6 (0.34) |
| [2382089](https://www.mql5.com/en/signals/2382089) | mixed | 89 | 38.2 | 1.97 | 3.187 | 140.33 | 3 | GBPUSD | 9-13 (0.517) |
| [2362805](https://www.mql5.com/en/signals/2362805) | mixed | 291 | 51.2 | 1.635 | 1.547 | 218.92 | 3 | AUDUSD | 16-20 (0.265) |
| [2378405](https://www.mql5.com/en/signals/2378405) | mixed | 469 | 85.29 | 1.626 | 0.281 | 2.03 | 3 | BTCUSD | 15-19 (0.356) |
| [2376723](https://www.mql5.com/en/signals/2376723) | mixed | 148 | 51.35 | 1.567 | 1.443 | 143.44 | 3 | XAUUSD | 12-16 (0.318) |
| [2361936](https://www.mql5.com/en/signals/2361936) | mixed | 464 | 85.34 | 1.555 | 0.267 | 31.14 | 3 | XAUUSD | 14-18 (0.304) |
| [2371457](https://www.mql5.com/en/signals/2371457) | mixed | 169 | 68.05 | 1.519 | 0.713 | 30.75 | 3 | XAUUSD | 14-18 (0.296) |
| [2345228](https://www.mql5.com/en/signals/2345228) | mixed | 403 | 66.5 | 1.463 | 0.737 | 8.13 | 3 | XAUUSD | 8-12 (0.32) |
| [2366593](https://www.mql5.com/en/signals/2366593) | mixed | 283 | 61.13 | 1.404 | 0.892 | 176.53 | 3 | XAUUSD | 6-10 (0.378) |
| [2384737](https://www.mql5.com/en/signals/2384737) | mixed | 85 | 51.76 | 1.402 | 1.306 | 42.78 | 3 | XAUUSD | 15-19 (0.329) |
| [2340140](https://www.mql5.com/en/signals/2340140) | mixed | 405 | 65.93 | 1.389 | 0.718 | 4.18 | 3 | XAUUSD | 13-17 (0.338) |
| [2384003](https://www.mql5.com/en/signals/2384003) | mixed | 438 | 85.39 | 1.355 | 0.232 | 1.75 | 3 | XAUUSD | 15-19 (0.331) |
| [2372782](https://www.mql5.com/en/signals/2372782) | mixed | 141 | 68.79 | 1.33 | 0.603 | 73.03 | 3 | XAUUSD | 22-2 (1.0) |
| [2362405](https://www.mql5.com/en/signals/2362405) | mixed | 181 | 70.17 | 1.297 | 0.541 | 71.33 | 3 | USDCAD | 23-3 (0.608) |
| [2323009](https://www.mql5.com/en/signals/2323009) | mixed | 158 | 73.42 | 1.283 | 0.464 | 48.1 | 3 | XAUUSD | 13-17 (0.222) |
| [2337459](https://www.mql5.com/en/signals/2337459) | mixed | 302 | 71.19 | 1.264 | 0.512 | 61.38 | 3 | XAUUSD | 15-19 (0.311) |
| [2337490](https://www.mql5.com/en/signals/2337490) | mixed | 301 | 71.1 | 1.26 | 0.512 | 61.4 | 3 | XAUUSD | 15-19 (0.312) |
| [2356196](https://www.mql5.com/en/signals/2356196) | mixed | 238 | 51.26 | 1.259 | 1.197 | 510.88 | 3 | XAUUSD | 1-5 (0.55) |
| [2368307](https://www.mql5.com/en/signals/2368307) | scalp_single | 160 | 51.88 | 1.249 | 1.159 | 9.17 | 2 | XAUUSD | 15-19 (0.375) |
| [2302517](https://www.mql5.com/en/signals/2302517) | intraday_single | 106 | 26.42 | 1.246 | 3.47 | 26.64 | 2 | XAUUSD | 15-19 (0.349) |
| [2295533](https://www.mql5.com/en/signals/2295533) | intraday_single | 285 | 92.28 | 1.243 | 0.104 | 153.13 | 2 | GOLD | 20-0 (1.0) |

## Implicaciones para `bot_mt5` (acciones concretas)

1. **Filtro anti-grid en research**: ignorar signals con max_concurrent≥3 o lot_jumps altos — no intentar clonar su curva.
2. **Ventanas horarias**: si los aplicables concentran opens, alinear `TRADING_WINDOWS_*` a esas horas (broker time), no a growth marketing.
3. **Alineación a vela**: si `align15` alto en no-grid → confirmar entradas en close M15 (ya cercano a nuestro diseño).
4. **RR objetivo**: usar mediana de `rr_avg` de aplicables (≥~1.5–2) con ATR SL; evitar perfiles WR>80 con RR<0.5.
5. **Un símbolo dominante**: preferir specialists (XAU/BTC/EUR) sobre portfolios multi-asset tipo 2278370.
6. **Forward test**: tomar top 5–10 aplicables y paper/demo en paralelo con kill-switch; no optimizar IS sobre sus CSV.

## Top grids (solo para estudio / no clonar)

| ID | n | WR | PF | max_pos | lot_jumps | Top sym |
|---:|---:|---:|---:|---:|---:|---|
| [2205157](https://www.mql5.com/en/signals/2205157) | 68445 | 37.15 | 1.038 | 16 | 5097 | WINQ24 |
| [810657](https://www.mql5.com/en/signals/810657) | 47040 | 39.38 | 1.118 | 14 | 4691 | WINJ21 |
| [2377527](https://www.mql5.com/en/signals/2377527) | 26589 | 70.92 | 1.374 | 33 | 1366 | XAUUSD |
| [2367442](https://www.mql5.com/en/signals/2367442) | 23022 | 59.6 | 1.209 | 43 | 783 | XAUUSD |
| [2373545](https://www.mql5.com/en/signals/2373545) | 22672 | 60.35 | 2.035 | 296 | 3530 | GBPJPY |
| [2343251](https://www.mql5.com/en/signals/2343251) | 22006 | 52.42 | 1.074 | 33 | 802 | XAUUSD |
| [1696414](https://www.mql5.com/en/signals/1696414) | 21652 | 66.99 | 1.402 | 201 | 1679 | AUDCAD |
| [2385382](https://www.mql5.com/en/signals/2385382) | 21522 | 56.05 | 1.659 | 31 | 5915 | BTCUSD |
| [2371373](https://www.mql5.com/en/signals/2371373) | 21312 | 64.42 | 1.178 | 77 | 3722 | EURUSD |
| [2373236](https://www.mql5.com/en/signals/2373236) | 20926 | 51.99 | 1.231 | 6 | 7 | XAUUSD |
| [2372154](https://www.mql5.com/en/signals/2372154) | 20008 | 73.68 | 1.549 | 28 | 1852 | GOLD |
| [2385414](https://www.mql5.com/en/signals/2385414) | 19338 | 57.09 | 1.621 | 30 | 5475 | BTCUSD |
| [2360697](https://www.mql5.com/en/signals/2360697) | 18412 | 62.47 | 1.356 | 1041 | 3368 | XAUUSD |
| [2357942](https://www.mql5.com/en/signals/2357942) | 17208 | 59.21 | 1.549 | 149 | 900 | XAUUSD |
| [2344031](https://www.mql5.com/en/signals/2344031) | 17070 | 62.31 | 1.114 | 90 | 3436 | NDX |
