"""
In-Built Obsidian Knowledge Vault Projector (Milestone O1).
Projects memories, entities, relations, citations, and daily logs from PostgreSQL
into a deterministic, human-readable markdown vault with [[wikilinks]], YAML frontmatter,
and Dataview-compatible typed inline relations.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, List, Optional, Tuple
import uuid
import asyncpg
import yaml

logger = logging.getLogger("lumen.vault.projector")

EDITABLE_START = "<!-- lumen:editable:start -->"
EDITABLE_END = "<!-- lumen:editable:end -->"


def get_default_vault_dir() -> Path:
    env_dir = os.getenv("LUMEN_VAULT_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    # Default to ./vault at monorepo root
    cwd = Path.cwd().resolve()
    if (cwd / "vault").exists():
        return cwd / "vault"
    # Search parent directory if inside packages/memory-engine
    if (cwd.parent / "vault").exists():
        return cwd.parent / "vault"
    return cwd / "vault"


def compute_content_hash(text: str) -> str:
    """Computes SHA-256 hash of the normalized statement content."""
    clean = text.strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def slugify(text: str, max_len: int = 48) -> str:
    """Converts a statement into a safe, readable filename slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    slug = s.strip("-")[:max_len].rstrip("-")
    return slug or "memory"


def safe_vault_target(vault_dir: Path, target_path: Path) -> Path:
    """Ensures target path cannot escape the designated vault root (path traversal invariant)."""
    resolved_root = vault_dir.resolve()
    resolved_target = target_path.resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise ValueError(f"Path traversal detected: {target_path} is outside vault {vault_dir}")
    return resolved_target


def atomic_write(target_path: Path, content: str) -> None:
    """Atomically writes content to disk using a temporary file and atomic rename."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".tmp_{target_path.name}_{uuid.uuid4().hex[:8]}"
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, target_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


async def build_entity_link_map(conn: asyncpg.Connection) -> List[Tuple[str, str]]:
    """
    Builds an alias -> canonical entity name mapping from PostgreSQL.
    Returns list of (mention_text, canonical_name) sorted by len(mention_text) DESC
    so longest matches match first without substring collisions.
    """
    rows = await conn.fetch(
        """
        SELECT e.id, e.canonical_name,
               COALESCE(e.aliases, '{}') || COALESCE(array_agg(ea.alias) FILTER (WHERE ea.alias IS NOT NULL), '{}') AS aliases
        FROM entities e
        LEFT JOIN entity_aliases ea ON ea.entity_id = e.id
        GROUP BY e.id, e.canonical_name, e.aliases
        """
    )
    mapping = {}
    for r in rows:
        c_name = r["canonical_name"].strip()
        if c_name:
            mapping[c_name.lower()] = c_name
            for a in r.get("aliases") or []:
                a_clean = a.strip()
                if a_clean:
                    mapping[a_clean.lower()] = c_name

    # Sort descending by length of mention text
    sorted_pairs = sorted(
        [(k, v) for k, v in mapping.items()],
        key=lambda item: len(item[0]),
        reverse=True,
    )
    return sorted_pairs


def wikilinkify(text: str, entity_map: List[Tuple[str, str]], exclude_canonical: Optional[str] = None) -> str:
    """
    Transforms entity mentions in a text into [[Canonical Name]] wikilinks.
    Guarantees no double-linking if text already contains [[Wikilinks]].
    """
    if not text or not entity_map:
        return text

    # Extract already linked spans [[...]] so we don't touch them
    tokens = []
    def save_link(match):
        tokens.append(match.group(0))
        return f"@@WIKILINK_TOKEN_{len(tokens) - 1}@@"

    processed = re.sub(r"\[\[[^\]]+\]\]", save_link, text)

    # Perform longest-match replacement with tokenization
    for mention_lower, canonical in entity_map:
        if exclude_canonical and canonical.lower() == exclude_canonical.lower():
            continue
        if len(mention_lower) < 2:
            continue

        pattern = re.compile(rf"\b{re.escape(mention_lower)}\b", re.IGNORECASE)
        def replace_with_token(match, c=canonical):
            tokens.append(f"[[{c}]]")
            return f"@@WIKILINK_TOKEN_{len(tokens) - 1}@@"

        processed = pattern.sub(replace_with_token, processed)

    # Restore all tokens
    for idx, link_str in enumerate(tokens):
        processed = processed.replace(f"@@WIKILINK_TOKEN_{idx}@@", link_str)

    return processed


def render_memory_note(
    mem: dict,
    versions: List[dict],
    citations: List[dict],
    entity_map: List[Tuple[str, str]],
) -> str:
    """
    Renders an atomic memory note with YAML frontmatter, editable block,
    verbatim citation quotes, and historical version trails.
    """
    statement = mem["statement"]
    content_hash = compute_content_hash(statement)
    statement_linked = wikilinkify(statement, entity_map)

    valid_from_str = mem["valid_from"].isoformat() if mem.get("valid_from") else None
    valid_to_str = mem["valid_to"].isoformat() if mem.get("valid_to") else None
    superseded_str = mem["superseded_at"].isoformat() if mem.get("superseded_at") else None

    frontmatter_dict = {
        "lumen_id": str(mem["id"]),
        "lineage_id": str(mem["lineage_id"]),
        "version": mem["version"],
        "status": mem["status"],
        "lane": mem["lane"],
        "sensitivity": mem["sensitivity"],
        "confidence": float(mem.get("confidence") or 1.0),
        "content_hash": content_hash,
        "valid_from": valid_from_str,
        "valid_to": valid_to_str,
        "superseded_at": superseded_str,
        "type": mem.get("type"),
        "epistemic": mem.get("epistemic"),
    }
    # Clean None values from frontmatter
    fm_clean = {k: v for k, v in frontmatter_dict.items() if v is not None}
    fm_yaml = yaml.dump(fm_clean, default_flow_style=False, sort_keys=False).strip()

    # Citations block
    cit_lines = []
    for c in citations:
        q = c.get("quote", "").strip()
        src = c.get("source_title") or "Unknown Event"
        said = c.get("said_at")
        said_fmt = said.strftime("%d %b %Y") if isinstance(said, datetime) else str(said or "")
        cit_lines.append(f'> "{q}" — {src}, {said_fmt}')
    cit_block = "\n>\n".join(cit_lines) if cit_lines else "> Grounded event assertion."

    # History trail
    history_lines = []
    for v in versions:
        v_num = v["version"]
        rec = v.get("recorded_at")
        rec_fmt = rec.strftime("%d %b %Y") if isinstance(rec, datetime) else str(rec or "")
        stmt = v["statement"]
        sup = v.get("superseded_at")
        sup_info = f" → superseded {sup.strftime('%d %b') if isinstance(sup, datetime) else ''}" if sup else ""
        history_lines.append(f"- **v{v_num}** ({rec_fmt}): {stmt}{sup_info}")

    history_block = "\n".join(history_lines) if history_lines else f"- **v{mem['version']}**: Current version"

    content = f"""---
{fm_yaml}
---
<!-- Note: Only the block between lumen:editable:start and end, and frontmatter status/lane, are user-editable. -->

