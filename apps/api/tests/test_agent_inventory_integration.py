"""Inventory ingestion + projection + user API tests (real PG + Redis).

Covers the ADR-0017 protocol end to end at the app layer: atomic snapshot
begin/pages/complete, idempotent duplicates, sequence gaps, torn snapshots
never touching the projection, watch-event lifecycle (missing, restored),
agent-restart re-basing, ingest security rejections (forbidden kinds,
credential-shaped content, claimed-id mismatch, oversized bodies), and the
scope-authorized user-facing inventory API with honest freshness.
"""

import json as jsonlib
import uuid as uuidlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_helpers import generate_keypair, pop_headers
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from drake_api.agents.ca import generate_ephemeral_ca
from drake_api.agents.internal_app import create_internal_agent_app
from drake_api.db import dispose_engines
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_catalog_api_integration import (
    build_users,
    grant,
    login_all,
    make_role,
    seed_catalog_world,
)
from test_catalog_persistence_integration import reset_catalog

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


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


def ca_settings(tmp_path: Path):
    cert, key = generate_ephemeral_ca(tmp_path / "ca")
    return require_it_settings().model_copy(
        update={"agent_ca_cert_file": str(cert), "agent_ca_key_file": str(key)}
    )


async def enroll_agent(
    engine: AsyncEngine, cluster_id: str
) -> tuple[str, ec.EllipticCurvePrivateKey]:
    """Insert an enrolled agent directly (enrollment itself is proven in
    test_agent_enrollment_integration); returns (agent_id, private key)."""
    key = generate_keypair()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    agent_id = str(uuidlib.uuid4())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO cluster_agents
                    (id, cluster_id, agent_version, public_key_pem,
                     certificate_serial, certificate_not_after)
                VALUES (:id, :cluster_id, 'test-0.1', :public_key, 'test-serial',
                        now() + interval '1 day')
                """
            ),
            {"id": agent_id, "cluster_id": cluster_id, "public_key": public_pem},
        )
        # Mirror the enrollment endpoint: the newest agent becomes the ONE
        # active inventory writer for the cluster.
        await connection.execute(
            text(
                """
                INSERT INTO cluster_inventory_state (cluster_id, active_agent_id)
                VALUES (:cluster_id, :agent_id)
                ON CONFLICT (cluster_id) DO UPDATE
                SET active_agent_id = EXCLUDED.active_agent_id, updated_at = now()
                """
            ),
            {"cluster_id": cluster_id, "agent_id": agent_id},
        )
    return agent_id, key


class AgentDriver:
    """Signs and sends agent messages like the Go engine does."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        key: ec.EllipticCurvePrivateKey,
        agent_id: str,
        cluster_id: str,
    ) -> None:
        self.client = client
        self.key = key
        self.agent_id = agent_id
        self.cluster_id = cluster_id
        self.sequence = 0

    def base(self, kind: str, *, sequence: int | None = None) -> dict[str, Any]:
        if sequence is None:
            self.sequence += 1
            sequence = self.sequence
        return {
            "api_version": "drake.duosis.com/agent/v1",
            "kind": kind,
            "cluster_id": self.cluster_id,
            "agent_id": self.agent_id,
            "request_id": str(uuidlib.uuid4()),
            "source_time": datetime.now(UTC).isoformat(),
            "sequence": sequence,
        }

    async def post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        body = jsonlib.dumps(payload).encode()
        headers = pop_headers(self.key, self.agent_id, "POST", path, body)
        headers["Content-Type"] = "application/json"
        return await self.client.post(path, content=body, headers=headers)

    async def heartbeat(self, state: str = "empty") -> httpx.Response:
        payload = self.base("heartbeat", sequence=self.sequence)
        payload["agent_version"] = "test-0.1"
        payload["inventory_state"] = state
        return await self.post("/internal/v1/agent/heartbeat", payload)

    async def begin(self, snapshot_uid: str, **overrides: Any) -> httpx.Response:
        payload = (
            self.base("snapshot_begin")
            | {
                "agent_version": "test-0.1",
                "snapshot_uid": snapshot_uid,
            }
            | overrides
        )
        return await self.post("/internal/v1/agent/inventory/snapshot/begin", payload)

    async def page(
        self,
        snapshot_uid: str,
        page_number: int,
        resources: list[dict[str, Any]],
        *,
        sequence: int | None = None,
    ) -> httpx.Response:
        payload = self.base("snapshot_page", sequence=sequence) | {
            "snapshot_uid": snapshot_uid,
            "page_number": page_number,
            "resources": resources,
        }
        return await self.post("/internal/v1/agent/inventory/snapshot/page", payload)

    async def complete(
        self, snapshot_uid: str, total_pages: int, total_resources: int
    ) -> httpx.Response:
        payload = self.base("snapshot_complete") | {
            "snapshot_uid": snapshot_uid,
            "total_pages": total_pages,
            "total_resources": total_resources,
        }
        return await self.post("/internal/v1/agent/inventory/snapshot/complete", payload)

    async def events(
        self, events: list[dict[str, Any]], *, sequence: int | None = None
    ) -> httpx.Response:
        payload = self.base("watch_events", sequence=sequence) | {"events": events}
        return await self.post("/internal/v1/agent/inventory/events", payload)


