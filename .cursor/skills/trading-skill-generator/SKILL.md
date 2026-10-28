---
name: trading-skill-generator
description: >-
  Generates modular Agent Skills for algorithmic trading and quantitative research
  under the agentskills.io format, using Gather (Recolectar datos de velas/logs),
  Act (Modificar código del bot), and Verify (Probar matemáticamente con Pytest).
  Use when creating tools for technical analysis, backtesting, or risk management;
  when the user asks for análisis técnico, backtesting, or gestión de riesgo;
  or when authoring a new trading/quant skill.
license: ISC
compatibility: Cursor; Python 3 and Pytest; project skills live in .cursor/skills/
metadata:
  author: meta-trader
  version: "1.0"
  standard: agentskills.io
  methodology: gather-act-verify
---

# Trading Skill Generator

Teach any Cursor agent how to **author** a new modular skill for Trading Algorítmico and Quant. This skill does not implement indicators, backtests, or risk rules itself — it produces another skill that does.

**Live bot:** rotation ensemble (5 modules, 4/5 + HTF/pullback/RSI gates). Generated skills must not restore H4/XAU/USD-ladder as the live path. Canonical runbook: [`trading/docs/ROTATION_ENGINE.md`](../../../trading/docs/ROTATION_ENGINE.md). Risk numbers stay with risk-quant (`BOT_MAX_RISK_USD=0.50`, `MIN_AGREE=4`).

Read this file fully before writing any `SKILL.md`. When you start drafting the new skill file, copy the skeleton in [references/skill-template.md](references/skill-template.md).

## Output location

Project skill (shared with the repo):

```
.cursor/skills/<skill-name>/
├── SKILL.md                 # required
├── references/              # optional, load on demand
└── scripts/                 # optional, executable helpers
```

