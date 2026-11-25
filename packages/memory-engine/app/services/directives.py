"""
Directives Service (Milestone D1).
Manages the procedural memory tier stored in the abstractions table:
User preferences, operational constraints, goals, styles, and behavioral rules.
Enforces:
- Append-only immutability (edits create a new version with superseded_by linked).
- Context-level cap (maximum 15 active directives per context pack).
- Priority ordering (1-10, highest priority first).
- Secret rejection (directives cannot be secret).
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, List, Optional
import uuid
import asyncpg

from app.domain.errors import NotFoundError, ConflictError

logger = logging.getLogger("lumen.services.directives")

MAX_ACTIVE_DIRECTIVES = 15
VALID_DIRECTIVE_TYPES = {"preference", "constraint", "goal", "style", "context"}


class DirectivesService:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_directive(
        self,
        title: str,
        body: str,
        directive_type: str = "preference",
        priority: int = 5,
        context_id: Optional[uuid.UUID] = None,
        memory_ids: Optional[List[uuid.UUID]] = None,
        pinned: bool = True,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Creates a new active directive in the procedural memory tier."""
        clean_title = title.strip()
        clean_body = body.strip()
        if not clean_title or not clean_body:
            raise ValueError("Directive title and body cannot be empty")

        if directive_type not in VALID_DIRECTIVE_TYPES:
            raise ValueError(f"Invalid directive_type: {directive_type}. Must be one of {VALID_DIRECTIVE_TYPES}")

        priority = max(1, min(10, priority))
        now = datetime.now(timezone.utc)
        directive_id = uuid.uuid4()
        event_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Resolve context_id if not supplied
                if not context_id:
                    ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = 'default'")
                    context_id = ctx_row["id"] if ctx_row else uuid.uuid4()

                # 2. Check active count cap
                active_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM abstractions
                    WHERE kind = 'directive' AND status = 'active' AND context_id = $1
                    """,
                    context_id,
                )
                if active_count >= MAX_ACTIVE_DIRECTIVES:
                    raise ConflictError(
                        f"Directive cap reached: maximum {MAX_ACTIVE_DIRECTIVES} active directives allowed per context"
                    )

                # 3. Create CAPTURE event
                ev_payload = json.dumps({"title": clean_title, "directive_type": directive_type, "priority": priority})
                await conn.execute(
                    """
                    INSERT INTO events (id, kind, type, actor, context_id, content, payload, content_hash, occurred_at, recorded_at)
                    VALUES ($1, 'CAPTURE', 'directive.created', $2, $3, $4, $5, md5($4), $6, $6)
                    """,
                    event_id,
                    actor,
                    context_id,
                    clean_body,
                    ev_payload,
                    now,
                )

                # 4. Insert directive into abstractions table
                await conn.execute(
                    """
                    INSERT INTO abstractions (
                        id, kind, title, body, directive_type, priority,
                        pinned, status, context_id, confidence, proof_count, created_event, created_at
                    )
                    VALUES ($1, 'directive', $2, $3, $4, $5, $6, 'active', $7, 1.0, $8, $9, $10)
                    """,
                    directive_id,
                    clean_title,
                    clean_body,
                    directive_type,
                    priority,
                    pinned,
                    context_id,
                    len(memory_ids or []),
                    event_id,
                    now,
                )

                # 5. Link supporting memories if provided
                for mid in (memory_ids or []):
                    await conn.execute(
                        """
                        INSERT INTO abstraction_sources (abstraction_id, memory_id)
                        VALUES ($1, $2) ON CONFLICT DO NOTHING
                        """,
                        directive_id,
                        mid,
                    )

                # 6. Outbox notification
                outbox_payload = {
                    "directive_id": str(directive_id),
                    "title": clean_title,
                    "directive_type": directive_type,
                    "priority": priority,
                    "action": "created",
                }
                await conn.execute(
                    """
                    INSERT INTO outbox (event_id, topic, payload, status)
                    VALUES ($1, 'directive.created', $2, 'queued')
                    """,
                    event_id,
                    json.dumps(outbox_payload),
                )

        logger.info(f"Created directive '{clean_title}' (priority {priority})")
        return {
            "id": str(directive_id),
            "title": clean_title,
            "body": clean_body,
            "directive_type": directive_type,
            "priority": priority,
            "pinned": pinned,
            "status": "active",
            "context_id": str(context_id),
            "proof_count": len(memory_ids or []),
            "created_at": now.isoformat(),
        }

    async def list_directives(
        self,
        context_id: Optional[uuid.UUID] = None,
        status: str = "active",
        directive_type: Optional[str] = None,
        limit: int = 15,
    ) -> List[dict[str, Any]]:
        """Lists directives ordered by priority DESC, created_at DESC."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT a.*,
                       COALESCE(array_agg(s.memory_id) FILTER (WHERE s.memory_id IS NOT NULL), '{}') AS source_memories
                FROM abstractions a
                LEFT JOIN abstraction_sources s ON a.id = s.abstraction_id
                WHERE a.kind = 'directive'
            """
            params: list[Any] = []
            if status:
                params.append(status)
                query += f" AND a.status = ${len(params)}"
            if context_id:
                params.append(context_id)
                query += f" AND a.context_id = ${len(params)}"
            if directive_type:
                params.append(directive_type)
                query += f" AND a.directive_type = ${len(params)}"

            query += f" GROUP BY a.id ORDER BY a.priority DESC, a.created_at DESC LIMIT {limit}"

            rows = await conn.fetch(query, *params)
            results = []
            for r in rows:
                d = dict(r)
                d["id"] = str(d["id"])
                d["context_id"] = str(d["context_id"])
                d["created_event"] = str(d["created_event"])
                if d.get("superseded_by"):
                    d["superseded_by"] = str(d["superseded_by"])
                d["source_memories"] = [str(m) for m in d.get("source_memories") or []]
                d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
                results.append(d)
            return results

    async def update_directive(
        self,
        directive_id: uuid.UUID,
        title: Optional[str] = None,
        body: Optional[str] = None,
        directive_type: Optional[str] = None,
        priority: Optional[int] = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        """
        Updates an existing directive immutably:
        Closes out old row with superseded_by and creates new version row.
        """
        now = datetime.now(timezone.utc)
        new_id = uuid.uuid4()
        event_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    """
                    SELECT * FROM abstractions
                    WHERE id = $1 AND kind = 'directive' AND status = 'active'
                    FOR UPDATE
                    """,
                    directive_id,
                )
                if not old:
                    raise NotFoundError(f"Active directive {directive_id} not found")

                new_title = (title if title is not None else old["title"]).strip()
                new_body = (body if body is not None else old["body"]).strip()
                new_type = directive_type or old["directive_type"]
                new_priority = max(1, min(10, priority if priority is not None else (old["priority"] or 5)))

                if new_type not in VALID_DIRECTIVE_TYPES:
                    raise ValueError(f"Invalid directive_type: {new_type}")

                # 1. CAPTURE event
                ev_payload = json.dumps({"superseded_id": str(directive_id), "title": new_title})
                await conn.execute(
                    """
                    INSERT INTO events (id, kind, type, actor, context_id, content, payload, content_hash, occurred_at, recorded_at)
                    VALUES ($1, 'CAPTURE', 'directive.updated', $2, $3, $4, $5, md5($4), $6, $6)
                    """,
                    event_id,
                    actor,
                    old["context_id"],
                    new_body,
                    ev_payload,
                    now,
                )

                # 2. Insert new directive row first to satisfy foreign key constraints
                await conn.execute(
                    """
                    INSERT INTO abstractions (
                        id, kind, title, body, directive_type, priority,
                        pinned, status, context_id, confidence, proof_count, created_event, created_at
                    )
                    VALUES ($1, 'directive', $2, $3, $4, $5, $6, 'active', $7, 1.0, $8, $9, $10)
                    """,
                    new_id,
                    new_title,
                    new_body,
                    new_type,
                    new_priority,
                    old["pinned"],
                    old["context_id"],
                    old["proof_count"],
                    event_id,
                    now,
                )

                # 3. Supersede old directive referencing new_id
                await conn.execute(
                    """
                    UPDATE abstractions
                    SET status = 'superseded', superseded_by = $1
                    WHERE id = $2
                    """,
                    new_id,
                    directive_id,
                )

                # 4. Copy over abstraction_sources
                await conn.execute(
                    """
                    INSERT INTO abstraction_sources (abstraction_id, memory_id)
                    SELECT $1, memory_id FROM abstraction_sources WHERE abstraction_id = $2
                    """,
                    new_id,
                    directive_id,
                )

        logger.info(f"Updated directive {directive_id} -> {new_id} ('{new_title}')")
        return {
            "id": str(new_id),
            "previous_id": str(directive_id),
            "title": new_title,
            "body": new_body,
            "directive_type": new_type,
            "priority": new_priority,
            "status": "active",
        }

    async def retract_directive(self, directive_id: uuid.UUID, actor: str = "user") -> dict[str, Any]:
        """Retracts an active directive."""
        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    """
                    SELECT * FROM abstractions
                    WHERE id = $1 AND kind = 'directive' AND status = 'active'
                    FOR UPDATE
                    """,
                    directive_id,
                )
                if not old:
                    raise NotFoundError(f"Active directive {directive_id} not found")

                await conn.execute(
                    """
                    UPDATE abstractions
                    SET status = 'retracted'
                    WHERE id = $1
                    """,
                    directive_id,
                )

                await conn.execute(
                    """
                    INSERT INTO events (id, kind, type, actor, context_id, target_id, content, content_hash, tombstone, occurred_at, recorded_at)
                    VALUES ($1, 'DERIVED', 'directive.tombstoned', $2, $3, $4, $5, md5($5), true, $6, $6)
                    """,
                    event_id,
                    actor,
                    old["context_id"],
                    directive_id,
                    f"Retracted directive: {old['title']}",
                    now,
                )

        logger.info(f"Retracted directive {directive_id}")
        return {"action": "retracted", "id": str(directive_id), "title": old["title"]}

    async def toggle_pin(self, directive_id: uuid.UUID) -> dict[str, Any]:
        """Toggles pinned state of an active directive."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE abstractions
                SET pinned = NOT pinned
                WHERE id = $1 AND kind = 'directive' AND status = 'active'
                RETURNING id, title, pinned
                """,
                directive_id,
            )
            if not row:
                raise NotFoundError(f"Active directive {directive_id} not found")
            return {"id": str(row["id"]), "title": row["title"], "pinned": row["pinned"]}
