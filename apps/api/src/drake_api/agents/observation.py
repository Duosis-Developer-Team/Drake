"""Server-derived cluster capability observation (ADR-0017 §4).

Three independent axes, each honest: agent CONNECTIVITY (heartbeat age),
inventory FRESHNESS (last completed snapshot / applied event age), and
workload HEALTH (stored per resource). A heartbeat alone never makes
inventory fresh; a fresh-looking state older than the staleness bound is
reported stale; no agent at all is `not_configured` — never unknown-as-ok.
"""

import os
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _bound(name: str, default: int) -> int:
    """Deterministic default with an ops override; E2E shrinks the windows
    to observe disconnect/stale transitions in seconds instead of minutes."""
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# Connectivity/freshness bounds: heartbeats are expected every 30s
# (3 missed → disconnected); inventory activity is expected continuously
# (15 min silence → stale).
HEARTBEAT_STALE_SECONDS = _bound("DRAKE_AGENT_HEARTBEAT_STALE_SECONDS", 90)
INVENTORY_STALE_SECONDS = _bound("DRAKE_AGENT_INVENTORY_STALE_SECONDS", 900)
CERT_EXPIRY_WARNING_SECONDS = 5 * 24 * 3600


def _not_configured() -> dict[str, Any]:
    return {
        "agent": {"status": "not_configured"},
        "inventory": {"state": "not_configured"},
    }


async def agent_observations(
    connection: AsyncConnection, cluster_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Batch observation for a page of clusters — one query set, no N+1."""
    observations: dict[uuid.UUID, dict[str, Any]] = {
        cluster_id: _not_configured() for cluster_id in cluster_ids
    }
    if not cluster_ids:
        return observations

    rows = (
        await connection.execute(
            text(
                """
                SELECT DISTINCT ON (cluster_id)
                    cluster_id, id, agent_version, lifecycle, last_heartbeat_at,
                    inventory_state, last_reconcile_at, certificate_not_after,
                    EXTRACT(EPOCH FROM (now() - last_heartbeat_at)) AS heartbeat_age,
                    EXTRACT(EPOCH FROM (now() - last_reconcile_at)) AS reconcile_age,
                    EXTRACT(EPOCH FROM (certificate_not_after - now())) AS cert_remaining
                FROM cluster_agents
                WHERE cluster_id = ANY(:cluster_ids)
                ORDER BY cluster_id, created_at DESC
                """
            ),
            {"cluster_ids": cluster_ids},
        )
    ).all()

    event_rows = (
        await connection.execute(
            text(
                """
                SELECT cluster_id, max(occurred_at),
                       EXTRACT(EPOCH FROM (now() - max(occurred_at)))
                FROM inventory_change_events
                WHERE cluster_id = ANY(:cluster_ids)
                GROUP BY cluster_id
                """
            ),
            {"cluster_ids": cluster_ids},
        )
    ).all()
    last_events = {row[0]: (row[1], float(row[2])) for row in event_rows}

    for row in rows:
        cluster_id = row[0]
        lifecycle = str(row[3])
        heartbeat_age = float(row[8]) if row[8] is not None else None
        if lifecycle != "active":
            agent_status = "revoked"
        elif heartbeat_age is None:
            agent_status = "enrolled"  # never heard from yet
        elif heartbeat_age <= HEARTBEAT_STALE_SECONDS:
            agent_status = "connected"
        else:
            agent_status = "disconnected"

        cert_remaining = float(row[10]) if row[10] is not None else None
        agent_block: dict[str, Any] = {
            "status": agent_status,
            "agent_version": row[2] or None,
            "last_heartbeat_at": row[4].isoformat() if row[4] else None,
            "certificate_not_after": row[7].isoformat() if row[7] else None,
            "certificate_expiry_warning": bool(
                cert_remaining is not None and cert_remaining < CERT_EXPIRY_WARNING_SECONDS
            ),
        }

        reported_state = str(row[5])
        reconcile_age = float(row[9]) if row[9] is not None else None
        event_at, event_age = last_events.get(cluster_id, (None, None))
        ages = [age for age in (reconcile_age, event_age) if age is not None]
        activity_age = min(ages) if ages else None
        # Freshness derives from INVENTORY activity, never from heartbeats:
        # a chatty agent with a silent inventory is stale, honestly.
        if reported_state == "fresh" and (
            activity_age is None or activity_age > INVENTORY_STALE_SECONDS
        ):
            state = "stale"
        else:
            state = reported_state
        observations[cluster_id] = {
            "agent": agent_block,
            "inventory": {
                "state": state,
                "last_reconcile_at": row[6].isoformat() if row[6] else None,
                "last_event_at": event_at.isoformat() if event_at else None,
            },
        }
    return observations
