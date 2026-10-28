# Trading docs

Canonical **live** runbook (since 2026-10-07):

- **[SWING_ENGINE.md](SWING_ENGINE.md)** — swing diario Donchian 20/10 + SMA200, solo compras, 0.5% / 10 posiciones, evidencia Vibe + QuantConnect, nota risk-quant, catálogo de `swing_trades.log`. Demo XBTFX only.

Stopped engine (history):

- **[ROTATION_ENGINE.md](ROTATION_ENGINE.md)** — rotación 5 módulos, 4/5, veto HTF M15, Kelly $0.50. Apagada el 2026-10-07 (no ganaba).

Agent context: [`AGENTS.md`](../../AGENTS.md). Operator manuals: [`README.md`](../../README.md), [`MANUAL.md`](../../MANUAL.md).

Do not edit the original ensemble plan under `docs/improvement/` unless the user asks.

Requirements / acceptance criteria (product-owner) go in `requirements/` when added.
