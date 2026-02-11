import time
import uuid
import logging

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import verify_token, TOKEN_TYPE_ACCESS

logger = logging.getLogger("sitespace.requests")


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
