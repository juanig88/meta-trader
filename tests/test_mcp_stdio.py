from __future__ import annotations

from trading.infrastructure.mcp.server import dispatch
from trading.infrastructure.mcp.stdio_jsonrpc import encode_message, parse_message


def test_parse_and_encode_roundtrip():
    cases = [
        ("initialize", '{"jsonrpc":"2.0","id":1,"method":"initialize"}'),
        ("blank", "   "),
        ("not json", "{nope"),
        ("array", "[1,2]"),
    ]
    parsed = [parse_message(raw) for _, raw in cases]
    assert parsed[0]["method"] == "initialize"
    assert parsed[1] is None
    assert parsed[2] is None
    assert parsed[3] is None
    encoded = encode_message({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert encoded.endswith("\n")
    assert parse_message(encoded)["id"] == 1


def test_dispatch_initialize_and_tools_list():
    init = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "local-trading"
    listed = dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"get_bot_snapshot", "get_eval_tracks", "get_recent_eval_events"}


def test_dispatch_unknown_method_and_notification():
    err = dispatch({"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert err["error"]["code"] == -32601
    assert dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_dispatch_unknown_tool():
    reply = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_order", "arguments": {}},
        }
    )
    assert reply["result"]["isError"] is True
    assert "unknown tool" in reply["result"]["content"][0]["text"]
