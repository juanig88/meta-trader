# Exness BTC M5 structure-breakout research

This is a **read-only exploratory test**, not a live strategy approval. No
orders are sent. The hypothesis was formulated after inspecting the user's
BTCUSDm screenshots from 2026-10-24 and 2026-10-25; those selected examples
are therefore discovery data, not independent validation.

## Frozen exploratory rule

On a closed M5 bar, signal a BUY if all are true:

1. The close is above the highest high of the preceding 12 completed M5 bars.
2. The close is above EMA20 and EMA20 is higher than it was four M5 bars ago.
3. Tick volume is at least 1.2 times the mean tick volume of the preceding 20
   completed M5 bars.

SELL is the exact inverse using the preceding 12-bar low and falling EMA20.
All thresholds are fixed before evaluating the broad history and must not be
selected or altered based on these results.

Entry is the next M5 bar's open. Each holding horizon (15, 60, and 240 minutes)
is tested independently, with one open position per horizon and symbol. The
test exits at the corresponding closed bar; it has no stop loss or take profit
and therefore measures directional persistence, not a complete trading plan.

H4 bias is reported as a diagnostic only: the last fully closed H4 close and
EMA20 slope over four H4 bars. It is not an entry gate.

## Cost and data limitations

- Uses the broker's historical `BTCUSDm` M5 OHLC and tick volume; chart OHLC is
  treated as bid and spread is estimated from each entry/exit bar's spread.
- Net PnL is shown at the current minimum lot and contract size. Historical
  contract terms may differ.
- Commission, slippage, and swap are excluded. UTC date crossings are reported
  because a 4-hour position may cross the broker rollover.
- Tick volume is broker activity, not consolidated crypto-exchange volume.
- Broker history availability determines the sample period. A positive
  exploratory result would still require an untouched prospective holdout,
  accurate commission/swap modeling, and demo forward validation.

## Interpretation guardrails

The chart-selected examples are not evidence of an edge by themselves.
Report all signals, skipped signals while a position is open, both sides,
each fixed horizon, confidence intervals clustered by UTC entry day, and
historical spread costs. Do not tune the rule, choose a winning horizon, or
invert the side based on these results.
