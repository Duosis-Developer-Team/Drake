# Fikir Sepeti — catalog admission evidence

**Status: prepared, NOT activated.** Nothing in this sprint wrote to the
production catalog. This document plus
`packages/contracts/onboarding/fikir-sepeti.project.yaml` are the admission
pack; activation is a separate, operator-gated decision, and there is one
open blocker below.

Fikir Sepeti is the first project Drake describes that Drake does not run.
Every other onboarded project has a cluster, an agent and an inventory.
This one has Vercel and Supabase, and the risk it exists to prove is not
that Drake crashes on it — it is that Drake's Kubernetes-shaped vocabulary
fills in the blanks and produces a page that looks complete and is wrong.

---

## What was read, and when

| | |
|---|---|
| Repository | `github.com/Duosis-Developer-Team/Fikir-Sepeti` |
| Default branch | `main` |
| HEAD at read time | `bde38ccd9c0e58570de28b0d4d1ef8eae41f7e5a` |
| Last push | 2026-07-16T13:57:28Z |
| Read at | 2026-08-11, read-only |
| Visibility | public, not archived |

The earlier audit SHA was **not** carried over. The default branch and HEAD
were re-read from remote metadata, and every claim below was re-derived
from the tree at that commit.

No fork, no clone push, no branch, no issue, no comment, no workflow run.
The repository was not modified in any way.

---

## Claims and their evidence

| Claim | Value | Evidence source | Verification |
|---|---|---|---|
| Repository | `Duosis-Developer-Team/Fikir-Sepeti` | Remote metadata (`default_branch`, HEAD `bde38ccd`) | **Observed** |
| Runtime | External | No Dockerfile, Helm chart, Kubernetes or Kustomize manifest anywhere in 221 tracked files | **Repository intent** |
| Hosting provider | Vercel | `.github/workflows/deploy.yml` runs `vercel deploy --prod`, gated on CI success on `main` | **Repository intent** |
| Framework | Next.js 16.2.10, React 19.2.4 | `package.json` dependencies | **Repository intent** |
| Datastore | Supabase PostgreSQL | `@supabase/supabase-js@^2.110.0`; 15 migrations under `supabase/migrations/`; `supabase/config.toml` | **Repository intent** |
| Tenant model | `shared_table` | `0002_tenants.sql`, `0003_tenant_id.sql`, `0005_rls.sql` | **Repository intent** |
| Owner team | **Unresolved** | No CODEOWNERS at any of the three locations GitHub honours; no team named in documentation | **Unmapped — blocker** |
| Workload | Not applicable | No Kubernetes runtime exists to hold one | **Derived** |
| Health | Unknown | No observer exists, and none was created | **Unavailable** |
| Freshness | Unavailable | No observation has ever been recorded | **Unavailable** |

### The four levels, kept apart

This separation is the entire point of ADR-0027, so it is stated
explicitly rather than left to the table above:

```
repository evidence          what the source code shows        ← everything here
owner confirmation           a human attesting to it           ← none obtained
provider observation         Vercel/Supabase reporting it      ← none obtained
production health observation  something actually probing it   ← none exists
```

**Nothing in this pack rises above the first level.** A repository that
imports a Supabase client and ships Supabase migrations is strong evidence
about the source code and *no* evidence that a production connection
exists, works, or points at that provider. Importing this manifest records
`repository_intent`, and `resolve_verification_for_import` will not raise
it regardless of what the manifest declares.

---

## Architecture

### Deployment

`.github/workflows/deploy.yml` — "Deploy to Vercel (production)" — triggers
on `workflow_run` completion of the CI workflow on `main`, and runs
`vercel deploy --prod --yes`. There is no other deployment path in the
repository: no container build, no registry push, no cluster apply.

`sync-vercel-env.yml` is a manual workflow that manages Vercel production
environment variables. It is evidence that a Vercel production project
exists and is administered from this repository. It is **not** evidence of
runtime health, and Drake obtained no Vercel credential and made no Vercel
API call.

One environment is declared, because one is deployed. Vercel builds preview
deployments per branch, but nothing in the repository promotes any of them
to a named environment, and a per-PR preview URL is not something Drake can
be accountable for.

### Supabase components actually used

Determined by reading source, not by assuming the platform's full surface:

| Component | Used | Evidence |
|---|---|---|
| PostgreSQL | **yes** | 15 migrations; `supabase/schema.sql`; all data access |
| Auth | **yes** | `components/AuthGate.tsx` uses `supabase.auth.getSession/signInWithOAuth/signInWithPassword/signUp/onAuthStateChange`; RLS helpers read `auth.jwt() ->> 'email'` |
| Realtime | **yes** | `app/page.tsx` opens a `home:live` channel with two `postgres_changes` subscriptions; `lib/supabase.ts` configures `realtime.params.eventsPerSecond` |
| Storage | no | no `supabase.storage` / `.storage.from(` anywhere |
| Edge Functions | no | no `supabase/functions/` directory |

