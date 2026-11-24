"""
In-Built Vault Reconciler (Milestone O2).
Handles two-way synchronization from Obsidian Vault edits back into PostgreSQL.
Enforces bi-temporal lineage increments, conflict staging in review_queue,
and protection against accidental file deletions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Optional, Tuple
import uuid
import asyncpg
import frontmatter

from app.vault.projector import (
    EDITABLE_START,
    EDITABLE_END,
    compute_content_hash,
    slugify,
    safe_vault_target,
    atomic_write,
    VaultProjector,
)

logger = logging.getLogger("lumen.vault.reconcile")


def extract_editable_statement(note_text: str) -> Optional[str]:
    """Extracts raw statement from within the lumen:editable block."""
    if EDITABLE_START not in note_text or EDITABLE_END not in note_text:
        return None
    start_idx = note_text.index(EDITABLE_START) + len(EDITABLE_START)
    end_idx = note_text.index(EDITABLE_END)
    block = note_text[start_idx:end_idx].strip()
    # Strip [[...]] wikilinks syntax if user edited with raw text
    cleaned = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", block)
    return cleaned.strip()


class VaultReconciler:
    """Reconciles human edits in the Obsidian Knowledge Vault back into PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool, vault_dir: Path):
        self.pool = pool
        self.vault_dir = vault_dir.resolve()
        self.projector = VaultProjector(pool, vault_dir)

    async def reconcile_file_change(self, file_path: Path) -> dict[str, Any]:
        """Processes an added or modified file in the vault."""
        resolved = safe_vault_target(self.vault_dir, file_path)
        if not resolved.exists() or resolved.suffix != ".md":
            return {"action": "ignored", "reason": "not_markdown_or_missing"}

        # Ignore special folders
        rel = resolved.relative_to(self.vault_dir)
        parts = rel.parts
        if any(p.startswith(".") for p in parts) or "_conflicts" in parts:
            return {"action": "ignored", "reason": "special_or_conflict_directory"}

        # Handle Inbox drop: notes in memories/inbox/ without lumen_id
        if "inbox" in parts:
            return await self._handle_inbox_drop(resolved)

        try:
            post = frontmatter.load(str(resolved))
        except Exception as e:
            logger.warning(f"Failed to parse frontmatter in {resolved}: {e}")
            return {"action": "error", "error": f"invalid_frontmatter: {e}"}

        metadata = post.metadata or {}
        lineage_id_str = metadata.get("lineage_id")
        if not lineage_id_str:
            # Entity or other unmanaged note
            return {"action": "ignored", "reason": "no_lineage_id"}

        try:
            lineage_id = uuid.UUID(str(lineage_id_str))
        except ValueError:
            return {"action": "error", "error": "invalid_lineage_uuid"}

        file_version = int(metadata.get("version", 1))
        stored_hash = metadata.get("content_hash", "")
        file_status = metadata.get("status", "active")
        file_lane = metadata.get("lane", "personal")

        # Extract statement from editable block
        edited_statement = extract_editable_statement(post.content)
        if not edited_statement:
            return {"action": "ignored", "reason": "no_editable_block"}

        current_hash = compute_content_hash(edited_statement)

        # If hash and status are unchanged, this was an internal projector write: ignore to avoid loop
        if current_hash == stored_hash and file_status != "retracted":
            return {"action": "no_op", "reason": "hash_match"}

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Fetch current active/latest version from DB
                db_row = await conn.fetchrow(
                    """
                    SELECT id, lineage_id, version, type, epistemic, statement,
                           confidence, importance, lane, sensitivity, status,
                           context_id, created_event, subject_id, predicate, object_id,
                           object_value, cardinality, polarity
                    FROM memories
                    WHERE lineage_id = $1
                    ORDER BY version DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    lineage_id,
                )

                if not db_row:
                    logger.warning(f"Lineage {lineage_id} not found in DB during reconcile")
                    return {"action": "error", "error": "lineage_not_found"}

                db_version = db_row["version"]
                now = datetime.now(timezone.utc)

                # Check for retraction requested in frontmatter
                if file_status == "retracted":
                    if db_row["status"] != "retracted":
                        await conn.execute(
                            """
                            UPDATE memories
                            SET status = 'retracted', valid_to = $1, superseded_at = $1
                            WHERE id = $2
                            """,
                            now,
                            db_row["id"],
                        )
                        ev_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO events (id, kind, type, actor, context_id, target_id, content, content_hash, tombstone, occurred_at, recorded_at, caused_by)
                            VALUES ($1, 'DERIVED', 'memory.tombstoned', 'vault', $2, $3, $4, md5($4), true, $5, $5, $6)
                            """,
                            ev_id,
                            db_row["context_id"],
                            db_row["id"],
                            f"Memory {db_row['id']} retracted from vault note edit",
                            now,
                            [db_row["created_event"]],
                        )
                        # Move note to _retracted/
                        retracted_dir = self.vault_dir / "_retracted"
                        retracted_dir.mkdir(parents=True, exist_ok=True)
                        dest = retracted_dir / resolved.name
                        if resolved.exists():
                            resolved.rename(dest)
                        return {"action": "retracted", "lineage_id": str(lineage_id)}
                    return {"action": "no_op", "reason": "already_retracted"}

                # Check for version conflict (stale note edit)
                if file_version < db_version:
                    logger.warning(
                        f"Stale note edit for lineage {lineage_id}: file has v{file_version}, DB has v{db_version}"
                    )
                    # Write conflict file
                    conflicts_dir = self.vault_dir / "_conflicts"
                    conflicts_dir.mkdir(parents=True, exist_ok=True)
                    conflict_filename = f"{resolved.stem}.conflict.md"
                    conflict_path = conflicts_dir / conflict_filename

                    conflict_body = f"""---
lineage_id: {lineage_id}
conflict_type: stale_version_edit
file_version: {file_version}
db_version: {db_version}
timestamp: {now.isoformat()}
---
# Memory Edit Conflict: {resolved.stem}

A human edit was made in Obsidian based on version **v{file_version}**, but the Memory Engine has already moved to **v{db_version}**.

## Your Edit (Obsidian)
{edited_statement}

## Engine Current Version (v{db_version})
{db_row['statement']}

> This conflict has been staged in the Lumen Review Queue.
"""
                    atomic_write(conflict_path, conflict_body)

                    # Stage review_queue row
                    proposal_payload = {
                        "lineage_id": str(lineage_id),
                        "file_path": str(rel),
                        "obsidian_edit": edited_statement,
                        "engine_current": db_row["statement"],
                        "file_version": file_version,
                        "db_version": db_version,
                    }
                    await conn.execute(
                        """
                        INSERT INTO review_queue (kind, reason, proposal, memory_ids, status, created_event)
                        VALUES ('vault_conflict', 'Concurrent edit conflict between Obsidian note and Memory Engine', $1, $2, 'open', $3)
                        """,
                        json.dumps(proposal_payload),
                        [db_row["id"]],
                        db_row["created_event"],
                    )

                    # Re-render main note from authoritative DB
                    await self.projector.rebuild()
                    return {
                        "action": "conflict",
                        "lineage_id": str(lineage_id),
                        "conflict_file": str(conflict_path),
                    }

                # Clean Edit: file_version == db_version
                # Create CAPTURE event for the human edit
                capture_ev_id = uuid.uuid4()
                ev_payload = json.dumps({"uri": str(rel), "file_path": str(rel)})
                await conn.execute(
                    """
                    INSERT INTO events (id, kind, type, actor, context_id, content, payload, content_hash, occurred_at, recorded_at)
                    VALUES ($1, 'CAPTURE', 'note.edit', 'vault', $2, $3, $4, md5($3), $5, $5)
                    """,
                    capture_ev_id,
                    db_row["context_id"],
                    edited_statement,
                    ev_payload,
                    now,
                )

                new_mem_id = uuid.uuid4()
                new_version = db_version + 1

                # Insert new version in memories
                await conn.execute(
                    """
                    INSERT INTO memories (
                        id, lineage_id, version, type, epistemic, statement,
                        subject_id, predicate, object_id, object_value, cardinality, polarity,
                        valid_from, valid_to, confidence, importance, status,
                        context_id, created_event, asserted_by, lane, sensitivity, recorded_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12,
                        $13, null, $14, $15, 'active',
                        $16, $17, 'vault', $18, $19, $20
                    )
                    """,
                    new_mem_id,
                    lineage_id,
                    new_version,
                    db_row["type"],
                    db_row["epistemic"],
                    edited_statement,
                    db_row["subject_id"],
                    db_row["predicate"],
                    db_row["object_id"],
                    db_row["object_value"],
                    db_row["cardinality"],
                    db_row["polarity"],
                    now,
                    db_row["confidence"],
                    db_row["importance"],
                    db_row["context_id"],
                    capture_ev_id,
                    file_lane,
                    db_row["sensitivity"],
                    now,
                )

                # Supersede old memory
                await conn.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded', superseded_at = $1, valid_to = $1, superseded_by = $2
                    WHERE id = $3
                    """,
                    now,
                    new_mem_id,
                    db_row["id"],
                )

                # Record DERIVED audit event
                derived_ev_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO events (id, kind, type, actor, context_id, target_id, content, content_hash, occurred_at, recorded_at, caused_by)
                    VALUES ($1, 'DERIVED', 'memory.corrected', 'vault', $2, $3, $4, md5($4), $5, $5, $6)
                    """,
                    derived_ev_id,
                    db_row["context_id"],
                    new_mem_id,
                    f"Lineage {lineage_id} edited in Obsidian: v{db_version} -> v{new_version}",
                    now,
                    [capture_ev_id],
                )

                # Outbox row
                ob_payload = {
                    "memory_id": str(new_mem_id),
                    "lineage_id": str(lineage_id),
                    "version": new_version,
                    "superseded_id": str(db_row["id"]),
                    "statement": edited_statement,
                    "lane": file_lane,
                    "actor": "vault",
                }
                await conn.execute(
                    """
                    INSERT INTO outbox (event_id, topic, payload, status)
                    VALUES ($1, 'memory.corrected', $2, 'queued')
                    """,
                    derived_ev_id,
                    json.dumps(ob_payload),
                )

        # Re-project note to update version, content_hash, and history in vault
        await self.projector.rebuild()
        logger.info(f"Reconciled Obsidian note edit: {lineage_id} incremented to v{new_version}")
        return {
            "action": "corrected",
            "lineage_id": str(lineage_id),
            "version": new_version,
            "statement": edited_statement,
        }

    async def reconcile_file_deletion(self, file_path: Path) -> dict[str, Any]:
        """
        Anti-deletion protection:
        Deleting a file in Obsidian or OS NEVER deletes or forgets a memory!
        Immediately re-projects the note from PostgreSQL.
        """
        logger.warning(f"File deleted in vault: {file_path}. Restoring from authoritative database...")
        await self.projector.rebuild()
        return {"action": "restored", "path": str(file_path)}

    async def _handle_inbox_drop(self, file_path: Path) -> dict[str, Any]:
        """
        Processes a raw markdown note dropped into vault/memories/inbox/.
        Ingests the content via SavePipeline and replaces the inbox note with wikilinks.
        """
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return {"action": "ignored", "reason": "empty_inbox_file"}

        from app.domain.events import CaptureRequest, SourceInfo
        from app.adapters.storage.pg_events import PostgresEventsAdapter
        from app.pipeline import SavePipeline

        async with self.pool.acquire() as conn:
            events_adapter = PostgresEventsAdapter(self.pool)
            cap_req = CaptureRequest(
                content=content,
                source=SourceInfo(kind="note", title=file_path.stem, author="user"),
                context="ctx_vault_inbox",
            )
            cap_res = await events_adapter.capture(cap_req)
            pipeline = SavePipeline(self.pool)
            save_report = await pipeline.run(cap_res.event_id)

            # Rebuild vault to project newly extracted memories
            await self.projector.rebuild()

            # Replace inbox note with links
            linked_bullets = [
                f"- [[{slugify(m['statement'])}-{str(m['id'])[:8]}|{m['statement']}]]"
                for m in (save_report.memories or [])
            ]
            replacement_text = f"""---
processed_at: {datetime.now(timezone.utc).isoformat()}
source_event_id: {cap_res.event_id}
---
# Ingested: {file_path.stem}

This note was automatically processed by the Lumen Memory Engine.
The following memories were created:

{chr(10).join(linked_bullets)}
"""
            atomic_write(file_path, replacement_text)
            return {
                "action": "inbox_processed",
                "memories_created": len(save_report.memories),
            }
