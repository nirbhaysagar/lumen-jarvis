import time
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SentinelSeverity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SentinelCategory(str, Enum):
    SYSTEM_RESOURCE = "system_resource"
    CONTAINER_HEALTH = "container_health"
    GIT_WORKSPACE = "git_workspace"
    PROCESS_ANOMALY = "process_anomaly"


class SentinelFinding(BaseModel):
    finding_id: str
    category: SentinelCategory
    severity: SentinelSeverity
    title: str
    details: str
    timestamp: float = Field(default_factory=time.time)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    recommendation: Optional[str] = None

    @property
    def deduplication_key(self) -> str:
        """Stable key for alert state deduplication (category + title)."""
        return f"{self.category.value}:{self.title}"


class SentinelReport(BaseModel):
    patrol_id: str
    timestamp: float = Field(default_factory=time.time)
    overall_status: SentinelSeverity = SentinelSeverity.OK
    findings: List[SentinelFinding] = Field(default_factory=list)
    system_metrics: Dict[str, Any] = Field(default_factory=dict)
    containers_monitored: List[Dict[str, Any]] = Field(default_factory=list)
    git_status: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def alert_count(self) -> int:
        return sum(1 for f in self.findings if f.severity in (SentinelSeverity.WARNING, SentinelSeverity.CRITICAL))

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SentinelSeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SentinelSeverity.WARNING)
