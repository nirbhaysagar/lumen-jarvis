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
        self.digests_dir = self.vault_path / "digests"
        self.research_dir = self.vault_path / "research"
        self.sentinel_dir = self.vault_path / "sentinel"
        self.sentinel_incidents_dir = self.sentinel_dir / "incidents"

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.entities_dir.mkdir(parents=True, exist_ok=True)
        self.digests_dir.mkdir(parents=True, exist_ok=True)
        self.research_dir.mkdir(parents=True, exist_ok=True)
        self.sentinel_dir.mkdir(parents=True, exist_ok=True)
        self.sentinel_incidents_dir.mkdir(parents=True, exist_ok=True)

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

        slug = f"{entity}_{attribute}"
        filepath = self._safe_vault_target(self.memories_dir, slug)

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
        clean = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
        clean = re.sub(r"\.\.+", "_", clean).strip(" _")
        return clean or "unnamed"

    def _safe_vault_target(self, target_dir: Path, raw_name: str) -> Path:
        clean = self._sanitize_name(raw_name)
        target = (target_dir / f"{clean}.md").resolve()
        if not target.is_relative_to(self.vault_path.resolve()):
            raise ValueError(f"Path traversal detected in vault note name: '{raw_name}'")
        return target

    def export_entity(self, entity: dict[str, Any]) -> Path:
        """Exports an entity node into an Obsidian Markdown note with [[Wikilinks]]."""
        canonical_name = entity.get("canonical_name", "Unknown")
        entity_id = entity.get("id", "unknown")
        category = entity.get("category", "concept")
        aliases = entity.get("aliases", [])
        description = entity.get("description", "") or f"Canonical entity representing {canonical_name}."

        filepath = self._safe_vault_target(self.entities_dir, canonical_name)

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

    def export_digest(
        self,
        date_str: str | None = None,
        report: dict[str, Any] | None = None,
        pending_proposals: list[dict[str, Any]] | None = None,
        active_tasks: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Exports a Morning Intelligence Digest to Markdown in vault/digests/."""
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        filename = f"digest_{date_str}.md"
        filepath = self.digests_dir / filename

        report_data = report or {}
        conflicts = report_data.get("conflicts_detected", 0)
        aliases = report_data.get("alias_candidates_found", 0)
        orphans = report_data.get("orphans_detected", 0)
        staged = report_data.get("proposals_staged", 0)

        # Pending proposals section
        proposals_lines = []
        if pending_proposals:
            for p in pending_proposals:
                p_id = p.get("id", "unknown")
                p_type = p.get("proposal_type", "NEW_TRIPLE")
                sub = p.get("subject_raw", "")
                pred = p.get("predicate_raw", "")
                obj = p.get("object_raw", "")
                reason = p.get("conflict_reason") or "No conflict reason specified"
                proposals_lines.append(
                    f"* **`[{p_type}]` [[{sub}]] `{pred}` [[{obj}]]** (ID: `{p_id}`)\n"
                    f"  > *Reason:* {reason}\n"
                    f"  > *Action:* Approve with `/approve-proposal {p_id}` or Reject with `/reject-proposal {p_id}`"
                )
        proposals_section = "\n".join(proposals_lines) if proposals_lines else "*No decisions currently pending review.*"

        # Active tasks section
        tasks_lines = []
        if active_tasks:
            for t in active_tasks:
                t_id = t.get("task_id", "unknown")
                name = t.get("name", "unnamed")
                state = t.get("state", "UNKNOWN")
                prog = int(float(t.get("fractional_progress", 0.0)) * 100)
                tasks_lines.append(f"* **`[{state}]` {name}** (`{prog}%`) — ID: `{t_id}`")
        tasks_section = "\n".join(tasks_lines) if tasks_lines else "*No active background tasks running.*"

        content = f"""---
date: '{date_str}'
type: morning_digest
conflicts_detected: {conflicts}
alias_candidates_found: {aliases}
orphans_detected: {orphans}
proposals_staged: {staged}
tags:
  - lumen/digest
  - daily/briefing
---

# 🌅 Lumen Morning Intelligence Digest — `{date_str}`

> Autonomous overnight consolidation completed. Knowledge graph integrity verified.

---

### 📊 Consolidation Patrol Summary
* **Identified Conflicts / Collisions:** `{conflicts}`
* **High-Similarity Alias Candidates:** `{aliases}`
* **Graph Orphan Nodes ($d \\le 1$):** `{orphans}`
* **New Review Proposals Staged:** `{staged}`

---

### 🛡️ Actions Requiring Operator Approval (The Review Queue)
{proposals_section}

---

### ⚙️ Background Concurrency & Task Telemetry
{tasks_section}

---
*Generated automatically by Lumen Overnight Consolidation Daemon.*
"""
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        return filepath

    def export_research_dossier(self, dossier: Any) -> Path:
        """
        Exports an autonomous deep research dossier into the Obsidian Vault under vault/research/{topic_slug}.md.
        """
        # Support either ResearchDossier instance or dict
        if hasattr(dossier, "model_dump"):
            data = dossier.model_dump()
        elif isinstance(dossier, dict):
            data = dossier
        else:
            data = vars(dossier)

        topic = data.get("topic", "Research Topic")
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", topic.lower()).strip("_")
        filepath = self._safe_vault_target(self.research_dir, slug)

        summary = data.get("summary", "")
        key_findings = data.get("key_findings", [])
        claims = data.get("claims", [])
        sources = data.get("sources", [])
        gen_time = data.get("generated_at", datetime.now(timezone.utc).isoformat())

        findings_lines = "\n".join(f"* {self._wikilinkify(f)}" for f in key_findings) if key_findings else "* No specific findings recorded."

        claims_lines = []
        for c in claims:
            c_text = c.get("claim", "") if isinstance(c, dict) else getattr(c, "claim", "")
            author = c.get("author") if isinstance(c, dict) else getattr(c, "author", None)
            url = c.get("source_url") if isinstance(c, dict) else getattr(c, "source_url", "")
            src_type = c.get("source_type") if isinstance(c, dict) else getattr(c, "source_type", "web")
            author_str = f" — *{author}*" if author else ""
            claims_lines.append(f"> \"{c_text}\"{author_str}\n>\n> [Source ({src_type})]({url})\n")

        claims_section = "\n".join(claims_lines) if claims_lines else "* No verbatim claims extracted."

        source_rows = []
        for s in sources:
            s_title = s.get("title", "") if isinstance(s, dict) else getattr(s, "title", "")
            s_type = s.get("source_type", "") if isinstance(s, dict) else getattr(s, "source_type", "")
            s_url = s.get("url", "") if isinstance(s, dict) else getattr(s, "url", "")
            source_rows.append(f"| {s_title[:60]} | `{s_type}` | [Link]({s_url}) |")

        sources_table = (
            "| Title | Category | Link |\n| :--- | :--- | :--- |\n" + "\n".join(source_rows)
            if source_rows else "* No source references recorded."
        )

        content = f"""---
title: "Research Dossier: {topic}"
topic: "{topic}"
generated_at: "{gen_time}"
source_count: {len(sources)}
claim_count: {len(claims)}
tags:
  - type/research
  - status/synthesized
---

# 🔍 Research Dossier: {self._wikilinkify(topic)}

## 📋 Executive Summary
{self._wikilinkify(summary)}

---

## 🎯 Key Technical Findings
{findings_lines}

---

## 💬 Practitioner Quotes & Empirical Claims
{claims_section}

---

## 🌐 Explored Knowledge Sources
{sources_table}

---
*Autonomous deep research compiled by Lumen ("The Eyes") via Multi-Source Discovery & Recursive Citation Crawling.*
"""
        filepath.write_text(content.strip() + "\n", encoding="utf-8")
        return filepath

    def export_sentinel_report(self, report: Any) -> Path:
        """
        Exports an Autonomous Sentinel Patrol Report into Obsidian Vault under vault/sentinel/latest.md.
        If warning or critical findings are detected, also archives an incident note.
        """
        if hasattr(report, "model_dump"):
            data = report.model_dump(mode="json")
        elif isinstance(report, dict):
            data = report
        else:
            data = vars(report)

        patrol_id = data.get("patrol_id", "patrol")
        raw_status = data.get("overall_status", "ok")
        if hasattr(raw_status, "value"):
            raw_status = raw_status.value
        status = str(raw_status).upper()
        ts = data.get("timestamp", datetime.now(timezone.utc).timestamp())
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        iso_str = dt.isoformat()

        findings = data.get("findings", [])
        system = data.get("system_metrics", {})
        containers = data.get("containers_monitored", [])
        git_info = data.get("git_status", {})
        duration_ms = data.get("duration_ms", 0.0)

        disk = system.get("disk", {})
        ram = system.get("memory", {})
        cpu = system.get("cpu", {})

        # Build findings markdown
        findings_lines = []
        for f in findings:
            f_title = f.get("title", "")
            raw_sev = f.get("severity", "info")
            if hasattr(raw_sev, "value"):
                raw_sev = raw_sev.value
            f_sev = str(raw_sev).upper()
            f_det = f.get("details", "")
            f_rec = f.get("recommendation", "")

            badge = "🚨 [CRITICAL]" if f_sev == "CRITICAL" else ("⚠️ [WARNING]" if f_sev == "WARNING" else "ℹ️ [INFO]")
            rec_line = f"\n  > 👉 **Action:** {f_rec}" if f_rec else ""
            findings_lines.append(f"* **{badge} {f_title}**\n  > {f_det}{rec_line}")

        findings_block = "\n".join(findings_lines) if findings_lines else "* All system metrics within operational baseline. Zero anomalies detected."

        # Build containers table
        container_rows = []
        for c in containers:
            c_name = c.get("name", "")
            c_state = c.get("state", "").upper()
            c_health = c.get("health", "").upper()
            c_status = c.get("status", "")
            container_rows.append(f"| `{c_name}` | `{c_state}` | `{c_health}` | {c_status} |")

        containers_table = (
            "| Container | State | Health | Details |\n| :--- | :--- | :--- | :--- |\n" + "\n".join(container_rows)
            if container_rows else "* No active or managed Docker containers detected."
        )

        content = f"""---
title: "Sentinel Health Patrol: {dt.strftime('%Y-%m-%d %H:%M:%S')}"
patrol_id: "{patrol_id}"
timestamp: "{iso_str}"
overall_status: "{status}"
duration_ms: {duration_ms:.2f}
alert_count: {len(findings)}
tags:
  - type/sentinel
  - status/{status.lower()}
---

# 🛡️ Sentinel System Patrol Report (`{status}`)

* **Patrol ID:** `{patrol_id}`
* **Conducted:** {dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({duration_ms:.1f}ms scan)
* **Overall Posture:** `{status}`

---

### 💻 Physical Host Resources
* **Disk (`/`):** `{disk.get('percent', 0.0)}%` utilized ({disk.get('free_gb', 0.0)} GB free / {disk.get('total_gb', 0.0)} GB total)
* **Memory (RAM):** `{ram.get('percent', 0.0)}%` utilized ({ram.get('available_gb', 0.0)} GB available / {ram.get('total_gb', 0.0)} GB total)
* **CPU Load:** `{cpu.get('percent', 0.0)}%` ({cpu.get('logical_cores', 1)} cores)

---

### 🐳 Managed Infrastructure Containers
{containers_table}

---

### 🌿 Git Workspace State
* **Branch:** `{git_info.get('branch', 'unknown')}` ({'DETACHED HEAD' if git_info.get('is_detached') else 'Normal tracking'})
* **Status:** {'CLEAN' if git_info.get('is_clean') else f"DIRTY ({git_info.get('staged_count', 0)} staged, {git_info.get('unstaged_count', 0)} unstaged, {git_info.get('untracked_count', 0)} untracked)"}

---

### 🔍 Patrol Findings & Recommendations
{findings_block}

---
*Autonomous background patrol conducted by Lumen Sentinel Daemon ("The Watchman").*
"""
        # Save to latest.md
        latest_file = self.sentinel_dir / "latest.md"
        latest_file.write_text(content.strip() + "\n", encoding="utf-8")

        # If warnings or critical findings, write an immutable incident note
        has_alerts = any(
            (f.get("severity").value if hasattr(f.get("severity"), "value") else str(f.get("severity"))).lower() in ("warning", "critical")
            for f in findings
        )
        if has_alerts:
            stamp = dt.strftime("%Y-%m-%d_%H%M%S")
            incident_file = self.sentinel_incidents_dir / f"incident_{stamp}_{patrol_id[:8]}.md"
            incident_file.write_text(content.strip() + "\n", encoding="utf-8")

        return latest_file

    def get_vault_stats(self) -> dict[str, int]:
        """Returns counts of generated Markdown files across vault categories."""
        return {
            "entities": len(list(self.entities_dir.glob("*.md"))),
            "memories": len(list(self.memories_dir.glob("*.md"))),
            "episodes": len(list(self.episodes_dir.glob("*.md"))),
            "daily": len(list(self.daily_dir.glob("*.md"))),
            "digests": len(list(self.digests_dir.glob("*.md"))),
            "research": len(list(self.research_dir.glob("*.md"))),
            "sentinel": len(list(self.sentinel_dir.glob("*.md"))) + len(list(self.sentinel_incidents_dir.glob("*.md"))),
            "total_notes": len(list(self.vault_path.rglob("*.md"))),
        }
