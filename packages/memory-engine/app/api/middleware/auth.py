"""
Authentication Middleware for Lumen Memory Engine.
Enforces LUMEN_MEMORY_TOKEN bearer token authentication on protected endpoints.
"""
import hmac
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from app.core.config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Unconditionally public endpoints
        if request.url.path == "/health":
            return await call_next(request)

        # 2. Public endpoints only when DEBUG is active
        if settings.DEBUG and request.url.path in {"/", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        # 3. Fail closed if LUMEN_MEMORY_TOKEN is not configured
        expected_token = settings.LUMEN_MEMORY_TOKEN
        if not expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required. LUMEN_MEMORY_TOKEN is not configured."},
            )

        # 4. Enforce Bearer token header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Bearer token in Authorization header"},
            )

        token = auth_header[7:].strip()
        if not hmac.compare_digest(token, expected_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid LUMEN_MEMORY_TOKEN"},
            )

        return await call_next(request)

