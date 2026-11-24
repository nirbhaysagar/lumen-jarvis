"""
Evaluation Metrics Framework for Lumen Memory Engine (Phase 0.5 - E1).
Calculates:
- Overall & Per-Category Accuracy
- Abstention Precision and Recall
- Citation Grounding Integrity Rate
- Latency percentiles (p50, p95, max)
- Generates structured JSON reports and GitHub-flavored Markdown summaries.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import math
from typing import Any, Sequence


def percentile(data: Sequence[float], p: float) -> float:
    """Calculates the p-th percentile (0 <= p <= 100) using linear interpolation."""
    if not data:
        return 0.0
    s_data = sorted(data)
    k = (len(s_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s_data[int(k)])
    d0 = s_data[int(f)] * (c - k)
    d1 = s_data[int(c)] * (k - f)
    return float(d0 + d1)


@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    top_k_pass: bool = True
    cites_pass: bool = True
    expected_abstain: bool = False
    actual_abstained: bool = False
    valid_citation_rate: float = 1.0
    latency_ms: float = 0.0


@dataclass
class EvalSummary:
    total_cases: int
    passed_cases: int
    accuracy: float
    category_breakdown: dict[str, dict[str, Any]]
    abstention_precision: float
    abstention_recall: float
    abstention_f1: float
    grounding_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_max_ms: float
    results: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "accuracy": round(self.accuracy, 4),
            "abstention": {
                "precision": round(self.abstention_precision, 4),
                "recall": round(self.abstention_recall, 4),
                "f1": round(self.abstention_f1, 4),
            },
            "grounding_rate": round(self.grounding_rate, 4),
            "latency_ms": {
                "p50": round(self.latency_p50_ms, 2),
                "p95": round(self.latency_p95_ms, 2),
                "max": round(self.latency_max_ms, 2),
            },
            "category_breakdown": self.category_breakdown,
            "results": self.results,
        }

    def to_markdown(self) -> str:
        lines = [
            "# 📊 Lumen Memory Engine Evaluation Report",
            "",
            f"**Total Cases:** {self.total_cases} | **Passed:** {self.passed_cases} | **Accuracy:** {self.accuracy * 100:.1f}%",
            "",
            "## 🎯 Key Quality Metrics",
            "| Metric | Value | Target | Status |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Overall Accuracy** | {self.accuracy * 100:.1f}% | ≥ 85.0% | {'✅ PASS' if self.accuracy >= 0.85 else '❌ FAIL'} |",
            f"| **Abstention Precision** | {self.abstention_precision * 100:.1f}% | ≥ 90.0% | {'✅ PASS' if self.abstention_precision >= 0.90 else '❌ FAIL'} |",
            f"| **Abstention Recall** | {self.abstention_recall * 100:.1f}% | ≥ 80.0% | {'✅ PASS' if self.abstention_recall >= 0.80 else '❌ FAIL'} |",
            f"| **Grounding Rate** | {self.grounding_rate * 100:.1f}% | 100.0% | {'✅ PASS' if self.grounding_rate >= 0.99 else '⚠️ CHECK'} |",
            f"| **Recall Latency (p50)** | {self.latency_p50_ms:.1f} ms | ≤ 50 ms | {'✅ PASS' if self.latency_p50_ms <= 50 else '⚠️ CHECK'} |",
            f"| **Recall Latency (p95)** | {self.latency_p95_ms:.1f} ms | ≤ 100 ms | {'✅ PASS' if self.latency_p95_ms <= 100 else '⚠️ CHECK'} |",
            "",
            "## 📁 Breakdown by Category",
            "| Category | Total | Passed | Accuracy |",
            "| :--- | :---: | :---: | :---: |",
        ]

        for cat, stats in sorted(self.category_breakdown.items()):
            acc = stats["accuracy"] * 100
            lines.append(f"| `{cat}` | {stats['total']} | {stats['passed']} | {acc:.1f}% |")

        lines.extend(["", "## 🔍 Failed Cases (if any)"])
        failures = [r for r in self.results if not r["passed"]]
        if not failures:
            lines.append("*All cases passed successfully!* 🎉")
        else:
            for f in failures:
                lines.append(f"- **{f['id']}** (`{f['category']}`)")
                for r in f.get("reasons", []):
                    lines.append(f"  - ↳ {r}")

        return "\n".join(lines)


def compute_eval_metrics(results: Sequence[CaseResult]) -> EvalSummary:
    """Aggregates CaseResults into an EvalSummary."""
    total = len(results)
    if total == 0:
        return EvalSummary(
            total_cases=0,
            passed_cases=0,
            accuracy=0.0,
            category_breakdown={},
            abstention_precision=0.0,
            abstention_recall=0.0,
            abstention_f1=0.0,
            grounding_rate=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            latency_max_ms=0.0,
            results=[],
        )

    passed = sum(1 for r in results if r.passed)
    accuracy = passed / total

    # Category breakdown
    cat_map: dict[str, list[CaseResult]] = {}
    for r in results:
        cat_map.setdefault(r.category, []).append(r)

    category_breakdown = {}
    for cat, items in cat_map.items():
        c_passed = sum(1 for i in items if i.passed)
        category_breakdown[cat] = {
            "total": len(items),
            "passed": c_passed,
            "accuracy": c_passed / len(items),
        }

    # Abstention metrics
    tp = sum(1 for r in results if r.expected_abstain and r.actual_abstained)
    fp = sum(1 for r in results if not r.expected_abstain and r.actual_abstained)
    fn = sum(1 for r in results if r.expected_abstain and not r.actual_abstained)

    prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if tp == 0 and fn == 0 else 0.0)
    rec = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * (prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

    # Grounding rate
    grounding_rates = [r.valid_citation_rate for r in results if not r.expected_abstain]
    avg_grounding = sum(grounding_rates) / len(grounding_rates) if grounding_rates else 1.0

    # Latencies
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    p50 = percentile(latencies, 50.0)
    p95 = percentile(latencies, 95.0)
    l_max = max(latencies) if latencies else 0.0

    res_dicts = [
        {
            "id": r.id,
            "category": r.category,
            "passed": r.passed,
            "reasons": r.reasons,
            "latency_ms": round(r.latency_ms, 2),
        }
        for r in results
    ]

    return EvalSummary(
        total_cases=total,
        passed_cases=passed,
        accuracy=accuracy,
        category_breakdown=category_breakdown,
        abstention_precision=prec,
        abstention_recall=rec,
        abstention_f1=f1,
        grounding_rate=avg_grounding,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_max_ms=l_max,
        results=res_dicts,
    )
