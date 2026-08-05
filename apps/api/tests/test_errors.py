"""Error envelope contract for HTTP, validation, and unhandled errors."""

from collections.abc import AsyncIterator

import pytest
from drake_api.main import create_app
from drake_api.testing import make_settings
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app_with_failing_routes() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/boom-http")
    async def boom_http() -> None:
        raise HTTPException(status_code=404, detail="resource missing")

    @app.get("/boom-unhandled")
    async def boom_unhandled() -> None:
        raise RuntimeError("internal detail with sensitive-looking content password=x")

    @app.get("/typed")
    async def typed(count: int) -> dict[str, int]:
        return {"count": count}

    return app


@pytest.fixture
async def failing_client(app_with_failing_routes: FastAPI) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: the 500 envelope produced by the app is
    # returned to the client instead of re-raising into the test process.
    transport = ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def test_http_exception_uses_envelope(failing_client: AsyncClient) -> None:
    response = await failing_client.get("/boom-http")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "resource missing"
    assert body["error"]["correlation_id"]
    assert body["error"]["retryable"] is False


async def test_validation_error_uses_envelope_with_details(failing_client: AsyncClient) -> None:
    response = await failing_client.get("/typed", params={"count": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]
    assert body["error"]["correlation_id"]


async def test_unhandled_error_never_leaks_internals(failing_client: AsyncClient) -> None:
    response = await failing_client.get("/boom-unhandled")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "error"
    assert body["error"]["message"] == "internal error"
    assert "password" not in response.text
    assert "sensitive" not in response.text


async def test_error_correlation_id_matches_request(failing_client: AsyncClient) -> None:
    response = await failing_client.get(
        "/boom-http", headers={"X-Correlation-ID": "trace-me-12345"}
    )
    assert response.json()["error"]["correlation_id"] == "trace-me-12345"
    assert response.headers["X-Correlation-ID"] == "trace-me-12345"
