"""Permission catalog and starter role templates (idempotent seeds).

Templates are convenience presets, not authorization shortcuts: the backend
never checks a role NAME — authority always flows through role_permissions.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

CATALOG_VERSION = 4

# Atomic permissions (from the product authority). ``auth.session`` powers
# nothing by itself — every authenticated user can read their own /v1/me.
PERMISSIONS: dict[str, str] = {
    "project.view": "View projects and their summaries",
    "environment.view": "View environments",
    "cluster.view": "View cluster inventory and health",
    "telemetry.query": "Run templated telemetry queries",
    "tenant.view": "View tenant directory and details",
    "tenant.usage.export": "Export tenant usage data",
    "protection.view": "View backup/restore posture",
    "alert.view": "View Alertmanager alerts and their projection",
    "slo.view": "View service SLOs and error budgets",
    "incident.ack": "Acknowledge incidents",
    "incident.assign": "Assign incidents",
    "alert.silence": "Create alert silences",
    "notification.view": "View notification policies and delivery audit",
    "notification.manage": "Manage notification policies and destinations",
    "integration.manage": "Manage integrations and connectors",
    "onboarding.view": "View project onboarding sessions and plans",
    "onboarding.manage": "Create onboarding sessions, analyse and approve plans",
    "onboarding.apply": "Apply an approved onboarding plan to the catalog",
    "onboarding.gitops": "Propose a project manifest to a repository by pull request",
    "template.manage": "Manage metric/dashboard templates",
    "rbac.manage": "Manage roles, grants and group mappings",
    "audit.view": "Read audit events",
}

# Starter role templates → permission sets (subsets of the catalog).
ROLE_TEMPLATES: dict[str, tuple[str, list[str]]] = {
    "Platform Owner": (
        "Full platform authority",
        sorted(PERMISSIONS.keys()),
    ),
    "Platform Admin": (
        "Platform administration without RBAC authority transfer",
        [
            "project.view",
            "environment.view",
            "cluster.view",
            "telemetry.query",
            "tenant.view",
            "protection.view",
            "alert.view",
            "slo.view",
            "incident.ack",
            "incident.assign",
            "alert.silence",
            "notification.view",
            "notification.manage",
            "integration.manage",
            "onboarding.view",
            "onboarding.manage",
            "onboarding.apply",
            "onboarding.gitops",
            "template.manage",
            "audit.view",
        ],
    ),
    "SRE / Operator": (
        "Operational health, alerts and incidents",
        [
            "project.view",
            "environment.view",
            "cluster.view",
            "telemetry.query",
            "protection.view",
            "alert.view",
            "slo.view",
            "incident.ack",
            "incident.assign",
            "alert.silence",
            "notification.view",
            "notification.manage",
        ],
    ),
    "Project Owner": (
        "Own project visibility including tenants",
        [
            "project.view",
            "environment.view",
            "telemetry.query",
            "tenant.view",
            "protection.view",
            "alert.view",
            "slo.view",
            "incident.ack",
            "notification.view",
        ],
    ),
    "Developer": (
        "Read project operational signals",
        # Deliberately NOT widened with `slo.view` or `alert.view`: adding a
        # permission to a starter template silently grants it to everyone
        # who already holds that role. New rights are granted explicitly.
        ["project.view", "environment.view", "telemetry.query"],
    ),
    "Billing/Operations Analyst": (
        "Commercial tenant facts and exports; no operational internals",
        ["tenant.view", "tenant.usage.export"],
    ),
    "Auditor": (
        "Read-only audit access",
        ["audit.view"],
    ),
    "Viewer": (
        "Summary visibility in assigned scopes",
        ["project.view", "environment.view"],
    ),
}

ORGANIZATION_ROOT_REF = "root"


async def seed_catalog(connection: AsyncConnection) -> None:
    """Idempotently upsert permissions, role templates, and the org root scope."""
    for key, description in PERMISSIONS.items():
        await connection.execute(
            text(
                """
                INSERT INTO permissions (key, description, catalog_version)
                VALUES (:key, :description, :version)
                ON CONFLICT (key) DO UPDATE
                SET description = EXCLUDED.description,
                    catalog_version = EXCLUDED.catalog_version
                """
            ),
            {"key": key, "description": description, "version": CATALOG_VERSION},
        )

    await connection.execute(
        text(
            """
            INSERT INTO scopes (scope_type, external_ref, display_name, parent_id)
            VALUES ('organization', :ref, 'Organization', NULL)
            ON CONFLICT (scope_type, external_ref) DO NOTHING
            """
        ),
        {"ref": ORGANIZATION_ROOT_REF},
    )

    for name, (description, permission_keys) in ROLE_TEMPLATES.items():
        await connection.execute(
            text(
                """
                INSERT INTO roles (name, description, is_system)
                VALUES (:name, :description, true)
                ON CONFLICT (name) DO NOTHING
                """
            ),
            {"name": name, "description": description},
        )
        for permission_key in permission_keys:
            await connection.execute(
                text(
                    """
                    INSERT INTO role_permissions (role_id, permission_key)
                    SELECT r.id, :permission FROM roles r WHERE r.name = :name
                    ON CONFLICT (role_id, permission_key) DO NOTHING
                    """
                ),
                {"name": name, "permission": permission_key},
            )
