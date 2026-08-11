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
| Owner team | `fikir-sepeti` (primary) | **Explicit operator confirmation** — not the repository | **Owner confirmed** |
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

**Every technical claim sits at the first level.** A repository that imports
a Supabase client and ships Supabase migrations is strong evidence about the
source code and *no* evidence that a production connection exists, works, or
points at that provider. Importing this manifest records
`repository_intent`, and `resolve_verification_for_import` will not raise it
regardless of what the manifest declares.

**Ownership is the one claim at the second level, and it got there by the
only route that leads there.** `fikir-sepeti` / `primary` comes from an
explicit operator decision, recorded here — **not** from the repository,
which establishes nothing about ownership. It is `owner_confirmed`, and it
is worth being precise about what that does and does not mean:

| | |
|---|---|
| It is | a human naming the owning team, on the record |
| It is **not** | a CODEOWNERS file — there is none |
| It is **not** | GitHub team membership — none was read or proven |
| It is **not** | a Drake RBAC grant — an ownership row grants nothing |
| It is **not** | a provider observation |
| It is **not** | a production health observation |

It is never promoted to `provider_observed`. Confirming an owner says who is
accountable; it says nothing whatsoever about whether the system is running.

The dependency's own `verification` field is unaffected and stays at
`repository_intent` — the two are different claims about different things,
and confirming one does not raise the other.

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

## Ownership: resolved, and what resolving it exposed

The repository establishes nothing about ownership. There is no CODEOWNERS
file at `CODEOWNERS`, `.github/CODEOWNERS` or `docs/CODEOWNERS`, no owning
team named in the documentation, and no team assignment Drake can read.

So the owner did not come from the repository. It came from an operator:

```
OWNER_TEAM_KEY = fikir-sepeti
OWNER_ROLE     = primary
OWNER_TYPE     = catalog metadata
RBAC_GRANT     = none
```

### Why `unknown-team` was wrong

The first version of this manifest used `unknown-team` and described it as a
"visible non-claim". It was not one. Apply turns whatever is written in
`owners` into a real `project_owners` row, so the placeholder would have
asserted ownership in the catalog under a name nobody owns — a sourceless
claim of exactly the kind this model exists to prevent, and one that reads
as a gap in the data rather than as a decision that was never made.

Withholding production apply was the right call, but it did not make the
manifest production-ready. The placeholder is gone.

### The planner never blocked on this — verified, and now fixed

Checked against `build_plan` rather than assumed. An owner team key is
**bounded metadata that grants no permission**; authority comes from RBAC
grants, which no manifest can reach. Drake has no independent team catalog
to resolve a key against, so an unrecognised team is never blocking — and
that is deliberate: if it blocked, the first project any team owns would be
permanently unonboardable.

`owner_team_unknown` was defined in the reason-code table and emitted by
nothing, and two manifests documented the guarantee it appeared to offer.
**The dead reason code has been removed** and the false prose corrected, in
`logislot.project.yaml`, `project-epsilon.yaml`, `LOGISLOT.md`, `HERMES.md`
and `PROJECT_ONBOARDING.md`.

### The latent defect this uncovered

Every owner planned as `no_change` / `applied_with_parent`. That is true
only while the project is being created in the same transaction. For a
project that **already exists**, `_apply_project_create` never runs, and it
was the only code that wrote `project_owners` — so a manifest adding an
owner planned "nothing to do" and then did nothing. The plan and the apply
agreed with each other and were both wrong, which is why nothing caught it.

Three cases are now told apart:

| Case | Action | Reason |
|---|---|---|
| New project | `no_change` | `applied_with_parent` — created in the same transaction |
| Existing/linked project, owner recorded | `no_change` | `owner_team_already_recorded` |
| Existing/linked project, owner missing | **`create`** | a real add, planned and applied |

The snapshot gained per-project ownership keyed like `uq_project_owner`
(project, team, role). The previous `owner_teams` set was a global
`SELECT DISTINCT team_key`, so **any other project using the same team name
made this project's missing owner look settled.**

The add is purely additive: an owner the manifest omits is never removed, an
existing team is never replaced, a role is never reassigned, and no
identity, role, scope or grant is created. Removing an owner stays a human
act.

### What this means for activation

The Fikir Sepeti plan has zero blocking items and one operator-confirmed
owner. The ownership question is closed.

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
(24 tests, run against the real manifest on disk)

- the plan contains no cluster, namespace or workload binding
- an absent metrics profile plans `no_change` / `metric_profile_not_configured`,
  not `unmapped` — the honest answer does not make the project unonboardable
- no expected workload and no expected datastore workload is derived
- an empty cluster produces **no drift items at all**
- re-planning against the applied catalog creates nothing twice
- verification is neither raised by the manifest nor lowered by a re-import
- the owner is `fikir-sepeti` / `primary`, with no placeholder anywhere
- a new project records its owner with the project; an existing project
  missing it plans a real `create`; one already recorded plans `no_change`
- another project owning the same team name does not satisfy this one
- the same team in a different role is an add, not a conflict

**Ownership plan/apply agreement** —
`apps/api/tests/test_owner_team_apply_integration.py` (12 tests, real
PostgreSQL)

- an owner added to an existing project is planned as `create` **and lands
  in the database** — the regression that motivated the fix
- re-importing the same owners adds nothing
- an owner the manifest drops is **not** removed
- another project's ownership is never touched
- recording an owner creates no identity, role, scope or grant
- `applied_with_parent` never appears for an owner that is not being created
  with its project
- `owner_team_unknown` is gone from the reason vocabulary

**Ephemeral apply, API round-trip** —
`apps/api/tests/test_fikir_sepeti_integration.py` (13 tests, real
PostgreSQL, ephemeral database)

- exactly one `fikir-sepeti` / `primary` ownership row, and a second import
  does not duplicate it

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

**Web** — `apps/web/src/test/fikir-sepeti-external.test.tsx` (10 tests)

- the managed dependency renders with class, provider and verification
- the owner renders as plain metadata — `fikir-sepeti (primary)` — with no
  verified/connected affirmation and no placeholder
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
| Owner team confirmed | ✅ `fikir-sepeti` / `primary`, operator-confirmed |
| Ownership plan/apply agreement proven | ✅ |
| Production write performed | none, by design |

**No open blockers.** Every gate is proven, and the ownership question that
held this pack is closed by an explicit operator decision.

Activation itself remains a separate, deliberate act: this sprint performed
no production catalog write, and the pack does not authorise one. What
changed is that nothing technical is now outstanding — the decision to admit
Fikir Sepeti into the production catalog is the only remaining step, and it
belongs to whoever is accountable for the catalog, not to this document.
