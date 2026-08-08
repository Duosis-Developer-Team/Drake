"""Protection Center: evidence, evaluation, ingest safety and scope.

The product rule this suite defends, stated once:

    backup job success  ≠  artifact exists
    artifact exists     ≠  artifact is valid
    valid artifact      ≠  offsite protection
    offsite backup      ≠  verified recoverability

Each of the scenarios below is one place that chain could be short-cut
into a comforting green tick.
"""

import hashlib
import hmac
import json
import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from drake_api.protection import ingest
from drake_api.protection.connectors import load_contract, seed_policies
from drake_api.protection.metrics import metric_rows, render_protection_metrics
from drake_api.protection.model import (
    ArtifactEvidence,
    BackupState,
    DrillEvidence,
    OverallState,
    PolicyPromise,
    ProtectionEvidence,
    RecoverabilityState,
    RunEvidence,
    evaluate_protection,
)
from drake_api.protection.service import evaluate_policy
from drake_api.settings import ProtectionConnector
from harness_s1 import S1Harness, build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
DAY = 86_400
WEEK = 604_800


# ===========================================================================
# the evaluation rules (pure)
# ===========================================================================


def promise(**overrides: Any) -> PolicyPromise:
    base: dict[str, Any] = {
        "rpo_seconds": WEEK,
        "rto_seconds": 4 * 3600,
        "requires_offsite": True,
        "requires_integrity_check": True,
        "restore_verification_ttl_seconds": 90 * DAY,
    }
    base.update(overrides)
    return PolicyPromise(**base)


def evidence(**overrides: Any) -> ProtectionEvidence:
    base: dict[str, Any] = {
        "last_success_at": NOW - timedelta(hours=6),
        "last_attempt": RunEvidence(status="succeeded", started_at=NOW - timedelta(hours=6)),
        "artifact": ArtifactEvidence(
            exists=True,
            checksum="a" * 64,
            integrity_result="passed",
            offsite_present=True,
        ),
        "drill": DrillEvidence(),
        "reporter_seen_at": NOW - timedelta(minutes=10),
    }
    base.update(overrides)
    return ProtectionEvidence(**base)


def test_a_successful_run_with_no_artifact_is_not_protected() -> None:
    """The whole point of the sprint. A green job is not a backup."""
    verdict = evaluate_protection(
        promise(), evidence(artifact=ArtifactEvidence(exists=False)), now=NOW
    )
    assert verdict.backup_state is BackupState.AT_RISK
    assert "artifact_missing" in verdict.reasons
    assert verdict.overall_state is OverallState.AT_RISK


def test_a_fresh_verified_artifact_with_no_drill_is_protected_but_unverified() -> None:
    verdict = evaluate_protection(promise(), evidence(), now=NOW)
    assert verdict.backup_state is BackupState.PROTECTED
    assert verdict.recoverability_state is RecoverabilityState.UNVERIFIED
    assert verdict.overall_state is OverallState.PROTECTED_UNVERIFIED
    assert "restore_never_verified" in verdict.reasons


def test_a_recent_passing_drill_makes_it_recoverable_verified() -> None:
    verdict = evaluate_protection(
        promise(),
        evidence(
            drill=DrillEvidence(
                result="passed",
                completed_at=NOW - timedelta(days=2),
                duration_seconds=1800,
                rto_met=True,
            )
        ),
        now=NOW,
    )
    assert verdict.overall_state is OverallState.RECOVERABLE_VERIFIED
    assert verdict.reasons == []


def test_missing_integrity_or_offsite_blocks_protected() -> None:
    no_integrity = evaluate_protection(
        promise(),
        evidence(artifact=ArtifactEvidence(exists=True, offsite_present=True)),
        now=NOW,
    )
    assert no_integrity.backup_state is BackupState.AT_RISK
    assert "integrity_missing" in no_integrity.reasons

    no_offsite = evaluate_protection(
        promise(),
        evidence(
            artifact=ArtifactEvidence(exists=True, integrity_result="passed", offsite_present=False)
        ),
        now=NOW,
    )
    assert no_offsite.backup_state is BackupState.AT_RISK
    assert "offsite_missing" in no_offsite.reasons


