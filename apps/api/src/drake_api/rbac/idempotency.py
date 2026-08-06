"""PostgreSQL-backed transactional idempotency for RBAC mutations.

Guarantees (all inside ONE database transaction with the mutation + audit):

- Concurrent identical requests: the unique constraint on
  (actor, operation, key) serializes them at the database — one performs the
  mutation, the other blocks on the conflicting insert until the first
  transaction resolves, then replays the committed response. Correct across
  multiple API replicas; no application locks, no Redis authority.
- Same key + same operation + same payload after commit → stored response is
  replayed; no second mutation.
- Same key + same operation + DIFFERENT payload/preconditions → stable
  ``409 idempotency_conflict``.
- Same key on a different operation → independent record (operation is part
  of the unique claim); never replays an unrelated response.
- Mutation or audit failure → the whole transaction (claim included) rolls
  back; the key is safely retryable.
- Client never received the response (commit happened) → retry replays.
- Expired records are reclaimed.

Stored responses are Drake-generated JSON payloads only; a credential-shape
guard rejects anything unsafe before storage. No cookies, tokens, session
IDs, or secrets are ever stored.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.logging import redact


class IdempotencyConflictError(Exception):
    """Same (actor, operation, key) reused with a different request → 409."""


def request_fingerprint(
    path_params: dict[str, Any], body: Any, precondition: str | None = None
) -> str:
    """Canonical fingerprint over everything that defines the request."""
    canonical = json.dumps(
        {"path": path_params, "body": body, "precondition": precondition},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyGuard:
    """Claims and completes one idempotency record on the CALLER's
    connection/transaction."""

    def __init__(
        self,
        connection: AsyncConnection,
        actor_identity_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> None:
        self._connection = connection
        self._actor = actor_identity_id
        self._operation = operation
        self._key = idempotency_key
        self._fingerprint = fingerprint
        self._record_id: Any = None

    async def claim(self) -> tuple[int, dict[str, Any]] | None:
        """Atomically claim the key or return the stored response to replay.

        Returns ``None`` when this request now owns the claim (caller must
        perform the mutation and then call :meth:`complete`), or
        ``(status, body)`` to replay. Raises ``IdempotencyConflictError`` on
        fingerprint mismatch.
        """
        # Reclaim expired records first (bounded, targeted delete).
        await self._connection.execute(
            text(
                """
                DELETE FROM idempotency_records
                WHERE actor_identity_id = :actor AND operation = :operation
                  AND idempotency_key = :key AND expires_at < now()
                """
            ),
            {"actor": self._actor, "operation": self._operation, "key": self._key},
        )

        # The concurrency point: a concurrent transaction inserting the same
        # (actor, operation, key) blocks HERE until the first commits or
        # rolls back — PostgreSQL's unique index arbitrates, not the app.
        row = (
            await self._connection.execute(
                text(
                    """
                    INSERT INTO idempotency_records
                        (actor_identity_id, operation, idempotency_key, request_fingerprint)
                    VALUES (:actor, :operation, :key, :fingerprint)
                    ON CONFLICT (actor_identity_id, operation, idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "actor": self._actor,
                    "operation": self._operation,
                    "key": self._key,
                    "fingerprint": self._fingerprint,
                },
            )
        ).first()

        if row is not None:
            self._record_id = row[0]
            return None

        stored = (
            await self._connection.execute(
                text(
                    """
                    SELECT request_fingerprint, status, response_status, response_body
                    FROM idempotency_records
                    WHERE actor_identity_id = :actor AND operation = :operation
                      AND idempotency_key = :key
                    """
                ),
                {"actor": self._actor, "operation": self._operation, "key": self._key},
            )
        ).first()
        if stored is None:
            # The competing transaction rolled back between our insert and
            # select; retry the claim once.
            return await self.claim()

        if stored[0] != self._fingerprint:
            raise IdempotencyConflictError()
        if stored[1] != "completed" or stored[2] is None:
            # A committed-but-incomplete record is impossible in the single-
            # transaction design; treat defensively as a conflict rather
            # than replaying garbage.
            raise IdempotencyConflictError()
        return int(stored[2]), dict(stored[3] or {})

    async def complete(self, status_code: int, body: dict[str, Any]) -> None:
        """Record the response inside the same transaction as the mutation."""
        serialized = json.dumps(body, ensure_ascii=False, default=str)
        if redact(serialized) != serialized:
            raise ValueError("idempotency response contains credential-shaped content")
        await self._connection.execute(
            text(
                """
                UPDATE idempotency_records
                SET status = 'completed', response_status = :status,
                    response_body = CAST(:body AS jsonb), completed_at = now()
                WHERE id = :id
                """
            ),
            {"status": status_code, "body": serialized, "id": self._record_id},
        )