def internal_client(settings) -> httpx.AsyncClient:
    app = create_internal_agent_app(settings)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://agent-internal",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def resource(
    kind: str,
    name: str,
    uid: str,
    *,
    namespace: str | None = "team-a",
    spec: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "api_group": "apps" if kind in ("Deployment", "StatefulSet") else "",
        "api_version": "v1",
        "kind": kind,
        "name": name,
        "uid": uid,
        "resource_version": "100",
        "observed_at": _now(),
    }
    if namespace is not None:
        record["namespace"] = namespace
    if spec is not None:
        record["spec_summary"] = spec
    if status is not None:
        record["status_summary"] = status
    if labels is not None:
        record["labels"] = labels
    if annotations is not None:
        record["annotations"] = annotations
    if conditions is not None:
        record["conditions"] = conditions
    return record


UID_DEPLOY = "aaaa1111-0000-0000-0000-000000000001"
UID_POD = "aaaa1111-0000-0000-0000-000000000002"
UID_NODE = "aaaa1111-0000-0000-0000-000000000003"


def healthy_world() -> list[dict[str, Any]]:
    return [
        resource(
            "Deployment",
            "api",
            UID_DEPLOY,
            spec={"replicas": 2},
            status={"ready_replicas": 2, "replicas": 2},
            labels={"app.kubernetes.io/name": "api"},
        ),
        resource(
            "Pod",
            "api-1",
            UID_POD,
            status={"phase": "Running", "restarts": 7, "crashloop": True},
        ),
        resource(
            "Node",
            "node-1",
            UID_NODE,
            namespace=None,
            conditions=[{"type": "Ready", "status": "True"}],
        ),
    ]


