"""Close the Datalake manual security gate (owner decision, 2026-11-22).

Sprint 5A gated Duosis-Developer-Team/Datalake-Platform-GUI over a tracked
`.env`. The owner closed that gate; the code catalogue no longer derives it.
The gate is also STORED on the repository's row, and a stored gate outranks
every observation by design (a rename must never quietly open one), so it
has to be closed here, explicitly, as the operator step it is.

The row's onboarding state is re-derived through the same precedence chain
the service uses (`resolve_effective`, without the gate): installation
deleted/suspended > access removed/suspended > reconciliation error/failed >
partial/stale evidence > complete > awaiting reconciliation. BLOCKED →
DISABLED/DISCOVERED is a legal transition; the next reconciliation pass
takes it from there. Only the row this gate closed is touched — a gate
anyone opened since, under another code, stays.

Writing to the repository stays out of bounds; this only lets Tracesis read
it, as it reads every other repository.

The downgrade reopens the gate and blocks the row again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPOSITORY = "duosis-developer-team/datalake-platform-gui"
_GATE = "manual_env_review"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
        UPDATE github_repositories AS r
        SET security_gate = NULL,
            onboarding_state = derived.state,
            state_reason = derived.reason,
            updated_at = now()
        FROM (
            SELECT r2.id,
                CASE
                    WHEN i.state IN ('deleted', 'suspended') THEN 'disabled'
                    WHEN r2.access_state IN ('removed', 'suspended') THEN 'disabled'
                    WHEN r2.last_error_code IS NOT NULL
                         OR r2.reconciliation_state = 'failed' THEN 'degraded'
                    WHEN r2.reconciliation_state IN ('partial', 'stale') THEN 'degraded'
                    WHEN r2.reconciliation_state = 'complete' THEN 'ready'
                    ELSE 'discovered'
                END AS state,
                CASE
                    WHEN i.state = 'deleted' THEN 'installation_deleted'
                    WHEN i.state = 'suspended' THEN 'installation_suspended'
                    WHEN r2.access_state IN ('removed', 'suspended')
                         THEN 'access_' || r2.access_state
                    WHEN r2.last_error_code IS NOT NULL
                         OR r2.reconciliation_state = 'failed' THEN 'reconciliation_error'
                    WHEN r2.reconciliation_state IN ('partial', 'stale')
                         THEN 'evidence_' || r2.reconciliation_state
                    WHEN r2.reconciliation_state = 'complete' THEN 'reconciled'
                    ELSE 'awaiting_reconciliation'
                END AS reason
            FROM github_repositories r2
            LEFT JOIN github_installations i ON i.id = r2.installation_id
            WHERE lower(r2.full_name) = :repository AND r2.security_gate = :gate
        ) AS derived
        WHERE r.id = derived.id
        """
        ),
        {"repository": _REPOSITORY, "gate": _GATE},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
        UPDATE github_repositories
        SET security_gate = :gate,
            onboarding_state = 'blocked',
            state_reason = :reason,
            updated_at = now()
        WHERE lower(full_name) = :repository AND security_gate IS NULL
        """
        ),
        {"repository": _REPOSITORY, "gate": _GATE, "reason": f"security_gate_{_GATE}"},
    )
