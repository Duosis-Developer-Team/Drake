"""Accepting protection evidence from a connector.

Every event is signed, bounded, typed, and idempotent. The parts that
matter:

**The connector decides the scope, not the payload.** A request
authenticates as a connector key; that key is registered against exactly
one project. An event cannot name a project it was not registered for, so
a compromised reporter for one system cannot rewrite another's protection
history.

**Provider time and Drake time stay separate.** `source_event_at` is when
the provider says it happened; `ingested_at` is when we heard. An event
that arrives late is late, not old — and an event that arrives out of
order never drags a newer projection backwards.

**Absence is not deletion.** An artifact the provider stops mentioning
becomes `missing`, never removed. A reporter outage is not proof that a
backup is gone, and retention expiring is not proof that a file was
deleted.
"""

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.settings import ProtectionConnector, Settings

logger = logging.getLogger("drake_api.protection.ingest")

SIGNATURE_HEADER = "X-Drake-Protection-Signature"
TIMESTAMP_HEADER = "X-Drake-Protection-Timestamp"
CONNECTOR_HEADER = "X-Drake-Protection-Connector"
SIGNATURE_VERSION = "v1"

# The versioned envelope. A payload naming anything outside this set is
# refused rather than partially applied — a connector that has learned a
# new trick needs a reviewed schema, not a permissive parser.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "drake.backup.run.started.v1",
        "drake.backup.run.completed.v1",
        "drake.backup.artifact.observed.v1",
        "drake.backup.integrity.completed.v1",
        "drake.backup.copy.observed.v1",
        "drake.restore.drill.completed.v1",
    }
)

RUN_STATUSES = frozenset({"started", "succeeded", "failed", "cancelled", "unknown"})
INTEGRITY_METHODS = frozenset({"checksum", "restore_probe", "archive_verify", "other"})
INTEGRITY_RESULTS = frozenset({"passed", "failed", "skipped"})
COPY_STATES = frozenset({"present", "pending", "missing", "failed"})
DRILL_RESULTS = frozenset({"passed", "failed", "partial", "unknown"})
# Typed pass/fail checks only. There is no key here for a row sample, a
# query, or command output, so none can arrive.
VALIDATION_KEYS = frozenset(
    {"schema_present", "row_counts_sane", "migrations_applied", "application_smoke"}
)

MAX_KEY_LENGTH = 200


class IngestRejectedError(ValueError):
    """A bounded, safe rejection code (never a provider message)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class IngestResult:
    outcome: str
    code: str | None = None


def load_signing_secret(connector: ProtectionConnector) -> bytes | None:
    """Read the connector's signing secret from its file reference."""
    if not connector.signing_secret_file:
        return None
    try:
        material = Path(connector.signing_secret_file).read_bytes().strip()
    except OSError:
        logger.warning("protection connector signing secret reference could not be read")
        return None
    return material or None


def verify_signature(
    connector: ProtectionConnector,
    *,
    secret: bytes | None,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: datetime,
) -> None:
    """Signature over `timestamp.body`, inside a bounded replay window.

    The timestamp is inside the signed material AND checked against the
    clock: signing the body alone would let a captured request be replayed
    forever, and checking the clock without signing it would let anyone
    change it.
    """
    if secret is None:
        # A connector with no configured secret cannot be authenticated, so
        # its evidence is refused rather than trusted.
        raise IngestRejectedError("connector_unsigned")
    if not timestamp or not signature:
        raise IngestRejectedError("signature_missing")
    try:
        sent_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError) as error:
        raise IngestRejectedError("timestamp_malformed") from error
    if abs((now - sent_at).total_seconds()) > max(1, connector.replay_window_seconds):
        raise IngestRejectedError("timestamp_outside_window")

    expected = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    # Constant time: a fast reject leaks the prefix that matched.
    if not hmac.compare_digest(signature, f"{SIGNATURE_VERSION}={expected}"):
        raise IngestRejectedError("signature_invalid")


def _require(value: Any, code: str) -> Any:
    if value in (None, ""):
        raise IngestRejectedError(code)
    return value


def _key(value: Any, code: str) -> str:
    text_value = str(_require(value, code))
    if len(text_value) > MAX_KEY_LENGTH:
        raise IngestRejectedError(code)
    return text_value