async def test_atomic_snapshot_projection_and_user_api(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        page_response = await driver.page(snapshot_uid, 1, healthy_world())
        assert page_response.status_code == 200, page_response.text
        complete_response = await driver.complete(snapshot_uid, 1, 3)
        assert complete_response.status_code == 200, complete_response.text
        assert complete_response.json()["result"] == "applied"

        # Replayed complete is an idempotent duplicate, not a second apply.
        replay = await driver.complete(snapshot_uid, 1, 3)
        assert replay.json()["result"] == "duplicate"

        assert (await driver.heartbeat("fresh")).status_code == 200

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        summary = (await viewer.get(f"/v1/clusters/{cluster_a}/inventory/summary")).json()
        assert summary["inventory"]["state"] == "fresh"
        assert summary["inventory"]["active_resources"] == 3
        assert summary["inventory"]["missing_resources"] == 0
        assert summary["agent"]["status"] == "connected"
        assert summary["pods"]["crashloop"] == 1
        assert summary["pods"]["restarts"] == 7
        assert summary["workloads"]["healthy"] == 1
        assert summary["nodes"]["healthy"] == 1

        listing = (
            await viewer.get(
                f"/v1/clusters/{cluster_a}/inventory/resources",
                params={"kind": "Pod"},
            )
        ).json()
        assert [row["name"] for row in listing["resources"]] == ["api-1"]
        pod = listing["resources"][0]
        assert pod["health"] == "unhealthy"
        assert "crashloop_backoff" in pod["health_reasons"]

        detail = (
            await viewer.get(f"/v1/clusters/{cluster_a}/inventory/resources/{pod['id']}")
        ).json()
        assert detail["kind"] == "Pod"
        assert detail["status_summary"]["restarts"] == 7
        assert detail["provenance"]["source"] == "cluster-agent"
        assert detail["labels"] == {}

        unhealthy_only = (
            await viewer.get(
                f"/v1/clusters/{cluster_a}/inventory/resources",
                params={"health": "unhealthy"},
            )
        ).json()
        assert {row["kind"] for row in unhealthy_only["resources"]} == {"Pod"}

    # A second snapshot without the pod flips it to missing — not deleted.
    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        driver.sequence = 3
        second_uid = str(uuidlib.uuid4())
        assert (await driver.begin(second_uid)).status_code == 200
        remaining = [row for row in healthy_world() if row["uid"] != UID_POD]
        assert (await driver.page(second_uid, 1, remaining)).status_code == 200
        assert (await driver.complete(second_uid, 1, 2)).json()["result"] == "applied"

    async with engine.connect() as connection:
        lifecycle = (
            await connection.execute(
                text(
                    "SELECT lifecycle, health FROM inventory_resources "
                    "WHERE cluster_id = :cid AND uid = :uid"
                ),
                {"cid": cluster_a, "uid": UID_POD},
            )
        ).first()
        assert lifecycle is not None, "missing resources are never hard-deleted"
        assert lifecycle[0] == "missing"
        assert lifecycle[1] == "unknown", "a missing resource is never healthy"
        changes = (
            await connection.execute(
                text(
                    "SELECT change_type, count(*) FROM inventory_change_events "
                    "WHERE cluster_id = :cid GROUP BY change_type"
                ),
                {"cid": cluster_a},
            )
        ).all()
        by_type = {row[0]: int(row[1]) for row in changes}
        assert by_type.get("added", 0) == 3
        assert by_type.get("missing", 0) == 1


async def test_torn_snapshot_never_touches_projection(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        assert (await driver.page(snapshot_uid, 1, healthy_world())).status_code == 200
        # Claims 2 pages but sent 1: refused, discarded, projection untouched.
        torn = await driver.complete(snapshot_uid, 2, 6)
        assert torn.status_code == 409
        assert "reconcile_required" in torn.text

        # Watch events against a never-completed projection are refused too.
        refused = await driver.events(
            [
                {
                    "event_id": str(uuidlib.uuid4()),
                    "change_type": "updated",
                    "resource": healthy_world()[0],
                }
            ]
        )
        assert refused.status_code == 409

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM inventory_resources WHERE cluster_id = :cid"),
                {"cid": cluster_a},
            )
        ).scalar_one()
        assert int(count) == 0, "a torn snapshot must never reach the projection"
        status_row = (
            await connection.execute(
                text(
                    "SELECT status FROM inventory_snapshots "
                    "WHERE cluster_id = :cid ORDER BY started_at DESC LIMIT 1"
                ),
                {"cid": cluster_a},
            )
        ).first()
        assert status_row is not None and status_row[0] == "discarded"
        state = (
            await connection.execute(
                text("SELECT inventory_state FROM cluster_agents WHERE id = :id"),
                {"id": agent_id},
            )
        ).scalar_one()
        assert state == "reconcile_required"


