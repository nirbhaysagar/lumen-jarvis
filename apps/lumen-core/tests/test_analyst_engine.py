import pytest
import os
from pathlib import Path
from src.analytics.engine import AnalystEngine
from src.tools.kernel import PythonKernelManager
from src.router.model_router import ModelRouter
from src.runtime.session import ConversationSession
from src.memory_client.client import MemoryServiceClient


class MockSearchEngine:
    async def search(self, query: str, limit: int = 5):
        from src.research.models import SearchResult
        return [
            SearchResult(
                title="Take-Two Interactive FY2023 Form 10-K",
                url="https://ir.take2games.com/sec-filings/10k",
                snippet="Rockstar Games segment contributed $3.5 billion in Net Bookings for FY23, up from $3.3B in FY22.",
                source="sec_filings",
            ),
            SearchResult(
                title="Rockstar Historical Net Bookings 2018-2023",
                url="https://statista.com/rockstar-revenue",
                snippet="Annual revenue trajectory: 2018 $2.2B, 2019 $2.5B, 2020 $2.8B, 2021 $3.0B, 2022 $3.3B, 2023 $3.5B.",
                source="statista",
            ),
        ]


class MockResearchAgent:
    def __init__(self):
        self.discovery = MockSearchEngine()
        self.search_engine = self.discovery


@pytest.mark.asyncio
async def test_analyst_engine_grounding_and_dossier():
    km = PythonKernelManager(timeout_seconds=15.0)
    engine = AnalystEngine(kernel=km, research_agent=MockResearchAgent())

    tokens = []
    async for token in engine.analyze_and_plot(
        prompt="Rockstar Games revenue trajectory 2018-2023",
        preview=False,
    ):
        tokens.append(token)

    full_output = "".join(tokens)
    assert "Data Grounding Gate" in full_output
    assert "Archiving Reproducible Dossier to Obsidian Vault" in full_output

    # Check generated files in vault/analytics and vault/charts
    analytics_files = sorted(Path("vault/analytics").glob("*rockstar*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    assert len(analytics_files) >= 1
    dossier_text = analytics_files[0].read_text()
    assert "Executed Python Code" in dossier_text

    charts = list(Path("vault/charts").glob("*.png"))
    assert len(charts) >= 1

    await km.stop()


@pytest.mark.asyncio
async def test_session_routing_to_analyst():
    memory_client = MemoryServiceClient(base_url="http://localhost:8000")
    km = PythonKernelManager(timeout_seconds=15.0)
    engine = AnalystEngine(kernel=km, research_agent=MockResearchAgent())
    session = ConversationSession(memory_client=memory_client, analyst_engine=engine)

    tokens = []
    # Test explicit /analyze routing to AnalystEngine without external LLM rate-limit dependency
    async for token in session.process_turn("/analyze rockstar games revenue 2018-2023"):
        tokens.append(token)

    full_response = "".join(tokens)
    assert "Data Grounding Gate" in full_response or "Archiving Reproducible Dossier" in full_response

    await km.stop()
    await memory_client.close()
