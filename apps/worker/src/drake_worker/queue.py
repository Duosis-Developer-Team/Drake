"""Redis-backed job queue with idempotency and dead-letter support.

Redis unavailability is a controlled, typed failure (``QueueUnavailableError``)
so callers degrade explicitly instead of crashing or silently pretending
success.
"""

from redis import Redis
from redis.exceptions import RedisError

from drake_worker.job import JobEnvelope

DEFAULT_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


class QueueUnavailableError(RuntimeError):
    """Raised when Redis cannot be reached. Never contains connection URLs."""


class RedisJobQueue:
    def __init__(
        self,
        client: Redis,
        queue_name: str = "drake:jobs",
        idempotency_ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._queue = queue_name
        self._dead_letter_queue = f"{queue_name}:dead-letter"
        self._idempotency_prefix = f"{queue_name}:idem:"
        self._idempotency_ttl = idempotency_ttl_seconds

    def enqueue(self, job: JobEnvelope) -> bool:
        """Enqueue a job. Returns False when the idempotency key was already seen."""
        marker = f"{self._idempotency_prefix}{job.idempotency_key}"
        try:
            claimed = self._client.set(marker, job.job_type, nx=True, ex=self._idempotency_ttl)
            if not claimed:
                return False
            self._client.lpush(self._queue, job.serialize())
            return True
        except RedisError as error:
            raise QueueUnavailableError("job queue is unavailable") from error

    def dequeue(self, timeout_seconds: int = 5) -> JobEnvelope | None:
        try:
            item = self._client.brpop([self._queue], timeout=timeout_seconds)
        except RedisError as error:
            raise QueueUnavailableError("job queue is unavailable") from error
        if item is None:
            return None
        _queue_name, raw = item
        return JobEnvelope.deserialize(raw)

    def push_dead_letter(self, job: JobEnvelope) -> None:
        if job.dead_letter is None:
            raise ValueError(
                "job must carry dead_letter info before entering the dead-letter queue"
            )
        try:
            self._client.lpush(self._dead_letter_queue, job.serialize())
        except RedisError as error:
            raise QueueUnavailableError("dead-letter queue is unavailable") from error

    def dead_letter_length(self) -> int:
        try:
            return int(self._client.llen(self._dead_letter_queue))
        except RedisError as error:
            raise QueueUnavailableError("dead-letter queue is unavailable") from error
