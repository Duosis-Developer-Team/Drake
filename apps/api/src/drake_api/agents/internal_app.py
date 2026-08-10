"""Dedicated internal agent application (ADR-0016).

A SEPARATE ASGI app for the internal agent listener: correlation + typed
errors, the agent routers, and nothing else — no sessions, no cookies, no
CSRF, no public routes. In a production-like environment this app refuses
to exist without Agent CA material and internal TLS configuration
(fail-closed in Settings.validate_runtime_security)."""

from typing import Any

from fastapi import FastAPI

from drake_api.agents.ca import AgentCertificateAuthority
from drake_api.agents.router_ingest import router as agent_ingest_router
from drake_api.agents.router_internal import certificate_router, enrollment_router
from drake_api.agents.router_internal import router as agent_internal_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.errors import register_error_handlers
from drake_api.settings import Settings, get_settings

# Stream-level request body ceiling. The largest legal message is one
# snapshot page of 500 fully-bounded resources; 8 MiB covers that with
# headroom while refusing pathological streams outright.
MAX_BODY_BYTES = 8 * 1024 * 1024


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    """Pure-ASGI body cap (BaseHTTPMiddleware is banned in this codebase:
    it swallowed http.disconnect in Sprint 3). Declared Content-Length is
    refused early; lying chunked streams are cut at the same ceiling."""

    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = None
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
        if declared is not None and declared > self.max_bytes:
            await self._refuse(send)
            return

        received = 0
        response_started = False

        async def bounded_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Any) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except _BodyTooLargeError:
            if response_started:
                raise
            await self._refuse(send)

    @staticmethod
    async def _refuse(send: Any) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


#: Which internal endpoints a listener serves.
#:
#: The split exists because of one unavoidable asymmetry: an agent enrolling
#: for the first time has no client certificate yet, and every request after
#: that must present one. Serving both from a single mutual-TLS listener is
#: impossible; serving both from a listener that merely *accepts* a missing
#: certificate would put the whole guarantee in application code — and this
#: stack cannot even see the peer certificate there (uvicorn does not expose
#: it to the ASGI app), so the check could not be written honestly.
#:
#: So production runs two listeners over the same app factory:
#:
#:   "enrollment" — server-authenticated TLS, no client certificate asked
#:                  for, and NOTHING but `POST /enroll` on it.
#:   "ingest"     — CERT_REQUIRED mutual TLS. Heartbeat, inventory and
#:                  certificate renewal. A caller without a valid client
#:                  certificate is refused during the handshake, before any
#:                  request exists.
#:
#: "all" keeps one listener for local, test and CI, where the transport is
#: not what is under test.
SURFACES = ("all", "enrollment", "ingest")


def create_internal_agent_app(settings: Settings | None = None, *, surface: str = "all") -> FastAPI:
    settings = settings or get_settings()
    settings.validate_runtime_security()
    app = FastAPI(title="Drake Agent Internal API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.agent_ca = AgentCertificateAuthority(settings)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    register_error_handlers(app)
    if surface not in SURFACES:
        raise ValueError(f"unknown internal listener surface: {surface!r}")
    if surface == "enrollment":
        # Deliberately nothing else. An enrolled agent's endpoints must not
        # be reachable on the listener that does not ask for a certificate.
        app.include_router(enrollment_router)
    elif surface == "ingest":
        # Deliberately NOT the enrollment route: a one-time token must not
        # be exchangeable on a listener a certificate already opened.
        app.include_router(certificate_router)
        app.include_router(agent_ingest_router)
    else:
        app.include_router(agent_internal_router)
        app.include_router(agent_ingest_router)
    return app
