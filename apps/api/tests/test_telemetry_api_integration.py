"""Query Broker integration tests (real PostgreSQL + Redis).

Authorization order (provider call count + cache access proven), IDOR
consistency, input abuse, provider failure modes with a deterministic fake
Prometheus, cache isolation and last-good staleness, concurrency budgets,
Redis-failure fail-closed behavior, and the integration observation
projection.
"""

import asyncio
import os
import uuid as uuidlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from drake_api.catalog.service import CatalogValidationError
from drake_api.db import dispose_engines
from drake_api.settings import TelemetryConnector
from drake_api.telemetry.observations import record_provider_observation
from harness_s1 import S1Harness, build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_catalog_api_integration import build_users, grant, make_role, seed_catalog_world
from test_catalog_persistence_integration import reset_catalog

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]
FAKE_CONNECTOR_URL = "http://127.0.0.1:59095"  # never dialed: MockTransport


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def engine() -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()


class FakeProvider:
    """Deterministic fake Prometheus with switchable failure modes."""

    def __init__(self) -> None:
        self.calls = 0
        self.mode = "ok"
        self.extra_label: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "fail":
            return httpx.Response(500, text="upstream exploded: secret internals")
        if self.mode == "timeout":
            raise httpx.ConnectTimeout("slow provider")
        if self.mode == "malformed":
            return httpx.Response(200, text="<html>definitely not json</html>")
        if self.mode == "oversized":
            return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        values = [[1_700_000_000 + offset * 60, "2.5"] for offset in range(5)]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": dict(self.extra_label), "values": values}],
                },
            },
        )


_SUBJECTS = ("user-plain", "user-env", "user-b-only", "user-cluster", "user-viewonly")


def telemetry_harness(fresh_ttl: int = 1) -> tuple[S1Harness, FakeProvider]:
    provider = FakeProvider()
    settings = require_it_settings().model_copy(
        update={
            "telemetry_connectors": {"it-fake": TelemetryConnector(url=FAKE_CONNECTOR_URL)},
            "telemetry_fresh_ttl_override_seconds": fresh_ttl,
        }
    )
    harness = build_harness(settings, telemetry_transport=httpx.MockTransport(provider.handler))
    # Register the S2/S3 test subjects with THIS harness's fake provider.
    user_type = type(harness.provider.users["user-owner"])
    for subject in _SUBJECTS:
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness, provider


async def configure_alpha_prometheus(engine: AsyncEngine, world: dict[str, Any]) -> str:
    async with engine.begin() as connection:
        integration_id = (
            await connection.execute(
                text(
                    """
                    UPDATE integrations
                    SET config_ref = 'it-fake', configuration_state = 'configured'
                    WHERE scope_id = :scope AND integration_type = 'prometheus'
                    RETURNING id
                    """
                ),
                {"scope": world["alpha"].scope_id},
            )
        ).scalar_one()
    return str(integration_id)


def query_body(
    template: str, scope_type: str, scope_id: str, *, hours: int = 1, step: int = 60
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "template_key": template,
        "scope": {"type": scope_type, "id": scope_id},
        "range": {
            "from": (now - timedelta(hours=hours)).isoformat(),
            "to": now.isoformat(),
            "step_seconds": step,
        },
        "parameters": {},
    }


