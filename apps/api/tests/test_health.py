"""Health endpoint behavior: live is dependency-free, ready never fakes health."""

import pytest
from drake_api.main import create_app
from drake_api.testing import integration_settings
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_live_returns_200_without_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_ready_reports_unavailable_dependencies_as_503(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["database"] == "unavailable"
    assert body["components"]["redis"] == "unavailable"


async def test_ready_response_carries_correlation_id(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.headers.get("X-Correlation-ID")


@pytest.mark.integration
async def test_ready_reports_ok_with_local_stack() -> None:
    settings = integration_settings()
    if settings is None:
        pytest.skip("DRAKE_IT_DATABASE_URL / DRAKE_IT_REDIS_URL not set")
    app: FastAPI = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"] == {"database": "ok", "redis": "ok"}