def test_a_failed_integrity_check_counts_even_when_not_required() -> None:
    """It ran and it failed. Ignoring that because the policy did not
    demand it would be perverse."""
    verdict = evaluate_protection(
        promise(requires_integrity_check=False),
        evidence(
            artifact=ArtifactEvidence(exists=True, integrity_result="failed", offsite_present=True)
        ),
        now=NOW,
    )
    assert "integrity_failed" in verdict.reasons


def test_an_rpo_breach_is_overdue() -> None:
    verdict = evaluate_protection(
        promise(rpo_seconds=DAY),
        evidence(last_success_at=NOW - timedelta(days=3)),
        now=NOW,
    )
    assert verdict.backup_state is BackupState.OVERDUE
    assert verdict.overall_state is OverallState.OVERDUE
    assert "backup_overdue" in verdict.reasons


def test_a_failed_latest_attempt_outranks_freshness() -> None:
    verdict = evaluate_protection(
        promise(),
        evidence(last_attempt=RunEvidence(status="failed", error_code="pg_dump_failed")),
        now=NOW,
    )
    assert verdict.backup_state is BackupState.FAILED
    assert verdict.overall_state is OverallState.FAILED


def test_a_failed_drill_beats_a_green_backup() -> None:
    """A new successful backup does not un-fail a restore that did not work."""
    verdict = evaluate_protection(
        promise(),
        evidence(drill=DrillEvidence(result="failed", completed_at=NOW - timedelta(hours=1))),
        now=NOW,
    )
    assert verdict.backup_state is BackupState.PROTECTED
    assert verdict.recoverability_state is RecoverabilityState.FAILED
    assert verdict.overall_state is OverallState.FAILED


def test_an_expired_or_slow_drill_is_not_verification() -> None:
    expired = evaluate_protection(
        promise(),
        evidence(
            drill=DrillEvidence(
                result="passed", completed_at=NOW - timedelta(days=200), rto_met=True
            )
        ),
        now=NOW,
    )
    assert expired.recoverability_state is RecoverabilityState.UNVERIFIED
    assert "restore_verification_expired" in expired.reasons

    slow = evaluate_protection(
        promise(rto_seconds=600),
        evidence(
            drill=DrillEvidence(
                result="passed",
                completed_at=NOW - timedelta(days=1),
                duration_seconds=9000,
                rto_met=False,
            )
        ),
        now=NOW,
    )
    assert slow.recoverability_state is RecoverabilityState.UNVERIFIED
    assert "rto_exceeded" in slow.reasons


def test_a_stale_reporter_makes_everything_unknown() -> None:
    """Without a live reporter, "protected" would mean "protected as far as
    we knew last week"."""
    verdict = evaluate_protection(
        promise(), evidence(reporter_seen_at=NOW - timedelta(days=5)), now=NOW
    )
    assert verdict.backup_state is BackupState.UNKNOWN
    assert verdict.overall_state is OverallState.UNKNOWN
    assert verdict.reasons == ["reporter_stale"]


# ===========================================================================
# ingest: signing, idempotency, ordering
# ===========================================================================

CONNECTOR = "hermes-backup"


def signed(secret: bytes, body: bytes, timestamp: str) -> str:
    return "v1=" + hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def connector_settings(tmp_path: Any, **overrides: Any):
    secret_file = tmp_path / "connector.key"
    if not secret_file.exists():
        secret_file.write_bytes(uuidlib.uuid4().hex.encode())
    connector = ProtectionConnector(
        project_key="hermes", signing_secret_file=str(secret_file), **overrides
    )
    return require_it_settings().model_copy(
        update={"protection_connectors": {CONNECTOR: connector, "logislot-backup": connector}}
    ), secret_file.read_bytes()


