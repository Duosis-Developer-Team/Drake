"""Inventory ingestion (ADR-0017): heartbeat, atomic snapshots, watch events.

Identity comes exclusively from the verified PoP principal — claimed
cluster/agent ids in bodies are cross-checked and refused on mismatch,
never trusted. Schema validation is fail-closed (extra=forbid, bounded,
forbidden kinds and credential-shaped values rejected). Duplicates are
idempotent no-ops; sequence gaps and torn snapshots are refused with an
explicit `reconcile_required`; the current projection is only ever touched
by ONE transaction per completed snapshot.
"""

import datetime as dt
import hashlib
import json
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.agents.health_rules import derive_health
from drake_api.agents.identity import AgentPrincipal, authenticate_agent
from drake_api.agents.maintenance import run_inventory_maintenance
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/internal/v1/agent", tags=["agent-ingest"])

_API_VERSION = "drake.duosis.com/agent/v1"

ALLOWED_KINDS = frozenset(
    {
        "Namespace",
        "Node",
        "Pod",
        "Service",
        "EndpointSlice",
        "Deployment",
        "ReplicaSet",
        "StatefulSet",
        "DaemonSet",
        "Job",
        "CronJob",
        "PersistentVolumeClaim",
        "PersistentVolume",
        "StorageClass",
        "HorizontalPodAutoscaler",
        "PodDisruptionBudget",
        "ResourceQuota",
        "LimitRange",
        "Event",
        "ServiceMonitor",
        "PodMonitor",
        "PrometheusRule",
    }
)

_FORBIDDEN_KEY_SUBSTRINGS = ("secret", "token", "password", "credential", "last-applied")
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT shape
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key shape
)

BoundedStr = Annotated[str, StringConstraints(max_length=512)]
SummaryValue = Annotated[str, StringConstraints(max_length=256)] | int | float | bool | None


class OwnerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=253)]
    uid: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    status: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    reason: Annotated[str, StringConstraints(max_length=128)] | None = None
    message: Annotated[str, StringConstraints(max_length=256)] | None = None


class ResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_group: Annotated[str, StringConstraints(max_length=253)]
    api_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    kind: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    namespace: Annotated[str, StringConstraints(min_length=1, max_length=63)] | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=253)]
    uid: Annotated[str, StringConstraints(min_length=8, max_length=64)]
    resource_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    labels: dict[Annotated[str, StringConstraints(max_length=317)], BoundedStr] = Field(
        default_factory=dict, max_length=32
    )
    annotations: dict[Annotated[str, StringConstraints(max_length=317)], BoundedStr] = Field(
        default_factory=dict, max_length=32
    )
    owners: list[OwnerRef] = Field(default_factory=list, max_length=8)
    spec_summary: dict[Annotated[str, StringConstraints(max_length=63)], SummaryValue] = Field(
        default_factory=dict, max_length=24
    )
    status_summary: dict[Annotated[str, StringConstraints(max_length=63)], SummaryValue] = Field(
        default_factory=dict, max_length=24
    )
    conditions: list[Condition] = Field(default_factory=list, max_length=12)
    observed_at: dt.datetime


class _AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: Literal["drake.duosis.com/agent/v1"]
    cluster_id: uuid.UUID
    agent_id: uuid.UUID
    request_id: uuid.UUID
    source_time: dt.datetime
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)


class HeartbeatMessage(_AgentMessage):
    kind: Literal["heartbeat"]
    agent_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    inventory_state: Literal["empty", "reconciling", "fresh", "stale", "reconcile_required"]


class SnapshotBeginMessage(_AgentMessage):
    kind: Literal["snapshot_begin"]
    agent_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    snapshot_uid: uuid.UUID


class SnapshotPageMessage(_AgentMessage):
    kind: Literal["snapshot_page"]
    agent_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    snapshot_uid: uuid.UUID
    page_number: int = Field(ge=1, le=10_000)
    resources: list[ResourceRecord] = Field(max_length=500)


