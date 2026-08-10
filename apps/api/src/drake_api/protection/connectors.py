"""Loading the connector contract, and seeding the policies it declares.

A reporter may only send evidence for a policy Drake already knows about.
That is deliberate: a policy carries the promises an assessment is made
against — RPO, offsite requirement, restore-verification lifetime — and a
reporter does not get to set the bar it will be judged by.

The contract file is versioned and reviewed, exactly like the telemetry
registry. It contains no URL, no credential and no storage path, because
the runtime registry (settings) holds the connector's secret reference and
the site keys are opaque handles.
"""

import json
import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("drake_api.protection.connectors")

CONTRACT_PATH = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "contracts"
    / "protection"
    / "connectors.v1.json"
)


@lru_cache
def load_contract(path: str | None = None) -> dict[str, Any]:
    """Read the versioned connector contract, fail-closed on malformation."""
    source = Path(path) if path else CONTRACT_PATH
    document: dict[str, Any] = json.loads(source.read_text())
    if int(document.get("schemaVersion", 0)) != 1:
        raise ValueError("unsupported protection connector contract version")
    for connector in document.get("connectors", []):
        if not connector.get("connectorKey") or not connector.get("projectKey"):
            raise ValueError("connector contract entry is missing its identity")
    return document


def sites_for(connector_key: str, path: str | None = None) -> dict[str, dict[str, Any]]:
    """The site registry for a connector: opaque keys, never destinations."""
    for connector in load_contract(path).get("connectors", []):
        if connector["connectorKey"] == connector_key:
            return {site["siteKey"]: site for site in connector.get("sites", [])}
    return {}


async def seed_policies(
    engine: AsyncEngine, *, path: str | None = None, connector_key: str | None = None
) -> int:
    """Create the declared policies, idempotently.

    Matched to the catalog by project and environment KEY, so a policy
    lands on the project that already exists rather than creating one. A
    declared policy whose project is not in the catalog is skipped, not
    invented — the catalog stays the source of truth for what exists.
    """
    document = load_contract(path)
    created = 0

    for connector in document.get("connectors", []):
        if connector_key is not None and connector["connectorKey"] != connector_key:
            continue
        async with engine.begin() as connection:
            project_id = (
                await connection.execute(
                    text("SELECT id FROM projects WHERE project_key = :key"),
                    {"key": connector["projectKey"]},
                )
            ).scalar_one_or_none()
            if project_id is None:
                logger.info("protection contract names a project not in the catalog; skipping")
                continue

            for policy in connector.get("policies", []):
                environment_id = None
                if policy.get("environmentKey"):
                    environment_id = (
                        await connection.execute(
                            text(
                                "SELECT id FROM environments "
                                "WHERE project_id = :project AND environment_key = :key"
                            ),
                            {"project": project_id, "key": policy["environmentKey"]},
                        )
                    ).scalar_one_or_none()

                inserted = (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO backup_policies
                                (connector_key, policy_external_key, display_name, project_id,
                                 environment_id, store_key, store_kind, provider_key,
                                 schedule_description, rpo_seconds, rto_seconds,
                                 retention_seconds, requires_offsite, requires_integrity_check,
                                 restore_verification_ttl_seconds)
                            VALUES (:connector, :key, :name, :project, :environment, :store,
                                    :store_kind, :provider, :schedule, :rpo, :rto, :retention,
                                    :offsite, :integrity, :restore_ttl)
                            ON CONFLICT (connector_key, policy_external_key) DO NOTHING
                            RETURNING id
                            """
                        ),
                        {
                            "connector": connector["connectorKey"],
                            "key": policy["policyExternalKey"],
                            "name": policy["displayName"],
                            "project": project_id,
                            "environment": environment_id,
                            "store": policy["storeKey"],
                            "store_kind": policy.get("storeKind", "postgresql"),
                            "provider": policy["providerKey"],
                            "schedule": policy.get("scheduleDescription"),
                            "rpo": policy["rpoSeconds"],
                            "rto": policy.get("rtoSeconds"),
                            "retention": policy.get("retentionSeconds"),
                            "offsite": policy.get("requiresOffsite", False),
                            "integrity": policy.get("requiresIntegrityCheck", False),
                            "restore_ttl": policy.get("restoreVerificationTtlSeconds"),
                        },
                    )
                ).first()
                if inserted is not None:
                    created += 1
    return created


def policy_keys(connector_key: str, path: str | None = None) -> list[str]:
    for connector in load_contract(path).get("connectors", []):
        if connector["connectorKey"] == connector_key:
            return [policy["policyExternalKey"] for policy in connector["policies"]]
    return []


def as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
