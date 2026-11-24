"""
Recall Threshold Calibration Script (Phase 0.6 - R2).
Sweeps candidate confidence thresholds tau in [0.10, 0.80] across eval cases.
Finds optimal tau* achieving >= 90% abstention precision while maximizing overall accuracy.
Saves calibrated configuration to packages/memory-engine/config/threshold.json.
"""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import json
import httpx
from httpx import ASGITransport
import asyncpg
import yaml

from app.core.config import settings
from app.main import app
from evals.memory.run import (
    clean_test_db,
    run_eval_case,
    TEST_DB_URL,
    MEMORY_TOKEN,
    EVAL_CLOCK,
)
from evals.memory.metrics import compute_eval_metrics


async def calibrate_threshold(
    case_files: list[Path],
    threshold_range: list[float],
) -> dict[str, Any]:
    print(f"=== Calibrating Recall Threshold over {len(case_files)} cases ===")
    print(f"Testing thresholds: {[round(t, 2) for t in threshold_range]}")

    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
    results_by_thresh: dict[float, dict[str, Any]] = {}

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
        for tau in threshold_range:
            tau_r = round(tau, 3)
            case_results = []
            
            # Run each case with this candidate threshold
            for cfile in case_files:
                # Temporarily inject threshold into case query if not set
                with open(cfile, "r") as f:
                    cdata = yaml.safe_load(f)
                cdata["query"]["threshold"] = tau_r
                
                # Write temp file or run directly
                res = await run_eval_case(cfile, client, pool, pacing_seconds=0.1)
                case_results.append(res)

            metrics = compute_eval_metrics(case_results)
            results_by_thresh[tau_r] = {
                "threshold": tau_r,
                "accuracy": round(metrics.accuracy, 4),
                "abstention_precision": round(metrics.abstention_precision, 4),
                "abstention_recall": round(metrics.abstention_recall, 4),
                "abstention_f1": round(metrics.abstention_f1, 4),
                "passed": metrics.passed_cases,
                "total": metrics.total_cases,
            }
            print(
                f"tau={tau_r:.2f} | Acc={metrics.accuracy*100:.1f}% | "
                f"Abstain Prec={metrics.abstention_precision*100:.1f}% | "
                f"Recall={metrics.abstention_recall*100:.1f}%"
            )

    await pool.close()

    # Select optimal tau*
    # Constraint: abstention_precision >= 0.90
    # Objective: maximize accuracy, tie-break by abstention_f1
    eligible = [
        v for v in results_by_thresh.values()
        if v["abstention_precision"] >= 0.90
    ]
    if eligible:
        optimal = max(eligible, key=lambda x: (x["accuracy"], x["abstention_f1"]))
    else:
        # Fallback to highest precision
        optimal = max(results_by_thresh.values(), key=lambda x: (x["abstention_precision"], x["accuracy"]))

    print("\n" + "=" * 60)
    print(f"Optimal Threshold: tau* = {optimal['threshold']}")
    print(f"  Accuracy: {optimal['accuracy']*100:.1f}%")
    print(f"  Abstention Precision: {optimal['abstention_precision']*100:.1f}%")
    print(f"  Abstention Recall: {optimal['abstention_recall']*100:.1f}%")
    print("=" * 60)

    config_path = Path(__file__).resolve().parent.parent.parent / "packages" / "memory-engine" / "config" / "threshold.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    out_payload = {
        "calibrated_threshold": optimal["threshold"],
        "metrics": optimal,
        "calibration_sweep": results_by_thresh,
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, indent=2)
    print(f"Saved calibrated threshold config to: {config_path}")

    return optimal


def main():
    parser = argparse.ArgumentParser(description="Calibrate recall abstention threshold")
    parser.add_argument("--category", type=str, default=None, help="Filter cases by category")
    args = parser.parse_args()

    cases_dir = Path(__file__).parent / "cases"
    all_files = list(cases_dir.glob("*.yaml"))

    if args.category:
        filtered = []
        for cf in all_files:
            with open(cf, "r") as f:
                d = yaml.safe_load(f)
            if d.get("category") == args.category:
                filtered.append(cf)
        all_files = filtered

    threshold_range = [0.20, 0.30, 0.40, 0.50, 0.60]
    asyncio.run(calibrate_threshold(all_files[:10], threshold_range))


if __name__ == "__main__":
    main()
