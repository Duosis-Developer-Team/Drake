"""Job envelope contract.

Every job travelling through the queue is a validated envelope. Unknown
fields are rejected, payloads are size-bounded, and credential-shaped payload
content is refused outright — a job payload is never a secret channel.
"""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from drake_worker.redaction import contains_credential_shape

JOB_SCHEMA_VERSION = 1

MAX_PAYLOAD_BYTES = 32_768

_JOB_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)


class DeadLetterInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str
    error_code: str | None = None
    failed_at: datetime


class JobEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_type: str
    schema_version: int = Field(default=JOB_SCHEMA_VERSION, ge=1)
    idempotency_key: str
    correlation_id: str
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    attempt: int = Field(default=1, ge=1)
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    dead_letter: DeadLetterInfo | None = None

    @field_validator("job_type")
    @classmethod
    def _job_type_shape(cls, value: str) -> str:
        if not _JOB_TYPE.fullmatch(value):
            raise ValueError("job_type must match ^[a-z][a-z0-9_.-]{2,63}$")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key_shape(cls, value: str) -> str:
        if not _SAFE_KEY.fullmatch(value):
            raise ValueError("idempotency_key must match ^[A-Za-z0-9._:-]{8,200}$")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC)")
        return value

    @model_validator(mode="after")
    def _payload_boundary(self) -> Self:
        serialized = json.dumps(self.payload, ensure_ascii=False, default=str)
        if len(serialized.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the safe size boundary")
        if contains_credential_shape(serialized):
            raise ValueError("payload contains credential-shaped content and was rejected")
        if self.attempt > self.retry.max_attempts:
            raise ValueError("attempt cannot exceed retry.max_attempts")
        return self

    def next_attempt(self) -> "JobEnvelope":
        """Envelope for the retry of a failed execution.

        Re-validates (model_copy would bypass validators), so an attempt
        beyond retry.max_attempts can never be constructed.
        """
        return JobEnvelope.model_validate({**self.model_dump(), "attempt": self.attempt + 1})

    def to_dead_letter(self, reason: str, error_code: str | None = None) -> "JobEnvelope":
        info = DeadLetterInfo(reason=reason, error_code=error_code, failed_at=datetime.now(UTC))
        return self.model_copy(update={"dead_letter": info})

    def serialize(self) -> str:
        return self.model_dump_json()

    @classmethod
    def deserialize(cls, raw: str | bytes) -> "JobEnvelope":
        return cls.model_validate_json(raw)


def new_job(
    job_type: str,
    *,
    idempotency_key: str,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 60.0,
    retry: RetryPolicy | None = None,
) -> JobEnvelope:
    return JobEnvelope(
        job_type=job_type,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id or uuid.uuid4().hex,
        timeout_seconds=timeout_seconds,
        retry=retry or RetryPolicy(),
        created_at=datetime.now(UTC),
        payload=payload or {},
    )
