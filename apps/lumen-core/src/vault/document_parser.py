"""
Multi-Format Document & File Parser for Lumen Vault.
Extracts structured text, tabular schemas, and metadata from:
- PDFs (pypdf with indirect prompt injection shielding)
- Spreadsheets (.csv, .tsv, .xlsx, .xls via Pandas & OpenPyXL)
- Text and Markdown vaults (.md, .txt, .json, .yaml, .rst)
- Images (.png, .jpg, .jpeg, .webp via Pillow)
"""
from __future__ import annotations
import os
import re
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

import pandas as pd
from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

from src.research.extractor import PromptInjectionShield

logger = logging.getLogger("lumen.document_parser")


class DocumentType(str, Enum):
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    MARKDOWN = "markdown"
    TEXT = "text"
    IMAGE = "image"
    UNKNOWN = "unknown"


class DocumentChunk(BaseModel):
    index: int
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TabularSummary(BaseModel):
    sheet_name: Optional[str] = None
    shape: Tuple[int, int]
    columns: List[str]
    dtypes: Dict[str, str]
    head_markdown: str
    summary_stats_markdown: str = ""
    row_sample: List[Dict[str, Any]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    file_path: Path
    file_name: str
    file_size_bytes: int
    doc_type: DocumentType
    title: str
    summary_text: str
    chunks: List[DocumentChunk] = Field(default_factory=list)
    tables: List[TabularSummary] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    has_injection_risk: bool = False
    quarantined_blocks: List[str] = Field(default_factory=list)


class DocumentParser:
    """Coordinates parsing across all supported document and spreadsheet types."""

    def __init__(self) -> None:
        self.shield = PromptInjectionShield()

    def detect_type(self, path: Path) -> DocumentType:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return DocumentType.PDF
        elif suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
            return DocumentType.SPREADSHEET
        elif suffix in {".md", ".markdown"}:
            return DocumentType.MARKDOWN
        elif suffix in {".txt", ".json", ".yaml", ".yml", ".rst", ".log", ".py", ".sh"}:
            return DocumentType.TEXT
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            return DocumentType.IMAGE
        return DocumentType.UNKNOWN

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Target document not found at: {path}")

        doc_type = self.detect_type(path)
        file_size = path.stat().st_size

        if doc_type == DocumentType.PDF:
            return self._parse_pdf(path, file_size)
        elif doc_type == DocumentType.SPREADSHEET:
            return self._parse_spreadsheet(path, file_size)
        elif doc_type in {DocumentType.MARKDOWN, DocumentType.TEXT}:
            return self._parse_text_or_markdown(path, file_size, doc_type)
        elif doc_type == DocumentType.IMAGE:
            return self._parse_image(path, file_size)
        else:
            return self._parse_fallback(path, file_size)

    def _parse_pdf(self, path: Path, file_size: int) -> ParsedDocument:
        """Extracts text, metadata, and tabular lines from PDF via pypdf with prompt shielding."""
        if pypdf is None:
            raise ImportError("pypdf is required to parse PDF documents.")

        chunks: List[DocumentChunk] = []
        raw_full_text = []
        all_quarantined = []
        has_risk = False

        reader = pypdf.PdfReader(str(path))
        num_pages = len(reader.pages)
        pdf_meta = {}
        if reader.metadata:
            for k, v in reader.metadata.items():
                clean_k = k.lstrip("/").lower()
                pdf_meta[clean_k] = str(v)

        doc_title = pdf_meta.get("title") or path.stem.replace("_", " ").title()

        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if not page_text.strip():
                continue

            sanitized, risk, quarantined = self.shield.scan_and_sanitize(page_text)
            if risk:
                has_risk = True
                all_quarantined.extend(quarantined)

            raw_full_text.append(sanitized)
            chunks.append(
                DocumentChunk(
                    index=page_idx,
                    text=sanitized,
                    metadata={"page": page_idx + 1, "total_pages": num_pages},
                )
            )

        full_content = "\n\n".join(raw_full_text)
        summary = (
            f"PDF Document: {doc_title} ({num_pages} pages, {file_size / 1024:.1f} KB)\n"
            f"Preview: {full_content[:400]}..."
        )

        return ParsedDocument(
            file_path=path,
            file_name=path.name,
            file_size_bytes=file_size,
            doc_type=DocumentType.PDF,
            title=doc_title,
            summary_text=summary,
            chunks=chunks,
            tables=[],
            metadata={"total_pages": num_pages, **pdf_meta},
            has_injection_risk=has_risk,
            quarantined_blocks=all_quarantined,
        )

    def _parse_spreadsheet(self, path: Path, file_size: int) -> ParsedDocument:
        """Parses CSV, TSV, or Excel spreadsheets into structured schemas, stats, and markdown heads."""
        suffix = path.suffix.lower()
        tables: List[TabularSummary] = []
        chunks: List[DocumentChunk] = []

        sheets_dict: Dict[str, pd.DataFrame] = {}
        if suffix in {".csv", ".tsv"}:
            sep = "\t" if suffix == ".tsv" else ","
            df = pd.read_csv(str(path), sep=sep)
            sheets_dict["default"] = df
        else:
            excel = pd.ExcelFile(str(path))
            for sheet in excel.sheet_names:
                sheets_dict[sheet] = pd.read_excel(excel, sheet_name=sheet)

        doc_title = path.stem.replace("_", " ").title()
        summary_lines = [f"Spreadsheet: {doc_title} ({len(sheets_dict)} sheet(s), {file_size / 1024:.1f} KB)"]

        chunk_idx = 0
        for sheet_name, df in sheets_dict.items():
            shape = df.shape
            cols = [str(c) for c in df.columns]
            dtypes = {str(c): str(t) for c, t in df.dtypes.items()}

            # Clean markdown representation of head
            try:
                head_md = df.head(8).to_markdown(index=False)
            except Exception:
                head_md = df.head(8).to_string(index=False)

            # Summary statistics for numeric columns
            numeric_df = df.select_dtypes(include=["number"])
            if not numeric_df.empty:
                try:
                    stats_md = numeric_df.describe().to_markdown()
                except Exception:
                    stats_md = numeric_df.describe().to_string()
            else:
                stats_md = "No numeric columns for statistical description."

            # Convert top sample rows to records
            row_sample = df.head(5).to_dict(orient="records")

            table_summary = TabularSummary(
                sheet_name=sheet_name if sheet_name != "default" else None,
                shape=shape,
                columns=cols,
                dtypes=dtypes,
                head_markdown=head_md,
                summary_stats_markdown=stats_md,
                row_sample=row_sample,
            )
            tables.append(table_summary)

            sheet_label = f"Sheet '{sheet_name}'" if sheet_name != "default" else "Dataset"
            summary_lines.append(f"• {sheet_label}: {shape[0]} rows × {shape[1]} columns. Columns: {', '.join(cols[:10])}")

            # Create text chunk for semantic search & vector memory
            chunk_text = (
                f"{doc_title} - {sheet_label}\n"
                f"Dimensions: {shape[0]} rows × {shape[1]} columns\n"
                f"Columns: {', '.join(cols)}\n\n"
                f"Sample Data (Head):\n{head_md}\n\n"
                f"Summary Statistics:\n{stats_md}"
            )
            chunks.append(
                DocumentChunk(
                    index=chunk_idx,
                    text=chunk_text,
                    metadata={
                        "sheet_name": sheet_name,
                        "rows": shape[0],
                        "columns": cols,
                    },
                )
            )
            chunk_idx += 1

        summary_text = "\n".join(summary_lines)

        return ParsedDocument(
            file_path=path,
            file_name=path.name,
            file_size_bytes=file_size,
            doc_type=DocumentType.SPREADSHEET,
            title=doc_title,
            summary_text=summary_text,
            chunks=chunks,
            tables=tables,
            metadata={"sheets": list(sheets_dict.keys()), "total_rows": sum(df.shape[0] for df in sheets_dict.values())},
            has_injection_risk=False,
            quarantined_blocks=[],
        )

    def _parse_text_or_markdown(self, path: Path, file_size: int, doc_type: DocumentType) -> ParsedDocument:
        """Parses markdown or plaintext with frontmatter extraction and prompt injection scanning."""
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="latin-1", errors="ignore")

        # Extract YAML frontmatter if markdown
        frontmatter = {}
        cleaned_content = content
        if doc_type == DocumentType.MARKDOWN:
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, flags=re.DOTALL)
            if fm_match:
                raw_fm = fm_match.group(1)
                cleaned_content = content[fm_match.end():]
                for line in raw_fm.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        frontmatter[k.strip()] = v.strip().strip("'\"")

        # Sanitize against prompt injection
        sanitized_content, has_risk, quarantined = self.shield.scan_and_sanitize(cleaned_content)

        # Infer title from frontmatter, first H1, or filename
        title = frontmatter.get("title")
        if not title:
            h1_match = re.search(r"^#\s+(.+)$", sanitized_content, flags=re.MULTILINE)
            if h1_match:
                title = h1_match.group(1).strip()
            else:
                title = path.stem.replace("_", " ").title()

        # Chunk content into ~800 char semantic passages
        chunks: List[DocumentChunk] = []
        raw_paras = [p.strip() for p in re.split(r"\n\s*\n", sanitized_content) if p.strip()]

        current_chunk_parts = []
        current_len = 0
        chunk_idx = 0

        for p in raw_paras:
            current_chunk_parts.append(p)
            current_len += len(p)
            if current_len >= 800:
                chunk_text = "\n\n".join(current_chunk_parts)
                chunks.append(DocumentChunk(index=chunk_idx, text=chunk_text, metadata={"chunk": chunk_idx}))
                chunk_idx += 1
                current_chunk_parts = []
                current_len = 0

        if current_chunk_parts:
            chunk_text = "\n\n".join(current_chunk_parts)
            chunks.append(DocumentChunk(index=chunk_idx, text=chunk_text, metadata={"chunk": chunk_idx}))

        summary = f"{doc_type.value.title()} Document: {title} ({len(chunks)} chunks, {file_size / 1024:.1f} KB)"

        return ParsedDocument(
            file_path=path,
            file_name=path.name,
            file_size_bytes=file_size,
            doc_type=doc_type,
            title=title,
            summary_text=summary,
            chunks=chunks,
            tables=[],
            metadata={"frontmatter": frontmatter, "total_characters": len(content)},
            has_injection_risk=has_risk,
            quarantined_blocks=quarantined,
        )

    def _parse_image(self, path: Path, file_size: int) -> ParsedDocument:
        """Extracts resolution, color format, and metadata from local image files."""
        try:
            with Image.open(path) as img:
                width, height = img.size
                img_format = img.format or path.suffix.upper().lstrip(".")
                mode = img.mode
                info = dict(img.info) if hasattr(img, "info") else {}
        except Exception as e:
            width, height, img_format, mode, info = 0, 0, "UNKNOWN", "UNKNOWN", {"error": str(e)}

        title = path.stem.replace("_", " ").title()
        summary = f"Image: {title} ({width}x{height} {img_format}, mode={mode}, {file_size / 1024:.1f} KB)"
        chunk_text = (
            f"Image Asset: {path.name}\n"
            f"Path: {path}\n"
            f"Dimensions: {width} x {height} pixels\n"
            f"Format: {img_format}\n"
            f"Color Mode: {mode}\n"
            f"File Size: {file_size / 1024:.2f} KB"
        )

        return ParsedDocument(
            file_path=path,
            file_name=path.name,
            file_size_bytes=file_size,
            doc_type=DocumentType.IMAGE,
            title=title,
            summary_text=summary,
            chunks=[DocumentChunk(index=0, text=chunk_text, metadata={"width": width, "height": height})],
            tables=[],
            metadata={"width": width, "height": height, "format": img_format, "mode": mode},
            has_injection_risk=False,
            quarantined_blocks=[],
        )

    def _parse_fallback(self, path: Path, file_size: int) -> ParsedDocument:
        """Generic fallback reader for unclassified plain text or binary files."""
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")[:3000]
        except Exception:
            content = "[Binary or unreadable file content]"

        title = path.stem.replace("_", " ").title()
        return ParsedDocument(
            file_path=path,
            file_name=path.name,
            file_size_bytes=file_size,
            doc_type=DocumentType.UNKNOWN,
            title=title,
            summary_text=f"File: {path.name} ({file_size / 1024:.1f} KB)",
            chunks=[DocumentChunk(index=0, text=content)],
            tables=[],
            metadata={"suffix": path.suffix},
            has_injection_risk=False,
            quarantined_blocks=[],
        )
