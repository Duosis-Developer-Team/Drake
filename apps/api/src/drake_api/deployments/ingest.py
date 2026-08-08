"""Inventory → deployment revisions.

Reads what the cluster agent already reported and records one row per
observed workload generation. Nothing here talks to a cluster, a registry
or a CI provider: the agent's bounded records are the only input, so this
adds no new trust boundary.

Idempotence is the whole design. Identity is
`(cluster, workload_uid, generation)`, so re-reading the same workload —
after an agent reconnect, a restarted worker, or simply the next cycle —
updates that row instead of inventing another deployment. A short
disconnect changes nothing, because neither the UID nor the generation
moves when a connection does.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.deployments.model import (
    COMMIT_KEYS,
    PROVIDER_KEYS,
    REPOSITORY_KEYS,
    RUN_ID_KEYS,
    WORKLOAD_KINDS,
    EvidenceState,
    ImageRef,
    Provenance,
    RolloutState,
    WorkloadObservation,
    evaluate_evidence,
    evaluate_rollout,
    normalize_commit,
    parse_digest,
)

logger = logging.getLogger("drake_api.deployments.ingest")

MAX_IMAGES = 8


@dataclass
class IngestReport:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_label(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Read an allowlisted label or annotation, in priority order."""
    labels = payload.get("labels") or {}
    annotations = payload.get("annotations") or {}
    for key in keys:
        for source in (annotations, labels):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def spec_images(payload: dict[str, Any]) -> list[ImageRef]:
    """The container images the workload declares, bounded."""
    containers = (payload.get("spec_summary") or {}).get("containers") or []
    images: list[ImageRef] = []
    for entry in containers[:MAX_IMAGES]:
        if not isinstance(entry, dict):
            continue
        image = str(entry.get("image") or "")
        if not image:
            continue
        images.append(
            ImageRef(name=str(entry.get("name") or ""), image=image, digest=parse_digest(image))
        )
    return images


def running_digest(pod_payloads: list[dict[str, Any]], container_name: str) -> str | None:
    """The digest a node actually pulled for this container.

    `spec.image` is what someone asked for; `status.imageID` is what is
    running. When the two disagree, the evidence layer reports a conflict
    rather than choosing.
    """
    for payload in pod_payloads:
        for entry in (payload.get("status_summary") or {}).get("container_images") or []:
            if not isinstance(entry, dict):
                continue
            if container_name and str(entry.get("name") or "") != container_name:
                continue
            digest = parse_digest(str(entry.get("image_id") or ""))
            if digest:
                return digest
    return None


async def _workload_rows(
    connection: AsyncConnection, cluster_id: uuid.UUID | None, limit: int
) -> list[Any]:
    clause = "AND r.cluster_id = :cluster" if cluster_id is not None else ""
    params: dict[str, Any] = {"kinds": sorted(WORKLOAD_KINDS), "limit": limit}
    if cluster_id is not None:
        params["cluster"] = cluster_id
    return list(
        (
            await connection.execute(
                text(
                    f"""
                    SELECT r.id, r.cluster_id, r.namespace, r.kind, r.name, r.uid,
                           r.payload, r.first_seen_at, r.last_seen_at, r.observed_at,
                           b.id, b.environment_service_id, b.project_id, b.environment_id,
                           b.service_id
                    FROM inventory_resources r
                    LEFT JOIN service_workload_bindings b
                           ON b.cluster_id = r.cluster_id
                          AND b.namespace = r.namespace
                          AND b.workload_kind = r.kind
                          AND b.workload_name = r.name
                    WHERE r.kind = ANY(:kinds)
                      AND r.lifecycle = 'active'
                      {clause}
                    ORDER BY r.observed_at DESC
                    LIMIT :limit
                    """  # noqa: S608 - `clause` is fixed text
                ),
                params,
            )
        ).all()
    )


