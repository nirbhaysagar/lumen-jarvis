"""
Automated 30+ Evaluation Suite for Lumen Autonomous Agent Dispatcher & ReAct Loop.
Tests:
- Typos and phonetically garbled user requests
- Conversational follow-ups and anaphora resolution
- Compound multi-intent requests
- Casual chit-chat (zero false tool dispatches)
- Ambiguous homonyms ("plot" in dramatic/literary contexts vs charting)
- Two-Man Rule security gating for computer control commands
- Max-step ceiling enforcement (8 steps) and 15-turn context retention
"""
from __future__ import annotations
import asyncio
import json
import pytest
from typing import Any, AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock

from src.router.agent_loop import (
    AGENT_TOOLS,
    LumenAgentLoop,
    is_read_only_command,
)
from src.router.model_router import ModelRouter
from src.tools.approval import ApprovalManager
from src.tools.models import ExecutionResult
from src.tools.runner import SandboxedRunner


# ==============================================================================
# Suite 1: Command Safety AST & Two-Man Rule Gating (14 cases)
# ==============================================================================

@pytest.mark.parametrize(
    "command,expected_read_only",
    [
        ("ls -la /tmp", True),
        ("pwd", True),
        ("cat README.md", True),
        ("head -n 20 main.py", True),
        ("git status", True),
        ("git log -n 5 --oneline", True),
        ("git diff HEAD~1", True),
        ("docker ps", True),
        ("docker images", True),
        ("uptime", True),
        ("rm -rf /tmp/test_dir", False),
        ("mv old_file.txt new_file.txt", False),
        ("chmod +x script.sh", False),
        ("docker run -d redis", False),
        ("git commit -m 'feat: test'", False),
        ("git push origin main", False),
        ("cat file.txt | grep secret", False),  # Pipes present execution risks
        ("echo 'malicious' > /etc/resolv.conf", False),  # Output redirection writes files
        ("sudo whoami", False),
        ("python3 -c 'import os; os.system(\"rm -rf /\")'", False),
    ],
)
def test_read_only_command_evals(command: str, expected_read_only: bool):
    """Evaluates strict AST classification of safe read-only vs mutating commands."""
    assert is_read_only_command(command) == expected_read_only


# ==============================================================================
# Suite 2: Ambiguous Homonyms ("plot" in non-charting contexts) (6 cases)
# ==============================================================================

HOMONYM_CASES = [
    "explain the plot of Hamlet",
    "the plot thickens in this Agatha Christie novel",
    "what was the gunpowder plot of 1605 in English history?",
    "summarize the movie plot of Inception",
    "help me plot the narrative arc for a fantasy book",
    "historical accounts say they uncovered a royal plot to overthrow the king",
]

@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", HOMONYM_CASES)
async def test_ambiguous_plot_homonyms_eval(prompt: str):
    """
    Evaluates that dramatic, literary, or historical 'plot' queries
    NEVER mistakenly invoke analyst_engine for charting.
    """
    router = ModelRouter()
    await asyncio.sleep(0.6)
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            {"role": "user", "content": prompt},
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    called_tools = [tc["function"]["name"] for tc in res.get("tool_calls", [])]
    assert "analyst_engine" not in called_tools, (
        f"False positive charting dispatch for homonym prompt: '{prompt}'. Called: {called_tools}"
    )


# ==============================================================================
# Suite 3: Plain Chat & Chit-Chat (No Tool False Positives) (6 cases)
# ==============================================================================

PLAIN_CHAT_CASES = [
    "hello Jarvis, how are you operating today?",
    "what is the official capital city of France?",
    "tell me a quick joke about quantum entanglement",
    "thank you kindly for your assistance, that will be all for now",
    "what is the theoretical speed of light in vacuum?",
    "good evening Jarvis, what a pleasant day",
]

@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", PLAIN_CHAT_CASES)
async def test_plain_chat_zero_tool_eval(prompt: str):
    """
    Evaluates that conversational chit-chat, greetings, and general trivia
    do NOT trigger unnecessary external tool calls.
    """
    router = ModelRouter()
    await asyncio.sleep(0.6)
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            {"role": "user", "content": prompt},
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    called_tools = [tc["function"]["name"] for tc in res.get("tool_calls", [])]
    # In plain chat, heavy execution tools must never be invoked
    assert not any(tool in ["computer_control", "deep_research", "analyst_engine"] for tool in called_tools), (
        f"Heavy execution tool invoked for plain chat '{prompt}': {called_tools}"
    )
    assert len(called_tools) == 0, f"Expected no tools for chat '{prompt}', got: {called_tools}"
    assert len(res.get("content", "")) > 0


