"""
Autonomous Agent Loop for Lumen (Project JARVIS).
Executes a multi-turn ReAct loop (Tool Call -> Feedback -> Decide Again -> Max 8 Steps)
with:
- Context Window: Retains last ~15 conversational turns (30 messages)
- Direct Argument Resolution: LLM supplies fully resolved entity and parameters
- Two-Man Rule Security Gate: Intercepts non-read-only shell commands for human confirmation
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from src.router.model_router import ModelRouter
from src.tools.models import CommandRiskLevel
from src.persona.jarvis import format_context_with_memories

logger = logging.getLogger("lumen.agent_loop")

READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "grep", "rg", "pwd", "whoami",
    "ps", "top", "uptime", "df", "free", "echo", "date", "env", "find"
}


def is_read_only_command(command: str) -> bool:
    """Evaluates whether a shell command is strictly read-only or presents mutation risk."""
    clean = command.strip()
    if not clean:
        return False
    # Check for shell redirection or pipes which can write files or run mutations
    if any(tok in clean for tok in [">", ">>", "|", ";", "&&", "||"]):
        return False

    base_cmd = clean.split()[0]
    if base_cmd in [
        "rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "chown",
        "sudo", "kill", "pkill", "dd", "python", "python3", "sh", "bash", "zsh"
    ]:
        return False

    if clean.startswith("git "):
        return any(clean.startswith(f"git {ro}") for ro in ["status", "log", "diff", "branch", "show"])

    if clean.startswith("docker "):
        return clean.startswith("docker ps") or clean.startswith("docker images")

    return base_cmd in READ_ONLY_COMMANDS


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyst_engine",
            "description": (
                "PRIMARY tool for quantitative data analysis, statistical computation (CAGR, growth, totals), "
                "financial metrics, and generating publication-quality dark-mode charts using Pandas and Matplotlib in a persistent kernel. "
                "Invoke whenever the user asks to plot, graph, chart, visualize, or analyze numbers, revenue trajectories, "
                "stock comparisons, or visual data trends. Do NOT use computer_control for plotting or data analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The fully-resolved analysis objective. Incorporate full entity names and metrics from conversational "
                            "history (e.g., if user says 'now for rdr 2', resolve to 'Red Dead Redemption 2 cumulative revenue trajectory')."
                        ),
                    },
                    "preview": {
                        "type": "boolean",
                        "description": "Whether to automatically open the chart in macOS Preview (default: true).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": (
                "Conducts an in-depth autonomous multi-source web investigation across DuckDuckGo, Hacker News, Reddit, and arXiv. "
                "Extracts quotes and citations, and compiles an Obsidian intelligence dossier. Invoke for investigative queries, "
                "complex non-visual research topics, or background fact-finding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The detailed, fully-resolved research question or topic.",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "computer_control",
            "description": (
                "Executes operating system shell commands (e.g. file management, checking processes, git commands, system diagnostics). "
                "Read-only commands execute directly; non-read-only mutating commands require human confirmation (Two-Man Rule). "
                "DO NOT use for plotting, charting, or data analysis (use analyst_engine instead)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to evaluate and execute.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_lookup",
            "description": (
                "Queries Lumen's bi-temporal memory engine (pgvector + Knowledge Graph) for past user decisions, "
                "recorded preferences, project architectures, or entity relationships."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The semantic concept or question to retrieve from long-term memory.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class LumenAgentLoop:
    """
    Orchestrates the autonomous ReAct agent loop for Lumen.
    Evaluates intent, calls tools, feeds back outputs, and loops (up to 8 steps)
    before returning a final, grounded conversational response.
    """

    def __init__(
        self,
        session: Any,
        router: Optional[ModelRouter] = None,
        max_steps: int = 8,
        history_turn_limit: int = 15,
    ):
        self.session = session
        self.router = router or session.router
        self.max_steps = max_steps
        self.history_turn_limit = history_turn_limit

    async def run(
        self,
        user_input: str,
        retrieved_memories: List[Dict[str, Any]],
        trace_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Runs the multi-step agent loop until completion or max steps reached.
        """
        # Assemble context: System prompt + memories + last ~15 turns (30 messages)
        recent_history = self.session.history[-(self.history_turn_limit * 2) :]
        messages = format_context_with_memories(
            memories=retrieved_memories,
            conversation_history=recent_history,
        )
        # Append current user prompt
        messages.append({"role": "user", "content": user_input})

        step = 0
        while step < self.max_steps:
            step += 1

            # 1. Ask model for next action (Tool Call or Final Dialogue)
            try:
                response = await self.router.create_completion(
                    messages=messages,
                    tools=AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                )
            except Exception as e:
                logger.error(f"AgentLoop Model Completion Error: {e}")
                # Fallback to direct stream
                async for token in self.router.stream_completion(messages):
                    yield token
                return

            tool_calls = response.get("tool_calls", [])
            assistant_content = response.get("content", "")

            # If no tools called, we have our final text response!
            if not tool_calls:
                if assistant_content:
                    yield assistant_content
                else:
                    # Fallback streaming if empty content
                    async for token in self.router.stream_completion(messages):
                        yield token
                return

            # Append assistant message with its tool calls into working history
            messages.append({
                "role": "assistant",
                "content": assistant_content or "",
                "tool_calls": tool_calls,
            })

            # 2. Execute each requested tool call
            for tc in tool_calls:
                call_id = tc.get("id", f"call_{step}")
                fn = tc.get("function", {})
                name = fn.get("name")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {"query": str(raw_args)}

                yield f"\n⚡ [Autonomous Agent Step {step}/{self.max_steps}] Invoking `{name}`...\n"

                tool_result_str = ""
                # Route A: Analyst Engine
                if name == "analyst_engine":
                    query = args.get("query", user_input)
                    preview = args.get("preview", True)
                    tokens = []
                    async for t in self.session.analyst.analyze_and_plot(query, preview=preview):
                        yield t
                        tokens.append(t)
                    tool_result_str = "".join(tokens)

                # Route B: Deep Research
                elif name == "deep_research":
                    topic = args.get("topic", user_input)
                    yield f"👁️ Conducting deep web research on: '{topic}'...\n"
                    dossier = await self.session.research.run_research(topic)
                    path = self.session.vault.export_research_dossier(dossier)
                    yield f"💾 Research dossier compiled at: [`{path.name}`](file://{path.resolve()})\n"
                    top_findings = "\n".join([f"• {c.claim}" for c in dossier.claims[:5]])
                    tool_result_str = f"Research completed. Top findings:\n{top_findings}\nDossier saved at {path}"

                # Route C: Computer Control (with Two-Man Rule gate)
                elif name == "computer_control":
                    command = args.get("command", "").strip()
                    if not command:
                        tool_result_str = "Error: empty command."
                    elif is_read_only_command(command):
                        yield f"💻 Executing read-only command: `{command}`\n"
                        res = await self.session.runner.execute(command)
                        tool_result_str = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}\nEXIT_CODE: {res.exit_code}"
                        if res.stdout:
                            yield f"```text\n{res.stdout.strip()}\n```\n"
                    else:
                        # Non-read-only -> Require operator confirmation!
                        req = self.session.approvals.request_approval(
                            command=command,
                            reason=f"Agent loop requested mutating shell command: '{command}'",
                        )
                        yield (
                            f"\n🛡️ [Two-Man Rule Authorization Required]\n"
                            f"• Command: `{command}`\n"
                            f"• Risk Level: HIGH (Mutating system operation)\n"
                            f"• Approval ID: `{req.approval_id}`\n"
                            f"👉 To authorize, type: `/approve {req.approval_id}`\n"
                            f"👉 To cancel, type: `/reject {req.approval_id}`\n"
                        )
                        tool_result_str = f"Command gated awaiting operator approval token ID: {req.approval_id}. Awaiting /approve {req.approval_id}."

                # Route D: Memory Lookup
                elif name == "memory_lookup":
                    query = args.get("query", user_input)
                    yield f"🧠 Querying bi-temporal memory for: '{query}'...\n"
                    mems = await self.session.memory.search(query=query, limit=5, trace_id=trace_id)
                    found = [f"• {m.get('entity')} {m.get('attribute')}: {m.get('value')}" for m in mems]
                    tool_result_str = "Retrieved memories:\n" + ("\n".join(found) if found else "No matching memory records.")

                else:
                    tool_result_str = f"Unknown tool: {name}"

                # Feed the tool result back into the agent conversation history
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": tool_result_str[:4000],  # Bound payload to avoid context explosion
                })

        # If loop reached max steps without natural exit, request final wrap-up
        yield f"\n[Maximum agent steps ({self.max_steps}) reached. Synthesizing final briefing...]\n\n"
        async for token in self.router.stream_completion(messages):
            yield token