- `name` in frontmatter **must match** the directory name.
- Follow [agentskills.io](https://agentskills.io/specification): lowercase `a-z`, `0-9`, hyphens; max 64 chars; no leading/trailing/consecutive hyphens; `description` max 1024 chars.
- Omit `disable-model-invocation` so the new skill can auto-activate from its description.
- Never write skills into `~/.cursor/skills-cursor/` (Cursor internals).

## Scope one skill per unit of work

| Domain | Typical skill name | Lives in code |
|--------|--------------------|---------------|
| Technical analysis | `ta-<topic>` | `trading/domain/`, `indicators.py`, strategy filters |
| Backtesting | `backtest-<topic>` | `trading/backtest/`, `backtest.py`, cost models |
| Risk management | `risk-<topic>` | `trading/risk/`, `trading/domain/`, pre-trade checks |

Do not bundle TA + backtest + live execution into one skill. Compose small skills instead.

## Methodology (required in every generated skill)

Every trading/quant skill body must encode this cycle, with these labels:

1. **Gather** (Recolectar datos de velas/logs)
2. **Act** (Modificar código del bot)
3. **Verify** (Probar matemáticamente con Pytest)

Do not skip a phase. Do not Act before Gather evidence. Do not finish without Verify.

### Gather — Recolectar datos de velas/logs

- Collect real inputs: OHLCV / MT5 rates, `trades.log`, `bot_state.json`, `bot_state` eval events, existing Pytest fixtures.
- Name **source, symbol, timeframe, and date range** in the skill or in the change notes.
- Do **not** invent price history, fills, or PnL. Prefer workspace files; for crypto research use configured MCP (`vibe-trading`, `ccxt`) — market data only, no order routing.
- If data is missing, stop and say what file or MCP call is required. Do not fabricate candles.

### Act — Modificar código del bot

- Change the smallest set of files that implements the rule.
- Place **pure math** (indicators, EV, stops, sizing formulas) in `trading/domain/` with **no** MT5, Flask, HTTP, or file I/O imports.
- Adapters (feeds, brokers, MCP, logs) stay in `trading/infrastructure/` or existing root modules (`bot_mt5.py`, `flask_dashboard.py`).
- Reuse existing functions; do not rewrite working logic.
- **Do not invent risk parameters** (lot, SL/TP, drawdown caps, leverage). Those come from risk-quant / existing `.env` and `BOT_*` config. The skill may *implement* a formula; it must not *choose* live numbers.
- Execution cannot bypass risk checks. Paper/live deployment is not in scope for generated skills.
- Do not document or implement the old 13-voter / 10/13 tally. Live is **5 modules, 4/5**.

### Verify — Probar matemáticamente con Pytest

- Add or update tests in `tests/` in the **same change**.
- Prefer **table-driven** tests: list of `{name, input, expected}` and one loop (see `tests/test_expected_value.py`).
- Assert **math**, not only mocks: closed-form EV, ATR distance, stop price, clamp, gate true/false.
- Cover happy path **and** edges: `0`, empty series, `NaN`/missing bar, invalid `p` outside `[0, 1]`, look-ahead (signal must use closed bars only).
- Run: `python -m pytest tests/<test_file>.py -q`
- If Pytest fails, fix Act or tests; do not declare the skill or feature done.

## How to generate a skill

Copy this checklist and complete it:

```
Skill generation:
- [ ] Gather: user goal + existing code/data/conventions identified
- [ ] Name + description drafted (WHAT + WHEN, third person, trigger keywords)
- [ ] SKILL.md written with Gather / Act / Verify sections
- [ ] Template filled from references/skill-template.md
- [ ] Layer map respected (domain vs infrastructure)
- [ ] Verify: new skill file exists; description would fire on TA / backtest / risk prompts
```

### 1. Gather (this generator)

Read: `AGENTS.md`, `.cursor/skills/clean-architecture/SKILL.md`, `.cursor/skills/unit-tests-with-code/SKILL.md`, `.cursor/skills/before-editing-code/SKILL.md`, and the modules the new skill will touch. Note naming, fixtures, and which logs/candle sources already exist.

### 2. Act (write the new skill)

Create `.cursor/skills/<skill-name>/SKILL.md` using [references/skill-template.md](references/skill-template.md). Frontmatter `description` must include **both** what the skill does **and** when to use it, with keywords the user would type (`RSI`, `ATR`, `walk-forward`, `max drawdown`, `análisis técnico`, `backtesting`, `gestión de riesgo`, …).

Body of the **generated** skill: only what the agent would get wrong without it (repo paths, formulas already in-tree, gotchas). Do not explain what an EMA is.

Keep the generated `SKILL.md` under 500 lines. Move long tables, formula derivations, or sample series to `references/` and tell the agent **when** to open them.

### 3. Verify (this generator)

- Frontmatter parses as YAML; `name` equals folder name.
- Description ≤ 1024 characters and contains activation keywords.
- Body has the three methodology headings with the Spanish parentheticals above.
- File references from `SKILL.md` are **one level deep**.
- No secrets, API keys, or `.env` values in the skill.

## Description recipe (generated skills)

Third person. WHAT + WHEN. Trigger terms. Example:

```yaml
description: >-
  Computes ATR-based stop distance from closed M15 bars and maps it to MT5 points.
  Use when implementing or changing ATR stops, sl_atr_mult, volatility-based SL/TP,
  or gestión de riesgo by ATR; not for discretionary lot sizing.
```

## Gotchas

- Pytest is the verifier for this Python bot (`tests/`). Do not tell agents to use Vitest unless they are editing JS/TS.
- `trades.log` lines may be `timestamp | LEVEL | {json}` — parse the JSON tail, same as the dashboard/MCP snapshot helpers.
- Look-ahead: Gather must use **closed** candles; a skill that allows `rates[-1]` as if finished is invalid.
- CCXT/vibe-trading MCP is research data, not the live MT5 book. Do not teach skills to place exchange orders.
- Risk-quant has highest priority: a generated skill must say when to **block** a change pending OOS/walk-forward, not how to overfit.

## Example

User: "Create a skill for the H4 EMA sell block."

- **Name:** `risk-h4-ema-sell-block`
- **Gather:** `trading/domain/htf_bias.py`, `tests/test_htf_bias.py`, `BOT_H4_EMA_SELL_BLOCK`
- **Act:** skill instructs agents to extend `sell_blocked_by_h4_ema_up` only; no new broker calls
- **Verify:** table-driven cases already in `test_htf_bias.py` plus any new edge (FLAT, unknown, flag off)
