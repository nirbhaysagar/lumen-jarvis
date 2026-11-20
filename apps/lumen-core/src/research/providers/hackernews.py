import html
import re
from typing import List
import httpx
from src.research.models import SearchResult
from src.research.providers.base import SearchProvider


class HackerNewsProvider(SearchProvider):
    """
    Direct interface to Hacker News via Algolia's official Search API.
    Retrieves authentic senior practitioner discussions, critiques, and architectural debates.
    """

    BASE_URL = "https://hn.algolia.com/api/v1/search"

    @property
    def name(self) -> str:
        return "HackerNews"

    @property
    def source_type(self) -> str:
        return "community_hn"

    def _clean_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        unescaped = html.unescape(raw_html)
        clean = re.sub(r"<[^>]+>", " ", unescaped)
        return " ".join(clean.split())

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        headers = {"User-Agent": "LumenResearchAgent/1.0 (https://cerebrodynamics.org)"}
        params = {
            "query": query,
            "hitsPerPage": max_results,
            "tags": "(story,comment)"
        }
        results: List[SearchResult] = []

        try:
            async with httpx.AsyncClient(headers=headers, timeout=8.0) as client:
                response = await client.get(self.BASE_URL, params=params)
                if response.status_code != 200:
                    return results

                data = response.json()
                hits = data.get("hits", [])

                for hit in hits:
                    obj_id = hit.get("objectID")
                    hn_url = f"https://news.ycombinator.com/item?id={obj_id}"
                    
                    # Handle stories vs comments
                    is_comment = "comment_text" in hit and bool(hit.get("comment_text"))
                    title = hit.get("title") or hit.get("story_title") or f"HN Discussion #{obj_id}"
                    snippet_raw = hit.get("comment_text") or hit.get("story_text") or hit.get("title") or ""
                    snippet = self._clean_html(snippet_raw)

                    external_url = hit.get("url") or hit.get("story_url")
                    final_url = external_url if (external_url and not is_comment) else hn_url

                    author = hit.get("author")
                    created_at = hit.get("created_at")
                    points = hit.get("points") or 0

                    results.append(
                        SearchResult(
                            title=title,
                            url=final_url,
                            snippet=snippet[:400] if snippet else title,
                            source_type=self.source_type,
                            author=f"@{author}" if author else None,
                            published_date=created_at,
                            score=1.0 + (min(points, 500) / 1000.0),
                            metadata={
                                "hn_item_id": obj_id,
                                "points": points,
                                "is_comment": is_comment,
                                "hn_discussion_url": hn_url
                            }
                        )
                    )
        except Exception:
            # Resilient fallback: return whatever succeeded or empty on network fault
            pass

        return results
