"""Queue behavior on fakeredis: idempotency, ordering, dead-letter."""

import fakeredis
from drake_worker.job import new_job
from drake_worker.queue import RedisJobQueue


def make_queue() -> RedisJobQueue:
    return RedisJobQueue(fakeredis.FakeRedis(), queue_name="test:jobs")


def test_enqueue_dequeue_roundtrip() -> None:
    queue = make_queue()
    job = new_job("catalog.sync", idempotency_key="catalog.sync:rt:0001")
    assert queue.enqueue(job) is True
    received = queue.dequeue(timeout_seconds=1)
    assert received == job


def test_duplicate_idempotency_key_is_not_enqueued_twice() -> None:
    queue = make_queue()
    first = new_job("catalog.sync", idempotency_key="catalog.sync:dup:0001")
    second = new_job("catalog.sync", idempotency_key="catalog.sync:dup:0001")
    assert queue.enqueue(first) is True
    assert queue.enqueue(second) is False
    assert queue.dequeue(timeout_seconds=1) == first
    assert queue.dequeue(timeout_seconds=1) is None


def test_different_idempotency_keys_both_run() -> None:
    queue = make_queue()
    assert queue.enqueue(new_job("catalog.sync", idempotency_key="catalog.sync:a:0001")) is True
    assert queue.enqueue(new_job("catalog.sync", idempotency_key="catalog.sync:b:0001")) is True


def test_fifo_ordering() -> None:
    queue = make_queue()
    first = new_job("catalog.sync", idempotency_key="catalog.sync:fifo:0001")
    second = new_job("catalog.sync", idempotency_key="catalog.sync:fifo:0002")
    queue.enqueue(first)
    queue.enqueue(second)
    assert queue.dequeue(timeout_seconds=1) == first
    assert queue.dequeue(timeout_seconds=1) == second


def test_dead_letter_requires_metadata_and_accumulates() -> None:
    queue = make_queue()
    job = new_job("catalog.sync", idempotency_key="catalog.sync:dl:0001")
    try:
        queue.push_dead_letter(job)
        raised = False
    except ValueError:
        raised = True
    assert raised is True

    queue.push_dead_letter(job.to_dead_letter("max_attempts_exhausted"))
    assert queue.dead_letter_length() == 1
