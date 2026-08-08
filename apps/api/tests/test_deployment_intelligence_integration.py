"""Deployment intelligence: rollout state, evidence, ingest and scope.

The evidence chain is the point of this sprint, so the tests concentrate
on the two ways it can be wrong: claiming more than Drake observed, and
inventing a deployment where there was only a reconnect.
"""

import json
import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from drake_api.deployments.health import SignalComparison, decide
from drake_api.deployments.ingest import ingest_deployments, running_digest, spec_images
from drake_api.deployments.model import (
    EvidenceState,
    Provenance,
    RolloutState,
    WorkloadObservation,
    evaluate_evidence,
    evaluate_rollout,
    normalize_commit,
    parse_digest,
    workflow_run_url,
)
from harness_s1 import S1Harness, build_harness, grant_platform_owner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


# ===========================================================================
# rollout evaluation (pure)
# ===========================================================================


def observation(**overrides: Any) -> WorkloadObservation:
    base: dict[str, Any] = {
        "kind": "Deployment",
        "generation": 4,
        "observed_generation": 4,
        "desired_replicas": 3,
        "ready_replicas": 3,
        "updated_replicas": 3,
        "available_replicas": 3,
        "first_seen_at": NOW - timedelta(minutes=1),
    }
    base.update(overrides)
    return WorkloadObservation(**base)


def test_a_completed_rollout_is_healthy() -> None:
    verdict = evaluate_rollout(observation(), now=NOW)
    assert verdict.state is RolloutState.HEALTHY
    assert verdict.complete is True


def test_a_controller_that_has_not_seen_its_spec_is_pending_not_degraded() -> None:
    """A one-second-old rollout is not a problem, and saying so would page
    someone every time anyone deploys."""
    verdict = evaluate_rollout(observation(observed_generation=3), now=NOW)
    assert verdict.state is RolloutState.PENDING
    assert verdict.reason == "generation_not_observed"


def test_a_rolling_update_in_flight_is_progressing_or_degraded() -> None:
    assert evaluate_rollout(observation(ready_replicas=0, updated_replicas=1), now=NOW).state is (
        RolloutState.PROGRESSING
    )
    assert evaluate_rollout(observation(ready_replicas=2, updated_replicas=2), now=NOW).state is (
        RolloutState.DEGRADED
    )


