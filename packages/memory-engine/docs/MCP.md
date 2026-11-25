# Lumen Memory Engine — Model Context Protocol (MCP) Server

Connect **Cursor**, **Claude Code**, or any MCP-compatible AI agent to your local Lumen Memory Engine so all tools share one verifiable, bi-temporal, cited long-term memory.

## Quick Setup

### 1. Claude Code
```bash
claude mcp add lumen --env LUMEN_MEMORY_TOKEN="your_token" -- lumen-memory-mcp --stdio
```

To allow Claude Code to write new memories (`remember`), forget, or correct:
```bash
claude mcp add lumen \
  --env LUMEN_MEMORY_TOKEN="your_token" \
  --env MCP_ALLOW_WRITES=1 \
  -- lumen-memory-mcp --stdio
```

### 2. Cursor (`.cursor/mcp.json`)
Add this to your global `~/.cursor/mcp.json` or workspace `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "lumen": {
      "command": "lumen-memory-mcp",
      "args": ["--stdio"],
      "env": {
        "LUMEN_ENGINE_URL": "http://127.0.0.1:8000",
        "LUMEN_MEMORY_TOKEN": "your_token",
        "MCP_ALLOW_WRITES": "0",
        "MCP_INCLUDE_PRIVATE": "0"
      }
    }
  }
}
```

### 3. Streamable HTTP Mode
```bash
LUMEN_MEMORY_TOKEN="your_token" lumen-memory-mcp --http --host 127.0.0.1 --port 8765
```

## Available Tools & Resources

| Tool | Description | Read / Write |
|---|---|---|
| `recall` | 5-channel hybrid recall with exact quotes, dates & abstention | Read |
| `context` | Full structured ContextPack with active directives & citations | Read |
| `timeline` | Bi-temporal version history for an entity or lineage | Read |
| `directives` | List active procedural rules & preferences | Read |
| `remember` | Capture observation to append-only log & extract | Write (`MCP_ALLOW_WRITES=1`) |
| `correct` | Update lineage to `v(n+1)` with optimistic locking | Write (`MCP_ALLOW_WRITES=1`) |
| `forget` | Retract memory with tombstone audit event | Write (`MCP_ALLOW_WRITES=1`) |

- **Resources:** `lumen://directives`, `lumen://memory/{id}`, `lumen://entity/{name}`

## Security & Privacy Defaults
- **Read-Only by Default:** `MCP_ALLOW_WRITES=0` prevents external agents from altering your memory until explicitly enabled.
- **Sensitivity Isolation:** `secret` memories (passwords, API keys, OTPs) are **never** exposed over MCP. `private` memories require `MCP_INCLUDE_PRIVATE=1`.
- **Attribution:** External writes are stamped `actor='mcp:<client>'` and `epistemic='belief'`.
