"""Build a child environment for ccxt-mcp that never inherits broker/chat secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping

# Keys that look like credentials. CCXT_MCP_* is allowlisted even if it matches.
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization|login)",
    re.IGNORECASE,
)

_ALWAYS_DROP_PREFIXES = (
    "MT5_",
    "TELEGRAM_",
    "GITHUB_TOKEN",
    "QUANTCONNECT_",
)

_CCXT_PREFIX = "CCXT_MCP_"

# Trading via MCP is out of scope for this workspace (QuantConnect-only deployment).
_FORCED_CCXT_ENV = {
    "CCXT_MCP_TRADING": "0",
    "CCXT_MCP_LOAD_ENV_KEYS": "0",
}


def is_secret_env_key(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return False
    if k.startswith(_CCXT_PREFIX):
        return False
    upper = k.upper()
    if any(upper.startswith(p) or upper == p.rstrip("_") for p in _ALWAYS_DROP_PREFIXES):
        return True
    return _SECRET_KEY_RE.search(k) is not None


def child_env_for_ccxt(src: Mapping[str, str]) -> dict[str, str]:
    """
    Copy `src` for a ccxt-mcp subprocess.

    Drops MT5/Telegram and any secret-looking keys except `CCXT_MCP_*`.
    Forces trading off so the agent cannot place orders through this MCP.
    """
    out: dict[str, str] = {}
    for key, value in src.items():
        if value is None:
            continue
        if is_secret_env_key(key):
            continue
        out[str(key)] = str(value)
    out.update(_FORCED_CCXT_ENV)
    return out