async def seed_project(engine: AsyncEngine, project_key: str, environment_key: str) -> Any:
    """A catalog project the contract can attach policies to."""
    world = await make_world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET project_key = :k WHERE id = :id"),
            {"k": project_key, "id": world["project_id"]},
        )
        await connection.execute(
            text("UPDATE environments SET environment_key = :k WHERE id = :id"),
            {"k": environment_key, "id": world["environment_id"]},
        )
    return world


async def policy_id_for(engine: AsyncEngine, key: str) -> uuidlib.UUID:
    async with engine.connect() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM backup_policies WHERE policy_external_key = :k"),
                        {"k": key},
                    )
                ).scalar_one()
            )
        )


def event(event_type: str, data: dict[str, Any], *, at: datetime, event_id: str | None = None):
    return {
        "id": event_id or f"evt-{uuidlib.uuid4().hex[:12]}",
        "type": event_type,
        "time": at.isoformat(),
        "data": data,
    }


async def test_the_contract_seeds_hermes_policies_as_separate_stores(
    engine: AsyncEngine,
) -> None:
    """Core and auth are two stores, so evidence for one can never stand in
    for the other."""
    await seed_project(engine, "hermes", "dev")
    created = await seed_policies(engine, connector_key=CONNECTOR)
    assert created >= 2

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT policy_external_key, store_key, rpo_seconds, requires_offsite, "
                    "requires_integrity_check FROM backup_policies "
                    "WHERE connector_key = :c ORDER BY policy_external_key"
                ),
                {"c": CONNECTOR},
            )
        ).all()
    stores = {row[0]: row[1] for row in rows}
    assert stores["hermes-dev-core"] == "hermes-core"
    assert stores["hermes-dev-auth"] == "hermes-auth"
    assert stores["hermes-dev-core"] != stores["hermes-dev-auth"]
    # The weekly promise, and both requirements, come from the contract.
    assert all(row[2] == WEEK and row[3] and row[4] for row in rows)


async def test_a_run_and_artifact_flow_through_ingest(engine: AsyncEngine, tmp_path: Any) -> None:
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    at = datetime.now(UTC) - timedelta(hours=1)

    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.run.completed.v1",
            {
                "policy_key": "hermes-dev-core",
                "run_id": "run-1",
                "status": "succeeded",
                "started_at": at.isoformat(),
                "completed_at": (at + timedelta(minutes=4)).isoformat(),
            },
            at=at,
        ),
        now=datetime.now(UTC),
    )
    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.artifact.observed.v1",
            {
                "run_id": "run-1",
                "artifact_key": "artifact-core-1",
                "size_bytes": 12345,
                "checksum_algorithm": "sha256",
                "checksum": "b" * 64,
                "encrypted": True,
                "storage_provider_key": "onedrive",
                "storage_site_key": "onedrive-primary",
            },
            at=at,
        ),
        now=datetime.now(UTC),
    )

    async with engine.connect() as connection:
        artifact = (
            await connection.execute(
                text(
                    "SELECT size_bytes, checksum, presence, storage_site_key "
                    "FROM backup_artifacts WHERE artifact_external_key = 'artifact-core-1'"
                )
            )
        ).first()
    assert artifact is not None
    assert artifact[0] == 12345
    assert artifact[2] == "present"
    # A site KEY, not a URL or a path.
    assert artifact[3] == "onedrive-primary"


async def test_the_same_event_twice_changes_nothing(engine: AsyncEngine, tmp_path: Any) -> None:
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    at = datetime.now(UTC) - timedelta(hours=1)
    envelope = event(
        "drake.backup.run.completed.v1",
        {
            "policy_key": "hermes-dev-core",
            "run_id": "run-dup",
            "status": "succeeded",
            "started_at": at.isoformat(),
            "completed_at": at.isoformat(),
        },
        at=at,
        event_id="evt-fixed",
    )

    first = await ingest.apply_event(engine, CONNECTOR, envelope, now=datetime.now(UTC))
    second = await ingest.apply_event(engine, CONNECTOR, envelope, now=datetime.now(UTC))
    assert first.outcome == "applied"
    assert second.outcome == "duplicate"

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM backup_runs WHERE provider_run_id = 'run-dup'")
            )
        ).scalar_one()
    assert count == 1


