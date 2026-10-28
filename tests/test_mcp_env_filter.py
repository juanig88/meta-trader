from __future__ import annotations

from trading.infrastructure.mcp.env_filter import child_env_for_ccxt, is_secret_env_key


def test_is_secret_env_key_table():
    cases = [
        ("mt5 password", "MT5_PASSWORD", True),
        ("mt5 login", "MT5_LOGIN", True),
        ("telegram token", "TELEGRAM_BOT_TOKEN", True),
        ("binance leftover", "BINANCE_APIKEY", True),
        ("okx secret", "OKX_SECRET", True),
        ("github token", "GITHUB_TOKEN", True),
        ("ccxt apikey kept", "CCXT_MCP_APIKEY", False),
        ("ccxt secret kept", "CCXT_MCP_SECRET", False),
        ("ccxt exchange kept", "CCXT_MCP_EXCHANGE", False),
        ("path kept", "PATH", False),
        ("empty", "", False),
        ("comspec kept", "COMSPEC", False),
    ]
    for name, key, want in cases:
        assert is_secret_env_key(key) is want, name


def test_child_env_for_ccxt_drops_broker_secrets_and_forces_trading_off():
    src = {
        "PATH": "C:\\Windows",
        "MT5_PASSWORD": "super-secret",
        "MT5_LOGIN": "12345",
        "TELEGRAM_BOT_TOKEN": "tg-secret",
        "BINANCE_APIKEY": "should-drop",
        "CCXT_MCP_APIKEY": "ccxt-key",
        "CCXT_MCP_SECRET": "ccxt-secret",
        "CCXT_MCP_EXCHANGE": "binance",
        "CCXT_MCP_TRADING": "live",
        "CCXT_MCP_LOAD_ENV_KEYS": "1",
    }
    got = child_env_for_ccxt(src)
    assert "MT5_PASSWORD" not in got
    assert "MT5_LOGIN" not in got
    assert "TELEGRAM_BOT_TOKEN" not in got
    assert "BINANCE_APIKEY" not in got
    assert got["CCXT_MCP_APIKEY"] == "ccxt-key"
    assert got["CCXT_MCP_SECRET"] == "ccxt-secret"
    assert got["CCXT_MCP_EXCHANGE"] == "binance"
    assert got["PATH"] == "C:\\Windows"
    assert got["CCXT_MCP_TRADING"] == "0"
    assert got["CCXT_MCP_LOAD_ENV_KEYS"] == "0"
