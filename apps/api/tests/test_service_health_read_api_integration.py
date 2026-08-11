"""The service-health read endpoints, end to end.

Real PostgreSQL, real Redis, the real Query Broker and the real registry —
only Prometheus is a deterministic fake, reached through the same HTTP
transport the production adapter uses. That is the point: the parts that
decide what a caller may see and what a verdict means are all the real
ones, so these tests can say something about the shipped behaviour rather
than about a stub.
"""

import asyncio
import time
import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from harness_s1 import S1Harness, grant_platform_owner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, make_role, seed_catalog_world
from test_telemetry_api_integration import configure_alpha_prometheus, engine, migrated_db

pytestmark = pytest.mark.integration

# Re-exported so pytest collects the module-scoped database fixtures.
__all__ = ["engine", "migrated_db"]

WORKLOAD = "alpha-api"
NAMESPACE = "alpha-dev"


class SignalProvider:
    """A fake Prometheus that answers each curated query differently.

    Keyed on the metric name inside the compiled expression, because that
    is the only thing about a template this test should depend on: it lets
    a healthy service, a missing signal and an outage be arranged
    independently, which a single constant value cannot do.
    """

    #: metric substring → value. Ordered: the first match wins, so more
    #: specific names must come first.
    HEALTHY: tuple[tuple[str, float], ...] = (
        ("drake:workload:replicas_desired", 3.0),
        ("drake:workload:replicas_ready", 3.0),
        ("drake:workload_pod:restarts_total", 0.0),
        ("drake:workload_pod:cpu_cfs_throttled_periods_total", 0.0),
        ("drake:workload_pod:cpu_usage_seconds_total", 0.4),
        ("drake:workload_pod:cpu_limit_cores", 2.0),
        ("drake:workload_pod:memory_working_set_bytes", 200_000_000.0),
        ("drake:workload_pod:memory_limit_bytes", 1_000_000_000.0),
        ("http_server_requests_total", 12.0),
        ("http_server_request_duration_seconds_bucket", 0.15),
        ("drake:workload:up", 1.0),
    )

    def __init__(self) -> None:
        self.calls = 0
        self.mode = "ok"
        #: metric substrings that should come back with no samples at all.
        self.empty: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "down":
            return httpx.Response(503, text="provider unavailable")

        # The adapter POSTs the query as form data, exactly as it does
        # against a real Prometheus.
        query = parse_qs(request.content.decode()).get("query", [""])[0]
        if any(needle in query for needle in self.empty):
            return httpx.Response(
                200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
            )

        value = 1.0
        for needle, candidate in self.HEALTHY:
            if needle in query:
                value = candidate
                break

        now = int(time.time())
        values = [[now - offset * 30, str(value)] for offset in range(4, -1, -1)]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    # No labels: every curated workload template fully
                    # aggregates, and the registry's output allowlist is
                    # empty for exactly that reason.
                    "result": [{"metric": {}, "values": values}],
                },
            },
        )


def health_harness() -> tuple[S1Harness, SignalProvider]:
    from drake_api.settings import TelemetryConnector
    from harness_s1 import build_harness, require_it_settings

    provider = SignalProvider()
    settings = require_it_settings().model_copy(
        update={
            "telemetry_connectors": {"it-fake": TelemetryConnector(url="http://127.0.0.1:59096")},
            # Health reads cache their own verdicts; a short broker TTL keeps
            # the two layers from hiding each other in these tests.
            "telemetry_fresh_ttl_override_seconds": 1,
        }
    )
    harness = build_harness(settings, telemetry_transport=httpx.MockTransport(provider.handler))
    # The shared catalog fixtures grant roles to these subjects, so this
    # harness's fake identity provider has to know them.
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-plain", "user-env", "user-b-only", "user-cluster"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness, provider


async def seed_workload(engine: AsyncEngine, cluster_id: Any, name: str = WORKLOAD) -> str:
    """Report a workload into inventory, as an agent would."""
    resource_uid = f"uid-{uuidlib.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, health, last_seen_at, observed_at)
                VALUES (:cluster, 'apps', 'apps/v1', 'Deployment', :ns, :name, :uid,
                        '1', 'healthy', now(), now())
                """
            ),
            {"cluster": cluster_id, "ns": NAMESPACE, "name": name, "uid": resource_uid},
        )
    return resource_uid


@asynccontextmanager
async def owner(harness: S1Harness, engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-in Platform Owner, for the duration of one test."""
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        yield client


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"sh-{uuidlib.uuid4().hex}"}


