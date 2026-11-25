"""
Session Synopses & Continuity Service (Milestone L2).
On session close: summarizes session events into <=150 words, embeds, and stores
in abstractions(kind='summary') with abstraction_sources provenance.
On session start (prime): returns top relevant synopses labeled as inference.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import uuid
import asyncpg

from app.core.config import settings
from app.adapters.llm.ollama_client import OllamaClient
from app.domain.ports import EmbeddingPort

logger = logging.getLogger("lumen.services.session")


class SessionService:
    """Manages session closeout synopses and session priming."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding: Optional[EmbeddingPort] = None,
        ollama_client: Optional[OllamaClient] = None,
    ):
        self.pool = pool
        self.embedding = embedding
        self.ollama_client = ollama_client or OllamaClient(base_url=settings.OLLAMA_BASE_URL)

    async def close_session(
        self,
        session_id: str,
        *,
        project: Optional[str] = None,
        branch: Optional[str] = None,
        context: str = "default",
        summary_override: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Summarizes a session's events into a <=150-word synopsis and persists it
        in abstractions with provenance links in abstraction_sources.
        """
        async with self.pool.acquire() as conn:
            ctx_id = await conn.fetchval("SELECT id FROM contexts WHERE slug = $1", context)
            if not ctx_id:
                ctx_id = await conn.fetchval(
                    "INSERT INTO contexts (id, slug, name) VALUES ($1, $2, $3) RETURNING id",
                    uuid.uuid4(), context, context.capitalize()
                )

            # Collect session events (matching session_id in payload or recent CAPTURE events)
            ev_rows = await conn.fetch(
                """
                SELECT id, payload, occurred_at
                FROM events
                WHERE context_id = $1
                  AND kind = 'CAPTURE'
                  AND (payload->>'session_id' = $2 OR $2 = 'recent')
                ORDER BY occurred_at DESC
                LIMIT 30
                """,
                ctx_id,
                session_id,
            )

            # Fallback to recent CAPTURE events if session_id wasn't tagged on events
            if not ev_rows:
                ev_rows = await conn.fetch(
                    """
                    SELECT id, payload, occurred_at
                    FROM events
                    WHERE context_id = $1 AND kind = 'CAPTURE'
                    ORDER BY occurred_at DESC
                    LIMIT 10
                    """,
                    ctx_id,
                )

            texts: list[str] = []
            ev_ids: list[uuid.UUID] = []
            for r in reversed(ev_rows):
                ev_ids.append(r["id"])
                p = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
                t = p.get("text") or p.get("content") or ""
                if t:
                    texts.append(t.strip())

            # Find derived memories linked to these events
            mem_ids: list[uuid.UUID] = []
            if ev_ids:
                m_rows = await conn.fetch(
                    """
                    SELECT id FROM memories
                    WHERE created_event = ANY($1::uuid[]) AND status = 'active'
                    """,
                    ev_ids,
                )
                mem_ids = [mr["id"] for mr in m_rows]

            # Create SYSTEM close event
            sys_ev_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO events (id, context_id, kind, actor, payload)
                VALUES ($1, $2, 'SYSTEM', 'session_service', $3)
                """,
                sys_ev_id,
                ctx_id,
                json.dumps({"event": "session.closed", "session_id": session_id, "project": project, "branch": branch}),
            )

            # Generate synopsis (<= 150 words)
            synopsis_text = summary_override
            if not synopsis_text:
                synopsis_text = await self._summarize_texts(texts, project=project, branch=branch)

            # Compute embedding if adapter available
            emb_str: Optional[str] = None
            if self.embedding is not None:
                try:
                    vec = await self.embedding.embed(synopsis_text)
                    emb_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                except Exception as e:
                    logger.warning(f"Failed to embed session synopsis: {e}")

            proj_label = project or "general"
            branch_label = f"@{branch}" if branch else ""
            title = f"Session Synopsis: {proj_label}{branch_label} [{session_id[:16]}]"

            abs_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO abstractions (
                    id, kind, title, body, context_id, confidence, proof_count,
                    pinned, status, created_event, embedding
                )
                VALUES ($1, 'summary', $2, $3, $4, 0.75, $5, false, 'active', $6, $7::vector)
                """,
                abs_id,
                title,
                synopsis_text,
                ctx_id,
                len(mem_ids),
                sys_ev_id,
                emb_str,
            )

            for mid in mem_ids:
                await conn.execute(
                    """
                    INSERT INTO abstraction_sources (abstraction_id, memory_id)
                    VALUES ($1, $2) ON CONFLICT DO NOTHING
                    """,
                    abs_id,
                    mid,
                )

        return {
            "status": "closed",
            "abstraction_id": str(abs_id),
            "session_id": session_id,
            "title": title,
            "synopsis": synopsis_text,
            "epistemic": "inference",
            "proof_count": len(mem_ids),
        }

    async def prime_session(
        self,
        *,
        project: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 3,
        context: str = "default",
    ) -> dict[str, Any]:
        """
        Returns top `limit` session synopses ranked by similarity * recency,
        each labeled as epistemic='inference' with its session date.
        """
        async with self.pool.acquire() as conn:
            ctx_id = await conn.fetchval("SELECT id FROM contexts WHERE slug = $1", context)
            if not ctx_id:
                return {"synopses": [], "count": 0}

            query_text = f"{project or ''} {branch or ''} open tasks decisions".strip()
            emb_str: Optional[str] = None
            if self.embedding is not None and query_text:
                try:
                    vec = await self.embedding.embed(query_text)
                    emb_str = "[" + ",".join(str(float(x)) for x in vec) + "]"
                except Exception:
                    emb_str = None

            if emb_str:
                rows = await conn.fetch(
                    """
                    SELECT id, title, body, confidence, proof_count, created_at,
                           CASE WHEN embedding IS NOT NULL
                                THEN (1.0 - (embedding <=> $2::vector))
                                ELSE 0.5
                           END AS sim_score
                    FROM abstractions
                    WHERE context_id = $1
                      AND kind = 'summary'
                      AND status = 'active'
                      AND title LIKE 'Session Synopsis:%'
                    ORDER BY sim_score DESC, created_at DESC
                    LIMIT $3
                    """,
                    ctx_id,
                    emb_str,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, title, body, confidence, proof_count, created_at, 0.5 AS sim_score
                    FROM abstractions
                    WHERE context_id = $1
                      AND kind = 'summary'
                      AND status = 'active'
                      AND title LIKE 'Session Synopsis:%'
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    ctx_id,
                    limit,
                )

            synopses = [
                {
                    "id": str(r["id"]),
                    "title": r["title"],
                    "synopsis": r["body"],
                    "epistemic": "inference",
                    "session_date": r["created_at"].isoformat() if r.get("created_at") else None,
                    "proof_count": r["proof_count"],
                    "score": round(float(r["sim_score"]), 4),
                }
                for r in rows
            ]

            return {"synopses": synopses, "count": len(synopses)}

    async def _summarize_texts(
        self,
        texts: list[str],
        *,
        project: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        if not texts:
            return f"Session closed for project '{project or 'general'}' with no recorded capture utterances."

        joined = "\n".join(f"- {t[:300]}" for t in texts)
        prompt = (
            f"Project: {project or 'general'} | Branch: {branch or 'main'}\n"
            "Summarize the key decisions, progress, and open tasks from these session notes in AT MOST 150 words:\n\n"
            f"{joined}"
        )

        try:
            resp = await self.ollama_client.chat(
                model=settings.LUMEN_LLM_MODEL,
                messages=[
                    {"role": "system", "content": "You are a concise technical session summarizer. Max 150 words."},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.2, "num_predict": 220},
            )
            content = resp.get("content", "").strip()
            if content:
                words = content.split()
                return " ".join(words[:150])
        except Exception as e:
            logger.warning(f"Ollama session summary fallback: {e}")

        # Deterministic fallback <= 150 words
        fallback = " | ".join(texts)
        words = fallback.split()
        return " ".join(words[:150])
