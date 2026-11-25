"""
Sessions API Endpoints (Milestone L2).
Handles session closeout synopsis generation and session priming for continuity.
Prefix: /v1/sessions
"""
from __future__ import annotations
from typing import Any, Optional
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.session_service import SessionService

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def get_db_pool(request: Request) -> asyncpg.Pool:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return pool


class CloseSessionRequest(BaseModel):
    project: Optional[str] = None
    branch: Optional[str] = None
    context: str = "default"
    summary_override: Optional[str] = None


@router.post("/{session_id}/close")
async def api_close_session(
    session_id: str,
    req: CloseSessionRequest,
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Closes a session, generating and storing a <=150-word synopsis in abstractions."""
    embedding = getattr(request.app.state, "embedding", None)
    svc = SessionService(pool, embedding=embedding)
    return await svc.close_session(
        session_id,
        project=req.project,
        branch=req.branch,
        context=req.context,
        summary_override=req.summary_override,
    )


@router.get("/prime")
async def api_prime_session(
    request: Request,
    project: Optional[str] = Query(default=None),
    branch: Optional[str] = Query(default=None),
    limit: int = Query(default=3, ge=1, le=10),
    context: str = Query(default="default"),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Returns top relevant session synopses for continuity on session start."""
    embedding = getattr(request.app.state, "embedding", None)
    svc = SessionService(pool, embedding=embedding)
    return await svc.prime_session(
        project=project,
        branch=branch,
        limit=limit,
        context=context,
    )
