"""
Security and Execution Models for Safe Computer Control.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class CommandRiskLevel(str, Enum):
    SAFE = "SAFE"            # Read-only operations (ls, git status, cat) -> Auto-approved
    CAUTION = "CAUTION"      # Modifying operations (git commit, pip install, pytest) -> Two-Man Rule approval required
    FORBIDDEN = "FORBIDDEN"  # Destructive / malicious operations (rm -rf /, fork bomb) -> Hard rejected


@dataclass
class CommandEvaluation:
    command: str
    risk_level: CommandRiskLevel
    reason: str
    is_allowed: bool
    requires_approval: bool


@dataclass
class ExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled and not self.error
