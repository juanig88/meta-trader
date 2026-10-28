---
name: replace-with-folder-name
description: >-
  WHAT the skill does in one line. Use when WHEN: include technical analysis,
  backtesting, or risk management keywords the user would type (English and Spanish if relevant).
---

# <Human title>

One sentence: the unit of work this skill owns. Link related in-repo modules by path.

## Gather (Recolectar datos de velas/logs)

- Inputs required (files, symbols, timeframes, logs).
- How to obtain them (workspace paths, MCP market-data tools). Never invent series.
- Look-ahead rule: only closed bars / already-logged events.

## Act (Modificar código del bot)

- Target layer and files (prefer `trading/domain/` for formulas).
- What to change vs what to leave alone.
- Forbidden: inventing `BOT_*` risk numbers; sending orders; mixing broker I/O into domain.

## Verify (Probar matemáticamente con Pytest)

- Test file path under `tests/`.
- Table-driven cases: happy path + edges (empty, zero, invalid probability, missing EMA, …).
- Command: `python -m pytest tests/<file>.py -q`
- Pass criteria: assertions on numeric/boolean domain results, not just "function was called".

## Gotchas

- Project-specific traps only (column names, point vs price, magic numbers already in code).
- Live path is the rotation ensemble (5 modules / 4/5), not H4 `strategy_core` and not 13 voters.
