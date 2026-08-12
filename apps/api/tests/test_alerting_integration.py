"""Alertmanager projection, SLO arithmetic and incident operations.

The responsibility line this suite defends, stated once:

    PrometheusRule = decides when a condition is true
    Alertmanager   = grouping, dedupe, inhibition, silence, base notification
    Drake          = business context, ownership, timeline, controlled ops

Each scenario below is one place that line could be crossed, or one place a
comforting-but-wrong answer could be produced: a retry counted as a second
outage, a group status closing a still-firing alert, an empty window
reported as 100%, a silence shown as active when Alertmanager never
accepted it.
"""

import json
import uuid as uuidlib
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from drake_api.alerting import ingest, silences
from drake_api.alerting import repository as alert_repo
from drake_api.alerting.model import (
    ALLOWED_LABELS,
    FORBIDDEN_LABELS,
    IngestRejectedError,
    Priority,
    normalize_alert,
    normalize_delivery,
)
from drake_api.alerting.slo import (
    BURN_PROFILE_30D,
    SloStatus,
    WindowObservation,
    burn_rate,
    evaluate_slo,
)
from drake_api.alerting.slo_service import ensure_definition
from drake_api.incidents.processor import assign_incident
from drake_api.settings import AlertmanagerIntegration
from harness_s1 import S1Harness, build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
MONTH = 2_592_000


# ===========================================================================
# payload normalization (pure)
# ===========================================================================


def alert_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "firing",
        "fingerprint": "a1b2c3d4e5f6a7b8",
        "labels": {
            "alertname": "HighErrorRate",
            "severity": "critical",
            "project": "pilot",
            "environment": "dev",
            "service": "api",
        },
        "annotations": {"summary": "Errors above objective"},
        "startsAt": "2026-08-08T11:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def delivery_payload(alerts: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "4",
        "groupKey": '{}:{alertname="HighErrorRate"}',
        "status": "firing",
        "receiver": "drake-webhook",
        "truncatedAlerts": 0,
        "groupLabels": {"alertname": "HighErrorRate"},
        "commonLabels": {"severity": "critical"},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.internal.invalid",
        "alerts": alerts,
    }
    base.update(overrides)
    return base


def test_only_allowlisted_labels_and_annotations_survive() -> None:
    """An alert label is writable by anyone who can write a recording rule."""
    alert = normalize_alert(
        alert_payload(
            labels={
                "alertname": "HighErrorRate",
                "severity": "critical",
                "project": "pilot",
                # Every one of these must be dropped.
                "email": "someone@example.com",
                "user_id": "42",
                "trace_id": "abc123",
                "instance": "10.0.0.7:9090",
                "pod": "api-7d9f",
                "url": "https://grafana.internal/d/abc",
            },
            annotations={
                "summary": "Errors above objective",
                "runbook_url": "https://wiki.internal/runbook",
                "dashboardURL": "https://grafana.internal/d/abc",
            },
        )
    )
    assert set(alert.labels) <= ALLOWED_LABELS
    assert not set(alert.labels) & FORBIDDEN_LABELS
    assert alert.annotations == {"summary": "Errors above objective"}


def test_a_value_carrying_a_scheme_is_dropped_wherever_it_appears() -> None:
    """A link is a link whichever field it arrived in."""
    alert = normalize_alert(
        alert_payload(
            labels={
                "alertname": "HighErrorRate",
                "severity": "critical",
                "runbook": "https://wiki.internal/runbook",
            },
            annotations={"summary": "See https://grafana.internal/d/abc for detail"},
        )
    )
    assert "runbook" not in alert.labels
    assert alert.annotations == {}


def test_an_unknown_severity_becomes_p3_not_p1() -> None:
    """A label typo must not page someone, and must not vanish either."""
    alert = normalize_alert(
        alert_payload(labels={"alertname": "X", "severity": "SEV-1", "project": "pilot"})
    )
    assert alert.severity == "unknown"
    assert alert.priority == str(Priority.P3)
    assert alert.severity_recognised is False


def test_severity_maps_to_priority() -> None:
    for severity, priority in (
        ("critical", "P1"),
        ("high", "P2"),
        ("medium", "P3"),
        ("info", "P4"),
    ):
        alert = normalize_alert(alert_payload(labels={"alertname": "X", "severity": severity}))
        assert alert.priority == priority


def test_a_resolved_alert_without_an_end_time_is_refused() -> None:
    """It cannot be placed on a timeline, and inventing one is fiction."""
    with pytest.raises(IngestRejectedError):
        normalize_alert(alert_payload(status="resolved"))


def test_one_malformed_alert_does_not_cost_the_rest_of_the_batch() -> None:
    delivery = normalize_delivery(
        delivery_payload(
            [
                alert_payload(),
                alert_payload(fingerprint="bbbb1111", startsAt="not-a-time"),
            ]
        ),
        b"{}",
    )
    assert len(delivery.alerts) == 1
    assert delivery.rejected == 1


