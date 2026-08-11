"""Fikir Sepeti: the first project Drake describes but does not run.

Every other onboarded project has a cluster. This one has Vercel and
Supabase, and the whole risk is that Drake's Kubernetes-shaped vocabulary
quietly fills in the blanks — a cluster binding for a project with no
cluster, a missing-workload drift finding for a workload that does not
exist, a health verdict for something nothing observes.

These tests run against the REAL manifest on disk rather than a copy, so a
value edited there without its consequences being understood fails here
rather than during an onboarding run against the live catalog.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from drake_api.catalog.external_runtime import (
    Availability,
    DependencyClass,
    Freshness,
    HostingProvider,
    RuntimeKind,
    Verification,
    WorkloadApplicability,
    dependency_metadata,
    evaluate_external_health,
    workload_applicability,
)
from drake_api.onboarding.drift import (
    DriftKind,
    evaluate_drift,
    expected_datastores_from_manifest,
    expected_workloads_from_manifest,
)
from drake_api.onboarding.model import (
    BLOCKING_ACTIONS,
    CatalogSnapshot,
    EntityKind,
    build_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "packages" / "contracts" / "onboarding" / "fikir-sepeti.project.yaml"

#: Any of these appearing in the plan would mean Drake invented Kubernetes
#: identity for a project that has none.
KUBERNETES_KINDS = {
    str(EntityKind.CLUSTER_BINDING),
    str(EntityKind.NAMESPACE_BINDING),
    str(EntityKind.WORKLOAD_BINDING),
}

REPOSITORY_ROW_ID = "11111111-1111-4111-8111-111111111111"


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def plan(snapshot: CatalogSnapshot | None = None):
    return build_plan(
        manifest(),
        snapshot or CatalogSnapshot(),
        repository_row_id=REPOSITORY_ROW_ID,
    )


# --- what the manifest says, and what it deliberately does not say ---------


def test_the_manifest_declares_an_external_vercel_environment() -> None:
    environments = manifest()["spec"]["environments"]
    assert len(environments) == 1
    (prod,) = environments
    assert prod["runtime"] == RuntimeKind.EXTERNAL
    assert prod["hostingProvider"] == HostingProvider.VERCEL
    assert prod["branch"] == "main"


def test_the_external_environment_claims_no_kubernetes_identity() -> None:
    """Not merely absent from the plan — absent from the source of truth.

    The schema refuses these keys for an external environment, so this also
    proves the manifest is not relying on a downstream layer to drop them.
    """
    (prod,) = manifest()["spec"]["environments"]
    assert "clusterRef" not in prod
    assert "namespace" not in prod


def test_the_service_asserts_no_scrape_target_and_no_probe() -> None:
    """Nothing collects metrics from this application and nothing probes it.

    `metricsProfile` and `health` are both omitted, which 0021 made
    expressible. A profile here would name a scrape target that does not
    exist; a health block would advertise an endpoint the repository does
    not serve. The repository's only health-ish route, /api/me, sits behind
    authentication and answers 401, which a prober would read as down.
    """
    (web,) = manifest()["spec"]["services"]
    assert web["name"] == "fikir-sepeti-web"
    assert web["component"] == "web"
    assert "metricsProfile" not in web
    assert "health" not in web
    assert "workloadSelector" not in web


def test_the_dependency_is_managed_and_carries_no_credential_material() -> None:
    (store,) = manifest()["spec"]["dataStores"]
    assert store["dependencyClass"] == DependencyClass.MANAGED_DATA_PLATFORM
    assert store["provider"] == HostingProvider.SUPABASE
    assert store["verification"] == Verification.REPOSITORY_INTENT
    # A managed platform is not something Drake measures, so it names no
    # measurement profile — and it carries nothing that could reach it.
    assert "measurementProfile" not in store
    assert "connectionSecretRef" not in store


def test_the_tenant_model_is_the_one_the_migrations_establish() -> None:
    """shared_table, evidenced rather than guessed.

    Fikir Sepeti puts a NOT NULL `tenant_id` on every domain table in one
    database and scopes RLS through `current_tenant_id()`. That is
    `shared_table` exactly; it is not per-schema and not per-database.
    """
    assert manifest()["spec"]["tenantModel"]["mode"] == "shared_table"


# --- the plan --------------------------------------------------------------


def test_the_plan_invents_no_kubernetes_entity() -> None:
    kinds = {item.entity_kind for item in plan().items}
    assert kinds & KUBERNETES_KINDS == set()


def test_the_plan_creates_the_project_environment_service_and_dependency() -> None:
    actions = {item.item_key: item.action for item in plan().items}
    assert actions["project:fikir-sepeti"] == "create"
    assert actions["environment:prod"] == "create"
    assert actions["service:fikir-sepeti-web"] == "create"
    assert actions["dependency:fikir-sepeti-db"] == "create"


def test_an_absent_metrics_profile_does_not_block_the_plan() -> None:
    """`not configured` is a state, not an unresolved reference.

    Before 13F.2 an omitted profile planned as `unmapped`, which is a
    blocking action — so the honest answer for a project nothing scrapes
    made the project unonboardable.
    """
    (item,) = [i for i in plan().items if i.entity_kind == str(EntityKind.METRIC_PROFILE)]
    assert item.action == "no_change"
    assert item.reason_code == "metric_profile_not_configured"


def test_the_dependency_plan_item_marks_workload_semantics_inapplicable() -> None:
    (item,) = [i for i in plan().items if i.entity_kind == str(EntityKind.DEPENDENCY)]
    assert item.detail["workload_applicability"] == str(WorkloadApplicability.NOT_APPLICABLE)


def test_the_manifest_names_the_operator_confirmed_owner() -> None:
    """`fikir-sepeti`, and no placeholder anywhere.

    The value does not come from the repository — there is no CODEOWNERS
    and no team named in documentation — it comes from an explicit operator
    decision, recorded in docs/onboarding/FIKIR_SEPETI.md. The earlier
    `unknown-team` was not the visible non-claim it looked like: apply turns
    whatever is written here into a real `project_owners` row.
    """
    (owner,) = manifest()["spec"]["owners"]
    assert owner == {"team": "fikir-sepeti", "role": "primary"}
    text = MANIFEST.read_text()
    for placeholder in ("unknown-team", "REPLACE_ME", "TODO", "TBD"):
        assert placeholder not in text.replace("`unknown-team` placeholder is gone", "")


def test_nothing_in_this_manifest_blocks_the_plan() -> None:
    assert [i.item_key for i in plan().items if i.action in BLOCKING_ACTIONS] == []


def test_a_new_project_records_its_owner_with_the_project() -> None:
    """One transaction, and the plan says so rather than implying it."""
    (owner,) = [i for i in plan().items if i.entity_kind == str(EntityKind.OWNER_TEAM)]
    assert owner.action == "no_change"
    assert owner.reason_code == "applied_with_parent"
    assert owner.proposed_name == "fikir-sepeti"
    assert owner.detail["role"] == "primary"
    # The one thing an ownership row must never be mistaken for.
    assert owner.detail["grants_no_permissions"] is True


def test_an_existing_project_missing_this_owner_plans_a_real_add() -> None:
    """The latent defect this sprint fixed.

    Every owner used to plan `no_change` / `applied_with_parent`, which is
    only true while the project is being created. For a project that already
    exists `_apply_project` never runs, so the plan said "nothing to do" and
    the owner was silently dropped.
    """
    existing = CatalogSnapshot(
        projects={"fikir-sepeti": "p-1"},
        project_repository={"fikir-sepeti": REPOSITORY_ROW_ID},
    )
    (owner,) = [i for i in plan(existing).items if i.entity_kind == str(EntityKind.OWNER_TEAM)]
    assert owner.action == "create"
    assert owner.payload == {"role": "primary", "team": "fikir-sepeti"}


def test_an_existing_project_that_already_records_this_owner_changes_nothing() -> None:
    existing = CatalogSnapshot(
        projects={"fikir-sepeti": "p-1"},
        project_repository={"fikir-sepeti": REPOSITORY_ROW_ID},
        project_owners=frozenset({("fikir-sepeti", "fikir-sepeti", "primary")}),
    )
    (owner,) = [i for i in plan(existing).items if i.entity_kind == str(EntityKind.OWNER_TEAM)]
    assert owner.action == "no_change"
    assert owner.reason_code == "owner_team_already_recorded"
    assert owner.payload == {}


def test_another_project_owning_the_same_team_name_is_not_this_project_owning_it() -> None:
    """The precise bug in the old snapshot.

    `owner_teams` is a GLOBAL set of team keys, so any other project using
    the name made this project's missing owner look settled.
    """
    elsewhere = CatalogSnapshot(
        projects={"fikir-sepeti": "p-1"},
        project_repository={"fikir-sepeti": REPOSITORY_ROW_ID},
        owner_teams={"fikir-sepeti": "fikir-sepeti"},
        project_owners=frozenset({("some-other-project", "fikir-sepeti", "primary")}),
    )
    (owner,) = [i for i in plan(elsewhere).items if i.entity_kind == str(EntityKind.OWNER_TEAM)]
    assert owner.action == "create"


def test_the_same_team_in_a_different_role_is_a_different_association() -> None:
    """Identity is (project, team, role) — the unique constraint's own key.

    So this plans an ADD rather than a conflict, and the recorded secondary
    row is left alone: an import does not reassign somebody's decision.
    """
    secondary = CatalogSnapshot(
        projects={"fikir-sepeti": "p-1"},
        project_repository={"fikir-sepeti": REPOSITORY_ROW_ID},
        project_owners=frozenset({("fikir-sepeti", "fikir-sepeti", "secondary")}),
    )
    (owner,) = [i for i in plan(secondary).items if i.entity_kind == str(EntityKind.OWNER_TEAM)]
    assert owner.action == "create"
    assert owner.payload["role"] == "primary"


def test_re_planning_against_the_applied_catalog_creates_nothing_twice() -> None:
    """Idempotency at the plan layer: a second import must not duplicate.

    The snapshot describes a catalog that already holds everything the
    first import created.
    """
    applied = CatalogSnapshot(
        projects={"fikir-sepeti": "p-1"},
        project_repository={"fikir-sepeti": REPOSITORY_ROW_ID},
        environments={("fikir-sepeti", "prod"): "e-1"},
        services={("fikir-sepeti", "fikir-sepeti-web"): "s-1"},
        dependencies={("fikir-sepeti", "fikir-sepeti-db"): "d-1"},
        project_owners=frozenset({("fikir-sepeti", "fikir-sepeti", "primary")}),
        dependency_metadata={
            ("fikir-sepeti", "fikir-sepeti-db"): {
                "dependency_key": "fikir-sepeti-db",
                "display_name": "fikir-sepeti-db",
                "dependency_class": str(DependencyClass.MANAGED_DATA_PLATFORM),
                "engine": "postgresql",
                "store_scope": "project",
                "provider": str(HostingProvider.SUPABASE),
                "verification": str(Verification.REPOSITORY_INTENT),
            }
        },
        project_metadata={
            "fikir-sepeti": {
                "display_name": "Fikir Sepeti",
                "criticality": "low",
                "tenant_model": "shared_table",
                "default_branch": "main",
            }
        },
        environment_metadata={
            ("fikir-sepeti", "prod"): {
                "runtime": str(RuntimeKind.EXTERNAL),
                "branch": "main",
                "criticality": "medium",
                "hosting_provider": str(HostingProvider.VERCEL),
            }
        },
        service_metadata={
            ("fikir-sepeti", "fikir-sepeti-web"): {
                "component": "web",
                "runtime": "nextjs",
                "metrics_profile": None,
                "workload_selector": {},
                "health": {},
            }
        },
    )
    actions = {item.item_key: item.action for item in plan(applied).items}
    assert actions["project:fikir-sepeti"] != "create"
    assert actions["environment:prod"] != "create"
    assert actions["owner_team:fikir-sepeti:primary"] != "create"
    assert actions["service:fikir-sepeti-web"] != "create"
    assert actions["dependency:fikir-sepeti-db"] != "create"


def test_a_re_import_never_raises_verification() -> None:
    """Even if the catalog already holds a higher level, and even though
    the manifest declares the lowest one — neither direction moves it."""
    store = manifest()["spec"]["dataStores"][0]
    fresh = dependency_metadata(store)
    assert fresh["verification"] == str(Verification.REPOSITORY_INTENT)

    confirmed = dependency_metadata(store, {"verification": str(Verification.OWNER_CONFIRMED)})
    assert confirmed["verification"] == str(Verification.OWNER_CONFIRMED)


# --- drift -----------------------------------------------------------------


def test_the_manifest_expects_no_workload_anywhere() -> None:
    assert expected_workloads_from_manifest(manifest()) == ()


def test_the_managed_dependency_is_not_an_expected_datastore_workload() -> None:
    """A Supabase database has no Deployment to be missing.

    If it were treated as one, every drift run would report it absent —
    forever, unfixably, because there is nothing to deploy.
    """
    assert expected_datastores_from_manifest(manifest()) == ()


def test_an_empty_cluster_produces_no_drift_for_this_project() -> None:
    """The decisive one. Nothing observed, nothing expected, no findings.

    A `missing` or `expected_not_observed` item here would be Drake
    reporting its own schema as an outage in somebody else's application.
    """
    document = manifest()
    report = evaluate_drift(
        expected=expected_workloads_from_manifest(document),
        observed=(),
        # No namespace either — this project has none to have been seen.
        observed_namespaces=(),
        expected_datastores=expected_datastores_from_manifest(document),
    )
    assert report.items == ()
    assert not any(
        item.kind
        in {
            DriftKind.EXPECTED_NOT_OBSERVED,
            DriftKind.NAMESPACE_NOT_OBSERVED,
            DriftKind.OBSERVED_NOT_EXPECTED,
        }
        for item in report.items
    )


# --- health ----------------------------------------------------------------


def test_the_dependency_reports_unknown_health_and_unavailable_freshness() -> None:
    """Nothing observes this, so there is no verdict — and saying so is the
    point. `unknown` is not `unhealthy`, and `unavailable` is not `stale`."""
    verdict = evaluate_external_health(verification=Verification.REPOSITORY_INTENT).as_dict()
    assert verdict["status"] == "unknown"
    assert verdict["freshness"] == str(Freshness.UNAVAILABLE)
    assert verdict["availability"] == str(Availability.UNKNOWN)
    assert verdict["source"]["status"] == "not_configured"
    assert verdict["last_observed_at"] is None
    assert verdict["verification"] == str(Verification.REPOSITORY_INTENT)


def test_the_managed_dependency_is_never_a_workload() -> None:
    (store,) = manifest()["spec"]["dataStores"]
    assert workload_applicability(store["dependencyClass"]) is WorkloadApplicability.NOT_APPLICABLE


# --- the manifest as a document -------------------------------------------


def test_the_manifest_carries_no_credential_shaped_value() -> None:
    """Cheap, and it is the failure that cannot be walked back.

    A manifest is committed, reviewed and copied. Anything secret-shaped in
    it is already published by the time anyone notices.
    """
    text = MANIFEST.read_text()
    lowered = text.lower()
    for token in ("service_role", "anon_key", "supabase_url", "eyj", "sb_secret", "://"):
        assert token not in lowered, f"manifest contains {token!r}"
    # A Supabase project ref would identify the actual instance.
    assert "supabase.co" not in lowered
