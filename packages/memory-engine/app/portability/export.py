"""
Deterministic JSONL Exporter (Milestone X1).
Exports all core tables in a REPEATABLE READ transaction ordered by primary key
with canonical JSON serialization, optional secret exclusion, and optional Fernet encryption.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional
import uuid
import asyncpg
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

EXPORT_TABLES: list[tuple[str, str]] = [
    ("contexts", "id"),
    ("sources", "id"),
    ("events", "id"),
    ("chunks", "id"),
    ("entities", "id"),
    ("entity_aliases", "entity_id, alias"),
    ("memories", "id"),
    ("citations", "id"),
    ("claims", "id"),
    ("memory_relations", "id"),
    ("abstractions", "id"),
    ("abstraction_sources", "abstraction_id, memory_id"),
    ("memory_signals", "lineage_id"),
    ("review_queue", "id"),
    ("redactions", "id"),
]


def derive_fernet_key(passphrase: str, salt: bytes = b"lumen_memory_salt_v1") -> bytes:
    """Derives a deterministic 32-byte URL-safe base64 Fernet key from a passphrase."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _canonical_json(obj: Any) -> str:
    def _default(val: Any) -> Any:
        if isinstance(val, uuid.UUID):
            return str(val)
        if isinstance(val, datetime):
            return val.astimezone(timezone.utc).isoformat()
        if isinstance(val, bytes):
            return base64.b64encode(val).decode("ascii")
        # Handle pgvector / array types
        if hasattr(val, "tolist"):
            return val.tolist()
        return str(val)

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=_default, separators=(",", ":"))


async def export_bundle(
    pool: asyncpg.Pool,
    output_dir: Path,
    *,
    with_embeddings: bool = False,
    exclude_secret: bool = False,
    passphrase: Optional[str] = None,
) -> dict[str, Any]:
    """
    Exports all Lumen Memory Engine tables into output_dir as canonical JSONL files
    plus a manifest.json with SHA-256 checksums.
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    fernet = Fernet(derive_fernet_key(passphrase)) if passphrase else None

    counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    secret_memory_ids: set[str] = set()
    secret_event_ids: set[str] = set()
    secret_quotes: set[str] = set()

    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            # If excluding secrets, identify secret memory IDs and quotes first
            if exclude_secret:
                sec_rows = await conn.fetch(
                    "SELECT id, created_event, statement FROM memories WHERE sensitivity = 'secret'"
                )
                for sr in sec_rows:
                    secret_memory_ids.add(str(sr["id"]))
                    if sr["created_event"]:
                        secret_event_ids.add(str(sr["created_event"]))
                    if sr["statement"]:
                        secret_quotes.add(sr["statement"])

            # Get current alembic version if available
            try:
                schema_rev = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1") or "unknown"
            except Exception:
                schema_rev = "unknown"

            for table_name, order_col in EXPORT_TABLES:
                rows = await conn.fetch(f"SELECT * FROM {table_name} ORDER BY {order_col} ASC")
                lines: list[str] = []

                for r in rows:
                    row_dict = dict(r)

                    # Strip embeddings unless explicitly requested
                    if not with_embeddings and "embedding" in row_dict:
                        row_dict["embedding"] = None

                    # Enforce --exclude-secret
                    if exclude_secret:
                        if table_name == "memories" and str(row_dict.get("id")) in secret_memory_ids:
                            continue
                        if table_name == "citations" and str(row_dict.get("memory_id")) in secret_memory_ids:
                            row_dict["quote"] = "[REDACTED_SECRET]"
                        if table_name == "events" and str(row_dict.get("id")) in secret_event_ids:
                            row_dict["payload"] = {"text": "[REDACTED_SECRET]", "redacted": True}
                        if table_name == "chunks" and str(row_dict.get("event_id")) in secret_event_ids:
                            row_dict["text"] = "[REDACTED_SECRET]"

                    # Normalize jsonb string fields if asyncpg returned raw json string
                    for k, v in list(row_dict.items()):
                        if isinstance(v, str) and k in ("payload", "metadata", "object_value", "attributes", "plan", "channels", "fused"):
                            try:
                                row_dict[k] = json.loads(v)
                            except Exception:
                                pass

                    lines.append(_canonical_json(row_dict))

                content_str = "\n".join(lines) + ("\n" if lines else "")
                content_bytes = content_str.encode("utf-8")

                if fernet:
                    content_bytes = fernet.encrypt(content_bytes)
                    file_name = f"{table_name}.jsonl.enc"
                else:
                    file_name = f"{table_name}.jsonl"

                file_path = output_dir / file_name
                file_path.write_bytes(content_bytes)

                counts[table_name] = len(lines)
                checksums[file_name] = hashlib.sha256(content_bytes).hexdigest()

    manifest = {
        "engine": "lumen-memory",
        "engine_version": "0.1.0",
        "schema_revision": schema_rev,
        "encrypted": bool(passphrase),
        "exclude_secret": exclude_secret,
        "with_embeddings": with_embeddings,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "checksums": checksums,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