def _timestamp(value: Any, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(_require(value, code)))
    except (TypeError, ValueError) as error:
        raise IngestRejectedError(code) from error
    # Naive timestamps are ambiguous, and an ambiguous backup time is worse
    # than a missing one.
    if parsed.tzinfo is None:
        raise IngestRejectedError(code)
    return parsed.astimezone(UTC)


def _optional_timestamp(value: Any, code: str) -> datetime | None:
    return None if value in (None, "") else _timestamp(value, code)


def _enum(value: Any, allowed: frozenset[str], code: str) -> str:
    text_value = str(_require(value, code))
    if text_value not in allowed:
        raise IngestRejectedError(code)
    return text_value


async def _resolve_policy(
    connection: AsyncConnection, connector_key: str, policy_key: str
) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                """
                SELECT id FROM backup_policies
                WHERE connector_key = :connector AND policy_external_key = :policy
                """
            ),
            {"connector": connector_key, "policy": policy_key},
        )
    ).first()
    if row is None:
        # Evidence for a policy Drake does not know about is refused, not
        # auto-created: a policy carries promises (RPO, offsite) that a
        # reporter does not get to invent.
        raise IngestRejectedError("unknown_policy")
    return uuid.UUID(str(row[0]))


async def _apply_run(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any], event_at: datetime
) -> str:
    policy_key = _key(data.get("policy_key"), "unknown_policy")
    policy_id = await _resolve_policy(connection, connector_key, policy_key)
    status = _enum(data.get("status"), RUN_STATUSES, "invalid_status")
    started_at = _timestamp(data.get("started_at"), "invalid_started_at")
    completed_at = _optional_timestamp(data.get("completed_at"), "invalid_completed_at")
    duration = (
        int((completed_at - started_at).total_seconds())
        if completed_at is not None
        else None
    )
    result = await connection.execute(
        text(
            """
            INSERT INTO backup_runs
                (policy_id, connector_key, provider_run_id, status, started_at, completed_at,
                 duration_seconds, attempt, error_code, source_event_at)
            VALUES (:policy, :connector, :run_id, :status, :started, :completed,
                    :duration, :attempt, :error, :event_at)
            ON CONFLICT (connector_key, provider_run_id) DO UPDATE
            SET status = EXCLUDED.status,
                completed_at = EXCLUDED.completed_at,
                duration_seconds = EXCLUDED.duration_seconds,
                attempt = EXCLUDED.attempt,
                error_code = EXCLUDED.error_code,
                source_event_at = EXCLUDED.source_event_at,
                updated_at = now()
            -- Out-of-order protection: a replayed older event never drags
            -- the current projection backwards.
            WHERE backup_runs.source_event_at <= EXCLUDED.source_event_at
            RETURNING id
            """
        ),
        {
            "policy": policy_id,
            "connector": connector_key,
            "run_id": _key(data.get("run_id"), "missing_run_id"),
            "status": status,
            "started": started_at,
            "completed": completed_at,
            "duration": duration,
            "attempt": int(data.get("attempt") or 1),
            "error": _error_code(data.get("error_code")),
            "event_at": event_at,
        },
    )
    return "applied" if result.first() is not None else "ignored_stale"


def _error_code(value: Any) -> str | None:
    """Only a bounded classification survives. A provider message is where
    a connection string ends up."""
    if value in (None, ""):
        return None
    candidate = str(value).strip().lower().replace("-", "_")
    if not candidate.replace("_", "").isalnum() or len(candidate) > 64:
        return "provider_error"
    return candidate