These are components of **one** managed platform, so the manifest records
**one** dependency. Splitting Auth and Realtime into separate rows would
invent dependencies that have no separate existence, no separate identity
and no separate failure domain.

### Tenant isolation

`shared_table`, and evidenced rather than inferred:

- `0002_tenants.sql` creates `tenants` and `app_users`, with
  `app_users.tenant_id` a NOT NULL FK and `unique (tenant_id, user_id)`.
- `0003_tenant_id.sql` adds `tenant_id` to nine domain tables — `baskets`,
  `ideas`, `votes`, `teams`, `team_members`, `team_votes`, `feedback`,
  `hackathon_participants`, `squad_members` — backfills them, then enforces
  NOT NULL on each.
- `0005_rls.sql` enables row-level security on every domain table and
  defines policies that scope through `public.current_tenant_id()`, which
  resolves the caller's tenant from `app_users` by the JWT email.
- `0004_rbac.sql` adds `roles` / `role_permissions` / `user_roles`, and
  `has_perm()` scopes permission checks by both tenant and user.

One database, one schema, shared tables discriminated by `tenant_id`. Not
`schema_per_tenant`: no per-tenant schema is created anywhere. Not
`database_per_tenant`: one Supabase project, one database.

No tenant data was read. This is a claim about shape, established from
migrations.

### Health, metrics, telemetry — none

There is no `/health` route, no `/metrics` route, no `prom-client`, no
OpenTelemetry, no Sentry and no APM SDK anywhere in the 134 source files.

`app/api/me/route.ts` documents itself as "auth health for QA" and returns
identity or 401. It is an authentication check that sits *behind*
authentication — a prober would read its 401 as down. It is not a health
endpoint and is deliberately not recorded as one.

Consequently the manifest declares no `metricsProfile` and no `health`
block. Migration 0021 made `service_definitions.metrics_profile` nullable
precisely so this can be said honestly; the column now holds NULL, and the
API renders `not_configured`.

---

## The open blocker: ownership

**No owner team could be evidenced.** There is no CODEOWNERS file at
`CODEOWNERS`, `.github/CODEOWNERS` or `docs/CODEOWNERS`, no owning team
named in the repository documentation, and no team assignment Drake can
read. So there is no evidence for any team key, and inventing
`fikir-sepeti` or `technical-team` would manufacture exactly the kind of
sourceless claim this whole model exists to prevent.

The manifest therefore names `unknown-team`, following the convention
already used by `packages/contracts/fixtures/valid/project-epsilon.yaml`.
The schema requires at least one owner (`minItems: 1`), so the choice is
between a visible non-claim and a plausible-looking fiction.

### The planner does not block on this — verified

This was checked against `build_plan` rather than assumed, and the result
contradicts what two comments in this repository previously claimed:

```
owner_team  owner_team:unknown-team  action=no_change  reason=applied_with_parent
```

An owner team key is a **bounded label that grants no permission**;
authority comes from RBAC grants, which no manifest can touch. An
unrecognised key plans identically to a recognised one, and
`_apply_project` **creates** the `project_owners` row from whatever the
manifest names. `owner_team_unknown` exists in the reason-code table and is
never emitted by anything.

This is deliberate, and the reasoning in `model.py` is sound: if unknown
teams blocked, the first project any team owns would be permanently
unonboardable. LogiSlot and Hermes name teams that are equally unevidenced
and plan the same way.

Two stale comments asserting the opposite have been corrected in this
change — in `logislot.project.yaml` and `project-epsilon.yaml` — and
`test_fikir_sepeti_admission.py` now pins the real behaviour so the gap
cannot be misread as a safety property.

### What this means for activation

The Fikir Sepeti plan has **zero blocking items**. It is fully applicable
today. Ownership is therefore an **operator gate on this manifest**,
enforced by withholding production apply — not a guard the planner
provides.

**Required operator decision:** name the real owning team before any
production apply, or decide that `unknown-team` is an acceptable recorded
owner. There is no third option in which the planner refuses on Drake's
behalf.

---

## What was proven, and where

Every claim below is a test, not a description.

**Contract** — `packages/contracts/test/fikir-sepeti-manifest.test.ts`

- the manifest validates
- it would be **refused** if a `namespace` or `clusterRef` were added
- adding a Kubernetes environment makes it invalid while it has no metrics
  profile, so the absence cannot be quietly combined with a cluster
- it carries no connection reference and nothing secret-shaped

