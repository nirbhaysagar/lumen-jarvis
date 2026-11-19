"""
Obsidian Knowledge Vault Synchronizer.
Transforms PostgreSQL facts, bi-temporal records, and compacted episodes into plain Markdown files with [[Wikilinks]].
Compatible with Obsidian, Logseq, and Foam.
"""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ObsidianVaultSync:
    def __init__(self, vault_path: str | Path | None = None) -> None:
        if vault_path:
            self.vault_path = Path(vault_path)
        else:
            # Dynamically locate project root
            current_file = Path(__file__).resolve()
            root = current_file.parent
            for _ in range(5):
                if (root / "infra").exists() or (root / ".git").exists():
                    break
                root = root.parent
            self.vault_path = root / "vault"

        self.memories_dir = self.vault_path / "memories"
        self.episodes_dir = self.vault_path / "episodes"
        self.daily_dir = self.vault_path / "daily"
        self.entities_dir = self.vault_path / "entities"

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.entities_dir.mkdir(parents=True, exist_ok=True)

    def _wikilinkify(self, text: str) -> str:
        """Adds wikilinks to recognized technologies, programming languages, and entities."""
        known_concepts = [
            "PostgreSQL", "pgvector", "MongoDB", "Redis", "SQLite", "MySQL",
            "Python", "TypeScript", "JavaScript", "Rust", "Golang", "C++",
            "FastAPI", "React", "Next.js", "Docker", "Asyncio", "Lumen", "JARVIS",
            "VS Code", "Cursor", "Linux", "HNSW"
        ]
        out = text
        for concept in known_concepts:
            pattern = rf"\b({re.escape(concept)})\b"
            # Avoid double-linking if already wikilinked
            if f"[[{concept}]]" not in out:
                out = re.sub(pattern, r"[[\1]]", out, flags=re.IGNORECASE)
        return out

    def export_fact(self, fact: dict[str, Any]) -> Path:
        """Exports an individual canonical fact into an atomic Markdown note with YAML frontmatter."""
        entity = fact.get("entity", "unknown")
        attribute = fact.get("attribute", "unknown")
        val = fact.get("value", "")
        fact_id = fact.get("id", "unknown")
        valid_from = fact.get("valid_from", datetime.now(timezone.utc).isoformat())
        valid_to = fact.get("valid_to")
        status = "ACTIVE" if not valid_to else "SUPERSEDED"

        slug = f"{entity}_{attribute}".replace("/", "_").replace(" ", "_")
        filename = f"{slug}.md"
        filepath = self.memories_dir / filename

        wikilinked_value = self._wikilinkify(str(val))

        content = f"""---
id: {fact_id}
entity: {entity}
attribute: {attribute}
memory_type: {fact.get("memory_type", "fact")}
epistemic_type: {fact.get("epistemic_type", "observed_fact")}
valid_from: '{valid_from}'
valid_to: {f"'{valid_to}'" if valid_to else 'null'}
status: {status}
tags:
  - lumen/memory
  - domain/{entity}
---

# [[{entity}]] ❯ [[{attribute}]]

> **Statement:** {wikilinked_value}

---

### 🧬 Provenance & Traceability
* **Fact ID:** `{fact_id}`
* **Validity Status:** `{status}`
* **Valid From:** `{valid_from}`
* **Valid Until:** `{valid_to or 'PRESENT (ACTIVE)'}`
* **Linked Concepts:** [[{entity}]], [[{attribute}]], [[Lumen Architecture]]
"""
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        return filepath

    def export_episode(
        self,
        session_id: str,
        summary: str,
        turn_count: int,
        evacuated_turns: list[dict[str, str]] | None = None,
    ) -> Path:
        """Exports a compacted conversation episode to Markdown."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"episode_{session_id}_{timestamp}.md"
        filepath = self.episodes_dir / filename

        turns_section = ""
        if evacuated_turns:
            turns_lines = ["\n### 📜 Condensed Conversation Turns\n"]
            for t in evacuated_turns:
                role = t.get("role", "unknown").upper()
                text = t.get("content", "").strip()
                turns_lines.append(f"* **{role}:** {text}")
            turns_section = "\n".join(turns_lines)

        content = f"""---
session_id: {session_id}
timestamp: '{datetime.now(timezone.utc).isoformat()}'
turn_count: {turn_count}
tags:
  - lumen/episode
  - compaction/condensed
---

# 🧠 Conversation Episode: `{session_id}`

> **Compacted Synopsis:**
> {self._wikilinkify(summary)}

{turns_section}