# ==============================================================================
# Suite 4: Typo and Slang Tolerance in Intent Resolution (6 cases)
# ==============================================================================

TYPO_CASES = [
    ("genearte grph for gta 5 revneue so far", "analyst_engine", "deep_research"),
    ("analize revnue trajectory of apple stock 2020 to 2024", "analyst_engine", "deep_research"),
    ("reserch and plt Take-Two sales trend", "analyst_engine", "deep_research"),
    ("creat chrt of netflix subscriber growth over past 3 years", "analyst_engine", "deep_research"),
    ("vizualize US gdp grouth rate trajectory", "analyst_engine", None),
    ("shw me a graff of tesla quarterly deliveries", "analyst_engine", "deep_research"),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,primary_expected,secondary_allowed", TYPO_CASES)
async def test_typo_intent_resolution_eval(prompt: str, primary_expected: str, secondary_allowed: str | None):
    """
    Evaluates robust semantic intent routing despite spelling typos, garbled verbs,
    and shorthand terms.
    """
    router = ModelRouter()
    await asyncio.sleep(0.6)
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            {"role": "user", "content": prompt},
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    called_tools = [tc["function"]["name"] for tc in res.get("tool_calls", [])]
    allowed = {primary_expected}
    if secondary_allowed:
        allowed.add(secondary_allowed)

    assert any(tool in allowed for tool in called_tools), (
        f"Prompt with typos '{prompt}' failed to invoke expected tools {allowed}. Called: {called_tools}"
    )


# ==============================================================================
# Suite 5: Conversational Follow-ups & Direct Argument Resolution (4 cases)
# ==============================================================================

@pytest.mark.asyncio
async def test_followup_rdr2_resolution():
    """
    Verifies that 'now for rdr 2' after discussing GTA 6 revenue
    resolves to Red Dead Redemption 2 cumulative revenue without a regex rewrite step.
    """
    router = ModelRouter()
    await asyncio.sleep(0.6)
    history = [
        {"role": "user", "content": "research and generate graph for gta 6 revenue so far"},
        {"role": "assistant", "content": "Because GTA VI has not yet shipped, there is no official revenue recorded to date."},
        {"role": "user", "content": "now for rdr 2"},
    ]
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            *history,
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    tool_calls = res.get("tool_calls", [])
    assert len(tool_calls) > 0, "Expected tool call for follow-up 'now for rdr 2'"
    fn = tool_calls[0]["function"]
    assert fn["name"] in ["analyst_engine", "deep_research"]
    args = json.loads(fn.get("arguments", "{}"))
    query = (args.get("query") or args.get("topic") or "").lower()
    assert ("red dead" in query or "rdr" in query) and ("revenue" in query or "sales" in query or "trajectory" in query)


@pytest.mark.asyncio
async def test_followup_platform_breakdown_resolution():
    """Verifies follow-up 'break it down by platform' resolves prior entity context."""
    router = ModelRouter()
    await asyncio.sleep(0.6)
    history = [
        {"role": "user", "content": "plot Take-Two annual revenue trajectory"},
        {"role": "assistant", "content": "Take-Two Interactive reported fiscal year revenue expanding from $3.3B to $5.3B."},
        {"role": "user", "content": "break it down by console versus PC and mobile"},
    ]
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            *history,
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    tool_calls = res.get("tool_calls", [])
    assert len(tool_calls) > 0
    fn = tool_calls[0]["function"]
    assert fn["name"] in ["analyst_engine", "deep_research"]
    args = json.loads(fn.get("arguments", "{}"))
    query = (args.get("query") or args.get("topic") or "").lower()
    assert ("take-two" in query or "take two" in query or "platform" in query or "console" in query or "breakdown" in query)


@pytest.mark.asyncio
async def test_followup_stock_comparison_resolution():
    """Verifies follow-up 'what about Microsoft' resolves to comparison against prior Apple context."""
    router = ModelRouter()
    await asyncio.sleep(0.6)
    history = [
        {"role": "user", "content": "plot Apple stock price trend for 2024"},
        {"role": "assistant", "content": "Apple (AAPL) rose 28% throughout 2024 to close near $250."},
        {"role": "user", "content": "what about Microsoft"},
    ]
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            *history,
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    tool_calls = res.get("tool_calls", [])
    assert len(tool_calls) > 0
    fn = tool_calls[0]["function"]
    assert fn["name"] == "analyst_engine"
    args = json.loads(fn.get("arguments", "{}"))
    query = args.get("query", "").lower()
    assert "microsoft" in query or "msft" in query


@pytest.mark.asyncio
async def test_followup_shell_context_resolution():
    """Verifies follow-up command switching from docker to git maintains tool category."""
    router = ModelRouter()
    await asyncio.sleep(0.6)
    history = [
        {"role": "user", "content": "check docker ps"},
        {"role": "assistant", "content": "Active containers: lumen-postgres, lumen-redis."},
        {"role": "user", "content": "now check git status"},
    ]
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            *history,
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    tool_calls = res.get("tool_calls", [])
    assert len(tool_calls) > 0
    fn = tool_calls[0]["function"]
    assert fn["name"] == "computer_control"
    args = json.loads(fn.get("arguments", "{}"))
    assert "git status" in args.get("command", "")


# ==============================================================================
# Suite 6: Compound Multi-Intent & Deep Queries (4 cases)
# ==============================================================================

COMPOUND_CASES = [
    ("research Take-Two earnings and plot the revenue trajectory", ["analyst_engine", "deep_research"]),
    ("check git status and tell me if anything is modified", ["computer_control"]),
    ("query long-term memory for my preferred database and explain why", ["memory_lookup"]),
    ("investigate quantum computing benchmarks and summarize findings", ["deep_research"]),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,expected_tools", COMPOUND_CASES)
async def test_compound_multi_intent_eval(prompt: str, expected_tools: list[str]):
    """Evaluates multi-intent compound dispatches."""
    router = ModelRouter()
    await asyncio.sleep(0.6)
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            {"role": "user", "content": prompt},
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    called_tools = [tc["function"]["name"] for tc in res.get("tool_calls", [])]
    assert any(tool in called_tools for tool in expected_tools), (
        f"Compound query '{prompt}' did not invoke any of {expected_tools}. Got: {called_tools}"
    )


# ==============================================================================
# Suite 7: LumenAgentLoop End-to-End Execution, Gating, & Ceilings (4 cases)
# ==============================================================================

@pytest.mark.asyncio
async def test_agent_loop_max_step_ceiling():
    """Verifies that the agent loop strictly terminates at max_steps (8)."""
    # Mock a router that perpetually returns a tool call to test ceiling abort
    mock_router = MagicMock(spec=ModelRouter)
    mock_router.create_completion = AsyncMock(
        return_value={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "loop_test",
                    "type": "function",
                    "function": {"name": "memory_lookup", "arguments": '{"query": "loop"}'},
                }
            ],
        }
    )
    async def mock_stream(messages):
        yield "Final wrap-up summary after hitting step limit."

    mock_router.stream_completion = mock_stream

    mock_session = MagicMock()
    mock_session.history = []
    mock_session.memory = MagicMock()
    mock_session.memory.search = AsyncMock(return_value=[])

    loop = LumenAgentLoop(session=mock_session, router=mock_router, max_steps=8, history_turn_limit=15)
    tokens = []
    async for t in loop.run("loop test", [], "tr_test"):
        tokens.append(t)

    full = "".join(tokens)
    assert "Maximum agent steps (8) reached" in full or "Final wrap-up" in full
    assert mock_router.create_completion.call_count == 8


