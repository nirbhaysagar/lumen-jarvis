import asyncio
import os
from pathlib import Path
import pytest

from src.sentinel.models import (
    SentinelCategory,
    SentinelFinding,
    SentinelReport,
    SentinelSeverity,
)
from src.sentinel.probes.system import SystemResourceProbe
from src.sentinel.probes.docker import DockerHealthProbe
from src.sentinel.probes.git import GitWorkspaceProbe
from src.sentinel.daemon import SentinelDaemon, SentinelDaemonState
from src.vault.vault_sync import ObsidianVaultSync


@pytest.mark.asyncio
async def test_system_resource_probe():
    # 1. Normal baseline evaluation
    probe = SystemResourceProbe(disk_warn_pct=99.9, ram_warn_pct=99.9, cpu_warn_pct=99.9)
    findings, metrics = await probe.evaluate("/")

    assert "disk" in metrics
    assert "memory" in metrics
    assert "cpu" in metrics
    assert metrics["disk"]["total_gb"] > 0
    assert metrics["memory"]["total_gb"] > 0
    assert len(findings) == 0

    # 2. Trigger warning by setting ultra-low threshold
    trigger_probe = SystemResourceProbe(disk_warn_pct=1.0, disk_crit_pct=99.0)
    crit_findings, _ = await trigger_probe.evaluate("/")
    assert len(crit_findings) >= 1
    assert any(f.category == SentinelCategory.SYSTEM_RESOURCE for f in crit_findings)
    assert any(f.recommendation is not None for f in crit_findings)


@pytest.mark.asyncio
async def test_docker_health_probe():
    probe = DockerHealthProbe(expected_containers=["lumen-postgres", "lumen-redis"])
    findings, containers = await probe.evaluate()

    # In our live test environment, Docker is running and lumen-postgres/redis are up
    assert isinstance(containers, list)
    if containers:
        container_names = [c["name"] for c in containers]
        assert "lumen-postgres" in container_names or "lumen-redis" in container_names

    # Assert findings don't crash
    assert isinstance(findings, list)


@pytest.mark.asyncio
async def test_git_workspace_probe():
    probe = GitWorkspaceProbe()
    findings, git_metrics = await probe.evaluate()

    assert git_metrics["is_git_repo"] is True
    assert "branch" in git_metrics
    assert git_metrics["branch"] in ("main", "master") or "HEAD" in git_metrics["branch"]
    assert isinstance(git_metrics["is_clean"], bool)
    assert isinstance(findings, list)


def test_sentinel_report_vault_export(tmp_path):
    vault = ObsidianVaultSync(vault_path=tmp_path)

    sample_report = SentinelReport(
        patrol_id="test_patrol_001",
        overall_status=SentinelSeverity.WARNING,
        findings=[
            SentinelFinding(
                finding_id="f1",
                category=SentinelCategory.SYSTEM_RESOURCE,
                severity=SentinelSeverity.WARNING,
                title="Elevated Host Disk Usage (88.5%)",
                details="Free disk space is below standard operational buffer.",
                recommendation="Prune build caches and container images.",
            )
        ],
        system_metrics={
            "disk": {"percent": 88.5, "free_gb": 12.4, "total_gb": 245.0},
            "memory": {"percent": 65.0, "available_gb": 8.0, "total_gb": 16.0},
            "cpu": {"percent": 15.2, "logical_cores": 8},
        },
        containers_monitored=[
            {"name": "lumen-postgres", "state": "running", "health": "healthy", "status": "Up 12 hours"}
        ],
        git_status={"branch": "main", "is_clean": True, "is_detached": False},
        duration_ms=45.2,
    )

    exported_file = vault.export_sentinel_report(sample_report)
    assert exported_file.exists()
    assert exported_file.name == "latest.md"

    content = exported_file.read_text(encoding="utf-8")
    assert "Sentinel System Patrol Report (`WARNING`)" in content
    assert "Elevated Host Disk Usage" in content
    assert "lumen-postgres" in content

    # Since it had a WARNING, an immutable incident report should also exist
    incident_files = list(vault.sentinel_incidents_dir.glob("*.md"))
    assert len(incident_files) == 1


