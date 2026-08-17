# MQL5 Signals — qué se puede y qué no

## Hallazgo clave

| Recurso | Auth | Útil para |
|---------|------|-----------|
| Página pública `/en/signals/{id}` | No | Growth, WR, PF, DD, distribución por símbolo, hold promedio |
| Listados `/en/signals/mt5*` | No | Descubrir IDs |
| **CSV** `/en/signals/{id}/export/positions` | **Sí (login)** | Tick-level positions, grid, minutes, SL fijo |

Ejemplo verificado: `https://www.mql5.com/en/signals/2329290/export/positions` redirige a login.

No hay forma legítima de “dejar la compu scrapeando CSVs” sin tu sesión MQL5.

## Qué corre automático (sin login)

```bash
python docs/mejoras/bots/mql5_signals_public_crawl.py --max-signals 200 --sleep 1.25
```

Salida:

- `mql5_crawl/signals_public.jsonl`
- `mql5_crawl/signals_public_report.md`

## CSVs (cookies o export manual)

**Con cookies** (archivo gitignored `mql5_crawl/cookies.txt` con `auth` + `sid`):

```bash
python docs/mejoras/bots/mql5_download_positions.py --top 35
python docs/mejoras/bots/mql5_analyze_inbox.py
```

**Manual:** MQL5 → Signal → Export positions → `inbox_positions/` → `mql5_analyze_inbox.py`.

## Descarga masiva + minería

```bash
python docs/mejoras/bots/mql5_download_positions.py --all --no-inbox --sleep 2.0
python docs/mejoras/bots/mql5_pattern_mining.py
# opcional: re-mine cada 10 min
python docs/mejoras/bots/mql5_mine_watch.py 600
```

Salidas: `mql5_crawl/positions_csv/`, `pattern_mining_report.md`, `applicable_candidates.json`.

## Risk-quant

Stats públicas + growth alto ≠ edge clonable. Ya vimos grids/martingales y libros con DD equity brutal en CSVs previos. El crawl ranking es **screening**, no aprobación.
