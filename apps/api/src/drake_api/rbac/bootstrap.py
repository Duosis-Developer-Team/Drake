"""Operator bootstrap: seed catalog and assign the first Platform Owner.

Usage (local/dev only; requires DRAKE_DATABASE_URL):

    uv run python -m drake_api.rbac.bootstrap --issuer <issuer> --subject <sub>

Looking a template up by name here is a seeding convenience, not an
authorization decision — runtime authority always flows through
role_permissions.
"""

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from drake_api.rbac.catalog import seed_catalog
from drake_api.settings import get_settings


async def bootstrap(issuer: str, subject: str, display_name: str) -> str:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            await seed_catalog(connection)
            row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO identities (issuer, subject, display_name)
                        VALUES (:issuer, :subject, :display_name)
                        ON CONFLICT (issuer, subject) DO UPDATE
                        SET display_name = EXCLUDED.display_name
                        RETURNING id
                        """
                    ),
                    {"issuer": issuer, "subject": subject, "display_name": display_name},
                )
            ).first()
            assert row is not None
            identity_id = row[0]
            result = await connection.execute(
                text(
                    """
                    INSERT INTO grants (identity_id, role_id, scope_id, created_by)
                    SELECT :identity_id, r.id, s.id, :identity_id
                    FROM roles r, scopes s
                    WHERE r.name = 'Platform Owner'
                      AND s.scope_type = 'organization' AND s.external_ref = 'root'
                      AND NOT EXISTS (
                        SELECT 1 FROM grants g
                        WHERE g.identity_id = :identity_id AND g.role_id = r.id
                          AND g.scope_id = s.id AND g.revoked_at IS NULL
                      )
                    RETURNING id
                    """
                ),
                {"identity_id": identity_id},
            )
            created = result.first() is not None
            await connection.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (actor_type, actor_id, action, result, target_type, target_id,
                         correlation_id, metadata, schema_version)
                    VALUES
                        ('system', 'bootstrap', 'rbac.bootstrap.platform_owner', 'success',
                         'identity', :identity_id, 'bootstrap', '{}'::jsonb, 1)
                    """
                ),
                {"identity_id": str(identity_id)},
            )
            return f"identity={identity_id} grant_created={created}"
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the first Platform Owner")
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--display-name", default="Bootstrap Operator")
    args = parser.parse_args()
    result = asyncio.run(bootstrap(args.issuer, args.subject, args.display_name))
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