def test_the_group_key_is_hashed_and_the_external_url_is_never_kept() -> None:
    body = json.dumps(delivery_payload([alert_payload()])).encode()
    delivery = normalize_delivery(json.loads(body), body)
    assert delivery.group_key_digest is not None
    assert "alertname" not in delivery.group_key_digest
    serialized = json.dumps(delivery.__dict__, default=str)
    assert "://" not in serialized


# ===========================================================================
# SLO arithmetic (pure)
# ===========================================================================


def test_an_empty_window_is_insufficient_data_not_a_hundred_percent() -> None:
    """0/0 is not perfect. A dead scrape target must not look healthy."""
    verdict = evaluate_slo(
        objective_ratio=0.999,
        window=WindowObservation(seconds=3600, good=0.0, bad=0.0, samples=0),
    )
    assert verdict.status == str(SloStatus.INSUFFICIENT_DATA)
    assert verdict.compliance_ratio is None
    assert verdict.error_budget_remaining is None


def test_a_query_failure_and_a_stale_read_are_different_from_each_other() -> None:
    failed = evaluate_slo(objective_ratio=0.999, window=None, query_failed=True)
    stale = evaluate_slo(objective_ratio=0.999, window=None, served_stale=True)
    assert failed.status == str(SloStatus.QUERY_FAILED)
    assert failed.error_code == "sli_query_failed"
    assert stale.status == str(SloStatus.STALE)
    # And neither is healthy or zero.
    assert failed.compliance_ratio is None and stale.compliance_ratio is None


def test_a_missing_sli_mapping_is_not_configured() -> None:
    verdict = evaluate_slo(objective_ratio=0.999, window=None, not_configured=True)
    assert verdict.status == str(SloStatus.NOT_CONFIGURED)


def test_error_budget_arithmetic_is_in_ratios() -> None:
    # 99.9% objective, 10,000 requests: 10 failures is exactly the budget.
    verdict = evaluate_slo(
        objective_ratio=0.999,
        window=WindowObservation(seconds=3600, good=9_990.0, bad=10.0, samples=60),
    )
    assert verdict.compliance_ratio == pytest.approx(0.999)
    assert verdict.error_budget_total == pytest.approx(10.0)
    assert verdict.error_budget_consumed == pytest.approx(1.0)
    assert verdict.error_budget_remaining == pytest.approx(0.0)
    assert verdict.status == str(SloStatus.EXHAUSTED)


def test_a_negative_remaining_budget_is_reported_as_negative() -> None:
    """180% burned is 80% past the objective. Clamping hides how far."""
    verdict = evaluate_slo(
        objective_ratio=0.999,
        window=WindowObservation(seconds=3600, good=9_982.0, bad=18.0, samples=60),
    )
    assert verdict.error_budget_consumed == pytest.approx(1.8)
    assert verdict.error_budget_remaining == pytest.approx(-0.8)


def test_a_zero_error_objective_produces_no_infinity() -> None:
    """100% leaves no budget, so a burn RATE is undefined, not infinite."""
    assert burn_rate(0.01, 1.0) is None
    clean = evaluate_slo(
        objective_ratio=1.0,
        window=WindowObservation(seconds=3600, good=1_000.0, bad=0.0, samples=60),
    )
    assert clean.status == str(SloStatus.HEALTHY)
    assert clean.zero_error_policy is True
    dirty = evaluate_slo(
        objective_ratio=1.0,
        window=WindowObservation(seconds=3600, good=999.0, bad=1.0, samples=60),
    )
    assert dirty.status == str(SloStatus.EXHAUSTED)
    assert dirty.error_budget_consumed is None


def test_a_burn_level_needs_both_windows_over_the_threshold() -> None:
    """One window alone is a spike or a memory. Paging on either flaps."""
    fast = BURN_PROFILE_30D[0]
    objective = 0.999
    # Comfortably over 14.4x in both windows.
    hot = WindowObservation(seconds=1, good=95.0, bad=5.0, samples=10)
    cold = WindowObservation(seconds=1, good=100.0, bad=0.0, samples=10)

    both = evaluate_slo(
        objective_ratio=objective,
        window=WindowObservation(seconds=3600, good=9_900.0, bad=100.0, samples=60),
        burn_windows={fast.long_seconds: hot, fast.short_seconds: hot},
    )
    assert any(level.active and level.name == fast.name for level in both.burn_rates)

    long_only = evaluate_slo(
        objective_ratio=objective,
        window=WindowObservation(seconds=3600, good=9_900.0, bad=100.0, samples=60),
        burn_windows={fast.long_seconds: hot, fast.short_seconds: cold},
    )
    assert not any(level.active for level in long_only.burn_rates)

    short_only = evaluate_slo(
        objective_ratio=objective,
        window=WindowObservation(seconds=3600, good=9_900.0, bad=100.0, samples=60),
        burn_windows={fast.long_seconds: cold, fast.short_seconds: hot},
    )
    assert not any(level.active for level in short_only.burn_rates)


# ===========================================================================
# projection, dedupe, resolve and reopen (database)
# ===========================================================================