def test_a_failed_condition_outranks_the_counters() -> None:
    verdict = evaluate_rollout(
        observation(
            ready_replicas=0,
            conditions=(
                {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"},
            ),
        ),
        now=NOW,
    )
    assert verdict.state is RolloutState.FAILED
    assert verdict.reason == "ProgressDeadlineExceeded"


def test_no_progress_within_the_window_is_stalled_not_failed() -> None:
    """Kubernetes has not given up, so neither does the label."""
    verdict = evaluate_rollout(
        observation(ready_replicas=1, first_seen_at=NOW - timedelta(hours=2)), now=NOW
    )
    assert verdict.state is RolloutState.STALLED


def test_scaled_to_zero_is_not_a_failed_rollout() -> None:
    verdict = evaluate_rollout(
        observation(desired_replicas=0, ready_replicas=0, updated_replicas=0), now=NOW
    )
    assert verdict.state is RolloutState.HEALTHY
    assert verdict.reason == "scaled_to_zero"


def test_missing_observations_are_unknown_rather_than_guessed() -> None:
    assert evaluate_rollout(observation(generation=None), now=NOW).state is RolloutState.UNKNOWN
    assert (
        evaluate_rollout(observation(updated_replicas=None), now=NOW).state is RolloutState.UNKNOWN
    )


# ===========================================================================
# provenance evidence (pure)
# ===========================================================================


def test_a_mutable_tag_alone_is_unverified() -> None:
    """It may well be the right build. Drake simply has no evidence."""
    verdict = evaluate_evidence(Provenance())
    assert verdict.state is EvidenceState.UNVERIFIED


def test_partial_evidence_is_never_promoted_to_verified() -> None:
    for provenance in (
        Provenance(declared_digest=DIGEST),
        Provenance(commit_sha=COMMIT),
        Provenance(commit_sha=COMMIT, declared_digest=DIGEST),
        Provenance(commit_sha=COMMIT, workflow_repository="acme/api", workflow_run_id="42"),
    ):
        assert evaluate_evidence(provenance).state is EvidenceState.PARTIAL


def test_the_whole_chain_is_verified() -> None:
    verdict = evaluate_evidence(
        Provenance(
            commit_sha=COMMIT,
            workflow_provider="github",
            workflow_repository="acme/api",
            workflow_run_id="42",
            declared_digest=DIGEST,
            running_digest=DIGEST,
        )
    )
    assert verdict.state is EvidenceState.VERIFIED
    assert verdict.detail["digest_match"] is True


def test_disagreeing_digests_are_a_conflict_not_a_choice() -> None:
    """The workload says one build, the node pulled another. Picking a side
    would be inventing the answer."""
    verdict = evaluate_evidence(
        Provenance(
            commit_sha=COMMIT,
            workflow_provider="github",
            workflow_repository="acme/api",
            workflow_run_id="42",
            declared_digest=DIGEST,
            running_digest=OTHER_DIGEST,
        )
    )
    assert verdict.state is EvidenceState.CONFLICT
    assert verdict.detail["digest_match"] is False


def test_a_version_label_that_is_not_a_commit_is_dropped() -> None:
    assert normalize_commit("v1.2.3") is None
    assert normalize_commit("latest") is None
    assert normalize_commit(COMMIT) == COMMIT
    assert normalize_commit("g" + COMMIT[:12]) == COMMIT[:12]


def test_digests_are_read_from_a_pinned_reference() -> None:
    assert parse_digest(f"ghcr.io/acme/api@{DIGEST}") == DIGEST
    assert parse_digest("ghcr.io/acme/api:v2") is None
    assert parse_digest(None) is None


def test_a_run_url_is_composed_only_from_validated_parts() -> None:
    """No URL is ever stored or accepted, so there is nothing to smuggle."""
    assert (
        workflow_run_url("https://github.com", "github", "acme/api", "42")
        == "https://github.com/acme/api/actions/runs/42"
    )
    # An unconfigured base, a foreign provider, or a repository/run that is
    # not shaped like one produces no link at all.
    assert workflow_run_url("", "github", "acme/api", "42") is None
    assert workflow_run_url("https://github.com", "gitlab", "acme/api", "42") is None
    assert workflow_run_url("https://github.com", "github", "acme/api/../..", "42") is None
    assert workflow_run_url("https://github.com", "github", "acme/api", "42; rm -rf /") is None


# ===========================================================================
# ingest
# ===========================================================================


async def seed_workload(
    engine: AsyncEngine,
    world: dict[str, Any],
    *,
    generation: int = 1,
    observed_generation: int | None = 1,
    image: str = f"ghcr.io/acme/api@{DIGEST}",
    ready: int = 3,
    labels: dict[str, str] | None = None,
    uid: str | None = None,
    name: str = "pilot-api",
) -> str:
    """Report one workload into inventory, exactly as the agent would."""
    resource_uid = uid or f"uid-{uuidlib.uuid4().hex[:12]}"
    payload = {
        "labels": labels or {},
        "annotations": {},
        "spec_summary": {
            "replicas": 3,
            "generation": generation,
            "containers": [{"name": "api", "image": image}],
        },
        "status_summary": {
            "replicas": 3,
            "ready_replicas": ready,
            "updated_replicas": 3,
            "available_replicas": ready,
            "observed_generation": observed_generation,
        },
        "conditions": [],
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, health, last_seen_at, observed_at)
                VALUES (:cluster, 'apps', 'apps/v1', 'Deployment', 'pilot-dev', :name, :uid,
                        '1', CAST(:payload AS jsonb), 'healthy', now(), now())
                ON CONFLICT (cluster_id, uid) DO UPDATE
                SET payload = EXCLUDED.payload, observed_at = now(), last_seen_at = now()
                """
            ),
            {
                "cluster": world["cluster_id"],
                "name": name,
                "uid": resource_uid,
                "payload": json.dumps(payload),
            },
        )
    return resource_uid


async def revisions_for(engine: AsyncEngine, workload_uid: str) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id, revision, rollout_state, evidence_state, primary_digest, "
                        "commit_sha, ready_replicas, previous_revision_id, version, "
                        "environment_service_id FROM deployment_revisions "
                        "WHERE workload_uid = :uid ORDER BY revision"
                    ),
                    {"uid": workload_uid},
                )
            ).all()
        )


async def world_with_binding(engine: AsyncEngine) -> dict[str, Any]:
    """A catalog world whose binding points at `pilot-dev/pilot-api`."""
    return await make_world(engine)


async def test_a_reported_workload_becomes_a_revision(engine: AsyncEngine) -> None:
    world = await world_with_binding(engine)
    uid = await seed_workload(engine, world)

    report = await ingest_deployments(engine)

    assert report.created == 1
    rows = await revisions_for(engine, uid)
    assert len(rows) == 1
    assert rows[0][1] == 1
    assert rows[0][2] == "healthy"
    assert rows[0][4] == DIGEST
    # The Sprint 5 binding links it to the catalog without any name guessing.
    assert rows[0][9] == world["environment_service_id"]


async def test_re_reading_the_same_workload_creates_no_second_deployment(
    engine: AsyncEngine,
) -> None:
    """An agent reconnect is not a release.

    Identity is (cluster, uid, generation), and none of those move when a
    connection drops.
    """
    world = await world_with_binding(engine)
    uid = await seed_workload(engine, world)

    first = await ingest_deployments(engine)
    second = await ingest_deployments(engine)
    third = await ingest_deployments(engine)

    assert first.created == 1
    assert (second.created, third.created) == (0, 0)
    assert (second.updated, third.updated) == (1, 1)
    rows = await revisions_for(engine, uid)
    assert len(rows) == 1


async def test_a_new_generation_is_a_new_revision_linked_to_the_previous(
    engine: AsyncEngine,
) -> None:
    world = await world_with_binding(engine)
    uid = await seed_workload(engine, world)
    await ingest_deployments(engine)

    await seed_workload(
        engine,
        world,
        generation=2,
        observed_generation=2,
        uid=uid,
        image=f"ghcr.io/acme/api@{OTHER_DIGEST}",
    )
    await ingest_deployments(engine)

    rows = await revisions_for(engine, uid)
    assert [row[1] for row in rows] == [1, 2]
    assert rows[1][4] == OTHER_DIGEST
    # Lineage: the new revision knows what it replaced.
    assert rows[1][7] == rows[0][0]


async def test_provenance_labels_produce_verified_evidence(engine: AsyncEngine) -> None:
    world = await world_with_binding(engine)
    uid = await seed_workload(
        engine,
        world,
        labels={
            "drake.duosis.com/commit-sha": COMMIT,
            "drake.duosis.com/repository": "acme/api",
            "drake.duosis.com/workflow-run-id": "4242",
        },
    )
    await ingest_deployments(engine)

    rows = await revisions_for(engine, uid)
    assert rows[0][3] == "verified"
    assert rows[0][5] == COMMIT


async def test_a_mutable_tag_stays_unverified(engine: AsyncEngine) -> None:
    world = await world_with_binding(engine)
    uid = await seed_workload(engine, world, image="ghcr.io/acme/api:latest")
    await ingest_deployments(engine)

    rows = await revisions_for(engine, uid)
    assert rows[0][3] == "unverified"
    assert rows[0][4] is None


async def test_a_workload_without_a_generation_is_skipped(engine: AsyncEngine) -> None:
    """Without a revision identity, every re-read would be a new release."""
    world = await world_with_binding(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, health, last_seen_at, observed_at)
                VALUES (:cluster, 'apps', 'apps/v1', 'Deployment', 'pilot-dev', 'weird',
                        :uid, '1', CAST(:payload AS jsonb), 'healthy', now(), now())
                """
            ),
            {
                "cluster": world["cluster_id"],
                "uid": f"uid-{uuidlib.uuid4().hex[:12]}",
                "payload": json.dumps({"spec_summary": {}, "status_summary": {}}),
            },
        )
    report = await ingest_deployments(engine)
    assert report.created == 0
    assert report.skipped == 1