async def test_an_out_of_order_event_does_not_drag_the_projection_backwards(
    engine: AsyncEngine,
) -> None:
    """A replayed older event is old news, not new news."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    newer = datetime.now(UTC) - timedelta(minutes=10)
    older = newer - timedelta(hours=2)

    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.run.completed.v1",
            {
                "policy_key": "hermes-dev-core",
                "run_id": "run-order",
                "status": "succeeded",
                "started_at": newer.isoformat(),
                "completed_at": newer.isoformat(),
            },
            at=newer,
        ),
        now=datetime.now(UTC),
    )
    stale = await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.run.completed.v1",
            {
                "policy_key": "hermes-dev-core",
                "run_id": "run-order",
                "status": "failed",
                "started_at": older.isoformat(),
                "completed_at": older.isoformat(),
            },
            at=older,
        ),
        now=datetime.now(UTC),
    )

    assert stale.outcome == "ignored_stale"
    async with engine.connect() as connection:
        status = (
            await connection.execute(
                text("SELECT status FROM backup_runs WHERE provider_run_id = 'run-order'")
            )
        ).scalar_one()
    assert status == "succeeded"


async def test_evidence_for_an_unknown_policy_is_refused(engine: AsyncEngine) -> None:
    """A reporter does not get to invent the bar it will be judged against."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    with pytest.raises(ingest.IngestRejectedError) as error:
        await ingest.apply_event(
            engine,
            CONNECTOR,
            event(
                "drake.backup.run.completed.v1",
                {
                    "policy_key": "something-invented",
                    "run_id": "run-x",
                    "status": "succeeded",
                    "started_at": datetime.now(UTC).isoformat(),
                },
                at=datetime.now(UTC),
            ),
            now=datetime.now(UTC),
        )
    assert error.value.code == "unknown_policy"


@contextmanager
def _labelled(label: str) -> Any:
    """Name the case in a failure, without an unused loop variable."""
    try:
        yield
    except AssertionError as error:  # pragma: no cover - only on failure
        raise AssertionError(f"{label}: {error}") from error


def test_a_signature_is_required_and_bounded_in_time(tmp_path: Any) -> None:
    settings, secret = connector_settings(tmp_path)
    connector = settings.protection_connectors[CONNECTOR]
    body = b'{"id":"evt-1"}'
    now = datetime.now(UTC)
    timestamp = str(int(now.timestamp()))

    # Correct signature passes.
    ingest.verify_signature(
        connector,
        secret=secret,
        timestamp=timestamp,
        signature=signed(secret, body, timestamp),
        body=body,
        now=now,
    )

    for label, kwargs in (
        ("no signature", {"signature": None}),
        ("wrong signature", {"signature": "v1=" + "0" * 64}),
        ("tampered body", {"body": b'{"id":"evt-2"}'}),
        ("no secret", {"secret": None}),
    ):
        with pytest.raises(ingest.IngestRejectedError), _labelled(label):
            ingest.verify_signature(
                connector,
                **{
                    "secret": secret,
                    "timestamp": timestamp,
                    "signature": signed(secret, body, timestamp),
                    "body": body,
                    "now": now,
                    **kwargs,
                },
            )

    # A captured request cannot be replayed once the window closes.
    old = str(int((now - timedelta(hours=1)).timestamp()))
    with pytest.raises(ingest.IngestRejectedError) as error:
        ingest.verify_signature(
            connector,
            secret=secret,
            timestamp=old,
            signature=signed(secret, body, old),
            body=body,
            now=now,
        )
    assert error.value.code == "timestamp_outside_window"


async def test_unknown_event_types_and_untyped_fields_are_refused(
    engine: AsyncEngine,
) -> None:
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    with pytest.raises(ingest.IngestRejectedError):
        await ingest.apply_event(
            engine,
            CONNECTOR,
            event("drake.backup.something.new.v1", {}, at=datetime.now(UTC)),
            now=datetime.now(UTC),
        )


