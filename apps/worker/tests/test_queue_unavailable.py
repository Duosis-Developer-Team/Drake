"""Redis unavailability is a controlled, typed failure without URL leakage."""

import pytest
from drake_worker.job import new_job
from drake_worker.queue import QueueUnavailableError, RedisJobQueue
from redis import Redis

# Deliberately closed local port: connection refused, deterministically.
UNREACHABLE = Redis(host="127.0.0.1", port=59379, socket_connect_timeout=0.2, socket_timeout=0.2)


def test_enqueue_raises_controlled_error() -> None:
    queue = RedisJobQueue(UNREACHABLE, queue_name="test:jobs")
    job = new_job("catalog.sync", idempotency_key="catalog.sync:down:0001")
    with pytest.raises(QueueUnavailableError) as excinfo:
        queue.enqueue(job)
    assert "59379" not in str(excinfo.value)
    assert "127.0.0.1" not in str(excinfo.value)


def test_dequeue_raises_controlled_error() -> None:
    queue = RedisJobQueue(UNREACHABLE, queue_name="test:jobs")
    with pytest.raises(QueueUnavailableError):
        queue.dequeue(timeout_seconds=1)
