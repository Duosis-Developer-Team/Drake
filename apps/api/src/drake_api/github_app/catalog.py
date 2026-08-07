"""The initial repository catalogue and its manual security gates.

A manual gate outranks automation (ADR-0020 §5). While a gate is open the
repository is BLOCKED and Drake performs no credential read, no
installation access, no reconciliation and no API call against it — the
block is enforced BEFORE any network path, not after.
"""

from dataclasses import dataclass

PROVIDER = "github"
ORGANIZATION = "Duosis-Developer-Team"


@dataclass(frozen=True)
class CatalogedRepository:
    owner: str
    name: str
    # Whether this repository may be evaluated at all in Sprint 5A.
    dry_run_eligible: bool
    # A non-null gate means BLOCKED until an operator closes it.
    security_gate: str | None = None
    gate_reason: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


# Sprint 5A target list. Datalake is deliberately closed to onboarding:
# its tracked `.env` finding needs authorized operator review, credential
# rotation where necessary, git-history containment, `.gitignore`
# remediation, and a safe `.env.example` contract first.
INITIAL_CATALOG: tuple[CatalogedRepository, ...] = (
    CatalogedRepository(ORGANIZATION, "Hermes", dry_run_eligible=True),
    CatalogedRepository(ORGANIZATION, "logislot", dry_run_eligible=True),
    CatalogedRepository(
        ORGANIZATION,
        "Datalake-Platform-GUI",
        dry_run_eligible=False,
        security_gate="manual_env_review",
        gate_reason=(
            "tracked .env manual security gate is open: operator review, credential "
            "rotation, git history containment, .gitignore remediation and a safe "
            ".env.example contract are required before onboarding"
        ),
    ),
    CatalogedRepository(ORGANIZATION, "Fikir-Sepeti", dry_run_eligible=True),
)

_BY_FULL_NAME = {entry.full_name.lower(): entry for entry in INITIAL_CATALOG}


def catalog_entry(full_name: str) -> CatalogedRepository | None:
    return _BY_FULL_NAME.get(full_name.strip().lower())


def security_gate_for(full_name: str) -> str | None:
    """The open manual gate for a repository, if any.

    Matching is on the normalized full name because Drake must recognise a
    gated repository WITHOUT ever calling GitHub to learn its numeric id.
    """
    entry = catalog_entry(full_name)
    return entry.security_gate if entry else None


def gate_reason_for(full_name: str) -> str:
    entry = catalog_entry(full_name)
    return entry.gate_reason if entry else ""


def is_dry_run_eligible(full_name: str) -> bool:
    """Unknown repositories are eligible; explicitly gated ones are not."""
    entry = catalog_entry(full_name)
    return entry.dry_run_eligible if entry else True
