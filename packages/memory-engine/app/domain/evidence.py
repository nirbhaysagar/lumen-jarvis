"""
Verbatim Evidence Span Verification Gate.
Locates evidence quotes inside raw events.content using exact substring matching
and RapidFuzz partial_ratio >= 90 fuzzy alignment.
Returns exact character offsets [char_start, char_end] and verification flag.
"""
from __future__ import annotations
import re
from typing import Tuple, Optional
from rapidfuzz import fuzz


def find_evidence_span(raw_text: str, quote: str, threshold: float = 90.0) -> Tuple[Optional[int], Optional[int], bool]:
    """
    Locates quote inside raw_text.
    Returns: (char_start, char_end, is_verified)
    """
    clean_quote = quote.strip()
    if not clean_quote or not raw_text:
        return None, None, False

    # 1. Exact match search
    exact_idx = raw_text.find(clean_quote)
    if exact_idx != -1:
        return exact_idx, exact_idx + len(clean_quote), True

    # 1b. Case-insensitive exact match
    lower_idx = raw_text.lower().find(clean_quote.lower())
    if lower_idx != -1:
        return lower_idx, lower_idx + len(clean_quote), True

    # 2. Sliding window search with RapidFuzz
    quote_len = len(clean_quote)
    text_len = len(raw_text)
    if quote_len > text_len:
        # Check overall similarity
        ratio = fuzz.ratio(clean_quote.lower(), raw_text.lower())
        if ratio >= threshold:
            return 0, text_len, True
        return None, None, False

    best_ratio = 0.0
    best_span = (None, None)

    # Search window around quote length (+- 20% slack)
    window_sizes = [quote_len, int(quote_len * 0.9), int(quote_len * 1.1)]
    for w_size in window_sizes:
        if w_size <= 0 or w_size > text_len:
            continue
        for start in range(0, text_len - w_size + 1, max(1, quote_len // 10)):
            end = start + w_size
            candidate = raw_text[start:end]
            score = fuzz.ratio(clean_quote.lower(), candidate.lower())
            if score > best_ratio:
                best_ratio = score
                best_span = (start, end)
                if best_ratio >= 98.0:
                    break
        if best_ratio >= 98.0:
            break

    if best_ratio >= threshold and best_span[0] is not None:
        return best_span[0], best_span[1], True

    return None, None, False