async def _pods_for(
    connection: AsyncConnection, cluster_id: uuid.UUID, namespace: str, workload_name: str
) -> list[dict[str, Any]]:
    """Pods belonging to this workload, via the owner chain.

    Deployment → ReplicaSet → Pod, so the match is on the owner reference
    the agent already records — never on a name prefix, which would
    silently pick up an unrelated workload with a shared prefix.
    """
    rows = (
        await connection.execute(
            text(
                """
                WITH owners AS (
                    SELECT uid FROM inventory_resources
                    WHERE cluster_id = :cluster AND namespace = :namespace
                      AND kind IN ('ReplicaSet', 'StatefulSet', 'DaemonSet')
                      AND payload->'owners' @> CAST(:owner AS jsonb)
                    UNION ALL
                    SELECT uid FROM inventory_resources
                    WHERE cluster_id = :cluster AND namespace = :namespace
                      AND kind IN ('StatefulSet', 'DaemonSet')
                      AND name = :name
                )
                SELECT p.payload
                FROM inventory_resources p
                WHERE p.cluster_id = :cluster AND p.namespace = :namespace
                  AND p.kind = 'Pod' AND p.lifecycle = 'active'
                  AND EXISTS (
                    SELECT 1 FROM owners o
                    WHERE p.payload->'owners' @> CAST(
                      json_build_array(json_build_object('uid', o.uid))::text AS jsonb)
                  )
                ORDER BY p.observed_at DESC
                LIMIT 20
                """
            ),
            {
                "cluster": cluster_id,
                "namespace": namespace,
                "name": workload_name,
                "owner": json.dumps([{"name": workload_name}]),
            },
        )
    ).all()
    return [row[0] or {} for row in rows]


