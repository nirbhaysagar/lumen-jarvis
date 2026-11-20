import asyncio
import os
import re
from typing import List, Tuple, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
from pydantic import BaseModel, Field
import httpx
from bs4 import BeautifulSoup


class ExtractedDocument(BaseModel):
    url: str
    title: str
    content: str
    outbound_links: List[str] = Field(default_factory=list)
    has_injection_risk: bool = False
    quarantined_blocks: List[str] = Field(default_factory=list)
    source_type: str = "web"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PromptInjectionShield:
    """
    Defends Lumen's reasoning loop against indirect prompt injection.
    Quarantines system prompt overrides, jailbreaks, and exfiltration attempts
    hidden in external web pages or PDFs before they reach the main context.
    """

    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+(instructions|commands|prompts|rules)", re.IGNORECASE),
        re.compile(r"disregard\s+(all\s+)?(previous|prior)\s+(instructions|directives)", re.IGNORECASE),
        re.compile(r"(you\s+are\s+now|act\s+as)\s+(an?\s+unrestricted|in\s+developer\s+mode|dan\b)", re.IGNORECASE),
        re.compile(r"(system\s+prompt|system\s+instruction)\s*[:=]", re.IGNORECASE),
        re.compile(r"(<\|im_start\|>|<\|system\|>|\[INST\]\s*<<SYS>>)", re.IGNORECASE),
        re.compile(r"(send|exfiltrate|post)\s+(all\s+)?(passwords|api[_\s]keys|environment\s+variables|data)\s+to", re.IGNORECASE),
        re.compile(r"execute\s+command\s*:\s*(rm\s+-rf|drop\s+table|curl\s+.*\|\s*bash)", re.IGNORECASE),
    ]

    IMAGE_BEACON_PATTERN = re.compile(r"!\[.*?\]\((https?://[^)]+?\?(exfil|token|leak|user|secret)=[^)]+?)\)", re.IGNORECASE)

    def scan_and_sanitize(self, raw_text: str) -> Tuple[str, bool, List[str]]:
        """
        Scans text for prompt injection vectors.
        Wraps detected payloads in quarantine boundaries and neutralizes exfiltration beacons.
        """
        quarantined: List[str] = []
        sanitized = raw_text

        # 1. Neutralize exfiltration image beacons
        def _strip_beacon(match):
            beacon_url = match.group(1)
            quarantined.append(f"Image Beacon: {beacon_url}")
            return f"[NEUTRALIZED_EXFILTRATION_BEACON: {urlparse(beacon_url).netloc}]"

        sanitized = self.IMAGE_BEACON_PATTERN.sub(_strip_beacon, sanitized)

        # 2. Neutralize instruction overrides
        for pattern in self.INJECTION_PATTERNS:
            matches = list(pattern.finditer(sanitized))
            for m in reversed(matches):
                snippet = m.group(0)
                quarantined.append(snippet)
                start, end = m.span()
                sanitized = (
                    sanitized[:start] +
                    f"\n[SECURITY QUARANTINE: POTENTIAL INJECTION ATTEMPT '{snippet}']\n" +
                    sanitized[end:]
                )

        has_risk = len(quarantined) > 0
        return sanitized, has_risk, quarantined