async def make_alertmanager(engine: AsyncEngine, world: dict[str, Any]) -> dict[str, Any]:
    """Register an Alertmanager integration against the seeded project."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT p.project_key, e.environment_key, sd.service_key
                    FROM projects p
                    JOIN environments e ON e.id = :environment
                    JOIN service_definitions sd ON sd.id = :service
                    WHERE p.id = :project
                    """
                ),
                {
                    "project": world["project_id"],
                    "environment": world["environment_id"],
                    "service": world["service_id"],
                },
            )
        ).first()
    assert row is not None
    resolved = await ingest.ensure_integration_row(engine, "am-test", str(row[0]))
    assert resolved is not None
    return {
        "integration_id": resolved[0],
        "project_id": resolved[1],
        "project_key": str(row[0]),
        "environment_key": str(row[1]),
        "service_key": str(row[2]),
    }


def labels_for(context: dict[str, Any], **extra: str) -> dict[str, str]:
    base = {
        "alertname": "HighErrorRate",
        "severity": "critical",
        "project": context["project_key"],
        "environment": context["environment_key"],
        "service": context["service_key"],
    }
    base.update(extra)
    return base


async def deliver(
    engine: AsyncEngine,
    context: dict[str, Any],
    alerts: list[dict[str, Any]],
    *,
    body_salt: str = "",
    now: datetime | None = None,
) -> Any:
    payload = delivery_payload(alerts)
    body = (json.dumps(payload, sort_keys=True) + body_salt).encode()
    delivery = normalize_delivery(payload, body)
    return await ingest.apply_delivery(
        engine,
        integration_id=context["integration_id"],
        project_id=context["project_id"],
        project_key=context["project_key"],
        delivery=delivery,
        now=now or NOW,
    )


async def counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as connection:
        return {
            table: int(
                (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
            )
            for table in (
                "alert_instances",
                "alert_events",
                "incidents",
                "incident_events",
                "alert_incident_links",
            )
        }


@pytest.mark.anyio
async def test_the_same_firing_delivery_three_times_is_one_of_everything(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """Alertmanager retries as a matter of course. A retry is not an outage."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    alert = alert_payload(labels=labels_for(context))

    first = await deliver(engine, context, [alert])
    second = await deliver(engine, context, [alert])
    third = await deliver(engine, context, [alert])

    assert first.duplicate is False
    assert second.duplicate is True and third.duplicate is True

    totals = await counts(engine)
    assert totals["alert_instances"] == 1
    assert totals["alert_events"] == 1
    assert totals["incidents"] == 1
    # One `opened` event, so the Sprint 7 planner will produce one plan.
    assert totals["incident_events"] == 1


@pytest.mark.anyio
async def test_a_redelivered_body_with_different_bytes_still_produces_one_event(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """The event dedupe key is the second line, behind the delivery digest.

    A payload that differs only in whitespace or key order has a different
    digest, so it passes the delivery check — the alert's own dedupe key is
    what stops it becoming a second transition.
    """
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    alert = alert_payload(labels=labels_for(context))

    await deliver(engine, context, [alert])
    outcome = await deliver(engine, context, [alert], body_salt="   ")

    assert outcome.duplicate is False
    assert outcome.alerts[0].event_recorded is False
    totals = await counts(engine)
    assert totals["alert_events"] == 1
    assert totals["incidents"] == 1


@pytest.mark.anyio
async def test_firing_resolved_firing_reopens_rather_than_duplicating(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    labels = labels_for(context)

    await deliver(engine, context, [alert_payload(labels=labels)])
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels, status="resolved", endsAt="2026-08-08T11:30:00Z")],
    )
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels, startsAt="2026-08-08T12:00:00Z")],
    )

    async with engine.connect() as connection:
        alert_row = (
            await connection.execute(
                text("SELECT status, occurrence, incident_id FROM alert_instances")
            )
        ).first()
        events = [
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT event_type FROM alert_events ORDER BY source_event_at")
                )
            ).all()
        ]
        incident_events = [
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT event_type FROM incident_events ORDER BY occurred_at")
                )
            ).all()
        ]
    assert alert_row is not None
    assert alert_row[0] == "firing"
    assert alert_row[1] == 2
    assert events == ["firing", "resolved", "reopened"]
    # The incident stayed open through the resolve, so the reopen updates it
    # rather than opening a second one.
    assert incident_events == ["opened", "alert_resolved", "alert_reopened"]
    assert (await counts(engine))["incidents"] == 1


@pytest.mark.anyio
async def test_a_resolved_alert_mitigates_but_does_not_close_the_incident(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """Alertmanager stopping means the CONDITION cleared, not that anyone
    looked or that the problem was handled."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    labels = labels_for(context)

    await deliver(engine, context, [alert_payload(labels=labels)])
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels, status="resolved", endsAt="2026-08-08T11:30:00Z")],
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT state, mitigated_at, resolved_at FROM incidents"))
        ).first()
    assert row is not None
    assert row[0] == "open"
    assert row[1] is not None
    assert row[2] is None


