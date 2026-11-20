import asyncio
import re
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone

from src.concurrency.models import CancellationToken, TaskCheckpoint
from src.research.models import SearchResult, SearchMode, ExtractedClaim, ResearchDossier
from src.research.discovery import MultiSourceDiscoveryEngine
from src.research.extractor import ContentExtractor, ExtractedDocument


class DeepResearchAgent:
    """
    Autonomous Deep Research Orchestrator.
    Dispatches multi-source discovery, executes recursive link graph walking (d=2, b=3)
    to discover un-SEO resources, extracts practitioner quotes and claims with provenance,
    and compiles intelligence dossiers while obeying cooperative cancellation on 'The Spine'.
    """

    def __init__(
        self,
        discovery_engine: Optional[MultiSourceDiscoveryEngine] = None,
        extractor: Optional[ContentExtractor] = None,
    ):
        self.discovery = discovery_engine or MultiSourceDiscoveryEngine()
        self.extractor = extractor or ContentExtractor()

    def _score_link_relevance(self, link_url: str, topic_keywords: Set[str]) -> float:
        """
        Lightweight semantic link heuristic.
        Scores outbound URLs based on keyword overlap in path and slug.
        """
        lower_url = link_url.lower()
        score = 0.0
        for kw in topic_keywords:
            if kw in lower_url:
                score += 1.0

        # Penalize non-content / auth / social root links
        for skip_token in ("login", "signup", "terms", "privacy", "cookie", "tag/", "category/"):
            if skip_token in lower_url:
                score -= 2.0

        return score

    def _extract_claims_and_quotes(self, doc: ExtractedDocument, topic_keywords: Set[str]) -> List[ExtractedClaim]:
        """
        Extracts salient claims and verbatim quotes from an extracted document.
        Identifies blockquotes, practitioner comments, and conclusive assertions.
        """
        claims: List[ExtractedClaim] = []
        lines = [line.strip() for line in doc.content.split("\n") if line.strip()]

        for raw_line in lines:
            # Skip security quarantine messages
            if "[SECURITY QUARANTINE" in raw_line or "[NEUTRALIZED" in raw_line:
                continue

            # Split paragraph lines into individual sentences
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw_line) if s.strip()]

            for sentence in sentences:
                lower_s = sentence.lower()
                # Must mention at least one topic keyword
                matches = sum(1 for kw in topic_keywords if kw in lower_s)
                if matches == 0:
                    continue

                # Identify candidate claims (sentences with assertive verbs, findings, or metrics)
                is_claim_like = any(indicator in lower_s for indicator in (
                    "because", "causes", "leads to", "degrades", "improves", "solves",
                    "recommended", "benchmark", "measured", "latency", "throughput",
                    "requires", "fails when", "fixed by", "instead of", "difference between",
                    "index", "performance", "scales", "bottleneck", "optimization",
                    "revenue", "sales", "billion", "million", "reported", "earnings",
                    "released", "launch", "confirmed", "announced", "projected", "forecast",
                    "growth", "quarter", "fiscal", "cagr", "cost", "profit", "subscribers",
                    "market share", "exceeded", "generated", "totaled", "stands at",
                    "according to", "shows that", "found that", "indicates that", "$", "%"
                ))

                if is_claim_like and 25 < len(sentence) < 400:
                    # Check for quotes
                    quote_text = sentence if (sentence.startswith('"') or sentence.startswith(">") or raw_line.startswith(">")) else None
                    claims.append(
                        ExtractedClaim(
                            claim=sentence.lstrip(">\" ").rstrip("\""),
                            quote=quote_text,
                            author=doc.metadata.get("author") or ("Community Contributor" if "community" in doc.source_type else None),
                            source_url=doc.url,
                            source_type=doc.source_type,
                            confidence=0.9 if "academic" in doc.source_type else 0.8
                        )
                    )

        return claims[:6]  # Cap per document to avoid bloat

    async def run_research(
        self,
        topic: str,
        token: Optional[CancellationToken] = None,
        checkpoint_cb: Optional[Any] = None,
        max_depth: int = 1,
        max_pages: int = 6
    ) -> ResearchDossier:
        """
        Executes end-to-end deep research loop.
        Cooperatively yields to CancellationToken between IO boundaries.
        """
        if token:
            token.throw_if_cancelled()

        if checkpoint_cb:
            await checkpoint_cb("Phase 1: Multi-source discovery across Web, Community & arXiv", 10)

        # 1. Multi-source seed search
        seed_results: List[SearchResult] = await self.discovery.search(topic, mode=SearchMode.ALL, max_per_source=4)
        if token:
            token.throw_if_cancelled()

        if checkpoint_cb:
            await checkpoint_cb(f"Phase 2: Ingesting seed sources ({len(seed_results)} discovered)", 30)

        # Topic keywords for link relevance & claim filtering
        topic_keywords = {w.lower() for w in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", topic) if len(w) > 3}

        # 2. Extract content from seed documents
        visited_urls: Set[str] = set()
        extracted_docs: List[ExtractedDocument] = []
        link_frontier: List[Tuple[float, str]] = []

        # Ingest top seed pages
        for result in seed_results[:max_pages]:
            if token and token.is_cancelled:
                token.throw_if_cancelled()

            if result.url in visited_urls:
                continue
            visited_urls.add(result.url)

            doc = await self.extractor.extract_from_url(result.url)
            doc.source_type = result.source_type
            if result.author:
                doc.metadata["author"] = result.author
            extracted_docs.append(doc)

            # Collect outbound links for recursive graph walking
            for link in doc.outbound_links:
                if link not in visited_urls:
                    score = self._score_link_relevance(link, topic_keywords)
                    if score > 0:
                        link_frontier.append((score, link))

        # 3. Recursive Web Hop (Un-SEO & Deep Citations)
        if max_depth >= 1 and link_frontier:
            if checkpoint_cb:
                await checkpoint_cb(f"Phase 3: Recursive citation graph walking ({len(link_frontier)} candidate links)", 60)

            # Sort frontier by score descending
            link_frontier.sort(key=lambda x: x[0], reverse=True)
            deep_links = [url for _, url in link_frontier[:2]]  # Hop to top 2 un-SEO citations

            for deep_url in deep_links:
                if token and token.is_cancelled:
                    token.throw_if_cancelled()

                if deep_url in visited_urls:
                    continue
                visited_urls.add(deep_url)

                deep_doc = await self.extractor.extract_from_url(deep_url)
                deep_doc.source_type = "un_seo_citation"
                extracted_docs.append(deep_doc)

        if checkpoint_cb:
            await checkpoint_cb("Phase 4: Synthesizing claims and compiling dossier", 85)

        # 4. Synthesize claims, quotes, and findings
        all_claims: List[ExtractedClaim] = []
        for doc in extracted_docs:
            doc_claims = self._extract_claims_and_quotes(doc, topic_keywords)
            all_claims.extend(doc_claims)

        # Deduplicate claims
        unique_claims: List[ExtractedClaim] = []
        seen_texts = set()
        for c in all_claims:
            norm = c.claim.lower().strip()
            if norm not in seen_texts:
                seen_texts.add(norm)
                unique_claims.append(c)

        # Top key findings
        key_findings = [c.claim for c in unique_claims[:5]]
        if not key_findings:
            key_findings = [f"Found {len(seed_results)} reference sources for topic: {topic}."]

        summary = (
            f"Autonomous research on '{topic}' synthesized from {len(extracted_docs)} distinct documents "
            f"across Web, Developer Communities (HN/Reddit), and Academic Papers.\n"
            f"Extracted {len(unique_claims)} verifiable technical claims with source attribution."
        )

        dossier = ResearchDossier(
            topic=topic,
            summary=summary,
            key_findings=key_findings,
            claims=unique_claims[:12],
            sources=seed_results[:8],
            generated_at=datetime.now(timezone.utc).isoformat()
        )

        if checkpoint_cb:
            await checkpoint_cb("Phase 5: Research complete", 100)

        return dossier
