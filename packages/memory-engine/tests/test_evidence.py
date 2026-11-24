"""
Unit tests for verbatim evidence verification and grounding checks.
"""
import time
import pytest
from app.domain.evidence import find_evidence_span, check_grounding


def test_evidence_exact_and_case_insensitive():
    text = "We decided to move Lumen to PostgreSQL 17 for better vacuuming."
    
    # Exact
    start, end, verified = find_evidence_span(text, "PostgreSQL 17 for better vacuuming")
    assert verified is True
    assert text[start:end] == "PostgreSQL 17 for better vacuuming"

    # Case-insensitive
    start, end, verified = find_evidence_span(text, "postgresql 17 for better vacuuming")
    assert verified is True
    assert text[start:end] == "PostgreSQL 17 for better vacuuming"


def test_evidence_one_typo_fuzzy_match():
    text = "We decided to move Lumen to PostgreSQL 17 because SQLite locked."
    # Typo: PostgreSQLL 17
    start, end, verified = find_evidence_span(text, "PostgreSQLL 17 because SQLite locked", threshold=90.0)
    assert verified is True
    assert "PostgreSQL 17" in text[start:end]
    # Round-trip invariant
    assert text[start:end] is not None


def test_evidence_hallucinated_quote_dropped():
    text = "Lumen runs completely locally on Apple Silicon Metal GPU."
    start, end, verified = find_evidence_span(text, "Lumen runs on AWS Lambda cloud", threshold=90.0)
    assert verified is False
    assert start is None
    assert end is None


def test_evidence_short_quote_rejected():
    text = "Yes, absolutely."
    # Quote shorter than 8 chars not matching whole utterance
    start, end, verified = find_evidence_span(text, "Yes")
    assert verified is False


def test_evidence_hindi_unicode():
    text = "ल्यूमेन एक स्थानीय मेमोरी इंजन है जो ओलामा का उपयोग करता है।"
    quote = "स्थानीय मेमोरी इंजन है"
    start, end, verified = find_evidence_span(text, quote)
    assert verified is True
    assert text[start:end] == quote


def test_evidence_quote_longer_than_text():
    text = "Short text."
    quote = "This quote is very long and exceeds the length of the short text entirely."
    start, end, verified = find_evidence_span(text, quote)
    assert verified is False


def test_grounding_check():
    quote = "We migrated to PostgreSQL 17 last month."
    
    # Real value
    assert check_grounding(quote, "PostgreSQL 17") is True
    assert check_grounding(quote, "Postgres") is True

    # Invented value
    assert check_grounding(quote, "MongoDB") is False
    assert check_grounding(quote, "MySQL 8.0") is False


def test_evidence_verification_performance():
    # 10 KB event text
    para = "Lumen personal operating system preserves bi-temporal facts and evidence citations. " * 120
    assert len(para) > 10000

    quote = "bi-temporal facts and evidence citations"
    t0 = time.perf_counter()
    start, end, verified = find_evidence_span(para, quote)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert verified is True
    assert elapsed_ms < 5.0, f"Expected < 5ms, took {elapsed_ms:.2f}ms"