@pytest.mark.anyio
async def test_an_old_resolved_event_does_not_drag_a_new_firing_backwards(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    labels = labels_for(context)

    await deliver(engine, context, [alert_payload(labels=labels)])
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels, status="resolved", endsAt="2026-08-08T11:30:00Z")],
    )
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels, startsAt="2026-08-08T12:00:00Z")],
    )
    # The resolved notification arrives again, late, after the new firing.
    stale = await deliver(
        engine,
        context,
        [alert_payload(labels=labels, status="resolved", endsAt="2026-08-08T11:30:00Z")],
        body_salt="late",
    )

    assert stale.alerts[0].stale is True
    async with engine.connect() as connection:
        status = (await connection.execute(text("SELECT status FROM alert_instances"))).scalar_one()
    assert status == "firing"


@pytest.mark.anyio
async def test_two_services_in_one_group_do_not_become_one_incident(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """A group is a notification batch, not an identity."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    async with engine.begin() as connection:
        other_service = (
            await connection.execute(
                text(
                    "INSERT INTO service_definitions (project_id, service_key, display_name, "
                    "component, runtime, metrics_profile, catalog_source_kind) "
                    "VALUES (:p, 'worker', 'Worker', 'worker', 'kubernetes', 'http', "
                    "'manifest') RETURNING id"
                ),
                {"p": context["project_id"]},
            )
        ).scalar_one()
        scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "SELECT 'service', :ref, e.scope_id FROM environments e WHERE e.id = :env "
                    "RETURNING id"
                ),
                {"ref": f"s-{uuidlib.uuid4().hex[:8]}", "env": world["environment_id"]},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO environment_services (environment_id, service_id, project_id, "
                "scope_id) VALUES (:e, :s, :p, :scope)"
            ),
            {
                "e": world["environment_id"],
                "s": other_service,
                "p": context["project_id"],
                "scope": scope,
            },
        )

    await deliver(
        engine,
        context,
        [
            alert_payload(fingerprint="aaaa1111", labels=labels_for(context)),
            alert_payload(
                fingerprint="bbbb2222",
                labels=labels_for(context, service="worker"),
            ),
        ],
    )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT a.fingerprint, a.incident_id, sd.service_key "
                    "FROM alert_instances a JOIN service_definitions sd ON sd.id = a.service_id "
                    "ORDER BY a.fingerprint"
                )
            )
        ).all()
    assert len(rows) == 2
    assert rows[0][2] != rows[1][2]
    assert rows[0][1] != rows[1][1]
    assert (await counts(engine))["incidents"] == 2


@pytest.mark.anyio
async def test_an_unmapped_alert_is_quarantined_not_filed_elsewhere(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)

    outcome = await deliver(
        engine,
        context,
        [
            # A service that does not exist in this project.
            alert_payload(
                fingerprint="cccc3333",
                labels=labels_for(context, service="ghost-service"),
            ),
            # A project the integration was not registered for.
            alert_payload(
                fingerprint="dddd4444",
                labels=labels_for(context, project="somebody-elses-project"),
            ),
        ],
    )

    assert outcome.unmapped == 2
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT mapping_state, mapping_error_code, project_id, incident_id "
                    "FROM alert_instances ORDER BY fingerprint"
                )
            )
        ).all()
    assert [row[0] for row in rows] == ["unmapped", "unmapped"]
    assert sorted(row[1] for row in rows) == ["project_mismatch", "service_unknown"]
    # No project, and therefore no incident anywhere.
    assert all(row[2] is None and row[3] is None for row in rows)
    assert (await counts(engine))["incidents"] == 0


@pytest.mark.anyio
async def test_an_ambiguous_service_fails_closed(engine: AsyncEngine, migrated_db: None) -> None:
    """One service in two environments, and no environment label to choose."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    async with engine.begin() as connection:
        scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "SELECT 'environment', :ref, p.scope_id FROM projects p WHERE p.id = :p "
                    "RETURNING id"
                ),
                {"ref": f"e-{uuidlib.uuid4().hex[:8]}", "p": context["project_id"]},
            )
        ).scalar_one()
        second_env = (
            await connection.execute(
                text(
                    "INSERT INTO environments (project_id, environment_key, runtime, "
                    "catalog_source_kind, cluster_id, namespace, scope_id) "
                    "VALUES (:p, 'staging', 'external', 'manifest', NULL, NULL, :scope) "
                    "RETURNING id"
                ),
                {"p": context["project_id"], "scope": scope},
            )
        ).scalar_one()
        service_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('service', :ref, :parent) RETURNING id"
                ),
                {"ref": f"s-{uuidlib.uuid4().hex[:8]}", "parent": scope},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO environment_services (environment_id, service_id, project_id, "
                "scope_id) VALUES (:e, :s, :p, :scope)"
            ),
            {
                "e": second_env,
                "s": world["service_id"],
                "p": context["project_id"],
                "scope": service_scope,
            },
        )

    labels = labels_for(context)
    labels.pop("environment")
    await deliver(engine, context, [alert_payload(labels=labels)])

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT mapping_state, mapping_error_code, incident_id FROM alert_instances")
            )
        ).first()
    assert row is not None
    assert row[0] == "ambiguous"
    assert row[1] == "environment_ambiguous"
    assert row[2] is None


