"""
Persistent Jupyter Kernel Manager for Lumen ("The Analyst").
Executes Python cells statefully in an isolated ipykernel with:
- Headless rendering (MPLBACKEND=Agg)
- Strict per-cell timeout with interrupt (preserves in-memory DataFrames on runaway loops)
- Automatic dark-mode styling bootstrap (lumen_dark.mplstyle)
- Path-only artifact interception (never leaks raw PNG/base64 strings)
"""
from __future__ import annotations
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import jupyter_client


ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_ansi(text: str) -> str:
    """Removes ANSI escape codes from tracebacks for clean markdown formatting."""
    return ANSI_REGEX.sub("", text)


class PythonKernelManager:
    """
    Manages a persistent, long-lived ipykernel session for autonomous data science.
    Retains in-memory variables and state across conversational turns.
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        timeout_seconds: float = 30.0,
    ):
        self.workspace_root = workspace_root or Path(__file__).resolve().parents[4]
        self.charts_dir = self.workspace_root / "vault" / "charts"
        self.scratch_dir = self.workspace_root / "vault" / "scratch"
        self.style_file = Path(__file__).resolve().parent / "lumen_dark.mplstyle"
        self.timeout_seconds = timeout_seconds

        self._km: Optional[jupyter_client.KernelManager] = None
        self._kc: Optional[jupyter_client.KernelClient] = None
        self._is_ready: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_ready and self._km is not None and self._km.is_alive()

    async def start(self) -> None:
        """Launches the persistent background ipykernel and injects bootstrap helpers."""
        async with self._lock:
            if self.is_running:
                return

            self.charts_dir.mkdir(parents=True, exist_ok=True)
            self.scratch_dir.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["MPLBACKEND"] = "Agg"
            env["PYTHONUNBUFFERED"] = "1"
            # Point to local workspace modules
            core_path = str(self.workspace_root / "apps" / "lumen-core")
            mem_path = str(self.workspace_root / "packages" / "memory-engine")
            existing_py_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{core_path}:{mem_path}:{existing_py_path}"

            loop = asyncio.get_running_loop()

            def _start_sync():
                km = jupyter_client.KernelManager(kernel_name="python3")
                km.start_kernel(env=env)
                kc = km.client()
                kc.start_channels()
                kc.wait_for_ready(timeout=15.0)
                return km, kc

            self._km, self._kc = await loop.run_in_executor(None, _start_sync)
            self._is_ready = True

            # Inject Bootstrap Cell (Styling, save_chart, imports)
            await self._inject_bootstrap()

    async def _inject_bootstrap(self) -> None:
        """Injects styling, chart saver hook, and common data science imports into kernel memory."""
        style_path_str = str(self.style_file).replace("\\", "/")
        charts_dir_str = str(self.charts_dir).replace("\\", "/")
        scratch_dir_str = str(self.scratch_dir).replace("\\", "/")

        bootstrap_code = f"""
import os
import sys
import time
import re
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import duckdb

# Load custom dark-mode style
if os.path.exists('{style_path_str}'):
    plt.style.use('{style_path_str}')

_LUMEN_CHARTS_DIR = '{charts_dir_str}'
_LUMEN_SCRATCH_DIR = '{scratch_dir_str}'

def save_chart(name: str = "chart") -> str:
    \"\"\"
    Saves active matplotlib figure to vault/charts/ and outputs structured artifact marker.
    \"\"\"
    safe_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', name).strip('_').lower()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{{safe_slug}}_{{ts}}.png"
    filepath = os.path.join(_LUMEN_CHARTS_DIR, filename)
    plt.savefig(filepath, dpi=300, bbox_inches='tight', facecolor=plt.gcf().get_facecolor())
    plt.close('all')
    rel_path = f"vault/charts/{{filename}}"
    print(f"__LUMEN_ARTIFACT__:{{rel_path}}")
    return rel_path