# {statement}

{EDITABLE_START}
{statement_linked}
{EDITABLE_END}

{cit_block}

## Version History
{history_block}
"""
    return content.strip() + "\n"


def render_entity_note(
    entity: dict,
    active_facts: List[dict],
    relations: List[dict],
    backlink_memories: List[dict],
    entity_map: List[Tuple[str, str]],
) -> str:
    """
    Renders an entity profile note with aliases in YAML frontmatter,
    active facts, Dataview-compatible relations, and backlinks.
    """
    name = entity["canonical_name"].strip()
    fm_dict = {
        "entity_id": str(entity["id"]),
        "kind": entity.get("kind", "concept"),
        "aliases": entity.get("aliases") or [],
    }
    fm_yaml = yaml.dump(fm_dict, default_flow_style=False, sort_keys=False).strip()

    # Active facts
    fact_lines = []
    for f in active_facts:
        stmt = wikilinkify(f["statement"], entity_map, exclude_canonical=name)
        mid = str(f["id"])[:8]
        fact_lines.append(f"- {stmt} `[{mid}]`")
    facts_block = "\n".join(fact_lines) if fact_lines else "- No active facts recorded."

    # Relations (Dataview format: - PREDICATE:: [[Target]])
    rel_lines = []
    for r in relations:
        pred = r.get("predicate") or "RELATION"
        target = r.get("target_name") or "Unknown"
        rel_lines.append(f"- {pred}:: [[{target}]]")
    rel_block = "\n".join(rel_lines) if rel_lines else "- No direct graph edges."

    # Backlinks (Memories mentioning this entity)
    backlink_lines = []
    seen_lineages = set()
    for b in backlink_memories:
        lid = str(b["lineage_id"])
        if lid in seen_lineages:
            continue
        seen_lineages.add(lid)
        slug = slugify(b["statement"])
        short_id = lid[:8]
        # wikilink to memory note
        backlink_lines.append(f"- [[{slug}-{short_id}|{b['statement']}]]")
    backlinks_block = "\n".join(backlink_lines) if backlink_lines else "- No backlink mentions."

    desc = entity.get("description")
    desc_section = f"\n{desc}\n" if desc else ""

    content = f"""---
{fm_yaml}
---
# {name}
{desc_section}
## Current Facts
{facts_block}

