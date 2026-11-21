"""Alert rules against the real database (ADR-0031).

The evaluator half: a sample is stored per verdict, a critical rule opens
and closes exactly one incident, the planner notifies it, a warning or a
muted rule never pages, and stale evidence moves nothing.

The API half: who may see and write rules, audit on every write, optimistic
versions, and a preview that replays the same samples the runner judged.
"""

import uuid as uuidlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from drake_api.alert_rules.evaluator import record_and_evaluate
from drake_api.notifications.planner import plan_pending
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import make_world
from test_notification_planner_integration import attach, make_destination, make_policy
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime.now(UTC).replace(microsecond=0)


def payload(at: datetime, *, error_ratio: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "computed_at": at.isoformat(),
        "application": {"error_ratio": error_ratio, "latency_p95_seconds": 0.2},
        "availability": {"desired_replicas": 2, "ready_replicas": 2},
        **extra,
    }


def catalog(world: dict[str, Any]) -> dict[str, Any]:
    return {
        "environment_service_id": world["environment_service_id"],
        "project_id": world["project_id"],
        "environment_id": world["environment_id"],
        "service_id": world["service_id"],
    }


async def make_rule(
    engine: AsyncEngine,
    world: dict[str, Any],
    *,
    severity: str = "critical",
    threshold: float = 0.05,
    for_seconds: int = 120,
    muted_until: datetime | None = None,
    scope: str = "project",
    signal: str = "error_ratio",
) -> uuidlib.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO alert_rules
                        (project_id, environment_id, service_id, name, signal, operator,
                         threshold, for_seconds, severity, muted_until)
                    VALUES (:p, :e, :s, 'Errors high', :signal, '>', :t, :f, :sev, :m)
                    RETURNING id
                    """
                ),
                {
                    "p": world["project_id"],
                    "e": world["environment_id"] if scope in ("environment", "service") else None,
                    "s": world["service_id"] if scope == "service" else None,
                    "t": threshold,
                    "f": for_seconds,
                    "sev": severity,
                    "m": muted_until,
                    "signal": signal,
                },
            )
        ).scalar_one()


async def feed(
    engine: AsyncEngine, world: dict[str, Any], values: list[float | None], start: datetime
) -> None:
    for index, value in enumerate(values):
        await record_and_evaluate(
            engine,
            binding_id=world["binding_id"],
            catalog=catalog(world),
            service_key="api",
            payload=payload(start + timedelta(minutes=index), error_ratio=value),
        )


async def rule_incidents(engine: AsyncEngine, rule_id: uuidlib.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id, state, source, resolution_source FROM incidents "
                        "WHERE correlation_key LIKE :k ORDER BY opened_at"
                    ),
                    {"k": f"rule:{rule_id}:%"},
                )
            ).all()
        )


async def rule_events(engine: AsyncEngine, rule_id: uuidlib.UUID) -> list[str]:
    async with engine.connect() as connection:
        return [
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT kind FROM alert_rule_events WHERE rule_id = :r ORDER BY at"),
                    {"r": rule_id},
                )
            ).all()
        ]


# --- evaluator --------------------------------------------------------------


async def test_a_critical_rule_opens_one_incident_and_closes_it(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world)
    start = NOW - timedelta(minutes=30)
    # breach at 0,1,2 (fires at minute 2: held 120 s), still breached at 3,
    # then healthy at 4.
    await feed(engine, world, [0.2, 0.2, 0.2, 0.3, 0.01], start)

    assert await rule_events(engine, rule) == ["fired", "resolved"]
    incidents = await rule_incidents(engine, rule)
    assert len(incidents) == 1
    assert incidents[0][1:] == ("resolved", "rule", "rule_resolved")
    async with engine.connect() as connection:
        kinds = [
            r[0]
            for r in (
                await connection.execute(
                    text(
                        "SELECT event_type FROM incident_events WHERE incident_id = :i "
                        "ORDER BY occurred_at"
                    ),
                    {"i": incidents[0][0]},
                )
            ).all()
        ]
        samples = (
            await connection.execute(
                text("SELECT count(*) FROM service_health_samples WHERE binding_id = :b"),
                {"b": world["binding_id"]},
            )
        ).scalar_one()
        state = (
            await connection.execute(
                text("SELECT state, incident_id FROM alert_rule_states WHERE rule_id = :r"),
                {"r": rule},
            )
        ).one()
    assert kinds == ["opened", "auto_resolved"]
    assert samples == 5
    assert state == ("ok", None)


async def test_a_rule_incident_reaches_the_notification_planner(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world, for_seconds=0)
    recipient = await make_destination(engine, world, identity_id=await _identity(engine))
    policy = await make_policy(engine, world)
    await attach(engine, policy, recipient)
    await feed(engine, world, [0.5], NOW - timedelta(minutes=5))
    await plan_pending(engine)
    incident = (await rule_incidents(engine, rule))[0][0]
    async with engine.connect() as connection:
        planned = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM in_app_notifications n JOIN incident_events e "
                    "ON e.id = n.incident_event_id WHERE e.incident_id = :i"
                ),
                {"i": incident},
            )
        ).scalar_one()
    assert planned == 1


async def _identity(engine: AsyncEngine) -> uuidlib.UUID:
    subject = f"rule-recipient-{uuidlib.uuid4().hex[:8]}"
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name, email) "
                    "VALUES ('https://tests.drake.local/v2.0', :s, 'R', :e) RETURNING id"
                ),
                {"s": subject, "e": f"{subject}@example.test"},
            )
        ).scalar_one()


async def test_warning_and_muted_rules_keep_history_but_never_page(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    warning = await make_rule(engine, world, severity="warning", for_seconds=0)
    muted = await make_rule(engine, world, for_seconds=0, muted_until=NOW + timedelta(hours=1))
    await feed(engine, world, [0.4, 0.01], NOW - timedelta(minutes=10))
    for rule in (warning, muted):
        assert await rule_events(engine, rule) == ["fired", "resolved"]
        assert await rule_incidents(engine, rule) == []


async def test_missing_values_and_stale_verdicts_move_nothing(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world, for_seconds=0)
    start = NOW - timedelta(minutes=20)
    await feed(engine, world, [0.4, None], start)
    # A last-good verdict is old evidence: no sample, no step.
    await record_and_evaluate(
        engine,
        binding_id=world["binding_id"],
        catalog=catalog(world),
        service_key="api",
        payload=payload(start + timedelta(minutes=5), error_ratio=0.0, served_from_last_good=True),
    )
    assert await rule_events(engine, rule) == ["fired"]
    assert [r[1] for r in await rule_incidents(engine, rule)] == ["open"]


async def test_a_rule_on_another_service_does_not_cover_this_binding(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    other = await make_world(engine)
    rule = await make_rule(engine, other, for_seconds=0, scope="service")
    await feed(engine, world, [0.9], NOW - timedelta(minutes=3))
    assert await rule_events(engine, rule) == []


async def test_a_fatal_log_rule_opens_an_incident_and_closes_it(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world, signal="fatal_logs", threshold=0, for_seconds=0)
    start = NOW - timedelta(minutes=30)
    for index, fatal in enumerate([0.0, 2.0, 1.0, 0.0]):
        await record_and_evaluate(
            engine,
            binding_id=world["binding_id"],
            catalog=catalog(world),
            service_key="api",
            payload=payload(start + timedelta(minutes=index), error_ratio=0.0),
            telemetry={"fatal_logs": fatal, "error_logs": fatal},
        )
    assert await rule_events(engine, rule) == ["fired", "resolved"]
    assert [tuple(r[1:]) for r in await rule_incidents(engine, rule)] == [
        ("resolved", "rule", "rule_resolved")
    ]
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT fatal_logs, error_ratio FROM service_health_samples "
                    "WHERE binding_id = :b ORDER BY observed_at"
                ),
                {"b": world["binding_id"]},
            )
        ).all()
    assert [tuple(r) for r in stored] == [(0.0, 0.0), (2.0, 0.0), (1.0, 0.0), (0.0, 0.0)]


async def test_a_stale_verdict_still_records_this_minutes_logs(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    logs = await make_rule(engine, world, signal="error_logs", threshold=5, for_seconds=0)
    metric = await make_rule(engine, world, for_seconds=0)
    old = NOW - timedelta(minutes=40)
    now = NOW - timedelta(minutes=2)
    await record_and_evaluate(
        engine,
        binding_id=world["binding_id"],
        catalog=catalog(world),
        service_key="api",
        payload=payload(old, error_ratio=0.9, served_from_last_good=True),
        telemetry={"error_logs": 40.0},
        now=now,
    )
    # The log rule saw a new observation; the metric rule saw only old
    # evidence and stays where it was.
    assert await rule_events(engine, logs) == ["fired"]
    assert await rule_events(engine, metric) == []
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT observed_at, error_logs, error_ratio FROM service_health_samples "
                    "WHERE binding_id = :b"
                ),
                {"b": world["binding_id"]},
            )
        ).all()
    assert [tuple(r) for r in stored] == [(now, 40.0, None)]


async def test_a_rule_that_matches_text_is_judged_on_its_own_value(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world, signal="matching_logs", threshold=0, for_seconds=0)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET log_contains = 'payment failed' WHERE id = :r"),
            {"r": rule},
        )
    start = NOW - timedelta(minutes=50)
    for index, matched in enumerate([None, 3.0, 0.0]):
        await record_and_evaluate(
            engine,
            binding_id=world["binding_id"],
            catalog=catalog(world),
            service_key="api",
            payload=payload(start + timedelta(minutes=index), error_ratio=0.0),
            rule_values={str(rule): matched},
        )
    # Unknown moved nothing; three matching lines fired; none resolved it.
    assert await rule_events(engine, rule) == ["fired", "resolved"]


async def test_an_anomaly_rule_fires_against_this_hours_normal(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    rule = await make_rule(engine, world, signal="error_logs", threshold=3, for_seconds=0)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE alert_rules SET operator = 'above_normal' WHERE id = :r"), {"r": rule}
        )
        # A week of history at this time of day: about ten error lines.
        for day in range(1, 8):
            for minute in range(-25, 26, 5):
                at = NOW - timedelta(days=day, minutes=minute)
                await connection.execute(
                    text(
                        "INSERT INTO service_health_samples (binding_id, observed_at, error_logs) "
                        "VALUES (:b, :at, :v) ON CONFLICT DO NOTHING"
                    ),
                    {"b": world["binding_id"], "at": at, "v": 10 + (minute % 3)},
                )
    for index, logs in enumerate([12.0, 40.0]):
        await record_and_evaluate(
            engine,
            binding_id=world["binding_id"],
            catalog=catalog(world),
            service_key="api",
            payload=payload(NOW - timedelta(minutes=4 - index), error_ratio=0.0),
            telemetry={"error_logs": logs},
        )
    # 12 is normal for this hour; 40 is not.
    assert await rule_events(engine, rule) == ["fired"]
    async with engine.connect() as connection:
        bound = (
            await connection.execute(
                text("SELECT threshold FROM alert_rule_events WHERE rule_id = :r"), {"r": rule}
            )
        ).scalar_one()
    assert 12 < bound < 40  # the normal's upper edge, recorded with the event


async def test_the_sampler_reads_a_page_of_bindings_in_two_statements(
    engine: AsyncEngine,
) -> None:
    from drake_api.alert_rules.telemetry import TelemetrySampler

    world = await make_world(engine)
    async with engine.connect() as connection:
        cluster_ref, namespace, kind, name = (
            await connection.execute(
                text(
                    "SELECT c.cluster_ref, b.namespace, b.workload_kind, b.workload_name "
                    "FROM service_workload_bindings b JOIN clusters c ON c.id = b.cluster_id "
                    "WHERE b.id = :b"
                ),
                {"b": world["binding_id"]},
            )
        ).one()
    wk = f"{cluster_ref}|{namespace}|{str(kind).lower()}|{name}"
    asked: list[str] = []

    class Store:
        connector = type("C", (), {"database": "drake", "timeout_seconds": 5.0})()

        async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Any:
            asked.append(statement)
            assert wk in parameters["workloads"]
            if "otel_logs" in statement:
                rows = [{"wk": wk, "errors": "7", "fatal": "0"}]
            else:
                rows = [{"wk": wk, "slow": "3", "routes": "2", "route_p95_ns": 2_000_000_000}]
            return type("R", (), {"rows": rows})()

    sampler = TelemetrySampler(Store(), logs_enabled=True, traces_enabled=True)  # type: ignore[arg-type]
    out = await sampler.sample(engine, [world["binding_id"]], NOW)
    assert len(asked) == 2
    assert out[str(world["binding_id"])] == {
        "error_logs": 7.0,
        "fatal_logs": 0.0,
        "slow_requests": 3.0,
        "slowest_route_p95_seconds": 2.0,
    }
    off = TelemetrySampler(Store(), logs_enabled=False, traces_enabled=False)  # type: ignore[arg-type]
    assert await off.sample(engine, [world["binding_id"]], NOW) == {}


# --- API --------------------------------------------------------------------


def harness_with_users() -> Any:
    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    return harness


async def _csrf(client: Any) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"ar-{uuidlib.uuid4().hex}"}


async def test_rule_crud_is_audited_versioned_and_listed_with_history(
    engine: AsyncEngine,
) -> None:
    harness = harness_with_users()
    world = await make_world(engine)
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

        options = (await client.get("/v1/alert-rules/options")).json()
        assert "error_ratio" in options["signals"]
        assert "fatal_logs" in options["signals"]
        assert options["evaluation_interval_seconds"] >= 15
        assert any(p["id"] == str(world["project_id"]) for p in options["projects"])

        body = {
            "project_id": str(world["project_id"]),
            "environment_id": str(world["environment_id"]),
            "service_id": str(world["service_id"]),
            "name": "Checkout errors",
            "signal": "error_ratio",
            "operator": ">",
            "threshold": 0.05,
            "for_seconds": 0,
            "severity": "critical",
        }
        created = await client.post("/v1/alert-rules", json=body, headers=await _csrf(client))
        assert created.status_code == 201, created.text
        rule_id = created.json()["id"]

        # A ratio above 1 and a service without its environment are refused.
        bad = {**body, "threshold": 2}
        assert (
            await client.post("/v1/alert-rules", json=bad, headers=await _csrf(client))
        ).status_code == 422
        orphan = {**body, "environment_id": None}
        assert (
            await client.post("/v1/alert-rules", json=orphan, headers=await _csrf(client))
        ).status_code == 422

        # Every signal the options offer can be saved and previewed: the
        # screen never offers a rule the API would refuse.
        for signal in options["signals"]:
            threshold = (
                0.05 if signal in ("error_ratio", "cpu_utilization", "memory_utilization") else 1
            )
            each = {
                **body,
                "name": f"Every signal {signal}",
                "signal": signal,
                "threshold": threshold,
            }
            if signal == "matching_logs":
                each["log_contains"] = "payment failed"
            saved = await client.post("/v1/alert-rules", json=each, headers=await _csrf(client))
            assert saved.status_code == 201, (signal, saved.text)
            draft = {k: v for k, v in each.items() if k != "name"}
            tried = await client.post(
                "/v1/alert-rules/preview", json=draft, headers=await _csrf(client)
            )
            assert tried.status_code == 200, (signal, tried.text)

        # A rule against its own normal, and one on a single endpoint.
        for extra in (
            {"operator": "above_normal", "threshold": 3},
            {"signal": "slowest_route_p95_seconds", "threshold": 1, "route": "POST /checkout"},
        ):
            each = {**body, "name": f"Kind {sorted(extra)}", **extra}
            saved = await client.post("/v1/alert-rules", json=each, headers=await _csrf(client))
            assert saved.status_code == 201, (extra, saved.text)
            listed = (await client.get(f"/v1/alert-rules?project_id={world['project_id']}")).json()
            mine = next(r for r in listed["rules"] if r["id"] == saved.json()["id"])
            assert mine["route"] == extra.get("route") and mine["operator"] == each["operator"]
            tried = await client.post(
                "/v1/alert-rules/preview",
                json={k: v for k, v in each.items() if k != "name"},
                headers=await _csrf(client),
            )
            assert tried.status_code == 200, (extra, tried.text)
        mixed = {**body, "operator": "above_normal", "threshold": 3, "log_contains": "x"}
        assert (
            await client.post("/v1/alert-rules", json=mixed, headers=await _csrf(client))
        ).status_code == 422
        routes = await client.get(
            f"/v1/alert-rules/routes?project_id={world['project_id']}"
            f"&environment_id={world['environment_id']}&service_id={world['service_id']}"
        )
        assert routes.status_code == 200 and isinstance(routes.json()["routes"], list)

        await feed(engine, world, [0.3, 0.3, 0.01], NOW - timedelta(minutes=8))

        listing = (await client.get(f"/v1/alert-rules?project_id={world['project_id']}")).json()
        rule = next(r for r in listing["rules"] if r["id"] == rule_id)
        assert rule["status"] == "ok"
        assert rule["can_manage"] is True
        assert len(rule["episodes"]) == 1
        assert rule["episodes"][0]["resolved_at"] is not None
        assert listing["summary"]["fired_24h"] >= 1

        history = (await client.get(f"/v1/alert-rules/{rule_id}/history")).json()
        assert [p["value"] for p in history["bindings"][0]["series"]] == [0.3, 0.3, 0.01]

        preview = await client.post(
            "/v1/alert-rules/preview",
            json={k: v for k, v in body.items() if k != "name"} | {"threshold": 0.2},
            headers=await _csrf(client),
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["count"] == 1

        patched = await client.post(
            f"/v1/alert-rules/{rule_id}",
            json={"threshold": 0.1, "expected_version": 1},
            headers=await _csrf(client),
        )
        assert patched.status_code == 200 and patched.json()["version"] == 2
        stale = await client.post(
            f"/v1/alert-rules/{rule_id}",
            json={"threshold": 0.2, "expected_version": 1},
            headers=await _csrf(client),
        )
        assert stale.status_code == 409

        muted = await client.post(
            f"/v1/alert-rules/{rule_id}/mute", json={"minutes": 60}, headers=await _csrf(client)
        )
        assert muted.status_code == 200 and muted.json()["muted_until"]
        listing = (await client.get(f"/v1/alert-rules?project_id={world['project_id']}")).json()
        assert next(r for r in listing["rules"] if r["id"] == rule_id)["status"] == "muted"

        deleted = await client.delete(f"/v1/alert-rules/{rule_id}", headers=await _csrf(client))
        assert deleted.status_code == 200

    async with engine.connect() as connection:
        actions = [
            r[0]
            for r in (
                await connection.execute(
                    text(
                        "SELECT action FROM audit_events WHERE target_id = :t ORDER BY occurred_at"
                    ),
                    {"t": rule_id},
                )
            ).all()
        ]
    assert actions == [
        "alert_rule.create",
        "alert_rule.update",
        "alert_rule.mute",
        "alert_rule.delete",
    ]


async def test_rules_are_invisible_and_unwritable_without_a_grant(engine: AsyncEngine) -> None:
    harness = harness_with_users()
    world = await make_world(engine)
    rule = await make_rule(engine, world)
    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        listing = (await client.get("/v1/alert-rules")).json()
        assert all(r["id"] != str(rule) for r in listing["rules"])
        assert (await client.get(f"/v1/alert-rules/{rule}/history")).status_code == 404
        assert (
            await client.delete(f"/v1/alert-rules/{rule}", headers=await _csrf(client))
        ).status_code == 404
        create = await client.post(
            "/v1/alert-rules",
            json={
                "project_id": str(world["project_id"]),
                "name": "x",
                "signal": "error_ratio",
                "operator": ">",
                "threshold": 0.1,
                "for_seconds": 0,
                "severity": "warning",
            },
            headers=await _csrf(client),
        )
        assert create.status_code == 404
        # Writes need CSRF before anything else is considered.
        assert (await client.delete(f"/v1/alert-rules/{rule}")).status_code in (401, 403)


async def test_disabling_or_deleting_a_firing_rule_closes_its_incident(
    engine: AsyncEngine,
) -> None:
    """A rule nobody evaluates any more must not leave its incident open.

    Disabled or deleted, the rule is never judged again, so nothing else
    would ever resolve what it opened: the page stayed open for a condition
    nobody watches.
    """
    harness = harness_with_users()
    world = await make_world(engine)
    disabled = await make_rule(engine, world, for_seconds=0)
    deleted = await make_rule(engine, world, for_seconds=0, scope="environment")
    await feed(engine, world, [0.4], NOW - timedelta(minutes=5))
    assert [i[1] for i in await rule_incidents(engine, disabled)] == ["open"]
    assert [i[1] for i in await rule_incidents(engine, deleted)] == ["open"]

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        async with engine.connect() as connection:
            version = (
                await connection.execute(
                    text("SELECT version FROM alert_rules WHERE id = :r"), {"r": disabled}
                )
            ).scalar_one()
        off = await client.post(
            f"/v1/alert-rules/{disabled}",
            json={"enabled": False, "expected_version": version},
            headers=await _csrf(client),
        )
        assert off.status_code == 200, off.text
        gone = await client.delete(f"/v1/alert-rules/{deleted}", headers=await _csrf(client))
        assert gone.status_code in (200, 204), gone.text

    for rule, reason in ((disabled, "rule_disabled"), (deleted, "rule_deleted")):
        incidents = await rule_incidents(engine, rule)
        assert [i[1:] for i in incidents] == [("resolved", "rule", "rule_resolved")]
        async with engine.connect() as connection:
            detail = (
                await connection.execute(
                    text(
                        "SELECT detail FROM incident_events "
                        "WHERE incident_id = :i AND event_type = 'auto_resolved'"
                    ),
                    {"i": incidents[0][0]},
                )
            ).scalar_one()
        assert detail == {"source": "rule", "reason": reason}

    # Disabled, the rule holds nothing: re-enabled, it starts from ok.
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT state, incident_id FROM alert_rule_states WHERE rule_id = :r"),
                {"r": disabled},
            )
        ).one()
    assert tuple(state) == ("ok", None)


async def test_alert_view_alone_cannot_replay_logs_or_traces(engine: AsyncEngine) -> None:
    """A log or trace rule's series is log and trace data.

    With `alert.view` alone a viewer could preview `matching_logs` for any
    text and read, five minutes at a time, how often it appeared in any
    service's logs. The series now needs the signal's own permission;
    without it the service is simply not covered.
    """
    from test_catalog_api_integration import grant, login_all, make_role

    harness = harness_with_users()
    world = await make_world(engine)
    await login_all(harness, ["user-nobody"])
    await make_role(
        harness, engine, "Alert viewer", ["alert.view", "project.view", "environment.view"]
    )
    await grant(engine, harness, "user-nobody", "Alert viewer", "organization", "root")
    log_rule = await make_rule(engine, world, signal="error_logs", threshold=0)
    metric_rule = await make_rule(engine, world)
    draft = {
        "project_id": str(world["project_id"]),
        "environment_id": None,
        "service_id": None,
        "signal": "matching_logs",
        "log_contains": "password",
        "operator": ">",
        "threshold": 0,
        "for_seconds": 0,
        "severity": "warning",
    }

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        owner = await client.post(
            "/v1/alert-rules/preview", json=draft, headers=await _csrf(client)
        )
        assert owner.status_code == 200, owner.text
        assert owner.json()["bindings"] == 1

    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        viewer = await client.post(
            "/v1/alert-rules/preview", json=draft, headers=await _csrf(client)
        )
        assert viewer.status_code == 200, viewer.text
        assert viewer.json()["bindings"] == 0
        assert viewer.json()["episodes"] == []

        history = await client.get(f"/v1/alert-rules/{log_rule}/history")
        assert history.status_code == 200
        assert history.json()["bindings"] == []
        # A metric rule's history is what alert.view is for.
        metric = await client.get(f"/v1/alert-rules/{metric_rule}/history")
        assert len(metric.json()["bindings"]) == 1

        routes = await client.get(
            "/v1/alert-rules/routes", params={"project_id": str(world["project_id"])}
        )
        assert routes.status_code == 200
        assert routes.json()["routes"] == []