async def post_query(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    me = (await client.get("/v1/me")).json()
    return await client.post(
        "/v1/telemetry/query", json=body, headers={"X-CSRF-Token": me["csrf_token"]}
    )


# --- authorization order & IDOR ---------------------------------------------


async def test_unauthorized_query_never_touches_provider_or_cache(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness()
    await configure_alpha_prometheus(engine, world)

    cache_reads = 0
    original_get = harness.app.state.telemetry_broker._cache.get

    async def spying_get(key: str) -> Any:
        nonlocal cache_reads
        cache_reads += 1
        return await original_get(key)

    harness.app.state.telemetry_broker._cache.get = spying_get

    # A viewer WITHOUT telemetry.query, granted on alpha itself:
    await make_role(harness, engine, "Catalog Reader S3", ["project.view", "environment.view"])
    async with harness.api_client() as client:
        await harness.login(client, "user-viewonly")
        await grant(engine, harness, "user-viewonly", "Catalog Reader S3", "project", "alpha")
        body = query_body("service.request-rate.v1", "service", str(world["dev_api"].id))
        response = await post_query(client, body)
        assert response.status_code == 404  # consistent not-found
    assert provider.calls == 0
    assert cache_reads == 0  # authorization failed BEFORE any cache lookup

    # Cross-project: beta developer probing an alpha service binding id.
    async with harness.api_client() as client:
        await harness.login(client, "user-b-only")
        response = await post_query(
            client, query_body("service.request-rate.v1", "service", str(world["dev_api"].id))
        )
        assert response.status_code == 404
    assert provider.calls == 0
    assert cache_reads == 0


async def test_sibling_and_cross_grant_denials(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness_users = await build_users(engine)
    del harness_users
    harness, provider = telemetry_harness()
    await configure_alpha_prometheus(engine, world)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")  # Developer @ alpha/dev only
        allowed = await post_query(
            env_user,
            query_body("environment.request-rate.v1", "environment", str(world["alpha_dev"].id)),
        )
        assert allowed.status_code == 200

        sibling_env = await post_query(
            env_user,
            query_body("environment.request-rate.v1", "environment", str(world["alpha_prod"].id)),
        )
        assert sibling_env.status_code == 404
        sibling_service = await post_query(
            env_user,
            query_body("service.request-rate.v1", "service", str(world["prod_api"].id)),
        )
        assert sibling_service.status_code == 404

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")  # cluster.view only
        denied = await post_query(
            viewer,
            query_body("environment.request-rate.v1", "environment", str(world["alpha_dev"].id)),
        )
        assert denied.status_code == 404

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")  # Developer @ alpha
        ghost = await post_query(
            plain, query_body("environment.request-rate.v1", "environment", str(uuidlib.uuid4()))
        )
        assert ghost.status_code == 404
    assert provider.calls == 1  # only the single authorized query


# --- input abuse -------------------------------------------------------------


async def test_query_shape_abuse_is_rejected(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness()
    await configure_alpha_prometheus(engine, world)
    service_id = str(world["dev_api"].id)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")

        # Forbidden fields simply do not exist (extra=forbid):
        for field, value in (
            ("query", "up"),
            ("promql", "sum(up)"),
            ("metric_name", "up"),
            ("provider_url", "http://evil.example"),
            ("config_ref", "steal-me"),
        ):
            body = query_body("service.request-rate.v1", "service", service_id)
            body[field] = value
            assert (await post_query(plain, body)).status_code == 422, field

        # Arbitrary label/regex/operator smuggling via parameters:
        body = query_body("service.request-rate.v1", "service", service_id)
        body["parameters"] = {"label": 'foo{bar=~".*"}'}
        assert (await post_query(plain, body)).status_code == 422
        body["parameters"] = {"unknown_param": "value"}
        assert (await post_query(plain, body)).status_code == 422

        # Huge range refused; unknown template 404; wrong scope type 422:
        body = query_body("service.request-rate.v1", "service", service_id, hours=24 * 40)
        assert (await post_query(plain, body)).status_code == 422
        body = query_body("ghost.template.v9", "service", service_id)
        assert (await post_query(plain, body)).status_code == 404
        body = query_body("environment.request-rate.v1", "service", service_id)
        assert (await post_query(plain, body)).status_code == 422

        # Tiny step is adjusted server-side, disclosed, and bounded:
        body = query_body("service.request-rate.v1", "service", service_id, hours=24, step=1)
        response = await post_query(plain, body)
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["range"]["step_adjusted"] is True
        assert envelope["range"]["effective_step_seconds"] >= 30
    assert provider.calls <= 2  # the two 200s at most (step test + none other)


# --- provider flows, cache, staleness ---------------------------------------


async def test_success_cache_and_observation(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness(fresh_ttl=60)
    integration_id = await configure_alpha_prometheus(engine, world)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        body = query_body("service.request-rate.v1", "service", str(world["dev_api"].id))
        first = await post_query(plain, body)
        assert first.status_code == 200
        envelope = first.json()
        assert envelope["data_state"] == "ok"
        assert envelope["cache_state"] == "miss"
        assert envelope["unit"] == "requests_per_second"
        assert envelope["scope"] == {"type": "service", "ref": "alpha/dev/api"}
        assert envelope["series"][0]["points"][0][1] == 2.5
        assert provider.calls == 1

        second = await post_query(plain, body)
        assert second.status_code == 200
        assert second.json()["cache_state"] == "fresh_hit"
        assert provider.calls == 1  # fresh cache absorbed the repeat

    # Observation projection: a real provider success, recorded once-ish.
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT observed_state, last_success_at, last_error_code "
                    "FROM integrations WHERE id = :id"
                ),
                {"id": integration_id},
            )
        ).first()
    assert row is not None and row[0] == "ok" and row[1] is not None and row[2] is None

    # Nothing provider-shaped in the cache: no PromQL, no connector URL.
    settings = require_it_settings()
    redis = aioredis.from_url(settings.redis_url)
    try:
        keys = [key async for key in redis.scan_iter(match="telemetry:*")]
        assert keys, "expected telemetry cache entries"
        for key in keys:
            value = await redis.get(key)
            if value is None:
                continue
            payload = value.decode() if isinstance(value, bytes) else str(value)
            for forbidden in ("http_server_requests_total", "59095", "config_ref", "it-fake"):
                assert forbidden not in payload
    finally:
        await redis.aclose()


async def test_cache_is_scope_isolated(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness(fresh_ttl=60)
    await configure_alpha_prometheus(engine, world)
    # beta gets its own configured integration on the SAME connector.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE integrations SET config_ref = 'it-fake', "
                "configuration_state = 'configured' "
                "WHERE scope_id = :scope AND integration_type = 'prometheus'"
            ),
            {"scope": world["beta"].scope_id},
        )

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        alpha_body = query_body(
            "environment.request-rate.v1", "environment", str(world["alpha_dev"].id)
        )
        beta_body = query_body(
            "environment.request-rate.v1", "environment", str(world["beta_dev"].id)
        )
        assert (await post_query(owner, alpha_body)).status_code == 200
        assert provider.calls == 1
        # Same template, same range shape — different scope MUST miss:
        assert (await post_query(owner, beta_body)).status_code == 200
        assert provider.calls == 2


async def test_provider_down_serves_stale_then_503(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness(fresh_ttl=1)
    integration_id = await configure_alpha_prometheus(engine, world)
    service_id = str(world["dev_api"].id)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        body = query_body("service.request-rate.v1", "service", service_id)
        assert (await post_query(plain, body)).status_code == 200

        provider.mode = "fail"
        await asyncio.sleep(1.2)  # let the (test-shortened) fresh TTL lapse
        stale = await post_query(plain, body)
        assert stale.status_code == 200
        envelope = stale.json()
        assert envelope["data_state"] == "stale"
        assert envelope["cache_state"] == "stale"
        assert "provider_unavailable" in envelope["warnings"]
        assert envelope["series"], "last-good payload preserved"
        assert "upstream exploded" not in stale.text  # raw error redacted

        # A different logical query (other range) has no last-good → 503.
        other = query_body("service.request-rate.v1", "service", service_id, hours=7)
        unavailable = await post_query(plain, other)
        assert unavailable.status_code == 503
        error = unavailable.json()["error"]
        assert error["retryable"] is True
        assert error["correlation_id"]
        assert "exploded" not in unavailable.text

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT observed_state, last_error_code FROM integrations WHERE id = :id"),
                {"id": integration_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "degraded"  # recent success → degraded, not stale
    assert row[1] == "provider_upstream_error"


async def test_provider_contract_failures_are_fail_closed(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness()
    await configure_alpha_prometheus(engine, world)
    service_id = str(world["dev_api"].id)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        for mode, hours in (("malformed", 2), ("oversized", 3)):
            provider.mode = mode
            response = await post_query(
                plain, query_body("service.request-rate.v1", "service", service_id, hours=hours)
            )
            assert response.status_code == 502, mode
            assert "html" not in response.text.lower() or "provider" in response.text

        # Unexpected label (under-aggregated template) is refused too:
        provider.mode = "ok"
        provider.extra_label = {"pod_name": "leak-1"}
        response = await post_query(
            plain, query_body("service.request-rate.v1", "service", service_id, hours=4)
        )
        assert response.status_code == 502
        assert "leak-1" not in response.text

        # Timeout → typed retryable unavailability (no last-good here):
        provider.extra_label = {}
        provider.mode = "timeout"
        response = await post_query(
            plain, query_body("service.request-rate.v1", "service", service_id, hours=5)
        )
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is True


async def test_concurrency_budget_and_redis_failure(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness()
    await configure_alpha_prometheus(engine, world)
    service_id = str(world["dev_api"].id)
    broker = harness.app.state.telemetry_broker

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        async with engine.connect() as connection:
            identity_id = (
                await connection.execute(
                    text("SELECT id FROM identities WHERE subject = 'user-plain'")
                )
            ).scalar_one()

        # Exhaust the principal's 4 concurrent slots with real leases:
        import time as time_module

        from drake_api.telemetry.budgets import ConcurrencyRejectedError

        leases = broker._leases
        key = f"telemetry:lease:principal:{identity_id}"
        held = []
        for _ in range(4):
            held.extend(await leases.acquire([key], int(time_module.time() * 1000)))
        response = await post_query(
            plain, query_body("service.request-rate.v1", "service", service_id, hours=6)
        )
        assert response.status_code == 429
        assert response.json()["error"]["retryable"] is True
        calls_before_release = provider.calls
        await leases.release(held)

        # Lease release restores capacity:
        response = await post_query(
            plain, query_body("service.request-rate.v1", "service", service_id, hours=6)
        )
        assert response.status_code == 200
        assert provider.calls == calls_before_release + 1

        # The atomic script rejects the (limit+1)th token under contention:
        now_ms = int(time_module.time() * 1000)
        tokens = [await leases.acquire([key], now_ms) for _ in range(4)]
        with pytest.raises(ConcurrencyRejectedError):
            await leases.acquire([key], now_ms)
        for held_tokens in tokens:
            await leases.release(held_tokens)

        # Redis down: budgets are NEVER bypassed — typed retryable 503.
        from drake_api.telemetry.budgets import ConcurrencyLeases

        dead = aioredis.from_url(
            "redis://127.0.0.1:59097/0", socket_connect_timeout=0.2, socket_timeout=0.2
        )
        original_leases = broker._leases
        broker._leases = ConcurrencyLeases(dead)
        try:
            calls_before = provider.calls
            response = await post_query(
                plain, query_body("service.request-rate.v1", "service", service_id, hours=8)
            )
            assert response.status_code == 503
            assert response.json()["error"]["retryable"] is True
            assert provider.calls == calls_before  # provider untouched
        finally:
            broker._leases = original_leases
            await dead.aclose()


async def test_not_configured_is_honest_and_costs_nothing(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    harness, provider = telemetry_harness()
    # beta's prometheus integration stays not_configured (fixture default).

    async with harness.api_client() as b_user:
        await harness.login(b_user, "user-b-only")
        response = await post_query(
            b_user,
            query_body("environment.request-rate.v1", "environment", str(world["beta_dev"].id)),
        )
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["data_state"] == "not_configured"
        assert envelope["series"] == []
    assert provider.calls == 0


# --- registry/dashboard endpoints -------------------------------------------


async def test_catalog_and_dashboard_endpoints(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    await build_users(engine)
    harness, _provider = telemetry_harness()

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        page = (await plain.get("/v1/metrics/catalog?limit=3")).json()
        assert len(page["metrics"]) == 3
        assert page["next_cursor"]
        rest = (
            await plain.get(f"/v1/metrics/catalog?limit=10&cursor={page['next_cursor']}")
        ).json()
        assert len(page["metrics"]) + len(rest["metrics"]) == 7
        assert rest["next_cursor"] is None
        assert (await plain.get("/v1/metrics/catalog?cursor=garbage")).status_code == 422

        dashboard = (await plain.get("/v1/dashboard-templates/service-golden-signals-v1")).json()
        assert dashboard["dashboard"]["key"] == "service-golden-signals-v1"
        assert (await plain.get("/v1/dashboard-templates/ghost-board")).status_code == 404
        assert (await plain.get("/v1/dashboard-templates/UPPER!bad")).status_code == 422

    async with harness.api_client() as anonymous:
        assert (await anonymous.get("/v1/metrics/catalog")).status_code == 401
        body = query_body("service.request-rate.v1", "service", str(uuidlib.uuid4()))
        assert (await anonymous.post("/v1/telemetry/query", json=body)).status_code == 401


# --- observation write path --------------------------------------------------


async def test_observation_projection_states(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    integration_id = await configure_alpha_prometheus(engine, world)

    await record_provider_observation(engine, integration_id, outcome="success")
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT observed_state FROM integrations WHERE id = :id"),
                {"id": integration_id},
            )
        ).scalar_one()
    assert state == "ok"

    # Failure with a recent success → degraded.
    await record_provider_observation(
        engine, integration_id, outcome="failure", error_code="provider_timeout"
    )
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT observed_state, last_error_code FROM integrations WHERE id = :id"),
                {"id": integration_id},
            )
        ).first()
    assert row is not None and row[0] == "degraded" and row[1] == "provider_timeout"

    # Failure with an overdue success → stale.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE integrations SET last_success_at = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": integration_id},
        )
    await record_provider_observation(
        engine, integration_id, outcome="failure", error_code="provider_timeout"
    )
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT observed_state FROM integrations WHERE id = :id"),
                {"id": integration_id},
            )
        ).scalar_one()
    assert state == "stale"

    # Unsafe error codes cannot enter the write path at all.
    with pytest.raises(CatalogValidationError):
        await record_provider_observation(
            engine, integration_id, outcome="failure", error_code="Timeout: http://x/y\nboom"
        )


# --- real local Prometheus smoke --------------------------------------------


async def test_real_prometheus_query_smoke(engine: AsyncEngine) -> None:
    """Every compiled template parses on a REAL Prometheus, and the broker
    end-to-end returns live data for the fixture world."""
    prometheus_url = os.environ.get("DRAKE_IT_PROMETHEUS_URL", "http://127.0.0.1:59090")

    # 1) All templates compile to parseable PromQL on the real API.
    import time as time_module

    from drake_api.telemetry.compiler import compile_query
    from drake_api.telemetry.registry import load_registry

    sample = {
        "project_key": "alpha",
        "environment_key": "dev",
        "service_key": "api",
        "cluster_ref": "cluster-a",
        "namespace": "alpha-dev",
    }
    now = int(time_module.time())
    async with httpx.AsyncClient(base_url=prometheus_url, timeout=10.0) as prometheus:
        for template in load_registry().templates.values():
            compiled = compile_query(template, sample, {}, 60)
            response = await prometheus.post(
                "/api/v1/query_range",
                data={
                    "query": compiled.query,
                    "start": str(now - 600),
                    "end": str(now),
                    "step": "60",
                },
            )
            assert response.status_code == 200, (template.key, response.text[:200])
            assert response.json()["status"] == "success", template.key

    # 2) Broker end-to-end against the real provider (no mock transport).
    world = await seed_catalog_world(engine)
    await build_users(engine)
    settings = require_it_settings().model_copy(
        update={"telemetry_connectors": {"it-real": TelemetryConnector(url=prometheus_url)}}
    )
    harness = build_harness(settings)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE integrations SET config_ref = 'it-real', "
                "configuration_state = 'configured' "
                "WHERE scope_id = :scope AND integration_type = 'prometheus'"
            ),
            {"scope": world["alpha"].scope_id},
        )

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        envelope = None
        for _ in range(15):  # scrape warm-up on a freshly started Prometheus
            response = await post_query(
                plain,
                query_body("environment.scrape-up.v1", "environment", str(world["alpha_dev"].id)),
            )
            assert response.status_code == 200
            envelope = response.json()
            if envelope["data_state"] == "ok":
                break
            await asyncio.sleep(2)
        assert envelope is not None and envelope["data_state"] == "ok"
        latest = envelope["series"][0]["points"][-1][1]
        assert latest == 1.0  # the fixture targets are being scraped
        assert envelope["source_type"] == "prometheus"
