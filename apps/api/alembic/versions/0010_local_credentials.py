"""Local email/password credentials for identities that have no IdP.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08

Drake authenticates through an identity provider by default. This adds a
second way in — an email and password verified by Drake itself — without
creating a second user system: a local credential is a row attached to an
existing `identities` row, so roles, grants, scopes and audit all keep
working exactly as they do for a federated user.

The identity's `issuer` is `local` and its `subject` is the normalized
email, which makes the existing (issuer, subject) uniqueness the thing that
prevents duplicates. Nothing here stores a password: only an Argon2id
hash, produced and verified by the application.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # One credential per identity. Deleting the identity takes the
        # credential with it; there is no orphan login.
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Lower-cased and trimmed at write time. Unique, so two identities
        # cannot both answer to the same address.
        sa.Column("email_normalized", sa.Text(), nullable=False),
        # An Argon2id PHC string: algorithm, parameters and salt all live
        # inside it, so the parameters can be raised later without a
        # schema change. Never a password, never a reversible encoding.
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("identity_id", name="uq_local_credential_identity"),
        sa.UniqueConstraint("email_normalized", name="uq_local_credential_email"),
        # Cheap shape check. It is not email validation — that belongs in
        # the application — but it does stop an obviously wrong value, and
        # it guarantees the stored form really was normalized.
        sa.CheckConstraint(
            "email_normalized = lower(email_normalized) AND email_normalized LIKE '%@%'",
            name="ck_local_credential_email_normalized",
        ),
        # A hash, and specifically an Argon2id one. A row that somehow
        # carried a plaintext password could not satisfy this.
        sa.CheckConstraint(
            "password_hash LIKE '$argon2id$%' AND length(password_hash) >= 40",
            name="ck_local_credential_hash_shape",
        ),
    )


def downgrade() -> None:
    op.drop_table("local_credentials")
