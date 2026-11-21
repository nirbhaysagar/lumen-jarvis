"""
Document & File Digestion Engine for Lumen Vault (document_vault).
Enables parsing, inspection, and memory engine ingestion for:
- Invoices and research reports (.pdf)
- Financial datasets and spreadsheets (.csv, .tsv, .xlsx, .xls)
- Obsidian / Markdown vaults and documentation (.md, .txt)
- Visual asset metadata (.png, .jpg, .webp)

Bridges unstructured documents and tabular data directly into:
1. The standalone Memory Engine (PostgreSQL pgvector embeddings + knowledge graph).
2. The Analyst Engine (Pandas schemas, sample records, and execution paths).
3. The Obsidian Knowledge Vault (vault/documents/<slug>.md).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from src.vault.document_parser import DocumentParser, DocumentType, ParsedDocument
from src.vault.vault_sync import ObsidianVaultSync
from src.memory_client.client import MemoryServiceClient

logger = logging.getLogger("lumen.document_vault")


class IngestionSummary(BaseModel):
    target_path: str
    total_files_scanned: int = 0
    total_files_ingested: int = 0
    total_chunks_indexed: int = 0
    total_tables_detected: int = 0
    documents: List[ParsedDocument] = Field(default_factory=list)
    dossier_paths: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class DocumentVault:
    """
    Coordinates local file discovery, content parsing, vector ingestion,
    and analytical table formulation.
    """

    SUPPORTED_EXTENSIONS = {
        ".pdf", ".csv", ".tsv", ".xlsx", ".xls",
        ".md", ".markdown", ".txt", ".json", ".yaml", ".yml",
        ".png", ".jpg", ".jpeg", ".webp"
    }

    IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv",
        ".pytest_cache", ".idea", ".vscode", "dist", "build"
    }

    def __init__(
        self,
        memory_client: Optional[MemoryServiceClient] = None,
        vault_sync: Optional[ObsidianVaultSync] = None,
    ) -> None:
        self.memory = memory_client
        self.vault = vault_sync or ObsidianVaultSync()
        self.parser = DocumentParser()

    @classmethod
    def find_local_files_in_text(cls, text: str) -> List[Path]:
        """
        Extracts existing local file or directory paths referenced in a user utterance.
        Recognizes tilde (~), absolute (/), relative (./), and bare filenames.
        """
        candidates: Set[str] = set()

        # 1. Quoted paths: "path/to/file.ext" or 'path/to/file.ext'
        for m in re.finditer(r"['\"]([^'\"]+?\.[a-zA-Z0-9]{2,4})['\"]", text):
            candidates.add(m.group(1).strip())

        # 2. Paths with supported extensions
        ext_pattern = r"(?:~|\/|\.\/|[a-zA-Z0-9_\-\.\/]+)\.(?:csv|tsv|xlsx|xls|pdf|md|txt|json|yaml|yml|png|jpg|jpeg|webp)\b"
        for m in re.finditer(ext_pattern, text, re.IGNORECASE):
            candidates.add(m.group(0).strip())

        # 3. Explicit directories mentioned after 'at', 'in', 'from', 'folder'
        dir_pattern = r"(?:at|in|from|folder|directory)\s+([~a-zA-Z0-9_\-\.\/]+)"
        for m in re.finditer(dir_pattern, text, re.IGNORECASE):
            raw = m.group(1).strip().rstrip(".,;!?")
            if "/" in raw or "~" in raw:
                candidates.add(raw)

        resolved_paths: List[Path] = []
        for cand in candidates:
            try:
                p = Path(cand).expanduser()
                if not p.is_absolute():
                    # Check relative to cwd
                    p = Path.cwd() / p
                if p.exists():
                    resolved_paths.append(p.resolve())
            except Exception:
                continue

        # Sort and deduplicate preserving order
        seen = set()
        unique = []
        for p in resolved_paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def inspect(self, path: str | Path) -> ParsedDocument:
        """Parses and returns structured representation of a single file."""
        return self.parser.parse(path)

    async def ingest(
        self,
        target_path: str | Path,
        conversation_id: Optional[str] = None,
        max_files: int = 100,
    ) -> IngestionSummary:
        """
        Scans a file or directory, parses supported files, writes memory events and facts,
        and saves Markdown document summaries into vault/documents/.
        """
        resolved = Path(target_path).expanduser().resolve()
        summary = IngestionSummary(target_path=str(resolved))

        if not resolved.exists():
            summary.errors.append(f"Target path does not exist: {resolved}")
            return summary

        files_to_process: List[Path] = []
        if resolved.is_file():
            files_to_process.append(resolved)
        elif resolved.is_dir():
            for root, dirs, files in os.walk(resolved):
                # Filter out ignored directories
                dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".")]
                for f in files:
                    fp = Path(root) / f
                    if fp.suffix.lower() in self.SUPPORTED_EXTENSIONS and not f.startswith("."):
                        files_to_process.append(fp)
                        if len(files_to_process) >= max_files:
                            break
                if len(files_to_process) >= max_files:
                    break

        summary.total_files_scanned = len(files_to_process)

        for fp in files_to_process:
            try:
                doc = self.parser.parse(fp)
                summary.documents.append(doc)
                summary.total_files_ingested += 1
                summary.total_chunks_indexed += len(doc.chunks)
                summary.total_tables_detected += len(doc.tables)

                # 1. Ingest into Standalone Memory Service (if active)
                if self.memory:
                    await self._ingest_doc_to_memory(doc, conversation_id)

                # 2. Archive Markdown Note in Obsidian Vault
                dossier_rel = self._export_document_dossier(doc)
                if dossier_rel:
                    summary.dossier_paths.append(dossier_rel)

            except Exception as e:
                logger.error(f"Failed to ingest {fp}: {e}")
                summary.errors.append(f"{fp.name}: {str(e)}")

        return summary

    async def _ingest_doc_to_memory(self, doc: ParsedDocument, conversation_id: Optional[str]) -> None:
        """Publishes document receipts, canonical facts, and knowledge triples to the memory engine."""
        try:
            # A. Ingest raw event receipt (L0)
            event_res = await self.memory.save_event(
                source="document_vault",
                payload={
                    "file_name": doc.file_name,
                    "file_path": str(doc.file_path),
                    "file_size": doc.file_size_bytes,
                    "doc_type": doc.doc_type.value,
                    "title": doc.title,
                    "total_chunks": len(doc.chunks),
                    "total_tables": len(doc.tables),
                    "metadata": doc.metadata,
                },
                conversation_id=conversation_id,
            )
            event_id = event_res.get("id") if event_res else "evt_local_doc"

            # B. Save canonical document facts (L1) with vector embeddings
            for chunk in doc.chunks[:15]:  # Bound initial per-file facts to avoid saturating rate limits
                attr_name = f"chunk_{chunk.index}"
                await self.memory.save_fact(
                    entity=doc.title,
                    attribute=attr_name,
                    value=chunk.text[:1200],
                    source_event_ids=[event_id] if event_id else [],
                    memory_type="document",
                    epistemic_type="observed_fact",
                )

            # C. Propose Relational Triples for Knowledge Graph
            await self.memory.propose_triple(
                subject="User",
                predicate="INGESTED_DOCUMENT",
                object_=doc.title,
                source_event_ids=[event_id] if event_id else [],
            )

            if doc.tables:
                await self.memory.propose_triple(
                    subject=doc.title,
                    predicate="CONTAINS_DATASET",
                    object_=f"{doc.file_name}_data",
                    source_event_ids=[event_id] if event_id else [],
                )

        except Exception as e:
            logger.warning(f"Memory engine ingestion encountered non-fatal error for {doc.file_name}: {e}")

    def _export_document_dossier(self, doc: ParsedDocument) -> str:
        """Generates an auditable, reproducible Markdown dossier in vault/documents/<slug>.md."""
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", doc.file_name.lower()).strip("_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{slug}_{timestamp}.md"
        out_path = self.vault.documents_dir / filename

        tables_section = ""
        if doc.tables:
            tables_parts = ["### 📊 Extracted Tabular Schemas"]
            for idx, tbl in enumerate(doc.tables, 1):
                sheet_lbl = f"Sheet: `{tbl.sheet_name}`" if tbl.sheet_name else f"Dataset {idx}"
                tables_parts.append(
                    f"#### {sheet_lbl}\n"
                    f"* **Dimensions:** `{tbl.shape[0]} rows × {tbl.shape[1]} columns`\n"
                    f"* **Columns:** `{', '.join(tbl.columns)}`\n\n"
                    f"##### Preview (Top Records):\n{tbl.head_markdown}\n\n"
                    f"> [!info]- Summary Statistics\n> ```text\n> "
                    f"{tbl.summary_stats_markdown.replace(chr(10), chr(10) + '> ')}\n> ```\n"
                )
            tables_section = "\n".join(tables_parts)

        chunks_section = ""
        if doc.chunks:
            chunk_snippets = [f"**[Chunk {c.index}]**\n{c.text[:400]}..." for c in doc.chunks[:5]]
            chunks_section = "### 📑 Content Passages\n" + "\n\n".join(chunk_snippets)

        metadata_yaml = "\n".join([f"  {k}: '{v}'" for k, v in doc.metadata.items()])

        dossier_content = f"""---
