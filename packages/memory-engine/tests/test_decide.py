"""
Unit tests for the pure decide() function (Tier A vs Tier B, create/supersede/reaffirm/stage).
"""
import uuid
import pytest
from app.domain.decide import CandidateMemory, ExistingMemory, decide


def test_decide_table_20_cases():
    sub_id = uuid.uuid4()
    obj_id1 = uuid.uuid4()
    obj_id2 = uuid.uuid4()
    lineage1 = uuid.uuid4()
    mem1_id = uuid.uuid4()

    existing_active = ExistingMemory(
        id=mem1_id,
        lineage_id=lineage1,
        version=1,
        statement="User uses PostgreSQL 16.",
        subject_id=sub_id,
        predicate="uses_database",
        object_id=obj_id1,
        object_value="PostgreSQL 16",
        cardinality="one",
        polarity=True,
        asserted_by="user",
        status="active",
    )

    cases = [
        # 1. New fact, no existing
        (
            CandidateMemory("User prefers dark mode.", sub_id, "prefers_theme", None, "dark", confidence=0.8),
            None, [],
            ("create", "A", "new"),
        ),
        # 2. Same-speaker cardinality=one update -> supersede
        (
            CandidateMemory("User uses PostgreSQL 17.", sub_id, "uses_database", obj_id2, "PostgreSQL 17", cardinality="one", confidence=0.9),
            existing_active, [],
            ("supersede", "A", "same_speaker_update"),
        ),
        # 3. Cross-speaker conflict -> stage with contradicts
        (
            CandidateMemory("User uses MongoDB.", sub_id, "uses_database", obj_id2, "MongoDB", cardinality="one", asserted_by="Rahul", confidence=0.8),
            None, [existing_active],
            ("stage", "B", "cross_speaker_conflict"),
        ),
        # 4. Low confidence -> stage
        (
            CandidateMemory("User might like Redis.", sub_id, "prefers_tool", None, "Redis", confidence=0.5),
            None, [],
            ("stage", "B", "low_confidence"),
        ),
        # 5. Inference -> stage
        (
            CandidateMemory("User is a backend engineer.", sub_id, "has_role", None, "backend", epistemic="inference", confidence=0.6),
            None, [],
            ("stage", "B", "inference"),
        ),
        # 6. Value not in quote (grounding failed) -> stage
        (
            CandidateMemory("User uses Docker.", sub_id, "uses_tool", None, "Docker", grounding_passed=False),
            None, [],
            ("stage", "B", "value_not_in_quote"),
        ),
        # 7. Restated fact (high similarity) -> reaffirm
        (
            CandidateMemory("User uses PostgreSQL 16.", sub_id, "uses_database", obj_id1, "PostgreSQL 16", confidence=0.9),
            existing_active, [],
            ("reaffirm", "A", "reaffirm"),
        ),
        # 8. Negation supersedes positive
        (
            CandidateMemory("User does not use PostgreSQL 16.", sub_id, "uses_database", obj_id1, "PostgreSQL 16", polarity=False, confidence=0.85),
            existing_active, [],
            ("supersede", "A", "negation_supersede"),
        ),
        # 9. Many-valued preference without change cue -> create new
        (
            CandidateMemory("User also likes Python.", sub_id, "likes_language", None, "Python", cardinality="many", confidence=0.8),
            existing_active, [],
            ("create", "A", "new"),
        ),
        # 10. Many-valued with change cue -> supersede
        (
            CandidateMemory("User switched to Rust.", sub_id, "uses_database", obj_id2, "Rust", cardinality="many", change_cue=True, confidence=0.85),
            existing_active, [],
            ("supersede", "A", "same_speaker_update"),
        ),
        # 11. Work lane decision
        (
            CandidateMemory("We decided on FastAPI.", sub_id, "selected_framework", None, "FastAPI", lane="work", confidence=0.9),
            None, [],
            ("create", "A", "new"),
        ),
        # 12. Secret sensitivity
        (
            CandidateMemory("My OTP is 998811.", sub_id, "has_otp", None, "998811", sensitivity="secret", confidence=0.9),
            None, [],
            ("create", "A", "new"),
        ),
        # 13. Reaffirm with slight case variation
        (
            CandidateMemory("user uses postgresql 16.", sub_id, "uses_database", obj_id1, "PostgreSQL 16", confidence=0.8),
            existing_active, [],
            ("reaffirm", "A", "reaffirm"),
        ),
        # 14. Multiple other speakers conflicting
        (
            CandidateMemory("User uses CockroachDB.", sub_id, "uses_database", None, "CockroachDB", asserted_by="Bob", confidence=0.85),
            None, [existing_active],
            ("stage", "B", "cross_speaker_conflict"),
        ),
        # 15. Same speaker agreement with others existing
        (
            CandidateMemory("User uses PostgreSQL 16.", sub_id, "uses_database", obj_id1, "PostgreSQL 16", asserted_by="user", confidence=0.85),
            existing_active, [existing_active],
            ("reaffirm", "A", "reaffirm"),
        ),
        # 16. Threshold boundary: confidence exactly 0.70 -> A/create
        (
            CandidateMemory("User knows C++.", sub_id, "knows_lang", None, "C++", confidence=0.70),
            None, [],
            ("create", "A", "new"),
        ),
        # 17. Threshold boundary: confidence 0.69 -> B/stage
        (
            CandidateMemory("User knows C++.", sub_id, "knows_lang", None, "C++", confidence=0.69),
            None, [],
            ("stage", "B", "low_confidence"),
        ),
        # 18. Other speaker same fact (no conflict) -> create
        (
            CandidateMemory("User uses PostgreSQL 16.", sub_id, "uses_database", obj_id1, "PostgreSQL 16", asserted_by="Alice", confidence=0.85),
            None, [existing_active],
            ("create", "A", "new"),
        ),
        # 19. Cardinality one change cue false but predicate is cardinality one
        (
            CandidateMemory("User primary database is SQLite.", sub_id, "uses_database", None, "SQLite", cardinality="one", change_cue=False, confidence=0.85),
            existing_active, [],
            ("supersede", "A", "same_speaker_update"),
        ),
        # 20. Code lane fact
        (
            CandidateMemory("Repository uses asyncpg driver.", sub_id, "uses_driver", None, "asyncpg", lane="code", confidence=0.9),
            None, [],
            ("create", "A", "new"),
        ),
        # 21. Cross-speaker conflict with a different predicate string
        (
            CandidateMemory(
                "Rahul says Lumen should migrate to MongoDB for document flexibility.",
                sub_id, "migrate_to", None, "MongoDB",
                asserted_by="Rahul", confidence=0.9,
            ),
            None, [existing_active],
            ("stage", "B", "cross_speaker_conflict"),
        ),
    ]

    assert len(cases) >= 20
    for i, (cand, own, others, expected) in enumerate(cases, 1):
        dec = decide(cand, own, others)
        exp_action, exp_tier, exp_reason = expected
        assert dec.action == exp_action, f"Case {i} action expected {exp_action}, got {dec.action}"
        assert dec.tier == exp_tier, f"Case {i} tier expected {exp_tier}, got {dec.tier}"
        assert dec.reason == exp_reason, f"Case {i} reason expected {exp_reason}, got {dec.reason}"
