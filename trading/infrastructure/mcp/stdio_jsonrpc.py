"""Newline-delimited JSON-RPC 2.0 helpers for MCP stdio transport."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TextIO


def parse_message(line: str) -> dict[str, Any] | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def encode_message(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def result_message(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_message(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def write_message(stream: TextIO, payload: Mapping[str, Any]) -> None:
    stream.write(encode_message(payload))
    stream.flush()
