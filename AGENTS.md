# Trading Bot — Agent Context

This repository hosts a **trading bot** (under `trading/`) alongside the existing Gastos expense-tracker app. This file answers **what the project is**; role behavior is defined in `.cursor/rules/*.mdc`.

**Live engine (since 2026-10-07):** daily **swing** Donchian 20/10 + SMA200 on the XBTFX **demo** (`swing_daily.py`, magic 260501). Runbook: [`trading/docs/SWING_ENGINE.md`](trading/docs/SWING_ENGINE.md). The rotation below is **stopped** (`kill_switch.halt` reason `rotacion_reemplazada_por_swing`) because it did not make money; it stays documented as history.

**Former live engine:** crypto **rotation** on XBTFX **demo**, not the old H4/XAU loop. Full evidence, why we open/close, and the `trades.log` event catalog: [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md). Cursor also loads `.cursor/rules/rotation-engine.mdc` on every chat. **Update that markdown when the live rules change.**

## What the rotation did (stopped 2026-10-07)

Automated **demo** trading that:

1. Scans 79 XBTFX crypto CFDs on M1/M5/M15 (also pulls ~48h M15 and ~12d H4 for a Murphy swing snapshot on 4/5 fires). `climax_unbroken` (`would_veto`) skips with `murphy_climax`. IMX/VEC/TRX/ZRX/RSR/AXS 4/5 skip with `rank_tail`.
2. Fires only on **4/5** independent modules, with HTF (M15 EMA50) veto, pullback, and RSI-in-range gates. The sent side is the consensus (no invert).
3. Sizes so a **$0.50** stop is used when that distance is ≤ **5%** of price; otherwise ±5% with lot scaled to Kelly (capped at 0.50 USD)
4. Skips if the bid/ask already covers that stop, or the spread hole is **> 10%** of the USD risk
5. Caps **60 broker ENTRY_IN** per rolling hour and **one** position per symbol
6. After **5 minutes**, close: `profit > 0.01` → `stale_take`, `profit < -0.0001` → `stale_loss`, band in between → `stale_time` (time-stop). Fill MTM 2026-10-03: +5m EV least-bad vs 15/30/60m on pre-invert fills.
7. If the floating book (profit+swap+comm of magic 260414) is **≥ $0.50**, at least two tickets are open, and the youngest is **≥ 10 min** old, flatten **all** rotation positions (`rot_book_harvest`) so slots can re-enter on a fresh 4/5
8. Blocks new entries while `kill_switch.halt` exists (or is unreadable); with `BOT_KILL_SWITCH_FLATTEN=1` (live since 2026-10-05; code default 0) a daily-loss hit flattens the rotation book once and persists the halt until a human clears it
9. Logs every vote, skip, fill, reject, and close to `trades.log` (`why_open=consenso_4_5`, skips `veto_htf` / `sin_pullback` / `rsi_rango` / `murphy_climax` / `rank_tail` / spread) plus a `ROTATION_BOOK` snapshot every 60s (floating net of magic 260414) for later crypto ranking. 4/5 fires also carry `murphy` (M15/H4 swing tag, 38–50% retrace or retest vs climax; 1%/ATR penetration) and `alpha` (shadow score vs the consensus; telemetry only).

