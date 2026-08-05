"""Audit event writer.

Sprint 0 foundation: validated inserts with correlation propagation and a
safe-metadata guard. Fail-closed wiring (a failed audit write aborting the
surrounding mutation) is applied when real mutations arrive in Sprint 1.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

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
    """Insert an audit event and return its id. Raises on failure."""
    values = validate_event(event)
    async with engine.begin() as connection:
        result = await connection.execute(
            insert(AuditEvent).values(**values).returning(AuditEvent.id)
        )
        return result.scalar_one()
