"""The infrastructure screen against the real database and a fake Prometheus (ADR-0032).

What matters here is who sees what, and that the screen's numbers are the
broker's: a cluster nobody granted is a 404, a cluster without telemetry is
an honest state rather than zeros, a project the viewer cannot see is not
named, a namespace drill-down is bound to a row the agent reported, and the
snapshot is served from its cache without asking Prometheus again.
"""

import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from drake_api.settings import TelemetryConnector
from harness_s1 import S1Harness, build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, make_role, seed_catalog_world
from test_telemetry_api_integration import FAKE_CONNECTOR_URL, engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]


class ShapedProvider:
    """Answers each template with series shaped like its output labels."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        query = form["query"][0]
        self.queries.append(query)
        start, end, step = int(form["start"][0]), int(form["end"][0]), int(form["step"][0])
        times = list(range(start, end + 1, step))
        if "by (pod, node)" in query:
            keys = [{"pod": "api-1", "node": "node1"}, {"pod": "worker-1", "node": "node2"}]
        elif "by (pod)" in query:
            keys = [{"pod": "api-1"}, {"pod": "worker-1"}]
        elif "by (namespace, node)" in query:
            keys = [
                {"namespace": "alpha-dev", "node": "node1"},
                {"namespace": "beta-dev", "node": "node2"},
                {"namespace": "kube-system", "node": "node1"},
            ]
        else:
            keys = [{"node": "node1"}, {"node": "node2"}]
        value = "0.5" if "utilization" in query or "1 -" in query else "2"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": k, "values": [[t, value] for t in times]} for k in keys],
                },
            },
        )


def infra_harness() -> tuple[S1Harness, ShapedProvider]:
    provider = ShapedProvider()
    settings = require_it_settings().model_copy(
        update={
            "telemetry_connectors": {"it-fake": TelemetryConnector(url=FAKE_CONNECTOR_URL)},
            "telemetry_fresh_ttl_override_seconds": 1,
        }
    )
    harness = build_harness(settings, telemetry_transport=httpx.MockTransport(provider.handler))
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-cluster", "user-viewonly", "user-plain"):
        harness.provider.users.setdefault(
            subject, user_type(subject, subject.title(), f"{subject}@example.test")
        )
    return harness, provider


async def cluster_world(engine: AsyncEngine, *, configured: bool = True) -> dict[str, Any]:
    world = await seed_catalog_world(engine)
    cluster = world["cluster_a"]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO integrations
                    (integration_type, scope_id, config_ref, configuration_state)
                VALUES ('prometheus', :scope, 'it-fake', :state)
                """
            ),
            {"scope": cluster.scope_id, "state": "configured" if configured else "not_configured"},
        )
        rows = [("Node", None, "node1"), ("Node", None, "node2")] + [
            ("Namespace", None, ns) for ns in ("alpha-dev", "beta-dev", "kube-system")
        ]
        for kind, namespace, name in rows:
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_resources
                        (cluster_id, api_group, api_version, kind, namespace, name, uid,
                         resource_version, payload, health, last_seen_at, observed_at)
                    VALUES (:c, '', 'v1', :kind, :ns, :name, :uid, '1',
                            CAST(:payload AS jsonb), 'healthy', now(), now())
                    """
                ),
                {
                    "c": cluster.id,
                    "kind": kind,
                    "ns": namespace,
                    "name": name,
                    "uid": uuidlib.uuid4().hex,
                    "payload": '{"spec_summary": {"kubelet_version": "v1.34.3"}}'
                    if kind == "Node"
                    else "{}",
                },
            )
    return world


@asynccontextmanager
async def signed_in(
    harness: S1Harness, engine: AsyncEngine, subject: str
) -> AsyncIterator[httpx.AsyncClient]:
    async with harness.api_client() as client:
        await harness.login(client, subject)
        if subject == "user-owner":
            await grant_platform_owner(engine, harness.provider.issuer, subject)
        yield client


async def test_owner_sees_nodes_namespaces_and_project_groups(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine)
    cluster_id = str(world["cluster_a"].id)
    async with signed_in(harness, engine, "user-owner") as client:
        listed = (await client.get("/v1/infrastructure/clusters")).json()["clusters"]
        mine = next(c for c in listed if c["id"] == cluster_id)
        assert mine["metrics_configured"] is True and mine["nodes"] == 2

        body = (await client.get(f"/v1/infrastructure/clusters/{cluster_id}")).json()
    assert body["state"] == "ok", body.get("missing")
    assert [n["name"] for n in body["nodes"]] == ["node1", "node2"]
    node1 = body["nodes"][0]
    assert node1["kubelet_version"] == "v1.34.3"
    assert node1["cpu"]["now"] == 0.5 and node1["cpu"]["allocatable"] == 2.0
    assert len(node1["cpu"]["series"]) == len(body["node_times"])
    groups = {n["namespace"]: n["group"] for n in body["namespaces"]}
    assert groups["alpha-dev"] == {"kind": "project", "key": "alpha", "name": "Alpha"}
    assert groups["kube-system"]["kind"] == "kubernetes"
    alpha = next(n for n in body["namespaces"] if n["namespace"] == "alpha-dev")
    assert alpha["project"]["environment_key"] == "dev"
    assert alpha["cpu_requested"] == 2.0 and alpha["pods"] == 2.0
    # Node requests are the sum of what runs on the node, not a separate guess.
    assert node1["cpu"]["requested"] == 4.0
    # Every query carried the cluster's own label, never a caller's value.
    assert provider.queries and all('cluster_ref="cluster-a"' in q for q in provider.queries)
    assert len(provider.queries) == 17


async def test_snapshot_is_served_from_cache(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine)
    cluster_id = str(world["cluster_a"].id)
    async with signed_in(harness, engine, "user-owner") as client:
        await client.get(f"/v1/infrastructure/clusters/{cluster_id}")
        before = len(provider.queries)
        again = (await client.get(f"/v1/infrastructure/clusters/{cluster_id}")).json()
    assert len(provider.queries) == before
    assert again["refreshing"] is False and again["age_seconds"] < 60


async def test_a_cluster_without_a_grant_is_not_there(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine)
    cluster_id = str(world["cluster_a"].id)
    async with signed_in(harness, engine, "user-viewonly") as client:
        assert (await client.get("/v1/infrastructure/clusters")).json()["clusters"] == []
        missing = await client.get(f"/v1/infrastructure/clusters/{cluster_id}")
        unknown = await client.get(f"/v1/infrastructure/clusters/{uuidlib.uuid4()}")
        pods = await client.get(f"/v1/infrastructure/clusters/{cluster_id}/namespaces/alpha-dev")
    assert missing.status_code == unknown.status_code == pods.status_code == 404
    assert provider.queries == []


async def test_cluster_view_without_telemetry_is_denied_not_zero(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine)
    await make_role(harness, engine, "Cluster Viewer Infra", ["cluster.view"])
    await harness_login(harness, "user-cluster")
    await grant(engine, harness, "user-cluster", "Cluster Viewer Infra", "organization", "root")
    async with signed_in(harness, engine, "user-cluster") as client:
        body = (await client.get(f"/v1/infrastructure/clusters/{world['cluster_a'].id}")).json()
    assert body["state"] == "denied"
    assert body["nodes"] == [] and body["namespaces"] == []
    assert provider.queries == []


async def test_projects_the_viewer_cannot_see_are_not_named(engine: AsyncEngine) -> None:
    harness, _provider = infra_harness()
    world = await cluster_world(engine)
    await make_role(harness, engine, "Cluster Telemetry Infra", ["cluster.view", "telemetry.query"])
    await harness_login(harness, "user-cluster")
    await grant(engine, harness, "user-cluster", "Cluster Telemetry Infra", "cluster", "cluster-a")
    async with signed_in(harness, engine, "user-cluster") as client:
        body = (await client.get(f"/v1/infrastructure/clusters/{world['cluster_a'].id}")).json()
    alpha = next(n for n in body["namespaces"] if n["namespace"] == "alpha-dev")
    assert alpha["project"] is None
    assert alpha["group"] == {"kind": "unclaimed", "key": "ns:alpha-dev", "name": "alpha-dev"}


async def test_a_not_configured_cluster_says_so(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine, configured=False)
    async with signed_in(harness, engine, "user-owner") as client:
        body = (await client.get(f"/v1/infrastructure/clusters/{world['cluster_a'].id}")).json()
    assert body["state"] == "not_configured"
    assert provider.queries == []


async def test_namespace_pods_are_bound_to_the_reported_namespace(engine: AsyncEngine) -> None:
    harness, provider = infra_harness()
    world = await cluster_world(engine)
    cluster_id = str(world["cluster_a"].id)
    async with signed_in(harness, engine, "user-owner") as client:
        body = (
            await client.get(f"/v1/infrastructure/clusters/{cluster_id}/namespaces/alpha-dev")
        ).json()
        never_reported = await client.get(
            f"/v1/infrastructure/clusters/{cluster_id}/namespaces/alpha-prod"
        )
        malformed = await client.get(
            f"/v1/infrastructure/clusters/{cluster_id}/namespaces/Bad_Name"
        )
    assert body["state"] == "ok"
    assert [p["pod"] for p in body["pods"]] == ["api-1", "worker-1"]
    api = body["pods"][0]
    assert api["node"] == "node1" and api["cpu_requested"] == 2.0
    assert len(api["cpu"]["series"]) == len(body["times"])
    pod_queries = [q for q in provider.queries if "by (pod" in q]
    assert len(pod_queries) == 6
    assert all('cluster_ref="cluster-a"' in q and 'namespace="alpha-dev"' in q for q in pod_queries)
    assert never_reported.status_code == 404
    assert malformed.status_code == 422


async def harness_login(harness: S1Harness, subject: str) -> None:
    """Create the identity, so a grant can name it."""
    async with harness.api_client() as client:
        await harness.login(client, subject)
