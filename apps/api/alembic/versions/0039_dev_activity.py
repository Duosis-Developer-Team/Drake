"""Development agents (Agent Visibility): who changes the catalog's repositories,
with which coding agent, and what it cost.

Two sources, kept apart:

- `dev_commits`: one row per commit on a catalog repository's default branch,
  read through the GitHub App. Who authored it (login and name — never the
  email), which coding agent the commit says helped (a `Co-Authored-By:`
  trailer, a "Generated with …" marker, or an agent author), its size, the
  catalog services its paths fall under, and the PR it landed through. The
  first line of the message only.
- `dev_agent_days` / `dev_agent_breakdown`: Claude Code's own telemetry,
  rolled up per day, project and Claude identity — cost, tokens, sessions,
  prompts, active time, lines, edit decisions; and per model, tool and
  language. Only sessions attributable to a catalog project are kept.
  Prompt and answer text never reach Tracesis (the gateway removes them).

`dev_person_aliases` makes one person out of several identities: two GitHub
logins, or a Claude identity and a login.

Kept 90 days by the loop that writes them. Additive; the downgrade drops
what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENTS = "'claude_code', 'copilot', 'codex', 'cursor', 'devin', 'other_ai', 'none'"


def _project_fk() -> sa.Column:
    return sa.Column(
        "project_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "dev_commits",
        _project_fk(),
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("authored_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("author_login", sa.Text(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("agent", sa.Text(), nullable=False, server_default=sa.text("'none'")),
        sa.Column("agent_evidence", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("additions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "services",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("unmapped_files", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column(
            "collected_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("project_id", "sha", name="pk_dev_commits"),
        sa.CheckConstraint(f"agent IN ({_AGENTS})", name="ck_dev_commit_agent"),
        sa.CheckConstraint(
            "agent_evidence IS NULL OR agent_evidence IN ('trailer', 'pr_marker', 'bot_author')",
            name="ck_dev_commit_evidence",
        ),
        # Names and logins, never an email.
        sa.CheckConstraint(
            "position('@' in coalesce(author_login, '') || author_name) = 0",
            name="ck_dev_commit_no_email",
        ),
    )
    op.create_index("ix_dev_commits_when", "dev_commits", ["project_id", "authored_at"])

    op.create_table(
        "dev_person_aliases",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("person_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("kind", "alias", name="pk_dev_person_aliases"),
        sa.CheckConstraint("kind IN ('github', 'claude')", name="ck_dev_alias_kind"),
        sa.CheckConstraint(
            "length(alias) BETWEEN 1 AND 200 AND length(person_key) BETWEEN 1 AND 100",
            name="ck_dev_alias_length",
        ),
        sa.CheckConstraint(
            "position('@' in alias || person_key || coalesce(display_name, '')) = 0",
            name="ck_dev_alias_no_email",
        ),
    )

    op.create_table(
        "dev_agent_days",
        sa.Column("day", sa.Date(), nullable=False),
        _project_fk(),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False, server_default=sa.text("'claude_code'")),
        sa.Column("person_hint", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cache_read_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "cache_creation_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("api_requests", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("api_errors", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("prompts", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sessions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Null when the metric never arrived: not measured is not zero.
        sa.Column("active_seconds", sa.Numeric(14, 1), nullable=True),
        sa.Column("lines_added", sa.BigInteger(), nullable=True),
        sa.Column("lines_removed", sa.BigInteger(), nullable=True),
        sa.Column("commits", sa.BigInteger(), nullable=True),
        sa.Column("pull_requests", sa.BigInteger(), nullable=True),
        sa.Column("edits_accepted", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("edits_rejected", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("day", "project_id", "identity", "tool", name="pk_dev_agent_days"),
        sa.CheckConstraint(
            "position('@' in identity || coalesce(person_hint, '')) = 0",
            name="ck_dev_agent_day_no_email",
        ),
    )
    op.create_index("ix_dev_agent_days_project", "dev_agent_days", ["project_id", "day"])

    op.create_table(
        "dev_agent_breakdown",
        sa.Column("day", sa.Date(), nullable=False),
        _project_fk(),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False, server_default=sa.text("'claude_code'")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("failures", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint(
            "day", "project_id", "identity", "tool", "kind", "name", name="pk_dev_agent_breakdown"
        ),
        sa.CheckConstraint(
            "kind IN ('model', 'tool', 'mcp', 'language')", name="ck_dev_agent_breakdown_kind"
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_dev_agent_breakdown_name"),
    )


def downgrade() -> None:
    op.drop_table("dev_agent_breakdown")
    op.drop_index("ix_dev_agent_days_project", table_name="dev_agent_days")
    op.drop_table("dev_agent_days")
    op.drop_table("dev_person_aliases")
    op.drop_index("ix_dev_commits_when", table_name="dev_commits")
    op.drop_table("dev_commits")