async def _apply_artifact(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any], event_at: datetime
) -> str:
    run = (
        await connection.execute(
            text(
                "SELECT id, policy_id FROM backup_runs "
                "WHERE connector_key = :connector AND provider_run_id = :run_id"
            ),
            {"connector": connector_key, "run_id": _key(data.get("run_id"), "missing_run_id")},
        )
    ).first()
    if run is None:
        raise IngestRejectedError("unknown_run")

    checksum = data.get("checksum")
    result = await connection.execute(
        text(
            """
            INSERT INTO backup_artifacts
                (run_id, policy_id, artifact_external_key, size_bytes, checksum_algorithm,
                 checksum, encrypted, storage_provider_key, storage_site_key,
                 created_at_source, expires_at, presence, last_seen_at, source_event_at)
            VALUES (:run, :policy, :key, :size, :algorithm, :checksum, :encrypted,
                    :provider, :site, :created, :expires, :presence, :seen, :event_at)
            ON CONFLICT (run_id, artifact_external_key) DO UPDATE
            SET size_bytes = EXCLUDED.size_bytes,
                checksum_algorithm = EXCLUDED.checksum_algorithm,
                checksum = EXCLUDED.checksum,
                encrypted = EXCLUDED.encrypted,
                storage_provider_key = EXCLUDED.storage_provider_key,
                storage_site_key = EXCLUDED.storage_site_key,
                expires_at = EXCLUDED.expires_at,
                presence = EXCLUDED.presence,
                last_seen_at = EXCLUDED.last_seen_at,
                source_event_at = EXCLUDED.source_event_at,
                updated_at = now()
            WHERE backup_artifacts.source_event_at <= EXCLUDED.source_event_at
            RETURNING id
            """
        ),
        {
            "run": run[0],
            "policy": run[1],
            "key": _key(data.get("artifact_key"), "missing_artifact_key"),
            "size": _optional_int(data.get("size_bytes")),
            "algorithm": data.get("checksum_algorithm") or None,
            "checksum": str(checksum).lower() if checksum else None,
            "encrypted": data.get("encrypted"),
            "provider": data.get("storage_provider_key") or None,
            "site": data.get("storage_site_key") or None,
            "created": _optional_timestamp(data.get("created_at"), "invalid_created_at"),
            "expires": _optional_timestamp(data.get("expires_at"), "invalid_expires_at"),
            "presence": _enum(
                data.get("presence") or "present",
                frozenset({"present", "missing", "expired"}),
                "invalid_presence",
            ),
            "seen": event_at,
            "event_at": event_at,
        },
    )
    return "applied" if result.first() is not None else "ignored_stale"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError) as error:
        raise IngestRejectedError("invalid_size") from error


async def _artifact_id(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any]
) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                """
                SELECT a.id FROM backup_artifacts a
                JOIN backup_runs r ON r.id = a.run_id
                WHERE r.connector_key = :connector
                  AND a.artifact_external_key = :key
                ORDER BY a.source_event_at DESC
                LIMIT 1
                """
            ),
            {
                "connector": connector_key,
                "key": _key(data.get("artifact_key"), "missing_artifact_key"),
            },
        )
    ).first()
    if row is None:
        raise IngestRejectedError("unknown_artifact")
    return uuid.UUID(str(row[0]))


async def _apply_integrity(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any], event_at: datetime
) -> str:
    artifact_id = await _artifact_id(connection, connector_key, data)
    result = await connection.execute(
        text(
            """
            INSERT INTO integrity_checks
                (artifact_id, check_external_key, method, result, error_code,
                 checked_at, source_event_at)
            VALUES (:artifact, :key, :method, :result, :error, :checked, :event_at)
            ON CONFLICT (artifact_id, check_external_key) DO UPDATE
            SET result = EXCLUDED.result,
                error_code = EXCLUDED.error_code,
                checked_at = EXCLUDED.checked_at,
                source_event_at = EXCLUDED.source_event_at,
                updated_at = now()
            WHERE integrity_checks.source_event_at <= EXCLUDED.source_event_at
            RETURNING id
            """
        ),
        {
            "artifact": artifact_id,
            "key": _key(data.get("check_key"), "missing_check_key"),
            "method": _enum(data.get("method"), INTEGRITY_METHODS, "invalid_method"),
            "result": _enum(data.get("result"), INTEGRITY_RESULTS, "invalid_result"),
            "error": _error_code(data.get("error_code")),
            "checked": _timestamp(data.get("checked_at"), "invalid_checked_at"),
            "event_at": event_at,
        },
    )
    return "applied" if result.first() is not None else "ignored_stale"


async def _apply_copy(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any], event_at: datetime
) -> str:
    artifact_id = await _artifact_id(connection, connector_key, data)
    result = await connection.execute(
        text(
            """
            INSERT INTO replication_copies
                (artifact_id, site_key, provider_key, state, is_offsite, size_bytes,
                 observed_at, source_event_at)
            VALUES (:artifact, :site, :provider, :state, :offsite, :size, :observed, :event_at)
            ON CONFLICT (artifact_id, site_key) DO UPDATE
            SET state = EXCLUDED.state,
                is_offsite = EXCLUDED.is_offsite,
                size_bytes = EXCLUDED.size_bytes,
                observed_at = EXCLUDED.observed_at,
                source_event_at = EXCLUDED.source_event_at,
                updated_at = now()
            WHERE replication_copies.source_event_at <= EXCLUDED.source_event_at
            RETURNING id
            """
        ),
        {
            "artifact": artifact_id,
            "site": _key(data.get("site_key"), "missing_site_key"),
            "provider": data.get("provider_key") or None,
            "state": _enum(data.get("state"), COPY_STATES, "invalid_state"),
            "offsite": bool(data.get("is_offsite", False)),
            "size": _optional_int(data.get("size_bytes")),
            "observed": event_at,
            "event_at": event_at,
        },
    )
    return "applied" if result.first() is not None else "ignored_stale"