**Plan and drift** — `apps/api/tests/test_fikir_sepeti_admission.py`
(19 tests, run against the real manifest on disk)

- the plan contains no cluster, namespace or workload binding
- an absent metrics profile plans `no_change` / `metric_profile_not_configured`,
  not `unmapped` — the honest answer does not make the project unonboardable
- no expected workload and no expected datastore workload is derived
- an empty cluster produces **no drift items at all**
- re-planning against the applied catalog creates nothing twice
- verification is neither raised by the manifest nor lowered by a re-import
- ownership does not block, and an unrecognised team plans identically to a
  known one

**Ephemeral apply, API round-trip** —
`apps/api/tests/test_fikir_sepeti_integration.py` (11 tests, real
PostgreSQL, ephemeral database)

- the environment persists as `external` / `vercel`
- `cluster_id` is NULL, `namespace` is empty, `service_workload_bindings` is
  empty
- the service persists with `metrics_profile` NULL
- the dependency persists as
  `managed_data_platform / postgresql / project / supabase / repository_intent`
- **no `last_observed_at` or `observed_at` column anywhere in the schema is
  non-NULL after an import** — a manifest import records no observation
- a second import through an independent session duplicates nothing and
  does not promote verification
- the API reports `not_applicable` for cluster/namespace/workload rather
  than reporting them missing
- the dependency is never among services
- the payload contains no credential, endpoint or provider URL
- the same dependency key under two projects is two rows, and each project
  sees only its own

**Web** — `apps/web/src/test/fikir-sepeti-external.test.tsx` (9 tests)

- the managed dependency renders with class, provider and verification
- workload reads "Not applicable"
- health reads `unknown`, freshness reads `unavailable`, and neither
  `unhealthy` nor `stale` appears
- no in-cluster datastore section is rendered
- no Connected / Healthy / Verified / Observed / Live affirmation appears
- no restart / replica / rollout / scale / redeploy action appears
- nothing secret-shaped is rendered
- the page source contains no `fikir-sepeti`, `supabase` or `vercel`
  conditional — the G10 components are used unchanged

The service **is** bound to the prod environment, and that is correct: the
Next.js application is deployed there. What must not exist is the
*Kubernetes workload binding*, asserted separately. A catalog binding and a
workload binding are different claims, and collapsing them would have made
the test pass by denying that Fikir Sepeti runs anywhere.

---

## Secret and private-endpoint scan

Clean. Scanned by file name and pattern; **no secret value was read,
logged, or copied into this document or any test.**

- No `.env*`, `*.pem`, `*.key`, `*.p12` or credential-named file is tracked.
- `.gitignore` covers `.env*` and `*.pem`.
- Zero matches for JWT-shaped literals, `sb_secret_*`, `service_role` +
  JWT, `sk-*`, `ghp_*`.
- Zero Supabase project-ref URL literals in tracked source.

`SUPABASE_SERVICE_ROLE_KEY`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`,
`VERCEL_PROJECT_ID` and `NEXT_PUBLIC_AUTH_BYPASS` appear in workflows as
**GitHub secret names only**, which is what a workflow is supposed to
contain. `sync-vercel-env.yml` handles the service-role key without echoing
it and says so.

One observation, reported and not acted on: `NEXT_PUBLIC_AUTH_BYPASS` is a
production environment variable whose removal is a manually-triggered
workflow input defaulting to true. Whether it is currently set on Vercel
production is **unknown** — determining it would require a Vercel
credential, which this sprint forbids and which was not sought. This is
recorded as a question for the owner, not as a finding.

---

## Boundaries held

No production catalog write, no production database write, no production
API mutation. No change of any kind to the Fikir Sepeti repository. No
deploy. No Vercel login, no Supabase login, no provider credential, no
provider API call. No health probe, no background polling, no Kubernetes
resource. No Datalake action. No GitHub App activation. No Sprint 13G work.

Repository access was read-only GitHub metadata and content at one pinned
commit.

---

## Activation readiness

| Gate | State |
|---|---|
| Manifest truthful and validated | ✅ |
| No fabricated Kubernetes identity | ✅ |
| Vercel runtime represented | ✅ |
| Supabase dependency represented | ✅ |
| Tenant model evidenced | ✅ `shared_table` |
| Ephemeral apply proven | ✅ |
| Idempotency proven | ✅ |
| API round-trip proven | ✅ |
| Web rendering proven | ✅ |
| Workload and drift exclusion proven | ✅ |
| No fake health or freshness | ✅ |
| Secret scan clean | ✅ |
| **Owner team evidenced** | ❌ **blocker** |
| Production write performed | none, by design |

**Not ready for production activation.** One decision is outstanding, and
it is not a technical one: who owns Fikir Sepeti. Everything else is
proven.
