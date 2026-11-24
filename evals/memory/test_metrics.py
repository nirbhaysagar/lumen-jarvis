"""
Unit tests for evals/memory/metrics.py.
"""
import pytest
from evals.memory.metrics import percentile, CaseResult, compute_eval_metrics


def test_percentile():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(data, 50.0) == 30.0
    assert percentile(data, 0.0) == 10.0
    assert percentile(data, 100.0) == 50.0
    assert percentile([], 50.0) == 0.0


def test_compute_eval_metrics_empty():
    summary = compute_eval_metrics([])
    assert summary.total_cases == 0
    assert summary.passed_cases == 0
    assert summary.accuracy == 0.0


def test_compute_eval_metrics_basic():
    results = [
        CaseResult(id="c1", category="fact", passed=True, expected_abstain=False, actual_abstained=False, latency_ms=10.0),
        CaseResult(id="c2", category="fact", passed=True, expected_abstain=False, actual_abstained=False, latency_ms=20.0),
        CaseResult(id="c3", category="abstention", passed=True, expected_abstain=True, actual_abstained=True, latency_ms=5.0),
        CaseResult(id="c4", category="abstention", passed=False, reasons=["Failed to abstain"], expected_abstain=True, actual_abstained=False, latency_ms=15.0),
    ]

    summary = compute_eval_metrics(results)
    assert summary.total_cases == 4
    assert summary.passed_cases == 3
    assert summary.accuracy == 0.75
    assert summary.category_breakdown["fact"]["accuracy"] == 1.0
    assert summary.category_breakdown["abstention"]["accuracy"] == 0.5
    
    # Abstention: 1 TP (c3), 0 FP, 1 FN (c4)
    assert summary.abstention_precision == 1.0
    assert summary.abstention_recall == 0.5
    assert summary.latency_p50_ms == 12.5

    md = summary.to_markdown()
    assert "Lumen Memory Engine Evaluation Report" in md
    assert "75.0%" in md

    d = summary.to_dict()
    assert d["total_cases"] == 4
    assert d["accuracy"] == 0.75
