# Forward demo — estado de enable (2026-08-07)

Replay gate (costos/spread). Criterio: PF≥1 **y** DD aceptable **y** edge en subidas/bajadas.

| Symbol | Sessions (ART) | Evidencia | Enable |
|--------|----------------|-----------|--------|
| BTCUSD | 06–09, 14–18, 21–23 lun–vie | 30d PF~1.7; **6m PF~1.07 DD~−$97**; 2025 negativo; BUY carga el PnL | **NO** |
| USDJPY | 20–02, 04–08 | 30d PF~0.60 | **NO** |
| ETH / AUD / NZD | — | sin gate PF≥1 | **NO** |
| US500 / USTEC / … | research | sin replay de estrategia aún | **NO** |
| XAUUSD parallel | 10–13 | baseline $2/$2 malo; A/B ATR×1.5+RR2+ADX25+H1 mejor en ~18m (N chico) | demo opcional |

## Arranque (solo XAU parallel, si lo activás)

1. MT5 demo abierto y logueado.
2. `BOT_SYMBOLS=` vacío; `BOT_XAUUSD_PARALLEL_ENABLED=1` si querés el scalper.
3. `py .\bot_mt5.py`
4. Opcional dashboard: `py .\flask_dashboard.py`.

## Replay on-demand

```bash
python session_window_replay.py --days 30 --out session_replay_results.jsonl
```

## No reactivar hasta

- BTC: edge que no dependa solo de bull / BUY, o modo régimen explícito + DD acotado.
- USDJPY / índices: replay 30d+ PF≥1 con costos reales.