## Relations
{rel_block}

## Mentioned In
{backlinks_block}
"""
    return content.strip() + "\n"


def render_daily_note(
    date_str: str,
    created: List[dict],
    corrected: List[dict],
    retracted: List[dict],
) -> str:
    """Renders a daily changelog note summarizing memory evolutions."""
    c_lines = [f"- **Created:** {wikilinkify(m['statement'], [])} (v{m['version']})" for m in created]
    cor_lines = [f"- **Corrected:** {m['statement']} (v{m['version']})" for m in corrected]
    ret_lines = [f"- **Retracted:** ~{m['statement']}~" for m in retracted]

    all_sections = []
    if c_lines:
        all_sections.append("### New Facts\n" + "\n".join(c_lines))
    if cor_lines:
        all_sections.append("### Updated Lineages\n" + "\n".join(cor_lines))
    if ret_lines:
        all_sections.append("### Retracted Facts\n" + "\n".join(ret_lines))

    body = "\n\n".join(all_sections) if all_sections else "No memory state changes recorded on this date."

    return f"""---
date: {date_str}
type: daily_digest
---
# Daily Memory Log: {date_str}

{body}
"""


class VaultProjector:
    """
    Projector service that projects Postgres memories into the Obsidian Knowledge Vault.
    """

    def __init__(self, pool: asyncpg.Pool, vault_dir: Optional[Path] = None):
        self.pool = pool
        self.vault_dir = (vault_dir or get_default_vault_dir()).resolve()

    def _ensure_dirs(self) -> None:
        (self.vault_dir / "entities").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "memories").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "_retracted").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "_conflicts").mkdir(parents=True, exist_ok=True)

    async def rebuild(self) -> dict[str, int]:
        """
        Deterministically rebuilds the entire Knowledge Vault from PostgreSQL.
        Running rebuild multiple times yields bit-identical results.
        """
        self._ensure_dirs()
        logger.info(f"Rebuilding in-built Knowledge Vault at {self.vault_dir}")

        async with self.pool.acquire() as conn:
            # 1. Build Entity Link Map
            entity_map = await build_entity_link_map(conn)

            # 2. Fetch Entities
            entity_rows = await conn.fetch(
                """
                SELECT e.id, e.canonical_name, e.kind, e.description,
                       COALESCE(e.aliases, '{}') || COALESCE(array_agg(ea.alias) FILTER (WHERE ea.alias IS NOT NULL), '{}') AS aliases
                FROM entities e
                LEFT JOIN entity_aliases ea ON ea.entity_id = e.id
                GROUP BY e.id, e.canonical_name, e.kind, e.description, e.aliases
                ORDER BY e.canonical_name ASC
                """
            )

            # 3. Fetch All Memories
            mem_rows = await conn.fetch(
                """
                SELECT m.*, sub.canonical_name AS subject_name, obj.canonical_name AS object_name
                FROM memories m
                LEFT JOIN entities sub ON m.subject_id = sub.id
                LEFT JOIN entities obj ON m.object_id = obj.id
                ORDER BY m.lineage_id ASC, m.version ASC
                """
            )

            # Group memories by lineage
            lineages: dict[uuid.UUID, List[dict]] = {}
            for r in mem_rows:
                lineages.setdefault(r["lineage_id"], []).append(dict(r))

            # Fetch Citations
            cit_rows = await conn.fetch(
                """
                SELECT c.memory_id, c.quote, e.occurred_at AS said_at, s.title AS source_title
                FROM citations c
                JOIN events e ON c.event_id = e.id
                LEFT JOIN sources s ON e.source_id = s.id
                """
            )
            cit_map = {}
            for c in cit_rows:
                cit_map.setdefault(c["memory_id"], []).append(dict(c))

            # Fetch Graph Relations
            rel_rows = await conn.fetch(
                """
                SELECT mr.from_memory, mr.type, sub.canonical_name AS source_name, obj.canonical_name AS target_name, mr.to_entity
                FROM memory_relations mr
                LEFT JOIN memories m ON mr.from_memory = m.id
                LEFT JOIN entities sub ON m.subject_id = sub.id
                LEFT JOIN entities obj ON mr.to_entity = obj.id
                WHERE mr.to_entity IS NOT NULL
                """
            )
            rel_by_entity: dict[str, List[dict]] = {}
            for r in rel_rows:
                s_name = r.get("source_name")
                if s_name:
                    rel_by_entity.setdefault(s_name, []).append({
                        "predicate": r["type"],
                        "target_name": r["target_name"],
                    })

            # 4. Project Memories
            memories_written = 0
            for lineage_id, versions in lineages.items():
                # Current active or newest version
                active_v = next((v for v in reversed(versions) if v["status"] == "active"), versions[-1])
                # Stable lineage filename based on root version
                root_v = versions[0]
                rec_date = root_v["recorded_at"] or datetime.now(timezone.utc)
                yyyy = rec_date.strftime("%Y")
                slug = slugify(root_v["statement"])
                short_lid = str(lineage_id)[:8]
                filename = f"{slug}-{short_lid}.md"

                # If retracted, place in _retracted/
                if active_v["status"] == "retracted":
                    target = safe_vault_target(self.vault_dir, self.vault_dir / "_retracted" / filename)
                else:
                    target = safe_vault_target(self.vault_dir, self.vault_dir / "memories" / yyyy / filename)

                cits = cit_map.get(active_v["id"], [])
                note_content = render_memory_note(active_v, versions, cits, entity_map)
                atomic_write(target, note_content)
                memories_written += 1

            # 5. Project Entities
            entities_written = 0
            for er in entity_rows:
                e_dict = dict(er)
                e_name = e_dict["canonical_name"].strip()
                e_id = e_dict["id"]

                # Active facts where entity is subject
                subject_facts = [
                    v for v_list in lineages.values()
                    for v in v_list
                    if v.get("subject_id") == e_id and v["status"] == "active"
                ]

                # Backlinks (any memory mentioning entity in statement)
                backlinks = []
                name_pattern = re.compile(rf"\b{re.escape(e_name)}\b", re.IGNORECASE)
                for v_list in lineages.values():
                    cur = v_list[-1]
                    if name_pattern.search(cur["statement"]):
                        backlinks.append(cur)

                rels = list(rel_by_entity.get(e_name, []))
                seen_rel = {(r["predicate"], r["target_name"]) for r in rels}
                for f in subject_facts:
                    pred = f.get("predicate")
                    tgt = f.get("object_name") or f.get("object_value")
                    if pred and tgt and (pred, tgt) not in seen_rel:
                        rels.append({"predicate": pred, "target_name": tgt})
                        seen_rel.add((pred, tgt))

                note_content = render_entity_note(e_dict, subject_facts, rels, backlinks, entity_map)
                safe_name = re.sub(r'[\\/*?:"<>|]', "", e_name)
                target = safe_vault_target(self.vault_dir, self.vault_dir / "entities" / f"{safe_name}.md")
                atomic_write(target, note_content)
                entities_written += 1

            # 6. Project Daily Notes
            daily_groups: dict[str, dict[str, list]] = {}
            for v_list in lineages.values():
                for v in v_list:
                    rec = v["recorded_at"]
                    if not rec:
                        continue
                    d_str = rec.strftime("%Y-%m-%d")
                    group = daily_groups.setdefault(d_str, {"created": [], "corrected": [], "retracted": []})
                    if v["version"] == 1 and v["status"] == "active":
                        group["created"].append(v)
                    elif v["version"] > 1 and v["status"] == "active":
                        group["corrected"].append(v)
                    elif v["status"] == "retracted":
                        group["retracted"].append(v)

            daily_written = 0
            for d_str, g in daily_groups.items():
                content = render_daily_note(d_str, g["created"], g["corrected"], g["retracted"])
                target = safe_vault_target(self.vault_dir, self.vault_dir / "daily" / f"{d_str}.md")
                atomic_write(target, content)
                daily_written += 1

            logger.info(
                f"Vault rebuild complete: {memories_written} memories, {entities_written} entities, {daily_written} daily notes."
            )
            return {
                "memories": memories_written,
                "entities": entities_written,
                "daily": daily_written,
            }


async def rebuild_vault(pool: Optional[asyncpg.Pool] = None, vault_dir: Optional[Path] = None) -> dict[str, int]:
    """Helper entrypoint for CLI command `lumen-memory vault rebuild`."""
    if not pool:
        from app.core.config import settings
        from app.adapters.storage.pg_pool import PostgresPoolManager
        storage = PostgresPoolManager(dsn=settings.database_url)
        await storage.connect()
        try:
            projector = VaultProjector(storage.pool, vault_dir=vault_dir)
            return await projector.rebuild()
        finally:
            await storage.disconnect()
    else:
        projector = VaultProjector(pool, vault_dir=vault_dir)
        return await projector.rebuild()
