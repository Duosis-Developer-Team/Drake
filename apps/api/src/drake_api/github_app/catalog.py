"""The initial repository catalogue and its manual security gates.

A gate comes from a catalogue entry or from configuration
(`github_gated_repositories`); none is open today. A manual gate outranks
automation (ADR-0020 §5). While a gate is open the
repository is BLOCKED and Drake performs no credential read, no
installation access, no reconciliation and no API call against it — the
block is enforced BEFORE any network path, not after.
"""

from collections.abc import Iterable
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


# Sprint 5A target list. Datalake-Platform-GUI was held behind a manual
# `.env` review gate until 2026-11-22, when the owner closed it: it is read
# like any other repository now. Writing to it (commits, pull requests,
# issues, comments) stays out of bounds by the owner's decision — Tracesis
# only ever reads through the App anyway.
INITIAL_CATALOG: tuple[CatalogedRepository, ...] = (
    CatalogedRepository(ORGANIZATION, "Hermes", dry_run_eligible=True),
    CatalogedRepository(ORGANIZATION, "logislot", dry_run_eligible=True),
    CatalogedRepository(ORGANIZATION, "Datalake-Platform-GUI", dry_run_eligible=True),
    CatalogedRepository(ORGANIZATION, "Fikir-Sepeti", dry_run_eligible=True),
)

#: The gate an operator opens through configuration.
CONFIGURED_GATE = "manual_review"
CONFIGURED_GATE_REASON = (
    "closed by an operator (github_gated_repositories): no credential read, "
    "reconciliation or API call until the operator removes it"
)

_BY_FULL_NAME = {entry.full_name.lower(): entry for entry in INITIAL_CATALOG}


# Configured gates, keyed by the normalized full name. Filled once at start
# (`configure_security_gates`); only ever added to, so a reload can never
# quietly open a gate.
_CONFIGURED_GATES: set[str] = set()


def configure_security_gates(full_names: Iterable[str]) -> None:
    for full_name in full_names:
        _CONFIGURED_GATES.add(full_name.strip().lower())


def catalog_entry(full_name: str) -> CatalogedRepository | None:
    return _BY_FULL_NAME.get(full_name.strip().lower())


def security_gate_for(full_name: str) -> str | None:
    """The open manual gate for a repository, if any.

    Matching is on the normalized full name because Drake must recognise a
    gated repository WITHOUT ever calling GitHub to learn its numeric id.
    """
    entry = catalog_entry(full_name)
    if entry and entry.security_gate:
        return entry.security_gate
    return CONFIGURED_GATE if full_name.strip().lower() in _CONFIGURED_GATES else None


def gate_reason_for(full_name: str) -> str:
    entry = catalog_entry(full_name)
    if entry and entry.security_gate:
        return entry.gate_reason
    return CONFIGURED_GATE_REASON if full_name.strip().lower() in _CONFIGURED_GATES else ""


def is_dry_run_eligible(full_name: str) -> bool:
    """Unknown repositories are eligible; explicitly gated ones are not."""
    if security_gate_for(full_name):
        return False
    entry = catalog_entry(full_name)
    return entry.dry_run_eligible if entry else True
