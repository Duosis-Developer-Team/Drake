"""Audit event writer.

Sprint 0 foundation: validated inserts with correlation propagation and a
safe-metadata guard. Fail-closed wiring (a failed audit write aborting the
surrounding mutation) is applied when real mutations arrive in Sprint 1.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.audit.models import AuditEvent
from drake_api.correlation import correlation_id_var, new_correlation_id
from drake_api.logging import REDACTED, redact

ACTOR_TYPES = frozenset({"user", "service", "system"})
RESULTS = frozenset({"success", "failure", "denied"})

AUDIT_SCHEMA_VERSION = 1

_MAX_METADATA_BYTES = 8_192


@dataclass(frozen=True, slots=True)
class AuditEventData:
    actor_type: str
    actor_id: str
    action: str
    result: str
    scope_type: str | None = None
    scope_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_event(event: AuditEventData) -> dict[str, Any]:
    """Validate an event and return column values ready for insertion.

    Raises ``ValueError`` for invalid enums or unsafe metadata. Error messages
    never include metadata values.
    """
    if event.actor_type not in ACTOR_TYPES:
        raise ValueError(f"invalid actor_type; expected one of {sorted(ACTOR_TYPES)}")
    if event.result not in RESULTS:
        raise ValueError(f"invalid result; expected one of {sorted(RESULTS)}")
    if not event.actor_id or not event.action:
        raise ValueError("actor_id and action are required")

    serialized = json.dumps(event.metadata, ensure_ascii=False)
    if len(serialized.encode()) > _MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the safe size boundary")
    if redact(serialized) != serialized or REDACTED in serialized:
        raise ValueError("metadata contains credential-shaped content and was rejected")

    correlation_id = event.correlation_id or correlation_id_var.get() or new_correlation_id()
    return {
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action": event.action,
        "result": event.result,
        "scope_type": event.scope_type,
        "scope_id": event.scope_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "correlation_id": correlation_id,
        "metadata": event.metadata,
        "schema_version": AUDIT_SCHEMA_VERSION,
    }


async def record_audit_event(engine: AsyncEngine, event: AuditEventData) -> uuid.UUID:
    """Insert an audit event in its OWN transaction. Raises on failure.

    Right for anything audited after its work has already committed — an
    acknowledgement, a login. Wrong for anything that must not exist unless
    its audit row does; those use `record_audit_event_in` below and share
    the caller's transaction.
    """
    values = validate_event(event)
    # Insert via the table (not the ORM entity): the "metadata" column key
    # would otherwise collide with the declarative Base.metadata attribute.
    table = cast(Table, AuditEvent.__table__)
    async with engine.begin() as connection:
        result = await connection.execute(insert(table).values(**values).returning(table.c.id))
        return cast(uuid.UUID, result.scalar_one())


async def record_audit_event_in(connection: AsyncConnection, event: AuditEventData) -> uuid.UUID:
    """Insert an audit event inside the CALLER'S transaction.

    For work that is not allowed to exist unauditably. An onboarding apply
    writes catalog rows, an SLO, a binding and its own result row; if the
    audit insert fails, all of it has to disappear with it — otherwise Drake
    has changed a customer's catalog with no record of who asked or why, and
    the operator has no way to discover that happened.

    The cost is that a failing audit fails the operation. That is the
    intended trade: fail closed.
    """
    values = validate_event(event)
    table = cast(Table, AuditEvent.__table__)
    result = await connection.execute(insert(table).values(**values).returning(table.c.id))
    return cast(uuid.UUID, result.scalar_one())
