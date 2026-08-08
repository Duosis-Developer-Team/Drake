"""The identity the evaluation runner acts as.

The runner has to read telemetry across every project, and the Query
Broker answers to a `Principal` — so the choice was between adding a
bypass to the broker's authorization or giving the runner a real identity
with a real grant. A bypass is a second authorization path, and second
paths are where authorization bugs live. So: a real identity, holding
exactly one permission, visible in the same RBAC screens as everyone else.

Two properties make this safe to leave standing:

- **It cannot log in.** The issuer is a URN no identity provider can mint
  a token for, and no local credential row exists for it, so neither auth
  mode has a path to a session as this identity.
- **It holds one permission.** `telemetry.query` at the organization root
  — enough to read curated templates, and nothing else. It cannot read the
  catalog, manage a binding, or acknowledge an incident.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.rbac.service import Principal

# A URN, not an https issuer: `validate_runtime_security` requires a real
# OIDC issuer to be https, and the login flow only ever mints identities
# for the configured issuer. Nothing can authenticate as this subject.
SYSTEM_ISSUER = "urn:drake:system"
SYSTEM_SUBJECT = "incident-evaluator"
SYSTEM_ROLE = "Drake Evaluator (system)"
SYSTEM_PERMISSION = "telemetry.query"


async def ensure_system_evaluator(engine: AsyncEngine) -> Principal:
    """Create (idempotently) the evaluator identity, role and grant.

    Done at runner start rather than in a migration so the grant is
    re-asserted if someone removes it by hand and then wonders why
    evaluation stopped — and so the role is visible before the first cycle
    rather than appearing mid-incident.
    """
    async with engine.begin() as connection:
        identity_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO identities (issuer, subject, identity_type, display_name, email)
                    VALUES (:issuer, :subject, 'service', 'Drake incident evaluator', '')
                    ON CONFLICT (issuer, subject) DO UPDATE
                    SET display_name = EXCLUDED.display_name
                    RETURNING id
                    """
                ),
                {"issuer": SYSTEM_ISSUER, "subject": SYSTEM_SUBJECT},
            )
        ).scalar_one()

        await connection.execute(
            text(
                """
                INSERT INTO roles (name, description, is_system)
                VALUES (:name, 'Reads curated telemetry for incident evaluation', true)
                ON CONFLICT (name) DO NOTHING
                """
            ),
            {"name": SYSTEM_ROLE},
        )
        # Exactly one permission. Widening this role would widen what a
        # background loop can read, with nobody in the request path to
        # notice.
        await connection.execute(
            text(
                """
                INSERT INTO role_permissions (role_id, permission_key)
                SELECT r.id, :permission FROM roles r WHERE r.name = :name
                ON CONFLICT (role_id, permission_key) DO NOTHING
                """
            ),
            {"name": SYSTEM_ROLE, "permission": SYSTEM_PERMISSION},
        )
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id, created_by)
                SELECT :identity, r.id, s.id, :identity
                FROM roles r, scopes s
                WHERE r.name = :name
                  AND s.scope_type = 'organization' AND s.external_ref = 'root'
                  AND NOT EXISTS (
                    SELECT 1 FROM grants g
                    WHERE g.identity_id = :identity AND g.role_id = r.id
                      AND g.scope_id = s.id AND g.revoked_at IS NULL
                  )
                """
            ),
            {"identity": identity_id, "name": SYSTEM_ROLE},
        )

    return Principal(identity_id=uuid.UUID(str(identity_id)), issuer=SYSTEM_ISSUER)