async def create_binding(
    client: httpx.AsyncClient, world: dict[str, Any], **overrides: Any
) -> dict[str, Any]:
    body = {
        "environment_service_id": str(world["dev_api"].id),
        "cluster_id": str(world["cluster_a"].id),
        "namespace": NAMESPACE,
        "workload_kind": "Deployment",
        "workload_name": WORKLOAD,
        "preset_key": "kubernetes.baseline.v1",
        "health_policy_key": "default.v1",
    }
    body.update(overrides)
    response = await client.post(
        "/v1/service-health/bindings", json=body, headers=await csrf(client)
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- the read path ------------------------------------------------------


async def test_a_bound_workload_reports_health_from_curated_queries(
    engine: AsyncEngine,
) -> None:
    """The acceptance chain, in one request.

    binding → preset/policy → curated templates → broker → engine → API.
    """
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, provider = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        assert binding["resolved"] is True

        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/health")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["status"] == "healthy"
    assert body["availability"]["desired_replicas"] == 3
    assert body["availability"]["ready_replicas"] == 3
    assert body["served_from_last_good"] is False
    # The baseline preset reads no golden signals, so the answer says it is
    # incomplete rather than presenting infrastructure health as the whole
    # picture.
    assert body["partial"] is True
    assert "application.golden_signals" in body["missing_signals"]
    assert body["application"]["status"] == "not_configured"
    assert body["cached"] is False
    assert body["policy_key"] == "default.v1"
    assert body["binding"]["workload_name"] == WORKLOAD
    assert body["binding"]["datasource_configured"] is True
    assert provider.calls > 0

    # Reason codes travel as codes with their text alongside; the UI is
    # never asked to parse a sentence back into a decision.
    assert isinstance(body["reasons"], list)
    assert isinstance(body["messages"], list)


async def test_no_query_credential_or_expression_reaches_the_client(
    engine: AsyncEngine,
) -> None:
    """A health response is a decision, not the material to re-derive one."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        health = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).text
        metrics = (await client.get(f"/v1/service-health/bindings/{binding['id']}/metrics")).text
        series = (
            await client.get(
                f"/v1/service-health/bindings/{binding['id']}/series?signal=cpu_usage&range=1h"
            )
        ).text

    for payload in (health, metrics, series):
        for forbidden in (
            "sum(rate(",
            "drake:workload",
            "drake:workload_pod:cpu_usage_seconds_total",
            "it-fake",  # the integration's config ref
            "127.0.0.1:59096",  # the provider URL
            "config_ref",
        ):
            assert forbidden not in payload, forbidden


async def test_an_empty_signal_is_reported_as_missing_not_as_zero(
    engine: AsyncEngine,
) -> None:
    """Through the whole stack, not just the orchestrator's unit tests."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, provider = health_harness()
    provider.empty = {"drake:workload:replicas_ready"}
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        body = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).json()
        summary = (await client.get(f"/v1/service-health/bindings/{binding['id']}/metrics")).json()

    assert body["availability"]["ready_replicas"] is None
    assert "availability.replicas" in body["missing_signals"]
    assert body["partial"] is True
    assert "no_ready_replicas" not in body["reasons"]
    assert summary["metrics"]["availability"]["ready_replicas"]["value"] is None
    assert summary["metrics"]["availability"]["ready_replicas"]["state"] == "empty"


async def test_a_datasource_outage_degrades_to_stale_never_to_critical(
    engine: AsyncEngine,
) -> None:
    """What an operator sees when Prometheus goes away.

    Not "every service is critical" — that would be Drake blaming the
    estate for its own outage. The last real numbers, plainly marked stale,
    with the sample timestamps they were actually measured at.
    """
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, provider = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        good = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).json()
        assert good["status"] == "healthy"

        provider.mode = "down"
        # Let the broker's fresh entries expire so the outage is actually
        # reached rather than absorbed by a cache hit.
        await asyncio.sleep(1.2)
        outage = (
            await client.get(f"/v1/service-health/bindings/{binding['id']}/health?refresh=true")
        ).json()

    assert outage["status"] == "stale"
    assert outage["status"] != "critical"
    assert outage["partial"] is True
    assert "telemetry_stale" in outage["reasons"]
    # The numbers still describe when they were measured, not when they
    # were served.
    assert outage["newest_sample_at"] == good["newest_sample_at"]
    assert outage["availability"]["ready_replicas"] == 3