@pytest.mark.anyio
async def test_a_p3_alert_is_recorded_but_pages_nobody(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    await deliver(
        engine,
        context,
        [alert_payload(labels=labels_for(context, severity="medium"))],
    )
    totals = await counts(engine)
    assert totals["alert_instances"] == 1
    assert totals["incidents"] == 0


@pytest.mark.anyio
async def test_a_project_level_signal_with_no_service_binding_opens_an_incident(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """The Sprint 9 limitation, closed.

    A backup policy protects a store, not a pod. Declining to record an
    incident because no workload binding existed was silence, not safety.
    """
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    labels = labels_for(context)
    labels.pop("service")

    await deliver(engine, context, [alert_payload(labels=labels)])

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT source, binding_id, environment_service_id, service_id, "
                    "project_id, environment_id, correlation_key FROM incidents"
                )
            )
        ).first()
    assert row is not None
    assert row[0] == "alert"
    assert row[1] is None and row[2] is None and row[3] is None
    assert row[4] is not None and row[5] is not None
    assert str(row[6]).startswith("alert:")


@pytest.mark.anyio
async def test_an_alert_and_the_protection_evaluator_link_instead_of_duplicating(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """Two witnesses to one fact should not page the same person twice."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO incidents
                    (source, correlation_key, project_id, environment_id, state, severity,
                     title, primary_reason, binding_revision, opened_at, last_critical_at)
                VALUES ('protection', 'protection:seed', :project, :environment, 'open',
                        'critical', 'store: Backup overdue', 'backup_overdue', 1, now(), now())
                """
            ),
            {"project": context["project_id"], "environment": world["environment_id"]},
        )

    labels = labels_for(context, signal="protection")
    labels.pop("service")
    await deliver(engine, context, [alert_payload(fingerprint="eeee5555", labels=labels)])

    async with engine.connect() as connection:
        incidents = (
            await connection.execute(text("SELECT source, correlation_key FROM incidents"))
        ).all()
        links = (await connection.execute(text("SELECT link_type FROM alert_incident_links"))).all()
    assert len(incidents) == 1
    assert incidents[0][0] == "protection"
    assert [row[0] for row in links] == ["correlated"]


# ===========================================================================
# authentication and the webhook boundary
# ===========================================================================


def integration(
    tmp_path: Any,
    # A fixture value written to a temp file, not a credential.
    token: str = "test-token-value",  # noqa: S107
) -> AlertmanagerIntegration:
    reference = tmp_path / "am-token"
    reference.write_text(token)
    return AlertmanagerIntegration(
        project_key="pilot",
        webhook_token_file=str(reference),
        api_base_url="https://alertmanager.test",
    )


def test_the_bearer_token_is_required_and_compared_in_constant_time(
    tmp_path: Any,
) -> None:
    configured = integration(tmp_path)
    ingest.verify_bearer(configured, "Bearer test-token-value")

    for header in (None, "", "test-token-value", "Basic test-token-value", "Bearer wrong"):
        with pytest.raises(IngestRejectedError):
            ingest.verify_bearer(configured, header)


def test_an_integration_with_no_readable_credential_authenticates_nobody() -> None:
    unconfigured = AlertmanagerIntegration(project_key="pilot")
    with pytest.raises(IngestRejectedError):
        ingest.verify_bearer(unconfigured, "Bearer anything")


@pytest.mark.anyio
async def test_a_delivered_notification_is_recorded_as_a_real_observation(
    engine: AsyncEngine, migrated_db: None, tmp_path: Any
) -> None:
    """The projection said "never heard from" about a source just heard from.

    Only the telemetry broker ever wrote `observed_state`, so an Alertmanager
    that was authenticating and delivering real alerts sat at `unknown`
    forever. A delivery that authenticated and projected IS an interaction
    with the integration, and this records it as one.

    It goes through the HTTP route on purpose: the other delivery tests call
    `apply_delivery` directly, which is exactly the layer this does not live
    in.
    """
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    reference = tmp_path / "am-token"
    reference.write_text("test-token-value")
    configured = AlertmanagerIntegration(
        # The seeded project, so the route resolves the same row this asserts on.
        project_key=context["project_key"],
        webhook_token_file=str(reference),
        api_base_url="https://alertmanager.test",
    )
    settings = require_it_settings()
    harness = build_harness(
        settings.model_copy(update={"alertmanager_integrations": {"am-test": configured}})
    )

    async def observed() -> tuple[str, Any]:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT observed_state, last_success_at FROM integrations "
                        "WHERE config_ref = 'am-test'"
                    )
                )
            ).first()
        assert row is not None
        return str(row[0]), row[1]

    before, _ = await observed()
    assert before == "unknown", "nothing has been heard from this integration yet"

    payload = delivery_payload([alert_payload(labels=labels_for(context))])
    async with harness.api_client() as client:
        accepted = await client.post(
            "/webhooks/alertmanager/am-test",
            json=payload,
            headers={"Authorization": "Bearer test-token-value"},
        )
    assert accepted.status_code == 202, accepted.text

    after, last_success = await observed()
    assert after == "ok"
    assert last_success is not None, "a success must carry the time it happened"


