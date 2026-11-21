"""What it takes for backup evidence to arrive, and the loop that judges it.

The Protection screen was empty in production for three separate reasons,
and none of them was visible on it: no reporter was registered in this
deployment's settings, the policies declared in the reviewed contract were
never created (nothing seeded them), and nothing judged a policy except an
arriving event — so a reporter that simply stopped would never have been
noticed as "overdue".

`connector_status` says, per declared reporter, which of those holds — for
reporters on projects the caller may see, and with no secret, URL or path
in it. `ProtectionEvaluator` seeds the declared policies of every reporter
this deployment has registered, then judges every policy on a timer, so
silence becomes "overdue" on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.catalog.authz import visible_scope_ids
from drake_api.protection import ingest
from drake_api.protection.connectors import load_contract, seed_policies
from drake_api.protection.service import evaluate_all
from drake_api.rbac.service import Principal
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.protection.setup")

LEASE_KEY = "protection:evaluation:cycle"
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

SIGNING = {
    "connector_header": ingest.CONNECTOR_HEADER,
    "timestamp_header": ingest.TIMESTAMP_HEADER,
    "signature_header": ingest.SIGNATURE_HEADER,
    # Stated as a recipe, not code: HMAC-SHA256 with the reporter's own
    # secret over "<unix seconds>.<raw body>", sent as "v1=<hex>".
    "algorithm": "hmac-sha256",
    "signed_material": "<unix-seconds>.<raw request body>",
    "signature_format": "v1=<hex digest>",
    "endpoint": "/v1/protection/ingest/events",
    "event_types": sorted(ingest.EVENT_TYPES),
}


def _secret_present(settings: Settings, key: str) -> bool:
    connector = settings.protection_connectors.get(key)
    if connector is None or not connector.signing_secret_file:
        return False
    try:
        return Path(connector.signing_secret_file).is_file()
    except OSError:
        return False


async def connector_status(
    connection: AsyncConnection, principal: Principal, settings: Settings
) -> dict[str, Any]:
    protection = await visible_scope_ids(connection, principal, "protection.view")
    projects = await visible_scope_ids(connection, principal, "project.view")
    visible = set(protection) & set(projects)
    rows = (
        await connection.execute(
            text("SELECT project_key, id FROM projects WHERE scope_id = ANY(:scopes)"),
            {"scopes": list(visible) or [uuid.UUID(int=0)]},
        )
    ).all()
    seeable = {str(r[0]): str(r[1]) for r in rows}
    seeded = {
        (str(r[0]), str(r[1]))
        for r in (
            await connection.execute(
                text("SELECT connector_key, policy_external_key FROM backup_policies")
            )
        ).all()
    }
    reporters: list[dict[str, Any]] = []
    for connector in load_contract().get("connectors", []):
        key = str(connector["connectorKey"])
        project = str(connector["projectKey"])
        if project not in seeable:
            continue
        registered = key in settings.protection_connectors
        seen = await ingest.reporter_seen_at(connection, key)
        reporters.append(
            {
                "connector_key": key,
                "display_name": connector.get("displayName") or key,
                "project_key": project,
                "project_id": seeable[project],
                "registered": registered,
                "secret_present": _secret_present(settings, key),
                "last_seen_at": seen.isoformat() if seen else None,
                "state": (
                    "reporting"
                    if seen is not None
                    else "waiting"
                    if registered and _secret_present(settings, key)
                    else "not_registered"
                ),
                "policies": [
                    {
                        "policy_key": p["policyExternalKey"],
                        "display_name": p.get("displayName") or p["policyExternalKey"],
                        "environment_key": p.get("environmentKey"),
                        "store_kind": p.get("storeKind"),
                        "schedule": p.get("scheduleDescription"),
                        "rpo_seconds": p.get("rpoSeconds"),
                        "requires_offsite": bool(p.get("requiresOffsite")),
                        "requires_integrity_check": bool(p.get("requiresIntegrityCheck")),
                        "created": (key, str(p["policyExternalKey"])) in seeded,
                    }
                    for p in connector.get("policies", [])
                ],
            }
        )
    return {
        "evaluating": settings.protection_evaluation_enabled,
        "reporters": reporters,
        "signing": SIGNING,
    }


async def run_once(
    engine: AsyncEngine, settings: Settings, redis: aioredis.Redis, *, lease_seconds: int = 300
) -> int:
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        logger.warning("protection evaluator: lease unavailable, skipping")
        return 0
    if not acquired:
        return 0
    try:
        for key in settings.protection_connectors:
            try:
                await seed_policies(engine, connector_key=key)
            except Exception:
                logger.warning("protection evaluator: declared policies could not be created")
        return len(await evaluate_all(engine, settings))
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)


class ProtectionEvaluator:
    """Lifespan-owned loop; exists only when evaluation is enabled."""

    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        redis: aioredis.Redis,
        *,
        interval_seconds: float = 300.0,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._redis = redis
        self._interval = max(60.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="protection-evaluation")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_once(self._engine, self._settings, self._redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("protection evaluator: cycle failed")
            await asyncio.sleep(self._interval)