async def test_sequence_gaps_duplicates_and_restart_rebase(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200  # seq 1
        assert (await driver.page(snapshot_uid, 1, healthy_world())).status_code == 200

        # A sequence GAP (skipping ahead) demands reconcile.
        gap = await driver.page(snapshot_uid, 2, [healthy_world()[0]], sequence=driver.sequence + 5)
        assert gap.status_code == 409
        assert "reconcile_required" in gap.text

        # The agent obeys: crash-safe restart resumes from the PERSISTED
        # sequence (never a blind re-base) and opens a fresh snapshot —
        # a begin may jump the sequence forward, it IS the reconcile.
        restarted = AgentDriver(client, key, agent_id, cluster_a)
        restarted.sequence = driver.sequence  # persisted last-ACK cursor
        new_uid = str(uuidlib.uuid4())
        assert (await restarted.begin(new_uid)).status_code == 200
        world_page = healthy_world()
        page_one = await restarted.page(new_uid, 1, world_page)
        assert page_one.status_code == 200, page_one.text

        # A DELAYED begin from the past (stale sequence, new uid) opens
        # nothing and regresses nothing.
        stale_begin = AgentDriver(client, key, agent_id, cluster_a)
        stale_begin.sequence = 0
        delayed = await stale_begin.begin(str(uuidlib.uuid4()))
        assert delayed.status_code == 200
        assert delayed.json()["result"] == "stale"

        # An exact page replay (same sequence, same page, SAME content)
        # is an idempotent no-op.
        replay = await restarted.page(new_uid, 1, world_page, sequence=restarted.sequence)
        assert replay.json()["result"] == "duplicate"

        done = await restarted.complete(new_uid, 1, 3)
        assert done.json()["result"] == "applied"

        # Watch flow: update + delete apply; the SAME batch replayed no-ops.
        update_events = [
            {
                "event_id": str(uuidlib.uuid4()),
                "change_type": "updated",
                "resource": resource(
                    "Deployment",
                    "api",
                    UID_DEPLOY,
                    spec={"replicas": 2},
                    status={"ready_replicas": 1, "replicas": 2},
                ),
            },
            {
                "event_id": str(uuidlib.uuid4()),
                "change_type": "deleted",
                "resource": resource("Pod", "api-1", UID_POD, status={"phase": "Running"}),
            },
        ]
        applied = await restarted.events(update_events)
        assert applied.status_code == 200, applied.text
        replayed = await restarted.events(update_events, sequence=restarted.sequence)
        assert replayed.json()["result"] == "duplicate"

    async with engine.connect() as connection:
        deployment = (
            await connection.execute(
                text(
                    "SELECT health, health_reasons FROM inventory_resources "
                    "WHERE cluster_id = :cid AND uid = :uid"
                ),
                {"cid": cluster_a, "uid": UID_DEPLOY},
            )
        ).first()
        assert deployment is not None
        assert deployment[0] == "degraded"
        assert "replicas_unavailable" in deployment[1]
        pod_lifecycle = (
            await connection.execute(
                text(
                    "SELECT lifecycle FROM inventory_resources "
                    "WHERE cluster_id = :cid AND uid = :uid"
                ),
                {"cid": cluster_a, "uid": UID_POD},
            )
        ).scalar_one()
        assert pod_lifecycle == "missing"
        # Exactly ONE update event applied despite the replay.
        updated_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM inventory_change_events "
                    "WHERE cluster_id = :cid AND change_type = 'updated'"
                ),
                {"cid": cluster_a},
            )
        ).scalar_one()
        assert int(updated_count) == 1


