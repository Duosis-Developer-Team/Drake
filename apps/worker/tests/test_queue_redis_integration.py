"""Queue behavior against the disposable local Redis (integration)."""

import os
import time

import pytest
from drake_worker.job import new_job
from drake_worker.queue import RedisJobQueue
from redis import Redis

pytestmark = pytest.mark.integration


def require_redis() -> Redis:
    url = os.environ.get("DRAKE_IT_REDIS_URL")
    if not url:
        pytest.skip("DRAKE_IT_REDIS_URL not set")
    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def test_roundtrip_and_idempotency_on_real_redis() -> None:
    client = require_redis()
    queue = RedisJobQueue(client, queue_name=f"it:jobs:{time.monotonic_ns()}")

    job = new_job("catalog.sync", idempotency_key=f"it:catalog.sync:{time.monotonic_ns()}")
    assert queue.enqueue(job) is True
    assert queue.enqueue(job) is False  # duplicate suppressed by idempotency marker

    received = queue.dequeue(timeout_seconds=2)
    assert received == job
    assert queue.dequeue(timeout_seconds=1) is None

    dead = job.to_dead_letter("integration_check")
    queue.push_dead_letter(dead)
    assert queue.dead_letter_length() == 1
