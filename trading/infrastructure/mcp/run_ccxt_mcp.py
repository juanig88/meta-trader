"""Launch official ccxt-mcp with a filtered environment (credentials from .env only)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.infrastructure.mcp.env_filter import child_env_for_ccxt
from trading.infrastructure.mcp.local_snapshot import resolve_root

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional at runtime if already exported
    load_dotenv = None


def _workspace_root() -> Path:
    return resolve_root(os.environ.get("LOCAL_MCP_ROOT"), fallback=_ROOT)


def load_workspace_env(root: Path) -> None:
    if load_dotenv is None:
        return
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def npx_command() -> list[str] | None:
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", "ccxt-mcp"]
    return None


def main() -> int:
    root = _workspace_root()
    load_workspace_env(root)
    env = child_env_for_ccxt(os.environ)
    cmd = npx_command()
    if cmd is None:
        sys.stderr.write("npx not found on PATH. Install Node.js 18+ to run ccxt-mcp.\n")
        return 1
    completed = subprocess.run(cmd, env=env, check=False)
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