async def ingest_workload(
    connection: AsyncConnection, row: Any, *, now: datetime
) -> str:
    """Record (or refresh) one workload's current revision."""
    payload: dict[str, Any] = row[6] or {}
    spec = payload.get("spec_summary") or {}
    status = payload.get("status_summary") or {}

    generation = _as_int(spec.get("generation"))
    if generation is None:
        # Without a generation there is no revision identity, and inventing
        # one would make every re-read a new deployment.
        return "skipped"

    cluster_id, namespace, kind, name, workload_uid = row[1], row[2], row[3], row[4], row[5]
    desired = _as_int(spec.get("replicas"))
    if kind == "DaemonSet":
        desired = _as_int(status.get("desired"))

    observation = WorkloadObservation(
        kind=kind,
        generation=generation,
        observed_generation=_as_int(status.get("observed_generation")),
        desired_replicas=desired,
        ready_replicas=_as_int(status.get("ready_replicas") or status.get("ready")),
        updated_replicas=_as_int(status.get("updated_replicas") or status.get("updated")),
        available_replicas=_as_int(
            status.get("available_replicas") or status.get("available")
        ),
        conditions=tuple(payload.get("conditions") or ()),
        observed_at=row[9],
        first_seen_at=row[7],
    )
    verdict = evaluate_rollout(observation, now=now)

    images = spec_images(payload)
    primary = images[0] if images else None
    declared = primary.digest if primary else None
    pods = await _pods_for(connection, cluster_id, namespace, name)
    running = running_digest(pods, primary.name if primary else "")

    provenance = Provenance(
        commit_sha=normalize_commit(_first_label(payload, COMMIT_KEYS)),
        workflow_provider=(_first_label(payload, PROVIDER_KEYS) or "github")
        if _first_label(payload, RUN_ID_KEYS)
        else None,
        workflow_repository=_first_label(payload, REPOSITORY_KEYS),
        workflow_run_id=_first_label(payload, RUN_ID_KEYS),
        declared_digest=declared,
        running_digest=running,
    )
    evidence = evaluate_evidence(provenance)

    enriched = [
        ImageRef(
            image.name,
            image.image,
            image.digest or (running if image is primary else None),
        ).to_payload()
        for image in images
    ]

    # The digest Drake reports as running: what the node pulled if known,
    # otherwise what the spec pinned. Never a tag.
    primary_digest = running or declared
    if evidence.state is EvidenceState.CONFLICT:
        # With two disagreeing digests there is no single answer, so the
        # column stays empty and the conflict is what gets reported.
        primary_digest = None

    result = await connection.execute(
        text(
            """
            INSERT INTO deployment_revisions
                (cluster_id, namespace, workload_kind, workload_name, workload_uid,
                 binding_id, environment_service_id, project_id, environment_id, service_id,
                 revision, observed_generation, images, primary_image, primary_digest,
                 commit_sha, workflow_provider, workflow_repository, workflow_run_id,
                 evidence_state, evidence_detail, rollout_state, rollout_reason,
                 desired_replicas, ready_replicas, updated_replicas, available_replicas,
                 rollout_started_at, rollout_completed_at, last_seen_at, previous_revision_id)
            VALUES
                (:cluster, :namespace, :kind, :name, :uid,
                 :binding, :es, :project, :environment, :service,
                 :revision, :observed_generation, CAST(:images AS jsonb), :primary_image,
                 :primary_digest, :commit, :provider, :repository, :run_id,
                 :evidence_state, CAST(:evidence_detail AS jsonb), :rollout_state, :reason,
                 :desired, :ready, :updated, :available,
                 :started_at, :completed_at, :last_seen,
                 (SELECT id FROM deployment_revisions
                  WHERE cluster_id = :cluster AND workload_uid = :uid AND revision < :revision
                  ORDER BY revision DESC LIMIT 1))
            ON CONFLICT (cluster_id, workload_uid, revision) DO UPDATE
            SET observed_generation = EXCLUDED.observed_generation,
                images = EXCLUDED.images,
                primary_image = EXCLUDED.primary_image,
                primary_digest = EXCLUDED.primary_digest,
                commit_sha = EXCLUDED.commit_sha,
                workflow_provider = EXCLUDED.workflow_provider,
                workflow_repository = EXCLUDED.workflow_repository,
                workflow_run_id = EXCLUDED.workflow_run_id,
                evidence_state = EXCLUDED.evidence_state,
                evidence_detail = EXCLUDED.evidence_detail,
                rollout_state = EXCLUDED.rollout_state,
                rollout_reason = EXCLUDED.rollout_reason,
                desired_replicas = EXCLUDED.desired_replicas,
                ready_replicas = EXCLUDED.ready_replicas,
                updated_replicas = EXCLUDED.updated_replicas,
                available_replicas = EXCLUDED.available_replicas,
                -- Completion is recorded once. A rollout that finished at
                -- 10:00 did not finish again at 10:05 because the worker
                -- looked twice.
                rollout_completed_at = COALESCE(
                    deployment_revisions.rollout_completed_at, EXCLUDED.rollout_completed_at),
                last_seen_at = EXCLUDED.last_seen_at,
                binding_id = COALESCE(EXCLUDED.binding_id, deployment_revisions.binding_id),
                environment_service_id = COALESCE(
                    EXCLUDED.environment_service_id,
                    deployment_revisions.environment_service_id),
                project_id = COALESCE(EXCLUDED.project_id, deployment_revisions.project_id),
                environment_id = COALESCE(
                    EXCLUDED.environment_id, deployment_revisions.environment_id),
                service_id = COALESCE(EXCLUDED.service_id, deployment_revisions.service_id),
                version = deployment_revisions.version + 1,
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {
            "cluster": cluster_id,
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "uid": workload_uid,
            "binding": row[10],
            "es": row[11],
            "project": row[12],
            "environment": row[13],
            "service": row[14],
            "revision": generation,
            "observed_generation": observation.observed_generation,
            "images": json.dumps(enriched),
            "primary_image": primary.image if primary else None,
            "primary_digest": primary_digest,
            "commit": provenance.commit_sha,
            "provider": provenance.workflow_provider,
            "repository": provenance.workflow_repository,
            "run_id": provenance.workflow_run_id,
            "evidence_state": str(evidence.state),
            "evidence_detail": json.dumps(evidence.detail),
            "rollout_state": str(verdict.state),
            "reason": verdict.reason,
            "desired": observation.desired_replicas,
            "ready": observation.ready_replicas,
            "updated": observation.updated_replicas,
            "available": observation.available_replicas,
            # First sight of this generation is the best available rollout
            # start; the agent's own first_seen_at is used when it predates
            # this scan, so a restart does not reset the clock.
            "started_at": row[7] or now,
            "completed_at": (row[9] or now) if verdict.complete else None,
            "last_seen": row[8] or now,
        },
    )
    inserted = result.first()
    return "created" if inserted and inserted[0] else "updated"


async def ingest_deployments(
    engine: AsyncEngine, *, cluster_id: uuid.UUID | None = None, limit: int = 200
) -> IngestReport:
    """One bounded pass over reported workloads."""
    report = IngestReport()
    async with engine.connect() as connection:
        rows = await _workload_rows(connection, cluster_id, limit)
    now = datetime.now(UTC)

    for row in rows:
        report.scanned += 1
        try:
            async with engine.begin() as connection:
                outcome = await ingest_workload(connection, row, now=now)
        except Exception:
            # One workload's problem is one workload's problem; the rest of
            # the estate still gets recorded.
            report.skipped += 1
            logger.warning("deployment ingest failed for one workload")
            continue
        if outcome == "created":
            report.created += 1
        elif outcome == "updated":
            report.updated += 1
        else:
            report.skipped += 1
    return report


def rollout_is_terminal(state: str) -> bool:
    return state in (str(RolloutState.HEALTHY), str(RolloutState.FAILED))
