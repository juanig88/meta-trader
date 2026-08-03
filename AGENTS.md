# Trading Bot — Agent Context

This repository hosts a **trading bot** (under `trading/`) alongside the existing Gastos expense-tracker app. This file answers **what the project is**; role behavior is defined in `.cursor/rules/*.mdc`.

## What the bot does

Automated trading system that:

1. Ingests market data from configured exchanges
2. Runs one or more strategies to generate signals
3. Applies risk checks before any order is sent
4. Executes orders in **paper** or **live** mode
5. Logs all decisions for audit and post-trade analysis

The bot prioritizes **capital preservation** over raw backtest returns. No strategy ships without risk-quant validation.

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
| **Docs** | `trading/docs/` | Requirements, acceptance criteria, runbooks |

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
