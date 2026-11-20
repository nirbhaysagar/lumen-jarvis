import asyncio
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List
from src.research.models import SearchResult
from src.research.providers.base import SearchProvider


class ArxivProvider(SearchProvider):
    """
    Academic research paper search via arXiv's official public API.
    Retrieves primary scientific literature, architecture whitepapers, and mathematical proofs.
    """

    BASE_URL = "https://export.arxiv.org/api/query"

    @property
    def name(self) -> str:
        return "arXiv"

    @property
    def source_type(self) -> str:
        return "academic_arxiv"

    def _sync_fetch(self, query: str, max_results: int) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            encoded_q = urllib.parse.quote_plus(query)
            url = f"{self.BASE_URL}?search_query=all:{encoded_q}&start=0&max_results={max_results}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) LumenResearch/1.0"
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as response:
                content = response.read()

            root = ET.fromstring(content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)

            for entry in entries:
                title_elem = entry.find("atom:title", ns)
                summary_elem = entry.find("atom:summary", ns)
                id_elem = entry.find("atom:id", ns)
                published_elem = entry.find("atom:published", ns)

                title = title_elem.text.strip().replace("\n", " ") if title_elem is not None and title_elem.text else "Untitled Paper"
                summary = summary_elem.text.strip().replace("\n", " ") if summary_elem is not None and summary_elem.text else ""
                paper_url = id_elem.text.strip() if id_elem is not None and id_elem.text else ""
                published_date = published_elem.text.strip() if published_elem is not None and published_elem.text else None

                # Extract authors
                authors = []
                for author_node in entry.findall("atom:author", ns):
                    name_node = author_node.find("atom:name", ns)
                    if name_node is not None and name_node.text:
                        authors.append(name_node.text.strip())

                author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "") if authors else "Unknown"

                # Check for PDF link
                pdf_url = paper_url.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in paper_url else paper_url

                results.append(
                    SearchResult(
                        title=f"[Paper] {title}",
                        url=paper_url,
                        snippet=summary[:500] if summary else title,
                        source_type=self.source_type,
                        author=author_str,
                        published_date=published_date,
                        score=1.2,  # Academic primary sources receive slight boost
                        metadata={
                            "pdf_url": pdf_url,
                            "authors": authors,
                            "is_academic_paper": True
                        }
                    )
                )
        except Exception:
            # Arxiv rate-limiting or network issues degrade gracefully
            pass

        return results

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            return await asyncio.to_thread(self._sync_fetch, query, max_results)
        except Exception:
            return []
