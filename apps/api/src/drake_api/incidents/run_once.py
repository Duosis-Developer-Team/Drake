"""Run exactly one evaluation cycle, from a terminal.

For an operator verifying a datasource change, or a test that wants a
deterministic cycle instead of waiting for a timer. It calls the same
`run_cycle` the background runner does — including the lease — so
running it while the runner is active is safe: one of the two simply
finds the lease held and does nothing.

This is deliberately a command and not an endpoint. A public "evaluate
now" button would let any authenticated user drive provider load and
influence when incidents open.
"""

import argparse
import asyncio
import json
import sys

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine

from drake_api.incidents.runner import run_cycle
from drake_api.service_health.cache import HealthCache
from drake_api.service_health.orchestrator import HealthOrchestrator
from drake_api.settings import get_settings
from drake_api.telemetry.broker import TelemetryBroker
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.provider import PrometheusAdapter
from drake_api.telemetry.registry import get_registry


async def run(batch_size: int, concurrency: int) -> dict[str, int | bool]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    redis = aioredis.from_url(settings.redis_url)
    try:
        registry = get_registry()
        broker = TelemetryBroker(
            settings=settings,
            engine=engine,
            redis=redis,
            registry=registry,
            adapter=PrometheusAdapter(settings),
            metrics=BrokerMetrics(),
        )
        orchestrator = HealthOrchestrator(engine, broker, registry, HealthCache(redis))
        report = await run_cycle(
            engine,
            orchestrator,
            redis,
            batch_size=batch_size,
            concurrency=concurrency,
            lease_seconds=settings.incident_runner_lease_seconds,
        )
        # Counts only. Nothing per-service, so this output can be pasted
        # into a ticket without leaking what runs where.
        return {
            "lease_acquired": report.lease_acquired,
            "evaluated": report.evaluated,
            "failed": report.failed,
            "incidents_opened": report.incidents_opened,
            "incidents_resolved": report.incidents_resolved,
            "transitions": report.transitions,
            "duplicates": report.duplicates,
        }
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one incident evaluation cycle")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    result = asyncio.run(run(max(1, args.batch_size), max(1, args.concurrency)))
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