async def test_a_restore_drill_keeps_only_typed_validations(engine: AsyncEngine) -> None:
    """Row samples, SQL and command output have no field to arrive in."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    at = datetime.now(UTC) - timedelta(hours=1)
    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.restore.drill.completed.v1",
            {
                "policy_key": "hermes-dev-core",
                "drill_id": "drill-1",
                "target_profile": "ephemeral",
                "result": "passed",
                "started_at": at.isoformat(),
                "completed_at": (at + timedelta(minutes=20)).isoformat(),
                "rto_met": True,
                "validations": {
                    "schema_present": True,
                    "row_counts_sane": True,
                    "sample_row": {"email": "customer@example.test"},
                    "command_output": "psql: restored 12 tables",
                },
            },
            at=at,
        ),
        now=datetime.now(UTC),
    )
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT validations FROM restore_drills WHERE drill_external_id='drill-1'")
            )
        ).scalar_one()
    assert stored == {"schema_present": True, "row_counts_sane": True}
    serialized = json.dumps(stored)
    assert "customer@example.test" not in serialized
    assert "psql" not in serialized


async def test_reconciliation_only_becomes_current_when_completed(
    engine: AsyncEngine,
) -> None:
    """An interrupted reconciliation leaves the last good projection alone."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    at = datetime.now(UTC) - timedelta(hours=1)

    snapshot_id = await ingest.begin_snapshot(engine, CONNECTOR)
    await ingest.snapshot_page(
        engine,
        CONNECTOR,
        snapshot_id,
        [
            event(
                "drake.backup.run.completed.v1",
                {
                    "policy_key": "hermes-dev-core",
                    "run_id": "run-recon",
                    "status": "succeeded",
                    "started_at": at.isoformat(),
                    "completed_at": at.isoformat(),
                },
                at=at,
                event_id="evt-recon-1",
            )
        ],
        now=datetime.now(UTC),
    )

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT state FROM protection_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )
        ).scalar_one()
    assert state == "open"
    assert await ingest.complete_snapshot(engine, snapshot_id) is True
    # Completing twice is not a second reconciliation.
    assert await ingest.complete_snapshot(engine, snapshot_id) is False