@pytest.mark.asyncio
async def test_sentinel_daemon_alert_deduplication(tmp_path):
    vault = ObsidianVaultSync(vault_path=tmp_path)

    class MockSystemProbe(SystemResourceProbe):
        def __init__(self, should_fail=True):
            super().__init__()
            self.should_fail = should_fail

        async def evaluate(self, target_path="/"):
            if self.should_fail:
                f = [
                    SentinelFinding(
                        finding_id="mock_f1",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.CRITICAL,
                        title="Low Disk Storage Space",
                        details="Critical capacity reached.",
                        recommendation="Free disk space.",
                    )
                ]
            else:
                f = []
            return f, {"disk": {"percent": 96.0 if self.should_fail else 50.0}}

    mock_probe = MockSystemProbe(should_fail=True)
    daemon = SentinelDaemon(
        interval_sec=0.2,
        vault_sync=vault,
        system_probe=mock_probe,
    )

    # Patrol 1: Anomaly discovered -> Alert enqueued
    report1 = await daemon.run_patrol()
    assert report1.overall_status == SentinelSeverity.CRITICAL
    alerts1 = await daemon.poll_pending_alerts()
    assert len(alerts1) == 1
    assert alerts1[0].title == "Low Disk Storage Space"

    # Patrol 2: Anomaly persists -> Deduplicated! Should NOT enqueue again
    report2 = await daemon.run_patrol()
    assert report2.overall_status == SentinelSeverity.CRITICAL
    alerts2 = await daemon.poll_pending_alerts()
    assert len(alerts2) == 0  # Deduplicated!

    # Patrol 3: Anomaly resolves -> Emits resolution alert
    mock_probe.should_fail = False
    report3 = await daemon.run_patrol()
    assert report3.overall_status in (SentinelSeverity.OK, SentinelSeverity.INFO)
    alerts3 = await daemon.poll_pending_alerts()
    assert len(alerts3) == 1
    assert "Incident Resolved" in alerts3[0].title


@pytest.mark.asyncio
async def test_sentinel_daemon_lifecycle(tmp_path):
    vault = ObsidianVaultSync(vault_path=tmp_path)
    daemon = SentinelDaemon(interval_sec=0.1, vault_sync=vault)

    # 1. Start daemon
    await daemon.start()
    assert daemon.is_running
    assert daemon.state in (SentinelDaemonState.PATROLLING, SentinelDaemonState.SLEEPING)

    # Allow 2 background iterations
    await asyncio.sleep(0.25)
    assert daemon.total_patrols >= 1
    assert daemon.last_report is not None

    summary = daemon.get_summary_text()
    assert "Sentinel System Diagnostic Matrix" in summary
    assert "Host Disk" in summary

    # 2. Stop daemon
    await daemon.stop()
    assert not daemon.is_running
    assert daemon.state == SentinelDaemonState.STOPPED


@pytest.mark.asyncio
async def test_sentinel_session_integration(tmp_path):
    from src.runtime.session import ConversationSession
    from src.memory_client.client import MemoryServiceClient

    vault = ObsidianVaultSync(vault_path=tmp_path)
    daemon = SentinelDaemon(interval_sec=10.0, vault_sync=vault)

    # Pre-stage a critical finding in sentinel's alert queue
    alert = SentinelFinding(
        finding_id="alert_001",
        category=SentinelCategory.CONTAINER_HEALTH,
        severity=SentinelSeverity.CRITICAL,
        title="lumen-postgres Container Offline",
        details="PostgreSQL container terminated unexpectedly.",
        recommendation="Run `docker compose up -d`.",
    )
    await daemon._pending_alerts.put(alert)

    # Mock memory client to avoid needing network
    class MockMem:
        async def search(self, *args, **kwargs):
            return []
        async def save_event(self, *args, **kwargs):
            return {"id": "ev_001"}
        async def save_fact(self, *args, **kwargs):
            return {"id": "f_001"}
        async def propose_triple(self, *args, **kwargs):
            return {"id": "p_001"}

    session = ConversationSession(
        vault_sync=vault,
        sentinel_daemon=daemon,
        memory_client=MockMem(),
    )

    # 1. Test /sentinel-status command
    status_tokens = []
    async for tok in session.process_turn("/sentinel-status"):
        status_tokens.append(tok)
    status_text = "".join(status_tokens)
    assert "Sentinel" in status_text

    # 2. Test conversational turn with proactive alert announcement
    turn_tokens = []
    async for tok in session.process_turn("Status report"):
        turn_tokens.append(tok)
    turn_text = "".join(turn_tokens)

    assert "Sentinel Proactive Security & Health Warning" in turn_text
    assert "lumen-postgres Container Offline" in turn_text
    assert "Run `docker compose up -d`." in turn_text
