"""
Authentication Middleware for Lumen Memory Engine.
Enforces LUMEN_MEMORY_TOKEN bearer token authentication on protected endpoints.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from app.core.config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Public endpoints (docs, health, root dashboard)
        if request.url.path in {"/", "/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        expected_token = settings.LUMEN_MEMORY_TOKEN
        if expected_token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing Bearer token in Authorization header"},
                )
            token = auth_header[7:].strip()
            if token != expected_token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid LUMEN_MEMORY_TOKEN"},
                )

        return await call_next(request)
