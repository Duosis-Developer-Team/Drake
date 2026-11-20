"""Error tracking, end to end (ADR-0036).

Real PostgreSQL, Redis, RBAC and binding repository; ClickHouse is a fake
behind the production adapter's HTTP transport. Every statement also
answers for another workload in the namespace, which must never surface.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_observability_overview_integration import KEY, STRANGER, _bound
from test_service_health_read_api_integration import owner
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

T = 1_763_640_000


def _rows(sql: str) -> list[dict[str, Any]]:
    if "event_name = 'exception'" in sql:
        return [
            {
                "wk": KEY,
                "etype": "KeyError",
                "shape": "missing key <id>",
                "n": "7",
                "first_seen": "2026-11-20 10:00:00.000000000",
                "last_seen": "2026-11-20 12:00:00.000000000",
                "message": "missing key 42",
                "stacktrace": 'Traceback (most recent call last):\n  File "app.py", line 3',
                "trace_id": "abc123",
                "routes": ["GET /cart"],
                "trend": [[T, "3"], [T + 3600, "4"]],
            },
            {"wk": STRANGER, "etype": "Secret", "shape": "x", "n": "99", "trend": []},
        ]
    if "AS code" in sql:
        return [
            {
                "wk": KEY,
                "route": "POST /pay",
                "code": "500",
                "n": "2",
                "first_seen": "2026-11-20 11:00:00.000000000",
                "last_seen": "2026-11-20 11:30:00.000000000",
                "message": "",
                "trace_id": "def456",
                "trend": [[T + 3600, "2"]],
            }
        ]
    if "otel_logs" in sql:
        return [
            {
                "wk": KEY,
                "shape": 'FATAL: database "<s>" does not exist',
                "n": "30",
                "first_seen": "2026-11-20 09:00:00.000000000",
                "last_seen": "2026-11-20 12:30:00.000000000",
                "severity_number": 21,
                "message": 'FATAL: database "hermes" does not exist',
                "trace_id": "",
                "trend": [[T, "30"]],
            }
        ]
    return []


class ErrorStore:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"data": _rows(request.content.decode())})


def _harness(tmp_path: Path) -> tuple[Any, ErrorStore]:
    from drake_api.settings import ObservabilityStoreConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = ErrorStore()
    settings = require_it_settings().model_copy(
        update={
            "observability_logs_enabled": True,
            "observability_traces_enabled": True,
            "observability_store": ObservabilityStoreConnector(
                url="http://127.0.0.1:58199",
                username="drake_reader",
                password_file=str(password_file),
            ),
        }
    )
    harness = build_harness(settings, observability_transport=httpx.MockTransport(store.handler))
    return harness, store


async def test_issues_are_grouped_scoped_and_cached(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    binding = await _bound(engine, harness)
    path = f"/v1/service-health/errors?binding_id={binding['id']}&range=24h"

    async with owner(harness, engine) as client:
        first = await client.get(path)
        asked = len(store.requests)
        second = await client.get(path)

    assert first.status_code == 200, first.text
    body = first.json()
    assert asked == 3  # one statement per kind
    assert len(store.requests) == asked  # the second read came from the cache
    assert second.json() == body

    kinds = {issue["kind"]: issue for issue in body["issues"]}
    assert set(kinds) == {"exception", "failed_request", "error_log"}
    assert all(issue["binding_id"] == binding["id"] for issue in body["issues"])
    assert not any("Secret" in issue["title"] for issue in body["issues"])

    exception = kinds["exception"]
    assert exception["title"] == "KeyError: missing key <id>"
    assert exception["count"] == 7
    assert exception["stacktrace"].startswith("Traceback")
    assert exception["routes"] == ["GET /cart"]
    assert len(exception["trend"]) == len(body["buckets"])
    assert kinds["failed_request"]["title"] == "POST /pay → 500"
    assert kinds["error_log"]["severity_number"] == 21
    # Newest last-seen first.
    assert body["issues"][0]["kind"] == "error_log"
    assert body["summary"]["events"] == {"exception": 7, "failed_request": 2, "error_log": 30}
    assert {k: v["state"] for k, v in body["sections"].items()} == {
        "exception": "ok",
        "failed_request": "ok",
        "error_log": "ok",
    }


async def test_an_issue_keeps_its_id_between_reads(engine: AsyncEngine, tmp_path: Path) -> None:
    from drake_api.observability.errors import fingerprint

    assert fingerprint("exception", "b", "KeyError", "x") == fingerprint(
        "exception", "b", "KeyError", "x"
    )
    assert fingerprint("exception", "b", "KeyError", "x") != fingerprint(
        "exception", "c", "KeyError", "x"
    )


async def test_seeing_a_service_is_not_permission_to_read_its_errors(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    from test_catalog_api_integration import grant

    harness, store = _harness(tmp_path)
    binding = await _bound(engine, harness)
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-env", user_type("user-env", "Env", "user-env@example.test")
    )
    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        response = await env_user.get(f"/v1/service-health/errors?binding_id={binding['id']}")
    assert response.status_code == 200
    assert response.json()["issues"] == []
    assert store.requests == []
