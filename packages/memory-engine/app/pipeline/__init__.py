"""
Lumen Memory Engine 5-Stage Save Pipeline.
Coordinates:
L0 Raw Capture -> [2] Extract -> [3] Verify -> [4] Resolve -> [5] Commit -> Outbox
"""
from __future__ import annotations
import json
import logging
import time
from typing import Any
import uuid
import asyncpg

from app.domain.extraction import MemoryExtractor, ExtractionError
from app.pipeline.contracts import (
    ExtractedEntity,
    ExtractedMemory,
    SaveReport,
)
from app.pipeline.verify import verify_memories
from app.pipeline.resolve import EntityResolver, resolve_decisions
from app.pipeline.commit import commit_pipeline_run

logger = logging.getLogger("lumen.pipeline")


class SavePipeline:
    def __init__(self, pool: asyncpg.Pool, extractor: MemoryExtractor | None = None):
        self.pool = pool
        self.extractor = extractor or MemoryExtractor()

    async def run(self, event_id: uuid.UUID) -> SaveReport:
        """
        Runs stages 2 through 5 of the save pipeline for a captured event:
        [2] Extract (local LLM)
        [3] Verify (Rapidfuzz verbatim evidence & grounding)
        [4] Resolve (Entities & Tier A/B decide)
        [5] Commit (Atomic versioned memories, outbox, audit save_runs)
        """
        stage_ms: dict[str, int] = {}

        async with self.pool.acquire() as conn:
            event_row = await conn.fetchrow(
                """
                SELECT id, content, speaker, actor, source_id, context_id, occurred_at, recorded_at
                FROM events WHERE id = $1
                """,
                event_id,
            )
            if not event_row:
                raise ValueError(f"Event {event_id} not found")

            chunk_rows = await conn.fetch(
                """
                SELECT id, ord, char_start, char_end, text, metadata
                FROM chunks WHERE event_id = $1 ORDER BY ord ASC
                """,
                event_id,
            )

        raw_text = event_row["content"]
        speaker = event_row["speaker"] or event_row["actor"] or "user"
        context_id = event_row["context_id"]
        source_id = event_row["source_id"]
        occurred_at = event_row["occurred_at"]
        chunks = [dict(r) for r in chunk_rows]

        # -------------------------------------------------------------
        # Stage 2: Structured Extraction
        # -------------------------------------------------------------
        t_ext = time.perf_counter()
        captured_at_str = occurred_at.isoformat() if occurred_at else None
        try:
            extracted_data = await self.extractor.extract(
                text=raw_text,
                speaker=speaker,
                captured_at=captured_at_str,
            )
        except ExtractionError as exc:
            logger.warning(f"Extraction failed for event {event_id} [{exc.kind}]: {exc.detail}")
            async with self.pool.acquire() as err_conn:
                async with err_conn.transaction():
                    await err_conn.execute(
                        """
                        INSERT INTO extraction_attempts (
                            event_id, model, attempt, ok, error_kind, error_detail, raw_output, latency_ms
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        event_id,
                        getattr(self.extractor, "model", "qwen2.5:7b-instruct"),
                        exc.attempt,
                        False,
                        exc.kind,
                        exc.detail,
                        exc.raw[:8192],
                        exc.latency_ms,
                    )

                    sys_ev_id = await err_conn.fetchval(
                        """
                        INSERT INTO events (
                            kind, type, actor, context_id, target_id, caused_by, payload
                        ) VALUES (
                            'SYSTEM', 'extract.failed', 'system:extractor', $1, $2, ARRAY[$2]::uuid[], $3
                        ) RETURNING id
                        """,
                        context_id,
                        event_id,
                        json.dumps({
                            "error_kind": exc.kind,
                            "error_detail": exc.detail,
                            "model": getattr(self.extractor, "model", "qwen2.5:7b-instruct"),
                        }),
                    )

                    await err_conn.execute(
                        """
                        INSERT INTO review_queue (
                            kind, memory_ids, entity_ids, reason, proposal, status, created_event
                        ) VALUES (
                            'failed_extraction', '{}', '{}', $1, $2, 'open', $3
                        )
                        """,
                        exc.kind,
                        json.dumps({
                            "event_id": str(event_id),
                            "error": exc.detail,
                            "error_kind": exc.kind,
                            "raw_output": exc.raw[:2000],
                        }),
                        sys_ev_id,
                    )

                    await err_conn.execute(
                        """
                        UPDATE jobs SET status = 'dead', last_error = $2
                        WHERE kind = 'extract' AND event_id = $1
                        """,
                        event_id,
                        exc.detail,
                    )
            raise exc

        stage_ms["extract"] = int((time.perf_counter() - t_ext) * 1000)

        # Record successful attempt in extraction_attempts
        async with self.pool.acquire() as succ_conn:
            await succ_conn.execute(
                """
                INSERT INTO extraction_attempts (
                    event_id, model, attempt, ok, latency_ms
                ) VALUES ($1, $2, $3, $4, $5)
                """,
                event_id,
                getattr(self.extractor, "model", "qwen2.5:7b-instruct"),
                1,
                True,
                stage_ms["extract"],
            )

        # Parse entities
        extracted_entities: list[ExtractedEntity] = []
        for e in extracted_data.get("entities", []):
            extracted_entities.append(
                ExtractedEntity(
                    ref=e.get("ref", "e1"),
                    name=e.get("name", ""),
                    kind=e.get("kind", "other"),
                    aliases=e.get("aliases", []),
                )
            )

        # Parse memories
        extracted_memories: list[ExtractedMemory] = []
        for m in extracted_data.get("memories", []):
            extracted_memories.append(
                ExtractedMemory(
                    ref=m.get("ref", "m1"),
                    type=m.get("type", "fact"),
                    epistemic=m.get("epistemic", "fact"),
                    asserted_by=m.get("asserted_by", speaker),
                    statement=m.get("statement", "").strip(),
                    subject_ref=m.get("subject"),
                    predicate=m.get("predicate"),
                    object_ref=m.get("object"),
                    object_value=m.get("object_value"),
                    attributes=m.get("attributes"),
                    cardinality=m.get("cardinality", "many"),
                    polarity=m.get("polarity", True),
                    valid_from_text=m.get("valid_from_text"),
                    evidence=m.get("evidence", []),
                    confidence=float(m.get("confidence", 0.8)),
                    importance=float(m.get("importance", 0.5)),
                    change_cue=bool(m.get("change_cue", False)),
                    relations=m.get("relations", []),
                    lane=m.get("lane", "personal"),
                    sensitivity=m.get("sensitivity", "normal"),
                    about_speaker=bool(m.get("about_speaker", False)),
                )
            )

        # -------------------------------------------------------------
        # Stage 3: Verbatim Evidence Verification
        # -------------------------------------------------------------
        t_ver = time.perf_counter()
        verified_memories, dropped_memories = verify_memories(
            raw_text=raw_text,
            chunks=chunks,
            memories=extracted_memories,
        )
        stage_ms["verify"] = int((time.perf_counter() - t_ver) * 1000)

        # -------------------------------------------------------------
        # Stage 4 & 5: Resolve and Commit inside single Transaction
        # -------------------------------------------------------------
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Stage 4: Resolve
                t_res = time.perf_counter()
                resolver = EntityResolver(conn, event_id, context_id)
                ref_to_id = await resolver.resolve_entities(extracted_entities)

                resolved_decisions = await resolve_decisions(
                    conn=conn,
                    context_id=context_id,
                    verified_memories=verified_memories,
                    ref_to_id=ref_to_id,
                )
                stage_ms["resolve"] = int((time.perf_counter() - t_res) * 1000)

                # Stage 5: Commit
                t_com = time.perf_counter()
                commit_result = await commit_pipeline_run(
                    conn=conn,
                    event_id=event_id,
                    context_id=context_id,
                    source_id=source_id,
                    event_speaker=speaker,
                    occurred_at=occurred_at,
                    resolved_decisions=resolved_decisions,
                    dropped_memories=dropped_memories,
                    stage_ms=stage_ms,
                    model_name=getattr(self.extractor, "model", "qwen2.5:7b-instruct"),
                )
                stage_ms["commit"] = int((time.perf_counter() - t_com) * 1000)

        return SaveReport(
            event_id=event_id,
            stage_ms=stage_ms,
            counts=commit_result["counts"],
            dropped=commit_result["dropped"],
            memories=commit_result["memories"],
            review_ids=commit_result["review_ids"],
            outbox_ids=commit_result["outbox_ids"],
        )
