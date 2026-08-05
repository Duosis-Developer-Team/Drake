"""Job envelope contract: validation, boundaries, and safe payloads."""

from datetime import UTC, datetime

import pytest
from drake_worker.job import JobEnvelope, RetryPolicy, new_job
from pydantic import ValidationError


def test_new_job_fills_contract_fields() -> None:
    job = new_job("catalog.sync", idempotency_key="catalog.sync:2026-08-06")
    assert job.schema_version == 1
    assert job.attempt == 1
    assert job.correlation_id
    assert job.created_at.tzinfo is not None
    assert job.retry.max_attempts == 3


def test_unknown_fields_are_rejected() -> None:
    job = new_job("catalog.sync", idempotency_key="catalog.sync:2026-08-06")
    raw = job.model_dump(mode="json")
    raw["surprise"] = True
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate(raw)


def test_invalid_job_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        new_job("Not A Valid Type!", idempotency_key="valid-key-0001")


def test_short_idempotency_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        new_job("catalog.sync", idempotency_key="short")


def test_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        JobEnvelope(
            job_type="catalog.sync",
            idempotency_key="catalog.sync:0001",
            correlation_id="c" * 12,
            created_at=datetime(2026, 8, 6),
        )


def test_oversized_payload_is_rejected() -> None:
    with pytest.raises(ValidationError, match="size boundary"):
        new_job(
            "catalog.sync",
            idempotency_key="catalog.sync:0002",
            payload={"blob": "x" * 40_000},
        )


def test_credential_shaped_payload_is_rejected() -> None:
    with pytest.raises(ValidationError, match="credential"):
        new_job(
            "catalog.sync",
            idempotency_key="catalog.sync:0003",
            payload={"dsn": "postgresql://user:fakepw@host/db"},
        )


def test_attempt_cannot_exceed_max_attempts() -> None:
    job = new_job(
        "catalog.sync",
        idempotency_key="catalog.sync:0004",
        retry=RetryPolicy(max_attempts=2),
    )
    second = job.next_attempt()
    assert second.attempt == 2
    with pytest.raises(ValidationError, match="max_attempts"):
        second.next_attempt()


def test_serialize_roundtrip_is_lossless() -> None:
    job = new_job(
        "backup.reconcile",
        idempotency_key="backup.reconcile:store-1:2026-08-06",
        payload={"store": "postgres-main"},
    )
    restored = JobEnvelope.deserialize(job.serialize())
    assert restored == job


def test_dead_letter_metadata_is_carried() -> None:
    job = new_job("catalog.sync", idempotency_key="catalog.sync:0005")
    dead = job.to_dead_letter("max_attempts_exhausted", "TimeoutError")
    assert dead.dead_letter is not None
    assert dead.dead_letter.reason == "max_attempts_exhausted"
    assert dead.dead_letter.failed_at.tzinfo is UTC
