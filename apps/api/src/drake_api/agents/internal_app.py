"""Dedicated internal agent application (ADR-0016).

A SEPARATE ASGI app for the internal agent listener: correlation + typed
errors, the agent routers, and nothing else — no sessions, no cookies, no
CSRF, no public routes. In a production-like environment this app refuses
to exist without Agent CA material and internal TLS configuration
(fail-closed in Settings.validate_runtime_security)."""

from fastapi import FastAPI

from drake_api.agents.ca import AgentCertificateAuthority
from drake_api.agents.router_internal import router as agent_internal_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.errors import register_error_handlers
from drake_api.settings import Settings, get_settings


def create_internal_agent_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_runtime_security()
    app = FastAPI(title="Drake Agent Internal API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.agent_ca = AgentCertificateAuthority(settings)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(agent_internal_router)
    return app