@pytest.mark.asyncio
async def test_agent_loop_two_man_rule_mutating_gate():
    """Verifies that a mutating shell command in agent loop is intercepted and yields approval token."""
    mock_session = MagicMock()
    mock_session.history = []
    mock_session.approvals = ApprovalManager()
    mock_session.runner = SandboxedRunner()

    mock_router = MagicMock(spec=ModelRouter)
    # Step 1: Request mutating command
    # Step 2: Final response acknowledging approval gate
    mock_router.create_completion = AsyncMock(
        side_effect=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_rm",
                        "type": "function",
                        "function": {"name": "computer_control", "arguments": '{"command": "rm -rf /tmp/test"}'},
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "Awaiting your confirmation to proceed, Sir.",
                "tool_calls": [],
            },
        ]
    )

    loop = LumenAgentLoop(session=mock_session, router=mock_router, max_steps=8, history_turn_limit=15)
    tokens = []
    async for t in loop.run("clean /tmp/test", [], "tr_test"):
        tokens.append(t)

    output = "".join(tokens)
    assert "Two-Man Rule Authorization Required" in output
    assert "/approve appr_" in output
    assert len(mock_session.approvals.list_pending()) == 1


@pytest.mark.asyncio
async def test_agent_loop_read_only_auto_exec():
    """Verifies that a read-only shell command executes immediately without pausing for approval."""
    mock_session = MagicMock()
    mock_session.history = []
    mock_session.approvals = ApprovalManager()
    mock_runner = MagicMock()
    mock_runner.execute = AsyncMock(
        return_value=ExecutionResult(command="ls -la", stdout="file1.txt\nfile2.txt", stderr="", exit_code=0, duration_ms=10)
    )
    mock_session.runner = mock_runner

    mock_router = MagicMock(spec=ModelRouter)
    mock_router.create_completion = AsyncMock(
        side_effect=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ls",
                        "type": "function",
                        "function": {"name": "computer_control", "arguments": '{"command": "ls -la"}'},
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "The directory contains file1.txt and file2.txt.",
                "tool_calls": [],
            },
        ]
    )

    loop = LumenAgentLoop(session=mock_session, router=mock_router, max_steps=8, history_turn_limit=15)
    tokens = []
    async for t in loop.run("list directory contents", [], "tr_test"):
        tokens.append(t)

    output = "".join(tokens)
    assert "Executing read-only command: `ls -la`" in output
    assert "file1.txt" in output
    assert len(mock_session.approvals.list_pending()) == 0  # Zero pending approvals!


