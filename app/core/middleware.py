import time
import uuid
import logging

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import JSONResponse

from app.core.security import verify_token, TOKEN_TYPE_ACCESS

logger = logging.getLogger("sitespace.requests")


class TvReadOnlyMiddleware(BaseHTTPMiddleware):
    """Block all write operations for users with role=tv.

    TV users are intended for display-only dashboard usage (e.g. a screen in a site
    office showing live booking and lookahead data). They must never be able to mutate
    state, but they do need to authenticate and read a scoped subset of project data.

    Enforcement is split across two layers — both are intentional and complementary:

    LAYER 1 — This middleware (blanket write-block):
        Intercepts every request before it reaches any route handler.
        Blocks POST/PUT/PATCH/DELETE for TV tokens.
        Exception: /api/auth/* is always passed through (login, refresh, logout).
        This is a cheap, early guard that requires no route-level code.

    LAYER 2 — Inline route guards in app/api/v1/*.py:
        Handle TV-specific restrictions that cannot be expressed as an HTTP-method
        filter. Examples:
          - GET /projects: TV users may only see projects they are explicitly assigned to.
          - GET /bookings: TV users must supply a project_id (cannot list all bookings).
          - GET /assets (global list): blocked for TV (must use project-scoped endpoint).
        These guards read `user_role` from the resolved entity and raise HTTP 403 with a
        descriptive message.

    WHY BOTH:
        Middleware cannot easily perform role-specific data scoping on reads (it would
        need to re-parse the body and call into the ORM). Route-level guards can, but
        they would have to be added to every write endpoint, creating a surface for
        omissions. Using middleware for writes + inline guards for scoped reads keeps
        each layer doing what it does best.

    Allowed: GET/HEAD/OPTIONS
    Blocked: POST/PUT/PATCH/DELETE (except /api/auth/*)
    """

    _WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # Allow auth endpoints to work (login, refresh, logout, etc.)
        path = request.url.path or ""
        if path.startswith("/api/auth"):
            return await call_next(request)

        if request.method.upper() not in self._WRITE_METHODS:
            return await call_next(request)

        # Best-effort token decode: if missing/invalid, let normal auth handling occur.
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return await call_next(request)

        token = auth[7:]
        payload = verify_token(token, TOKEN_TYPE_ACCESS)
        if not payload:
            return await call_next(request)

        role = payload.get("role")
        role_norm = role.strip().lower() if isinstance(role, str) else role
        if role_norm == "tv":
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "message": "TV role is read-only",
                    "detail": "TV role is read-only",
                },
            )

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every request with method, path, status, duration, and user context.
    Also sets Sentry user context for error attribution.
    """

    SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip noisy endpoints
        if request.url.path in self.SKIP_PATHS:
            try:
                return await call_next(request)
            finally:
                sentry_sdk.set_user(None)

        request_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()

        # Extract user from JWT (best-effort, don't block request)
        user_id, user_email, user_role = _extract_user(request)

        # Set Sentry user context
        if user_id:
            sentry_sdk.set_user({
                "id": user_id,
                "email": user_email or "",
                "role": user_role or "",
            })

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 1)

            # Log the request
            status_code = response.status_code
            level = logging.WARNING if status_code >= 400 else logging.INFO
            logger.log(
                level,
                "%s %s %s %sms [user=%s] [rid=%s]",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                user_id or "anon",
                request_id,
            )

            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            sentry_sdk.set_user(None)


def _extract_user(request: Request):
    """Best-effort JWT decode to get user info. Never raises."""
    try:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None, None, None
        token = auth[7:]
        payload = verify_token(token, TOKEN_TYPE_ACCESS)
        if not payload:
            return None, None, None
        return payload.get("sub"), payload.get("email"), payload.get("role")
    except Exception:
        return None, None, None