print("Lumen Python Kernel initialized successfully. Ready for analytical workloads.")
"""
        res = await self._run_code_sync(bootstrap_code, timeout=10.0)
        if res.get("status") != "ok":
            print(f"⚠️ [Kernel Bootstrap Warning]: {res.get('stderr')}")

    async def execute(self, code: str) -> Dict[str, Any]:
        """
        Executes a Python code cell.
        Returns structured dictionary with status, stdout, stderr, and artifacts.
        """
        if not self.is_running:
            await self.start()

        async with self._lock:
            start_time = time.perf_counter()
            result = await self._run_code_sync(code, timeout=self.timeout_seconds)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            result["execution_time_ms"] = round(elapsed_ms, 2)
            return result

    async def _run_code_sync(self, code: str, timeout: float) -> Dict[str, Any]:
        """Synchronous execution wrapper run inside executor to maintain async responsiveness."""
        loop = asyncio.get_running_loop()

        def _exec():
            if not self._kc or not self._km:
                return {"status": "error", "stdout": "", "stderr": "Kernel not initialized.", "artifacts": []}

            msg_id = self._kc.execute(code)
            stdout_parts = []
            stderr_parts = []
            artifacts = []
            status = "ok"

            start_t = time.time()
            is_idle = False

            while not is_idle:
                time_left = timeout - (time.time() - start_t)
                if time_left <= 0:
                    # Timeout exceeded -> send kernel interrupt!
                    self._km.interrupt_kernel()
                    status = "timeout"
                    stderr_parts.append(
                        f"\n[Execution Timeout: Cell exceeded {timeout:.1f}s limit. Kernel interrupted cleanly; in-memory state preserved.]\n"
                    )
                    break

                try:
                    msg = self._kc.get_iopub_msg(timeout=min(time_left, 1.0))
                except Exception:
                    continue

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg.get("msg_type")
                content = msg.get("content", {})

                if msg_type == "stream":
                    name = content.get("name")
                    text = content.get("text", "")
                    if name == "stdout":
                        # Check for artifact marker
                        for line in text.splitlines(keepends=True):
                            if line.startswith("__LUMEN_ARTIFACT__:"):
                                artifact_path = line.split("__LUMEN_ARTIFACT__:", 1)[1].strip()
                                artifacts.append(artifact_path)
                            else:
                                stdout_parts.append(line)
                    elif name == "stderr":
                        stderr_parts.append(clean_ansi(text))

                elif msg_type == "error":
                    status = "error"
                    tb = content.get("traceback", [])
                    clean_tb = [clean_ansi(line) for line in tb]
                    stderr_parts.append("\n".join(clean_tb))

                elif msg_type == "execute_result":
                    data = content.get("data", {})
                    plain_text = data.get("text/plain", "")
                    if plain_text:
                        stdout_parts.append(plain_text + "\n")

                elif msg_type == "status":
                    if content.get("execution_state") == "idle":
                        is_idle = True

            # Pull shell message to ensure complete sync
            try:
                self._kc.get_shell_msg(timeout=2.0)
            except Exception:
                pass

            return {
                "status": status,
                "stdout": "".join(stdout_parts).strip(),
                "stderr": "".join(stderr_parts).strip(),
                "artifacts": list(dict.fromkeys(artifacts)),
            }

        return await loop.run_in_executor(None, _exec)

    async def interrupt(self) -> bool:
        """Interrupts currently executing cell without killing kernel."""
        if self._km:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._km.interrupt_kernel)
            return True
        return False

    async def restart(self) -> None:
        """Restarts the kernel from clean slate and re-runs bootstrap."""
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        """Gracefully shuts down kernel client channels and terminates daemon."""
        async with self._lock:
            if not self._km:
                return

            loop = asyncio.get_running_loop()

            def _shutdown():
                try:
                    if self._kc:
                        self._kc.stop_channels()
                except Exception:
                    pass
                try:
                    if self._km:
                        self._km.shutdown_kernel(now=True)
                except Exception:
                    pass

            await loop.run_in_executor(None, _shutdown)
            self._km = None
            self._kc = None
            self._is_ready = False