def test_images_and_running_digests_are_read_from_bounded_summaries() -> None:
    images = spec_images(
        {"spec_summary": {"containers": [{"name": "api", "image": f"x@{DIGEST}"}]}}
    )
    assert images[0].digest == DIGEST
    pod = {"status_summary": {"container_images": [{"name": "api", "image_id": f"y@{DIGEST}"}]}}
    assert running_digest([pod], "api") == DIGEST


# ===========================================================================
# health correlation (pure decision)
# ===========================================================================


def test_a_regression_anywhere_outranks_an_improvement_elsewhere() -> None:
    """Faster but erroring is not an improvement."""
    verdict = decide(
        {
            "latency_p95": SignalComparison(1.0, 0.2, lower_is_better=True),
            "error_ratio": SignalComparison(0.01, 0.4, lower_is_better=True),
        },
        incident_count=0,
    )
    assert str(verdict) == "regressed"


def test_nothing_measured_is_insufficient_data_not_stable() -> None:
    """`stable` would read as "we checked and it was fine"."""
    verdict = decide(
        {"error_ratio": SignalComparison(None, None, lower_is_better=True)}, incident_count=0
    )
    assert str(verdict) == "insufficient_data"


def test_small_movement_is_noise() -> None:
    verdict = decide(
        {"latency_p95": SignalComparison(1.0, 1.05, lower_is_better=True)}, incident_count=0
    )
    assert str(verdict) == "stable"


def test_an_incident_in_the_window_is_a_regression() -> None:
    assert str(decide({}, incident_count=1)) == "regressed"


# ===========================================================================
# API and scope
# ===========================================================================


