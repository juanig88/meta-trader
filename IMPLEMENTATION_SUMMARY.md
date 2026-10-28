# XAUUSD Parallel Strategy Implementation Summary

> **Not the live bot.** Since 2026-10-07 production is the daily Donchian 20/10 + SMA200 **swing** on XBTFX demo (`trading/docs/SWING_ENGINE.md`); the crypto rotation is stopped. This file is a historical XAU work log.

## Task Overview
Implement critical fixes for the XAUUSD parallel trading strategy based on performance analysis showing:
- Net PnL: -$55.39 (balance dropped from ~$182.58 to $127.23)
- 295 trades in ~5 hours = ~1 trade/minute (extreme overtrading)
- Profit Factor: 0.52 (losing strategy)
- Win rate: 59.3% but avg win $0.34 vs avg loss -$0.96 (terrible R:R)
- Max drawdown: 33.9% ($65.03)
- Commissions: ~$23.60 (295 × $0.08) - huge drag

## Root Problems Identified
1. **Asymmetric and too tight TP/SL**: TP $0.25 (2.5 pips) vs Loss limit $1.0 (10 pips) = R:R 1:4 against
2. **Spread eats TP**: XAUUSD spread ~30-55 pts (3-5.5 pips) > TP of 2.5 pips
3. **No trend filter**: Buy/sell based only on last 2 H4 closes → whipsaw in range
4. **Overtrading**: 295 trades = 1/min; no real cooldown between trades
5. **Commissions destroy edge**: $23.60 in commissions vs $59.65 gross profit
6. **Loss limit triggers on noise**: -$1.0 hit on normal gold pullbacks

## Steps Completed ✅

### 1. Created .env Configuration File
Added all required environment variables for the XAUUSD parallel strategy:
```env
BOT_XAUUSD_PARALLEL_ENABLED=1
BOT_XAUUSD_PARALLEL_SYMBOL=XAUUSD
BOT_XAUUSD_PARALLEL_MAGIC=260415
BOT_XAUUSD_PARALLEL_LOT=0.01
BOT_XAUUSD_PARALLEL_TP_MONEY=2.0      # $2.0 TP (20 pips @ 0.01 lot)
BOT_XAUUSD_PARALLEL_SL_MONEY=1.0      # $1.0 SL (10 pips) - hard stop in broker
BOT_XAUUSD_PARALLEL_MAX_POSITIONS=1
BOT_XAUUSD_PARALLEL_MAX_SPREAD_POINTS=30   # No trade if spread > 30 pts (3 pips)
BOT_XAUUSD_PARALLEL_MIN_HOLD_SECONDS=300   # Min 5 min between open/close
BOT_XAUUSD_PARALLEL_COOLDOWN_SECONDS=600   # 10 min cooldown after close
BOT_XAUUSD_PARALLEL_TREND_FILTER=1         # 1=enable
BOT_XAUUSD_PARALLEL_TREND_TF=H1            # H1 or H4
BOT_XAUUSD_PARALLEL_TREND_LOOKBACK=50      # EMA 50 for trend
BOT_XAUUSD_PARALLEL_SESSION_START=13       # 13 UTC = 10 ARG (NY open)
BOT_XAUUSD_PARALLEL_SESSION_END=21         # 21 UTC = 18 ARG (NY close)
BOT_XAUUSD_PARALLEL_DAILY_LOSS_LIMIT=-10.0  # Stop if -$10 in day
BOT_XAUUSD_PARALLEL_MAX_TRADES_PER_DAY=20   # Max 20 trades/day
```

### 2. Restored bot_mt5.py from Git
The file was corrupted with invalid syntax from previous attempts. Restored to clean state using `git checkout bot_mt5.py`.

### 3. Analyzed Current Code Structure
Read and understood the existing XAUUSD parallel strategy implementation in bot_mt5.py:
- Config class has basic XAUUSD parallel parameters (lines 328-335)
- Functions: `run_xauusd_parallel_cycle()`, `manage_xauusd_parallel_positions()`, `open_xauusd_parallel_trade()`
- Need to add new parameters and update logic

## Steps Remaining ❌

### 4. Update Config Class with New Parameters
Add the following fields to the `Config` dataclass (around line 328-335):
```python
xauusd_parallel_enabled: bool = False
xauusd_parallel_symbol: str = "XAUUSD"
xauusd_parallel_magic: int = 260415
xauusd_parallel_lot: float = 0.01
xauusd_parallel_tp_money: float = 2.0      # Changed from 0.25
xauusd_parallel_sl_money: float = 1.0      # NEW: Hard SL in broker
xauusd_parallel_max_positions: int = 1
xauusd_parallel_max_spread_points: float = 30.0  # Changed from 55.0
# NEW parameters:
xauusd_parallel_min_hold_seconds: int = 300
xauusd_parallel_cooldown_seconds: int = 600
xauusd_parallel_trend_filter: bool = True
xauusd_parallel_trend_tf: str = "H1"
xauusd_parallel_trend_lookback: int = 50
xauusd_parallel_session_start: int = 13
xauusd_parallel_session_end: int = 21
xauusd_parallel_daily_loss_limit: float = -10.0
xauusd_parallel_max_trades_per_day: int = 20
```

