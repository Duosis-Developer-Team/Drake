"""Correlation ID handling.

Every request carries a correlation ID: an inbound ``X-Correlation-ID`` header
is kept when it is well-formed, otherwise a new UUID is generated. The value is
exposed via a context variable for logging/audit and echoed on the response.
"""

import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"

# Conservative charset/length so hostile header values never propagate into
# logs, audit rows, or upstream calls.
_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(raw: str | None) -> str:
    """Return the inbound ID when safe, otherwise a freshly generated one."""
    if raw is not None and _SAFE_CORRELATION.fullmatch(raw):
        return raw
    return new_correlation_id()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = sanitize_correlation_id(request.headers.get(CORRELATION_HEADER))
        token = correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