@asynccontextmanager
async def owner(harness: S1Harness, engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        yield client


def deployment_harness() -> S1Harness:
    harness = build_harness()
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-env", "user-b-only"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness


async def test_the_api_reports_the_chain_without_raw_kubernetes(
    engine: AsyncEngine,
) -> None:
    world = await world_with_binding(engine)
    await seed_workload(
        engine,
        world,
        labels={
            "drake.duosis.com/commit-sha": COMMIT,
            "drake.duosis.com/repository": "acme/api",
            "drake.duosis.com/workflow-run-id": "4242",
        },
    )
    await ingest_deployments(engine)
    harness = deployment_harness()

    async with owner(harness, engine) as client:
        listed = await client.get("/v1/deployments")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == 1
        entry = body["items"][0]
        detail = await client.get(f"/v1/deployments/{entry['id']}")
        timeline = await client.get(f"/v1/deployments/{entry['id']}/revisions")
        incidents = await client.get(f"/v1/deployments/{entry['id']}/incidents")

    assert entry["rollout_state"] == "healthy"
    assert entry["evidence_state"] == "verified"
    assert entry["short_digest"] == "a" * 12
    assert entry["short_commit"] == COMMIT[:7]
    assert entry["workflow"]["run_url"] == "https://github.com/acme/api/actions/runs/4242"
    assert entry["replicas"] == {"desired": 3, "ready": 3, "updated": 3, "available": 3}
    assert detail.status_code == 200
    assert [row["revision"] for row in timeline.json()["revisions"]] == [1]
    # Correlation is labelled as correlation.
    assert incidents.json()["correlation_only"] is True

    serialized = listed.text + detail.text
    for forbidden in ("spec_summary", "annotations", "sum(rate(", "promql", "password"):
        assert forbidden not in serialized.lower(), forbidden


async def test_filters_are_an_allowlist(engine: AsyncEngine) -> None:
    world = await world_with_binding(engine)
    await seed_workload(engine, world)
    await ingest_deployments(engine)
    harness = deployment_harness()

    async with owner(harness, engine) as client:
        assert (await client.get("/v1/deployments?rollout_state=healthy")).json()["total"] == 1
        assert (await client.get("/v1/deployments?rollout_state=failed")).json()["total"] == 0
        # A pinned digest with no commit or workflow behind it: real
        # evidence, but the chain does not close.
        partial = await client.get("/v1/deployments?evidence_state=partial")
        assert partial.json()["total"] == 1
        verified = await client.get("/v1/deployments?evidence_state=verified")
        assert verified.json()["total"] == 0
        for query in (
            "rollout_state=exploded",
            "evidence_state=probably",
            "workload_kind=CronJob",
            "started_within=99y",
        ):
            assert (await client.get(f"/v1/deployments?{query}")).status_code == 422, query
        assert (await client.get("/v1/deployments?cursor=nope")).status_code == 422


async def test_deployments_outside_scope_are_invisible(engine: AsyncEngine) -> None:
    from test_catalog_api_integration import grant, make_role, seed_catalog_world

    world = await world_with_binding(engine)
    await seed_workload(engine, world)
    await ingest_deployments(engine)
    await seed_catalog_world(engine)
    harness = deployment_harness()

    async with owner(harness, engine) as client:
        deployment_id = (await client.get("/v1/deployments")).json()["items"][0]["id"]

    await make_role(harness, engine, "Beta Deploy", ["environment.view"])
    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Beta Deploy", "project", "beta")
        listed = (await outsider.get("/v1/deployments")).json()
        hidden = await outsider.get(f"/v1/deployments/{deployment_id}")
        missing = await outsider.get(f"/v1/deployments/{uuidlib.uuid4()}")

    # Absent from the list AND from the total: a count that included it
    # would confirm it exists.
    assert listed["items"] == []
    assert listed["total"] == 0
    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_there_is_no_endpoint_that_mutates_kubernetes(
    engine: AsyncEngine,
) -> None:
    """This sprint observes. Rolling back or scaling needs an authorization
    story that "can read deployments" is not."""
    world = await world_with_binding(engine)
    await seed_workload(engine, world)
    await ingest_deployments(engine)
    harness = deployment_harness()

    async with owner(harness, engine) as client:
        deployment_id = (await client.get("/v1/deployments")).json()["items"][0]["id"]
        me = (await client.get("/v1/me")).json()
        headers = {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": uuidlib.uuid4().hex}
        for path in (
            f"/v1/deployments/{deployment_id}/rollback",
            f"/v1/deployments/{deployment_id}/restart",
            f"/v1/deployments/{deployment_id}/scale",
        ):
            response = await client.post(path, json={}, headers=headers)
            assert response.status_code == 404, path
