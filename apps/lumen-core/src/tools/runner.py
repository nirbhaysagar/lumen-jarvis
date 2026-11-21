"""
Sandboxed Subprocess Runner: Ring 3 of Safe Computer Control.
Executes evaluated shell commands asynchronously with strict timeouts,
cancellation tokens, bounded stream buffers, working directory jail checks,
and sanitized environment isolation (scrubbing API keys and secrets).
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
import time
from typing import Any
from ..concurrency.models import CancellationToken
from .gatekeeper import ASTGatekeeper
from .models import CommandRiskLevel, ExecutionResult

logger = logging.getLogger("lumen.tools.runner")


class SandboxedRunner:
    # System-critical roots that commands should never use as working directory
    CRITICAL_SYSTEM_DIRS = {
        "/etc", "/system", "/library", "/private", "/bin", "/sbin", "/usr/bin", "/usr/sbin"
    }

    # Safe environment variables allowed in sandboxed executions
    SAFE_ENV_KEYS = {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL",
        "TERM", "TMPDIR", "PWD", "VIRTUAL_ENV", "PYTHONPATH"
    }

    # Sensitive keywords scrubbed from subprocess environment
    SECRET_KEYWORDS = (
        "KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CREDENTIAL",
        "DATABASE_URL", "POSTGRES", "REDIS", "PRIVATE"
    )

    def __init__(
        self,
        default_timeout: float = 30.0,
        workspace_root: str | Path | None = None,
        max_output_bytes: int = 512 * 1024,  # 512 KB output buffer limit
        sanitize_environment: bool = True,
    ) -> None:
        self.default_timeout = default_timeout
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path(os.getcwd()).resolve()
        self.max_output_bytes = max_output_bytes
        self.sanitize_environment = sanitize_environment
        self.gatekeeper = ASTGatekeeper()

    def build_sanitized_env(self, custom_env: dict[str, str] | None = None) -> dict[str, str]:
        """
        Builds a sanitized environment dictionary that prevents leakage of
        API keys, database URLs, and sensitive authentication tokens to child processes.
        """
        if not self.sanitize_environment:
            base = dict(os.environ)
            if custom_env:
                base.update(custom_env)
            return base

        sanitized: dict[str, str] = {}
        for k, v in os.environ.items():
            upper_k = k.upper()
            # Omit any sensitive credential keys
            if any(secret in upper_k for secret in self.SECRET_KEYWORDS):
                continue
            if k in self.SAFE_ENV_KEYS:
                sanitized[k] = v

        if custom_env:
            for ck, cv in custom_env.items():
                if not any(secret in ck.upper() for secret in self.SECRET_KEYWORDS):
                    sanitized[ck] = cv

        return sanitized

    async def _read_stream_bounded(
        self,
        stream: asyncio.StreamReader | None,
        max_bytes: int,
    ) -> tuple[str, bool]:
        """
        Reads an async stream in chunks up to max_bytes to protect against
        unbounded memory consumption and terminal flooding attacks.
        """
        if not stream:
            return "", False

        chunks: list[bytes] = []
        total = 0
        truncated = False

        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                truncated = True
                chunks.append(chunk[: max_bytes - (total - len(chunk))])
                break
            chunks.append(chunk)

        text = b"".join(chunks).decode("utf-8", errors="replace")
        if truncated:
            text += f"\n\n⚠️ [OUTPUT TRUNCATED: Exceeded {max_bytes // 1024} KB limit to protect system memory]"
        return text, truncated

    async def execute(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        cancellation_token: CancellationToken | None = None,
        bypass_gatekeeper: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """
        Executes a shell command after AST gatekeeper evaluation.
        Enforces timeout limits, output buffer limits, directory jail checks,
        environment sanitization, and cooperative cancellation.
        """
        effective_timeout = timeout or self.default_timeout
        start_time = time.perf_counter()

        # 1. Evaluate safety via AST Gatekeeper
        if not bypass_gatekeeper:
            evaluation = self.gatekeeper.evaluate(command)
            if not evaluation.is_allowed:
                return ExecutionResult(
                    command=command,
                    exit_code=126,  # Standard UNIX permission/blocked error
                    stdout="",
                    stderr=f"SECURITY REFUSAL: {evaluation.reason}",
                    duration_ms=0.0,
                    error=evaluation.reason,
                )
            if evaluation.requires_approval:
                return ExecutionResult(
                    command=command,
                    exit_code=126,
                    stdout="",
                    stderr=f"SECURITY REFUSAL: Command requires explicit operator approval ({evaluation.reason}). Use Two-Man Rule.",
                    duration_ms=0.0,
                    error=f"Requires approval: {evaluation.reason}",
                )

        # 2. Check Directory Jail / Critical System Roots
        target_cwd = Path(cwd).resolve() if cwd else self.workspace_root
        target_cwd_str = str(target_cwd).lower()
        for crit_dir in self.CRITICAL_SYSTEM_DIRS:
            if target_cwd_str == crit_dir or target_cwd_str.startswith(f"{crit_dir}/"):
                return ExecutionResult(
                    command=command,
                    exit_code=126,
                    stdout="",
                    stderr=f"SECURITY REFUSAL: Working directory '{target_cwd}' is inside protected system path '{crit_dir}'.",
                    duration_ms=0.0,
                    error="Working directory jail violation",
                )

        # 3. Check cancellation token before launching
        if cancellation_token and cancellation_token.is_cancelled:
            return ExecutionResult(
                command=command,
                exit_code=130,
                stdout="",
                stderr="Execution cancelled before launch.",
                duration_ms=0.0,
                cancelled=True,
            )

        # 4. Prepare Sanitized Environment
        exec_env = self.build_sanitized_env(env)

        # 5. Spawn Asynchronous Subprocess
        process = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(target_cwd),
                env=exec_env,
            )

            # Concurrent reader tasks with bounded buffer limits
            async def collect_outputs():
                out_task = asyncio.create_task(self._read_stream_bounded(process.stdout, self.max_output_bytes))
                err_task = asyncio.create_task(self._read_stream_bounded(process.stderr, self.max_output_bytes))
                (stdout_str, out_trunc), (stderr_str, err_trunc) = await asyncio.gather(out_task, err_task)
                await process.wait()
                if out_trunc or err_trunc:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                return stdout_str, stderr_str

            if cancellation_token:
                # Concurrent cancellation polling monitor
                async def monitor_cancellation():
                    while process.returncode is None:
                        if cancellation_token.is_cancelled:
                            try:
                                process.terminate()
                                await asyncio.sleep(0.2)
                                if process.returncode is None:
                                    process.kill()
                            except ProcessLookupError:
                                pass
                            raise asyncio.CancelledError("Cancelled via cancellation token.")
                        await asyncio.sleep(0.05)

                monitor_task = asyncio.create_task(monitor_cancellation())
                try:
                    stdout_str, stderr_str = await asyncio.wait_for(
                        collect_outputs(),
                        timeout=effective_timeout,
                    )
                finally:
                    monitor_task.cancel()
            else:
                stdout_str, stderr_str = await asyncio.wait_for(
                    collect_outputs(),
                    timeout=effective_timeout,
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecutionResult(
                command=command,
                exit_code=process.returncode or 0,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_ms=round(elapsed_ms, 2),
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            if process:
                try:
                    process.terminate()
                    await asyncio.sleep(0.1)
                    if process.returncode is None:
                        process.kill()
                except ProcessLookupError:
                    pass
            return ExecutionResult(
                command=command,
                exit_code=124,  # Standard UNIX timeout code
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s.",
                duration_ms=round(elapsed_ms, 2),
                timed_out=True,
                error="Timeout exceeded",
            )

        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            if process:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            return ExecutionResult(
                command=command,
                exit_code=130,
                stdout="",
                stderr="Process interrupted and killed via cooperative cancellation token.",
                duration_ms=round(elapsed_ms, 2),
                cancelled=True,
                error="Cancelled by user",
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecutionResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration_ms=round(elapsed_ms, 2),
                error=str(e),
            )