### 5. Implement Trend Filtering Function
Create `_is_trend_favorable(symbol: str) -> bool` function that:
- Gets EMA(H1, 50) using existing `get_ema()` function
- Gets H4 trend using existing `get_trend_h4()` function
- Returns True only if:
  - BUY: price > EMA(H1,50) AND H4 trend == "UP"
  - SELL: price < EMA(H1,50) AND H4 trend == "DOWN"

### 6. Update `_effective_params()` Function
Add new XAUUSD parallel parameters to the returned dictionary so they can be accessed per-symbol.

### 7. Update `open_xauusd_parallel_trade()` Function
Add validation checks before opening trades:
- **Spread check**: Reject if spread > `xauusd_parallel_max_spread_points` (30 pts)
- **Trend filter**: Call `_is_trend_favorable()` and reject if False
- **Cooldown check**: Use existing `_can_trade()` with new cooldown parameter
- **Session filter**: Only allow trades between `session_start` (13) and `session_end` (21) UTC
- **Daily loss limit**: Check if daily realized PnL <= `daily_loss_limit` (-$10)
- **Daily trade limit**: Check if daily trade count >= `max_trades_per_day` (20)
- **Min hold time**: Track entry time, prevent close before `min_hold_seconds` (300s)

### 8. Update `manage_xauusd_parallel_positions()` Function
- Set SL/TP in broker request (not just floating): use `request["sl"]` and `request["tp"]`
- Implement realistic TP ($2.0) and SL ($1.0) in account currency
- Respect `min_hold_seconds` before allowing close
- Track daily PnL and trade count

### 9. Implement Daily Tracking Functions
Create helper functions:
- `_get_daily_realized_pnl(symbol: str, magic: int) -> float`: Sum realized PnL for today
- `_get_daily_trade_count(symbol: str, magic: int) -> int`: Count closed trades today
- `_record_daily_trade(symbol: str, magic: int)`: Increment daily counter
- Reset counters at midnight

### 10. Update `run_xauusd_parallel_cycle()` Function
- Call new validation functions
- Pass new parameters to `open_xauusd_parallel_trade()` and `manage_xauusd_parallel_positions()`
- Log all validation steps for debugging

### 11. Load Parameters from Environment Variables
Update the config loading logic (likely in `main()` or config initialization) to read the new `.env` variables and populate the Config object.

### 12. Testing & Verification
- [ ] Verify all new parameters load correctly from .env
- [ ] Test trend filtering with EMA(H1,50) and H4 trend
- [ ] Verify spread check rejects entries when spread > 30 points
- [ ] Verify cooldown prevents new trades within 10 minutes after close
- [ ] Verify session filter only allows trading 13-21 UTC
- [ ] Verify daily loss limit stops trading when PnL <= -$10
- [ ] Verify daily trade limit caps at 20 trades per day
- [ ] Test position management with SL/TP set in broker
- [ ] Verify min hold time (5 min) prevents premature closes
- [ ] Test edge cases: no trend, flat market, extreme spread, cooldown period
- [ ] Verify logging shows all new validation steps
- [ ] Verify no regression in existing functionality for other symbols
- [ ] Verify XAUUSD parallel strategy doesn't affect other symbol trading

## Key Implementation Details

### Files to Modify
- `bot_mt5.py` - Main implementation file
- `.env` - Already created with new parameters

### Existing Functions to Leverage
- `get_ema(symbol, timeframe, period)` - For H1 EMA(50)
- `get_trend_h4(symbol)` - For H4 trend direction
- `_get_spread_points(symbol)` - For spread check
- `_can_trade(cfg, symbol, now)` - For cooldown (needs parameter update)
- `_session_allows_new_trade()` - For session filtering (needs XAUUSD-specific session)
- `mt5.symbol_info_tick(symbol).bid` - For current price

### New Global State Variables Needed
```python
# Daily tracking (reset at midnight)
_xauusd_daily_pnl: dict[date, float] = {}
_xauusd_daily_trades: dict[date, int] = {}
_xauusd_last_trade_time: dict[str, datetime] = {}
_xauusd_entry_times: dict[int, datetime] = {}  # ticket -> entry time
```

## Configuration Changes Summary

| Parameter | Old Value | New Value | Reason |
|-----------|-----------|-----------|--------|
| TP | $0.25 (2.5 pips) | $2.0 (20 pips) | Realistic R:R ≥ 1:2 |
| SL | None (floating -$1.0) | $1.0 (10 pips) | Hard stop in broker |
| Max Spread | 55 pts | 30 pts | Spread must be < TP |
| Cooldown | None effective | 600 sec (10 min) | Prevent overtrading |
| Trend Filter | None | EMA(H1,50) + H4 | Avoid whipsaw |
| Session | None | 13-21 UTC | NY session only |
| Daily Loss Limit | None | -$10 | Risk control |
| Max Trades/Day | None | 20 | Prevent overtrading |
| Min Hold Time | None | 300 sec (5 min) | Prevent scalping noise |

## Next Steps for New Conversation
1. Start by updating the Config class with new parameters
2. Implement the trend filtering function
3. Update `_effective_params()` to include new params
4. Update `open_xauusd_parallel_trade()` with all validation checks
5. Update `manage_xauusd_parallel_positions()` with broker SL/TP
6. Implement daily tracking functions
7. Update `run_xauusd_parallel_cycle()` to orchestrate everything
8. Test thoroughly with paper trading before live</arg_value>
</write_to_file>