---
*Generated automatically by Lumen Context Compaction Engine.*
"""
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        return filepath

    def _sanitize_name(self, name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def export_entity(self, entity: dict[str, Any]) -> Path:
        """Exports an entity node into an Obsidian Markdown note with [[Wikilinks]]."""
        canonical_name = entity.get("canonical_name", "Unknown")
        entity_id = entity.get("id", "unknown")
        category = entity.get("category", "concept")
        aliases = entity.get("aliases", [])
        description = entity.get("description", "") or f"Canonical entity representing {canonical_name}."

        clean_filename = f"{self._sanitize_name(canonical_name)}.md"
        filepath = self.entities_dir / clean_filename

        alias_list_str = "\n".join([f"  - '{a}'" for a in aliases]) if aliases else "  []"
        alias_badges = ", ".join([f"[[{a}]]" for a in aliases]) if aliases else "*None recorded*"

        # Preserve existing relationships if file already exists
        existing_relations_block = ""
        if filepath.exists():
            old_text = filepath.read_text(encoding="utf-8")
            if "### 🌐 Knowledge Graph Connections" in old_text:
                parts = old_text.split("### 🌐 Knowledge Graph Connections")
                if len(parts) > 1:
                    existing_relations_block = "\n### 🌐 Knowledge Graph Connections" + parts[1]

        if not existing_relations_block:
            existing_relations_block = """
### 🌐 Knowledge Graph Connections
| Direction | Relation | Entity | Confidence | Status |
| :--- | :--- | :--- | :--- | :--- |
"""

        content = f"""---
id: {entity_id}
canonical_name: {canonical_name}
category: {category}
aliases:
{alias_list_str}
tags:
  - lumen/entity
  - category/{category}
---

# [[{canonical_name}]]

> {self._wikilinkify(description)}

* **Known Aliases:** {alias_badges}
* **Category:** `#{category}`
* **Entity ID:** `{entity_id}`

{existing_relations_block.strip()}
"""
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        return filepath

    def export_triple(self, triple: dict[str, Any]) -> tuple[Path, Path]:
        """Updates subject and object entity notes with bi-directional Wikilink relationships."""
        sub_name = triple.get("subject_name") or str(triple.get("subject_id", "Unknown"))
        obj_name = triple.get("object_name") or str(triple.get("object_id", "Unknown"))
        predicate = triple.get("predicate", "RELATES_TO")
        confidence = float(triple.get("confidence", 1.0))
        valid_to = triple.get("valid_to")
        status = "ACTIVE" if not valid_to else "SUPERSEDED"

        # Ensure base notes exist for both entities
        sub_path = self.entities_dir / f"{self._sanitize_name(sub_name)}.md"
        if not sub_path.exists():
            self.export_entity({"canonical_name": sub_name, "category": "auto_created"})

        obj_path = self.entities_dir / f"{self._sanitize_name(obj_name)}.md"
        if not obj_path.exists():
            self.export_entity({"canonical_name": obj_name, "category": "auto_created"})

        # Outbound link on Subject
        outbound_row = f"| Outbound (→) | [[{predicate}]] | [[{obj_name}]] | {confidence:.2f} | {status} |"
        sub_content = sub_path.read_text(encoding="utf-8")
        if outbound_row not in sub_content:
            if "| :--- | :--- | :--- | :--- | :--- |" in sub_content:
                sub_content = sub_content.replace(
                    "| :--- | :--- | :--- | :--- | :--- |",
                    f"| :--- | :--- | :--- | :--- | :--- |\n{outbound_row}",
                )
            else:
                sub_content += f"\n\n### 🌐 Knowledge Graph Connections\n| Direction | Relation | Entity | Confidence | Status |\n| :--- | :--- | :--- | :--- | :--- |\n{outbound_row}\n"
            sub_path.write_text(sub_content.strip() + "\n", encoding="utf-8")

        # Inbound link on Object
        inbound_row = f"| Inbound (←) | [[{predicate}]] | [[{sub_name}]] | {confidence:.2f} | {status} |"
        obj_content = obj_path.read_text(encoding="utf-8")
        if inbound_row not in obj_content:
            if "| :--- | :--- | :--- | :--- | :--- |" in obj_content:
                obj_content = obj_content.replace(
                    "| :--- | :--- | :--- | :--- | :--- |",
                    f"| :--- | :--- | :--- | :--- | :--- |\n{inbound_row}",
                )
            else:
                obj_content += f"\n\n### 🌐 Knowledge Graph Connections\n| Direction | Relation | Entity | Confidence | Status |\n| :--- | :--- | :--- | :--- | :--- |\n{inbound_row}\n"
            obj_path.write_text(obj_content.strip() + "\n", encoding="utf-8")

        return sub_path, obj_path

    def get_vault_stats(self) -> dict[str, int]:
        """Returns counts of generated Markdown files across vault categories."""
        return {
            "entities": len(list(self.entities_dir.glob("*.md"))),
            "memories": len(list(self.memories_dir.glob("*.md"))),
            "episodes": len(list(self.episodes_dir.glob("*.md"))),
            "daily": len(list(self.daily_dir.glob("*.md"))),
            "total_notes": len(list(self.vault_path.rglob("*.md"))),
        }
