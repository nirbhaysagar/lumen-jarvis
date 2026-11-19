"""
Sandboxed Subprocess Runner: Ring 3 of Safe Computer Control.
Executes evaluated shell commands asynchronously with strict timeouts,
cancellation tokens, and stdout/stderr capture.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any
from ..concurrency.models import CancellationToken
from .gatekeeper import ASTGatekeeper
from .models import CommandRiskLevel, ExecutionResult

logger = logging.getLogger("lumen.tools.runner")


class SandboxedRunner:
    def __init__(self, default_timeout: float = 30.0) -> None:
        self.default_timeout = default_timeout
        self.gatekeeper = ASTGatekeeper()

    async def execute(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        cancellation_token: CancellationToken | None = None,
        bypass_gatekeeper: bool = False,
    ) -> ExecutionResult:
        """
        Executes a shell command after AST gatekeeper evaluation.
        Enforces timeout limits and cooperative cancellation.
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

        # 2. Check cancellation token before launching
        if cancellation_token and cancellation_token.is_cancelled:
            return ExecutionResult(
                command=command,
                exit_code=130,
                stdout="",
                stderr="Execution cancelled before launch.",
                duration_ms=0.0,
                cancelled=True,
            )

        # 3. Spawn asynchronous subprocess
        process = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else os.getcwd(),
            )

            # Monitor cancellation concurrently while waiting for process
            async def wait_process():
                stdout_data, stderr_data = await process.communicate()
                return stdout_data, stderr_data

            if cancellation_token:
                # Polling cancellation check concurrently
                async def monitor_cancellation():
                    while process.returncode is None:
                        if cancellation_token.is_cancelled:
                            try:
                                process.terminate()
                                await asyncio.sleep(0.3)
                                if process.returncode is None:
                                    process.kill()
                            except ProcessLookupError:
                                pass
                            raise asyncio.CancelledError("Cancelled via cancellation token.")
                        await asyncio.sleep(0.05)

                monitor_task = asyncio.create_task(monitor_cancellation())
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        wait_process(),
                        timeout=effective_timeout,
                    )
                finally:
                    monitor_task.cancel()
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    wait_process(),
                    timeout=effective_timeout,
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecutionResult(
                command=command,
                exit_code=process.returncode or 0,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                duration_ms=round(elapsed_ms, 2),
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            if process:
                try:
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
