"""MCP server protocol shape (the docker-exec path is proven by the live spike)."""

from __future__ import annotations

from kontinuum.sandbox_mcp import _handle


def test_initialize_echoes_protocol_version():
    r = _handle({"method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r["protocolVersion"] == "2025-06-18"
    assert r["serverInfo"]["name"] == "kontinuum-sandbox"


def test_tools_list_exposes_the_run_tool():
    r = _handle({"method": "tools/list"})
    assert r["tools"][0]["name"] == "run"
    assert "command" in r["tools"][0]["inputSchema"]["properties"]
