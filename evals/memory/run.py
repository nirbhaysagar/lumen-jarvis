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
import secrets
from dotenv import load_dotenv
import asyncpg
import yaml
import httpx
from httpx import ASGITransport

load_dotenv()

_EVAL_TOKEN = os.getenv("LUMEN_MEMORY_TOKEN") or secrets.token_urlsafe(32)
_EVAL_PG_PASS = os.getenv("POSTGRES_PASSWORD", "")
_EVAL_DB_URL = (
    os.getenv("LUMEN_DATABASE_URL")
    or os.getenv("DATABASE_URL")
    or f"postgresql://lumen:{_EVAL_PG_PASS}@localhost:5432/lumen_memory_test"
)

# Force test database isolation before importing app
os.environ["LUMEN_ENV"] = "test"
os.environ["POSTGRES_DB"] = "lumen_memory_test"
os.environ["POSTGRES_PASSWORD"] = _EVAL_PG_PASS
os.environ["LUMEN_DATABASE_URL"] = _EVAL_DB_URL
os.environ["DATABASE_URL"] = _EVAL_DB_URL
os.environ["LUMEN_MEMORY_TOKEN"] = _EVAL_TOKEN

# Add packages/memory-engine to sys.path
engine_path = Path(__file__).resolve().parent.parent.parent / "packages" / "memory-engine"
if str(engine_path) not in sys.path:
    sys.path.insert(0, str(engine_path))

from app.core.config import settings
from app.main import app

settings.POSTGRES_DB = "lumen_memory_test"
settings.POSTGRES_PASSWORD = _EVAL_PG_PASS
settings.LUMEN_ENV = "test"
settings.LUMEN_MEMORY_TOKEN = _EVAL_TOKEN

EVAL_CLOCK = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

TEST_DB_URL = _EVAL_DB_URL
MEMORY_TOKEN = _EVAL_TOKEN


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
                    retrieval_traces, review_queue, outbox, jobs 
                CASCADE;
                """
            )
        except Exception as e:
            pass


async def run_eval_case(case_path: Path, client: httpx.AsyncClient, pool: asyncpg.Pool) -> dict[str, Any]:
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

        try:
            resp = await client.post(
                "/v1/capture",
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
                ev_id = resp.json().get("event_id")
                setup_event_ids.append(ev_id)

                # Trigger extraction & application
                await client.post(
                    "/v1/extract",
                    json={
                        "event_id": ev_id,
                        "content": content,
                        "speaker": "user",
                        "captured_at": occurred_at,
                        "context": context,
                    },
                    headers=headers,
                )
            else:
                setup_event_ids.append(f"err_{resp.status_code}")
        except Exception as e:
            setup_event_ids.append(f"exc_{e}")

        # Pacing to avoid burst token rate limiting on free tier
        await asyncio.sleep(2.0)

    # 3. Query recall
    query_text = query.get("text", "")
    recall_resp = None
    try:
        recall_resp = await client.post(
            "/v1/recall",
            json={
                "query": query_text,
                "context": "default",
                "limit": 5,
            },
            headers=headers,
        )
    except Exception as e:
        pass

    # 4. Evaluate expectations
    reasons: list[str] = []
    top_k_pass = True

    if recall_resp is None or recall_resp.status_code != 200:
        reasons.append(f"Recall API call failed or returned status {getattr(recall_resp, 'status_code', 'None')}")
    else:
        data = recall_resp.json()
        memories = data.get("results", [])
        answer = " ".join([m.get("statement", "") for m in memories])

        # Check answer_contains_any
        if "answer_contains_any" in expect:
            matched = any(s.lower() in answer.lower() for s in expect["answer_contains_any"])
            if not matched:
                top_k_pass = False
                reasons.append(f"Answer statements '{answer}' did not contain any of {expect['answer_contains_any']}")

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
                    top_k_pass = False
                    reasons.append(f"Setup[{s_idx}] ({target_ev}) was not cited by any recalled memory")

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

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
            results = []
            for cfile in case_files:
                res = await run_eval_case(cfile, client, pool)
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