class SnapshotCompleteMessage(_AgentMessage):
    kind: Literal["snapshot_complete"]
    agent_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    snapshot_uid: uuid.UUID
    total_pages: int = Field(ge=0, le=10_000)
    total_resources: int = Field(ge=0, le=500_000)


class WatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: uuid.UUID
    change_type: Literal["added", "updated", "deleted"]
    resource: ResourceRecord


class WatchEventsMessage(_AgentMessage):
    kind: Literal["watch_events"]
    agent_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    events: list[WatchEvent] = Field(min_length=1, max_length=500)


def _rejected(reason: str) -> HTTPException:
    # Schema-level refusals: explicit, bounded, no echo of payload content.
    return HTTPException(status_code=422, detail=f"message rejected: {reason}")


def _reconcile_required() -> HTTPException:
    return HTTPException(status_code=409, detail="reconcile_required")


class _SequenceGapError(Exception):
    """Raised inside the ingest transaction; handled OUTSIDE it so the
    reconcile_required mark survives the rollback of the refused work."""


class _StaleSnapshotError(Exception):
    """A page/complete referenced an unknown or superseded snapshot; the
    reconcile demand must be committed outside the rolled-back request."""


class _TornSnapshotError(Exception):
    def __init__(self, snapshot_id: uuid.UUID) -> None:
        self.snapshot_id = snapshot_id


async def _demand_reconcile(
    engine: Any, agent_id: uuid.UUID, discard_snapshot: uuid.UUID | None = None
) -> None:
    """Its own committed transaction: the refusal must be durable even
    though the refused request's transaction rolled back."""
    async with engine.begin() as connection:
        if discard_snapshot is not None:
            await connection.execute(
                text("UPDATE inventory_snapshots SET status = 'discarded' WHERE id = :id"),
                {"id": discard_snapshot},
            )
        await connection.execute(
            text("UPDATE cluster_agents SET inventory_state = 'reconcile_required' WHERE id = :id"),
            {"id": agent_id},
        )


async def _lock_writer_state(connection: AsyncConnection, cluster_id: uuid.UUID) -> Any:
    """Upsert-then-lock the per-cluster inventory writer row — the single
    serialization point for every inventory write (ADR-0017). Lock order
    is always writer state FIRST, then the agent row."""
    await connection.execute(
        text(
            "INSERT INTO cluster_inventory_state (cluster_id) VALUES (:cluster_id) "
            "ON CONFLICT (cluster_id) DO NOTHING"
        ),
        {"cluster_id": cluster_id},
    )
    return (
        await connection.execute(
            text(
                """
                SELECT active_agent_id, current_generation, applied_generation,
                       applied_snapshot_id, pending_snapshot_id
                FROM cluster_inventory_state WHERE cluster_id = :cluster_id
                FOR UPDATE
                """
            ),
            {"cluster_id": cluster_id},
        )
    ).one()


def _require_active_writer(state: Any, principal: AgentPrincipal) -> None:
    """Only ONE agent may write inventory for a cluster: the most recently
    enrolled one. A superseded agent gets the generic refusal — it must
    not learn who replaced it."""
    if state[0] != principal.agent_id:
        raise HTTPException(status_code=403, detail="agent authentication failed")


