"""Objectives measured on stored requests (migration 0031).

Real PostgreSQL and Redis; the trace store is a double that answers the
rollup statement with chosen counts, because what is under test is what is
kept, how a window is judged from it, and who may define one.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis.asyncio as aioredis
from drake_api.alerting import sli_rollups
from drake_api.alerting.slo_service import (
    TRACE_AVAILABILITY,
    evaluate_definition,
    load_definitions,
    trace_threshold_ms,
)
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, login_all, make_role
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = sli_rollups.align(datetime.now(UTC))


class Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows


class FakeStore:
    """Answers the rollup statement: `per_bucket(key, t)` gives the counts
    of one workload's bucket, or None for no spans."""

    def __init__(self, per_bucket: Any = None, fail: bool = False) -> None:
        self.per_bucket = per_bucket or (lambda _key, _t: None)
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Result:
        self.calls.append(parameters)
        assert "otel_traces" in statement and "SpanKind = 'Server'" in statement
        if self.fail:
            raise RuntimeError("store down")
        keys = re.findall(r"'([^']*)'", parameters["workloads"])
        start = datetime.fromisoformat(parameters["start"].replace(" ", "T")).replace(tzinfo=UTC)
        end = datetime.fromisoformat(parameters["end"].replace(" ", "T")).replace(tzinfo=UTC)
        rows = []
        for key in keys:
            for bucket in sli_rollups.buckets_between(start, end):
                counts = self.per_bucket(key, bucket)
                if counts:
                    rows.append({"wk": key, "t": str(int(bucket.timestamp())), **counts})
        return Result(rows)


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(sli_rollups.LEASE_KEY)
    yield client
    await client.delete(sli_rollups.LEASE_KEY)
    await client.aclose()


def counts(requests: int, errors: int = 0, slow: int = 0) -> dict[str, str]:
    """`slow` requests are over every threshold up to 1 s, none above."""
    out = {"requests": str(requests), "errors": str(errors), "timed": str(requests)}
    for ms in sli_rollups.THRESHOLDS_MS:
        out[f"over_{ms}ms"] = str(slow if ms <= 1000 else 0)
    return out


async def rollup_rows(engine: AsyncEngine, binding: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT bucket_start, requests, errors, over_1000ms FROM sli_rollups "
                        "WHERE binding_id = :b ORDER BY bucket_start"
                    ),
                    {"b": binding},
                )
            ).all()
        )


async def test_the_writer_keeps_counts_and_zero_for_quiet_buckets(
    engine: AsyncEngine, redis: Any
) -> None:
    world = await make_world(engine)
    busy = NOW - timedelta(minutes=30)
    store = FakeStore(lambda _k, t: counts(120, errors=3, slow=5) if t == busy else None)

    report = await sli_rollups.run_once(engine, store, redis, database="otel", now=NOW)  # type: ignore[arg-type]

    assert report.lease_acquired and report.failed_reads == 0
    rows = await rollup_rows(engine, world["binding_id"])
    # Backfilled to what the store holds; every bucket written, quiet ones as 0.
    assert len(rows) == len(
        sli_rollups.buckets_between(
            sli_rollups.align(NOW - sli_rollups.SETTLE - sli_rollups.BACKFILL),
            sli_rollups.align(NOW - sli_rollups.SETTLE),
        )
    )
    by = {r[0]: r for r in rows}
    assert tuple(by[busy])[1:] == (120, 3, 5)
    assert sum(r[1] for r in rows) == 120
    # Reads stay under the reader's row cap.
    assert all(len(c["workloads"]) > 0 for c in store.calls)

    # The next run reads only the last few buckets again (late spans).
    store.calls.clear()
    await sli_rollups.run_once(
        engine, store, redis, database="otel", now=NOW + timedelta(minutes=5)
    )  # type: ignore[arg-type]
    assert len(store.calls) == 1


