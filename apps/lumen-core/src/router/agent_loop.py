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
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from src.router.model_router import ModelRouter
from src.tools.models import CommandRiskLevel
from src.persona.jarvis import format_context_with_memories

logger = logging.getLogger("lumen.agent_loop")

READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "grep", "rg", "pwd", "whoami",
    "ps", "top", "uptime", "df", "free", "echo", "date", "env", "find",
    "uname", "which", "history", "stat"
}


def _is_single_read_only(clean: str) -> bool:
    clean = clean.strip()
    if not clean:
        return False
    # Check for shell redirection, subshells, or pipes which can write files or run mutations
    if any(tok in clean for tok in [">", ">>", "<", "|", "`", "$("]):
        return False

    base_cmd = clean.split()[0]
    if base_cmd in [
        "rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "chown",
        "sudo", "kill", "pkill", "dd", "python", "python3", "sh", "bash", "zsh"
    ]:
        return False

    if clean.startswith("git "):
        tokens = clean.split()[1:]
        subcmd = None
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ["-C", "-c"]:
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            subcmd = t
            break
        return subcmd in ["status", "log", "diff", "branch", "show", "rev-parse", "describe", "tag"]

    if clean.startswith("docker "):
        return clean.startswith("docker ps") or clean.startswith("docker images")

    if base_cmd == "find":
        destructive_flags = [
            "-delete", "-exec", "-execdir", "-ok", "-okdir",
            "-fprint", "-fprintf", "-fls"
        ]
        if any(flag in clean for flag in destructive_flags):
            return False

    return base_cmd in READ_ONLY_COMMANDS