async def test_ingest_security_rejections(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    cluster_b = str(world["cluster_b"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200

        # 1) Secret kind never enters, even with a valid signature.
        secret = resource("Secret", "db-credentials", "bbbb1111-0000-0000-0000-000000000001")
        refused = await driver.page(snapshot_uid, 1, [secret], sequence=driver.sequence + 1)
        assert refused.status_code == 422

        # 2) ConfigMap kind is refused identically.
        configmap = resource("ConfigMap", "app-config", "bbbb1111-0000-0000-0000-000000000002")
        assert (
            await driver.page(snapshot_uid, 1, [configmap], sequence=driver.sequence + 1)
        ).status_code == 422

        # 3) Credential-shaped annotation keys are refused.
        sneaky = resource(
            "Pod",
            "leaky",
            "bbbb1111-0000-0000-0000-000000000003",
            annotations={"kubernetes.io/service-account-token": "sa-token"},
        )
        assert (
            await driver.page(snapshot_uid, 1, [sneaky], sequence=driver.sequence + 1)
        ).status_code == 422

        # 4) Credential-shaped VALUES are refused (private key material).
        leaky_value = resource(
            "Pod",
            "leaky-2",
            "bbbb1111-0000-0000-0000-000000000004",
            labels={"app.kubernetes.io/name": "-----BEGIN EC PRIVATE KEY-----"},
        )
        assert (
            await driver.page(snapshot_uid, 1, [leaky_value], sequence=driver.sequence + 1)
        ).status_code == 422

        # 5) Full raw manifests (nested structures) fail the schema outright.
        raw = resource("Pod", "raw", "bbbb1111-0000-0000-0000-000000000005")
        raw["spec_summary"] = {"containers": [{"env": [{"name": "PW", "value": "x"}]}]}
        assert (
            await driver.page(snapshot_uid, 1, [raw], sequence=driver.sequence + 1)
        ).status_code == 422

        # 6) Claimed cluster_id ≠ verified identity → generic agent refusal.
        forged = driver.base("snapshot_begin") | {
            "agent_version": "test-0.1",
            "snapshot_uid": str(uuidlib.uuid4()),
            "cluster_id": cluster_b,
        }
        forged_response = await driver.post("/internal/v1/agent/inventory/snapshot/begin", forged)
        assert forged_response.status_code == 403
        assert "agent authentication failed" in forged_response.text

        # 7) Claimed agent_id of a DIFFERENT agent (signed with our key).
        other_agent, _ = await enroll_agent(engine, cluster_a)
        impostor = driver.base("heartbeat", sequence=0) | {
            "agent_version": "test-0.1",
            "inventory_state": "empty",
            "agent_id": other_agent,
        }
        impostor_response = await driver.post("/internal/v1/agent/heartbeat", impostor)
        assert impostor_response.status_code == 403

        # 8) Oversized body: refused at the stream boundary with 413.
        big = b'{"padding":"' + b"x" * (8 * 1024 * 1024) + b'"}'
        headers = pop_headers(key, agent_id, "POST", "/internal/v1/agent/heartbeat", big)
        headers["Content-Type"] = "application/json"
        too_large = await client.post("/internal/v1/agent/heartbeat", content=big, headers=headers)
        assert too_large.status_code == 413

    async with engine.connect() as connection:
        forbidden = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM inventory_staging_resources "
                    "WHERE kind IN ('Secret', 'ConfigMap')"
                )
            )
        ).scalar_one()
        assert int(forbidden) == 0
        projected = (
            await connection.execute(
                text("SELECT count(*) FROM inventory_resources WHERE cluster_id = :cid"),
                {"cid": cluster_a},
            )
        ).scalar_one()
        assert int(projected) == 0


