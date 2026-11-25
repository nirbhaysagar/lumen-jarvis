"""
Lumen Memory Evaluation Runner v2 (Phase 0.5 - E2).
Executes declarative YAML eval cases against an isolated test database (lumen_memory_test).
Fixed eval clock: 2026-11-21T10:00:00+05:30.
Computes: recall accuracy, supersede correctness, honest abstention, citation grounding, and latencies.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Sequence
import secrets
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
import asyncpg
import yaml
import httpx
from httpx import ASGITransport

load_dotenv()

_EVAL_TOKEN = os.getenv("LUMEN_MEMORY_TOKEN") or secrets.token_urlsafe(32)
_EVAL_PG_PASS = os.getenv("POSTGRES_PASSWORD", "")


def _force_test_database(url: str) -> str:
    """Evals truncate between cases. Never let that URL point at the live database."""
    parts = urlparse(url)
    return urlunparse(parts._replace(path="/lumen_memory_test"))


_raw_eval_url = (
    os.getenv("LUMEN_DATABASE_URL")
    or os.getenv("DATABASE_URL")
    or f"postgresql://lumen:{_EVAL_PG_PASS}@localhost:5432/lumen_memory_test"
)
_EVAL_DB_URL = _force_test_database(_raw_eval_url)

# Force test database isolation before importing app
os.environ["LUMEN_ENV"] = "test"
os.environ["LUMEN_EVAL_MODE"] = "1"
os.environ["POSTGRES_DB"] = "lumen_memory_test"
os.environ["POSTGRES_PASSWORD"] = _EVAL_PG_PASS
os.environ["LUMEN_DATABASE_URL"] = _EVAL_DB_URL
os.environ["DATABASE_URL"] = _EVAL_DB_URL
os.environ["LUMEN_MEMORY_TOKEN"] = _EVAL_TOKEN

# Add packages/memory-engine and workspace root to sys.path
root_path = Path(__file__).resolve().parent.parent.parent
engine_path = root_path / "packages" / "memory-engine"
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))
if str(engine_path) not in sys.path:
    sys.path.insert(0, str(engine_path))

from app.core.config import settings
from app.main import app
from evals.memory.metrics import CaseResult, compute_eval_metrics
from evals.memory.scoring import obsolete_still_asserted

settings.POSTGRES_DB = "lumen_memory_test"
settings.POSTGRES_PASSWORD = _EVAL_PG_PASS
settings.LUMEN_ENV = "test"
settings.LUMEN_MEMORY_TOKEN = _EVAL_TOKEN

EVAL_CLOCK = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
TEST_DB_URL = _EVAL_DB_URL
MEMORY_TOKEN = _EVAL_TOKEN

logger = logging.getLogger("lumen.evals")


def parse_relative_time(rel_str: str | None, base_time: datetime = EVAL_CLOCK) -> datetime:
    if not rel_str:
        return base_time
    clean = rel_str.strip()
    if clean.endswith("d"):
        days = int(clean[:-1])
        return base_time + timedelta(days=days)
    if clean.endswith("h"):
        hours = int(clean[:-1])
        return base_time + timedelta(hours=hours)
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        return base_time


async def clean_test_db(pool: asyncpg.Pool):
    """Truncate tables in lumen_memory_test between test cases."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                TRUNCATE TABLE 
                    sources, events, chunks, entities, entity_aliases, 
                    memories, claims, citations, memory_relations, 
                    abstractions, abstraction_sources, memory_signals, 
                    retrieval_traces, review_queue, outbox, jobs, save_runs 
                CASCADE;
                """
            )
        except Exception:
            pass


async def verify_citation_grounding(pool: asyncpg.Pool) -> float:
    """Verifies that all citations in the DB match the exact substring in the event."""
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM citations")
        if not total or total == 0:
            return 1.0
        mismatched = await conn.fetchval(
            """
            SELECT count(*) FROM citations c JOIN events e ON e.id = c.event_id
            WHERE substring(e.content from c.char_start + 1 for c.char_end - c.char_start) <> c.quote
            """
        )
        return float(total - mismatched) / float(total)


async def run_eval_case(
    case_path: Path,
    client: httpx.AsyncClient,
    pool: asyncpg.Pool,
    pacing_seconds: float = 0.5,
) -> CaseResult:
    with open(case_path, "r", encoding="utf-8") as f:
        case = yaml.safe_load(f)

    case_id = case.get("id", case_path.stem)
    category = case.get("category", "unknown")
    setup = case.get("setup", [])
    query = case.get("query", {})
    expect = case.get("expect", {})

    # 1. Clean test DB
    await clean_test_db(pool)

    setup_event_ids: list[str] = []
    headers = {"Authorization": f"Bearer {MEMORY_TOKEN}"}

    # 2. Replay setup captures
    for idx, item in enumerate(setup):
        content = item.get("content", "")
        source = item.get("source", {"kind": "chat", "title": "eval", "author": "user"})
        occurred_at = parse_relative_time(item.get("occurred_at")).isoformat()
        context = item.get("context", "default")
        speaker = item.get("speaker", "user")

        try:
            resp = await client.post(
                "/v1/capture",
                json={
                    "content": content,
                    "source": source,
                    "occurred_at": occurred_at,
                    "context": context,
                    "speaker": speaker,
                    "idempotency_key": f"{case_id}_setup_{idx}",
                },
                headers=headers,
            )
            if resp.status_code in (200, 201):
                ev_id = resp.json().get("event_id")
                setup_event_ids.append(ev_id)

                # Trigger extraction & application
                await client.post(
                    "/v1/extract",
                    json={
                        "event_id": ev_id,
                        "content": content,
                        "speaker": speaker,
                        "captured_at": occurred_at,
                        "context": context,
                    },
                    headers=headers,
                )
            else:
                setup_event_ids.append(f"err_{resp.status_code}")
        except Exception as e:
            setup_event_ids.append(f"exc_{e}")

        if pacing_seconds > 0:
            await asyncio.sleep(pacing_seconds)

    # 3. Query recall
    query_text = query.get("text", "")
    query_context = query.get("context", "default")
    query_limit = query.get("limit", 5)
    query_as_of = query.get("as_of")
    
    recall_payload = {
        "query": query_text,
        "context": query_context,
        "limit": query_limit,
    }
    if query_as_of:
        recall_payload["as_of"] = parse_relative_time(query_as_of).isoformat()

    t_start = time.perf_counter()
    recall_resp = None
    try:
        recall_resp = await client.post(
            "/v1/recall",
            json=recall_payload,
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Recall request error in case {case_id}: {e}")
    latency_ms = (time.perf_counter() - t_start) * 1000

    # 4. Evaluate expectations
    reasons: list[str] = []
    top_k_pass = True
    cites_pass = True
    expected_abstain = bool(expect.get("abstain", False))
    actual_abstained = False

    if recall_resp is None or recall_resp.status_code != 200:
        reasons.append(f"Recall API call failed (status {getattr(recall_resp, 'status_code', 'None')})")
    else:
        data = recall_resp.json()
        memories = data.get("results", [])
        actual_abstained = (data.get("status") == "abstain" or len(memories) == 0)
        answer = " ".join([m.get("statement", "") for m in memories])

        # Check abstain expectation
        if expected_abstain:
            if not actual_abstained:
                reasons.append(
                    f"Expected abstention, but got status='{data.get('status')}' with {len(memories)} memories: {answer}"
                )
        else:
            if actual_abstained:
                reasons.append(f"Unexpected abstention for query '{query_text}'")

        # Check answer_contains_any
        if "answer_contains_any" in expect:
            matched = any(s.lower() in answer.lower() for s in expect["answer_contains_any"])
            if not matched:
                reasons.append(f"Answer '{answer}' did not contain any of {expect['answer_contains_any']}")

        # Check answer_excludes_any (e.g. obsolete value after supersede)
        if "answer_excludes_any" in expect:
            for s in expect["answer_excludes_any"]:
                if obsolete_still_asserted(answer, s):
                    reasons.append(f"Answer contains obsolete/excluded value '{s}': {answer}")

        # Check top_k_contains_setup
        if "top_k_contains_setup" in expect:
            tk_cfg = expect["top_k_contains_setup"]
            k = tk_cfg.get("k", 5)
            s_idx = tk_cfg.get("index", 0)
            target_ev = setup_event_ids[s_idx] if s_idx < len(setup_event_ids) else None
            found_in_top_k = False
            for m in memories[:k]:
                for c in m.get("citations", []):
                    if c.get("event_id") == target_ev:
                        found_in_top_k = True
                        break
            if not found_in_top_k:
                top_k_pass = False
                reasons.append(f"Setup[{s_idx}] ({target_ev}) not found in top-{k} memories")

        # Check cites_setup
        if "cites_setup" in expect:
            for s_idx in expect["cites_setup"]:
                target_ev = setup_event_ids[s_idx] if s_idx < len(setup_event_ids) else None
                found = False
                for m in memories:
                    for c in m.get("citations", []):
                        if c.get("event_id") == target_ev:
                            found = True
                            break
                if not found:
                    cites_pass = False
                    reasons.append(f"Setup[{s_idx}] ({target_ev}) was not cited by any recalled memory")

        # Check review_queue_staged
        if expect.get("review_queue_staged"):
            async with pool.acquire() as conn:
                rq_count = await conn.fetchval("SELECT count(*) FROM review_queue")
                if not rq_count or rq_count == 0:
                    reasons.append("Expected proposal in review_queue, but queue is empty")

        # Check lane filtering
        if "lane" in expect:
            expected_lane = expect["lane"]
            for m in memories:
                if m.get("lane") != expected_lane:
                    reasons.append(f"Memory lane '{m.get('lane')}' does not match expected '{expected_lane}'")

    valid_citation_rate = await verify_citation_grounding(pool)
    if valid_citation_rate < 1.0:
        reasons.append(f"Citation grounding check failed: {valid_citation_rate * 100:.1f}% valid")

    passed = len(reasons) == 0

    return CaseResult(
        id=case_id,
        category=category,
        passed=passed,
        reasons=reasons,
        top_k_pass=top_k_pass,
        cites_pass=cites_pass,
        expected_abstain=expected_abstain,
        actual_abstained=actual_abstained,
        valid_citation_rate=valid_citation_rate,
        latency_ms=latency_ms,
    )


async def run_suite(
    case_files: list[Path],
    category_filter: str | None = None,
    case_filter: str | None = None,
    export_json: Path | None = None,
    export_md: Path | None = None,
    timeout: float = 30.0,
    pacing: float = 0.5,
) -> int:
    selected_files = []
    for cfile in sorted(case_files):
        if case_filter and cfile.stem != case_filter and cfile.name != case_filter:
            continue
        selected_files.append(cfile)

    if not selected_files:
        print("No matching eval cases found.")
        return 1

    print(f"=== Running Lumen Memory Evals ({len(selected_files)} cases) ===")
    print(f"Eval Clock: {EVAL_CLOCK.isoformat()}")

    # Asyncpg pool for cleaning DB and verifying citations
    try:
        pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
    except Exception as e:
        print(f"Could not connect to {TEST_DB_URL}: {e}")
        return 1

    results: list[CaseResult] = []

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=timeout) as client:
            for cfile in selected_files:
                # Filter category by reading header if category_filter is set
                if category_filter:
                    with open(cfile, "r") as f:
                        header = yaml.safe_load(f)
                    if header.get("category") != category_filter:
                        continue

                res = await run_eval_case(cfile, client, pool, pacing_seconds=pacing)
                results.append(res)

                status_str = "✅ PASS" if res.passed else "❌ FAIL"
                print(f"{status_str} | {res.id:<35} | {res.category:<22} | {res.latency_ms:>6.1f} ms")
                if not res.passed:
                    for r in res.reasons:
                        print(f"       ↳ {r}")

    await pool.close()

    summary = compute_eval_metrics(results)
    print("\n" + "=" * 60)
    print(f"Results: {summary.passed_cases}/{summary.total_cases} passed ({summary.accuracy * 100:.1f}%)")
    print(f"Abstention Precision: {summary.abstention_precision * 100:.1f}% | Recall: {summary.abstention_recall * 100:.1f}%")
    print(f"Recall Latency (p50): {summary.latency_p50_ms:.1f} ms | (p95): {summary.latency_p95_ms:.1f} ms")
    print("=" * 60)

    # Export JSON report
    if export_json:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        with open(export_json, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"JSON report saved to: {export_json}")

    # Export Markdown report
    if export_md:
        export_md.parent.mkdir(parents=True, exist_ok=True)
        with open(export_md, "w", encoding="utf-8") as f:
            f.write(summary.to_markdown())
        print(f"Markdown report saved to: {export_md}")

    return 0 if summary.accuracy >= 0.85 else 1


def main():
    parser = argparse.ArgumentParser(description="Lumen Memory Evaluation Runner v2")
    parser.add_argument("--category", type=str, default=None, help="Filter cases by category")
    parser.add_argument("--case", type=str, default=None, help="Filter by specific case id or filename")
    parser.add_argument("--export", type=str, default=None, help="Export JSON report path")
    parser.add_argument("--markdown", type=str, default=None, help="Export Markdown report path")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request HTTP timeout in seconds")
    parser.add_argument("--pacing", type=float, default=0.5, help="Pacing between setup captures in seconds")
    parser.add_argument("--cases-dir", type=str, default=None, help="Custom cases directory")

    args = parser.parse_args()

    cases_dir = Path(args.cases_dir) if args.cases_dir else Path(__file__).parent / "cases"
    case_files = list(cases_dir.glob("*.yaml"))

    export_json = Path(args.export) if args.export else Path(__file__).parent / "results" / "latest.json"
    export_md = Path(args.markdown) if args.markdown else Path(__file__).parent / "results" / "latest.md"

    exit_code = asyncio.run(
        run_suite(
            case_files=case_files,
            category_filter=args.category,
            case_filter=args.case,
            export_json=export_json,
            export_md=export_md,
            timeout=args.timeout,
            pacing=args.pacing,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