async def test_mutating_a_binding_makes_the_cached_verdict_unreachable(
    engine: AsyncEngine,
) -> None:
    """Invalidation, demonstrated rather than asserted about."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _provider = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        await client.get(f"/v1/service-health/bindings/{binding['id']}/health")
        cached = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).json()
        assert cached["cached"] is True

        updated = await client.post(
            f"/v1/service-health/bindings/{binding['id']}",
            json={
                "preset_key": "http.service.v1",
                "health_policy_key": "tolerant.v1",
                "expected_revision": 1,
            },
            headers=await csrf(client),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["revision"] == 2

        after = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).json()

    # The pre-mutation verdict is not merely scheduled for deletion — it is
    # not addressable, so the next read recomputes under the new policy.
    assert after["cached"] is False
    assert after["policy_key"] == "tolerant.v1"


async def test_a_concurrent_edit_is_refused_rather_than_overwritten(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        stale_write = await client.post(
            f"/v1/service-health/bindings/{binding['id']}",
            json={
                "preset_key": "http.service.v1",
                "health_policy_key": "default.v1",
                "expected_revision": 99,
            },
            headers=await csrf(client),
        )
    assert stale_write.status_code == 409


async def test_a_disabled_binding_is_not_configured_rather_than_unhealthy(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, provider = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        disabled = await client.post(
            f"/v1/service-health/bindings/{binding['id']}/lifecycle",
            json={"lifecycle": "disabled", "expected_revision": 1},
            headers=await csrf(client),
        )
        assert disabled.status_code == 200, disabled.text
        before = provider.calls
        body = (await client.get(f"/v1/service-health/bindings/{binding['id']}/health")).json()

    assert body["status"] == "not_configured"
    assert body["reasons"] == ["binding_disabled"]
    # A determined verdict costs no provider round-trip.
    assert provider.calls == before


# --- bounded series -----------------------------------------------------


async def test_a_series_is_bounded_to_preset_signals_and_fixed_ranges(
    engine: AsyncEngine,
) -> None:
    """There is no field in which a selector, regex or PromQL could arrive."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        base = f"/v1/service-health/bindings/{binding['id']}/series"

        ok = await client.get(f"{base}?signal=cpu_usage&range=1h")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["signal"] == "cpu_usage"
        assert body["range_key"] == "1h"
        assert body["series"], "a configured signal should return points"
        # Fully aggregated templates carry no labels at all.
        assert body["series"][0]["labels"] == {}

        # The baseline preset reads no golden signals, so there is nothing
        # to chart — and no way to ask for one anyway.
        assert (await client.get(f"{base}?signal=request_rate&range=1h")).status_code == 404
        assert (await client.get(f"{base}?signal=__class__&range=1h")).status_code == 404
        assert (await client.get(f"{base}?signal=cpu_usage&range=90d")).status_code == 422
        assert (await client.get(f"{base}?signal=cpu_usage&range=1s")).status_code == 422


# --- listing ------------------------------------------------------------


