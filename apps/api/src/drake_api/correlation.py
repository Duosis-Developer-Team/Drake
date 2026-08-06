"""Correlation ID handling.

Every request carries a correlation ID: an inbound ``X-Correlation-ID`` header
is kept when it is well-formed, otherwise a new UUID is generated. The value is
exposed via a context variable for logging/audit and echoed on the response.

Implemented as a PURE ASGI middleware on purpose: ``BaseHTTPMiddleware``
bridges ``receive`` through its own task/queue machinery, which can swallow
``http.disconnect`` delivery to downstream watchers — and the telemetry
endpoint's client-disconnect cancellation depends on receiving it. This
middleware passes ``receive`` through untouched.
"""

import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = sanitize_correlation_id(Headers(scope=scope).get(CORRELATION_HEADER))
        token = correlation_id_var.set(correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            correlation_id_var.reset(token)