@pytest.mark.anyio
async def test_the_webhook_refuses_an_unknown_integration_and_a_bad_token(
    engine: AsyncEngine, migrated_db: None, tmp_path: Any
) -> None:
    settings = require_it_settings()
    configured = integration(tmp_path)
    harness = build_harness(
        settings.model_copy(update={"alertmanager_integrations": {"am-test": configured}})
    )
    payload = delivery_payload([alert_payload()])
    async with harness.api_client() as client:
        unknown = await client.post("/webhooks/alertmanager/nope", json=payload)
        bad_token = await client.post(
            "/webhooks/alertmanager/am-test",
            json=payload,
            headers={"Authorization": "Bearer wrong"},
        )
        missing = await client.post("/webhooks/alertmanager/am-test", json=payload)
    # One status for every authentication failure: distinguishing them tells
    # a prober which half to keep working on.
    assert unknown.status_code == 401
    assert bad_token.status_code == 401
    assert missing.status_code == 401
    assert unknown.json()["error"]["code"] == bad_token.json()["error"]["code"]


# ===========================================================================
# silences
# ===========================================================================


def test_matchers_are_backend_produced_and_never_regex() -> None:
    matchers = silences.build_matchers(
        {
            "environment": "dev",
            "service": "api",
            # A regex someone put in a label must not become a matcher.
            "namespace": ".*",
            "email": "someone@example.com",
        },
        project_key="pilot",
        alert_name="HighErrorRate",
    )
    assert all(matcher["isRegex"] is False for matcher in matchers)
    names = {matcher["name"] for matcher in matchers}
    # Always anchored on the project, so a silence cannot escape it.
    assert "project" in names and "alertname" in names
    assert "email" not in names
    # The value is carried verbatim as an EXACT match, so `.*` matches the
    # literal string and cannot widen anything.
    namespace = next(m for m in matchers if m["name"] == "namespace")
    assert namespace["value"] == ".*" and namespace["isRegex"] is False


def test_a_duration_outside_the_configured_bounds_is_refused(tmp_path: Any) -> None:
    configured = integration(tmp_path).model_copy(
        update={"min_silence_seconds": 300, "max_silence_seconds": 3600}
    )
    assert silences.clamp_duration(configured, 900) == 900
    for requested in (60, 7_200):
        with pytest.raises(silences.SilenceError):
            silences.clamp_duration(configured, requested)


