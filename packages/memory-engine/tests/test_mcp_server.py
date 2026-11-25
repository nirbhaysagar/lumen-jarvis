"""
Tests for Lumen MCP Server (Milestone P1).
Verifies:
1. Read-only safety gate: remember, forget, correct are refused when MCP_ALLOW_WRITES=0.
2. Read tools (recall, context, timeline, directives) work via MCPServer.call_tool().
3. Write tools work when MCP_ALLOW_WRITES=1.
4. Sensitivity isolation: secret and private memories are filtered properly.
5. MCP Resources (lumen://directives) return valid JSON.
"""
import json
import os
import uuid
import pytest
from httpx import ASGITransport

from app.core.config import settings
from app.main import app
from app.mcp.server import mcp, set_custom_transport, _filter_sensitivity


def _extract_tool_data(res) -> dict:
    if hasattr(res, "structuredContent") and res.structuredContent:
        return res.structuredContent
    if hasattr(res, "content") and res.content:
        return json.loads(res.content[0].text)
    if isinstance(res, tuple):
        return res[1]
    return json.loads(res[0].text)


@pytest.mark.asyncio
async def test_mcp_read_only_gate_and_tools(monkeypatch):
    monkeypatch.setenv("LUMEN_MEMORY_TOKEN", settings.LUMEN_MEMORY_TOKEN)
    monkeypatch.setenv("MCP_ALLOW_WRITES", "0")
    monkeypatch.setenv("MCP_INCLUDE_PRIVATE", "0")

    transport = ASGITransport(app=app)
    set_custom_transport(transport)

    try:
        async with app.router.lifespan_context(app):
            # 1. Verify tools list
            tools = await mcp.list_tools()
            tool_names = {t.name for t in tools}
            assert {"remember", "recall", "context", "forget", "correct", "timeline", "directives"}.issubset(tool_names)

            # 2. Write tools MUST be refused when MCP_ALLOW_WRITES=0
            rem_data = _extract_tool_data(await mcp.call_tool("remember", {"text": "Test MCP memory"}))
            assert rem_data["status"] == "refused"

            fgt_data = _extract_tool_data(await mcp.call_tool("forget", {"memory_id": str(uuid.uuid4()), "reason": "test"}))
            assert fgt_data["status"] == "refused"

            cor_data = _extract_tool_data(
                await mcp.call_tool(
                    "correct",
                    {"memory_id": str(uuid.uuid4()), "new_statement": "X", "base_version": 1, "reason": "test"},
                )
            )
            assert cor_data["status"] == "refused"

            # 3. Read tools work when MCP_ALLOW_WRITES=0
            dir_data = _extract_tool_data(await mcp.call_tool("directives", {}))
            assert "directives" in dir_data

            tl_data = _extract_tool_data(await mcp.call_tool("timeline", {}))
            assert "timeline" in tl_data

            ctx_data = _extract_tool_data(await mcp.call_tool("context", {"query": "What is my setup?"}))
            assert "prompt_injection" in ctx_data

            # 4. Resources work
            res_contents = await mcp.read_resource("lumen://directives")
            if hasattr(res_contents, "contents"):
                raw_str = res_contents.contents[0].content
            elif hasattr(res_contents[0], "content"):
                raw_str = res_contents[0].content
            else:
                raw_str = str(res_contents[0])
            parsed_res = json.loads(raw_str)
            assert "directives" in parsed_res

            # 5. Enable writes and test remember
            monkeypatch.setenv("MCP_ALLOW_WRITES", "1")
            rem_ok_data = _extract_tool_data(
                await mcp.call_tool("remember", {"text": "Lumen MCP works seamlessly.", "client_name": "cursor"})
            )
            assert rem_ok_data["status"] == "captured"
            assert rem_ok_data["event_id"] is not None
            assert rem_ok_data["actor"] == "mcp:cursor"
    finally:
        set_custom_transport(None)


def test_mcp_sensitivity_filter(monkeypatch):
    items = [
        {"id": "1", "statement": "Public fact", "sensitivity": "normal"},
        {"id": "2", "statement": "Personal address", "sensitivity": "private"},
        {"id": "3", "statement": "API Key sk-1234", "sensitivity": "secret"},
    ]

    monkeypatch.setenv("MCP_INCLUDE_PRIVATE", "0")
    res_default = _filter_sensitivity(items)
    assert [i["id"] for i in res_default] == ["1"]

    monkeypatch.setenv("MCP_INCLUDE_PRIVATE", "1")
    res_priv = _filter_sensitivity(items)
    assert [i["id"] for i in res_priv] == ["1", "2"]
