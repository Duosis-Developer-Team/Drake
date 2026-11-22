"""Development agents per repository, not only per catalog project.

The map widens from the catalog's projects to every repository of the
GitHub organisations Tracesis watches (`dev_activity_orgs`). A repository
nobody has catalogued still has commits and Claude Code sessions; it just
has no project, and no services.

- `dev_commits`: `project_id` becomes nullable; `repository_external_id`
  (GitHub's stable id — a rename keeps it) names the repository on every
  row, and the key moves to (repository_external_id, sha).
- `dev_agent_days` / `dev_agent_breakdown`: `project_id` becomes nullable;
  `repository` (owner/name, lower case) names the repository on every row
  and joins the key; `repository_external_id` is kept when known.

Existing rows are filled from the catalog (the project's repository). The
email checks stay as they were. Additive; the downgrade drops the rows that
have no project and restores the 0039 keys.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- commits -------------------------------------------------------------
    op.add_column(
        "dev_commits", sa.Column("repository_external_id", sa.BigInteger(), nullable=True)
    )
    op.execute(
        """
        UPDATE dev_commits c SET repository_external_id = r.external_id
        FROM github_repositories r
        WHERE lower(r.full_name) = lower(c.repository)
        """
    )
    # A row whose repository Tracesis no longer knows cannot be keyed; the
    # loop reads it again from GitHub if the repository comes back.
    op.execute("DELETE FROM dev_commits WHERE repository_external_id IS NULL")
    op.alter_column("dev_commits", "repository_external_id", nullable=False)
    op.drop_constraint("pk_dev_commits", "dev_commits", type_="primary")
    op.create_primary_key("pk_dev_commits", "dev_commits", ["repository_external_id", "sha"])
    op.alter_column("dev_commits", "project_id", nullable=True)
    op.create_index(
        "ix_dev_commits_repository", "dev_commits", ["repository_external_id", "authored_at"]
    )

    # --- Claude Code usage -----------------------------------------------------
    for table in ("dev_agent_days", "dev_agent_breakdown"):
        op.add_column(
            table,
            sa.Column("repository", sa.Text(), nullable=False, server_default=sa.text("''")),
        )
        op.add_column(table, sa.Column("repository_external_id", sa.BigInteger(), nullable=True))
        op.execute(
            f"""
            UPDATE {table} d
            SET repository = lower(p.repo_owner || '/' || p.repo_name),
                repository_external_id = r.external_id
            FROM projects p
            LEFT JOIN github_repositories r
              ON lower(r.full_name) = lower(p.repo_owner || '/' || p.repo_name)
            WHERE p.id = d.project_id
            """  # noqa: S608 - fixed table names
        )
    op.drop_constraint("pk_dev_agent_days", "dev_agent_days", type_="primary")
    op.create_primary_key(
        "pk_dev_agent_days", "dev_agent_days", ["day", "repository", "identity", "tool"]
    )
    op.alter_column("dev_agent_days", "project_id", nullable=True)
    op.create_index("ix_dev_agent_days_repository", "dev_agent_days", ["repository", "day"])

    op.drop_constraint("pk_dev_agent_breakdown", "dev_agent_breakdown", type_="primary")
    op.create_primary_key(
        "pk_dev_agent_breakdown",
        "dev_agent_breakdown",
        ["day", "repository", "identity", "tool", "kind", "name"],
    )
    op.alter_column("dev_agent_breakdown", "project_id", nullable=True)
    op.create_check_constraint(
        "ck_dev_agent_days_repository",
        "dev_agent_days",
        "length(repository) BETWEEN 3 AND 300 AND position('@' in repository) = 0",
    )
    op.create_check_constraint(
        "ck_dev_agent_breakdown_repository",
        "dev_agent_breakdown",
        "length(repository) BETWEEN 3 AND 300 AND position('@' in repository) = 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_dev_agent_breakdown_repository", "dev_agent_breakdown", type_="check")
    op.drop_constraint("ck_dev_agent_days_repository", "dev_agent_days", type_="check")

    op.execute("DELETE FROM dev_agent_breakdown WHERE project_id IS NULL")
    # Two repositories of one project would collide under the old key.
    op.execute(
        """
        DELETE FROM dev_agent_breakdown a USING dev_agent_breakdown b
        WHERE a.ctid < b.ctid AND a.day = b.day AND a.project_id = b.project_id
          AND a.identity = b.identity AND a.tool = b.tool AND a.kind = b.kind
          AND a.name = b.name
        """
    )
    op.drop_constraint("pk_dev_agent_breakdown", "dev_agent_breakdown", type_="primary")
    op.alter_column("dev_agent_breakdown", "project_id", nullable=False)
    op.create_primary_key(
        "pk_dev_agent_breakdown",
        "dev_agent_breakdown",
        ["day", "project_id", "identity", "tool", "kind", "name"],
    )

    op.drop_index("ix_dev_agent_days_repository", table_name="dev_agent_days")
    op.execute("DELETE FROM dev_agent_days WHERE project_id IS NULL")
    op.execute(
        """
        DELETE FROM dev_agent_days a USING dev_agent_days b
        WHERE a.ctid < b.ctid AND a.day = b.day AND a.project_id = b.project_id
          AND a.identity = b.identity AND a.tool = b.tool
        """
    )
    op.drop_constraint("pk_dev_agent_days", "dev_agent_days", type_="primary")
    op.alter_column("dev_agent_days", "project_id", nullable=False)
    op.create_primary_key(
        "pk_dev_agent_days", "dev_agent_days", ["day", "project_id", "identity", "tool"]
    )
    for table in ("dev_agent_days", "dev_agent_breakdown"):
        op.drop_column(table, "repository_external_id")
        op.drop_column(table, "repository")

    op.drop_index("ix_dev_commits_repository", table_name="dev_commits")
    op.execute("DELETE FROM dev_commits WHERE project_id IS NULL")
    op.execute(
        """
        DELETE FROM dev_commits a USING dev_commits b
        WHERE a.ctid < b.ctid AND a.project_id = b.project_id AND a.sha = b.sha
        """
    )
    op.drop_constraint("pk_dev_commits", "dev_commits", type_="primary")
    op.alter_column("dev_commits", "project_id", nullable=False)
    op.create_primary_key("pk_dev_commits", "dev_commits", ["project_id", "sha"])
    op.drop_column("dev_commits", "repository_external_id")