def _validations(value: Any) -> dict[str, bool]:
    """Typed pass/fail only. Anything outside the vocabulary is dropped."""
    if not isinstance(value, dict):
        return {}
    return {
        key: bool(entry)
        for key, entry in value.items()
        if key in VALIDATION_KEYS and isinstance(entry, bool)
    }


async def _apply_drill(
    connection: AsyncConnection, connector_key: str, data: dict[str, Any], event_at: datetime
) -> str:
    policy_id = await _resolve_policy(
        connection, connector_key, _key(data.get("policy_key"), "unknown_policy")
    )
    artifact_id: uuid.UUID | None = None
    if data.get("artifact_key"):
        artifact_id = await _artifact_id(connection, connector_key, data)

    started_at = _timestamp(data.get("started_at"), "invalid_started_at")
    completed_at = _optional_timestamp(data.get("completed_at"), "invalid_completed_at")
    duration = (
        int((completed_at - started_at).total_seconds()) if completed_at is not None else None
    )
    result = await connection.execute(
        text(
            """
            INSERT INTO restore_drills
                (policy_id, artifact_id, connector_key, drill_external_id, target_profile,
                 result, started_at, completed_at, duration_seconds, rto_met, validations,
                 error_code, source_event_at)
            VALUES (:policy, :artifact, :connector, :drill_id, :target, :result, :started,
                    :completed, :duration, :rto_met, CAST(:validations AS jsonb), :error,
                    :event_at)
            ON CONFLICT (connector_key, drill_external_id) DO UPDATE
            SET result = EXCLUDED.result,
                completed_at = EXCLUDED.completed_at,
                duration_seconds = EXCLUDED.duration_seconds,
                rto_met = EXCLUDED.rto_met,
                validations = EXCLUDED.validations,
                error_code = EXCLUDED.error_code,
                source_event_at = EXCLUDED.source_event_at,
                updated_at = now()
            WHERE restore_drills.source_event_at <= EXCLUDED.source_event_at
            RETURNING id
            """
        ),
        {
            "policy": policy_id,
            "artifact": artifact_id,
            "connector": connector_key,
            "drill_id": _key(data.get("drill_id"), "missing_drill_id"),
            "target": _key(data.get("target_profile"), "missing_target_profile"),
            "result": _enum(data.get("result"), DRILL_RESULTS, "invalid_result"),
            "started": started_at,
            "completed": completed_at,
            "duration": duration,
            "rto_met": data.get("rto_met"),
            "validations": json.dumps(_validations(data.get("validations"))),
            "error": _error_code(data.get("error_code")),
            "event_at": event_at,
        },
    )
    return "applied" if result.first() is not None else "ignored_stale"


_HANDLERS = {
    "drake.backup.run.started.v1": _apply_run,
    "drake.backup.run.completed.v1": _apply_run,
    "drake.backup.artifact.observed.v1": _apply_artifact,
    "drake.backup.integrity.completed.v1": _apply_integrity,
    "drake.backup.copy.observed.v1": _apply_copy,
    "drake.restore.drill.completed.v1": _apply_drill,
}


