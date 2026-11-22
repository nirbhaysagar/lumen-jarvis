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
        parsed: List[SearchResult] = []
        seen_urls: set[str] = set()

        try:
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS

                with DDGS() as ddgs:
                    # 1. First attempt: Web text search
                    try:
                        raw_results = list(ddgs.text(query, max_results=max_results))
                        for r in raw_results:
                            url = r.get("href", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                parsed.append(
                                    SearchResult(
                                        title=r.get("title", "No Title"),
                                        url=url,
                                        snippet=r.get("body", ""),
                                        source_type=self.source_type,
                                        author=None,
                                        score=1.0,
                                        metadata={"provider": "duckduckgo_text"}
                                    )
                                )
                    except Exception:
                        pass

                    # 2. Second attempt: Live news search (vital for sports scores, breaking results, today's events)
                    needs_news = any(k in query.lower() for k in ("score", "match", "vs", "versus", "today", "live", "news", "winner", "result", "cup", "game"))
                    if len(parsed) < max_results or needs_news:
                        try:
                            raw_news = list(ddgs.news(query, max_results=max_results))
                            for r in raw_news:
                                url = r.get("url") or r.get("href", "")
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    parsed.append(
                                        SearchResult(
                                            title=r.get("title", "No Title"),
                                            url=url,
                                            snippet=r.get("body", ""),
                                            source_type=self.source_type,
                                            author=r.get("source"),
                                            score=1.0,
                                            metadata={"provider": "duckduckgo_news"}
                                        )
                                    )
                        except Exception:
                            pass
        except Exception:
            pass

        return parsed[:max_results]

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            results = await asyncio.to_thread(self._sync_search, query, max_results)
            return results
        except Exception:
            return []