def _page_content_hash(resources: list[ResourceRecord]) -> str:
    canonical = json.dumps(
        [resource.model_dump(mode="json") for resource in resources], sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_resource(resource: ResourceRecord) -> None:
    """Fail-closed content checks beyond shape: forbidden kinds and
    credential-shaped keys/values never enter storage."""
    if resource.kind not in ALLOWED_KINDS:
        raise _rejected("kind not allowed")
    for mapping in (resource.labels, resource.annotations):
        for key, value in mapping.items():
            lowered = key.lower()
            if any(fragment in lowered for fragment in _FORBIDDEN_KEY_SUBSTRINGS):
                raise _rejected("credential-shaped key")
            _reject_credential_value(value)
    for summary in (resource.spec_summary, resource.status_summary):
        for summary_key, summary_value in summary.items():
            if any(fragment in summary_key.lower() for fragment in _FORBIDDEN_KEY_SUBSTRINGS):
                raise _rejected("credential-shaped key")
            if isinstance(summary_value, str):
                _reject_credential_value(summary_value)
    for condition in resource.conditions:
        if condition.message:
            _reject_credential_value(condition.message)


def _reject_credential_value(value: str) -> None:
    for pattern in _CREDENTIAL_VALUE_PATTERNS:
        if pattern.search(value):
            raise _rejected("credential-shaped value")


def _check_claims(principal: AgentPrincipal, message: _AgentMessage) -> None:
    """Claimed ids must match the VERIFIED identity; mismatch is refused
    with the same generic agent refusal (no oracle)."""
    if message.agent_id != principal.agent_id or message.cluster_id != principal.cluster_id:
        raise HTTPException(status_code=403, detail="agent authentication failed")


def _payload_column(resource: ResourceRecord) -> str:
    return json.dumps(
        {
            "labels": resource.labels,
            "annotations": resource.annotations,
            "owners": [owner.model_dump() for owner in resource.owners],
            "spec_summary": resource.spec_summary,
            "status_summary": resource.status_summary,
            "conditions": [
                condition.model_dump(exclude_none=True) for condition in resource.conditions
            ],
        }
    )


async def _sequence_gate(
    connection: AsyncConnection, principal: AgentPrincipal, sequence: int
) -> str:
    """Returns 'apply' | 'duplicate'.

    A gap raises ONLY the internal `_SequenceGapError`: the refused
    request's transaction must roll back completely, and the durable
    `reconcile_required` mark is committed by the endpoint's handler in a
    SEPARATE transaction (`_demand_reconcile`) — never in here, where the
    rollback would silently erase it. The row is locked FOR UPDATE so
    concurrent messages from a restarted duplicate agent serialize
    instead of racing.
    """
    row = (
        await connection.execute(
            text("SELECT last_sequence FROM cluster_agents WHERE id = :id FOR UPDATE"),
            {"id": principal.agent_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=403, detail="agent authentication failed")
    last_sequence = int(row[0])
    if sequence <= last_sequence:
        return "duplicate"
    if sequence > last_sequence + 1:
        raise _SequenceGapError
    await connection.execute(
        text("UPDATE cluster_agents SET last_sequence = :sequence WHERE id = :id"),
        {"sequence": sequence, "id": principal.agent_id},
    )
    return "apply"


@router.post("/heartbeat")
async def heartbeat(
    request: Request,
    body: HeartbeatMessage,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    _check_claims(principal, body)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        # Heartbeat is liveness only: it never advances the inventory
        # sequence and never makes inventory fresh (ADR-0017 §4).
        await connection.execute(
            text(
                "UPDATE cluster_agents SET last_heartbeat_at = now(), "
                "agent_version = :version WHERE id = :id"
            ),
            {"version": body.agent_version, "id": principal.agent_id},
        )
    return {"api_version": _API_VERSION, "kind": "ack", "result": "ok"}


@router.post("/inventory/snapshot/begin")
async def snapshot_begin(
    request: Request,
    body: SnapshotBeginMessage,
    background: BackgroundTasks,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    _check_claims(principal, body)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    # Housekeeping rides on the reconcile boundary, AFTER the response:
    # bounded batches, advisory-locked, never touching the projection.
    background.add_task(run_inventory_maintenance, engine, settings, principal.cluster_id)
    async with engine.begin() as connection:
        # ALL checks precede ANY mutation. Lock order: writer state → agent.
        state = await _lock_writer_state(connection, principal.cluster_id)
        _require_active_writer(state, principal)
        known = (
            await connection.execute(
                text(
                    "SELECT id FROM inventory_snapshots "
                    "WHERE cluster_id = :cluster_id AND snapshot_uid = :snapshot_uid"
                ),
                {"cluster_id": principal.cluster_id, "snapshot_uid": body.snapshot_uid},
            )
        ).first()
        if known is not None:
            # Exact replay of an already-registered begin: idempotent.
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}
        last_sequence = int(
            (
                await connection.execute(
                    text("SELECT last_sequence FROM cluster_agents WHERE id = :id FOR UPDATE"),
                    {"id": principal.agent_id},
                )
            ).scalar_one()
        )
        if body.sequence <= last_sequence:
            # A DELAYED begin from the past (or a lost-ACK collision): it
            # opens nothing, regresses nothing, and changes no state. The
            # agent's follow-up pages will miss their snapshot and trigger
            # a clean reconcile with a fresh sequence.
            return {"api_version": _API_VERSION, "kind": "ack", "result": "stale"}
        # A begin IS the reconcile action, so it may jump the sequence
        # forward (unlike pages/completes/events, which must be gapless).
        new_generation = int(state[1]) + 1
        inserted = (
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_snapshots
                        (cluster_id, agent_id, snapshot_uid, generation)
                    VALUES (:cluster_id, :agent_id, :snapshot_uid, :generation)
                    RETURNING id
                    """
                ),
                {
                    "cluster_id": principal.cluster_id,
                    "agent_id": principal.agent_id,
                    "snapshot_uid": body.snapshot_uid,
                    "generation": new_generation,
                },
            )
        ).one()
        # The new snapshot supersedes any pending one; the projection stays.
        if state[4] is not None:
            await connection.execute(
                text(
                    "UPDATE inventory_snapshots SET status = 'discarded' "
                    "WHERE id = :id AND status = 'pending'"
                ),
                {"id": state[4]},
            )
        await connection.execute(
            text(
                """
                UPDATE cluster_inventory_state
                SET current_generation = :generation, pending_snapshot_id = :snapshot_id,
                    updated_at = now()
                WHERE cluster_id = :cluster_id
                """
            ),
            {
                "generation": new_generation,
                "snapshot_id": inserted[0],
                "cluster_id": principal.cluster_id,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE cluster_agents
                SET last_sequence = :sequence, inventory_state = 'reconciling',
                    agent_version = COALESCE(:version, agent_version)
                WHERE id = :id
                """
            ),
            {"sequence": body.sequence, "version": body.agent_version, "id": principal.agent_id},
        )
    return {"api_version": _API_VERSION, "kind": "ack", "result": "accepted"}


@router.post("/inventory/snapshot/page")
async def snapshot_page(
    request: Request,
    body: SnapshotPageMessage,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    _check_claims(principal, body)
    for resource in body.resources:
        _validate_resource(resource)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        return await _snapshot_page_txn(engine, principal, body)
    except _SequenceGapError:
        await _demand_reconcile(engine, principal.agent_id)
        raise _reconcile_required() from None
    except _StaleSnapshotError:
        await _demand_reconcile(engine, principal.agent_id)
        raise _reconcile_required() from None
    except _TornSnapshotError as torn:
        await _demand_reconcile(engine, principal.agent_id, discard_snapshot=torn.snapshot_id)
        raise _reconcile_required() from None


async def _snapshot_page_txn(
    engine: Any, principal: AgentPrincipal, body: SnapshotPageMessage
) -> dict[str, Any]:
    async with engine.begin() as connection:
        state = await _lock_writer_state(connection, principal.cluster_id)
        _require_active_writer(state, principal)
        snapshot = (
            await connection.execute(
                text(
                    """
                    SELECT id, status FROM inventory_snapshots
                    WHERE cluster_id = :cluster_id AND snapshot_uid = :snapshot_uid
                    """
                ),
                {"cluster_id": principal.cluster_id, "snapshot_uid": body.snapshot_uid},
            )
        ).first()
        if snapshot is None or snapshot[1] == "discarded":
            # Unknown or superseded snapshot: the agent must run a fresh
            # full reconcile, and the demand must survive this rollback.
            raise _StaleSnapshotError
        if snapshot[1] == "complete":
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}
        content_hash = _page_content_hash(body.resources)
        gate = await _sequence_gate(connection, principal, body.sequence)
        if gate == "duplicate":
            # Replays are no-ops only when the content matches what was
            # stored; the same page number with DIFFERENT content is a
            # torn stream, not a retry.
            existing_hash = (
                await connection.execute(
                    text(
                        "SELECT content_hash FROM inventory_snapshot_pages "
                        "WHERE snapshot_id = :snapshot_id AND page_number = :page_number"
                    ),
                    {"snapshot_id": snapshot[0], "page_number": body.page_number},
                )
            ).first()
            if existing_hash is not None and existing_hash[0] != content_hash:
                raise _TornSnapshotError(snapshot[0])
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}

        page = (
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_snapshot_pages
                        (snapshot_id, page_number, resource_count, content_hash)
                    VALUES (:snapshot_id, :page_number, :resource_count, :content_hash)
                    ON CONFLICT (snapshot_id, page_number) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "snapshot_id": snapshot[0],
                    "page_number": body.page_number,
                    "resource_count": len(body.resources),
                    "content_hash": content_hash,
                },
            )
        ).first()
        if page is None:
            existing_hash = (
                await connection.execute(
                    text(
                        "SELECT content_hash FROM inventory_snapshot_pages "
                        "WHERE snapshot_id = :snapshot_id AND page_number = :page_number"
                    ),
                    {"snapshot_id": snapshot[0], "page_number": body.page_number},
                )
            ).scalar_one()
            if existing_hash != content_hash:
                raise _TornSnapshotError(snapshot[0])
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}
        for resource in body.resources:
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_staging_resources
                        (snapshot_id, api_group, api_version, kind, namespace,
                         name, uid, resource_version, payload, observed_at)
                    VALUES
                        (:snapshot_id, :api_group, :api_version, :kind, :namespace,
                         :name, :uid, :resource_version, CAST(:payload AS jsonb),
                         :observed_at)
                    ON CONFLICT (snapshot_id, uid) DO NOTHING
                    """
                ),
                {
                    "snapshot_id": snapshot[0],
                    "api_group": resource.api_group,
                    "api_version": resource.api_version,
                    "kind": resource.kind,
                    "namespace": resource.namespace,
                    "name": resource.name,
                    "uid": resource.uid,
                    "resource_version": resource.resource_version,
                    "payload": _payload_column(resource),
                    "observed_at": resource.observed_at,
                },
            )
        await connection.execute(
            text(
                "UPDATE inventory_snapshots SET received_pages = received_pages + 1, "
                "resource_count = resource_count + :count WHERE id = :id"
            ),
            {"count": len(body.resources), "id": snapshot[0]},
        )
    return {"api_version": _API_VERSION, "kind": "ack", "result": "accepted"}


@router.post("/inventory/snapshot/complete")
async def snapshot_complete(
    request: Request,
    body: SnapshotCompleteMessage,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    _check_claims(principal, body)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        return await _snapshot_complete_txn(engine, principal, body, settings)
    except _SequenceGapError:
        await _demand_reconcile(engine, principal.agent_id)
        raise _reconcile_required() from None
    except _StaleSnapshotError:
        await _demand_reconcile(engine, principal.agent_id)
        raise _reconcile_required() from None
    except _TornSnapshotError as torn:
        # The refusal must be durable although the request's transaction
        # rolled back: discard + reconcile_required commit separately.
        await _demand_reconcile(engine, principal.agent_id, discard_snapshot=torn.snapshot_id)
        raise _reconcile_required() from None


async def _snapshot_complete_txn(
    engine: Any, principal: AgentPrincipal, body: SnapshotCompleteMessage, settings: Settings
) -> dict[str, Any]:
    async with engine.begin() as connection:
        state = await _lock_writer_state(connection, principal.cluster_id)
        _require_active_writer(state, principal)
        snapshot = (
            await connection.execute(
                text(
                    """
                    SELECT id, status, received_pages, resource_count, generation
                    FROM inventory_snapshots
                    WHERE cluster_id = :cluster_id AND snapshot_uid = :snapshot_uid
                    FOR UPDATE
                    """
                ),
                {"cluster_id": principal.cluster_id, "snapshot_uid": body.snapshot_uid},
            )
        ).first()
        if snapshot is None or snapshot[1] == "discarded":
            # A superseded snapshot can never complete (ADR-0017).
            raise _StaleSnapshotError
        if snapshot[1] == "complete":
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}
        # An older generation can never apply over a newer projection.
        if int(snapshot[4]) <= int(state[2]):
            raise _TornSnapshotError(snapshot[0])
        # Bounded completion window: a timed-out snapshot cannot apply;
        # the last good projection stays and freshness shows the gap.
        expired = (
            await connection.execute(
                text(
                    "SELECT started_at < now() - make_interval(secs => :ttl) "
                    "FROM inventory_snapshots WHERE id = :id"
                ),
                {"ttl": settings.agent_snapshot_ttl_seconds, "id": snapshot[0]},
            )
        ).scalar_one()
        if bool(expired):
            raise _TornSnapshotError(snapshot[0])
        gate = await _sequence_gate(connection, principal, body.sequence)
        if gate == "duplicate":
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}

        # Page continuity: counters alone can lie. The staged page set must
        # be EXACTLY 1..total_pages and the DISTINCT staged resources must
        # match the declared total (duplicate UIDs across pages collapse in
        # staging, so inflated totals surface here).
        page_rows = (
            await connection.execute(
                text(
                    "SELECT page_number FROM inventory_snapshot_pages "
                    "WHERE snapshot_id = :id ORDER BY page_number"
                ),
                {"id": snapshot[0]},
            )
        ).all()
        page_numbers = [int(row[0]) for row in page_rows]
        staged_count = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM inventory_staging_resources WHERE snapshot_id = :id"
                    ),
                    {"id": snapshot[0]},
                )
            ).scalar_one()
        )
        if (
            page_numbers != list(range(1, body.total_pages + 1))
            or staged_count != body.total_resources
            or int(snapshot[2]) != body.total_pages
            or int(snapshot[3]) != body.total_resources
        ):
            raise _TornSnapshotError(snapshot[0])

        await _apply_snapshot(connection, principal, snapshot[0])
        await connection.execute(
            text(
                """
                UPDATE cluster_inventory_state
                SET applied_generation = :generation, applied_snapshot_id = :snapshot_id,
                    pending_snapshot_id = NULL, updated_at = now()
                WHERE cluster_id = :cluster_id
                """
            ),
            {
                "generation": int(snapshot[4]),
                "snapshot_id": snapshot[0],
                "cluster_id": principal.cluster_id,
            },
        )
    return {"api_version": _API_VERSION, "kind": "ack", "result": "applied"}


async def _apply_snapshot(
    connection: AsyncConnection, principal: AgentPrincipal, snapshot_id: uuid.UUID
) -> None:
    """The ONE transaction that touches the current projection: staged rows
    upsert, absentees flip to missing, change events append, and the
    snapshot + agent state finalize together."""
    staged = (
        await connection.execute(
            text(
                """
                SELECT api_group, api_version, kind, namespace, name, uid,
                       resource_version, payload, observed_at
                FROM inventory_staging_resources WHERE snapshot_id = :snapshot_id
                """
            ),
            {"snapshot_id": snapshot_id},
        )
    ).all()
    seen_uids: list[str] = []
    for row in staged:
        payload = row[7] if isinstance(row[7], dict) else json.loads(row[7])
        health, reasons = derive_health(str(row[2]), payload)
        existing = (
            await connection.execute(
                text(
                    "SELECT id, lifecycle FROM inventory_resources "
                    "WHERE cluster_id = :cluster_id AND uid = :uid"
                ),
                {"cluster_id": principal.cluster_id, "uid": row[5]},
            )
        ).first()
        change_type = None
        if existing is None:
            change_type = "added"
        elif existing[1] == "missing":
            change_type = "restored"
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, health, health_reasons, lifecycle,
                     last_seen_at, observed_at, last_snapshot_id)
                VALUES
                    (:cluster_id, :api_group, :api_version, :kind, :namespace, :name,
                     :uid, :resource_version, CAST(:payload AS jsonb), :health,
                     CAST(:reasons AS jsonb), 'active', now(), :observed_at,
                     :snapshot_id)
                ON CONFLICT (cluster_id, uid) DO UPDATE SET
                    api_group = EXCLUDED.api_group,
                    api_version = EXCLUDED.api_version,
                    kind = EXCLUDED.kind,
                    namespace = EXCLUDED.namespace,
                    name = EXCLUDED.name,
                    resource_version = EXCLUDED.resource_version,
                    payload = EXCLUDED.payload,
                    health = EXCLUDED.health,
                    health_reasons = EXCLUDED.health_reasons,
                    lifecycle = 'active',
                    last_seen_at = now(),
                    observed_at = EXCLUDED.observed_at,
                    last_snapshot_id = EXCLUDED.last_snapshot_id
                """
            ),
            {
                "cluster_id": principal.cluster_id,
                "api_group": row[0],
                "api_version": row[1],
                "kind": row[2],
                "namespace": row[3],
                "name": row[4],
                "uid": row[5],
                "resource_version": row[6],
                "payload": json.dumps(payload),
                "health": health,
                "reasons": json.dumps(reasons),
                "observed_at": row[8],
                "snapshot_id": snapshot_id,
            },
        )
        if change_type is not None:
            await _append_change(
                connection,
                principal.cluster_id,
                str(row[2]),
                row[3],
                str(row[4]),
                str(row[5]),
                change_type,
            )
        seen_uids.append(str(row[5]))

    # Resources absent from this snapshot become missing — never deleted.
    missing_rows = (
        await connection.execute(
            text(
                """
                UPDATE inventory_resources
                SET lifecycle = 'missing', health = 'unknown',
                    health_reasons = '["resource_missing_from_snapshot"]'::jsonb
                WHERE cluster_id = :cluster_id AND lifecycle = 'active'
                  AND NOT (uid = ANY(:seen))
                RETURNING kind, namespace, name, uid
                """
            ),
            {"cluster_id": principal.cluster_id, "seen": seen_uids},
        )
    ).all()
    for row in missing_rows:
        await _append_change(
            connection,
            principal.cluster_id,
            str(row[0]),
            row[1],
            str(row[2]),
            str(row[3]),
            "missing",
        )

    await connection.execute(
        text(
            "UPDATE inventory_snapshots SET status = 'complete', completed_at = now() "
            "WHERE id = :id"
        ),
        {"id": snapshot_id},
    )
    await connection.execute(
        text("DELETE FROM inventory_staging_resources WHERE snapshot_id = :id"),
        {"id": snapshot_id},
    )
    await connection.execute(
        text(
            "UPDATE cluster_agents SET inventory_state = 'fresh', "
            "last_reconcile_at = now() WHERE id = :id"
        ),
        {"id": principal.agent_id},
    )


