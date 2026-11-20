import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.sentinel.models import SentinelCategory, SentinelFinding, SentinelSeverity


class DockerHealthProbe:
    """
    Evaluates local Docker daemon state, container uptime, and healthcheck integrity.
    Proactively detects terminated databases, memory stores, or crashlooping containers.
    """

    def __init__(self, expected_containers: Optional[List[str]] = None):
        self.expected_containers = expected_containers or ["lumen-postgres", "lumen-redis"]

    async def evaluate(self) -> Tuple[List[SentinelFinding], List[Dict[str, Any]]]:
        """Runs non-blocking async Docker inspection."""
        findings: List[SentinelFinding] = []
        containers_monitored: List[Dict[str, Any]] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "ps",
                "-a",
                "--format",
                "{{json .}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode != 0:
                err_msg = stderr.decode().strip() or "Docker CLI exited with non-zero code."
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.CONTAINER_HEALTH,
                        severity=SentinelSeverity.WARNING,
                        title="Docker Daemon Unreachable or Inactive",
                        details=f"Failed to query Docker containers: {err_msg}",
                        recommendation="Ensure Docker Desktop / Docker daemon is running (`open -a Docker`).",
                    )
                )
                return findings, containers_monitored

            output_lines = stdout.decode().strip().splitlines()
            found_names = set()

            for line in output_lines:
                if not line.strip():
                    continue
                try:
                    c = json.loads(line)
                    name = c.get("Names", "")
                    state = c.get("State", "").lower()
                    status = c.get("Status", "")
                    health = c.get("HealthStatus", "").lower()
                    image = c.get("Image", "")

                    found_names.add(name)
                    c_info = {
                        "name": name,
                        "state": state,
                        "status": status,
                        "health": health,
                        "image": image,
                    }
                    containers_monitored.append(c_info)

                    # 1. Healthcheck failure check
                    if health == "unhealthy":
                        findings.append(
                            SentinelFinding(
                                finding_id=f"find_{uuid.uuid4().hex[:8]}",
                                category=SentinelCategory.CONTAINER_HEALTH,
                                severity=SentinelSeverity.CRITICAL,
                                title=f"Container '{name}' Healthcheck Failing",
                                details=f"Container '{name}' ({image}) reported UNHEALTHY state ({status}).",
                                metrics=c_info,
                                recommendation=f"Check container logs via `docker logs {name}` and verify dependent network/credentials.",
                            )
                        )

                    # 2. Crashloop / Restarting check
                    elif state == "restarting":
                        findings.append(
                            SentinelFinding(
                                finding_id=f"find_{uuid.uuid4().hex[:8]}",
                                category=SentinelCategory.CONTAINER_HEALTH,
                                severity=SentinelSeverity.WARNING,
                                title=f"Container '{name}' Crashlooping",
                                details=f"Container '{name}' is continuously restarting ({status}).",
                                metrics=c_info,
                                recommendation=f"Inspect crash trace via `docker logs --tail 50 {name}`.",
                            )
                        )

                    # 3. Exited state for expected essential services
                    elif state == "exited" and name in self.expected_containers:
                        findings.append(
                            SentinelFinding(
                                finding_id=f"find_{uuid.uuid4().hex[:8]}",
                                category=SentinelCategory.CONTAINER_HEALTH,
                                severity=SentinelSeverity.CRITICAL,
                                title=f"Essential Service '{name}' Is Terminated",
                                details=f"Container '{name}' is in EXITED state ({status}). System functionality may be impaired.",
                                metrics=c_info,
                                recommendation=f"Restart service via `docker start {name}` or run `docker compose up -d` in infra/.",
                            )
                        )

                except json.JSONDecodeError:
                    continue

            # 4. Check for missing expected containers
            for expected in self.expected_containers:
                if expected not in found_names:
                    findings.append(
                        SentinelFinding(
                            finding_id=f"find_{uuid.uuid4().hex[:8]}",
                            category=SentinelCategory.CONTAINER_HEALTH,
                            severity=SentinelSeverity.WARNING,
                            title=f"Expected Service '{expected}' Not Found",
                            details=f"Container '{expected}' does not exist on the local Docker engine.",
                            metrics={"expected_name": expected},
                            recommendation="Deploy core infrastructure with `docker compose -f infra/docker-compose.yml up -d`.",
                        )
                    )

        except asyncio.TimeoutError:
            findings.append(
                SentinelFinding(
                    finding_id=f"find_{uuid.uuid4().hex[:8]}",
                    category=SentinelCategory.CONTAINER_HEALTH,
                    severity=SentinelSeverity.WARNING,
                    title="Docker Query Timed Out",
                    details="The Docker daemon did not respond within 5.0 seconds.",
                    recommendation="Check if Docker engine is frozen or restarting.",
                )
            )
        except FileNotFoundError:
            findings.append(
                SentinelFinding(
                    finding_id=f"find_{uuid.uuid4().hex[:8]}",
                    category=SentinelCategory.CONTAINER_HEALTH,
                    severity=SentinelSeverity.INFO,
                    title="Docker CLI Not Available",
                    details="The `docker` executable was not found on system PATH.",
                    recommendation="Install Docker Desktop if containerized features are needed.",
                )
            )
        except Exception as e:
            findings.append(
                SentinelFinding(
                    finding_id=f"find_{uuid.uuid4().hex[:8]}",
                    category=SentinelCategory.CONTAINER_HEALTH,
                    severity=SentinelSeverity.WARNING,
                    title="Docker Probe Error",
                    details=f"Unexpected error while inspecting containers: {e}",
                )
            )

        return findings, containers_monitored
