"""
API route handler for POST /v1/capture.
Synchronously captures raw text into the L0 event log, slices chunks,
and queues background extraction and embedding jobs.
"""
from fastapi import APIRouter, Depends, Request, status
from app.domain.events import CaptureRequest, CaptureResult
from app.adapters.storage.pg_events import PostgresEventsAdapter

router = APIRouter(prefix="/v1", tags=["capture"])


def get_events_adapter(request: Request) -> PostgresEventsAdapter:
    # Use pool from app.state.storage or state.db_pool
    storage = getattr(request.app.state, "storage", None)
    if storage and hasattr(storage, "pool"):
        return PostgresEventsAdapter(storage.pool)
    pool = getattr(request.app.state, "db_pool", None)
    if pool:
        return PostgresEventsAdapter(pool)
    raise RuntimeError("Database pool not available in application state")


@router.post("/capture", response_model=CaptureResult, status_code=status.HTTP_201_CREATED)
async def capture_event(
    req: CaptureRequest,
    adapter: PostgresEventsAdapter = Depends(get_events_adapter),
) -> CaptureResult:
    result = await adapter.capture(req)
    return result