async def test_reconciliation_does_not_duplicate_streamed_evidence(
    engine: AsyncEngine,
) -> None:
    """Reconciliation fills gaps; it does not re-report what already arrived."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    at = datetime.now(UTC) - timedelta(hours=1)
    envelope = event(
        "drake.backup.run.completed.v1",
        {
            "policy_key": "hermes-dev-core",
            "run_id": "run-shared",
            "status": "succeeded",
            "started_at": at.isoformat(),
            "completed_at": at.isoformat(),
        },
        at=at,
        event_id="evt-shared",
    )
    await ingest.apply_event(engine, CONNECTOR, envelope, now=datetime.now(UTC))

    snapshot_id = await ingest.begin_snapshot(engine, CONNECTOR)
    await ingest.snapshot_page(engine, CONNECTOR, snapshot_id, [envelope], now=datetime.now(UTC))
    await ingest.complete_snapshot(engine, snapshot_id)

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM backup_runs WHERE provider_run_id = 'run-shared'")
            )
        ).scalar_one()
    assert count == 1


# ===========================================================================
# end-to-end evaluation and incidents
# ===========================================================================


async def build_evidence_chain(
    engine: AsyncEngine,
    policy_key: str,
    *,
    with_artifact: bool = True,
    with_integrity: bool = True,
    with_offsite: bool = True,
    with_drill: str | None = None,
    age: timedelta = timedelta(hours=2),
    run_status: str = "succeeded",
) -> None:
    """Feed one policy's chain through the real ingest path."""
    at = datetime.now(UTC) - age
    suffix = uuidlib.uuid4().hex[:8]
    run_id = f"run-{suffix}"
    artifact_key = f"artifact-{suffix}"

    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.run.completed.v1",
            {
                "policy_key": policy_key,
                "run_id": run_id,
                "status": run_status,
                "started_at": at.isoformat(),
                "completed_at": at.isoformat(),
            },
            at=at,
        ),
        now=datetime.now(UTC),
    )
    if not with_artifact:
        return
    await ingest.apply_event(
        engine,
        CONNECTOR,
        event(
            "drake.backup.artifact.observed.v1",
            {
                "run_id": run_id,
                "artifact_key": artifact_key,
                "size_bytes": 4096,
                "checksum_algorithm": "sha256",
                "checksum": "c" * 64,
                "encrypted": True,
            },
            at=at,
        ),
        now=datetime.now(UTC),
    )
    if with_integrity:
        await ingest.apply_event(
            engine,
            CONNECTOR,
            event(
                "drake.backup.integrity.completed.v1",
                {
                    "artifact_key": artifact_key,
                    "check_key": f"check-{suffix}",
                    "method": "checksum",
                    "result": "passed",
                    "checked_at": at.isoformat(),
                },
                at=at,
            ),
            now=datetime.now(UTC),
        )
    if with_offsite:
        await ingest.apply_event(
            engine,
            CONNECTOR,
            event(
                "drake.backup.copy.observed.v1",
                {
                    "artifact_key": artifact_key,
                    "site_key": "onedrive-primary",
                    "provider_key": "onedrive",
                    "state": "present",
                    "is_offsite": True,
                },
                at=at,
            ),
            now=datetime.now(UTC),
        )
    if with_drill is not None:
        await ingest.apply_event(
            engine,
            CONNECTOR,
            event(
                "drake.restore.drill.completed.v1",
                {
                    "policy_key": policy_key,
                    "artifact_key": artifact_key,
                    "drill_id": f"drill-{suffix}",
                    "target_profile": "ephemeral",
                    "result": with_drill,
                    "started_at": at.isoformat(),
                    "completed_at": (at + timedelta(minutes=15)).isoformat(),
                    "rto_met": True,
                    "validations": {"schema_present": True, "application_smoke": True},
                },
                at=at,
            ),
            now=datetime.now(UTC),
        )


async def test_the_full_chain_reaches_recoverable_verified(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    """policy → run → artifact → integrity → offsite → drill → verified."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    await build_evidence_chain(engine, "hermes-dev-core", with_drill="passed")

    outcome = await evaluate_policy(
        engine, settings, await policy_id_for(engine, "hermes-dev-core")
    )
    assert outcome.verdict.overall_state is OverallState.RECOVERABLE_VERIFIED


async def test_a_run_without_an_artifact_is_not_protected_end_to_end(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    await build_evidence_chain(engine, "hermes-dev-core", with_artifact=False)

    outcome = await evaluate_policy(
        engine, settings, await policy_id_for(engine, "hermes-dev-core")
    )
    assert outcome.verdict.overall_state is OverallState.AT_RISK
    assert "artifact_missing" in outcome.verdict.reasons


async def test_hermes_core_evidence_does_not_protect_hermes_auth(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    """Two stores, two chains. One backup does not cover both databases."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    await build_evidence_chain(engine, "hermes-dev-core", with_drill="passed")

    core = await evaluate_policy(engine, settings, await policy_id_for(engine, "hermes-dev-core"))
    auth = await evaluate_policy(engine, settings, await policy_id_for(engine, "hermes-dev-auth"))
    assert core.verdict.overall_state is OverallState.RECOVERABLE_VERIFIED
    assert auth.verdict.overall_state is OverallState.OVERDUE
    assert "backup_overdue" in auth.verdict.reasons


async def test_a_failed_drill_keeps_logislot_unrecoverable_despite_a_good_backup(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    """Backup success and restore result are independent signals."""
    await seed_project(engine, "logislot", "prod")
    await seed_policies(engine, connector_key="logislot-backup")
    settings, _ = connector_settings(tmp_path)

    global CONNECTOR
    previous, CONNECTOR = CONNECTOR, "logislot-backup"
    try:
        await build_evidence_chain(
            engine, "logislot-prod-db", with_drill="failed", age=timedelta(hours=2)
        )
        outcome = await evaluate_policy(
            engine, settings, await policy_id_for(engine, "logislot-prod-db")
        )
    finally:
        CONNECTOR = previous

    assert outcome.verdict.backup_state is BackupState.PROTECTED
    assert outcome.verdict.recoverability_state is RecoverabilityState.FAILED
    assert outcome.verdict.overall_state is OverallState.FAILED


async def test_an_overdue_policy_opens_one_incident_and_resolves_it(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    """One incident per active problem, resolved through the Sprint 6
    lifecycle when it clears."""
    world = await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    policy_id = await policy_id_for(engine, "hermes-dev-core")
    await build_evidence_chain(engine, "hermes-dev-core", age=timedelta(days=30))

    first = await evaluate_policy(engine, settings, policy_id)
    second = await evaluate_policy(engine, settings, policy_id)
    third = await evaluate_policy(engine, settings, policy_id)

    assert first.verdict.overall_state is OverallState.OVERDUE
    assert first.incident_opened is not None
    # Still broken is not news: no second incident.
    assert second.incident_opened is None
    assert third.incident_opened is None

    async with engine.connect() as connection:
        open_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM incidents WHERE binding_id = :b "
                    "AND state IN ('open','acknowledged')"
                ),
                {"b": world["binding_id"]},
            )
        ).scalar_one()
    assert open_count == 1

    # A fresh, complete chain clears it.
    await build_evidence_chain(engine, "hermes-dev-core", with_drill="passed")
    recovered = await evaluate_policy(engine, settings, policy_id)
    assert recovered.incident_resolved is not None

    async with engine.connect() as connection:
        events = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT event_type FROM incident_events "
                        "WHERE incident_id = :i ORDER BY occurred_at"
                    ),
                    {"i": first.incident_opened},
                )
            ).all()
        ]
    assert events == ["opened", "auto_resolved"]


