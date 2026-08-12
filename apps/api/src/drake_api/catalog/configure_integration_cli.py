"""Point a project's integration at a server-owned connector.

    python -m drake_api.catalog.configure_integration_cli \
        --integration-type prometheus \
        --project-key hermes \
        --config-ref duosis-prod-1 \
        --actor <identity-uuid>

Why this exists, and why it is a CLI: onboarding creates a project's
integration rows with no configuration, because a manifest describes a
project and cannot know which metrics backend a platform runs. Connecting
them is a platform decision, made once per provider by whoever operates it
— exactly the shape of decision `register_cluster_cli` exists for, and it
has no API surface for the same reason: there is no screen for it yet, and
inventing one to avoid a 60-line tool is the larger commitment.

What it is not: a way to set arbitrary fields. It takes a reference NAME,
never a URL and never a credential — the address lives in the API's own
settings, keyed by this name, and a ref that looks like a secret is
refused. There is no delete, no disable, and no way to reach a row outside
the named project.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.audit.service import AuditEventData, record_audit_event_in
from drake_api.catalog.service import CatalogService, CatalogValidationError
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.settings import get_settings

# Bounded on purpose: these are the integration types Drake resolves a
# server-owned connector for. A typo should be a refusal, not a row nobody
# reads.
CONFIGURABLE_TYPES = ("prometheus",)


async def _resolve_scope(
    connection: AsyncConnection, project_key: str, cluster_ref: str
) -> tuple[uuid.UUID | None, str]:
    """The scope the integration hangs on, and the name to report it by.

    A cluster-scope integration is not a nicety: Drake resolves a
    cluster-scope telemetry query against the CLUSTER's scope, so capacity
    questions are answered by an integration registered there — a project's
    one cannot serve them however well configured it is.
    """
    if cluster_ref:
        row = (
            await connection.execute(
                text(
                    "SELECT scope_id FROM clusters "
                    "WHERE cluster_ref = :ref AND lifecycle = 'active'"
                ),
                {"ref": cluster_ref},
            )
        ).first()
        return (row[0], f"cluster {cluster_ref}") if row else (None, f"cluster {cluster_ref}")
    row = (
        await connection.execute(
            text("SELECT scope_id FROM projects WHERE project_key = :key AND lifecycle = 'active'"),
            {"key": project_key},
        )
    ).first()
    return (row[0], project_key) if row else (None, project_key)


async def _run(
    integration_type: str, project_key: str, cluster_ref: str, config_ref: str, actor: uuid.UUID
) -> int:
    settings = get_settings()
    engine = get_engine(settings)
    async with engine.begin() as connection:
        known = (
            await connection.execute(text("SELECT 1 FROM identities WHERE id = :id"), {"id": actor})
        ).first()
        if known is None:
            print("refused: the actor identity is not a known Drake identity")
            return 2

        scope_id, target = await _resolve_scope(connection, project_key, cluster_ref)
        if scope_id is None:
            print(f"refused: no active {target}")
            return 2

        service = CatalogService(connection, source_kind="operator")
        changed = await service.configure_integration(integration_type, scope_id, config_ref)
        if not changed:
            # Either it is already pointed here, or there is no such row.
            existing = (
                await connection.execute(
                    text(
                        "SELECT config_ref FROM integrations "
                        "WHERE integration_type = :type AND scope_id = :scope "
                        "AND lifecycle = 'active'"
                    ),
                    {"type": integration_type, "scope": scope_id},
                )
            ).first()
            if existing is None:
                print(f"refused: {target} has no active {integration_type} integration")
                return 2
            print(f"unchanged: {target}/{integration_type} already references {config_ref}")
            return 0

        await record_audit_event_in(
            connection,
            AuditEventData(
                actor_type="user",
                actor_id=str(actor),
                action="catalog.integration.configure",
                result="success",
                target_type="integration",
                target_id=f"{target}/{integration_type}",
                correlation_id=correlation_id_var.get(),
                metadata={
                    "scope": target,
                    "integration_type": integration_type,
                    # The NAME, which is not the address and not a secret.
                    "config_ref": config_ref,
                    "channel": "management",
                },
            ),
        )
    print(f"configured: {target}/{integration_type} -> {config_ref}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect a project integration to a connector.")
    parser.add_argument("--integration-type", required=True, choices=CONFIGURABLE_TYPES)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-key")
    scope.add_argument(
        "--cluster-ref",
        help="cluster-scope integration; capacity queries resolve against this scope",
    )
    parser.add_argument("--config-ref", required=True, help="connector name, never a URL or secret")
    parser.add_argument("--actor", required=True, help="the operator's Drake identity id")
    args = parser.parse_args()
    try:
        actor = uuid.UUID(args.actor)
    except ValueError:
        print("refused: --actor must be a Drake identity UUID")
        return 2
    try:
        return asyncio.run(
            _run(
                args.integration_type,
                args.project_key or "",
                args.cluster_ref or "",
                args.config_ref,
                actor,
            )
        )
    except CatalogValidationError as error:
        print(f"refused: {error}")
        return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
