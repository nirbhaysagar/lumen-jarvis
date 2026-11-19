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

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

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

    def get_vault_stats(self) -> dict[str, int]:
        """Returns counts of generated Markdown files across vault categories."""
        return {
            "memories": len(list(self.memories_dir.glob("*.md"))),
            "episodes": len(list(self.episodes_dir.glob("*.md"))),
            "daily": len(list(self.daily_dir.glob("*.md"))),
            "total_notes": len(list(self.vault_path.rglob("*.md"))),
        }
