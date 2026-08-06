# Sprint 2 — Security Report

Public-safe summary of the catalog security posture. Everything listed as
enforced is backed by executed tests (see S2_TEST_REPORT).

## 1. Catalog write path

- `CatalogService` is the only write path; the entity and its RBAC scope
  node commit in one transaction (rollback-together proven by fault
  injection). No API/UI mutation surface exists in Sprint 2.
- Cross-project binding is impossible at two layers: the service validates
  environment vs service project identity from authoritative rows, and
  PostgreSQL enforces it with an authoritative `project_id` on bindings
  plus composite RESTRICT foreign keys to both parents (migration 0005,
  fail-closed backfill — pre-existing invalid rows abort the migration).
- Scope refs derive from authoritative records only — callers can no
  longer supply a parallel key identity for a relationship they name by
  id, and `ScopeResolver.ensure` fails closed if an existing scope sits
  under a different parent.
- `last_error_code` is a bounded machine-readable code (DB CHECK + app
  validation): no free text, URLs, newlines, or provider bodies can be
  stored; external stable refs (keys, cluster refs, integration types)
  carry DB-level shape checks.
- Bounded, credential-free metadata: workload selectors and health paths are
  size-limited JSON that reject credential shapes, URLs-as-paths, and
  unknown fields. No secrets, manifests, or environment dumps are storable.
- All catalog foreign keys are RESTRICT; lifecycle is archive-based —
  no cascade deletion of monitoring history is possible.
- The fixture bootstrap (and the E2E reset/grant seeding scripts) refuse to
  run outside `local`/`test` and load fixtures only through the service, so
  test scaffolding can never become a production seed.

## 2. Read authorization

- Visibility is computed in SQL as the subtree union of the caller's grants
  per permission (`project.view`, `environment.view`, `cluster.view`) —
  before search, counts, and pagination. Unauthorized rows cannot leak
  through totals, cursors, suggestions, or aggregates (negative-tested).
- Every collection (projects, environments, services, clusters,
  integrations) is bounded: keyset cursors with deterministic unique
  ordering, capped limits, bounded search/filters — no offset pagination.
  Integration-health authorization is itself a SQL predicate; unauthorized
  rows never leave the database and cannot influence cursors or filters.
- Out-of-scope details are consistent 404s (anti-enumeration); collections
  return authorized-only 200 with honest empty states (policy recorded in
  ADR-0014 and negative-tested).
- Cluster and project authority are fully separated: a project grant yields
  no cluster inventory (list empty, detail 404) and a cluster grant yields
  no project metadata; cluster detail lists only environments the caller
  can already see.
- Narrow environment grants surface minimal project breadcrumb context;
  sibling environments/services stay invisible in lists, details, counts,
  and search.
- Search input is length-bounded, LIKE-escaped (wildcards are literals),
  parameterized (no SQL interpolation), and capped at 20 results.

## 3. Response hygiene

- Responses carry opaque IDs, safe metadata, lifecycle, version, scope
  context, provenance, and `as_of` — never `config_ref`, credentials,
  connection strings, kubeconfigs, OIDC subjects/issuers, full manifests,
  or provider error bodies (asserted in tests).
- Integration health exposes states, timestamps, and a bounded error code
  only. With no provider connected, states are `not_configured`/`unknown` —
  the API cannot fabricate `healthy` in Sprint 2 by construction.

## 4. Web

- Browser reads stay same-origin `/v1` with `cache: no-store`, so revoked
  scopes cannot linger in HTTP caches; the provider-access guard still
  forbids direct provider references and absolute URLs in client code.
- Navigation gating is UX only; every screen re-fetches through the
  authorized API, and error UI surfaces correlation IDs without backend
  internals.
- Official brand assets ship as static images; no external asset origins.

## 5. Unchanged guarantees (re-verified this sprint)

Transactional idempotency, append-only audit, session/OIDC hardening,
secret-scan (history + tree + canary), dependency scan, and the read-only
agent contract all remain green.

## 6. Known security work ahead

- Real provider integrations arrive in later sprints, each behind its own
  security gate (query broker budgets, agent enrollment, webhook signing).
- Session inactivity timeout and auth rate limiting remain tracked
  hardening items.
- One source-repository security review item remains tracked privately and
  gates the corresponding integration sprint (unchanged).
