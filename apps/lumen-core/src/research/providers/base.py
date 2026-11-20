from abc import ABC, abstractmethod
from typing import List
from src.research.models import SearchResult


class SearchProvider(ABC):
    """
    Abstract interface for search and discovery providers.
    Every provider must return a normalized List[SearchResult].
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        pass

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Category string: web, community_hn, community_reddit, academic_arxiv, etc."""
        pass

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """Execute query and return normalized results."""
        pass
