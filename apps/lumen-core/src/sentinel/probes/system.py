import asyncio
import os
import uuid
from typing import Any, Dict, List, Tuple
import psutil

from src.sentinel.models import SentinelCategory, SentinelFinding, SentinelSeverity


class SystemResourceProbe:
    """
    Evaluates host system physical resource pressure:
    - Root and volume disk space consumption.
    - RAM utilization and available physical memory.
    - CPU load and process execution pressure.
    """

    def __init__(
        self,
        disk_warn_pct: float = 85.0,
        disk_crit_pct: float = 95.0,
        ram_warn_pct: float = 88.0,
        ram_crit_pct: float = 96.0,
        cpu_warn_pct: float = 90.0,
    ):
        self.disk_warn_pct = disk_warn_pct
        self.disk_crit_pct = disk_crit_pct
        self.ram_warn_pct = ram_warn_pct
        self.ram_crit_pct = ram_crit_pct
        self.cpu_warn_pct = cpu_warn_pct

    async def evaluate(self, target_path: str = "/") -> Tuple[List[SentinelFinding], Dict[str, Any]]:
        """Runs non-blocking resource inspection."""
        # Execute psutil probes in threadpool to prevent blocking the event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._evaluate_sync, target_path)

    def _evaluate_sync(self, target_path: str) -> Tuple[List[SentinelFinding], Dict[str, Any]]:
        findings: List[SentinelFinding] = []

        # 1. Disk Space Inspection
        try:
            disk = psutil.disk_usage(target_path)
            total_gb = disk.total / (1024**3)
            used_gb = disk.used / (1024**3)
            free_gb = disk.free / (1024**3)
            disk_pct = disk.percent

            disk_metrics = {
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "percent": disk_pct,
                "path": target_path,
            }

            if disk_pct >= self.disk_crit_pct:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.CRITICAL,
                        title=f"Critical Disk Space Shortage on '{target_path}' ({disk_pct:.1f}%)",
                        details=f"Free disk space has dropped to {free_gb:.1f} GB out of {total_gb:.1f} GB ({disk_pct:.1f}% utilized).",
                        metrics=disk_metrics,
                        recommendation="Purge temporary build caches, prune Docker images (`docker system prune`), or archive older logs.",
                    )
                )
            elif disk_pct >= self.disk_warn_pct:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.WARNING,
                        title=f"High Disk Space Consumption on '{target_path}' ({disk_pct:.1f}%)",
                        details=f"Disk space is {disk_pct:.1f}% utilized with {free_gb:.1f} GB remaining.",
                        metrics=disk_metrics,
                        recommendation="Review large workspace artifacts and consider clearing cache files.",
                    )
                )
        except Exception as e:
            disk_metrics = {"error": str(e)}

        # 2. Virtual Memory (RAM) Inspection
        try:
            mem = psutil.virtual_memory()
            total_ram_gb = mem.total / (1024**3)
            avail_ram_gb = mem.available / (1024**3)
            ram_pct = mem.percent

            ram_metrics = {
                "total_gb": round(total_ram_gb, 2),
                "available_gb": round(avail_ram_gb, 2),
                "percent": ram_pct,
            }

            if ram_pct >= self.ram_crit_pct:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.CRITICAL,
                        title=f"Critical Memory Exhaustion ({ram_pct:.1f}%)",
                        details=f"Host system RAM is severely constrained. Only {avail_ram_gb:.2f} GB available out of {total_ram_gb:.1f} GB.",
                        metrics=ram_metrics,
                        recommendation="Terminate idle background applications or inspect rogue memory-leaking processes.",
                    )
                )
            elif ram_pct >= self.ram_warn_pct:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.WARNING,
                        title=f"High Memory Pressure ({ram_pct:.1f}%)",
                        details=f"System memory usage has exceeded normal bounds ({avail_ram_gb:.2f} GB remaining).",
                        metrics=ram_metrics,
                        recommendation="Monitor memory-intensive tasks and avoid spawning excessive parallel subagents.",
                    )
                )
        except Exception as e:
            ram_metrics = {"error": str(e)}

        # 3. CPU Utilization
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count() or 1
            cpu_metrics = {"percent": cpu_pct, "logical_cores": cpu_count}

            if cpu_pct >= self.cpu_warn_pct:
                findings.append(
                    SentinelFinding(
                        finding_id=f"find_{uuid.uuid4().hex[:8]}",
                        category=SentinelCategory.SYSTEM_RESOURCE,
                        severity=SentinelSeverity.WARNING,
                        title=f"Elevated CPU Load ({cpu_pct:.1f}%)",
                        details=f"Processor workload is at {cpu_pct:.1f}% across {cpu_count} logical cores.",
                        metrics=cpu_metrics,
                        recommendation="Check top running tasks for compilation or indexing loops.",
                    )
                )
        except Exception as e:
            cpu_metrics = {"error": str(e)}

        composite_metrics = {
            "disk": disk_metrics,
            "memory": ram_metrics,
            "cpu": cpu_metrics,
        }

        return findings, composite_metrics
