import pytest
import asyncio
import tempfile
import os
from pathlib import Path

from src.concurrency.models import CancellationToken
from src.research.models import SearchResult, SearchMode, ResearchDossier, ExtractedClaim
from src.research.agent import DeepResearchAgent
from src.research.discovery import MultiSourceDiscoveryEngine
from src.research.extractor import ContentExtractor, ExtractedDocument
from src.vault.vault_sync import ObsidianVaultSync
from tests.test_multi_source_search import MockSearchProvider


class MockContentExtractor(ContentExtractor):
    def __init__(self, canned_docs: dict[str, ExtractedDocument]):
        super().__init__()
        self.canned_docs = canned_docs

    async def extract_from_url(self, url: str, max_chars: int = 15000) -> ExtractedDocument:
        if url in self.canned_docs:
            return self.canned_docs[url]
        return ExtractedDocument(
            url=url,
            title="Generic Page",
            content=f"Content for {url}. Vector indexing degrades latency when dimensions exceed 1000.",
            source_type="web"
        )


@pytest.mark.asyncio
async def test_deep_research_agent_end_to_end():
    # Setup mock providers
    web_res = [
        SearchResult(title="Postgres Vector Guide", url="https://db-blog.io/vector", snippet="Vector guide", source_type="web")
    ]
    hn_res = [
        SearchResult(title="HN Discussion", url="https://news.ycombinator.com/item?id=888", snippet="Discussion", source_type="community_hn", author="pg_hacker")
    ]
    arxiv_res = [
        SearchResult(title="[Paper] High Dim Vector ANN", url="https://arxiv.org/abs/2401.001", snippet="Theoretical proof", source_type="academic_arxiv")
    ]

    discovery = MultiSourceDiscoveryEngine(
        web_provider=MockSearchProvider("Web", "web", web_res),
        hn_provider=MockSearchProvider("HN", "community_hn", hn_res),
        reddit_provider=MockSearchProvider("Reddit", "community_reddit", []),
        arxiv_provider=MockSearchProvider("arXiv", "academic_arxiv", arxiv_res),
    )

    canned_docs = {
        "https://db-blog.io/vector": ExtractedDocument(
            url="https://db-blog.io/vector",
            title="Postgres Vector Guide",
            content=(
                "# Vector Performance\n\n"
                "When scaling beyond 1000 dimensions, HNSW memory usage causes cache misses.\n"
                "We measured 5x throughput improvement by tuning shared_buffers.\n"
                "Check out the internal benchmark paper at https://db-blog.io/benchmark-paper."
            ),
            outbound_links=["https://db-blog.io/benchmark-paper", "https://db-blog.io/privacy"],
            source_type="web"
        ),
        "https://news.ycombinator.com/item?id=888": ExtractedDocument(
            url="https://news.ycombinator.com/item?id=888",
            title="HN Discussion",
            content="> \"We solved high dimensional recall drops because ef_search was set too low instead of indexing bugs.\"",
            outbound_links=[],
            source_type="community_hn",
            metadata={"author": "pg_hacker"}
        ),
        "https://db-blog.io/benchmark-paper": ExtractedDocument(
            url="https://db-blog.io/benchmark-paper",
            title="Internal Benchmark Paper",
            content="Empirical benchmark shows vector quantization solves memory bandwidth bottlenecks.",
            outbound_links=[],
            source_type="un_seo_citation"
        )
    }

    extractor = MockContentExtractor(canned_docs)
    agent = DeepResearchAgent(discovery_engine=discovery, extractor=extractor)

    progress_log = []
    async def on_progress(msg, pct):
        progress_log.append((msg, pct))

    dossier: ResearchDossier = await agent.run_research(
        topic="Postgres vector high dimensional latency",
        checkpoint_cb=on_progress,
        max_depth=1,
        max_pages=4
    )

    # 1. Dossier metadata verified
    assert dossier.topic == "Postgres vector high dimensional latency"
    assert "synthesized from" in dossier.summary
    assert len(dossier.sources) >= 2

    # 2. Key findings and claims extracted
    assert len(dossier.claims) >= 1
    claim_texts = [c.claim for c in dossier.claims]
    assert any("causes cache misses" in c or "throughput improvement" in c or "ef_search" in c for c in claim_texts)

    # 3. Checkpoints occurred
    assert len(progress_log) >= 4
    assert progress_log[-1][1] == 100


@pytest.mark.asyncio
async def test_deep_research_agent_cooperative_cancellation():
    discovery = MultiSourceDiscoveryEngine()
    agent = DeepResearchAgent(discovery_engine=discovery)

    token = CancellationToken()
    token.cancel()  # Cancel prior to start

    with pytest.raises(asyncio.CancelledError):
        await agent.run_research(topic="Cancellation Test", token=token)


def test_obsidian_vault_sync_exports_research_dossier():
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = ObsidianVaultSync(vault_path=tmp_dir)

        dossier = ResearchDossier(
            topic="HNSW Graph Partitioning",
            summary="Autonomous investigation into high-dimensional HNSW vector partitioning.",
            key_findings=[
                "HNSW graph traversal scales logarithmically.",
                "Partitioning reduces index build times by 40%."
            ],
            claims=[
                ExtractedClaim(
                    claim="Partitioning vectors into sub-clusters mitigates cache pollution.",
                    quote="\"Sub-clustering is recommended instead of monolithic indices.\"",
                    author="Senior Architect",
                    source_url="https://database-internals.com/hnsw",
                    source_type="community_hn"
                )
            ],
            sources=[
                SearchResult(title="HNSW Internals", url="https://database-internals.com/hnsw", snippet="Docs", source_type="community_hn")
            ]
        )

        file_path = vault.export_research_dossier(dossier)

        assert file_path.exists()
        assert file_path.name == "hnsw_graph_partitioning.md"
        content = file_path.read_text(encoding="utf-8")

        assert "---" in content
        assert "Research Dossier: [[HNSW]] Graph Partitioning" in content
        assert "Executive Summary" in content
        assert "Partitioning reduces index build times" in content
        assert "Senior Architect" in content
        assert "database-internals.com/hnsw" in content

        # Check vault stats includes research notes
        stats = vault.get_vault_stats()
        assert stats["research"] == 1
