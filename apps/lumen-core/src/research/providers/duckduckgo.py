import asyncio
from typing import List
from src.research.models import SearchResult
from src.research.providers.base import SearchProvider


class DuckDuckGoProvider(SearchProvider):
    """
    General Web Search provider using DuckDuckGo via primp browser impersonation.
    Zero API credentials required.
    """

    def __init__(self, source_type: str = "web", name: str = "DuckDuckGo"):
        self._source_type = source_type
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_type(self) -> str:
        return self._source_type

    def _sync_search(self, query: str, max_results: int) -> List[SearchResult]:
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    raw_results = list(ddgs.text(query, max_results=max_results))
                    parsed: List[SearchResult] = []
                    for r in raw_results:
                        parsed.append(
                            SearchResult(
                                title=r.get("title", "No Title"),
                                url=r.get("href", ""),
                                snippet=r.get("body", ""),
                                source_type=self.source_type,
                                author=None,
                                score=1.0,
                                metadata={"provider": "duckduckgo"}
                            )
                        )
                    return parsed
        except Exception:
            return []

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            results = await asyncio.to_thread(self._sync_search, query, max_results)
            return results
        except Exception:
            return []
