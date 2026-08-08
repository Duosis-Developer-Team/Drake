"""Alert, SLO and silence reads: scope-filtered, bounded, provider-free.

Two rules shape every query here, both inherited rather than reinvented.

**Scope filtering happens in SQL, before counting or paging.** A caller who
cannot see a project cannot learn an alert exists from a total, a filter
option, or a cursor.

**Filters are an allowlist.** Every one is an id, an enum member or a fixed
window. There is no field through which a caller supplies SQL, a regex, a
matcher or a query expression, so there is nothing to sanitize.

No response here carries a raw webhook body, an authorization header, a
provider exception, `generatorURL`, `externalURL`, an annotation URL, a
PromQL expression, or an Alertmanager address. There are no columns for
most of them, and the rest never leave this module.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.alerting.contracts import indicator_measurement, silence_reason_codes
from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

# Reading alerts is its own right on top of being able to see the project:
# an alert names what is currently broken and how badly.
ALERT_PERMISSION = "alert.view"
SLO_PERMISSION = "slo.view"
PROJECT_PERMISSION = "environment.view"
# Unmapped alerts are integration evidence, not project evidence: they
# belong to nobody's project by definition, so only an operator responsible
# for the integration itself may see them.
PLATFORM_PERMISSION = "integration.manage"
SILENCE_PERMISSION = "alert.silence"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_TIMELINE = 50

ALERT_STATUSES = frozenset({"firing", "resolved"})
SEVERITIES = frozenset({"critical", "high", "medium", "info", "unknown"})
PRIORITIES = frozenset({"P1", "P2", "P3", "P4"})
MAPPING_STATES = frozenset({"mapped", "unmapped", "ambiguous"})
SLO_STATES = frozenset(
    {
        "healthy",
        "warning",
        "critical",
        "exhausted",
        "insufficient_data",
        "stale",
        "query_failed",
        "not_configured",
    }
)
SILENCE_STATES = frozenset(
    {"pending", "active", "expired", "failed", "cancel_pending", "cancelled"}
)
INDICATORS = frozenset({"availability", "latency"})
WINDOWS: dict[str, int] = {"1h": 3_600, "24h": 86_400, "7d": 604_800, "30d": 2_592_000}


class FilterError(ValueError):
    """A filter value outside the allowlist (422)."""


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    """Never let an empty visibility set become an unfiltered query."""
    return list(ids) or [uuid.UUID(int=0)]


async def alert_scopes(connection: AsyncConnection, principal: Principal) -> list[uuid.UUID]:
    """Both rights are required, so the visible set is their intersection."""
    alerts = await visible_scope_ids(connection, principal, ALERT_PERMISSION)
    projects = await visible_scope_ids(connection, principal, PROJECT_PERMISSION)
    return _sentinel(alerts & projects)


async def slo_scopes(connection: AsyncConnection, principal: Principal) -> list[uuid.UUID]:
    slos = await visible_scope_ids(connection, principal, SLO_PERMISSION)
    projects = await visible_scope_ids(connection, principal, PROJECT_PERMISSION)
    return _sentinel(slos & projects)


async def sees_unmapped(connection: AsyncConnection, principal: Principal) -> bool:
    return bool(await visible_scope_ids(connection, principal, PLATFORM_PERMISSION))


def _check(value: str | None, allowed: frozenset[str] | dict[str, int], name: str) -> None:
    if value is not None and value not in allowed:
        raise FilterError(f"unsupported {name}")


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

_ALERT_COLUMNS = """
    a.id, a.fingerprint, a.alert_name, a.status, a.severity, a.priority,
    a.mapping_state, a.mapping_error_code, a.owner_team, a.slo_key, a.runbook_key,
    a.starts_at, a.ends_at, a.last_seen_at, a.source_event_at, a.ingested_at,
    a.resolved_at, a.labels, a.annotations, a.occurrence, a.silenced, a.inhibited,
    a.incident_id, a.namespace, a.version,
    p.project_key, e.environment_key, sd.service_key, c.cluster_ref,
    i.state, i.severity, i.priority, i.title, i.acknowledged_at, i.assigned_at
