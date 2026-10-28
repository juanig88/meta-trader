"""Stdio MCP server: read-only local bot state / eval logs. No order routing."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.infrastructure.mcp.local_snapshot import (
    bot_snapshot,
    eval_tracks,
    recent_eval_events,
    resolve_root,
)
from trading.infrastructure.mcp.stdio_jsonrpc import (
    error_message,
    parse_message,
    result_message,
    write_message,
)

_SERVER_NAME = "local-trading"
_SERVER_VERSION = "1.0.0"
_PROTOCOL = "2024-11-05"

_TOOLS = [
    {
        "name": "get_bot_snapshot",
        "description": (
            "Read-only snapshot of local bot_state.json (health + redacted state). "
            "Does not connect to MT5 and cannot place orders."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_eval_tracks",
        "description": "Read-only eval_tracks block from bot_state.json, if present.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_recent_eval_events",
        "description": (
            "Tail-parse local trades.log for EVAL_* events. "
            "Read-only analysis; no broker calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max events to return (newest last). Default 40.",
                }
            },
            "additionalProperties": False,
        },
    },
]


def _workspace_root() -> Path:
    return resolve_root(os.environ.get("LOCAL_MCP_ROOT"), fallback=_ROOT)


def _initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": _PROTOCOL,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        "instructions": (
            "Local MetaTrader bot snapshot only. Public crypto market data lives on the "
            "ccxt and vibe-trading MCP servers. This server never sends orders."
        ),
    }


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if not isinstance(args, dict):
        return _text_result({"error": "arguments must be an object"}, is_error=True)
    root = _workspace_root()
    if name == "get_bot_snapshot":
        return _text_result(bot_snapshot(root))
    if name == "get_eval_tracks":
        return _text_result(eval_tracks(root))
    if name == "get_recent_eval_events":
        return _text_result(recent_eval_events(root, limit=args.get("limit", 40)))
    return _text_result({"error": f"unknown tool: {name}"}, is_error=True)


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications return None."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None

    if request_id is None:
        return None

    if method == "initialize":
        return result_message(request_id, _initialize_result())
    if method == "ping":
        return result_message(request_id, {})
    if method == "tools/list":
        return result_message(request_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        return result_message(request_id, _call_tool(name, arguments))

    return error_message(request_id, -32601, f"method not found: {method}")


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    while True:
        line = sys.stdin.readline()
        if line == "":
            return 0
        message = parse_message(line)
        if message is None:
            continue
        reply = dispatch(message)
        if reply is not None:
            write_message(sys.stdout, reply)


if __name__ == "__main__":
    raise SystemExit(main())
