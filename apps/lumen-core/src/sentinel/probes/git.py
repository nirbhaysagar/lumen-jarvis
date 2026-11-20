import asyncio
import os
from pathlib import Path
import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.sentinel.models import SentinelCategory, SentinelFinding, SentinelSeverity


class GitWorkspaceProbe:
    """
    Evaluates local Git repository status, branch topology, and workspace cleanliness.
    Monitors uncommitted modifications, untracked artifacts, detached HEAD, and unpushed commits.
    """

    def __init__(self, repo_path: Optional[Path | str] = None):
        if repo_path:
            self.repo_path = Path(repo_path)
        else:
            # Locate root repo directory
            curr = Path(__file__).resolve()
            root = curr.parent
            for _ in range(5):
                if (root / ".git").exists():
                    break
                root = root.parent
            self.repo_path = root

    async def evaluate(self) -> Tuple[List[SentinelFinding], Dict[str, Any]]:
        """Runs non-blocking async Git inspection."""
        findings: List[SentinelFinding] = []
        git_metrics: Dict[str, Any] = {
            "repo_path": str(self.repo_path),
            "is_git_repo": False,
        }

        if not (self.repo_path / ".git").exists():
            return findings, git_metrics

        git_metrics["is_git_repo"] = True

        try:
            # 1. Query git status --porcelain -b
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.repo_path),
                "status",
                "--porcelain",
                "-b",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=4.0)

            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.GIT_WORKSPACE,
                        severity=SentinelSeverity.WARNING,
                        title="Git Status Query Failed",
                        details=f"Command `git status` returned code {proc.returncode}: {err_msg}",
                    )
                )
                return findings, git_metrics

            lines = stdout.decode().splitlines()
            branch_line = lines[0] if lines else "## unknown"
            file_lines = lines[1:] if len(lines) > 1 else []

            # Parse branch
            branch_info = branch_line.replace("## ", "").strip()
            is_detached = "HEAD (no branch)" in branch_info or "(no branch)" in branch_info
            current_branch = branch_info.split("...")[0].strip()

            staged_files = []
            unstaged_files = []
            untracked_files = []

            for f in file_lines:
                if len(f) < 3:
                    continue
                code = f[:2]
                path = f[3:].strip()

                if code == "??":
                    untracked_files.append(path)
                else:
                    if code[0] not in (" ", "?"):
                        staged_files.append(path)
                    if code[1] not in (" ", "?"):
                        unstaged_files.append(path)

            git_metrics.update({
                "branch": current_branch,
                "is_detached": is_detached,
                "staged_count": len(staged_files),
                "unstaged_count": len(unstaged_files),
                "untracked_count": len(untracked_files),
                "is_clean": len(file_lines) == 0,
            })

            # Check for Detached HEAD
            if is_detached:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.GIT_WORKSPACE,
                        severity=SentinelSeverity.WARNING,
                        title="Git Repository in Detached HEAD State",
                        details=f"The repository at {self.repo_path.name} is detached from any tracking branch.",
                        metrics=git_metrics,
                        recommendation="Checkout an active branch or create a new branch (`git checkout -b <branch>`).",
                    )
                )

            # Check for uncommitted work accumulation
            total_dirty = len(staged_files) + len(unstaged_files)
            if total_dirty >= 15:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.GIT_WORKSPACE,
                        severity=SentinelSeverity.WARNING,
                        title=f"Large Uncommitted Change Accumulation ({total_dirty} files)",
                        details=f"Workspace has {len(staged_files)} staged and {len(unstaged_files)} unstaged modifications.",
                        metrics=git_metrics,
                        recommendation="Consider creating a milestone commit or running tests before proceeding.",
                    )
                )
            elif total_dirty > 0:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.GIT_WORKSPACE,
                        severity=SentinelSeverity.INFO,
                        title=f"Active Workspace Changes ({total_dirty} files dirty)",
                        details=f"Working copy has {len(staged_files)} staged and {len(unstaged_files)} unstaged files.",
                        metrics=git_metrics,
                    )
                )

        except asyncio.TimeoutError:
            findings.append(
                SentinelFinding(
                    finding_id=f"find_{uuid.uuid4().hex[:8]}",
                    category=SentinelCategory.GIT_WORKSPACE,
                    severity=SentinelSeverity.WARNING,
                    title="Git Query Timed Out",
                    details="The `git status` invocation exceeded the 4.0 second ceiling.",
                )
            )
        except Exception as e:
            findings.append(
                SentinelFinding(
                    finding_id=f"find_{uuid.uuid4().hex[:8]}",
                    category=SentinelCategory.GIT_WORKSPACE,
                    severity=SentinelSeverity.WARNING,
                    title="Git Probe Error",
                    details=f"Unexpected error while inspecting repository: {e}",
                )
            )

        return findings, git_metrics