async def test_user_api_scope_isolation_and_freshness(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    cluster_b = str(world["cluster_b"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        assert (await driver.page(snapshot_uid, 1, healthy_world())).status_code == 200
        assert (await driver.complete(snapshot_uid, 1, 3)).json()["result"] == "applied"
        assert (await driver.heartbeat("fresh")).status_code == 200

    # Heartbeat-only agent on cluster B: connectivity ≠ inventory freshness.
    agent_b, key_b = await enroll_agent(engine, cluster_b)
    async with internal_client(settings) as client:
        driver_b = AgentDriver(client, key_b, agent_b, cluster_b)
        assert (await driver_b.heartbeat("empty")).status_code == 200

    # A user with NO cluster grant: uniform 404 on everything.
    async with harness.api_client() as nobody:
        await harness.login(nobody, "user-plain")
        for path in (
            f"/v1/clusters/{cluster_a}/inventory/summary",
            f"/v1/clusters/{cluster_a}/inventory/resources",
        ):
            response = await nobody.get(path)
            assert response.status_code == 404, path

    # A user scoped to cluster A only: B is a uniform 404, A works.
    await login_all(harness, ["user-cluster-a-only"])
    await make_role(harness, engine, "Cluster A Viewer S4", ["cluster.view"])
    await grant(
        engine, harness, "user-cluster-a-only", "Cluster A Viewer S4", "cluster", "cluster-a"
    )
    async with harness.api_client() as scoped:
        await harness.login(scoped, "user-cluster-a-only")
        summary = await scoped.get(f"/v1/clusters/{cluster_a}/inventory/summary")
        assert summary.status_code == 200
        denied = await scoped.get(f"/v1/clusters/{cluster_b}/inventory/summary")
        assert denied.status_code == 404
        listing = (await scoped.get(f"/v1/clusters/{cluster_a}/inventory/resources")).json()
        resource_id = listing["resources"][0]["id"]
        # The SAME resource id via the wrong cluster path: uniform 404.
        cross = await scoped.get(f"/v1/clusters/{cluster_b}/inventory/resources/{resource_id}")
        assert cross.status_code == 404

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        # Catalog capability derives from REAL observation now.
        detail_a = (await viewer.get(f"/v1/clusters/{cluster_a}")).json()
        assert detail_a["operational"]["agent"] == "connected"
        assert detail_a["operational"]["inventory"] == "fresh"
        detail_b = (await viewer.get(f"/v1/clusters/{cluster_b}")).json()
        assert detail_b["operational"]["agent"] == "connected"
        # Heartbeat alone NEVER makes inventory fresh.
        assert detail_b["operational"]["inventory"] == "empty"

        # Cursor pagination is stable and bounded.
        first = (
            await viewer.get(f"/v1/clusters/{cluster_a}/inventory/resources", params={"limit": 2})
        ).json()
        assert len(first["resources"]) == 2
        assert first["next_cursor"]
        second = (
            await viewer.get(
                f"/v1/clusters/{cluster_a}/inventory/resources",
                params={"limit": 2, "cursor": first["next_cursor"]},
            )
        ).json()
        names = {row["name"] for row in first["resources"]} | {
            row["name"] for row in second["resources"]
        }
        assert len(names) == 3

        # Unknown kinds are refused (the filter is allowlist-bound too).
        bad_kind = await viewer.get(
            f"/v1/clusters/{cluster_a}/inventory/resources", params={"kind": "Secret"}
        )
        assert bad_kind.status_code == 422

    # Stale downgrade: a "fresh" claim with old activity reads STALE.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE cluster_agents SET last_reconcile_at = now() - interval '2 hours' "
                "WHERE id = :id"
            ),
            {"id": agent_id},
        )
        await connection.execute(
            text(
                "UPDATE inventory_change_events SET occurred_at = now() - interval '2 hours' "
                "WHERE cluster_id = :cid"
            ),
            {"cid": cluster_a},
        )
    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        summary = (await viewer.get(f"/v1/clusters/{cluster_a}/inventory/summary")).json()
        assert summary["inventory"]["state"] == "stale"
        assert summary["agent"]["status"] == "connected", (
            "connectivity and freshness are separate axes"
        )
