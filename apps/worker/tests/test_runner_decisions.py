"""Runner decisions: retry limits, dead-letter, timeout, correlation propagation."""

import time

from drake_worker.job import JobEnvelope, RetryPolicy, new_job
from drake_worker.runner import JobRunner, job_correlation_id


def make_job(**kwargs: object) -> JobEnvelope:
    defaults: dict[str, object] = {
        "idempotency_key": f"test:runner:{time.monotonic_ns()}",
    }
    defaults.update(kwargs)
    return new_job("test.job", **defaults)  # type: ignore[arg-type]


def test_successful_handler_completes() -> None:
    runner = JobRunner()
    runner.register("test.job", lambda _job: None)
    outcome = runner.execute(make_job())
    assert outcome.decision == "completed"
    runner.shutdown()


def test_unregistered_job_type_goes_to_dead_letter() -> None:
    runner = JobRunner()
    outcome = runner.execute(make_job())
    assert outcome.decision == "dead_letter"
    assert outcome.job.dead_letter is not None
    assert outcome.job.dead_letter.reason == "unregistered_job_type"
    runner.shutdown()


def test_failure_retries_until_max_attempts_then_dead_letters() -> None:
    runner = JobRunner()

    def failing(_job: JobEnvelope) -> None:
        raise RuntimeError("boom")

    runner.register("test.job", failing)

    job = make_job(retry=RetryPolicy(max_attempts=3, backoff_seconds=0.0))
    first = runner.execute(job)
    assert first.decision == "retry"
    assert first.job.attempt == 2

    second = runner.execute(first.job)
    assert second.decision == "retry"
    assert second.job.attempt == 3

    final = runner.execute(second.job)
    assert final.decision == "dead_letter"
    assert final.job.dead_letter is not None
    assert final.job.dead_letter.reason == "max_attempts_exhausted"
    assert final.error_code == "RuntimeError"
    runner.shutdown()


def test_timeout_is_a_failure_decision_not_a_crash() -> None:
    runner = JobRunner()
    runner.register("test.job", lambda _job: time.sleep(2.0))
    job = make_job(timeout_seconds=0.2, retry=RetryPolicy(max_attempts=1))
    outcome = runner.execute(job)
    assert outcome.decision == "dead_letter"
    assert outcome.error_code == "timeout"
    runner.shutdown()


def test_correlation_id_is_visible_to_handler() -> None:
    runner = JobRunner()
    seen: list[str] = []
    runner.register("test.job", lambda job: seen.append(job_correlation_id.get()))
    job = make_job(correlation_id="corr-prop-000001")
    outcome = runner.execute(job)
    assert outcome.decision == "completed"
    assert seen == ["corr-prop-000001"]
    runner.shutdown()


def test_duplicate_handler_registration_is_rejected() -> None:
    runner = JobRunner()
    runner.register("test.job", lambda _job: None)
    try:
        runner.register("test.job", lambda _job: None)
        raised = False
    except ValueError:
        raised = True
    assert raised is True
    runner.shutdown()
