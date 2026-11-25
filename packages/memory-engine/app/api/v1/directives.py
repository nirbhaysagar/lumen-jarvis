"""
Directives API Endpoints (Milestone D1).
Manages procedural memory: user preferences, operational constraints, goals, and style rules.
Prefix: /v1/directives
"""
from __future__ import annotations
from typing import Any, List, Optional, Literal
import uuid
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.domain.errors import NotFoundError, ConflictError
from app.services.directives import DirectivesService

router = APIRouter(prefix="/v1/directives", tags=["directives"])


def get_db_pool(request: Request) -> asyncpg.Pool:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return pool


class CreateDirectiveRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=128, description="Short descriptive label")
    body: str = Field(..., min_length=1, description="Rule, preference, or constraint text")
    directive_type: Literal["preference", "constraint", "goal", "style", "context"] = "preference"
    priority: int = Field(default=5, ge=1, le=10, description="Priority from 1 (lowest) to 10 (highest)")
    context: str = Field(default="default", description="Context slug")
    memory_ids: Optional[List[uuid.UUID]] = Field(default=None, description="Supporting memory UUIDs")
    pinned: bool = Field(default=True, description="Always include in prompt context pack")


class UpdateDirectiveRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=128)
    body: Optional[str] = Field(default=None, min_length=1)
    directive_type: Optional[Literal["preference", "constraint", "goal", "style", "context"]] = None
    priority: Optional[int] = Field(default=None, ge=1, le=10)


@router.post("", status_code=201)
async def create_directive(
    req: CreateDirectiveRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Creates a new active directive in the procedural memory tier."""
    svc = DirectivesService(pool)
    try:
        # Resolve context slug to context_id
        async with pool.acquire() as conn:
            ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", req.context)
            ctx_id = ctx_row["id"] if ctx_row else None

        res = await svc.create_directive(
            title=req.title,
            body=req.body,
            directive_type=req.directive_type,
            priority=req.priority,
            context_id=ctx_id,
            memory_ids=req.memory_ids,
            pinned=req.pinned,
            actor="user",
        )
        return res
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_directives(
    context: str = "default",
    directive_type: Optional[str] = Query(None, description="Filter by type (preference, constraint, etc.)"),
    status: str = Query("active", description="Status (active, superseded, retracted)"),
    limit: int = Query(15, ge=1, le=50),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Lists directives ordered by priority DESC, created_at DESC."""
    svc = DirectivesService(pool)
    async with pool.acquire() as conn:
        ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", context)
        ctx_id = ctx_row["id"] if ctx_row else None

    directives = await svc.list_directives(
        context_id=ctx_id,
        status=status,
        directive_type=directive_type,
        limit=limit,
    )
    return {
        "directives": directives,
        "count": len(directives),
        "context": context,
    }


@router.patch("/{directive_id}")
async def update_directive(
    directive_id: uuid.UUID,
    req: UpdateDirectiveRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Updates a directive immutably, generating a new version linked to the old one."""
    svc = DirectivesService(pool)
    try:
        return await svc.update_directive(
            directive_id=directive_id,
            title=req.title,
            body=req.body,
            directive_type=req.directive_type,
            priority=req.priority,
            actor="user",
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{directive_id}")
async def retract_directive(
    directive_id: uuid.UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Retracts an active directive."""
    svc = DirectivesService(pool)
    try:
        return await svc.retract_directive(directive_id=directive_id, actor="user")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{directive_id}/pin")
async def toggle_pin(
    directive_id: uuid.UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Toggles the pinned status of an active directive."""
    svc = DirectivesService(pool)
    try:
        return await svc.toggle_pin(directive_id=directive_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
