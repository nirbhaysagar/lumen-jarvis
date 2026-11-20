import asyncio
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from src.sentinel.models import (
    SentinelCategory,
    SentinelFinding,
    SentinelReport,
    SentinelSeverity,
)
from src.sentinel.probes.system import SystemResourceProbe
from src.sentinel.probes.docker import DockerHealthProbe
from src.sentinel.probes.git import GitWorkspaceProbe
from src.vault.vault_sync import ObsidianVaultSync

logger = logging.getLogger("lumen.sentinel")


class SentinelDaemonState(str, Enum):
    STOPPED = "stopped"
    PATROLLING = "patrolling"
    SLEEPING = "sleeping"


class SentinelDaemon:
    """
    Autonomous Sentinel Daemon ("The Watchman").
    Periodically conducts proactive, non-blocking health and security patrols across:
    1. System Resources (Disk usage, RAM exhaustion, CPU spikes).
    2. Container Health (Docker databases, Redis, crashlooping or unhealthy containers).
    3. Git Workspace (Dirty file accumulation, detached HEAD, branch state).

    Deduplicates alerts to prevent notification fatigue, stages proactive warnings
    into the conversation session, and archives incident notes to the Obsidian Vault.
    """

    def __init__(
        self,
        interval_sec: float = 60.0,
        vault_sync: Optional[ObsidianVaultSync] = None,
        system_probe: Optional[SystemResourceProbe] = None,
        docker_probe: Optional[DockerHealthProbe] = None,
        git_probe: Optional[GitWorkspaceProbe] = None,
        expected_containers: Optional[List[str]] = None,
        repo_path: Optional[Path | str] = None,
    ):
        self.interval_sec = interval_sec
        self.vault = vault_sync or ObsidianVaultSync()
        self.system_probe = system_probe or SystemResourceProbe()
        self.docker_probe = docker_probe or DockerHealthProbe(expected_containers=expected_containers)
        self.git_probe = git_probe or GitWorkspaceProbe(repo_path=repo_path)

        self.state: SentinelDaemonState = SentinelDaemonState.STOPPED
        self.total_patrols: int = 0
        self.last_report: Optional[SentinelReport] = None
        self._start_time: float = 0.0

        # Alert deduplication & queuing
        self._active_alert_keys: Set[str] = set()
        self._pending_alerts: asyncio.Queue[SentinelFinding] = asyncio.Queue()
        self._listeners: List[Callable[[SentinelReport], None]] = []

        # Background loop task
        self._patrol_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self.state != SentinelDaemonState.STOPPED

    @property
    def uptime_seconds(self) -> float:
        if not self.is_running:
            return 0.0
        return time.time() - self._start_time

    def add_listener(self, listener: Callable[[SentinelReport], None]) -> None:
        """Registers a callback notified after each patrol."""
        self._listeners.append(listener)

    async def poll_pending_alerts(self) -> List[SentinelFinding]:
        """Drains pending proactive alerts staged for operator notification."""
        alerts = []
        while not self._pending_alerts.empty():
            try:
                alerts.append(self._pending_alerts.get_nowait())
            except Exception:
                break
        return alerts

    async def run_patrol(self) -> SentinelReport:
        """Executes a single comprehensive diagnostic patrol across all probes."""
        start_time = time.perf_counter()
        patrol_id = f"patrol_{uuid.uuid4().hex[:8]}"
        self.total_patrols += 1

        # Run all three probes in parallel
        system_fut = self.system_probe.evaluate()
        docker_fut = self.docker_probe.evaluate()
        git_fut = self.git_probe.evaluate()

        (sys_findings, sys_metrics), (doc_findings, doc_metrics), (git_findings, git_metrics) = await asyncio.gather(
            system_fut, docker_fut, git_fut
        )

        all_findings = sys_findings + doc_findings + git_findings

        # Determine overall system posture
        if any(f.severity == SentinelSeverity.CRITICAL for f in all_findings):
            overall_status = SentinelSeverity.CRITICAL
        elif any(f.severity == SentinelSeverity.WARNING for f in all_findings):
            overall_status = SentinelSeverity.WARNING
        elif any(f.severity == SentinelSeverity.INFO for f in all_findings):
            overall_status = SentinelSeverity.INFO
        else:
            overall_status = SentinelSeverity.OK

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        report = SentinelReport(
            patrol_id=patrol_id,
            timestamp=time.time(),
            overall_status=overall_status,
            findings=all_findings,
            system_metrics=sys_metrics,
            containers_monitored=doc_metrics,
            git_status=git_metrics,
            duration_ms=duration_ms,
        )

        # Alert Deduplication & Queuing
        current_finding_keys = set()
        for f in all_findings:
            if f.severity in (SentinelSeverity.WARNING, SentinelSeverity.CRITICAL):
                key = f.deduplication_key
                current_finding_keys.add(key)
                # Only enqueue if alert is new to prevent repeated spam
                if key not in self._active_alert_keys:
                    await self._pending_alerts.put(f)

        # Detect resolved issues
        resolved_keys = self._active_alert_keys - current_finding_keys
        for r_key in resolved_keys:
            resolved_finding = SentinelFinding(
                finding_id=f"res_{uuid.uuid4().hex[:8]}",
                category=SentinelCategory.SYSTEM_RESOURCE,
                severity=SentinelSeverity.OK,
                title=f"Incident Resolved: {r_key}",
                details="Anomaly previously reported has cleared and returned to normal baseline.",
            )
            await self._pending_alerts.put(resolved_finding)

        self._active_alert_keys = current_finding_keys
        self.last_report = report

        # Export report to Obsidian Vault
        try:
            self.vault.export_sentinel_report(report)
        except Exception as e:
            logger.warning(f"Could not export Sentinel report to vault: {e}")

        # Notify registered listeners
        for listener in self._listeners:
            try:
                listener(report)
            except Exception:
                pass

        return report

    async def _patrol_loop(self) -> None:
        """Continuous background patrol loop."""
        while not self._stop_event.is_set():
            try:
                self.state = SentinelDaemonState.PATROLLING
                await self.run_patrol()
                self.state = SentinelDaemonState.SLEEPING

                # Sleep until next interval or until stop requested
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_sec)
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Sentinel patrol loop: {e}")
                await asyncio.sleep(5.0)

    async def start(self) -> None:
        """Starts the background Sentinel patrol daemon."""
        if self.is_running:
            return

        self._start_time = time.time()
        self._stop_event.clear()
        self.state = SentinelDaemonState.SLEEPING
        self._patrol_task = asyncio.create_task(self._patrol_loop())
        logger.info(f"Sentinel Daemon started (Patrol cadence: {self.interval_sec}s).")

    async def stop(self) -> None:
        """Stops the Sentinel patrol daemon cleanly."""
        if not self.is_running:
            return

        self._stop_event.set()
        if self._patrol_task:
            self._patrol_task.cancel()
            try:
                await self._patrol_task
            except asyncio.CancelledError:
                pass

        self.state = SentinelDaemonState.STOPPED
        logger.info("Sentinel Daemon stopped.")

    def get_summary_text(self) -> str:
        """Renders human-readable status matrix for REPL."""
        if not self.last_report:
            return "Sentinel Daemon initialized. No diagnostic patrol completed yet."

        r = self.last_report
        status_badge = "🚨 CRITICAL" if r.overall_status == SentinelSeverity.CRITICAL else (
            "⚠️ WARNING" if r.overall_status == SentinelSeverity.WARNING else "✅ HEALTHY"
        )

        disk = r.system_metrics.get("disk", {})
        ram = r.system_metrics.get("memory", {})
        git = r.git_status

        running_containers = sum(1 for c in r.containers_monitored if c.get("state") == "running")
        total_containers = len(r.containers_monitored)

        lines = [
            f"🛡️ [Sentinel System Diagnostic Matrix] ❯ {status_badge}",
            f"• Daemon State: {self.state.value.upper()} (Uptime: {self.uptime_seconds:.1f}s | Cadence: {self.interval_sec}s)",
            f"• Patrols Completed: {self.total_patrols} (Latest scan: {r.duration_ms:.1f}ms)",
            f"• Host Disk (`/`): {disk.get('percent', 0.0)}% utilized ({disk.get('free_gb', 0.0)} GB free / {disk.get('total_gb', 0.0)} GB total)",
            f"• Host RAM: {ram.get('percent', 0.0)}% utilized ({ram.get('available_gb', 0.0)} GB available)",
            f"• Docker Containers: {running_containers}/{total_containers} running",
            f"• Git Workspace: Branch `{git.get('branch', 'unknown')}` ({'CLEAN' if git.get('is_clean') else 'DIRTY'})",
        ]

        if r.findings:
            lines.append("\nActive Patrol Findings:")
            for idx, f in enumerate(r.findings, 1):
                sev = f.severity.value.upper()
                badge = "🚨" if sev == "CRITICAL" else ("⚠️" if sev == "WARNING" else "ℹ️")
                lines.append(f"{idx}. {badge} [{sev}] {f.title}\n   {f.details}")
                if f.recommendation:
                    lines.append(f"   👉 Recommended: {f.recommendation}")
        else:
            lines.append("\n✅ All monitored subsystems are within operational baseline.")

        return "\n".join(lines)