"""

_ALERT_JOINS = """
    FROM alert_instances a
    LEFT JOIN projects p ON p.id = a.project_id
    LEFT JOIN environments e ON e.id = a.environment_id
    LEFT JOIN service_definitions sd ON sd.id = a.service_id
    LEFT JOIN clusters c ON c.id = a.cluster_id
    LEFT JOIN incidents i ON i.id = a.incident_id
    JOIN integrations ig ON ig.id = a.integration_id
"""


def _alert_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        # A short prefix. The full fingerprint is Alertmanager's identity for
        # the alert and is only needed server-side.
        "fingerprint_prefix": str(row[1])[:12],
        "alert_name": row[2],
        "status": row[3],
        "severity": row[4],
        "priority": row[5],
        "mapping_state": row[6],
        "mapping_error_code": row[7],
        "owner_team": row[8],
        "slo_key": row[9],
        # A KEY into a reviewed runbook registry, never a URL.
        "runbook_key": row[10],
        "starts_at": row[11].isoformat(),
        "ends_at": row[12].isoformat() if row[12] else None,
        "last_seen_at": row[13].isoformat(),
        "source_event_at": row[14].isoformat(),
        # When Drake HEARD it, as distinct from when it happened.
        "ingested_at": row[15].isoformat(),
        "resolved_at": row[16].isoformat() if row[16] else None,
        "labels": dict(row[17] or {}),
        "annotations": dict(row[18] or {}),
        "occurrence": row[19],
        "silenced": row[20],
        "inhibited": row[21],
        "namespace": row[23],
        "version": row[24],
        "project_key": row[25],
        "environment_key": row[26],
        "service_key": row[27],
        "cluster_ref": row[28],
        "incident": (
            None
            if row[22] is None
            else {
                "id": str(row[22]),
                "state": row[29],
                "severity": row[30],
                "priority": row[31],
                "title": row[32],
                "acknowledged_at": row[33].isoformat() if row[33] else None,
                "assigned_at": row[34].isoformat() if row[34] else None,
            }
        ),
    }


async def list_alerts(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    status: str | None = None,
    severity: str | None = None,
    priority: str | None = None,
    mapping_state: str | None = None,
    silenced: bool | None = None,
    window: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    _check(status, ALERT_STATUSES, "status")
    _check(severity, SEVERITIES, "severity")
    _check(priority, PRIORITIES, "priority")
    _check(mapping_state, MAPPING_STATES, "mapping state")
    _check(window, WINDOWS, "window")

    scopes = await alert_scopes(connection, principal)
    platform = await sees_unmapped(connection, principal)

    # Mapped alerts follow their project's visibility. Unmapped ones belong
    # to no project, so they are visible only to whoever runs the
    # integration — and to nobody else, at all.
    visibility = "(p.scope_id = ANY(:scopes)"
    if platform:
        visibility += " OR a.mapping_state <> 'mapped'"
    visibility += ")"

    conditions = [visibility]
    params: dict[str, Any] = {"scopes": scopes, "limit": limit, "offset": offset}
    if project_id is not None:
        conditions.append("a.project_id = :project")
        params["project"] = project_id
    if environment_id is not None:
        conditions.append("a.environment_id = :environment")
        params["environment"] = environment_id
    if service_id is not None:
        conditions.append("a.service_id = :service")
        params["service"] = service_id
    if cluster_id is not None:
        conditions.append("a.cluster_id = :cluster")
        params["cluster"] = cluster_id
    if status is not None:
        conditions.append("a.status = :status")
        params["status"] = status
    if severity is not None:
        conditions.append("a.severity = :severity")
        params["severity"] = severity
    if priority is not None:
        conditions.append("a.priority = :priority")
        params["priority"] = priority
    if mapping_state is not None:
        conditions.append("a.mapping_state = :mapping_state")
        params["mapping_state"] = mapping_state
    if silenced is not None:
        conditions.append("a.silenced = :silenced")
        params["silenced"] = silenced
    if window is not None:
        conditions.append("a.last_seen_at >= now() - make_interval(secs => :window)")
        params["window"] = WINDOWS[window]

    where = " AND ".join(conditions)
    # Counted over the SAME predicate as the page, so a total can never
    # hint at a row the list will not show.
    total = (
        await connection.execute(
            text(f"SELECT count(*) {_ALERT_JOINS} WHERE {where}"),
            params,
        )
    ).scalar_one()
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_ALERT_COLUMNS} {_ALERT_JOINS}
                WHERE {where}
                ORDER BY a.last_seen_at DESC, a.id
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    return {
        "items": [_alert_row(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


async def alert_summary(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    scopes = await alert_scopes(connection, principal)
    platform = await sees_unmapped(connection, principal)
    visibility = "(p.scope_id = ANY(:scopes)"
    if platform:
        visibility += " OR a.mapping_state <> 'mapped'"
    visibility += ")"
    row = (
        await connection.execute(
            text(
                f"""
                SELECT
                    count(*) FILTER (WHERE a.status = 'firing') AS firing,
                    count(*) FILTER (WHERE a.status = 'firing' AND a.priority = 'P1') AS p1,
                    count(*) FILTER (WHERE a.status = 'firing' AND a.priority = 'P2') AS p2,
                    count(*) FILTER (WHERE a.status = 'firing' AND a.silenced) AS silenced,
                    count(*) FILTER (WHERE a.mapping_state <> 'mapped') AS unmapped,
                    count(*) FILTER (WHERE a.incident_id IS NOT NULL
                                       AND a.status = 'firing') AS with_incident
                {_ALERT_JOINS}
                WHERE {visibility}
                """
            ),
            {"scopes": scopes},
        )
    ).first()
    assert row is not None
    return {
        "firing": int(row[0]),
        "p1": int(row[1]),
        "p2": int(row[2]),
        "silenced": int(row[3]),
        # Only meaningful for a platform operator; zero for everyone else
        # because the predicate above excluded them entirely.
        "unmapped": int(row[4]),
        "with_incident": int(row[5]),
    }


async def get_alert(
    connection: AsyncConnection, principal: Principal, alert_id: uuid.UUID
) -> dict[str, Any] | None:
    scopes = await alert_scopes(connection, principal)
    platform = await sees_unmapped(connection, principal)
    visibility = "(p.scope_id = ANY(:scopes)"
    if platform:
        visibility += " OR a.mapping_state <> 'mapped'"
    visibility += ")"
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_ALERT_COLUMNS} {_ALERT_JOINS}
                WHERE a.id = :id AND {visibility}
                """
            ),
            {"id": alert_id, "scopes": scopes},
        )
    ).first()
    return None if row is None else _alert_row(row)


