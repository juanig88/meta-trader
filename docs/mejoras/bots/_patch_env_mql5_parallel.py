"""Aplica config paralelo post-MQL5 insights al .env (sin tocar secrets)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV = ROOT / ".env"

# Ventanas ART (LOCAL) derivadas del mining no-grid + overlap Londres/NY.
XAU_SESSIONS = "07:00-10:00,14:00-16:00,19:00-23:00"
SYMBOLS = "BTCUSD,EURUSD,USDJPY"

EURUSD = {
    "ema_fast_m15": 12,
    "ema_slow_m15": 26,
    "sl_tp_atr_enabled": True,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.25,
    "sl_tp_atr_min_points": 80,
    "max_spread_points": 25,
    "adx_min": 20,
    "risk_per_trade_pct": 1.5,
    "trading_window_sessions": "08:00-12:00,14:00-18:00",
    "max_positions_per_symbol": 1,
    "cooldown_minutes": 25,
    "max_trades_per_hour_per_symbol": 2,
    "price_slope_filter_enabled": True,
    "price_slope_n": 6,
    "range_filter_enabled": True,
    "range_filter_timeframe": "H1",
    "range_filter_lookback_bars": 20,
    "range_filter_mid_frac": 0.28,
    "anti_chase_atr_mult": 2.5,
    "rsi_h4_filter_enabled": False,
    "extension_filter_max_pct": 3.5,
    "extension_filter_h1_bars": 16,
    "break_even_trigger_money": 1.5,
    "trail_trigger_money": 3.0,
    "trail_new_sl_money": 1.5,
    "trail_new_tp_money": 4.5,
    "filter_exit_min_profit_money": 0.3,
    "eval_track": "eur_session_rr15",
}

USDJPY = {
    "ema_fast_m15": 12,
    "ema_slow_m15": 26,
    "sl_tp_atr_enabled": True,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "sl_tp_atr_min_points": 100,
    "max_spread_points": 30,
    "adx_min": 18,
    "risk_per_trade_pct": 1.5,
    "trading_window_sessions": "07:00-12:00,20:00-02:00",
    "max_positions_per_symbol": 1,
    "cooldown_minutes": 30,
    "max_trades_per_hour_per_symbol": 2,
    "price_slope_filter_enabled": True,
    "price_slope_n": 6,
    "range_filter_enabled": True,
    "range_filter_timeframe": "H1",
    "range_filter_lookback_bars": 24,
    "range_filter_mid_frac": 0.25,
    "anti_chase_atr_mult": 2.5,
    "rsi_h4_filter_enabled": False,
    "extension_filter_max_pct": 3.0,
    "extension_filter_h1_bars": 16,
    "break_even_trigger_money": 1.5,
    "trail_trigger_money": 3.0,
    "trail_new_sl_money": 1.5,
    "trail_new_tp_money": 5.0,
    "filter_exit_min_profit_money": 0.4,
    "eval_track": "jpy_session_rr15",
}


def _set_line(text: str, key: str, value: str) -> str:
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    if pat.search(text):
        return pat.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"


def main() -> int:
    text = ENV.read_text(encoding="utf-8")
    text = _set_line(text, "BOT_SYMBOLS", SYMBOLS)
    text = _set_line(text, "BOT_MAX_OPEN_TRADES", "4")
    text = _set_line(text, "BOT_MAX_OPEN_TRADES_TOTAL", "4")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_ENABLED", "1")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_SESSIONS", XAU_SESSIONS)
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_START_HOUR", "7")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_END_HOUR", "23")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_TP_RR", "2.0")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_SL_ATR_MULT", "1.5")
    text = _set_line(text, "BOT_XAUUSD_PARALLEL_ADX_MIN", "22")
    text = _set_line(text, "BOT_QUIET_SESSION_EXEMPT_SYMBOLS", "USDJPY,BTCUSD")
    text = _set_line(
        text,
        "BOT_PORTFOLIO_WEIGHTS",
        json.dumps({"BTCUSD": 0.35, "EURUSD": 0.35, "USDJPY": 0.30}, separators=(",", ":")),
    )
    text = _set_line(
        text,
        "BOT_CORRELATION_GROUPS",
        json.dumps(
            {"CRYPTO": ["BTCUSD"], "FX_MAJORS": ["EURUSD", "USDJPY"]},
            separators=(",", ":"),
        ),
    )

    m = re.search(r"^BOT_SYMBOL_CONFIG=(.*)$", text, re.M)
    if not m:
        raise SystemExit("BOT_SYMBOL_CONFIG not found")
    raw = m.group(1).strip()
    cfg = json.loads(raw)
    # Drop XNG from active book (keep key optional for history)
    cfg.pop("XNGUSD", None)
    # BTC: keep sessions; slightly ease ADX via overlay later; ensure RR via ATR
    btc = dict(cfg.get("BTCUSD") or {})
    btc["trading_window_sessions"] = "06:00-09:00,14:00-18:00,21:00-23:00"
    btc["sl_tp_atr_enabled"] = True
    btc["sl_atr_mult"] = 1.5
    btc["tp_atr_mult"] = 2.25
    btc["eval_track"] = "btc_breakout_h4"
    btc["max_positions_per_symbol"] = 1
    cfg["BTCUSD"] = btc
    cfg["EURUSD"] = {**(cfg.get("EURUSD") or {}), **EURUSD}
    # Replace USDJPY fully (remove replay_gate)
    cfg["USDJPY"] = USDJPY

    new_cfg = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False)
    text = re.sub(r"^BOT_SYMBOL_CONFIG=.*$", "BOT_SYMBOL_CONFIG=" + new_cfg, text, count=1, flags=re.M)
    ENV.write_text(text, encoding="utf-8")
    print("patched", ENV)
    print("BOT_SYMBOLS", SYMBOLS)
    print("XAU sessions", XAU_SESSIONS)
    print("symbols in config", sorted(cfg.keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
