import pytest
import asyncio
from typing import List
from src.research.models import SearchResult, SearchMode
from src.research.deoptimizer import QueryDeoptimizer
from src.research.discovery import MultiSourceDiscoveryEngine
from src.research.providers.base import SearchProvider
from src.research.providers.hackernews import HackerNewsProvider


class MockSearchProvider(SearchProvider):
    def __init__(self, name: str, source_type: str, mock_results: List[SearchResult], delay: float = 0.0, should_fail: bool = False):
        self._name = name
        self._source_type = source_type
        self.mock_results = mock_results
        self.delay = delay
        self.should_fail = should_fail
        self.last_query = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_type(self) -> str:
        return self._source_type

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        self.last_query = query
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} connection failed")
        return self.mock_results[:max_results]


def test_query_deoptimizer_basic():
    deopt = QueryDeoptimizer()
    prompt = "Can you please search for why postgresql hnsw.ef_search drops recall with 1536 dimensions?"
    res = deopt.deoptimize(prompt)

    # Conversational prefix removed
    assert "can you please search" not in res.general_query.lower()

    # Anti-SEO exclusions present
    assert "-seo" in res.general_query
    assert "-affiliate" in res.general_query

    # Technical tokens quoted
    assert '"hnsw.ef_search"' in res.exact_token_query or '"1536"' in res.exact_token_query

    # Community and academic queries generated
    assert "site:news.ycombinator.com" in res.community_query
    assert "benchmark" in res.academic_query or "evaluation" in res.academic_query


def test_query_deoptimizer_handles_raw_queries():
    deopt = QueryDeoptimizer()
    res = deopt.deoptimize("Kokoro TTS WebRTC audio latency")
    assert "Kokoro" in res.general_query
    assert "TTS" in res.general_query
    assert "-seo" in res.general_query


@pytest.mark.asyncio
async def test_discovery_engine_fanout_and_deduplication():
    web_res = [
        SearchResult(title="Postgres Guide", url="https://example.com/pg?utm_source=twitter", snippet="Guide to Postgres", source_type="web", score=1.0),
        SearchResult(title="HNSW Docs", url="https://example.com/hnsw", snippet="HNSW documentation", source_type="web", score=1.0)
    ]
    hn_res = [
        # Duplicate URL with different query params
        SearchResult(title="Postgres Discussion", url="https://example.com/pg?ref=hn", snippet="Great discussion on Postgres", source_type="community_hn", score=1.2),
        SearchResult(title="HN Comment", url="https://news.ycombinator.com/item?id=123", snippet="My take on pgvector", source_type="community_hn", score=1.5)
    ]
    reddit_res = [
        SearchResult(title="Reddit r/PostgreSQL", url="https://reddit.com/r/PostgreSQL/comments/456", snippet="Solved my index issue", source_type="community_reddit", score=1.1)
    ]
    arxiv_res = [
        SearchResult(title="[Paper] High Dimensional ANN", url="https://arxiv.org/abs/2301.00001", snippet="Theoretical proof", source_type="academic_arxiv", score=1.3)
    ]

    mock_web = MockSearchProvider("Web", "web", web_res)
    mock_hn = MockSearchProvider("HN", "community_hn", hn_res)
    mock_reddit = MockSearchProvider("Reddit", "community_reddit", reddit_res)
    mock_arxiv = MockSearchProvider("arXiv", "academic_arxiv", arxiv_res)

    engine = MultiSourceDiscoveryEngine(
        web_provider=mock_web,
        hn_provider=mock_hn,
        reddit_provider=mock_reddit,
        arxiv_provider=mock_arxiv
    )

    results = await engine.search("postgresql vector search", mode=SearchMode.ALL)

    # Verify deduplication: 6 total items from 4 providers deduplicate to 5 unique URLs
    # (https://example.com/pg appears once despite different tracking params)
    urls = [r.url for r in results]
    assert len(urls) == 5

    # Verify multi-source representation
    source_types = {r.source_type for r in results}
    assert "web" in source_types
    assert "community_hn" in source_types
    assert "community_reddit" in source_types
    assert "academic_arxiv" in source_types

    # Top scored item should be at the front
    assert results[0].score >= results[-1].score


@pytest.mark.asyncio
async def test_discovery_engine_fault_tolerance():
    # One provider throws an exception, another times out, third succeeds
    failing_provider = MockSearchProvider("Failing", "web", [], should_fail=True)
    slow_provider = MockSearchProvider("Slow", "academic_arxiv", [
        SearchResult(title="Slow Paper", url="https://arxiv.org/abs/999", snippet="Slow", source_type="academic_arxiv")
    ], delay=5.0)
    healthy_provider = MockSearchProvider("Healthy", "community_hn", [
        SearchResult(title="Good Comment", url="https://news.ycombinator.com/item?id=789", snippet="Reliable advice", source_type="community_hn")
    ])

    engine = MultiSourceDiscoveryEngine(
        web_provider=failing_provider,
        hn_provider=healthy_provider,
        reddit_provider=failing_provider,
        arxiv_provider=slow_provider
    )

    # Search with a tight timeout of 0.2s so the slow provider times out
    results = await engine.search("resilience test", mode=SearchMode.ALL, timeout=0.2)

    # Engine did not crash! Healthy results were preserved.
    assert len(results) == 1
    assert results[0].title == "Good Comment"
    assert results[0].source_type == "community_hn"


@pytest.mark.asyncio
async def test_discovery_engine_mode_filtering():
    mock_web = MockSearchProvider("Web", "web", [
        SearchResult(title="Web Result", url="https://web.com", snippet="Web", source_type="web")
    ])
    mock_hn = MockSearchProvider("HN", "community_hn", [
        SearchResult(title="HN Result", url="https://hn.com", snippet="HN", source_type="community_hn")
    ])
    mock_reddit = MockSearchProvider("Reddit", "community_reddit", [
        SearchResult(title="Reddit Result", url="https://reddit.com", snippet="Reddit", source_type="community_reddit")
    ])
    mock_arxiv = MockSearchProvider("arXiv", "academic_arxiv", [
        SearchResult(title="Paper", url="https://arxiv.org", snippet="Paper", source_type="academic_arxiv")
    ])

    engine = MultiSourceDiscoveryEngine(
        web_provider=mock_web,
        hn_provider=mock_hn,
        reddit_provider=mock_reddit,
        arxiv_provider=mock_arxiv
    )

    # Community only
    community_res = await engine.search("test", mode=SearchMode.COMMUNITY)
    assert all("community" in r.source_type for r in community_res)

    # Academic only
    academic_res = await engine.search("test", mode=SearchMode.ACADEMIC)
    assert all(r.source_type == "academic_arxiv" for r in academic_res)


@pytest.mark.asyncio
async def test_live_hackernews_provider():
    hn = HackerNewsProvider()
    results = await hn.search("pgvector postgresql", max_results=2)
    # Should return real results if internet is reachable, or empty list gracefully if offline
    assert isinstance(results, list)
    if results:
        assert results[0].source_type == "community_hn"
        assert results[0].url.startswith("http")
        assert len(results[0].title) > 0

