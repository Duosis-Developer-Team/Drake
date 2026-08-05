"""Job execution foundation.

The runner owns retry/dead-letter decisions and correlation propagation.
Sprint 0 registers no real domain handlers; the execution contract is what
matters and is fully unit-tested.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import Any, Literal

from drake_worker.job import JobEnvelope

logger = logging.getLogger("drake_worker.runner")

job_correlation_id: ContextVar[str] = ContextVar("job_correlation_id", default="")

JobHandler = Callable[[JobEnvelope], Any]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    decision: Literal["completed", "retry", "dead_letter"]
    job: JobEnvelope
    error_code: str | None = None


class JobRunner:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="drake-job")

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise ValueError(f"handler already registered for job_type {job_type}")
        self._handlers[job_type] = handler

    def execute(self, job: JobEnvelope) -> ExecutionOutcome:
        """Run one job and decide: completed, retry, or dead_letter.

        Handler failures never raise out of the runner; they become retry or
        dead-letter decisions. The job's correlation ID is active for the
        duration of the handler so all logs correlate.
        """
        handler = self._handlers.get(job.job_type)
        if handler is None:
            return ExecutionOutcome(
                decision="dead_letter",
                job=job.to_dead_letter("unregistered_job_type", "unregistered"),
                error_code="unregistered",
            )

        token = job_correlation_id.set(job.correlation_id)
        try:
            # Run the handler inside a copy of the current context so the
            # correlation ID contextvar is visible in the executor thread.
            context = copy_context()
            future = self._executor.submit(context.run, handler, job)
            future.result(timeout=job.timeout_seconds)
            return ExecutionOutcome(decision="completed", job=job)
        except FutureTimeoutError:
            future.cancel()
            return self._failure(job, "timeout")
        except Exception as error:
            logger.warning("job %s failed: %s", job.job_type, type(error).__name__)
            return self._failure(job, type(error).__name__)
        finally:
            job_correlation_id.reset(token)

    @staticmethod
    def _failure(job: JobEnvelope, error_code: str) -> ExecutionOutcome:
        if job.attempt < job.retry.max_attempts:
            return ExecutionOutcome(decision="retry", job=job.next_attempt(), error_code=error_code)
        return ExecutionOutcome(
            decision="dead_letter",
            job=job.to_dead_letter("max_attempts_exhausted", error_code),
            error_code=error_code,
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
