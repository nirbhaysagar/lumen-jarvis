"""
Integration and Security Tests for Phase 4: Safe Computer Control.
Verifies AST Gatekeeper policy enforcement, Two-Man Rule approval gates,
and Sandboxed Subprocess execution under timeouts and cancellation tokens.
"""
import pytest
from src.tools.models import CommandRiskLevel
from src.tools.gatekeeper import ASTGatekeeper
from src.tools.runner import SandboxedRunner
from src.tools.approval import ApprovalManager
from src.concurrency.models import CancellationToken
from src.runtime.session import ConversationSession


def test_gatekeeper_forbidden_patterns():
    gatekeeper = ASTGatekeeper()

    # Destructive deletes
    assert gatekeeper.evaluate("rm -rf /").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("rm -rf /*").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("rm -rf ~").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("rm -rf *").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("rm -r -f .").risk_level == CommandRiskLevel.FORBIDDEN

    # Fork bombs
    assert gatekeeper.evaluate(":(){ :|:& };:").risk_level == CommandRiskLevel.FORBIDDEN

    # Disk wipes
    assert gatekeeper.evaluate("mkfs.ext4 /dev/sda1").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("dd if=/dev/zero of=/dev/sda").risk_level == CommandRiskLevel.FORBIDDEN

    # Database destruction
    assert gatekeeper.evaluate("DROP DATABASE lumen;").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("DROP TABLE facts;").risk_level == CommandRiskLevel.FORBIDDEN


def test_gatekeeper_safe_commands():
    gatekeeper = ASTGatekeeper()

    eval_status = gatekeeper.evaluate("git status")
    assert eval_status.risk_level == CommandRiskLevel.SAFE
    assert eval_status.is_allowed is True
    assert eval_status.requires_approval is False

    assert gatekeeper.evaluate("ls -la").risk_level == CommandRiskLevel.SAFE
    assert gatekeeper.evaluate("pwd").risk_level == CommandRiskLevel.SAFE
    assert gatekeeper.evaluate("git log -n 5").risk_level == CommandRiskLevel.SAFE
    assert gatekeeper.evaluate("echo 'Hello World'").risk_level == CommandRiskLevel.SAFE


def test_gatekeeper_caution_commands():
    gatekeeper = ASTGatekeeper()

    # Modifying git operations
    eval_commit = gatekeeper.evaluate("git commit -m 'feat'")
    assert eval_commit.risk_level == CommandRiskLevel.CAUTION
    assert eval_commit.requires_approval is True

    # Package managers
    eval_pip = gatekeeper.evaluate("pip install requests")
    assert eval_pip.risk_level == CommandRiskLevel.CAUTION
    assert eval_pip.requires_approval is True

    # Output redirection
    eval_redir = gatekeeper.evaluate("echo 'secret' > config.txt")
    assert eval_redir.risk_level == CommandRiskLevel.CAUTION
    assert eval_redir.requires_approval is True


@pytest.mark.asyncio
async def test_sandboxed_runner_safe_execution():
    runner = SandboxedRunner()
    result = await runner.execute("echo 'LUMEN_SANDBOX_ONLINE'")
    assert result.is_success is True
    assert result.exit_code == 0
    assert "LUMEN_SANDBOX_ONLINE" in result.stdout
    assert result.duration_ms > 0.0


@pytest.mark.asyncio
async def test_sandboxed_runner_forbidden_block():
    runner = SandboxedRunner()
    result = await runner.execute("rm -rf /")
    assert result.is_success is False
    assert result.exit_code == 126
    assert "SECURITY REFUSAL" in result.stderr


@pytest.mark.asyncio
async def test_sandboxed_runner_timeout():
    runner = SandboxedRunner(default_timeout=0.1)
    result = await runner.execute("sleep 2.0")
    assert result.timed_out is True
    assert result.exit_code == 124
    assert "timed out" in result.stderr


@pytest.mark.asyncio
async def test_sandboxed_runner_cancellation():
    runner = SandboxedRunner()
    token = CancellationToken()

    # Cancel immediately
    token.cancel()
    result = await runner.execute("sleep 5.0", cancellation_token=token)
    assert result.cancelled is True
    assert result.exit_code == 130


@pytest.mark.asyncio
async def test_session_exec_and_approval_flow():
    session = ConversationSession()

    # 1. Test Forbidden Command Block in Session
    forbidden_tokens = []
    async for token in session.process_turn("/exec rm -rf /"):
        forbidden_tokens.append(token)
    forbidden_reply = "".join(forbidden_tokens)
    assert "🚫 [SECURITY BLOCK - AST GATEKEEPER]" in forbidden_reply

    # 2. Test Safe Command Auto-run
    safe_tokens = []
    async for token in session.process_turn("/exec echo 'Safe Execution'"):
        safe_tokens.append(token)
    safe_reply = "".join(safe_tokens)
    assert "⚡ [Safe Command]" in safe_reply
    assert "Safe Execution" in safe_reply

    # 3. Test Caution Command Requires Two-Man Rule Approval
    caution_tokens = []
    async for token in session.process_turn("/exec git commit -m 'test'"):
        caution_tokens.append(token)
    caution_reply = "".join(caution_tokens)
    assert "⚠️ [Two-Man Rule Authorization Gate]" in caution_reply
    assert "/approve appr_" in caution_reply

    # Extract approval ID
    approval_id = None
    for line in caution_reply.splitlines():
        if "/approve appr_" in line:
            approval_id = line.strip().split("/approve ")[1].strip()
            break
    assert approval_id is not None

    # 4. Reject command
    reject_tokens = []
    async for token in session.process_turn(f"/reject {approval_id}"):
        reject_tokens.append(token)
    reject_reply = "".join(reject_tokens)
    assert "rejected and cancelled" in reject_reply