async def alert_events(
    connection: AsyncConnection, principal: Principal, alert_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    if await get_alert(connection, principal, alert_id) is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT event_type, status, occurrence, source_event_at, received_at, detail
                FROM alert_events
                WHERE alert_instance_id = :id
                ORDER BY source_event_at DESC, received_at DESC
                LIMIT :limit
                """
            ),
            {"id": alert_id, "limit": MAX_TIMELINE},
        )
    ).all()
    return [
        {
            "event_type": row[0],
            "status": row[1],
            "occurrence": row[2],
            "source_event_at": row[3].isoformat(),
            "received_at": row[4].isoformat(),
            "detail": dict(row[5] or {}),
        }
        for row in rows
    ]


async def alert_silences(
    connection: AsyncConnection, principal: Principal, alert_id: uuid.UUID
) -> list[dict[str, Any]]:
    if await get_alert(connection, principal, alert_id) is None:
        return []
    rows = (
        await connection.execute(
            text(
                """
                SELECT id, state, reason_code, reason_note, requested_seconds,
                       requested_at, starts_at, ends_at, error_code, version
                FROM silence_requests
                WHERE alert_instance_id = :id
                ORDER BY requested_at DESC
                LIMIT :limit
                """
            ),
            {"id": alert_id, "limit": MAX_TIMELINE},
        )
    ).all()
    return [_silence_row(row) for row in rows]


# ---------------------------------------------------------------------------
# SLOs
# ---------------------------------------------------------------------------

_SLO_COLUMNS = """
    d.id, d.slo_key, d.display_name, d.indicator, d.objective_ratio, d.window_seconds,
    d.threshold_profile_key, d.burn_profile_key, d.enabled, d.version,
    p.project_key, e.environment_key, sd.service_key, d.project_id, d.environment_id,
    ev.status, ev.data_quality, ev.compliance_ratio, ev.error_budget_total,
    ev.error_budget_consumed, ev.error_budget_remaining, ev.burn_rates,
    ev.evaluated_for, ev.window_start, ev.window_end, ev.freshness_seconds,
    ev.error_code, ev.sample_count, ev.objective_ratio, ev.definition_version
"""

_SLO_JOINS = """
    FROM slo_definitions d
    JOIN projects p ON p.id = d.project_id
    LEFT JOIN environments e ON e.id = d.environment_id
    LEFT JOIN service_definitions sd ON sd.id = d.service_id
    LEFT JOIN LATERAL (
        SELECT * FROM slo_evaluations x
        WHERE x.slo_definition_id = d.id
        ORDER BY x.evaluated_for DESC, x.ingested_at DESC
        LIMIT 1
    ) ev ON true
"""


def _slo_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "slo_key": row[1],
        "display_name": row[2],
        "indicator": row[3],
        # Always a ratio. The UI formats a percentage; nothing computes one.
        "objective_ratio": float(row[4]),
        "window_seconds": row[5],
        "threshold_profile_key": row[6],
        "burn_profile_key": row[7],
        "enabled": row[8],
        "version": row[9],
        "project_key": row[10],
        "environment_key": row[11],
        "service_key": row[12],
        "project_id": str(row[13]),
        "environment_id": str(row[14]) if row[14] else None,
        # How this number is actually produced. A compliance figure whose
        # method is invisible invites a stronger reading than it deserves.
        "measurement": indicator_measurement(str(row[3])),
        # `null` when nothing has been evaluated. Never a cheerful default.
        "evaluation": (
            None
            if row[15] is None
            else {
                "status": row[15],
                "data_quality": row[16],
                "compliance_ratio": _float(row[17]),
                "error_budget_total": _float(row[18]),
                "error_budget_consumed": _float(row[19]),
                # May be negative, deliberately.
                "error_budget_remaining": _float(row[20]),
                "burn_rates": list(row[21] or ()),
                "evaluated_for": row[22].isoformat(),
                "window_start": row[23].isoformat(),
                "window_end": row[24].isoformat(),
                "freshness_seconds": row[25],
                "error_code": row[26],
                "sample_count": row[27],
                # The objective this evaluation was JUDGED against, which is
                # not necessarily the one configured today.
                "objective_ratio": float(row[28]),
                "definition_version": row[29],
            }
        ),
    }


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


async def list_slos(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    indicator: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    _check(indicator, INDICATORS, "indicator")
    _check(status, SLO_STATES, "SLO state")

    scopes = await slo_scopes(connection, principal)
    conditions = ["p.scope_id = ANY(:scopes)"]
    params: dict[str, Any] = {"scopes": scopes, "limit": limit, "offset": offset}
    if project_id is not None:
        conditions.append("d.project_id = :project")
        params["project"] = project_id
    if environment_id is not None:
        conditions.append("d.environment_id = :environment")
        params["environment"] = environment_id
    if service_id is not None:
        conditions.append("d.service_id = :service")
        params["service"] = service_id
    if indicator is not None:
        conditions.append("d.indicator = :indicator")
        params["indicator"] = indicator
    if status is not None:
        conditions.append("ev.status = :status")
        params["status"] = status

    where = " AND ".join(conditions)
    total = (
        await connection.execute(
            text(f"SELECT count(*) {_SLO_JOINS} WHERE {where}"),
            params,
        )
    ).scalar_one()
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_SLO_COLUMNS} {_SLO_JOINS}
                WHERE {where}
                ORDER BY p.project_key, d.slo_key
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    return {
        "items": [_slo_row(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


async def get_slo(
    connection: AsyncConnection, principal: Principal, slo_id: uuid.UUID
) -> dict[str, Any] | None:
    scopes = await slo_scopes(connection, principal)
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_SLO_COLUMNS} {_SLO_JOINS}
                WHERE d.id = :id AND p.scope_id = ANY(:scopes)
                """
            ),
            {"id": slo_id, "scopes": scopes},
        )
    ).first()
    return None if row is None else _slo_row(row)


