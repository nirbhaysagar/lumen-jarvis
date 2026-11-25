"""
Model Context Protocol (MCP) Server for Lumen Memory Engine (Milestone P1).
Exposes remember, recall, context, forget, correct, timeline, and directives tools
plus lumen://memory/{id}, lumen://entity/{name}, and lumen://directives resources
over stdio and streamable HTTP transports.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
from typing import Any, Callable, Optional
import httpx
from mcp.server.mcpserver import MCPServer

logger = logging.getLogger("lumen.mcp")

# Global/configurable transport override for in-memory testing
_CUSTOM_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


def set_custom_transport(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    """Allows tests to inject an ASGITransport for in-process testing."""
    global _CUSTOM_TRANSPORT
    _CUSTOM_TRANSPORT = transport


def is_writes_allowed() -> bool:
    """MCP is read-only by default (MCP_ALLOW_WRITES=0)."""
    val = os.getenv("MCP_ALLOW_WRITES", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def is_private_included() -> bool:
    """Private memories are excluded from MCP unless MCP_INCLUDE_PRIVATE=1."""
    val = os.getenv("MCP_INCLUDE_PRIVATE", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def _get_engine_url() -> str:
    return os.getenv("LUMEN_ENGINE_URL", "http://127.0.0.1:8000").rstrip("/")


def _get_token() -> str:
    return os.getenv("LUMEN_MEMORY_TOKEN", "")


async def _api_request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Executes an authenticated request against the Lumen Memory Engine HTTP API."""
    base_url = _get_engine_url()
    token = _get_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    kwargs: dict[str, Any] = {"timeout": 30.0}
    if _CUSTOM_TRANSPORT is not None:
        kwargs["transport"] = _CUSTOM_TRANSPORT
        base_url = "http://test"

    async with httpx.AsyncClient(base_url=base_url, **kwargs) as client:
        resp = await client.request(method, path, json=json_body, params=params, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Lumen API error ({resp.status_code}): {detail}")
        return resp.json()


def _filter_sensitivity(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enforces sensitivity policy: secret is never returned; private requires MCP_INCLUDE_PRIVATE=1."""
    include_priv = is_private_included()
    filtered = []
    for item in items:
        sens = item.get("sensitivity", "normal")
        if sens == "secret":
            continue
        if sens == "private" and not include_priv:
            continue
        filtered.append(item)
    return filtered


# Initialize MCPServer (FastMCP in mcp 2.x)
mcp = MCPServer("lumen-memory")


@mcp.tool()
async def remember(
    text: str,
    source: str = "mcp",
    occurred_at: Optional[str] = None,
    lane: Optional[str] = None,
    client_name: str = "agent",
) -> dict[str, Any]:
    """
    Capture a new observation or memory into Lumen's append-only event log
    and enqueue structured extraction. Refused when MCP_ALLOW_WRITES=0.
    """
    if not is_writes_allowed():
        return {
            "status": "refused",
            "error": "Write operations are disabled on this MCP server (MCP_ALLOW_WRITES=0).",
        }

    actor = f"mcp:{client_name}"
    payload: dict[str, Any] = {
        "content": text,
        "speaker": actor,
        "source": {
            "kind": "import",
            "title": source,
            "author": actor,
        },
        "context": "default",
        "metadata": {
            "epistemic_default": "belief",
            "via": "mcp",
            "lane_hint": lane,
        },
    }
    if occurred_at:
        payload["occurred_at"] = occurred_at

    res = await _api_request("POST", "/v1/capture", json_body=payload)
    return {
        "status": "captured",
        "event_id": res.get("event_id"),
        "actor": actor,
    }


@mcp.tool()
async def recall(
    query: str,
    limit: int = 5,
    as_of: Optional[str] = None,
    valid_at: Optional[str] = None,
) -> dict[str, Any]:
    """
    Hybrid 5-channel recall with cross-encoder reranking and honest abstention.
    Returns verified memories with exact quotes, dates, and version numbers.
    """
    body: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "context": "default",
    }
    if as_of:
        body["as_of"] = as_of
    if valid_at:
        body["valid_at"] = valid_at

    res = await _api_request("POST", "/v1/recall", json_body=body)
    raw_results = res.get("results", [])
    safe_results = _filter_sensitivity(raw_results)

    return {
        "status": res.get("status", "ok"),
        "abstain_reason": res.get("abstain_reason"),
        "trace_id": res.get("trace_id"),
        "results": [
            {
                "memory_id": r.get("id") or r.get("memory_id"),
                "lineage_id": r.get("lineage_id"),
                "statement": r.get("statement"),
                "version": r.get("version"),
                "said_at": r.get("said_at"),
                "quote": (r.get("citations") or [{}])[0].get("quote") if r.get("citations") else None,
                "score": r.get("score"),
            }
            for r in safe_results
        ],
    }


@mcp.tool()
async def context(query: str, limit: int = 8) -> dict[str, Any]:
    """
    Retrieves a full structured ContextPack (active directives, verified facts,
    citations, and grounded-answer prompt injection). Private & secret memories excluded by default.
    """
    res = await _api_request(
        "POST",
        "/v1/context",
        json_body={"query": query, "k": limit, "context": "default"},
    )
    safe_memories = _filter_sensitivity(res.get("relevant_memories", []))
    return {
        "status": res.get("status"),
        "abstain_reason": res.get("abstain_reason"),
        "trace_id": res.get("trace_id"),
        "directives": res.get("directives", []),
        "relevant_memories": safe_memories,
        "prompt_injection": res.get("prompt_injection", ""),
    }


@mcp.tool()
async def forget(
    memory_id: str,
    reason: str,
    purge_text: bool = False,
) -> dict[str, Any]:
    """
    Retracts a memory through its lineage with an append-only tombstone audit event.
    Refused when MCP_ALLOW_WRITES=0.
    """
    if not is_writes_allowed():
        return {
            "status": "refused",
            "error": "Write operations are disabled on this MCP server (MCP_ALLOW_WRITES=0).",
        }

    res = await _api_request(
        "DELETE",
        f"/v1/viewer/memories/{memory_id}",
        params={"reason": reason, "purge_text": str(purge_text).lower()},
    )
    return res


@mcp.tool()
async def correct(
    memory_id: str,
    new_statement: str,
    base_version: int,
    reason: str,
) -> dict[str, Any]:
    """
    Corrects a memory through its lineage, creating version v(n+1) with a SUPERSEDES edge.
    Enforces optimistic locking via base_version. Refused when MCP_ALLOW_WRITES=0.
    """
    if not is_writes_allowed():
        return {
            "status": "refused",
            "error": "Write operations are disabled on this MCP server (MCP_ALLOW_WRITES=0).",
        }

    res = await _api_request(
        "PATCH",
        f"/v1/viewer/memories/{memory_id}",
        json_body={
            "statement": new_statement,
            "base_version": base_version,
            "reason": reason,
        },
    )
    return res


@mcp.tool()
async def timeline(
    entity: Optional[str] = None,
    lineage_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict[str, Any]:
    """
    Returns the bi-temporal versioned timeline for an entity, lineage, or date window.
    """
    params: dict[str, Any] = {}
    if entity:
        params["entity"] = entity
    if lineage_id:
        params["lineage_id"] = lineage_id
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    res = await _api_request("GET", "/v1/timeline", params=params)
    return res


@mcp.tool()
async def directives() -> dict[str, Any]:
    """
    Lists active procedural directives (standing user preferences, constraints, and style rules).
    """
    res = await _api_request("GET", "/v1/directives")
    return res


@mcp.resource("lumen://directives")
async def resource_directives() -> str:
    """Read-only resource returning active procedural directives as JSON."""
    res = await _api_request("GET", "/v1/directives")
    return json.dumps(res, indent=2)


@mcp.resource("lumen://memory/{memory_id}")
async def resource_memory(memory_id: str) -> str:
    """Read-only resource returning a specific memory card with citations."""
    res = await _api_request("GET", f"/v1/viewer/memories/{memory_id}")
    if res.get("sensitivity") == "secret":
        return json.dumps({"error": "Secret memories are not accessible via MCP."})
    if res.get("sensitivity") == "private" and not is_private_included():
        return json.dumps({"error": "Private memories are excluded unless MCP_INCLUDE_PRIVATE=1."})
    return json.dumps(res, indent=2)


@mcp.resource("lumen://entity/{name}")
async def resource_entity(name: str) -> str:
    """Read-only resource returning an entity's knowledge graph neighborhood and timeline."""
    graph_res = await _api_request("GET", "/v1/graph", params={"entity": name, "depth": 1})
    timeline_res = await _api_request("GET", "/v1/timeline", params={"entity": name, "limit": 20})
    return json.dumps({"entity": name, "graph": graph_res, "timeline": timeline_res}, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumen Memory Engine MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run over stdio transport (default)")
    parser.add_argument("--http", action="store_true", help="Run over streamable HTTP transport")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port")
    args = parser.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