# ===========================================================================
# API scope and metrics
# ===========================================================================


@asynccontextmanager
async def owner(harness: S1Harness, engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        yield client


def protection_harness(tmp_path: Any) -> S1Harness:
    settings, _ = connector_settings(tmp_path)
    harness = build_harness(settings)
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-env", "user-b-only"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness


async def test_the_api_reports_the_chain_without_provider_material(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    await build_evidence_chain(engine, "hermes-dev-core", with_drill="passed")
    policy_id = await policy_id_for(engine, "hermes-dev-core")
    await evaluate_policy(engine, settings, policy_id)
    harness = protection_harness(tmp_path)

    async with owner(harness, engine) as client:
        summary = await client.get("/v1/protection/summary")
        listed = await client.get("/v1/protection/policies")
        detail = await client.get(f"/v1/protection/policies/{policy_id}")
        runs = await client.get(f"/v1/protection/policies/{policy_id}/runs")
        drills = await client.get(f"/v1/protection/policies/{policy_id}/drills")

    assert summary.json()["overall"]["recoverable_verified"] >= 1
    body = listed.json()
    assert body["total"] >= 1
    entry = next(item for item in body["items"] if item["store_key"] == "hermes-core")
    assert entry["evaluation"]["overall_state"] == "recoverable_verified"
    assert detail.status_code == 200
    assert runs.json()["runs"][0]["status"] == "succeeded"
    assert drills.json()["drills"][0]["result"] == "passed"

    serialized = summary.text + listed.text + detail.text + runs.text + drills.text
    for forbidden in (
        "https://",
        "sas",
        "token",
        "password",
        "credential",
        "pg_dump -",
        "select ",
        "traceback",
        ".sql",
        ".dump",
    ):
        assert forbidden not in serialized.lower(), forbidden


async def test_a_caller_outside_scope_sees_nothing_anywhere(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    from test_catalog_api_integration import grant, make_role, seed_catalog_world

    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    settings, _ = connector_settings(tmp_path)
    await build_evidence_chain(engine, "hermes-dev-core")
    policy_id = await policy_id_for(engine, "hermes-dev-core")
    await evaluate_policy(engine, settings, policy_id)
    await seed_catalog_world(engine)
    harness = protection_harness(tmp_path)
    await make_role(harness, engine, "Beta Protection", ["protection.view", "environment.view"])

    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Beta Protection", "project", "beta")
        listed = (await outsider.get("/v1/protection/policies")).json()
        summary = (await outsider.get("/v1/protection/summary")).json()
        hidden = await outsider.get(f"/v1/protection/policies/{policy_id}")
        missing = await outsider.get(f"/v1/protection/policies/{uuidlib.uuid4()}")

    # Absent from the list, the total AND the summary counts.
    assert listed["items"] == []
    assert listed["total"] == 0
    assert summary["total_policies"] == 0
    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_protection_view_alone_is_not_enough(engine: AsyncEngine, tmp_path: Any) -> None:
    """Both rights are required: knowing a system is unprotected is
    sensitive, and so is knowing it exists."""
    await seed_project(engine, "hermes", "dev")
    await seed_policies(engine, connector_key=CONNECTOR)
    harness = protection_harness(tmp_path)
    await make_role_only(harness, engine, "Protection Only", ["protection.view"])

    async with harness.api_client() as client:
        await harness.login(client, "user-env")
        await grant_root(engine, harness, "user-env", "Protection Only")
        listed = (await client.get("/v1/protection/policies")).json()
    assert listed["items"] == []


async def make_role_only(
    harness: S1Harness, engine: AsyncEngine, name: str, permissions: list[str]
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO roles (name, description, is_system) VALUES (:n, 'test', true) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": name},
        )
        for permission in permissions:
            await connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_key) "
                    "SELECT r.id, :p FROM roles r WHERE r.name = :n "
                    "ON CONFLICT DO NOTHING"
                ),
                {"n": name, "p": permission},
            )