async def test_a_failed_read_leaves_the_buckets_unobserved(engine: AsyncEngine, redis: Any) -> None:
    world = await make_world(engine)
    report = await sli_rollups.run_once(
        engine, FakeStore(fail=True), redis, database="otel", now=NOW
    )  # type: ignore[arg-type]
    assert report.failed_reads == report.reads > 0
    assert await rollup_rows(engine, world["binding_id"]) == []


async def define(engine: AsyncEngine, world: dict[str, Any], **values: Any) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_definitions
                        (project_id, environment_id, service_id, environment_service_id,
                         slo_key, display_name, indicator, objective_ratio, window_seconds,
                         sli_template_key, threshold_profile_key, burn_profile_key)
                    VALUES (:p, :e, :s, :es, :key, 'API availability', :indicator, :objective,
                            :window, :template, :threshold, 'standard.30d.v1')
                    RETURNING id
                    """
                ),
                {
                    "p": world["project_id"],
                    "e": world["environment_id"],
                    "s": world["service_id"],
                    "es": world["environment_service_id"],
                    "key": f"api-{uuid.uuid4().hex[:6]}",
                    "indicator": values.get("indicator", "availability"),
                    "objective": values.get("objective", 0.99),
                    "window": values.get("window", 7 * 86400),
                    "template": values.get("template", TRACE_AVAILABILITY),
                    "threshold": values.get("threshold"),
                },
            )
        ).scalar_one()


async def judge(engine: AsyncEngine, slo: uuid.UUID, now: datetime) -> Any:
    [definition] = await load_definitions(engine, definition_id=slo)
    return await evaluate_definition(engine, None, None, definition, now=now)  # type: ignore[arg-type]


async def fill(engine: AsyncEngine, redis: Any, per_bucket: Any, now: datetime) -> None:
    await redis.delete(sli_rollups.LEASE_KEY)
    await sli_rollups.run_once(engine, FakeStore(per_bucket), redis, database="otel", now=now)  # type: ignore[arg-type]


async def slo_incidents(engine: AsyncEngine, slo: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT state, resolution_source FROM incidents "
                        "WHERE correlation_key = :k ORDER BY opened_at"
                    ),
                    {"k": f"slo:{slo}"},
                )
            ).all()
        )


async def test_availability_is_counted_per_request_and_a_fast_burn_pages(
    engine: AsyncEngine, redis: Any
) -> None:
    world = await make_world(engine)
    slo = await define(engine, world, objective=0.99)
    # Quiet and clean, then the last hour fails half its requests.
    burning_since = NOW - timedelta(hours=1)
    await fill(
        engine,
        redis,
        lambda _k, t: counts(100, errors=50) if t >= burning_since else counts(100),
        NOW,
    )

    verdict = await judge(engine, slo, NOW)
    # Only ~3 days of a 7-day window exist: judged, and said to be partial.
    assert verdict.data_quality == "partial"
    assert verdict.total is not None and verdict.bad is not None
    assert verdict.bad == pytest.approx(50 * 12, rel=0.2)
    assert verdict.status in ("critical", "exhausted")
    assert any(level.active and level.severity == "critical" for level in verdict.burn_rates)
    assert [i[0] for i in await slo_incidents(engine, slo)] == ["open"]

    # Evaluated again while still burning: still one incident.
    await judge(engine, slo, NOW)
    assert len(await slo_incidents(engine, slo)) == 1

    # Clean again for long enough that no paging level holds: resolved.
    later = NOW + timedelta(hours=7)
    await fill(engine, redis, lambda _k, t: counts(100) if t >= NOW else None, later)
    await judge(engine, slo, later)
    assert [tuple(i) for i in await slo_incidents(engine, slo)] == [("resolved", "slo_recovered")]


async def test_latency_counts_requests_over_the_threshold(engine: AsyncEngine, redis: Any) -> None:
    world = await make_world(engine)
    slo = await define(
        engine,
        world,
        indicator="latency",
        template="traces.latency.v1",
        threshold="traces.over_1000ms",
        objective=0.9,
    )
    assert trace_threshold_ms("traces.over_1000ms") == 1000
    await fill(engine, redis, lambda _k, _t: counts(100, slow=4), NOW)
    verdict = await judge(engine, slo, NOW)
    assert verdict.compliance_ratio == pytest.approx(0.96)
    assert verdict.status == "healthy"
    assert await slo_incidents(engine, slo) == []


async def test_no_traffic_is_insufficient_not_perfect(engine: AsyncEngine, redis: Any) -> None:
    world = await make_world(engine)
    slo = await define(engine, world)
    await fill(engine, redis, lambda _k, _t: None, NOW)
    verdict = await judge(engine, slo, NOW)
    assert verdict.status == "insufficient_data"
    assert verdict.compliance_ratio is None


async def _csrf(client: Any) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"slo-{uuid.uuid4().hex}"}


async def test_objectives_are_defined_changed_and_removed_in_the_screen(
    engine: AsyncEngine, redis: Any
) -> None:
    harness = build_harness(require_it_settings())
    world = await make_world(engine)
    manifest = await define(engine, world, template="service.availability.v1")
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

        options = (await client.get("/v1/slo/options")).json()
        assert options["can_manage"] is True
        mine = next(
            s
            for s in options["services"]
            if s["environment_service_id"] == str(world["environment_service_id"])
        )
        assert mine["objectives"] == 1
        assert 1000 in options["thresholds_ms"]

        body = {
            "environment_service_id": str(world["environment_service_id"]),
            "display_name": "API latency",
            "indicator": "latency",
            "objective_ratio": 0.95,
            "window_seconds": 30 * 86400,
            "threshold_ms": 1000,
        }
        created = await client.post("/v1/slo", json=body, headers=await _csrf(client))
        assert created.status_code == 201, created.text
        slo_id = created.json()["id"]

        # Only offered values are accepted.
        odd = await client.post(
            "/v1/slo", json={**body, "threshold_ms": 777}, headers=await _csrf(client)
        )
        assert odd.status_code == 422

        listed = (await client.get("/v1/slo")).json()["items"]
        row = next(i for i in listed if i["id"] == slo_id)
        assert row["source"] == "traces" and row["latency_threshold_ms"] == 1000
        assert next(i for i in listed if i["id"] == str(manifest))["source"] == "manifest"

        changed = await client.post(
            f"/v1/slo/{slo_id}",
            json={"expected_version": 1, "objective_ratio": 0.99},
            headers=await _csrf(client),
        )
        assert changed.status_code == 200 and changed.json()["version"] == 2
        stale = await client.post(
            f"/v1/slo/{slo_id}",
            json={"expected_version": 1, "objective_ratio": 0.9},
            headers=await _csrf(client),
        )
        assert stale.status_code == 409

        # A manifest objective is the manifest's.
        assert (
            await client.delete(f"/v1/slo/{manifest}", headers=await _csrf(client))
        ).status_code == 404
        assert (
            await client.delete(f"/v1/slo/{slo_id}", headers=await _csrf(client))
        ).status_code == 200

    async with engine.connect() as connection:
        actions = [
            r[0]
            for r in (
                await connection.execute(
                    text(
                        "SELECT action FROM audit_events WHERE target_id = :id ORDER BY occurred_at"
                    ),
                    {"id": slo_id},
                )
            ).all()
        ]
    assert actions == ["slo.create", "slo.update", "slo.delete"]


async def test_viewing_objectives_is_not_defining_them(engine: AsyncEngine) -> None:
    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    world = await make_world(engine)
    await login_all(harness, ["user-nobody"])
    await make_role(harness, engine, "SLO Viewer", ["slo.view", "project.view"])
    await grant(engine, harness, "user-nobody", "SLO Viewer", "organization", "root")
    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        options = (await client.get("/v1/slo/options")).json()
        assert options["can_manage"] is False and options["services"] == []
        refused = await client.post(
            "/v1/slo",
            json={
                "environment_service_id": str(world["environment_service_id"]),
                "display_name": "x",
                "indicator": "availability",
                "objective_ratio": 0.99,
                "window_seconds": 7 * 86400,
            },
            headers=await _csrf(client),
        )
        assert refused.status_code == 404