@pytest.mark.anyio
async def test_the_database_refuses_a_regex_matcher_even_if_code_tried(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """Application checks lose races. This one cannot."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    async with engine.begin() as connection:
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'service', 'T', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await silences.request_silence(
                connection,
                integration_id=context["integration_id"],
                project_id=context["project_id"],
                alert_instance_id=None,
                incident_id=None,
                matchers=[{"name": "service", "value": ".*", "isRegex": True, "isEqual": True}],
                seconds=900,
                reason_code="known_issue",
                reason_note=None,
                actor_identity_id=uuidlib.UUID(str(identity)),
                key=uuidlib.uuid4().hex,
            )


@pytest.mark.anyio
async def test_a_provider_failure_never_shows_as_an_active_silence(
    engine: AsyncEngine, migrated_db: None, tmp_path: Any
) -> None:
    """An operator who thinks an alert is suppressed will stop watching it."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    settings = require_it_settings()
    configured = integration(tmp_path)
    settings = settings.model_copy(update={"alertmanager_integrations": {"am-test": configured}})
    async with engine.begin() as connection:
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'service', 'T', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()
        silence_id, _created = await silences.request_silence(
            connection,
            integration_id=context["integration_id"],
            project_id=context["project_id"],
            alert_instance_id=None,
            incident_id=None,
            matchers=[{"name": "project", "value": context["project_key"], "isRegex": False}],
            seconds=900,
            reason_code="known_issue",
            reason_note=None,
            actor_identity_id=uuidlib.UUID(str(identity)),
            key=uuidlib.uuid4().hex,
        )

    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "alertmanager exploded"})

    async def resolver(_hostname: str, _port: int) -> list[str]:
        return ["203.0.113.10"]

    await silences.process_pending(
        engine,
        settings,
        max_attempts=1,
        transport=httpx.MockTransport(refuse),
        resolver=resolver,
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT state, provider_silence_id, error_code FROM silence_requests "
                    "WHERE id = :id"
                ),
                {"id": silence_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"
    assert row[1] is None
    assert row[2] == "http_500"
    # The provider's own message never reaches Drake's storage.
    assert "exploded" not in str(row[2])


@pytest.mark.anyio
async def test_a_successful_silence_records_the_provider_id_and_bounds(
    engine: AsyncEngine, migrated_db: None, tmp_path: Any
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    settings = require_it_settings().model_copy(
        update={"alertmanager_integrations": {"am-test": integration(tmp_path)}}
    )
    captured: dict[str, Any] = {}

    def accept(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"silenceID": "prov-1234"})

    async def resolver(_hostname: str, _port: int) -> list[str]:
        return ["203.0.113.10"]

    async with engine.begin() as connection:
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'service', 'T', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()
        silence_id, _created = await silences.request_silence(
            connection,
            integration_id=context["integration_id"],
            project_id=context["project_id"],
            alert_instance_id=None,
            incident_id=None,
            matchers=silences.build_matchers(
                {"environment": "dev"},
                project_key=context["project_key"],
                alert_name="HighErrorRate",
            ),
            seconds=900,
            reason_code="planned_maintenance",
            reason_note="database migration",
            actor_identity_id=uuidlib.UUID(str(identity)),
            key=uuidlib.uuid4().hex,
        )

    await silences.process_pending(
        engine, settings, transport=httpx.MockTransport(accept), resolver=resolver
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT state, provider_silence_id, starts_at, ends_at "
                    "FROM silence_requests WHERE id = :id"
                ),
                {"id": silence_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "active"
    assert row[1] == "prov-1234"
    assert (row[3] - row[2]).total_seconds() == pytest.approx(900, abs=2)
    # `createdBy` is a stable internal reference, never an email or a name.
    assert captured["body"]["createdBy"].startswith("drake:")
    assert "@" not in captured["body"]["createdBy"]
    assert all(not matcher["isRegex"] for matcher in captured["body"]["matchers"])


# ===========================================================================
# acknowledge, assign, and scope
# ===========================================================================


@pytest.mark.anyio
async def test_acknowledge_and_silence_do_not_become_each_other(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """Acknowledging says a human saw it. Silencing stops a notification.

    Neither implies the other, and a silence must never mark an incident as
    seen — that is how an outage ends up owned by nobody.
    """
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    await deliver(engine, context, [alert_payload(labels=labels_for(context))])

    async with engine.begin() as connection:
        incident_id, version = (
            await connection.execute(text("SELECT id, version FROM incidents"))
        ).first()
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'service', 'T', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()

    from drake_api.incidents.processor import acknowledge_incident

    async with engine.begin() as connection:
        result = await acknowledge_incident(
            connection, incident_id, uuidlib.UUID(str(identity)), version
        )
    assert result["outcome"] == "acknowledged"

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT state, acknowledged_at FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).first()
        silenced = (
            await connection.execute(text("SELECT silenced FROM alert_instances"))
        ).scalar_one()
    assert row is not None
    assert row[0] == "acknowledged" and row[1] is not None
    # Acknowledging changed nothing about suppression.
    assert silenced is False


@pytest.mark.anyio
async def test_assignment_is_idempotent_and_rejects_a_stale_version(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    await deliver(engine, context, [alert_payload(labels=labels_for(context))])

    async with engine.connect() as connection:
        incident_id, version = (
            await connection.execute(text("SELECT id, version FROM incidents"))
        ).first()
    async with engine.begin() as connection:
        actor = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'service', 'A', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()

    async with engine.begin() as connection:
        first = await assign_incident(
            connection,
            incident_id,
            assignee_id=uuidlib.UUID(str(actor)),
            actor_identity_id=uuidlib.UUID(str(actor)),
            expected_version=version,
        )
    assert first["outcome"] == "assigned"

    # A safe retry with the version the caller held.
    async with engine.begin() as connection:
        retry = await assign_incident(
            connection,
            incident_id,
            assignee_id=uuidlib.UUID(str(actor)),
            actor_identity_id=uuidlib.UUID(str(actor)),
            expected_version=version,
        )
    assert retry["outcome"] == "unchanged"

    # A genuinely stale version, proposing a different owner.
    async with engine.begin() as connection:
        stale = await assign_incident(
            connection,
            incident_id,
            assignee_id=None,
            actor_identity_id=uuidlib.UUID(str(actor)),
            expected_version=version - 1,
        )
    assert stale["outcome"] == "conflict"

    async with engine.connect() as connection:
        events = [
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT event_type FROM incident_events ORDER BY occurred_at")
                )
            ).all()
        ]
    # Exactly one assignment event, despite three calls.
    assert events.count("assigned") == 1


@pytest.mark.anyio
async def test_a_caller_outside_scope_sees_no_alert_no_count_and_no_slo(
    engine: AsyncEngine, migrated_db: None
) -> None:
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    await deliver(engine, context, [alert_payload(labels=labels_for(context))])
    await ensure_definition(
        engine,
        project_key=context["project_key"],
        slo_key="availability.30d",
        display_name="API availability",
        indicator="availability",
        objective_ratio=0.999,
        window_seconds=MONTH,
        environment_key=context["environment_key"],
        service_key=context["service_key"],
    )

    from drake_api.rbac.service import Principal

    async with engine.begin() as connection:
        stranger = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, identity_type, display_name, "
                    "email) VALUES ('urn:test', :s, 'user', 'Stranger', '') RETURNING id"
                ),
                {"s": uuidlib.uuid4().hex},
            )
        ).scalar_one()

    principal = Principal(identity_id=uuidlib.UUID(str(stranger)), issuer="urn:test")
    async with engine.connect() as connection:
        alerts = await alert_repo.list_alerts(connection, principal)
        summary = await alert_repo.alert_summary(connection, principal)
        slos = await alert_repo.list_slos(connection, principal)
        silence_page = await alert_repo.list_silences(connection, principal)
        alert_id = (await connection.execute(text("SELECT id FROM alert_instances"))).scalar_one()
        detail = await alert_repo.get_alert(connection, principal, alert_id)

    # Not merely an empty list: the totals are zero too, so nothing leaks
    # through a count.
    assert alerts["items"] == [] and alerts["total"] == 0
    assert summary == {
        "firing": 0,
        "p1": 0,
        "p2": 0,
        "silenced": 0,
        "unmapped": 0,
        "with_incident": 0,
    }
    assert slos["items"] == [] and slos["total"] == 0
    assert silence_page["items"] == [] and silence_page["total"] == 0
    assert detail is None