@pytest.mark.asyncio
async def test_agent_loop_context_window_15_turns():
    """Verifies that LumenAgentLoop feeds the last ~15 conversational turns (30 messages) into context."""
    mock_session = MagicMock()
    # Populate history with 40 turns (80 messages)
    mock_session.history = []
    for i in range(40):
        mock_session.history.append({"role": "user", "content": f"User turn {i}"})
        mock_session.history.append({"role": "assistant", "content": f"Assistant reply {i}"})

    captured_messages = []

    async def fake_create_completion(messages, tools, tool_choice, temperature):
        nonlocal captured_messages
        captured_messages = list(messages)
        return {"role": "assistant", "content": "Context checked.", "tool_calls": []}

    mock_router = MagicMock(spec=ModelRouter)
    mock_router.create_completion = fake_create_completion

    loop = LumenAgentLoop(session=mock_session, router=mock_router, max_steps=8, history_turn_limit=15)
    tokens = []
    async for t in loop.run("Current prompt", [], "tr_test"):
        tokens.append(t)

    # 1 system msg + 30 history messages (15 turns * 2) + 1 current user prompt = 32 messages
    assert len(captured_messages) == 32
    # Verify that the oldest retained message is turn 25 (40 - 15 = 25)
    assert captured_messages[1]["content"] == "User turn 25"
    assert captured_messages[-1]["content"] == "Current prompt"


# ==============================================================================
# Suite 8: Document Vault & File-Based Analytics Dispatch (4 cases)
# ==============================================================================

DOC_VAULT_CASES = [
    ("Ingest my notes folder at ~/Documents/research into long-term memory", {"document_vault"}),
    ("Parse and inspect this spreadsheet at sales_data.csv", {"document_vault"}),
    ("Compare the metrics in sales_2023.csv against sales_2024.csv and show YoY variance", {"analyst_engine", "document_vault"}),
    ("Analyze this invoice PDF at ~/Downloads/q3_expenses.pdf and plot expenditure by vendor", {"analyst_engine", "document_vault"}),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,allowed_tools", DOC_VAULT_CASES)
async def test_document_vault_dispatch_eval(prompt: str, allowed_tools: set[str]):
    """
    Evaluates that document ingestion/inspection routes to document_vault,
    and document/file-based plotting routes to analyst_engine or document_vault first.
    """
    router = ModelRouter()
    await asyncio.sleep(0.6)
    res = await router.create_completion(
        messages=[
            {"role": "system", "content": "You are JARVIS, an autonomous AI assistant."},
            {"role": "user", "content": prompt},
        ],
        tools=AGENT_TOOLS,
        tool_choice="auto",
    )
    called_tools = [tc["function"]["name"] for tc in res.get("tool_calls", [])]
    assert any(tool in allowed_tools for tool in called_tools), (
        f"Prompt '{prompt}' failed to invoke any of {allowed_tools}. Called: {called_tools}"
    )
