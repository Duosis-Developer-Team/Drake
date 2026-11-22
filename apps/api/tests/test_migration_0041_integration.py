"""0041 closes the stored Datalake security gate, and only that one.

The gate is stored on the repository's row and, by design, no observation
can close it — so the owner's decision (2026-11-22) has to reach existing
databases through this migration. Run only against the disposable stack.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from test_migrations_audit_integration import alembic_config, require_database_url

pytestmark = pytest.mark.integration

DATALAKE = "Duosis-Developer-Team/Datalake-Platform-GUI"
OTHER = "Duosis-Developer-Team/Still-Closed"


def _wipe(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM github_repositories WHERE external_id IN (941001, 941002)")
        )
        connection.execute(text("DELETE FROM github_installations WHERE external_id = 941000"))
        connection.execute(text("DELETE FROM scopes WHERE external_ref = 'migration-0041'"))


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_url = require_database_url()
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "0040")
    engine = create_engine(database_url)
    _wipe(engine)
    try:
        yield engine
    finally:
        command.upgrade(config, "head")
        _wipe(engine)
        engine.dispose()


def _seed(engine: Engine, *, reconciliation_state: str = "never") -> None:
    with engine.begin() as connection:
        scope_id = connection.execute(
            text(
                "INSERT INTO scopes (scope_type, external_ref, display_name) "
                "VALUES ('organization', 'migration-0041', 'Migration 0041') RETURNING id"
            )
        ).scalar_one()
        installation_id = connection.execute(
            text(
                "INSERT INTO github_installations (external_id, scope_id, account_login, "
                "account_type, state) VALUES (941000, :scope, 'Duosis-Developer-Team', "
                "'Organization', 'active') RETURNING id"
            ),
            {"scope": scope_id},
        ).scalar_one()
        for external_id, full_name, gate in (
            (941001, DATALAKE, "manual_env_review"),
            # Another gate, under another code, on another repository: untouched.
            (941002, OTHER, "manual_review"),
        ):
            owner, name = full_name.split("/")
            connection.execute(
                text(
                    "INSERT INTO github_repositories (installation_id, scope_id, external_id, "
                    "full_name, owner_login, name, default_branch, security_gate, "
                    "onboarding_state, state_reason, reconciliation_state) VALUES "
                    "(:i, :scope, :ext, :full, :owner, :name, 'main', :gate, 'blocked', "
                    ":reason, :recon)"
                ),
                {
                    "i": installation_id,
                    "scope": scope_id,
                    "ext": external_id,
                    "full": full_name,
                    "owner": owner,
                    "name": name,
                    "gate": gate,
                    "reason": f"security_gate_{gate}",
                    "recon": reconciliation_state,
                },
            )


def _row(engine: Engine, external_id: int) -> tuple[str | None, str, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT security_gate, onboarding_state, state_reason "
                "FROM github_repositories WHERE external_id = :id"
            ),
            {"id": external_id},
        ).one()
    return row[0], row[1], row[2]


def test_0041_closes_the_datalake_gate_and_rederives_its_state(engine: Engine) -> None:
    _seed(engine)
    config = alembic_config(require_database_url())
    command.upgrade(config, "0041")

    assert _row(engine, 941001) == (None, "discovered", "awaiting_reconciliation")
    assert _row(engine, 941002) == ("manual_review", "blocked", "security_gate_manual_review")

    command.downgrade(config, "0040")
    assert _row(engine, 941001) == (
        "manual_env_review",
        "blocked",
        "security_gate_manual_env_review",
    )
    assert _row(engine, 941002) == ("manual_review", "blocked", "security_gate_manual_review")


def test_0041_follows_the_precedence_chain_not_a_fixed_state(engine: Engine) -> None:
    """A repository last reconciled completely comes back ready: the gate was
    only ever the strongest reason, not the only one."""
    _seed(engine, reconciliation_state="complete")
    command.upgrade(alembic_config(require_database_url()), "0041")
    assert _row(engine, 941001) == (None, "ready", "reconciled")