async def slo_evaluations(
    connection: AsyncConnection, principal: Principal, slo_id: uuid.UUID, *, limit: int = 30
) -> list[dict[str, Any]] | None:
    if await get_slo(connection, principal, slo_id) is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT evaluated_for, window_start, window_end, status, data_quality,
                       compliance_ratio, error_budget_remaining, error_budget_consumed,
                       burn_rates, objective_ratio, definition_version, sample_count,
                       freshness_seconds, error_code
                FROM slo_evaluations
                WHERE slo_definition_id = :id
                ORDER BY evaluated_for DESC
                LIMIT :limit
                """
            ),
            {"id": slo_id, "limit": min(limit, 100)},
        )
    ).all()
    return [
        {
            "evaluated_for": row[0].isoformat(),
            "window_start": row[1].isoformat(),
            "window_end": row[2].isoformat(),
            "status": row[3],
            "data_quality": row[4],
            "compliance_ratio": _float(row[5]),
            "error_budget_remaining": _float(row[6]),
            "error_budget_consumed": _float(row[7]),
            "burn_rates": list(row[8] or ()),
            "objective_ratio": float(row[9]),
            "definition_version": row[10],
            "sample_count": row[11],
            "freshness_seconds": row[12],
            "error_code": row[13],
        }
        for row in rows
    ]


async def slo_context(
    connection: AsyncConnection, principal: Principal, slo_id: uuid.UUID
) -> dict[str, Any] | None:
    """Nearby deployments, incidents and alerts for one SLO.

    Temporal correlation ONLY. Drake shows what else was happening; it does
    not claim a deployment caused an SLO breach, because it has no evidence
    of causation and saying so would send someone to the wrong place.
    """
    slo = await get_slo(connection, principal, slo_id)
    if slo is None:
        return None
    row = (
        await connection.execute(
            text(
                "SELECT environment_service_id, project_id, environment_id, service_id "
                "FROM slo_definitions WHERE id = :id"
            ),
            {"id": slo_id},
        )
    ).first()
    assert row is not None
    environment_service_id = row[0]

    deployments: list[dict[str, Any]] = []
    if environment_service_id is not None:
        deployments = [
            {
                "id": str(item[0]),
                "observed_at": item[1].isoformat(),
                "rollout_state": item[2],
                "evidence_state": item[3],
                "generation": item[4],
            }
            for item in (
                await connection.execute(
                    text(
                        """
                        SELECT r.id, r.observed_at, r.rollout_state, r.evidence_state,
                               r.generation
                        FROM deployment_revisions r
                        WHERE r.environment_service_id = :es
                        ORDER BY r.observed_at DESC
                        LIMIT 5
                        """
                    ),
                    {"es": environment_service_id},
                )
            ).all()
        ]

    incidents = [
        {
            "id": str(item[0]),
            "state": item[1],
            "severity": item[2],
            "priority": item[3],
            "title": item[4],
            "opened_at": item[5].isoformat(),
        }
        for item in (
            await connection.execute(
                text(
                    """
                    SELECT id, state, severity, priority, title, opened_at
                    FROM incidents
                    WHERE project_id = :project
                      AND (CAST(:environment AS uuid) IS NULL OR environment_id = :environment)
                    ORDER BY opened_at DESC
                    LIMIT 5
                    """
                ),
                {"project": row[1], "environment": row[2]},
            )
        ).all()
    ]

    alerts = [
        {
            "id": str(item[0]),
            "alert_name": item[1],
            "status": item[2],
            "priority": item[3],
            "last_seen_at": item[4].isoformat(),
        }
        for item in (
            await connection.execute(
                text(
                    """
                    SELECT id, alert_name, status, priority, last_seen_at
                    FROM alert_instances
                    WHERE project_id = :project AND status = 'firing'
                      AND (CAST(:environment AS uuid) IS NULL OR environment_id = :environment)
                    ORDER BY last_seen_at DESC
                    LIMIT 5
                    """
                ),
                {"project": row[1], "environment": row[2]},
            )
        ).all()
    ]

    return {
        "deployments": deployments,
        "incidents": incidents,
        "alerts": alerts,
        # Said out loud, in the payload, so a client cannot present this as
        # a causal claim without deliberately ignoring it.
        "correlation_note": (
            "Temporal correlation only. Drake does not claim a deployment caused "
            "an SLO breach."
        ),
    }


# ---------------------------------------------------------------------------
# silences
# ---------------------------------------------------------------------------


def _silence_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "state": row[1],
        "reason_code": row[2],
        "reason_note": row[3],
        "requested_seconds": row[4],
        "requested_at": row[5].isoformat(),
        # Null until the PROVIDER accepted it. A pending silence suppresses
        # nothing, and showing a start time would suggest otherwise.
        "starts_at": row[6].isoformat() if row[6] else None,
        "ends_at": row[7].isoformat() if row[7] else None,
        "error_code": row[8],
        "version": row[9],
    }


async def list_silences(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    state: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    _check(state, SILENCE_STATES, "silence state")
    scopes = await alert_scopes(connection, principal)
    conditions = ["p.scope_id = ANY(:scopes)"]
    params: dict[str, Any] = {"scopes": scopes, "limit": limit, "offset": offset}
    if project_id is not None:
        conditions.append("s.project_id = :project")
        params["project"] = project_id
    if state is not None:
        conditions.append("s.state = :state")
        params["state"] = state
    where = " AND ".join(conditions)
    joins = "FROM silence_requests s JOIN projects p ON p.id = s.project_id"
    total = (
        await connection.execute(
            text(f"SELECT count(*) {joins} WHERE {where}"), params
        )
    ).scalar_one()
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT s.id, s.state, s.reason_code, s.reason_note, s.requested_seconds,
                       s.requested_at, s.starts_at, s.ends_at, s.error_code, s.version,
                       p.project_key, s.alert_instance_id, s.incident_id
                {joins}
                WHERE {where}
                ORDER BY s.requested_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    return {
        "items": [
            {
                **_silence_row(row),
                "project_key": row[10],
                "alert_instance_id": str(row[11]) if row[11] else None,
                "incident_id": str(row[12]) if row[12] else None,
            }
            for row in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


async def can_silence(connection: AsyncConnection, principal: Principal) -> bool:
    return bool(await visible_scope_ids(connection, principal, SILENCE_PERMISSION))


async def silence_authority(
    connection: AsyncConnection, principal: Principal, project_scope_id: uuid.UUID
) -> bool:
    """Silencing is authorised per project, not globally."""
    return project_scope_id in await visible_scope_ids(connection, principal, SILENCE_PERMISSION)


def filter_options() -> dict[str, Any]:
    """The accepted vocabulary — static, so it enumerates nothing."""
    return {
        "alert_statuses": sorted(ALERT_STATUSES),
        "severities": sorted(SEVERITIES),
        "priorities": sorted(PRIORITIES),
        "mapping_states": sorted(MAPPING_STATES),
        "slo_states": sorted(SLO_STATES),
        "silence_states": sorted(SILENCE_STATES),
        "indicators": sorted(INDICATORS),
        "windows": sorted(WINDOWS),
        "silence_reasons": [
            {"key": key, "label": label} for key, label in sorted(silence_reason_codes().items())
        ],
    }
