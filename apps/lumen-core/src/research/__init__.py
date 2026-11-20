"""
Lumen Research Engine ("The Eyes").
Multi-source web, social, academic, and document discovery adapter.
"""
from src.research.models import SearchResult, SearchMode, DeoptimizedQueries, ExtractedClaim, ResearchDossier
from src.research.deoptimizer import QueryDeoptimizer
from src.research.discovery import MultiSourceDiscoveryEngine
from src.research.extractor import ContentExtractor, PromptInjectionShield, ExtractedDocument, SSRFGuard, SSRFSecurityError
from src.research.agent import DeepResearchAgent

__all__ = [
    "SearchResult",
    "SearchMode",
    "DeoptimizedQueries",
    "ExtractedClaim",
    "ResearchDossier",
    "QueryDeoptimizer",
    "MultiSourceDiscoveryEngine",
    "ContentExtractor",
    "PromptInjectionShield",
    "ExtractedDocument",
    "SSRFGuard",
    "SSRFSecurityError",
    "DeepResearchAgent",
]