async def grant_root(engine: AsyncEngine, harness: S1Harness, subject: str, role: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT i.id, r.id, s.id FROM identities i, roles r, scopes s
                WHERE i.issuer = :issuer AND i.subject = :subject AND r.name = :role
                  AND s.scope_type = 'organization' AND s.external_ref = 'root'
                """
            ),
            {"issuer": harness.provider.issuer, "subject": subject, "role": role},
        )


def test_metrics_labels_are_controlled_and_absent_values_emit_nothing() -> None:
    """An artifact id as a label is a slow leak; a zero timestamp reads as
    1970."""
    rendered = render_protection_metrics(
        metric_rows(
            [
                (
                    "hermes",
                    "dev",
                    "hermes-core",
                    "hermes-dev-core",
                    1_754_000_000,
                    1_754_000_000,
                    240,
                    4096,
                    0,
                    None,
                )
            ]
        )
    )
    import re

    assert 'project="hermes"' in rendered
    assert 'store="hermes-core"' in rendered
    assert "drake_backup_last_success_timestamp_seconds{" in rendered
    # No restore has ever succeeded, so no sample is emitted at all — a
    # zero here would render as 1970 and read as "restored 55 years ago".
    assert "drake_restore_last_success_timestamp_seconds{" not in rendered

    # The label NAMES are the fixed set, whatever the metric is called. An
    # artifact id or checksum as a label is unbounded cardinality and a
    # slow leak of the thing being measured.
    label_names = set(re.findall(r'[{,]([a-z_]+)="', rendered))
    assert label_names == {"project", "environment", "store", "policy"}


def test_the_connector_contract_declares_no_url_or_credential() -> None:
    """The contract is reviewed configuration, not a place for a secret.

    Checked structurally rather than by substring: the file's own notes
    mention tokens precisely to say it holds none, and a naive grep would
    fail on the documentation that makes the guarantee explicit.
    """
    forbidden_keys = {"url", "endpoint", "token", "password", "secret", "credential", "sas"}

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.lower() not in forbidden_keys, f"{path}.{key}"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            # Notes may DISCUSS credentials; no value may BE a location.
            assert "://" not in node, path

    walk(load_contract())