async def apply_event(
    engine: AsyncEngine, connector_key: str, envelope: dict[str, Any], *, now: datetime
) -> IngestResult:
    """Apply one signed, verified event. Idempotent on its own event id."""
    event_id = _key(envelope.get("id"), "missing_event_id")
    event_type = str(envelope.get("type") or "")
    if event_type not in EVENT_TYPES:
        await _record(engine, event_id, connector_key, event_type, now, "rejected", "unknown_type")
        raise IngestRejectedError("unknown_type")
    event_at = _timestamp(envelope.get("time"), "invalid_event_time")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise IngestRejectedError("invalid_payload")

    async with engine.begin() as connection:
        # The event id IS the idempotency key. A retried delivery lands on
        # the primary key and stops here, before touching any projection.
        claimed = (
            await connection.execute(
                text(
                    """
                    INSERT INTO protection_ingest_events
                        (event_id, connector_key, event_type, source_event_at, outcome)
                    VALUES (:id, :connector, :type, :at, 'applied')
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """
                ),
                {"id": event_id, "connector": connector_key, "type": event_type, "at": event_at},
            )
        ).first()
        if claimed is None:
            return IngestResult("duplicate")

        outcome = await _HANDLERS[event_type](connection, connector_key, data, event_at)
        if outcome != "applied":
            await connection.execute(
                text(
                    "UPDATE protection_ingest_events SET outcome = :outcome WHERE event_id = :id"
                ),
                {"outcome": outcome, "id": event_id},
            )
    return IngestResult(outcome)


async def _record(
    engine: AsyncEngine,
    event_id: str,
    connector_key: str,
    event_type: str,
    now: datetime,
    outcome: str,
    code: str | None,
) -> None:
    """Record a rejection so Integration Health can show it."""
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO protection_ingest_events
                        (event_id, connector_key, event_type, source_event_at, outcome,
                         error_code)
                    VALUES (:id, :connector, :type, :at, :outcome, :code)
                    ON CONFLICT (event_id) DO NOTHING
                    """
                ),
                {
                    "id": event_id,
                    "connector": connector_key,
                    "type": event_type or "unknown",
                    "at": now,
                    "outcome": outcome,
                    "code": code,
                },
            )
    except Exception:  # noqa: S110 - a failed audit write must not mask the rejection
        pass


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------


async def begin_snapshot(engine: AsyncEngine, connector_key: str) -> uuid.UUID:
    """Open a reconciliation snapshot.

    An open snapshot changes nothing. Only `complete` makes its contents
    the current picture, so an interrupted reconciliation leaves the last
    good projection standing rather than half-replacing it.
    """
    async with engine.begin() as connection:
        return uuid.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO protection_snapshots (connector_key, state) "
                            "VALUES (:connector, 'open') RETURNING id"
                        ),
                        {"connector": connector_key},
                    )
                ).scalar_one()
            )
        )


async def snapshot_page(
    engine: AsyncEngine,
    connector_key: str,
    snapshot_id: uuid.UUID,
    events: list[dict[str, Any]],
    *,
    now: datetime,
) -> int:
    """Apply one page of reconciliation evidence.

    The same event ids the live stream uses, so reconciliation cannot
    duplicate evidence that already arrived — it fills gaps and nothing
    else.
    """
    applied = 0
    for envelope in events:
        try:
            result = await apply_event(engine, connector_key, envelope, now=now)
        except IngestRejectedError:
            continue
        if result.outcome in ("applied", "duplicate"):
            applied += 1
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE protection_snapshots
                SET pages = pages + 1, records = records + :records
                WHERE id = :id AND state = 'open'
                """
            ),
            {"id": snapshot_id, "records": len(events)},
        )
    return applied


async def complete_snapshot(engine: AsyncEngine, snapshot_id: uuid.UUID) -> bool:
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                UPDATE protection_snapshots
                SET state = 'completed', completed_at = now()
                WHERE id = :id AND state = 'open'
                RETURNING id
                """
            ),
            {"id": snapshot_id},
        )
    return result.first() is not None


async def reporter_seen_at(
    connection: AsyncConnection, connector_key: str
) -> datetime | None:
    """When this connector last successfully reported anything."""
    return (
        await connection.execute(
            text(
                """
                SELECT max(received_at) FROM protection_ingest_events
                WHERE connector_key = :connector AND outcome IN ('applied', 'duplicate')
                """
            ),
            {"connector": connector_key},
        )
    ).scalar_one_or_none()


def resolve_connector(settings: Settings, key: str | None) -> tuple[str, ProtectionConnector]:
    """Server-side connector resolution. A payload never names its own."""
    if not key:
        raise IngestRejectedError("connector_missing")
    connector = settings.protection_connectors.get(key)
    if connector is None:
        raise IngestRejectedError("connector_unknown")
    return key, connector


def stale_after(connector: ProtectionConnector) -> timedelta:
    return timedelta(seconds=max(60, connector.stale_after_seconds))
