# ADR-0014: Catalog authority and scope topology

**Status:** Accepted

## Context

Sprint 2 introduces the first real control-plane capability: the
project/environment/service/cluster catalog with scoped read APIs. Two
decisions must be locked before persistence exists: where catalog truth
lives, and how catalog entities map onto the single-parent RBAC scope tree —
in particular for projects whose environments span multiple clusters.

## Decision 1 — Source of truth

1. The repository manifest (`.drake/project.yaml`) is and will remain the
   authority for a project's observability *intent*. Drake PostgreSQL stores
   the last accepted catalog revision plus runtime projections — never a
   competing source of intent.
2. Sprint 2 implements **no** GitHub App and **no** repository importer.
   Catalog rows carry explicit provenance (`catalog_source_kind`,
   source reference, revision, accepted-at) so their origin is always
   visible.
3. Sprint 2 exposes **no persistent catalog mutation through the UI or
   public API**. The only write path is an idempotent local/test fixture
   bootstrap that loads the fictional contract fixtures through the same
   application service (and refuses to run outside `local`/`test` —
   fail-closed). This is test scaffolding, not production behavior.
4. When UI-driven intent editing arrives (a later sprint), it will
   materialize as a GitHub pull request against the manifest — never a
   silent database edit.

## Decision 2 — Scope topology

```text
organization
├── cluster                       (RBAC branch of its own)
└── project
    └── environment
        └── service binding
```

1. **Clusters and projects are sibling branches under the organization.**
   A Kubernetes environment references its cluster via `cluster_id` (a
   foreign key), but the cluster is never the RBAC parent of project data.
   This keeps the tree single-parent while letting one project's
   environments live on different clusters.
2. A cluster grant therefore never implies authority over project metadata
   that happens to run on that cluster, and a project grant never implies
   cluster inventory authority. The two capabilities meet only in views
   that each check their own permission.
3. **Service definitions are project-level; service bindings are
   environment-specific.** The RBAC `service` scope node corresponds to the
   *binding* (`environment_services` row), because that is the unit access
   is actually granted on.
4. Scope node references are hierarchical and stable:
   project → `<project_key>`, environment → `<project_key>/<env_key>`,
   service binding → `<project_key>/<env_key>/<service_key>`,
   cluster → `<cluster_ref>`.
5. **Catalog rows and their scope nodes are created atomically** in one
   PostgreSQL transaction through the catalog application service. A catalog
   row without a scope node (or vice versa) cannot exist.
6. Integrations attach to any scope node (`organization`, `cluster`,
   `project`, `environment`, `service`) via `scope_id`; the integrations
   module never writes catalog tables directly.

## Decision 3 — Read authorization semantics (Sprint 2 API)

1. Detail endpoints outside the caller's scope return a **consistent 404**
   (anti-enumeration, matching Sprint 1 semantics).
2. Collection endpoints return **200 with an authorized-only result set**
   (empty is a valid, honest answer for a user with no catalog visibility).
   This is deliberately chosen over 403 so a newly onboarded user sees an
   empty state rather than an error; capability-specific admin surfaces
   (RBAC, audit) keep their 403 behavior.
3. Counts, aggregates, search results, and pagination cursors are computed
   **after** authorization filtering — unauthorized rows can never leak
   through totals, suggestions, or cursor behavior.
4. A user holding only a narrow environment/service grant may see the
   minimal parent project context needed for breadcrumbs (project identity
   and metadata), but sibling environments/services stay invisible and
   authorized child counts reflect only their visibility.

## Consequences

- Multi-cluster projects need no topology hacks; environment→cluster is a
  reference, not a parentage.
- Future catalog sync (manifest importer) upserts through the same
  application service, inheriting atomic scope creation and provenance.
- Renaming a project key is a controlled migration (scope refs embed keys);
  acceptable at this stage and revisited with the importer ADR.