def is_read_only_command(command: str) -> bool:
    """Evaluates whether a shell command is strictly read-only or presents mutation risk."""
    clean = command.strip()
    if not clean:
        return False
    # Disallow pipes or file redirections immediately
    if any(tok in clean for tok in [">", ">>", "<", "|", "||", "`", "$("]):
        return False

    # Split compound commands on && or ;
    parts = re.split(r"\s*(?:&&|;)\s*", clean)
    if not parts or any(not p.strip() for p in parts):
        return False

    return all(_is_single_read_only(p) for p in parts)


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyst_engine",
            "description": (
                "PRIMARY tool for quantitative data analysis, financial metrics, and generating publication-quality dark-mode charts "
                "using Pandas and Matplotlib in a persistent kernel. Invoke whenever the user asks to plot, graph, chart, visualize, "
                "or analyze numbers, statistics, revenue trajectories, or visual trends — even if they say 'research and graph' or 'find data and plot', "
                "because analyst_engine performs live web research automatically. Can directly analyze, compare, and chart local files "
                "referenced in query (e.g. 'compare sales_2023.csv against sales_2024.csv', 'plot expenditure from invoice.pdf'). "
                "Do NOT call deep_research when a graph, plot, or chart is requested. "
                "DO NOT invoke for dramatic, literary, narrative, or conspiracy plots (e.g. 'plot of Hamlet', 'plot a novel or narrative arc', 'plot to overthrow the king', 'political plot') — "
                "only invoke for numeric, mathematical, or empirical data analysis."
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
            "name": "web_search",
            "description": (
                "PRIMARY and DEFAULT tool for all internet search queries, web lookups, checking websites, live facts, current events, "
                "sports scores, news, weather, or quick information retrieval. Completely free, open-source, and fast (<2 seconds). "
                "Invoke this tool whenever the user asks to check online, search the web, check an internet site, or look up any fact. "
                "Use this tool freely as much as needed for all light and standard search requests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The specific search query to look up on the web (e.g. 'India vs New Zealand test match day 4 score').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of search results to retrieve (default: 5).",
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
                "STRICT RESTRICTION: DO NOT INVOKE this tool UNLESS the user explicitly and verbatim says 'deep research', 'create a dossier', or 'in-depth investigation'. "
                "For ALL other searches, web lookups, internet queries, checking websites, sports scores, news, or general questions, ALWAYS invoke 'web_search'. "
                "This tool runs an exhaustive multi-source web investigation across DuckDuckGo, Reddit, Hacker News, and arXiv, "
                "extracts quotes/citations, and compiles a permanent archival intelligence dossier in Obsidian (vault/research/). "
                "NEVER call this tool automatically for simple, general, or conversational research."
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
                "Commands execute directly in the active project workspace root. "
                "Always run repository and system commands directly in the current directory (e.g. 'git log -n 5 --oneline', 'git status', 'ls -la') without extra path flags. "
                "DO NOT use for plotting, charting, or data analysis (use analyst_engine instead)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to evaluate and execute directly in the workspace.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "morning_briefing",
            "description": (
                "Generates an autonomous morning executive intelligence briefing including real-time weather and forecast, "
                "Sentinel host telemetry (CPU, RAM, Disk, Docker), top tech/world news headlines, pending memory review proposals, "
                "and active tasks. Automatically writes a permanent note to vault/digests/briefing_YYYY-MM-DD.md and returns a spoken summary. "
                "Use when the user greets with 'Good morning', asks for their morning briefing, daily overview, or executive summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Optional city name for weather forecast. Defaults to auto-detected location.",
                    },
                },
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
    {
        "type": "function",
        "function": {
            "name": "document_vault",
            "description": (
                "Ingests, scans, and parses local documents and files (.pdf, .csv, .xlsx, .md, .txt, images). "
                "Use 'ingest' action to index files or entire folders into persistent memory (pgvector + Knowledge Graph) "
                "and archive dossiers into Obsidian vault (vault/documents/). "
                "Use 'inspect' action to preview tabular schemas, columns, and content. "
                "For plotting or generating charts from local files, invoke analyst_engine instead (which automatically uses document_vault)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path to ingest or inspect (e.g. '~/Documents/research', 'sales_2023.csv', '~/Downloads/invoice.pdf').",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["ingest", "inspect"],
                        "description": "Whether to ingest into long-term memory ('ingest') or inspect schemas/text ('inspect'). Default is 'inspect' for single files, 'ingest' for folders.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_commons",
            "description": (
                "Authoritative empirical data grounding via Google Data Commons Knowledge Graph. "
                "Retrieves verified, official time-series statistics and datasets from UN, World Bank, "
                "US Census Bureau, CDC, Eurostat, NOAA, and IPCC. "
                "Use when the user asks for official statistics, historical indicators, or data tables "
                "for economic, demographic, health, climate, or energy metrics (e.g. 'population of California', "
                "'carbon emissions per capita in Germany', 'US unemployment rate history'). "
                "Exports clean CSV datasets directly to vault/scratch/ for downstream analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing the statistical metrics and places to look up.",
                    },
                    "places": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of country, state, or city names (e.g. ['United States', 'Germany']).",
                    },
                    "variables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of StatisticalVariable DCIDs or indicator keywords (e.g. ['Amount_Emissions_CarbonDioxide_PerCapita']).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_sentinel",
            "description": (
                "Performs a comprehensive system health and infrastructure inspection. "
                "Monitors physical host hardware (CPU, RAM, Disk usage), Docker containers (Postgres, Redis, active/crashed), "
                "and Git workspace cleanliness. Invoke whenever the user asks for a system health check, status check, container inspection, "
                "or sentinel diagnostic report."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
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
        executed_tool_calls: set[str] = set()
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

                call_sig = f"{name}:{json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args)}"
                if call_sig in executed_tool_calls:
                    yield f"⚠️ Tool `{name}` was already executed with identical parameters in a prior step. Proceeding to final synthesis.\n"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": "Notice: Duplicate call. Findings already available above. Do not repeat; synthesize your final response.",
                    })
                    continue
                executed_tool_calls.add(call_sig)

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
                    user_lower = user_input.lower()
                    explicit_deep_terms = ("deep research", "dossier", "deep investigation", "in-depth research", "in-depth investigation")
                    if not any(term in user_lower for term in explicit_deep_terms):
                        # User directive: don't use deep research until user explicitly says so!
                        yield f"🌐 Performing fast open-source web search for: '{topic}'...\n"
                        from ..research.providers.duckduckgo import DuckDuckGoProvider
                        provider = DuckDuckGoProvider()
                        results = await provider.search(query=topic, max_results=5)
                        if results:
                            formatted = [f"{idx}. {r.title}: {r.snippet} (Source: {r.url})" for idx, r in enumerate(results, 1)]
                            tool_result_str = "\n".join(formatted)
                        else:
                            tool_result_str = f"No web search results found for query: '{topic}'"
                    else:
                        yield f"👁️ Conducting deep web research on: '{topic}'...\n"
                        dossier = await self.session.research.run_research(topic)
                        path = self.session.vault.export_research_dossier(dossier)
                        yield f"💾 Research dossier compiled at: [`{path.name}`](file://{path.resolve()})\n"
                        findings_list = [c.claim for c in dossier.claims[:5]] if dossier.claims else dossier.key_findings
                        top_findings = "\n".join([f"• {f}" for f in findings_list])
                        tool_result_str = f"Research completed. Key findings:\n{top_findings}\nDossier saved at {path}"

                # Route B2: Fast Web Search
                elif name == "web_search":
                    search_query = args.get("query", user_input)
                    max_res = min(int(args.get("max_results", 5)), 8)
                    yield f"🌐 Searching the web for: '{search_query}'...\n"
                    from ..research.providers.duckduckgo import DuckDuckGoProvider
                    provider = DuckDuckGoProvider()
                    results = await provider.search(query=search_query, max_results=max_res)
                    if results:
                        formatted = []
                        for idx, r in enumerate(results, 1):
                            formatted.append(f"{idx}. {r.title}: {r.snippet} (Source: {r.url})")
                        tool_result_str = "\n".join(formatted)
                    else:
                        tool_result_str = f"No web search results found for query: '{search_query}'"

                # Route B3: Autonomous Morning Executive Briefing
                elif name == "morning_briefing":
                    city = args.get("city")
                    yield f"🌅 Compiling your Morning Executive Intelligence Briefing...\n\n"
                    report = await self.session.briefing.generate_briefing(city=city)
                    path = Path(report.vault_path)
                    yield f"💾 Intelligence briefing archived at: [`{path.name}`](file://{path.resolve()})\n\n"
                    tool_result_str = (
                        f"Morning Briefing generated successfully.\n\n"
                        f"Spoken summary to read to the user:\n{report.spoken_summary}\n\n"
                        f"Full Obsidian Markdown dossier saved to: {report.vault_path}"
                    )

                # Route C: Computer Control (with Two-Man Rule gate)
                elif name == "computer_control":
                    command = args.get("command", "").strip()
                    # Auto-sanitize git commands with non-existent or placeholder -C paths
                    if "git " in command and "-C " in command:
                        m = re.search(r"git\s+-C\s+(\S+)\s*", command)
                        if m:
                            path_str = m.group(1).strip("\"'")
                            expanded = Path(os.path.expanduser(path_str))
                            if not expanded.exists() or "your_repo" in path_str or "<" in path_str:
                                command = re.sub(r"git\s+-C\s+\S+\s*", "git ", command).strip()

                    cmd_lower = command.lower()
                    if not command:
                        tool_result_str = "Error: empty command."
                    elif cmd_lower.startswith("approve ") or cmd_lower.startswith("/approve "):
                        appr_id = command.split()[-1].strip().lstrip("/")
                        if appr_id in self.session.approvals._pending:
                            req = self.session.approvals._pending[appr_id]
                            self.session.approvals.approve(appr_id)
                            yield f"🔓 Authorization granted for `{req.command}`. Executing in sandbox...\n"
                            res = await self.session.runner.execute(req.command, bypass_gatekeeper=True, timeout=30.0)
                            status_tag = "SUCCESS" if res.is_success else f"FAILED (Exit {res.exit_code})"
                            tool_result_str = f"Command {appr_id} approved.\n[{status_tag} in {res.duration_ms}ms]\n{res.stdout or res.stderr or '(no output)'}"
                            yield tool_result_str + "\n"
                        else:
                            tool_result_str = f"No pending approval found for ID: {appr_id}"
                            yield f"⚠️ {tool_result_str}\n"
                    elif cmd_lower.startswith("reject ") or cmd_lower.startswith("/reject "):
                        appr_id = command.split()[-1].strip().lstrip("/")
                        if self.session.approvals.reject(appr_id):
                            tool_result_str = f"Request {appr_id} rejected and cancelled by operator."
                            yield f"❌ {tool_result_str}\n"
                        else:
                            tool_result_str = f"No pending approval found for ID: {appr_id}"
                            yield f"⚠️ {tool_result_str}\n"
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
                            f"👉 Say or type \"Yes, proceed\" or \"Approve\" to execute\n"
                            f"👉 Or say \"Cancel\" / \"No\" to abort (CLI: `/approve {req.approval_id}`)\n"
                        )
                        return

                # Route D: Memory Lookup
                elif name == "memory_lookup":
                    query = args.get("query", user_input)
                    yield f"🧠 Querying bi-temporal memory for: '{query}'...\n"
                    mems = await self.session.memory.search(query=query, limit=5, trace_id=trace_id)
                    found = [f"• {m.get('entity')} {m.get('attribute')}: {m.get('value')}" for m in mems]
                    tool_result_str = "Retrieved memories:\n" + ("\n".join(found) if found else "No matching memory records.")

                # Route E: Document Vault
                elif name == "document_vault":
                    path = args.get("path", user_input).strip()
                    action = args.get("action", "inspect")
                    tokens = []
                    async for t in self.session.document_vault.process(path=path, action=action):
                        yield t
                        tokens.append(t)
                    tool_result_str = "".join(tokens)

                # Route F: Google Data Commons
                elif name == "data_commons":
                    query = args.get("query", user_input)
                    places = args.get("places")
                    variables = args.get("variables")
                    yield f"🏛️ Querying Google Data Commons for: '{query}'...\n"
                    res = self.session.data_commons.ground_query(
                        query=query,
                        places=places,
                        variables=variables,
                    )
                    if res.get("success"):
                        csv_file = res.get("csv_filename")
                        rows = res.get("rows", 0)
                        provs = res.get("provenances", [])
                        yield f"✅ Verified Data Commons dataset retrieved ({rows} rows, saved to `{csv_file}`).\n"
                        if provs:
                            yield f"• Provenance: {', '.join(provs[:2])}\n"
                        tool_result_str = (
                            f"Data Commons grounding successful.\n"
                            f"CSV file saved: {res.get('csv_path')}\n"
                            f"Entities resolved: {res.get('entities')}\n"
                            f"Variables: {res.get('variables')}\n"
                            f"Records: {rows}\n"
                            f"Sample Data:\n{res.get('summary_md')}\n"
                            f"Provenances: {provs}"
                        )
                    else:
                        tool_result_str = f"Data Commons query could not resolve records: {res.get('reason')}"
                        yield f"⚠️ {tool_result_str}\n"

                # Route G: Sentinel System Health
                elif name == "system_sentinel":
                    yield "🛡️ Running Sentinel system and container diagnostic scan...\n"
                    await self.session.sentinel.run_patrol()
                    tool_result_str = self.session.sentinel.get_summary_text()
                    yield f"{tool_result_str}\n"

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
