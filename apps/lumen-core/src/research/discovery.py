import asyncio
import re
from typing import List, Dict, Optional, Set
from urllib.parse import urlparse, urlunparse

from src.research.models import SearchResult, SearchMode, DeoptimizedQueries
from src.research.deoptimizer import QueryDeoptimizer
from src.research.providers.duckduckgo import DuckDuckGoProvider
from src.research.providers.hackernews import HackerNewsProvider
from src.research.providers.reddit import RedditProvider
from src.research.providers.arxiv import ArxivProvider
from src.research.providers.base import SearchProvider


class MultiSourceDiscoveryEngine:
    """
    Coordinates multi-surface information discovery.
    Runs de-optimized queries across Web, Practitioner Communities (HN, Reddit),
    and Academic literature (arXiv) concurrently with fault-tolerant aggregation.
    """

    def __init__(
        self,
        deoptimizer: Optional[QueryDeoptimizer] = None,
        web_provider: Optional[SearchProvider] = None,
        hn_provider: Optional[SearchProvider] = None,
        reddit_provider: Optional[SearchProvider] = None,
        arxiv_provider: Optional[SearchProvider] = None,
    ):
        self.deoptimizer = deoptimizer or QueryDeoptimizer()
        self.web = web_provider or DuckDuckGoProvider(source_type="web", name="DuckDuckGo")
        self.hn = hn_provider or HackerNewsProvider()
        self.reddit = reddit_provider or RedditProvider()
        self.arxiv = arxiv_provider or ArxivProvider()

    def _normalize_url(self, raw_url: str) -> str:
        """Strip tracking query parameters (utm_*, ref, etc.) and trailing slash."""
        if not raw_url:
            return ""
        parsed = urlparse(raw_url)
        # Strip tracking queries
        filtered_queries = [
            q for q in parsed.query.split("&")
            if not any(q.startswith(prefix) for prefix in ("utm_", "ref=", "source="))
        ]
        new_query = "&".join(filtered_queries)
        normalized_path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc.lower(), normalized_path, parsed.params, new_query, ""))

    async def search(
        self,
        prompt: str,
        mode: SearchMode = SearchMode.ALL,
        max_per_source: int = 5,
        timeout: float = 12.0
    ) -> List[SearchResult]:
        """
        Executes parallel multi-source search across selected provider modes.
        Deduplicates results by canonical URL and merges them ranked by relevance.
        """
        deoptimized: DeoptimizedQueries = self.deoptimizer.deoptimize(prompt)
        tasks: List[asyncio.Task] = []

        if mode in (SearchMode.ALL, SearchMode.GENERAL):
            # Dispatch general query and exact token query to catch un-SEO technical pages
            tasks.append(asyncio.create_task(self.web.search(deoptimized.general_query, max_results=max_per_source)))
            if deoptimized.exact_token_query != deoptimized.general_query:
                tasks.append(asyncio.create_task(self.web.search(deoptimized.exact_token_query, max_results=max_per_source)))

        if mode in (SearchMode.ALL, SearchMode.COMMUNITY):
            # Dispatch to Hacker News and Reddit
            tasks.append(asyncio.create_task(self.hn.search(prompt, max_results=max_per_source)))
            tasks.append(asyncio.create_task(self.reddit.search(prompt, max_results=max_per_source)))

        if mode in (SearchMode.ALL, SearchMode.ACADEMIC):
            # Dispatch to arXiv
            tasks.append(asyncio.create_task(self.arxiv.search(deoptimized.academic_query, max_results=max_per_source)))

        # Run tasks with asyncio.wait so slow providers do not cancel completed fast results
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Cancel any pending/slow tasks
        for p in pending:
            p.cancel()

        # Deduplicate and sort
        seen_urls: Set[str] = set()
        deduped_results: List[SearchResult] = []

        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                continue
            batch = task.result()
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, SearchResult):
                    continue
                canonical = self._normalize_url(item.url)
                if canonical and canonical in seen_urls:
                    continue
                if canonical:
                    seen_urls.add(canonical)
                deduped_results.append(item)

        # Sort: items with higher score, preserving multi-source representation
        deduped_results.sort(key=lambda r: r.score, reverse=True)
        return deduped_results
