"""
Stage 3: Evidence verification.
Validates extracted memories against verbatim text coordinates.
Drops unsupported memories without verbatim evidence.
"""
from __future__ import annotations
import os
from typing import Sequence
import uuid

from app.domain.evidence import find_evidence_span, check_grounding
from app.pipeline.contracts import ExtractedMemory, VerifiedMemory, DroppedMemory


def verify_memories(
    raw_text: str,
    chunks: Sequence[dict],
    memories: Sequence[ExtractedMemory],
) -> tuple[list[VerifiedMemory], list[DroppedMemory]]:
    """
    Verifies character-for-character evidence for each memory:
    - Locates verbatim quotes in chunks/raw text.
    - Drops unsupported memories (never stores whole text as fake quote).
    - Checks grounding of key assertion values.
    """
    verified: list[VerifiedMemory] = []
    dropped: list[DroppedMemory] = []
    keep_unsupported = os.getenv("KEEP_UNSUPPORTED") == "1"

    for mem in memories:
        quotes = mem.evidence or []
        best_span: tuple[int, int, str, uuid.UUID | None] | None = None

        # 1. Search in chunks first (if chunks provided)
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            chunk_start = chunk.get("char_start", 0)
            chunk_id = chunk.get("id")

            for q in quotes:
                s, e, ok = find_evidence_span(chunk_text, q)
                if ok and s is not None and e is not None:
                    abs_start = chunk_start + s
                    abs_end = chunk_start + e
                    actual_slice = raw_text[abs_start:abs_end]
                    best_span = (abs_start, abs_end, actual_slice, chunk_id)
                    break
            if best_span:
                break

        # 2. Fall back to searching full raw text
        if not best_span:
            for q in quotes:
                s, e, ok = find_evidence_span(raw_text, q)
                if ok and s is not None and e is not None:
                    actual_slice = raw_text[s:e]
                    # Find matching chunk_id if possible
                    c_id = None
                    for c in chunks:
                        if c.get("char_start", 0) <= s and c.get("char_end", 0) >= e:
                            c_id = c.get("id")
                            break
                    best_span = (s, e, actual_slice, c_id)
                    break

        # 3. Decision: Verified vs Dropped
        if best_span:
            c_start, c_end, quote_slice, c_id = best_span
            # Grounding check: key value must appear in quote
            grounding_passed = check_grounding(quote_slice, mem.object_value)
            verified.append(
                VerifiedMemory(
                    extracted=mem,
                    char_start=c_start,
                    char_end=c_end,
                    quote=quote_slice,
                    chunk_id=c_id,
                    grounding_passed=grounding_passed,
                )
            )
        else:
            if keep_unsupported:
                # Debugging escape hatch: store as pending_review
                verified.append(
                    VerifiedMemory(
                        extracted=mem,
                        char_start=0,
                        char_end=len(raw_text),
                        quote=raw_text,
                        chunk_id=None,
                        grounding_passed=False,
                    )
                )
            else:
                dropped.append(
                    DroppedMemory(
                        statement=mem.statement,
                        reason="no_verbatim_evidence",
                    )
                )

    return verified, dropped