async def _append_change(
    connection: AsyncConnection,
    cluster_id: uuid.UUID,
    kind: str,
    namespace: str | None,
    name: str,
    uid: str,
    change_type: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO inventory_change_events
                (cluster_id, kind, namespace, name, uid, change_type)
            VALUES (:cluster_id, :kind, :namespace, :name, :uid, :change_type)
            """
        ),
        {
            "cluster_id": cluster_id,
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "uid": uid,
            "change_type": change_type,
        },
    )


@router.post("/inventory/events")
async def watch_events(
    request: Request,
    body: WatchEventsMessage,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    _check_claims(principal, body)
    for event in body.events:
        _validate_resource(event.resource)
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        return await _watch_events_txn(engine, principal, body)
    except _SequenceGapError:
        await _demand_reconcile(engine, principal.agent_id)
        raise _reconcile_required() from None


async def _watch_events_txn(
    engine: Any, principal: AgentPrincipal, body: WatchEventsMessage
) -> dict[str, Any]:
    async with engine.begin() as connection:
        # Watch events bind to the CURRENT applied generation: only the
        # active writer, only after its snapshot applied, only while its
        # own state is fresh. Everything else is a reconcile demand.
        state = await _lock_writer_state(connection, principal.cluster_id)
        _require_active_writer(state, principal)
        if state[3] is None or state[4] is not None:
            raise _reconcile_required()
        state_row = (
            await connection.execute(
                text("SELECT inventory_state FROM cluster_agents WHERE id = :id"),
                {"id": principal.agent_id},
            )
        ).first()
        if state_row is None:
            raise HTTPException(status_code=403, detail="agent authentication failed")
        if str(state_row[0]) != "fresh":
            raise _reconcile_required()
        gate = await _sequence_gate(connection, principal, body.sequence)
        if gate == "duplicate":
            return {"api_version": _API_VERSION, "kind": "ack", "result": "duplicate"}

        for event in body.events:
            resource = event.resource
            if event.change_type == "deleted":
                changed = (
                    await connection.execute(
                        text(
                            """
                            UPDATE inventory_resources
                            SET lifecycle = 'missing', health = 'unknown',
                                health_reasons = '["resource_deleted"]'::jsonb,
                                last_seen_at = now()
                            WHERE cluster_id = :cluster_id AND uid = :uid
                              AND lifecycle = 'active'
                            RETURNING id
                            """
                        ),
                        {"cluster_id": principal.cluster_id, "uid": resource.uid},
                    )
                ).first()
                if changed is not None:
                    await _append_change(
                        connection,
                        principal.cluster_id,
                        resource.kind,
                        resource.namespace,
                        resource.name,
                        resource.uid,
                        "missing",
                    )
                continue
            payload = json.loads(_payload_column(resource))
            health, reasons = derive_health(resource.kind, payload)
            existing = (
                await connection.execute(
                    text(
                        "SELECT lifecycle FROM inventory_resources "
                        "WHERE cluster_id = :cluster_id AND uid = :uid"
                    ),
                    {"cluster_id": principal.cluster_id, "uid": resource.uid},
                )
            ).first()
            if existing is None:
                change_type = "added"
            elif str(existing[0]) == "missing":
                change_type = "restored"
            else:
                change_type = "updated"
            await connection.execute(
                text(
                    """
                    INSERT INTO inventory_resources
                        (cluster_id, api_group, api_version, kind, namespace, name,
                         uid, resource_version, payload, health, health_reasons,
                         lifecycle, last_seen_at, observed_at)
                    VALUES
                        (:cluster_id, :api_group, :api_version, :kind, :namespace,
                         :name, :uid, :resource_version, CAST(:payload AS jsonb),
                         :health, CAST(:reasons AS jsonb), 'active', now(),
                         :observed_at)
                    ON CONFLICT (cluster_id, uid) DO UPDATE SET
                        resource_version = EXCLUDED.resource_version,
                        payload = EXCLUDED.payload,
                        health = EXCLUDED.health,
                        health_reasons = EXCLUDED.health_reasons,
                        lifecycle = 'active',
                        last_seen_at = now(),
                        observed_at = EXCLUDED.observed_at
                    """
                ),
                {
                    "cluster_id": principal.cluster_id,
                    "api_group": resource.api_group,
                    "api_version": resource.api_version,
                    "kind": resource.kind,
                    "namespace": resource.namespace,
                    "name": resource.name,
                    "uid": resource.uid,
                    "resource_version": resource.resource_version,
                    "payload": json.dumps(payload),
                    "health": health,
                    "reasons": json.dumps(reasons),
                    "observed_at": resource.observed_at,
                },
            )
            await _append_change(
                connection,
                principal.cluster_id,
                resource.kind,
                resource.namespace,
                resource.name,
                resource.uid,
                change_type,
            )
    return {"api_version": _API_VERSION, "kind": "ack", "result": "applied"}
