"""Patch .env for parallel demo tracks: XAU parallel + BTC + XNG."""
from __future__ import annotations

import json
import re
from pathlib import Path

ENV = Path(__file__).resolve().parents[4] / ".env"


def main() -> None:
    text = ENV.read_text(encoding="utf-8")
    m = re.search(r"^BOT_SYMBOL_CONFIG=(.*)$", text, re.M)
    if not m:
        raise SystemExit("BOT_SYMBOL_CONFIG not found")
    cfg = json.loads(m.group(1))

    btc = dict(cfg.get("BTCUSD") or {})
    btc.update(
        {
            "trading_window_sessions": "06:00-09:00,14:00-18:00,21:00-23:00",
            "max_positions_per_symbol": 1,
            "max_trades_per_hour_per_symbol": 2,
            "cooldown_minutes": 45,
            "adx_min": 22,
            "rsi_h4_filter_enabled": True,
            "sl_tp_atr_enabled": True,
            "sl_atr_mult": 2.0,
            "tp_atr_mult": 3.5,
            "eval_track": "btc_breakout_h4",
        }
    )
    cfg["BTCUSD"] = btc

    cfg["XNGUSD"] = {
        "trading_window_sessions": "08:00-20:00",
        "max_positions_per_symbol": 1,
        "max_lot_per_trade": 0.1,
        "lot": 0.1,
        "use_risk_sizing": False,
        "max_spread_points": 80,
        "adx_min": 20,
        "ema_fast_m15": 50,
        "ema_slow_m15": 200,
        "cooldown_minutes": 30,
        "max_trades_per_hour_per_symbol": 3,
        "sl_tp_atr_enabled": True,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,
        "sl_tp_atr_min_points": 50,
        "price_slope_filter_enabled": True,
        "price_slope_n": 6,
        "range_filter_enabled": True,
        "range_filter_timeframe": "H1",
        "range_filter_lookback_bars": 24,
        "range_filter_mid_frac": 0.28,
        "anti_chase_atr_mult": 2.5,
        "rsi_h4_filter_enabled": False,
        "extension_filter_max_pct": 5.0,
        "extension_filter_h1_bars": 16,
        "filter_exit_min_profit_money": 0.5,
        "break_even_trigger_money": 2.0,
        "trail_trigger_money": 4.0,
        "trail_new_sl_money": 2.0,
        "trail_new_tp_money": 6.0,
        "eval_track": "xng_breakout_session",
    }

    def sub(pattern: str, repl: str) -> None:
        nonlocal text
        text2, n = re.subn(pattern, repl, text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"failed to patch: {pattern}")
        text = text2

    sub(r"^BOT_SYMBOL_CONFIG=.*$", "BOT_SYMBOL_CONFIG=" + json.dumps(cfg, ensure_ascii=False, separators=(",", ":")))
    sub(r"^BOT_SYMBOLS=.*$", "BOT_SYMBOLS=BTCUSD,XNGUSD")
    sub(
        r"^BOT_PORTFOLIO_WEIGHTS=.*$",
        "BOT_PORTFOLIO_WEIGHTS=" + json.dumps({"BTCUSD": 0.5, "XNGUSD": 0.5}, separators=(",", ":")),
    )
    sub(
        r"^BOT_CORRELATION_GROUPS=.*$",
        "BOT_CORRELATION_GROUPS="
        + json.dumps({"CRYPTO": ["BTCUSD"], "ENERGY": ["XNGUSD"]}, separators=(",", ":")),
    )
    sub(r"^BOT_MAX_POSITIONS_PER_SYMBOL=.*$", "BOT_MAX_POSITIONS_PER_SYMBOL=1")
    sub(r"^BOT_MAX_OPEN_TRADES=.*$", "BOT_MAX_OPEN_TRADES=3")
    sub(r"^BOT_MAX_OPEN_TRADES_TOTAL=.*$", "BOT_MAX_OPEN_TRADES_TOTAL=3")
    sub(r"^BOT_DAILY_LOSS_LIMIT=.*$", "BOT_DAILY_LOSS_LIMIT=-25.0")
    sub(r"^BOT_MAX_LOT_PER_TRADE=.*$", "BOT_MAX_LOT_PER_TRADE=0.1")
    sub(
        r"^# Main loop:.*$",
        "# Main loop: BTCUSD + XNGUSD (ventanas LOCAL). XAUUSD solo via parallel.",
    )

    if re.search(r"^BOT_XAUUSD_PARALLEL_SL_MODE=", text, re.M):
        sub(r"^BOT_XAUUSD_PARALLEL_SL_MODE=.*$", "BOT_XAUUSD_PARALLEL_SL_MODE=atr")
    else:
        text = text.replace(
            "BOT_XAUUSD_PARALLEL_ENABLED=1",
            "\n".join(
                [
                    "BOT_XAUUSD_PARALLEL_ENABLED=1",
                    "BOT_XAUUSD_PARALLEL_SL_MODE=atr",
                    "BOT_XAUUSD_PARALLEL_SL_ATR_MULT=1.5",
                    "BOT_XAUUSD_PARALLEL_TP_RR=2.0",
                    "BOT_XAUUSD_PARALLEL_ADX_MIN=25",
                    "BOT_XAUUSD_PARALLEL_H1_FILTER=1",
                ]
            ),
        )

    # Ensure telegram session + signal notify for eval
    if re.search(r"^TELEGRAM_NOTIFY_SESSION_WINDOW=", text, re.M):
        sub(r"^TELEGRAM_NOTIFY_SESSION_WINDOW=.*$", "TELEGRAM_NOTIFY_SESSION_WINDOW=1")
    if re.search(r"^TELEGRAM_NOTIFY_SIGNALS=", text, re.M):
        sub(r"^TELEGRAM_NOTIFY_SIGNALS=.*$", "TELEGRAM_NOTIFY_SIGNALS=1")

    ENV.write_text(text, encoding="utf-8")
    print("patched", ENV)
    print("BOT_SYMBOLS=BTCUSD,XNGUSD")
    print("XNGUSD lot min note: 0.1 on IC Markets")


if __name__ == "__main__":
    main()
