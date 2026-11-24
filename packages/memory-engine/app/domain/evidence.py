"""
Verbatim Evidence Span Verification Gate.
Locates evidence quotes inside raw events.content using exact substring matching
and RapidFuzz partial_ratio_alignment (score >= 90.0).
Returns exact character offsets [char_start, char_end] and verification flag.
Guarantees round-trip invariant: raw_text[char_start:char_end] is the exact stored quote.
"""
from __future__ import annotations
from typing import Tuple, Optional
from rapidfuzz.fuzz import partial_ratio_alignment, partial_ratio


def find_evidence_span(
    raw_text: str,
    quote: str,
    threshold: float = 90.0,
) -> Tuple[Optional[int], Optional[int], bool]:
    """
    Locates quote inside raw_text using exact match, case-insensitive match,
    and rapidfuzz.fuzz.partial_ratio_alignment.
    Rejects quotes shorter than 8 characters unless equal to whole utterance.
    Returns: (char_start, char_end, is_verified)
    """
    clean_quote = quote.strip()
    if not clean_quote or not raw_text:
        return None, None, False

    # Quote cannot be longer than the raw text
    if len(clean_quote) > len(raw_text):
        return None, None, False

    # Short quote rejection
    if len(clean_quote) < 8 and clean_quote != raw_text.strip():
        return None, None, False

    # 1. Exact match search
    exact_idx = raw_text.find(clean_quote)
    if exact_idx != -1:
        return exact_idx, exact_idx + len(clean_quote), True

    # 2. Case-insensitive exact match
    lower_idx = raw_text.lower().find(clean_quote.lower())
    if lower_idx != -1:
        return lower_idx, lower_idx + len(clean_quote), True

    # 3. RapidFuzz partial_ratio_alignment
    alignment = partial_ratio_alignment(clean_quote.lower(), raw_text.lower())
    if alignment.score >= threshold:
        start = alignment.dest_start
        end = alignment.dest_end
        if start < end:
            return start, end, True

    return None, None, False


def check_grounding(quote_text: str, key_value: str | int | float | None) -> bool:
    """
    Checks that the key value / assertion object actually appears in the quote.
    Uses partial_ratio >= 80.
    """
    if key_value is None:
        return True
    str_val = str(key_value).strip().lower()
    if not str_val:
        return True
    
    # Direct substring
    if str_val in quote_text.lower():
        return True
    
    # Fuzzy ratio
    score = partial_ratio(str_val, quote_text.lower())
    return score >= 80.0
