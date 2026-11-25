"""
Tests for Query Understanding & Planning Engine (Milestone Q1).
Verifies intent classification, relative/absolute time range parsing,
multi-hop decomposition, entity extraction, and zero-LLM fast path.
"""
from datetime import datetime, timezone
import pytest

from app.domain.query_plan import QueryPlanner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_intent,expect_time_range,expect_as_of,expected_entities",
    [
        ("What database does Lumen use?", "factual", False, False, ["Lumen"]),
        ("Where does Alice work?", "factual", False, False, ["Alice"]),
        ("What did I decide last month?", "temporal", True, False, []),
        ("What happened yesterday?", "temporal", True, False, []),
        ("What did we ship in March 2026?", "temporal", True, False, []),
        ("What happened 2 weeks ago?", "temporal", True, False, []),
        ("What did you believe on 1 May 2026?", "temporal", False, True, []),
        ("What do my sources agree or disagree on about Redis?", "consensus", False, False, ["Redis"]),
        ("What editor do I prefer for Python?", "preference", False, False, ["Python"]),
        ("Where does Alice live and what database does Lumen use?", "multi_hop", False, False, ["Alice", "Lumen"]),
    ],
)
async def test_query_planner_table(
    query: str,
    expected_intent: str,
    expect_time_range: bool,
    expect_as_of: bool,
    expected_entities: list[str],
):
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    planner = QueryPlanner()
    plan = await planner.plan(query, now=now)

    assert plan.intent == expected_intent
    assert plan.used_llm is False
    if expect_time_range:
        assert plan.time_range is not None
        assert plan.time_range[0] < plan.time_range[1]
    if expect_as_of:
        assert plan.as_of is not None
    for ent in expected_entities:
        assert ent in plan.entities
    if expected_intent == "multi_hop":
        assert len(plan.sub_queries) == 2