title: '{doc.title.replace("'", "")}'
date: '{datetime.now().strftime('%Y-%m-%d')}'
type: ingested_document
file_name: '{doc.file_name}'
file_path: '{str(doc.file_path)}'
doc_type: '{doc.doc_type.value}'
file_size_bytes: {doc.file_size_bytes}
tags:
  - lumen/document_vault
  - format/{doc.doc_type.value}
metadata:
{metadata_yaml if metadata_yaml else "  source: local_file"}
---

# 📄 Ingested Document: {doc.title}

* **File Path:** `{doc.file_path}`
* **Format:** `{doc.doc_type.value.upper()}` ({doc.file_size_bytes / 1024:.1f} KB)
* **Total Chunks:** `{len(doc.chunks)}` | **Tables Detected:** `{len(doc.tables)}`
* **Prompt Injection Shield:** `{'⚠️ QUARANTINED THREAT DETECTED' if doc.has_injection_risk else '✅ VERIFIED CLEAN'}`

---

### 📝 Executive Summary
{doc.summary_text}

{tables_section}

{chunks_section}
"""
        out_path.write_text(dossier_content, encoding="utf-8")
        return f"vault/documents/{filename}"

    async def process(
        self,
        path: str,
        action: str = "inspect",
        query: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Streaming execution handler for Lumen's ReAct agent loop.
        Yields human-readable step-by-step progress tokens.
        """
        clean_path = path.strip().strip("'\"")
        yield f"📂 Accessing target path: `{clean_path}`...\n"

        resolved = Path(clean_path).expanduser().resolve()
        if not resolved.exists():
            yield f"❌ Error: Path does not exist: `{clean_path}`\n"
            return

        if action.lower() == "ingest" or resolved.is_dir():
            yield f"🔄 Ingesting documents into persistent memory & Obsidian vault...\n"
            summary = await self.ingest(resolved)
            yield f"✅ Ingestion complete: {summary.total_files_ingested}/{summary.total_files_scanned} files processed.\n"
            yield f"• Indexed {summary.total_chunks_indexed} semantic passages across {summary.total_tables_detected} datasets.\n"
            for d_path in summary.dossier_paths[:3]:
                full = self.vault.vault_path.parent / d_path
                yield f"💾 Document dossier archived: [`{d_path}`](file://{full.resolve()})\n"
            if len(summary.dossier_paths) > 3:
                yield f"• ...and {len(summary.dossier_paths) - 3} additional files.\n"
            if summary.errors:
                yield f"⚠️ Notices: {', '.join(summary.errors[:2])}\n"
        else:
            yield f"🔍 Inspecting document structure and extracting schemas...\n"
            doc = self.inspect(resolved)
            yield f"📄 **{doc.title}** (`{doc.doc_type.value.upper()}`, {doc.file_size_bytes / 1024:.1f} KB)\n\n"
            yield f"{doc.summary_text}\n\n"
            if doc.tables:
                for idx, t in enumerate(doc.tables, 1):
                    sheet_info = f"Sheet: `{t.sheet_name}` " if t.sheet_name else ""
                    yield f"📊 **Dataset {idx}** ({sheet_info}{t.shape[0]} rows × {t.shape[1]} cols):\n```text\n{t.head_markdown}\n```\n\n"
            elif doc.chunks:
                preview = doc.chunks[0].text[:300]
                yield f"📝 **Content Sample:**\n```text\n{preview}...\n```\n\n"