class ContentExtractor:
    """
    Unified ingestion engine for web pages, PDFs, and local documents.
    Features:
    1. Multi-tier HTML extraction: fast stealth HTTP -> dynamic Crawl4AI fallback.
    2. Document conversion: Microsoft MarkItDown for PDFs, DOCX, and local files.
    3. Outbound link parsing for recursive deep research graph walking.
    4. Defense-in-depth prompt injection sanitization.
    """

    def __init__(self, shield: Optional[PromptInjectionShield] = None):
        self.shield = shield or PromptInjectionShield()
        self._markitdown = None

    def _get_markitdown(self):
        if self._markitdown is None:
            from markitdown import MarkItDown
            self._markitdown = MarkItDown()
        return self._markitdown

    def _clean_soup(self, soup: BeautifulSoup) -> str:
        """Strip non-content elements and extract structured text."""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "iframe", "form"]):
            tag.decompose()

        # Extract text preserving paragraphs and headers
        lines = []
        for elem in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code", "table"]):
            text = elem.get_text(separator=" ", strip=True)
            if not text:
                continue
            if elem.name in ("h1", "h2", "h3", "h4"):
                level = int(elem.name[1])
                lines.append(f"\n{'#' * level} {text}\n")
            elif elem.name == "li":
                lines.append(f"- {text}")
            elif elem.name in ("pre", "code"):
                lines.append(f"```\n{text}\n```")
            else:
                lines.append(f"{text}\n")

        return "\n".join(lines).strip()

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Extract valid absolute HTTP/HTTPS links for crawler exploration."""
        links: List[str] = []
        seen = set()
        base_domain = urlparse(base_url).netloc

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            # Strip anchors
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean_url += f"?{parsed.query}"
            if clean_url not in seen:
                seen.add(clean_url)
                links.append(clean_url)

        return links

    async def extract_from_url(self, url: str, max_chars: int = 15000) -> ExtractedDocument:
        """
        Extracts content from a URL (HTML or PDF).
        Tiers:
        1. Fast HTTP + BeautifulSoup
        2. Dynamic Crawl4AI if page is an SPA with empty initial HTML
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
        }

        # Check for PDF direct link
        if url.lower().endswith(".pdf"):
            return await self._extract_remote_pdf(url, headers, max_chars)

        raw_html = ""
        final_url = url
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12.0) as client:
                resp = await client.get(url)
                final_url = str(resp.url)
                content_type = resp.headers.get("content-type", "").lower()
                if "application/pdf" in content_type:
                    return await self._extract_pdf_bytes(resp.content, final_url, max_chars)
                raw_html = resp.text
        except Exception as e:
            return ExtractedDocument(
                url=url,
                title="Fetch Failed",
                content=f"Error fetching URL: {str(e)}",
                source_type="web"
            )

        soup = BeautifulSoup(raw_html, "html.parser")
        title = soup.title.string.strip() if (soup.title and soup.title.string) else "Untitled Page"
        clean_text = self._clean_soup(soup)
        outbound_links = self._extract_links(soup, final_url)

        # Check if page is an empty client-rendered Single Page Application (SPA)
        if len(clean_text) < 200 and not url.endswith((".txt", ".json", ".xml")):
            crawl4ai_text = await self._crawl4ai_fallback(final_url)
            if crawl4ai_text and len(crawl4ai_text) > len(clean_text):
                clean_text = crawl4ai_text

        # Sanitize with Prompt Injection Shield
        sanitized_text, has_risk, quarantined = self.shield.scan_and_sanitize(clean_text[:max_chars])

        return ExtractedDocument(
            url=final_url,
            title=title,
            content=sanitized_text,
            outbound_links=outbound_links[:50],
            has_injection_risk=has_risk,
            quarantined_blocks=quarantined,
            source_type="web"
        )

    async def _crawl4ai_fallback(self, url: str) -> Optional[str]:
        """Fallback to Crawl4AI for dynamic SPAs."""
        try:
            from crawl4ai import AsyncWebCrawler
            async with AsyncWebCrawler(verbose=False) as crawler:
                res = await crawler.arun(url=url)
                if res and res.markdown:
                    return res.markdown
        except Exception:
            pass
        return None

    async def _extract_remote_pdf(self, url: str, headers: dict, max_chars: int) -> ExtractedDocument:
        """Download remote PDF and extract via MarkItDown."""
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
                resp = await client.get(url)
                return await self._extract_pdf_bytes(resp.content, url, max_chars)
        except Exception as e:
            return ExtractedDocument(
                url=url,
                title="PDF Download Failed",
                content=f"Error downloading PDF: {str(e)}",
                source_type="document"
            )

    async def _extract_pdf_bytes(self, content_bytes: bytes, url: str, max_chars: int) -> ExtractedDocument:
        """Process PDF bytes with MarkItDown."""
        import tempfile
        def _convert():
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content_bytes)
                tmp_path = tmp.name
            try:
                mid = self._get_markitdown()
                result = mid.convert(tmp_path)
                return result.text_content
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        text = await asyncio.to_thread(_convert)
        sanitized, has_risk, quarantined = self.shield.scan_and_sanitize(text[:max_chars])
        filename = os.path.basename(urlparse(url).path) or "Document.pdf"

        return ExtractedDocument(
            url=url,
            title=f"[PDF] {filename}",
            content=sanitized,
            outbound_links=[],
            has_injection_risk=has_risk,
            quarantined_blocks=quarantined,
            source_type="document"
        )

    def extract_from_file(self, file_path: str, max_chars: int = 15000) -> ExtractedDocument:
        """Extract text from local files (PDF, DOCX, TXT, MD) using MarkItDown."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        mid = self._get_markitdown()
        result = mid.convert(file_path)
        raw_text = result.text_content or ""
        sanitized, has_risk, quarantined = self.shield.scan_and_sanitize(raw_text[:max_chars])

        return ExtractedDocument(
            url=f"file://{os.path.abspath(file_path)}",
            title=os.path.basename(file_path),
            content=sanitized,
            outbound_links=[],
            has_injection_risk=has_risk,
            quarantined_blocks=quarantined,
            source_type="document"
        )
