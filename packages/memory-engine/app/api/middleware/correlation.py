"""
Correlation ID Middleware.
Attaches X-Trace-ID to every request context and response header for distributed tracing.
"""
import uuid
import contextvars
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

trace_id_var = contextvars.ContextVar("trace_id", default="")


def get_current_trace_id() -> str:
    return trace_id_var.get()


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("X-Trace-ID") or f"tr_{uuid.uuid4().hex[:12]}"
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            trace_id_var.reset(token)
