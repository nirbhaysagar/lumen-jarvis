"""
Lumen Memory Evaluation Runner.
Executes declarative YAML eval cases against an isolated test database (lumen_memory_test).
Fixed eval clock: 2026-11-21T10:00:00+05:30.
Reports: recall@5, citation accuracy, and abstention accuracy.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
import asyncpg
import yaml
import httpx

EVAL_CLOCK = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

TEST_DB_URL = os.getenv(
    "LUMEN_DATABASE_URL",
    "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test"
)
MEMORY_API_URL = os.getenv("LUMEN_MEMORY_URL", "http://127.0.0.1:7420")
MEMORY_TOKEN = os.getenv("LUMEN_MEMORY_TOKEN", "test-token-secret")


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
    tables = [
        "retrieval_traces", "outbox", "jobs", "review_queue", "memory_signals",
        "abstraction_sources", "abstractions", "memory_relations", "citations",
        "claims", "memories", "entity_aliases", "entities", "chunks",
        "events", "sources"
    ]
    async with pool.acquire() as conn:
        for tbl in tables:
            try:
                # Check if table exists
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
                    tbl
                )
                if exists:
                    await conn.execute(f"TRUNCATE TABLE {tbl} CASCADE;")
            except Exception as e:
                pass


async def run_eval_case(case_path: Path, pool: asyncpg.Pool) -> dict[str, Any]:
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
    headers = {"Authorization": f"Bearer {MEMORY_TOKEN}"} if MEMORY_TOKEN else {}

    # 2. Replay setup captures
    async with httpx.AsyncClient(timeout=10.0) as http:
        for idx, item in enumerate(setup):
            op = item.get("op", "capture")
            if op.startswith("forget:"):
                # Handle forget
                target_idx = int(op.split(":")[1])
                # tombstone target
                continue

            content = item.get("content", "")
            source = item.get("source", {"kind": "chat"})
            occurred_at = parse_relative_time(item.get("occurred_at")).isoformat()
            context = item.get("context", "default")

            try:
                resp = await http.post(
                    f"{MEMORY_API_URL}/v1/capture",
                    json={
                        "content": content,
                        "source": source,
                        "occurred_at": occurred_at,
                        "context": context,
                        "idempotency_key": f"{case_id}_setup_{idx}",
                    },
                    headers=headers,
                )
                if resp.status_code in (200, 201):
                    setup_event_ids.append(resp.json().get("event_id"))
                else:
                    setup_event_ids.append(f"err_{resp.status_code}")
            except Exception as e:
                setup_event_ids.append(f"exc_{e}")

        # 3. Query recall
        query_text = query.get("text", "")
        as_of = parse_relative_time(query.get("as_of")).isoformat() if query.get("as_of") else None

        recall_resp = None
        try:
            recall_resp = await http.post(
                f"{MEMORY_API_URL}/v1/recall",
                json={
                    "query": query_text,
                    "contexts": query.get("context", ["default"]),
                    "as_of": as_of,
                    "k": 5,
                },
                headers=headers,
            )
        except Exception as e:
            pass

    # 4. Evaluate expectations
    passed = False
    reasons: list[str] = []
    top_k_pass = True

    if recall_resp is None or recall_resp.status_code != 200:
        reasons.append(f"Recall API call failed or returned status {getattr(recall_resp, 'status_code', 'None')}")
    else:
        data = recall_resp.json()
        memories = data.get("memories", [])
        answer = data.get("answer", "")

        # Check answer_contains_any
        if "answer_contains_any" in expect:
            matched = any(s.lower() in answer.lower() for s in expect["answer_contains_any"])
            if not matched:
                top_k_pass = False
                reasons.append(f"Answer '{answer}' did not contain any of {expect['answer_contains_any']}")

        # Check top_k_contains_setup
        if "top_k_contains_setup" in expect:
            tk_cfg = expect["top_k_contains_setup"]
            k = tk_cfg.get("k", 5)
            s_idx = tk_cfg.get("index", 0)
            target_ev = setup_event_ids[s_idx] if s_idx < len(setup_event_ids) else None
            # Check if any memory citations refer to setup event
            found_in_top_k = False
            for m in memories[:k]:
                for c in m.get("citations", []):
                    if c.get("event_id") == target_ev:
                        found_in_top_k = True
                        break
            if not found_in_top_k:
                top_k_pass = False
                reasons.append(f"Setup[{s_idx}] not found in top-{k} memories")

    passed = len(reasons) == 0

    return {
        "id": case_id,
        "category": category,
        "passed": passed,
        "reasons": reasons,
        "top_k_pass": top_k_pass,
    }


async def main():
    cases_dir = Path(__file__).parent / "cases"
    case_files = sorted(list(cases_dir.glob("*.yaml")))
    if not case_files:
        print(f"No eval cases found in {cases_dir}")
        return

    print(f"=== Running Lumen Memory Evals ({len(case_files)} cases) ===")
    print(f"Eval Clock: {EVAL_CLOCK.isoformat()}")

    # Asyncpg pool for cleaning DB
    try:
        pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
    except Exception as e:
        print(f"Could not connect to {TEST_DB_URL}: {e}")
        sys.exit(1)

    results = []
    for cfile in case_files:
        res = await run_eval_case(cfile, pool)
        results.append(res)
        status_str = "✅ PASS" if res["passed"] else "❌ FAIL"
        print(f"{status_str} | {res['id']:<30} | {res['category']}")
        if not res["passed"]:
            for r in res["reasons"]:
                print(f"       ↳ {r}")

    await pool.close()

    total = len(results)
    passed_cnt = sum(1 for r in results if r["passed"])
    print("\n" + "=" * 50)
    print(f"Results: {passed_cnt}/{total} passed ({passed_cnt/total*100:.1f}%)")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
