# Parallel demo — XAU + BTC + EUR + JPY (post MQL5 mining)

Premisa: **no serializar**. Cuatro tracks en la misma cuenta demo IC Markets.
XNG salió del book activo (lot mínimo alto + ADX bloqueando + menos edge en el mining).

Fuente de ventanas/RR: `mql5_crawl/exhaustive_insights.md` (no-grid, RR≥1.5, XAU dominante).

## Tracks

| Track | Símbolo | Path | Ventana LOCAL (ART) | Entrada / riesgo |
|---|---|---|---|---|
| `xau_parallel_scalp` | XAUUSD | parallel magic 260415 | `07–10, 14–16, 19–23` | EMA9/21 + ADX≥22 + H1 · ATR×1.5 / **RR 2.0** · lot 0.01 |
| `btc_breakout_h4` | BTCUSD | main | `06–09, 14–18, 21–23` | breakout + SELL solo H4 DOWN · ATR×1.5 / TP×2.25 |
| `eur_session_rr15` | EURUSD | main | `08–12, 14–18` | breakout M15 · ATR×1.5 / TP×2.25 |
| `jpy_session_rr15` | USDJPY | main | `07–12, 20–02` | breakout · ATR×1.5 / TP×2.5 · exempt quiet 00–05 |

## Riesgo demo

- Máx 1 posición / símbolo, máx **4** abiertas totales  
- Daily kill: **−$25**  
- Weekend block ON  
- Quiet 00–05 ART con **exención** `USDJPY,BTCUSD`  
- **No** grids / martingale

## Arranque

```powershell
cd c:\Users\juani\Projects\meta-trader
# reiniciar bot tras cambiar .env
py .\bot_mt5.py
py .\flask_dashboard.py
```

## Logs

| Evento | Uso |
|---|---|
| `EVAL_TRACKS_BOOT` | 4 tracks al start |
| `EVAL_CYCLE` / `EVAL_SKIP` | por símbolo |
| `XAUUSD_PARALLEL_*` | scalper oro (ahora multi-sesión) |

```powershell
Select-String -Path trades.log -Pattern 'EVAL_|XAUUSD_PARALLEL|TRADE_'
```

## Por qué antes no abría

1. XAU ventana `10–14` **fin exclusivo** → a las 14:xx ya estaba off (NY open cortado).  
2. Solo BTC+XNG en loop; XNG con ADX&lt;min → NONE.  
3. BTC sin cross M15 → `signal_m15=NONE` (normal; no es bug de ventana).

## Kill-switch

- DD diario ≤ −25 → no nuevas entradas  
- Track con PF rolling malo 3d → sacar de `BOT_SYMBOLS` o `BOT_XAUUSD_PARALLEL_ENABLED=0`
