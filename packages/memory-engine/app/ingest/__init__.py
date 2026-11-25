"""
Universal Document Ingestion Pipeline (Milestone I1).
Parses PDF, Markdown, HTML, YouTube transcripts, spreadsheets, and plain text,
and captures the clean text into Lumen's append-only event log.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional
import asyncpg
import httpx

from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.events import CaptureRequest, CaptureResult, SourceInfo
from app.ingest.models import ParsedDoc
from app.ingest.parsers.pdf import parse_pdf
from app.ingest.parsers.markdown import parse_markdown
from app.ingest.parsers.html import parse_html
from app.ingest.parsers.spreadsheet import parse_spreadsheet
from app.ingest.parsers.text import parse_text
from app.ingest.parsers.youtube import (
    is_youtube_url,
    fetch_youtube_transcript,
    parse_youtube_transcript_json,
)

MAX_URL_BYTES = 10 * 1024 * 1024  # 10 MB cap for web fetches
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB cap for uploaded files


def parse_file_bytes(
    raw_bytes: bytes,
    filename: str,
    *,
    title: Optional[str] = None,
    content_type: Optional[str] = None,
) -> ParsedDoc:
    """Dispatches raw file bytes to the appropriate format parser."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf" or content_type == "application/pdf":
        return parse_pdf(raw_bytes, filename=filename, title=title)
    if ext in (".md", ".markdown") or content_type in ("text/markdown",):
        return parse_markdown(raw_bytes, filename=filename, title=title)
    if ext in (".html", ".htm") or content_type in ("text/html",):
        return parse_html(raw_bytes, filename=filename, title=title)
    if ext in (".xlsx", ".csv"):
        return parse_spreadsheet(raw_bytes, filename=filename, title=title)
    if ext == ".json":
        try:
            return parse_youtube_transcript_json(raw_bytes, filename=filename, title=title)
        except Exception:
            return parse_text(raw_bytes, filename=filename, title=title)
    return parse_text(raw_bytes, filename=filename, title=title)


async def ingest_parsed_doc(
    pool: asyncpg.Pool,
    doc: ParsedDoc,
    *,
    uri: Optional[str] = None,
    context: str = "default",
    speaker: str = "user",
) -> dict[str, Any]:
    """Captures a ParsedDoc into Lumen's L0 event log."""
    if not doc.text or not doc.text.strip():
        raise ValueError("Parsed document contains no extractable text.")

    events_adapter = PostgresEventsAdapter(pool)
    req = CaptureRequest(
        content=doc.text,
        source=SourceInfo(
            kind=doc.kind if doc.kind in ("file", "web", "import", "note") else "file",
            uri=uri,
            title=doc.title,
            author=doc.author or speaker,
            metadata=doc.metadata,
        ),
        context=context,
        occurred_at=doc.published_at,
        speaker=speaker,
    )
    cap_res: CaptureResult = await events_adapter.capture(req)
    return {
        "status": "ingested",
        "event_id": str(cap_res.event_id),
        "source_id": str(cap_res.source_id),
        "title": doc.title,
        "kind": doc.kind,
        "char_count": len(doc.text),
        "job_ids": cap_res.job_ids,
        "metadata": doc.metadata,
    }


async def ingest_file(
    pool: asyncpg.Pool,
    raw_bytes: bytes,
    filename: str,
    *,
    title: Optional[str] = None,
    content_type: Optional[str] = None,
    context: str = "default",
    speaker: str = "user",
) -> dict[str, Any]:
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds maximum size limit of {MAX_FILE_BYTES} bytes.")
    doc = parse_file_bytes(raw_bytes, filename, title=title, content_type=content_type)
    return await ingest_parsed_doc(pool, doc, uri=f"file://{filename}", context=context, speaker=speaker)


async def ingest_url(
    pool: asyncpg.Pool,
    url: str,
    *,
    title: Optional[str] = None,
    context: str = "default",
    speaker: str = "user",
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> dict[str, Any]:
    if is_youtube_url(url):
        doc = fetch_youtube_transcript(url, title=title)
    else:
        kwargs: dict[str, Any] = {"timeout": 15.0, "follow_redirects": True}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url, headers={"User-Agent": "LumenMemoryEngine/0.1"})
            resp.raise_for_status()
            raw_bytes = resp.content[:MAX_URL_BYTES]
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if "html" in ctype or raw_bytes.lstrip().startswith(b"<"):
                doc = parse_html(raw_bytes, url=url, title=title)
            else:
                doc = parse_text(raw_bytes, filename=url.split("/")[-1] or "web.txt", title=title or url)
                doc.kind = "web"

    return await ingest_parsed_doc(pool, doc, uri=url, context=context, speaker=speaker)
