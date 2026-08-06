"""E2E grant seeding (local/test only, idempotent).

Creates the narrow-scope E2E identities and grants used by the catalog
browser-acceptance suite:

- user-plain   → Developer @ project/beta
- user-env     → Developer @ environment/alpha/dev
- user-cluster → E2E Cluster Viewer (cluster.view) @ organization/root
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ISSUER = "http://127.0.0.1:9556"

IDENTITIES = (
    ("user-plain", "Plain User"),
    ("user-env", "Env User"),
    ("user-cluster", "Cluster User"),
)

GRANTS = (
    ("user-plain", "Developer", "project", "beta"),
    ("user-env", "Developer", "environment", "alpha/dev"),
    ("user-cluster", "E2E Cluster Viewer", "organization", "root"),
)


async def main() -> None:
    env = os.environ.get("DRAKE_ENV", "local")
    if env not in ("local", "test"):
        raise RuntimeError("e2e grant seeding is local/test only")
    engine = create_async_engine(os.environ["DRAKE_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            for subject, display in IDENTITIES:
                await connection.execute(
                    text(
                        """
                        INSERT INTO identities (issuer, subject, display_name)
                        VALUES (:issuer, :subject, :display)
                        ON CONFLICT (issuer, subject) DO NOTHING
                        """
                    ),
                    {"issuer": ISSUER, "subject": subject, "display": display},
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO roles (name, description, is_system)
                    VALUES ('E2E Cluster Viewer', 'cluster.view only', false)
                    ON CONFLICT (name) DO NOTHING
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO role_permissions (role_id, permission_key)
                    SELECT r.id, 'cluster.view' FROM roles r
                    WHERE r.name = 'E2E Cluster Viewer'
                    ON CONFLICT (role_id, permission_key) DO NOTHING
                    """
                )
            )
            for subject, role, scope_type, scope_ref in GRANTS:
                await connection.execute(
                    text(
                        """
                        INSERT INTO grants (identity_id, role_id, scope_id)
                        SELECT i.id, r.id, s.id
                        FROM identities i, roles r, scopes s
                        WHERE i.issuer = :issuer AND i.subject = :subject
                          AND r.name = :role
                          AND s.scope_type = :scope_type AND s.external_ref = :scope_ref
                          AND NOT EXISTS (
                            SELECT 1 FROM grants g
                            WHERE g.identity_id = i.id AND g.role_id = r.id
                              AND g.scope_id = s.id AND g.revoked_at IS NULL
                          )
                        """
                    ),
                    {
                        "issuer": ISSUER, "subject": subject, "role": role,
                        "scope_type": scope_type, "scope_ref": scope_ref,
                    },
                )
    finally:
        await engine.dispose()
    sys.stdout.write("e2e grants seeded\n")


if __name__ == "__main__":
    asyncio.run(main())
