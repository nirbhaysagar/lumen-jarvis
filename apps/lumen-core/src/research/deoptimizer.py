import re
from typing import List, Set
from src.research.models import DeoptimizedQueries


class QueryDeoptimizer:
    """
    Morphs natural language prompts into targeted, de-optimized queries.
    Escapes commercial SEO farms by isolating exact technical tokens,
    injecting anti-commercial exclusions, and targeting discussion/academic forums.
    """

    CONVERSATIONAL_PREFIXES = [
        r"^can you (please )?(search|look up|find|tell me about|explain)",
        r"^please (search|look up|find|explain)",
        r"^(search|look up|find|investigate|research)",
        r"^(what is|what are|how to|how do i|why is|why does|why do)",
        r"^give me (information|details|a summary) (on|about)",
        r"^i want to know (about|how)",
    ]

    STOPWORDS: Set[str] = {
        "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "and", "or", "is", "are", "was", "were", "be", "been", "being", "have", "has",
        "had", "do", "does", "did", "how", "what", "why", "when", "where", "which",
        "this", "that", "these", "those", "can", "could", "should", "would", "about"
    }

    ANTI_SEO_EXCLUSIONS = "-seo -affiliate -sponsored -buy -course -top10"

    def deoptimize(self, prompt: str) -> DeoptimizedQueries:
        """
        Decomposes a user prompt into 4 specialized queries:
        1. general: Core keywords stripped of fluff, with anti-commercial filters.
        2. exact_token: Identifies precise system/code tokens and wraps in quotes.
        3. community: Formatted for Hacker News, Reddit, and practitioner forums.
        4. academic: Formatted for research papers, arXiv, and empirical benchmarks.
        """
        cleaned = prompt.strip()
        for prefix in self.CONVERSATIONAL_PREFIXES:
            cleaned = re.sub(prefix, "", cleaned, flags=re.IGNORECASE).strip()

        cleaned = re.sub(r"[?!.,;]+$", "", cleaned).strip()
        words = cleaned.split()

        # Identify technical tokens: snake_case, dot.notation, camelCase, numbers, hyphenated
        technical_tokens: List[str] = []
        for w in words:
            clean_w = w.strip("\"'()[]{}")
            if not clean_w:
                continue
            is_tech = (
                "_" in clean_w or
                "." in clean_w or
                "-" in clean_w or
                any(c.isdigit() for c in clean_w) or
                (clean_w.isupper() and len(clean_w) > 1) or
                (any(c.isupper() for c in clean_w[1:]) and not clean_w.isupper())
            )
            if is_tech and clean_w.lower() not in self.STOPWORDS:
                technical_tokens.append(clean_w)

        # Core keywords: remove generic stopwords
        core_keywords = [w for w in words if w.lower() not in self.STOPWORDS]
        if not core_keywords:
            core_keywords = words

        core_phrase = " ".join(core_keywords)

        # 1. General query with anti-SEO negative tokens
        general_query = f"{core_phrase} {self.ANTI_SEO_EXCLUSIONS}".strip()

        # 2. Exact token query: quotes technical terms
        if technical_tokens:
            quoted_tokens = " ".join(f'"{t}"' for t in technical_tokens[:4])
            remaining_words = [w for w in core_keywords if w not in technical_tokens]
            exact_token_query = f"{quoted_tokens} {' '.join(remaining_words[:4])}".strip()
        else:
            exact_token_query = f'"{core_phrase}"'

        # 3. Community query
        community_query = f"{core_phrase} (site:news.ycombinator.com OR site:reddit.com OR site:lobste.rs)"

        # 4. Academic query: appends research & benchmark indicators
        academic_query = f"{core_phrase} benchmark evaluation architecture"

        return DeoptimizedQueries(
            original_prompt=prompt,
            general_query=general_query,
            exact_token_query=exact_token_query,
            community_query=community_query,
            academic_query=academic_query
        )
