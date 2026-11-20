import re
from typing import List
from src.research.models import SearchResult
from src.research.providers.duckduckgo import DuckDuckGoProvider
from src.research.providers.base import SearchProvider


class RedditProvider(SearchProvider):
    """
    Practitioner Community search targeting Reddit discussions.
    Uses search syntax scoping ('site:reddit.com') to safely query discussions
    without triggering Reddit's aggressive 403 anti-bot API gates.
    """

    def __init__(self):
        self._ddg = DuckDuckGoProvider(source_type="community_reddit", name="Reddit")

    @property
    def name(self) -> str:
        return "Reddit"

    @property
    def source_type(self) -> str:
        return "community_reddit"

    def _extract_subreddit(self, url: str) -> str:
        match = re.search(r"reddit\.com/r/([^/]+)", url)
        return f"r/{match.group(1)}" if match else "Reddit"

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        targeted_query = f"site:reddit.com {query}"
        results = await self._ddg.search(targeted_query, max_results=max_results)
        
        # Enrich metadata with subreddit identification
        for r in results:
            r.source_type = self.source_type
            sub = self._extract_subreddit(r.url)
            r.author = sub
            r.metadata["subreddit"] = sub

        return results
