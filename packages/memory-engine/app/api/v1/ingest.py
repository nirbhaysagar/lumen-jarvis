"""
Universal Ingestion API Endpoints (Milestone I1).
Handles file uploads (PDF, Markdown, HTML, CSV/XLSX, TXT), URL fetching (Web + YouTube),
and raw text ingestion into Lumen's L0 event log.
Prefix: /v1/ingest
"""
from __future__ import annotations
from typing import Any, Optional
import asyncpg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.ingest import ingest_file, ingest_parsed_doc, ingest_url, MAX_FILE_BYTES
from app.ingest.models import ParsedDoc

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


def get_db_pool(request: Request) -> asyncpg.Pool:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return pool


class IngestUrlRequest(BaseModel):
    url: str = Field(..., min_length=4)
    title: Optional[str] = None
    context: str = "default"


class IngestTextRequest(BaseModel):
    content: str = Field(..., min_length=1)
    title: str = Field(default="Untitled Note")
    context: str = "default"


@router.post("/file")
async def api_ingest_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    context: str = Form(default="default"),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Ingests an uploaded document (PDF, Markdown, HTML, XLSX, CSV, TXT, Transcript JSON)."""
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds 50 MB limit")
    try:
        return await ingest_file(
            pool,
            raw_bytes,
            file.filename or "upload.txt",
            title=title,
            content_type=file.content_type,
            context=context,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/url")
async def api_ingest_url(
    req: IngestUrlRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Ingests a web page or YouTube transcript URL."""
    try:
        return await ingest_url(pool, req.url, title=req.title, context=req.context)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")


@router.post("/text")
async def api_ingest_text(
    req: IngestTextRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Ingests raw pasted text as a document capture event."""
    doc = ParsedDoc(
        text=req.content.strip(),
        title=req.title,
        kind="import",
        metadata={"format": "text"},
    )
    try:
        return await ingest_parsed_doc(pool, doc, context=req.context)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