async def test_the_service_list_includes_unbound_services_as_not_configured(
    engine: AsyncEngine,
) -> None:
    """A list that hid unbound services would flatter an unobserved estate."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        await create_binding(client, world)
        response = await client.get(
            f"/v1/service-health/services?environment_id={world['alpha_dev'].id}"
        )
        assert response.status_code == 200, response.text
        body = response.json()

    by_service = {item["service_key"]: item for item in body["items"]}
    assert set(by_service) == {"api", "web"}

    bound = by_service["api"]
    assert bound["binding"]["workload_name"] == WORKLOAD
    assert bound["health"]["status"] == "healthy"
    assert bound["health"]["availability"]["ready_replicas"] == 3

    unbound = by_service["web"]
    assert unbound["binding"] is None
    assert unbound["health"]["status"] == "not_configured"
    assert unbound["health"]["reasons"] == ["no_binding"]


async def test_the_service_list_shows_only_services_in_scope(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness, _ = health_harness()
    await make_role(harness, engine, "Env Reader SH", ["environment.view"])

    async with harness.api_client() as client:
        await harness.login(client, "user-env")
        await grant(engine, harness, "user-env", "Env Reader SH", "environment", "alpha/dev")
        body = (await client.get("/v1/service-health/services")).json()

    keys = {(item["project_key"], item["environment_key"]) for item in body["items"]}
    assert keys == {("alpha", "dev")}
    assert body["total"] == len(body["items"])
    del world


# --- binding form options -----------------------------------------------


async def test_binding_options_are_dependent_and_scope_filtered(
    engine: AsyncEngine,
) -> None:
    """Cluster → namespace → workload, each level from inventory the caller sees."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    await seed_workload(engine, world["cluster_a"].id, name="alpha-worker")
    harness, _ = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        first = (await client.get("/v1/service-health/binding-options")).json()
        assert {c["cluster_ref"] for c in first["clusters"]} == {"cluster-a", "cluster-b"}
        # Nothing downstream until a cluster is chosen.
        assert first["namespaces"] == []
        assert first["workloads"] == []

        second = (
            await client.get(
                f"/v1/service-health/binding-options?cluster_id={world['cluster_a'].id}"
            )
        ).json()
        assert NAMESPACE in second["namespaces"]
        assert second["workloads"] == []

        third = (
            await client.get(
                f"/v1/service-health/binding-options?cluster_id={world['cluster_a'].id}"
                f"&namespace={NAMESPACE}"
                f"&environment_service_id={world['dev_api'].id}"
            )
        ).json()
        names = {workload["name"] for workload in third["workloads"]}
        assert names == {WORKLOAD, "alpha-worker"}
        assert all(workload["kind"] == "Deployment" for workload in third["workloads"])
        # Datasource state only — never a URL, a ref or a credential.
        assert third["datasource"]["configured"] is True
        assert set(third["datasource"]) == {
            "configured",
            "integration_type",
            "configuration_state",
            "observed_state",
            "last_success_at",
        }
        assert [preset["key"] for preset in third["presets"]]


async def test_binding_options_do_not_confirm_clusters_the_caller_cannot_see(
    engine: AsyncEngine,
) -> None:
    """An invisible cluster and an empty one give the same answer."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()
    await make_role(harness, engine, "Env Reader SH2", ["environment.view"])

    async with harness.api_client() as client:
        await harness.login(client, "user-env")
        await grant(engine, harness, "user-env", "Env Reader SH2", "environment", "alpha/dev")
        body = (
            await client.get(
                f"/v1/service-health/binding-options?cluster_id={world['cluster_a'].id}"
                f"&namespace={NAMESPACE}"
            )
        ).json()

    assert body["clusters"] == []
    assert body["namespaces"] == []
    assert body["workloads"] == []


# --- uniform not-found ---------------------------------------------------


async def test_every_read_endpoint_is_uniformly_not_found_out_of_scope(
    engine: AsyncEngine,
) -> None:
    """A binding a caller may not see is indistinguishable from one that is absent."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, _ = health_harness()
    await configure_alpha_prometheus(engine, world)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
    await make_role(harness, engine, "Beta Reader SH", ["environment.view", "telemetry.query"])

    absent = uuidlib.uuid4()
    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Beta Reader SH", "project", "beta")
        for path in (
            "/v1/service-health/bindings/{}",
            "/v1/service-health/bindings/{}/health",
            "/v1/service-health/bindings/{}/metrics",
            "/v1/service-health/bindings/{}/series?signal=cpu_usage&range=1h",
        ):
            hidden = await outsider.get(path.format(binding["id"]))
            missing = await outsider.get(path.format(absent))
            assert hidden.status_code == 404, path
            assert missing.status_code == 404, path
            # Identical down to the wording; only the correlation id, which
            # is per-request by design, differs.
            hidden_error = hidden.json()["error"]
            missing_error = missing.json()["error"]
            del hidden_error["correlation_id"], missing_error["correlation_id"]
            assert hidden_error == missing_error, path