The five modules (`trading/domain/ensemble.py`): **htf**, **momentum_short**, **intermarket**, **vol_bands**, **structure**. XBTFX volume does not vote. Runbook: [`trading/docs/ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md).

Paper/demo only. Capital preservation over backtest cosmetics. Risk-quant owns size/stops; do not invent new numbers.

## Architecture overview

Clean architecture with dependencies pointing inward:

```
Infrastructure → Application → Domain
```

| Layer | Path | Responsibility |
|-------|------|----------------|
| **Domain** | `trading/domain/` | Entities, value objects, ports, pure strategy/risk rules (no I/O) |
| **Application** | `trading/application/` | Use cases: run strategy, place order, reconcile positions |
| **Infrastructure** | `trading/infrastructure/` | Exchange adapters, data feeds, persistence, logging sinks |
| **Data** | `trading/data/` | Historical and live market data pipelines |
| **Strategies** | `trading/strategies/` | Strategy implementations (consume domain ports) |
| **Backtest** | `trading/backtest/` | Backtesting engine, walk-forward runners, cost models |
| **Risk** | `trading/risk/` | Position sizing, stops, exposure limits, pre-trade checks |
| **Execution** | `trading/execution/` | Order routing, fill handling, paper/live brokers |
| **Docs** | `trading/docs/` | Live runbook [`ROTATION_ENGINE.md`](trading/docs/ROTATION_ENGINE.md); requirements when added |

### Data flow (simplified)

```
Market data → Strategy signals → Risk gate → Execution → Exchange
                     ↑                ↓
                  Backtest        Logging / metrics
```

## Module map (planned)

| Module | Key concerns |
|--------|--------------|
| `trading/domain/` | `Order`, `Position`, `Signal`, `RiskLimits` (interfaces) |
| `trading/data/` | OHLCV ingestion, normalization, gap detection |
| `trading/strategies/` | Signal logic per strategy; no exchange imports |
| `trading/backtest/` | Event-driven or vectorized backtest; fee/slippage model |
| `trading/risk/` | Sizing, stop-loss, max drawdown, exposure caps |
| `trading/execution/` | Paper broker, live broker, order state machine |
| `trading/infrastructure/` | REST/WebSocket exchange clients, DB repos |
| `trading/application/` | Orchestrators wiring domain + infrastructure |
| `trading/docs/requirements/` | User stories and acceptance criteria |

> **Note:** Folders are scaffolded as development progresses. Agents should create modules in the layer that matches their concern.

## Multi-role workflow

| Role | Rule file | When active |
|------|-----------|-------------|
| Risk & Quant Analyst | `.cursor/rules/risk-quant.mdc` | Always (highest priority) |
| Technical Leader | `.cursor/rules/tech-lead.mdc` | Always |
| Developer | `.cursor/rules/dev.mdc` | Files under `trading/**/*.{ts,tsx,mjs,js}` |
| QA | `.cursor/rules/qa.mdc` | Test files under `trading/` |
| Product Owner | `.cursor/rules/product-owner.mdc` | `trading/docs/`, `docs/trading/`, `trading/**/*.md` |

## Test commands

```bash
# Run all tests (watch mode)
npm test

# Run all tests once (CI)
npm run test:run

# Run only trading bot tests (once trading/ exists)
npm run test:run -- trading/

# Lint
npm run lint
```

## MCP-connected platforms

This project uses **two complementary MCP platforms**. They serve different stages of the strategy lifecycle — research/validation vs. project lifecycle/deployment.

| Platform | Setup | Primary roles | Purpose |
|----------|-------|---------------|---------|
| **vibe-trading** | `.cursor/mcp.json` (`vibe-trading-mcp`) | risk-quant | Multi-source research, market data (19+ sources), factor/alpha analysis, backtesting, swarm second opinions |
| **quantconnect** | Cursor extension (View → Extensions → "QuantConnect") | tech-lead, risk-quant | Project scaffolding, QC-native backtests, parameter optimization, paper/live deployment |

### vibe-trading

Registered in `.cursor/mcp.json`. Provides market data, backtest engines, a 460-alpha factor zoo, and swarm presets (`risk_committee`, `quant_strategy_desk`). **Dev and QA must not invoke this MCP by default.**

After changing `.cursor/mcp.json`, reload MCP in Cursor (Settings → MCP → refresh).

### quantconnect

Installed and authenticated via the **QuantConnect Cursor extension** (GUI step — not configured in this repo). Credentials are managed by the extension; agents must not attempt to configure QuantConnect auth from the terminal.

| Role | QuantConnect usage |
|------|-------------------|
| **tech-lead** | Project scaffolding, parameter optimization, **paper/live deployment** (only path for deployment) |
| **risk-quant** | **Second, independent backtest** on QC infrastructure before approving a strategy |

### Dual-backtest approval gate

A strategy is **not approved for paper or live trading** until:

1. **Vibe-Trading** validates research quality (bias checks, factor zoo, cost modeling, metrics)
2. **QuantConnect** produces an independent backtest on QC infrastructure
3. **risk-quant** issues an explicit approval note referencing **both** results

Two independent backtest engines agreeing is a stronger signal than one. A Vibe-Trading-only result is insufficient for deployment approval.

Call `GetMcpTools` with `{"server": "vibe-trading"}` or `{"server": "quantconnect"}` to inspect available tools after servers are connected.

## Environment

Secrets and exchange credentials live in `.env.local` (never committed). See `.env.example` for required variables as they are added.

## Related project

The repo root also contains **Gastos App** (Next.js expense tracker). Trading bot work is isolated under `trading/` and does not share runtime with the web app unless explicitly integrated later.