@pytest.mark.anyio
async def test_an_authorized_caller_sees_both_axes_of_the_alert(
    engine: AsyncEngine, migrated_db: None
) -> None:
    """The read path end to end, through the real API and a real session."""
    world = await make_world(engine)
    context = await make_alertmanager(engine, world)
    await deliver(engine, context, [alert_payload(labels=labels_for(context))])
    await ensure_definition(
        engine,
        project_key=context["project_key"],
        slo_key="availability.30d",
        display_name="API availability",
        indicator="availability",
        objective_ratio=0.999,
        window_seconds=MONTH,
        environment_key=context["environment_key"],
        service_key=context["service_key"],
    )

    harness: S1Harness = build_harness()
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

        alerts = await client.get("/v1/alerts")
        summary = await client.get("/v1/alerts/summary")
        filters = await client.get("/v1/alerting/filters")
        slos = await client.get("/v1/slo")

    assert alerts.status_code == 200
    body = alerts.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["status"] == "firing" and row["priority"] == "P1"
    assert row["incident"] is not None
    # A prefix, never the full provider fingerprint.
    assert len(row["fingerprint_prefix"]) <= 12
    serialized = json.dumps(body)
    assert "://" not in serialized
    assert "authorization" not in serialized.lower()

    assert summary.json()["p1"] == 1
    assert set(filters.json()) >= {"alert_statuses", "severities", "slo_states"}

    slo_body = slos.json()
    assert slo_body["total"] == 1
    # Nothing has been evaluated, so there is no verdict — never a
    # comfortable default.
    assert slo_body["items"][0]["evaluation"] is None
    assert slo_body["items"][0]["objective_ratio"] == pytest.approx(0.999)


# ===========================================================================
# the independent base notification gate
# ===========================================================================


def test_the_route_fixture_keeps_an_independent_base_receiver() -> None:
    """Drake observes; it must not intercept.

    The property under test is `continue: true` on Drake's route. Without
    it, matching Drake's route consumes the alert and the base receiver is
    never called — which would make Drake a single point of failure for
    paging, exactly what this sprint must not build.
    """
    import re

    from drake_api.alerting.contracts import ROUTE_FIXTURE_PATH

    document = ROUTE_FIXTURE_PATH.read_text()

    drake_route = re.search(r"- receiver: drake-webhook\n(?P<body>(?:\s{6}.*\n)+)", document)
    assert drake_route is not None
    assert "continue: true" in drake_route.group("body")

    # The base receiver exists, is not a Drake endpoint, and is reached by
    # its own route.
    assert "name: base-oncall" in document
    assert re.search(r"- receiver: base-oncall\b", document)
    base_block = document[document.index("- name: base-oncall") :]
    assert "drake" not in base_block.split("- name:")[1].lower()

    # Grouping, dedupe and inhibition stay in Alertmanager.
    assert "group_by:" in document and "inhibit_rules:" in document

    # No production URL, credential or token in the repository.
    assert "insecure_skip_verify: false" in document
    assert "credentials_file:" in document
    assert re.search(r"credentials:\s*['\"]?[A-Za-z0-9]", document) is None
    for line in document.splitlines():
        if "url:" in line:
            assert ".invalid" in line, line


def test_the_prometheus_rule_fixture_declares_the_label_contract() -> None:
    from drake_api.alerting.contracts import RULES_FIXTURE_PATH

    document = RULES_FIXTURE_PATH.read_text()
    for label in ("project", "environment", "service", "severity"):
        assert f"{label}:" in document
    # Multi-window burn rules pair a long and a short window.
    assert "long_window:" in document and "short_window:" in document
    assert "14.4" in document
    # And nothing that would let a rule smuggle a link into Drake.
    assert "generatorURL" not in document.replace(
        "# `generatorURL` and `externalURL` are ignored.", ""
    )


def test_the_slo_contract_carries_no_url_or_promql() -> None:
    from drake_api.alerting.contracts import load_contract

    document = load_contract()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.lower() not in {"url", "token", "password", "secret", "expr"}
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            assert "://" not in node
            # A PromQL fragment would mean the profile is a query, not a
            # policy.
            assert "rate(" not in node and "{" not in node

    walk(document)
